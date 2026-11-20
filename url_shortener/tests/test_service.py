import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from url_shortener.service import create_app, valid_url


class ValidationTests(unittest.TestCase):
    def test_destination_validation(self):
        self.assertTrue(valid_url("https://example.com/path?q=1"))
        for url in ("file:///etc/passwd", "//example.com", "http://", "https://user@example.com",
                    "https://example.com:bad", "https://example.com\r\n", None):
            with self.subTest(url=url):
                self.assertFalse(valid_url(url))


class ApiTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.database = Path(directory.name) / "links.db"
        self.client = self.enterContext(TestClient(create_app(self.database), follow_redirects=False))

    def request(self, method, path, body=None):
        headers = {"Content-Type": "application/json"} if body is not None else {}
        response = self.client.request(method, path, content=body, headers=headers)
        return response.status_code, response.headers.get("Location"), response.content

    def test_create_redirect_and_count(self):
        url = "https://example.com/path?q=1"
        status, path, body = self.request("POST", "/links", json.dumps({"url": url}))
        self.assertEqual(status, 201)
        link = json.loads(body)
        self.assertEqual(path, link["path"])
        stats_path = f"/links/{link['code']}/stats"
        self.assertEqual(json.loads(self.request("GET", stats_path)[2])["clicks"], 0)
        for _ in range(2):
            self.assertEqual(self.request("GET", path)[:2], (302, url))
        status, _, body = self.request("GET", stats_path)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"code": link["code"], "clicks": 2})
        with TestClient(create_app(self.database)) as restarted:
            self.assertEqual(restarted.get(stats_path).json()["clicks"], 2)

    def test_invalid_and_unknown_requests(self):
        self.assertEqual(self.request("POST", "/links", "{")[0], 400)
        self.assertEqual(self.request("GET", "/abcdefgh")[0], 404)
        self.assertEqual(self.request("GET", "/links/abcdefgh/stats")[0], 404)

    def test_openapi_and_request_body_limit(self):
        self.assertEqual(self.client.get("/docs").status_code, 200)
        spec = self.client.get("/openapi.json").json()
        self.assertIn("201", spec["paths"]["/links"]["post"]["responses"])
        self.assertIn("302", spec["paths"]["/{code}"]["get"]["responses"])
        response = self.client.post("/links", content=" " * 4097)
        self.assertEqual(response.status_code, 400)
