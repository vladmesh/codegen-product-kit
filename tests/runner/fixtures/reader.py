"""Fixture tg-reader of the runner proof (docs/RUNNER_PROOF.md).

It replaces only the reader behind the pinned platform's real Caddy and auth, as service
`tg-reader` on port 8000, and serves the published reader contract
(`codegen_kit_tg_channels/contracts/openapi.json`) for one fixture channel. Its single post is
created when the product first reads posts after resolving the channel, so it is always newer
than the product's subscription and cannot be a leftover event.

Only requests carrying auth's identity for the expected product are answered; anything else is
refused with 403, so a request that bypassed forward_auth fails the proof. Every request prints
one JSON line after LOG_PREFIX with names, never credentials. Standard library only: it runs in
the platform's auth image.
"""

from __future__ import annotations

from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import re
import sys
import threading
from urllib.parse import parse_qs, urlsplit

LOG_PREFIX = "runner-reader "
CHANNEL = re.compile(r"[A-Za-z][A-Za-z0-9_]{3,31}\Z")
CHANNELS = re.compile(r"[A-Za-z][A-Za-z0-9_]{3,31}(,[A-Za-z][A-Za-z0-9_]{3,31}){0,49}\Z")
POST_ID = 7001
JSON = "application/json"
PROBLEM = "application/problem+json"


def instant(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def problem(status: int, title: str, detail: str) -> tuple[int, str, dict]:
    return (
        status,
        PROBLEM,
        {"type": "about:blank", "title": title, "status": status, "detail": detail},
    )


class Fixture:
    def __init__(self, channel: str, post_text: str, product_id: str, scope: str) -> None:
        self.channel = channel.lower()
        self.post_text = post_text
        self.product_id = product_id
        self.scope = scope
        self.lock = threading.Lock()
        self.resolved_at: datetime | None = None
        self.post: dict | None = None
        self.seq = 0

    def identity(self, headers: dict[str, str]) -> dict | None:
        """Auth's verified identity, or None when it is absent, foreign or carries the key."""
        scopes = headers.get("x-product-scopes", "").split()
        try:
            quota = json.loads(headers.get("x-product-quota", ""))
        except ValueError:
            return None
        if (
            headers.get("x-product-id") != self.product_id
            or self.scope not in scopes
            or not isinstance(quota, dict)
            or "authorization" in headers
        ):
            return None
        return {"product_id": self.product_id, "scopes": scopes, "quota": quota}

    def channel_view(self, name: str, now: datetime) -> dict:
        if name != self.channel:
            return {"channel": name, "status": "not_found", "checked_at": instant(now)}
        with self.lock:
            self.resolved_at = self.resolved_at or now
        return {
            "channel": self.channel,
            "status": "ok",
            "checked_at": instant(now),
            "title": "Runner fixture channel",
            "subscribers": 1,
            "posts_per_day": 1.0,
            "last_post_at": None,
            "last_post_id": None,
            "tracked": False,
        }

    def page(self, channels: list[str], cursor: str | None, now: datetime) -> dict:
        after = 0
        if cursor is not None:
            after = int(cursor.removeprefix("seq:"))
        with self.lock:
            if self.channel in channels and self.resolved_at is not None and self.post is None:
                self.seq += 1
                self.post = {
                    "channel": self.channel,
                    "id": POST_ID,
                    "url": f"https://t.me/{self.channel}/{POST_ID}",
                    "seq": self.seq,
                    "date": instant(now),
                    "text": self.post_text,
                    "edited": False,
                    "deleted": False,
                    "links": [],
                    "media": [],
                    "hashtags": [],
                    "fetched_at": instant(now),
                    "views": 1,
                    "lang": "en",
                    "reply": False,
                    "forwarded_from": None,
                    "link_preview": None,
                }
            items = []
            if self.post is not None and self.channel in channels and self.post["seq"] > after:
                items.append(self.post)
            return {
                "items": items,
                "next_cursor": f"seq:{self.seq}",
                "channels": [
                    {"channel": name, "status": "ok" if name == self.channel else "not_found"}
                    for name in channels
                ],
                "has_more": False,
            }

    def respond(
        self, target: str, headers: dict[str, str], now: datetime
    ) -> tuple[int, str, dict, dict]:
        """Status, content type, body and the log record of one GET request."""
        parts = urlsplit(target)
        path, query = parts.path, parse_qs(parts.query)
        record = {"event": "request", "path": path, "query": sorted(query)}
        if path in ("/healthz", "/readyz"):
            return 200, JSON, {"status": "ok"}, record | {"status": 200}
        identity = self.identity(headers)
        record |= {
            "identity": identity,
            "authorization_present": "authorization" in headers,
            "product_id_header": headers.get("x-product-id"),
        }
        if identity is None:
            status, kind, body = problem(403, "Forbidden", "no verified product identity")
        elif path.startswith("/v1/channels/"):
            name = path.removeprefix("/v1/channels/")
            if CHANNEL.fullmatch(name):
                status, kind, body = 200, JSON, self.channel_view(name.lower(), now)
                record["channel"] = body["channel"]
            else:
                status, kind, body = problem(400, "Bad Request", "invalid channel username")
        elif path == "/v1/posts":
            listed = query.get("channels", [""])[0]
            cursor = query.get("cursor", [None])[0]
            if CHANNELS.fullmatch(listed) and (cursor is None or re.fullmatch(r"seq:\d+", cursor)):
                channels = sorted(set(listed.lower().split(",")))
                status, kind, body = 200, JSON, self.page(channels, cursor, now)
                record |= {"channels": channels, "post_seqs": [i["seq"] for i in body["items"]]}
            else:
                status, kind, body = problem(400, "Bad Request", "invalid posts query")
        else:
            status, kind, body = problem(404, "Not Found", "no such reader endpoint")
        return status, kind, body, record | {"status": status}


def handler(fixture: Fixture) -> type[BaseHTTPRequestHandler]:
    class Reader(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            headers = {name.lower(): value for name, value in self.headers.items()}
            status, kind, body, record = fixture.respond(self.path, headers, datetime.now(UTC))
            payload = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            if record["path"] not in ("/healthz", "/readyz"):
                print(LOG_PREFIX + json.dumps(record, sort_keys=True), flush=True)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            return

    return Reader


def main() -> None:
    fixture = Fixture(
        os.environ["FIXTURE_CHANNEL"],
        os.environ["FIXTURE_POST_TEXT"],
        os.environ["FIXTURE_PRODUCT_ID"],
        os.environ["FIXTURE_SCOPE"],
    )
    print(LOG_PREFIX + json.dumps({"event": "started", "channel": fixture.channel}), flush=True)
    server = ThreadingHTTPServer(("0.0.0.0", 8000), handler(fixture))  # noqa: S104
    try:
        server.serve_forever()
    finally:
        server.server_close()
        sys.stdout.flush()


if __name__ == "__main__":
    main()
