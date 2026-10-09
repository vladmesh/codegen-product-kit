"""Fake Telegram Bot API of the runner proof (docs/RUNNER_PROOF.md).

The product bot's only replaced external transport. It serves HTTPS on :443 under the network
alias `api.telegram.org` with a certificate from a runner-generated CA, which the bot container
trusts through SSL_CERT_FILE; the bot image and its configuration are otherwise unchanged.

A plain HTTP control port (:8081, published to the runner's loopback only) queues a user's
message as an update and reads what the bot sent. Sent messages can only be recorded by the
bot's own Bot API calls carrying its token: the control port has no way to add one. Every
record has a sequence number, so the runner accepts only messages sent after its watermark.
Standard library only.
"""

from __future__ import annotations

from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import ssl
import threading
import time
from urllib.parse import parse_qs, urlsplit

BOT_ID = 7000000001
BOT_USER = {
    "id": BOT_ID,
    "is_bot": True,
    "first_name": "Runner proof bot",
    "username": "runner_proof_bot",
    "can_join_groups": False,
    "can_read_all_group_messages": False,
    "supports_inline_queries": False,
}
LONG_POLL_SECONDS = 1.0
TRUE_METHODS = {
    "deletewebhook",
    "setwebhook",
    "setmycommands",
    "deletemycommands",
    "answercallbackquery",
    "close",
    "logout",
    "setchatmenubutton",
}


def command_update(update_id: int, user_id: int, text: str, date: int) -> dict:
    """A private-chat update with one leading bot command, as the Bot API delivers it."""
    command = text.split(maxsplit=1)[0]
    entities = (
        [{"type": "bot_command", "offset": 0, "length": len(command)}]
        if command.startswith("/")
        else []
    )
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id,
            "date": date,
            "chat": {"id": user_id, "type": "private", "first_name": "Runner"},
            "from": {"id": user_id, "is_bot": False, "first_name": "Runner", "language_code": "en"},
            "text": text,
            "entities": entities,
        },
    }


def parameters(content_type: str, body: bytes, query: str) -> dict[str, str]:
    """Bot API parameters from a query string, form-urlencoded or JSON body."""
    values = {key: items[-1] for key, items in parse_qs(query).items()}
    if content_type.startswith("application/json") and body:
        values.update({key: str(value) for key, value in json.loads(body).items()})
    elif body:
        decoded = parse_qs(body.decode(), keep_blank_values=True)
        values.update({key: items[-1] for key, items in decoded.items()})
    return values


class BotApi:
    def __init__(self, token: str) -> None:
        self.token = token
        self.changed = threading.Condition()
        self.updates: list[dict] = []
        self.sent: list[dict] = []
        self.calls: Counter[str] = Counter()
        self.next_update = 1
        self.next_message = 1

    def queue(self, user_id: int, text: str) -> int:
        with self.changed:
            update = command_update(self.next_update, user_id, text, int(time.time()))
            self.next_update += 1
            self.updates.append(update)
            self.changed.notify_all()
            return update["update_id"]

    def message(self, chat_id: int, text: str) -> dict:
        with self.changed:
            message_id = self.next_message
            self.next_message += 1
            self.sent.append({"seq": len(self.sent) + 1, "chat_id": chat_id, "text": text})
            self.changed.notify_all()
        return {
            "message_id": message_id,
            "date": int(time.time()),
            "chat": {"id": chat_id, "type": "private"},
            "from": BOT_USER,
            "text": text,
        }

    def get_updates(self, values: dict[str, str]) -> list[dict]:
        offset = int(values.get("offset") or 0)
        wait = min(float(values.get("timeout") or 0), LONG_POLL_SECONDS)
        deadline = time.monotonic() + wait
        with self.changed:
            while True:
                found = [item for item in self.updates if item["update_id"] >= offset]
                remaining = deadline - time.monotonic()
                if found or remaining <= 0:
                    return found
                self.changed.wait(remaining)

    def call(self, path: str, values: dict[str, str]) -> tuple[int, dict]:
        """Answer `/bot<token>/<method>` like the Bot API; the token must match."""
        prefix, _, method = path.lstrip("/").partition("/")
        if prefix != f"bot{self.token}" or not method:
            return 401, {"ok": False, "error_code": 401, "description": "Unauthorized"}
        name = method.lower()
        with self.changed:
            self.calls[name] += 1
        if name == "getme":
            result: object = BOT_USER
        elif name == "getupdates":
            result = self.get_updates(values)
        elif name in ("sendmessage", "editmessagetext"):
            result = self.message(int(values["chat_id"]), values["text"])
        elif name == "getmycommands":
            result = []
        elif name in TRUE_METHODS:
            result = True
        else:
            return 400, {"ok": False, "error_code": 400, "description": f"unsupported {method}"}
        return 200, {"ok": True, "result": result}

    def state(self) -> dict:
        with self.changed:
            return {
                "calls": dict(self.calls),
                "sent": list(self.sent),
                "queued": len(self.updates),
            }


def reply(handler: BaseHTTPRequestHandler, status: int, body: object) -> None:
    payload = json.dumps(body).encode()
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(payload)))
    handler.end_headers()
    handler.wfile.write(payload)


def bot_handler(api: BotApi) -> type[BaseHTTPRequestHandler]:
    class Bot(BaseHTTPRequestHandler):
        def handle_call(self) -> None:
            target = urlsplit(self.path)
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            values = parameters(self.headers.get("Content-Type", ""), body, target.query)
            reply(self, *api.call(target.path, values))

        do_GET = handle_call
        do_POST = handle_call

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            return  # request lines contain the bot token

    return Bot


def control_handler(api: BotApi) -> type[BaseHTTPRequestHandler]:
    class Control(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/control/health":
                reply(self, 200, {"status": "ok"})
            elif self.path == "/control/state":
                reply(self, 200, api.state())
            else:
                reply(self, 404, {"error": "not found"})

        def do_POST(self) -> None:
            if self.path != "/control/messages":
                reply(self, 404, {"error": "not found"})
                return
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
            reply(self, 200, {"update_id": api.queue(int(body["user_id"]), str(body["text"]))})

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            return

    return Control


def main() -> None:
    api = BotApi(os.environ["TELEGRAM_FIXTURE_TOKEN"])
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(os.environ["TELEGRAM_FIXTURE_CERT"], os.environ["TELEGRAM_FIXTURE_KEY"])
    bot = ThreadingHTTPServer(("0.0.0.0", 443), bot_handler(api))  # noqa: S104
    bot.socket = context.wrap_socket(bot.socket, server_side=True)
    control = ThreadingHTTPServer(("0.0.0.0", 8081), control_handler(api))  # noqa: S104
    threading.Thread(target=bot.serve_forever, daemon=True).start()
    print("runner-telegram started", flush=True)
    control.serve_forever()


if __name__ == "__main__":
    main()
