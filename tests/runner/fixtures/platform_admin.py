"""Throwaway product registration and ingress refusals of the runner proof (docs/RUNNER_PROOF.md).

Runs as compose service `runner-admin` in the pinned platform project: it reaches the real auth
admin API over `orch-link`, like the orchestrator, and the real Caddy over `edge`, like
cloudflared. It registers the throwaway product, its tg-reader grant (the scopes and quota the
installed package declares) and the synthetic key, reads the product back, then proves that a
missing, unknown or malformed key is refused at ingress with auth's problem document.

Prints one JSON summary after LOG_PREFIX and never prints the key or the admin token.
Standard library only.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import secrets
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

LOG_PREFIX = "runner-admin "
KEY_ALPHABET = "abcdefghijklmnopqrstuvwxyz234567"


def request(url: str, method: str = "GET", body: object = None, headers=None):
    data = None if body is None else json.dumps(body).encode()
    prepared = Request(url, data=data, method=method, headers=dict(headers or {}))  # noqa: S310
    if data is not None:
        prepared.add_header("Content-Type", "application/json")
    try:
        with urlopen(prepared, timeout=10) as response:  # noqa: S310
            return response.status, response.headers.get("Content-Type", ""), response.read()
    except HTTPError as error:
        return error.code, error.headers.get("Content-Type", ""), error.read()


def unknown_key() -> str:
    key_id = "".join(secrets.choice(KEY_ALPHABET) for _ in range(12))
    secret = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")
    return f"cps_{key_id}_{secret}"


def wait_for_ingress(ingress: str, timeout: float = 120.0) -> None:
    deadline = time.monotonic() + timeout
    while True:
        try:
            if request(f"{ingress}/healthz")[0] == 200:
                return
        except (URLError, OSError):
            pass
        if time.monotonic() > deadline:
            raise RuntimeError("caddy /healthz did not answer 200 in time")
        time.sleep(1)


def register(admin: str, token: str, product: str, key: str, grant: dict) -> dict:
    headers = {"Authorization": f"Bearer {token}"}
    base = f"{admin}/admin/v1/products/{product}"
    key_id = key.split("_")[1]
    steps = [
        ("product", base, {"display_name": "runner proof", "orchestrator_project_id": product}),
        ("grant", f"{base}/grants/tg-reader", grant),
        ("key", f"{base}/keys/{key_id}", {"key": key, "label": "runner proof"}),
    ]
    statuses = {}
    for label, url, body in steps:
        statuses[label] = request(url, "PUT", body, headers)[0]
        if statuses[label] not in (200, 201):
            raise RuntimeError(f"admin PUT {label} answered {statuses[label]}")
    status, _, raw = request(base, headers=headers)
    if status != 200:
        raise RuntimeError(f"admin GET product answered {status}")
    product_view = json.loads(raw)
    return {
        "statuses": statuses,
        "readback": {
            "product_id": product_view["product_id"],
            "disabled": product_view["disabled"],
            "grants": [
                {"service": item["service"], "scopes": item["scopes"], "quota": item["quota"]}
                for item in product_view["grants"]
            ],
            "key_ids_match": [item["key_id"] for item in product_view["keys"]] == [key_id],
        },
    }


def refusals(ingress: str, channel: str) -> list[dict]:
    path = f"/tg-reader/v1/channels/{channel}"
    results = []
    for label, headers in [
        ("missing key", {}),
        ("unknown key", {"Authorization": f"Bearer {unknown_key()}"}),
        ("malformed key", {"Authorization": "Bearer cps_not_a_product_key"}),
    ]:
        status, kind, _ = request(f"{ingress}{path}", headers=headers)
        results.append(
            {
                "label": label,
                "path": path,
                "status": status,
                "problem": kind.startswith("application/problem+json"),
            }
        )
        if status != 401 or not kind.startswith("application/problem+json"):
            raise RuntimeError(f"ingress {label} answered {status} {kind}")
    return results


def main() -> int:
    token = Path(os.environ["AUTH_ADMIN_TOKEN_FILE"]).read_text(encoding="utf-8").strip()
    key = Path(os.environ["RUNNER_PRODUCT_KEY_FILE"]).read_text(encoding="utf-8").strip()
    ingress = os.environ["INGRESS_URL"]
    summary: dict = {"status": "failed"}
    try:
        wait_for_ingress(ingress)
        summary["registration"] = register(
            os.environ["AUTH_ADMIN_URL"],
            token,
            os.environ["RUNNER_PRODUCT_ID"],
            key,
            json.loads(os.environ["RUNNER_GRANT"]),
        )
        summary["refusals"] = refusals(ingress, os.environ["RUNNER_FIXTURE_CHANNEL"])
        summary["status"] = "passed"
    except Exception as error:  # noqa: BLE001  # the summary carries the failure
        summary["error"] = f"{type(error).__name__}: {error}"
    print(LOG_PREFIX + json.dumps(summary, sort_keys=True), flush=True)
    return 0 if summary["status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
