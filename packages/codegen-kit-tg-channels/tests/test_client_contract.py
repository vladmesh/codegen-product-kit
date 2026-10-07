"""Every client operation and consumed response field agrees with the vendored API."""
# ruff: noqa: PLR2004

import asyncio
from copy import deepcopy
from datetime import UTC, datetime
import json
import logging
from pathlib import Path
import re
import traceback

import httpx
from jsonschema import Draft202012Validator
import pytest
import yaml

from framework.action_contracts import normalize_schema, verify_action_openapi
from framework.bindings import load_binding, validate_binding
from framework.spec.packages import load_package_manifest

PACKAGE = Path(__file__).parents[1] / "codegen_kit_tg_channels"
NOW = "2026-10-07T21:00:00Z"
KEY = "cps_test_SUPER_SECRET_KEY"


def document():
    return json.loads((PACKAGE / "contracts/openapi.json").read_text())


def channel(status="ok"):
    return {"channel": "cyproplan", "status": status, "checked_at": NOW}


def post(**changes):
    return {
        "channel": "cyproplan",
        "id": 7,
        "url": "https://t.me/cyproplan/7",
        "seq": 1,
        "date": NOW,
        "text": "  News  ",
        "edited": False,
        "deleted": False,
        "links": [],
        "media": [],
        "fetched_at": NOW,
    } | changes


def page(items=(), **changes):
    return {
        "items": list(items),
        "next_cursor": "opaque-next",
        "channels": [{"channel": "cyproplan", "status": "ok"}],
        "has_more": False,
    } | changes


def test_used_operations_requests_responses_and_security(modules):
    spec = document()
    seen = []
    replies = [channel(), page([post()]), page()]

    def respond(request):
        assert request.headers["Authorization"] == f"Bearer {KEY}"
        assert str(request.url).startswith(spec["servers"][0]["url"] + "/v1/")
        route = "/v1/channels/{channel}" if "/channels/" in request.url.path else "/v1/posts"
        operation = spec["paths"][route][request.method.lower()]
        assert operation["operationId"] == (
            "resolveChannel" if "channels/" in route else "listPosts"
        )
        declared = {p["name"]: p for p in operation["parameters"]}
        parameters = dict(request.url.params)
        if route.endswith("{channel}"):
            parameters["channel"] = request.url.path.rsplit("/", 1)[1]
        assert parameters.keys() <= declared.keys()
        assert {p["name"] for p in declared.values() if p["required"]} <= parameters.keys()
        for name, value in parameters.items():
            value = int(value) if name == "limit" else value
            Draft202012Validator(declared[name]["schema"]).validate(value)
        body = replies[len(seen)]
        schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
        Draft202012Validator(normalize_schema(schema, spec)).validate(body)
        seen.append(parameters)
        return httpx.Response(200, json=body)

    async def exercise():
        client = modules.client.ReaderClient(
            spec["servers"][0]["url"], KEY, transport=httpx.MockTransport(respond)
        )
        assert (await client.resolve("cyproplan")).status == "ok"
        now = datetime.now(UTC)
        result = await client.posts(["cyproplan", "cyproplan"], since=now)
        assert result.items[0].text == "  News  " and not result.has_more
        await client.posts(["cyproplan"], since=now, cursor=result.next_cursor)
        await client.close()

    asyncio.run(exercise())
    assert "since" in seen[1] and "cursor" not in seen[1]
    assert seen[2]["cursor"] == "opaque-next" and "since" not in seen[2]
    assert spec["security"] == [{"productKey": []}]
    assert spec["components"]["securitySchemes"]["productKey"]["scheme"] == "bearer"
    manifest = yaml.safe_load((PACKAGE / "package.yaml").read_text())
    base = next(item for item in manifest["environment"] if item["name"] == "PLATFORM_BASE_URL")
    assert base["source"]["url"] == spec["servers"][0]["url"]
    assert modules.service.USERNAME.pattern.removesuffix("\\Z") == re.compile(
        spec["paths"]["/v1/channels/{channel}"]["get"]["parameters"][0]["schema"]["pattern"]
    ).pattern.removeprefix("^").removesuffix("$")


