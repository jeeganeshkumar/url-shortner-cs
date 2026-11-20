import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from fastapi.testclient import TestClient

from url_shortener.service import create_app


class CandidateTests(unittest.TestCase):
    def test_schema_migration_expiry_and_click_policy(self):
        features = json.loads(os.environ.get(
            "SHORTENER_CANDIDATE", '{"expiry": true, "click_policy": "exclude_previews"}'
        ))
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "legacy.db"
            with closing(sqlite3.connect(database)) as db, db:
                db.execute("CREATE TABLE links (code TEXT PRIMARY KEY, url TEXT NOT NULL, clicks INTEGER NOT NULL DEFAULT 0)")
                db.execute("INSERT INTO links VALUES ('legacy01', 'https://example.com', 7)")
            now = [100]
            app = create_app(database, features, lambda: now[0])
            with TestClient(app, follow_redirects=False) as client:

                def request(method, path, body=None, headers=None):
                    headers = dict(headers or {})
                    if body is not None:
                        headers["Content-Type"] = "application/json"
                    response = client.request(method, path, content=body, headers=headers)
                    return response.status_code, response.content

                self.assertEqual(request("GET", "/legacy01")[0], 302)
                self.assertEqual(json.loads(request("GET", "/links/legacy01/stats")[1])["clicks"], 8)
                status, body = request("POST", "/links", '{"url":"https://example.com","ttl_seconds":2}')
                self.assertEqual(status, 201)
                code = json.loads(body)["code"]
                path = f"/{code}"
                stats = f"/links/{code}/stats"
                self.assertEqual(request("GET", path, headers={"Sec-Purpose": "Preview"})[0], 302)
                self.assertEqual(request("GET", path)[0], 302)
                expected = 2 if features["click_policy"] == "all" else 1
                self.assertEqual(json.loads(request("GET", stats)[1])["clicks"], expected)
                now[0] = 102
                self.assertEqual(request("GET", path)[0], 410 if features["expiry"] else 302)
                self.assertEqual(json.loads(request("GET", stats)[1])["clicks"],
                                 expected if features["expiry"] else expected + 1)
                if features["expiry"]:
                    self.assertEqual(request("POST", "/links", '{"url":"https://example.com","ttl_seconds":-1}')[0], 400)
