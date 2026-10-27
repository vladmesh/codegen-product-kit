"""tg-reader test double at its HTTP boundary, for the connected /top proof.

Usage: connected_reader.py <directory> <port> <key>. It serves one channel's three posts on
127.0.0.1, refuses any request without the bearer key, writes the served posts to
<directory>/reader-posts.json, appends one JSON line per request to
<directory>/reader-requests.jsonl and touches <directory>/ready once it listens.
Standard library only: it runs with the backend container's interpreter.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
from urllib.parse import parse_qs, urlsplit

CHANNEL = "cyproplan"
PREFIX = "/tg-reader/v1/"
#: (post id, hours old, views): ranked by the defaults as 2, 1, 3.
POSTS = ((1, 2, 120), (2, 2, 9000), (3, 48, None))


def served_posts(now: datetime) -> list[dict]:
    posts = []
    for number, hours_old, views in POSTS:
        date = (now - timedelta(hours=hours_old)).isoformat()
        posts.append(
            {
                "channel": CHANNEL,
                "id": number,
                "url": f"https://t.me/{CHANNEL}/{number}",
                "seq": number,
                "date": date,
                "text": f"Post {number}",
                "edited": False,
                "deleted": False,
                "links": [],
                "media": [],
                "fetched_at": date,
                "views": views,
            }
        )
    return posts


def main() -> None:
    directory, port, key = Path(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
    now = datetime.now(UTC).replace(microsecond=0)
    posts = served_posts(now)
    (directory / "reader-posts.json").write_text(json.dumps(posts))
    requests = directory / "reader-requests.jsonl"

    class Reader(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            url = urlsplit(self.path)
            authorized = self.headers.get("Authorization") == f"Bearer {key}"
            with requests.open("a") as log:
                log.write(json.dumps({"path": url.path, "authorized": authorized}) + "\n")
            if not authorized:
                self._answer(401, {"type": "about:blank", "title": "Unauthorized", "status": 401})
            elif url.path == f"{PREFIX}channels/{CHANNEL}":
                checked = datetime.now(UTC).isoformat()
                self._answer(200, {"channel": CHANNEL, "status": "ok", "checked_at": checked})
            elif url.path == f"{PREFIX}posts":
                channels = parse_qs(url.query)["channels"][0].split(",")
                self._answer(
                    200,
                    {
                        "items": posts if CHANNEL in channels else [],
                        "next_cursor": "end",
                        "channels": [{"channel": name, "status": "ok"} for name in channels],
                        "has_more": False,
                    },
                )
            else:
                self._answer(404, {"type": "about:blank", "title": "Not Found", "status": 404})

        def _answer(self, status: int, body: dict) -> None:
            content = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, *_arguments: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", port), Reader)
    (directory / "ready").touch()
    server.serve_forever()


if __name__ == "__main__":
    main()