@pytest.mark.parametrize("model_name", ["ResolvedChannel", "ChannelState", "Post", "PostPage"])
def test_every_consumed_response_field_matches_projection(modules, model_name):
    spec = document()
    source_name = "Channel" if model_name == "ResolvedChannel" else model_name
    upstream = normalize_schema(spec["components"]["schemas"][source_name], spec)
    model = getattr(modules.models, model_name)
    projection = normalize_schema(model.model_json_schema(), model.model_json_schema())
    # Pydantic local $defs and upstream components are resolved by the same normalizer.
    for field, field_schema in projection["properties"].items():
        upstream_field = upstream["properties"][field]
        if field in ("channels", "items"):
            # Nested response models are separately checked above.
            assert field_schema["type"] == upstream_field["type"] == "array"
        else:
            expected = deepcopy(upstream_field)
            expected.pop("format", None) if expected.get("format") == "int64" else None
            assert field_schema == expected, field
        assert (field in projection["required"]) == (field in upstream["required"])


@pytest.mark.parametrize(
    "route,status",
    [
        ("/v1/channels/{channel}", 400),
        ("/v1/channels/{channel}", 429),
        ("/v1/channels/{channel}", 503),
        ("/v1/posts", 400),
        ("/v1/posts", 401),
        ("/v1/posts", 403),
        ("/v1/posts", 409),
        ("/v1/posts", 429),
    ],
)
def test_all_declared_error_statuses_are_sanitized(modules, caplog, route, status):
    spec = document()
    response_spec = spec["paths"][route]["get"]["responses"][str(status)]
    problem = {"type": "about:blank", "title": KEY, "status": status, "detail": KEY}
    schema = response_spec["content"]["application/problem+json"]["schema"]
    Draft202012Validator(normalize_schema(schema, spec)).validate(problem)
    if status == 429:
        assert response_spec["headers"]["Retry-After"]["schema"] == {"type": "integer"}
    caplog.set_level(logging.DEBUG)

    async def exercise():
        client = modules.client.ReaderClient(
            spec["servers"][0]["url"],
            KEY,
            transport=httpx.MockTransport(
                lambda request: httpx.Response(status, json=problem, headers={"Retry-After": "120"})
            ),
        )
        with pytest.raises(modules.client.ServiceError) as caught:
            if "channels" in route:
                await client.resolve("cyproplan")
            else:
                await client.posts(["cyproplan"], since=datetime.now(UTC))
        assert caught.value.status == status
        assert caught.value.retry_after == 120
        assert caught.value.__context__ is None and caught.value.__cause__ is None
        assert KEY not in str(caught.value) + repr(caught.value) + repr(vars(client))
        await client.close()

    asyncio.run(exercise())
    assert KEY not in caplog.text


@pytest.mark.parametrize("failure", ["transport", "json", "schema"])
def test_key_and_raw_failures_never_escape(modules, caplog, failure):
    caplog.set_level(logging.DEBUG)

    def respond(request):
        if failure == "transport":
            raise httpx.ConnectError(KEY, request=request)
        if failure == "json":
            return httpx.Response(200, text=KEY)
        return httpx.Response(200, json={"status": KEY})

    async def exercise():
        client = modules.client.ReaderClient(
            "https://platform.example.test/tg-reader", KEY, transport=httpx.MockTransport(respond)
        )
        try:
            await client.resolve("cyproplan")
        except modules.client.ServiceError as error:
            assert KEY not in "".join(traceback.format_exception(error))
            assert error.__context__ is None
            assert error.status == 503
        else:
            raise AssertionError("invalid response accepted")
        await client.close()

    asyncio.run(exercise())
    assert KEY not in caplog.text


def test_required_environment_has_no_runtime_defaults(modules, monkeypatch):
    documented = {
        line.partition("=")[0]
        for line in (PACKAGE.parent / ".env.example").read_text().splitlines()
        if line and not line.startswith("#")
    }
    assert documented == {"PLATFORM_KEY", "PLATFORM_BASE_URL", "REDIS_URL"}
    monkeypatch.delenv("PLATFORM_BASE_URL", raising=False)
    monkeypatch.delenv("PLATFORM_KEY", raising=False)
    with pytest.raises(RuntimeError, match="PLATFORM_BASE_URL is not set"):
        modules.client.ReaderClient.from_environment()
    monkeypatch.setenv("PLATFORM_BASE_URL", "https://platform.example.test")
    with pytest.raises(RuntimeError, match="PLATFORM_KEY is not set"):
        modules.client.ReaderClient.from_environment()


def test_real_product_actions_and_bilingual_binding_agree(modules):
    from fastapi import FastAPI

    manifest = load_package_manifest(PACKAGE / "package.yaml")
    app = FastAPI()
    app.include_router(modules.api.router, prefix=manifest.http.prefix)
    verify_action_openapi(manifest, app.openapi())
    validate_binding(load_binding(PACKAGE / "bindings/default.yaml"), manifest, {})
    assert [action.name for action in manifest.actions] == ["add", "list", "remove", "digest"]
    assert manifest.setting_seeds[0].key == "starting_channels"
