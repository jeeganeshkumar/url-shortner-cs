"""Local URL shortener with SQLite persistence."""

import argparse
import json
import re
import secrets
import sqlite3
import time
from contextlib import asynccontextmanager, closing
from urllib.parse import urlsplit

import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

CODE = r"[A-Za-z0-9_-]{8}"
FEATURES = {"expiry": {False, True}, "click_policy": {"all", "exclude_previews"}}


def check_features(features):
    if (
        not isinstance(features, dict)
        or features.keys() != FEATURES.keys()
        or type(features["expiry"]) is not bool
        or not isinstance(features["click_policy"], str)
        or features["click_policy"] not in FEATURES["click_policy"]
    ):
        raise ValueError("Invalid feature configuration")
    return features


def valid_url(value):
    if (
        not isinstance(value, str)
        or not value.isascii()
        or len(value) > 2048
        or any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in value)
        or chr(92) in value
    ):
        return False
    try:
        parsed = urlsplit(value)
        port = parsed.port
        return bool(
            parsed.scheme in {"http", "https"}
            and parsed.hostname
            and parsed.username is None
            and (port is None or port > 0)
        )
    except ValueError:
        return False


class CreateLink(BaseModel):
    url: str = Field(description="Absolute ASCII HTTP(S) destination")
    ttl_seconds: int | None = Field(default=None, description="1–86400 seconds when expiry is enabled")

    @field_validator("url")
    @classmethod
    def validate_destination(cls, value):
        if not valid_url(value):
            raise ValueError("Invalid destination URL")
        return value

    @field_validator("ttl_seconds", mode="before")
    @classmethod
    def validate_ttl(cls, value):
        if value is not None and (type(value) is not int or not 1 <= value <= 86400):
            raise ValueError("Invalid TTL")
        return value


class CreatedLink(BaseModel):
    code: str
    path: str


class LinkStats(BaseModel):
    code: str
    clicks: int


class ErrorBody(BaseModel):
    error: str


NO_STORE = {"Cache-Control": "no-store"}


def error(status, message):
    return JSONResponse({"error": message}, status_code=status, headers=NO_STORE)


class LimitLinkBody:
    """Bound the create request body before FastAPI parses its JSON."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or (scope["method"], scope["path"]) != ("POST", "/links"):
            await self.app(scope, receive, send)
            return

        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > 4096:
                response = error(400, "Request body exceeds 4096 bytes")
                await response(scope, receive, send)
                return
            body.extend(chunk)
            if not message.get("more_body", False):
                break

        replayed = False

        async def replay():
            nonlocal replayed
            if replayed:
                return await receive()
            replayed = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        await self.app(scope, replay, send)


def initialize_database(database):
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """CREATE TABLE IF NOT EXISTS links (
                code TEXT PRIMARY KEY,
                url TEXT NOT NULL,
                clicks INTEGER NOT NULL DEFAULT 0 CHECK (clicks >= 0)
            )"""
        )
        columns = {row[1] for row in connection.execute("PRAGMA table_info(links)")}
        if "expires_at" not in columns:
            connection.execute("ALTER TABLE links ADD COLUMN expires_at INTEGER")


def create_app(database="links.sqlite3", features=None, clock=time.time):
    features = dict(check_features(features)) if features is not None else {
        "expiry": False, "click_policy": "all",
    }
    expiry_enabled = features["expiry"]

    @asynccontextmanager
    async def lifespan(app):
        initialize_database(database)
        yield

    app = FastAPI(title="URL Shortener", lifespan=lifespan, redirect_slashes=False)
    app.add_middleware(LimitLinkBody)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return error(400, "Supply an ASCII HTTP(S) URL and an optional TTL of 1 to 86400 seconds")

    @app.exception_handler(sqlite3.Error)
    async def storage_error(request, exc):
        return error(503, "Link storage unavailable")

    @app.post("/links", status_code=201, response_model=CreatedLink,
              responses={400: {"model": ErrorBody}, 503: {"model": ErrorBody}, 422: {"description": "Handled as 400"}})
    def create_link(payload: CreateLink, response: Response):
        ttl = payload.ttl_seconds if expiry_enabled else None
        expires_at = int(clock()) + ttl if ttl is not None else None
        with closing(sqlite3.connect(database)) as connection, connection:
            for _ in range(5):
                code = secrets.token_urlsafe(6)
                result = connection.execute(
                    "INSERT INTO links (code, url, expires_at) VALUES (?, ?, ?) ON CONFLICT(code) DO NOTHING",
                    (code, payload.url, expires_at),
                )
                if result.rowcount:
                    break
            else:
                raise sqlite3.OperationalError("Code allocation failed")
        path = f"/{code}"
        response.headers.update({**NO_STORE, "Location": path})
        return CreatedLink(code=code, path=path)

    @app.get("/links/{code}/stats", response_model=LinkStats,
             responses={404: {"model": ErrorBody}, 503: {"model": ErrorBody}})
    def stats(code: str, response: Response):
        if not re.fullmatch(CODE, code):
            return error(404, "Route not found")
        with closing(sqlite3.connect(database)) as connection:
            row = connection.execute("SELECT clicks FROM links WHERE code = ?", (code,)).fetchone()
        if row is None:
            return error(404, "Unknown link")
        response.headers.update(NO_STORE)
        return LinkStats(code=code, clicks=row[0])

    @app.get("/{code}", status_code=302, response_class=Response,
             responses={302: {"headers": {"Location": {"schema": {"type": "string"}}}},
                        404: {"model": ErrorBody}, 410: {"model": ErrorBody}, 503: {"model": ErrorBody}})
    def redirect(code: str, request: Request):
        if not re.fullmatch(CODE, code):
            return error(404, "Route not found")
        now = int(clock())
        markers = " ".join(request.headers.get(header, "") for header in ("Purpose", "Sec-Purpose"))
        preview = "preview" in markers.lower().replace(";", " ").split()
        increment = int(features["click_policy"] == "all" or not preview)
        with closing(sqlite3.connect(database)) as connection, connection:
            row = connection.execute(
                """UPDATE links SET clicks = clicks + ?
                   WHERE code = ? AND (? OR expires_at IS NULL OR expires_at > ?)
                   RETURNING url""",
                (increment, code, not expiry_enabled, now),
            ).fetchone()
            if row is None and expiry_enabled and connection.execute(
                "SELECT 1 FROM links WHERE code = ? AND expires_at <= ?", (code, now),
            ).fetchone():
                return error(410, "Expired link")
        if row is None:
            return error(404, "Unknown link")
        return Response(status_code=302, headers={**NO_STORE, "Location": row[0]})

    return app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--db", default="links.sqlite3")
    parser.add_argument("--features", type=argparse.FileType("r"))
    args = parser.parse_args()
    features = None
    if args.features:
        with args.features as stream:
            features = check_features(json.load(stream))
    uvicorn.run(create_app(args.db, features), host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
