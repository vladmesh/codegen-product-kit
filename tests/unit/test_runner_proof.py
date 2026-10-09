"""Local coverage of the CI runner proof's decisions and fixtures (docs/RUNNER_PROOF.md).

Docker, the orchestrator executor and the platform run only in the Runner Proof workflow; these
tests pin what the runner executes, what it accepts as evidence and the fixture contracts.
"""

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import re

from jsonschema import Draft202012Validator
import pytest
import yaml

from tests.runner import support
from tests.runner.fixtures import platform_admin, reader, telegram_api

ROOT = Path(__file__).parents[2]
CONTRACT = json.loads(
    (
        ROOT / "packages/codegen-kit-tg-channels/codegen_kit_tg_channels/contracts/openapi.json"
    ).read_text()
)
PRODUCT = "runner-test"
IDENTITY = {
    "x-product-id": PRODUCT,
    "x-product-scopes": "tg-reader:read",
    "x-product-quota": '{"channels_max":50}',
}
NOW = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)


def contract_errors(schema: str, body: dict) -> list[str]:
    validator = Draft202012Validator(
        {"$ref": f"#/components/schemas/{schema}", "components": CONTRACT["components"]}
    )
    return [error.message for error in validator.iter_errors(body)]


def fixture() -> reader.Fixture:
    return reader.Fixture("Runner_Fixture", "fixture post text", PRODUCT, "tg-reader:read")


def test_reader_refuses_requests_without_verified_product_identity() -> None:
    reader_fixture = fixture()
    for headers in (
        {},
        {**IDENTITY, "x-product-id": "another-product"},
        {**IDENTITY, "x-product-scopes": "other:read"},
        {**IDENTITY, "authorization": "Bearer cps_leaked"},
    ):
        status, kind, body, record = reader_fixture.respond(
            "/v1/channels/runner_fixture", headers, NOW
        )
        assert (status, kind) == (403, reader.PROBLEM)
        assert contract_errors("Problem", body) == []
        assert record["identity"] is None and record["status"] == 403
    assert reader_fixture.resolved_at is None


def test_reader_serves_the_published_contract_and_a_post_newer_than_the_subscription() -> None:
    reader_fixture = fixture()
    status, _, early, _ = reader_fixture.respond(
        "/v1/posts?channels=runner_fixture&since=2026-10-06T12:00:00Z", IDENTITY, NOW
    )
    assert status == 200 and early["items"] == []
    status, _, channel, record = reader_fixture.respond(
        "/v1/channels/runner_fixture", IDENTITY, NOW
    )
    assert status == 200 and channel["status"] == "ok"
    assert contract_errors("Channel", channel) == []
    assert record["identity"] == {
        "product_id": PRODUCT,
        "scopes": ["tg-reader:read"],
        "quota": {"channels_max": 50},
    }
    later = NOW + timedelta(seconds=30)
    status, _, page, record = reader_fixture.respond(
        "/v1/posts?channels=runner_fixture&since=2026-10-06T12:00:00Z&limit=200", IDENTITY, later
    )
    assert status == 200 and contract_errors("PostPage", page) == []
    (post,) = page["items"]
    assert post["text"] == "fixture post text" and post["date"] == "2026-10-09T12:00:30Z"
    assert post["url"] == f"https://t.me/runner_fixture/{reader.POST_ID}"
    assert record["post_seqs"] == [post["seq"]]
    status, _, after, _ = reader_fixture.respond(
        f"/v1/posts?channels=runner_fixture&cursor={page['next_cursor']}", IDENTITY, later
    )
    assert status == 200 and after["items"] == [] and contract_errors("PostPage", after) == []


@pytest.mark.parametrize(
    ("target", "status"),
    [
        ("/v1/channels/other_channel", 200),
        ("/v1/channels/x", 400),
        ("/v1/posts?channels=bad,", 400),
        ("/v1/posts?channels=runner_fixture&cursor=opaque", 400),
        ("/v1/usage", 404),
    ],
)
def test_reader_answers_other_requests_with_contract_shapes(target: str, status: int) -> None:
    code, kind, body, _ = fixture().respond(target, IDENTITY, NOW)
    assert code == status
    if status == 200:
        assert body["status"] == "not_found" and contract_errors("Channel", body) == []
    else:
        assert kind == reader.PROBLEM and contract_errors("Problem", body) == []


def test_fake_bot_api_needs_the_token_and_records_only_bot_sends() -> None:
    api = telegram_api.BotApi("123:token")
    assert api.call("/bot999:other/getMe", {})[0] == 401
    assert api.call("/bot123:token/getMe", {}) == (
        200,
        {"ok": True, "result": telegram_api.BOT_USER},
    )
    update_id = api.queue(42, "/channel @runner_fixture")
    status, body = api.call("/bot123:token/getUpdates", {"offset": "0", "timeout": "0"})
    (update,) = body["result"]
    assert status == 200 and update["update_id"] == update_id
    assert update["message"]["entities"] == [{"type": "bot_command", "offset": 0, "length": 8}]
    assert update["message"]["chat"]["id"] == update["message"]["from"]["id"] == 42
    assert api.call("/bot123:token/getUpdates", {"offset": str(update_id + 1)})[1]["result"] == []
    assert api.state()["sent"] == []
    status, body = api.call("/bot123:token/sendMessage", {"chat_id": "42", "text": "hello"})
    assert status == 200 and body["result"]["chat"]["id"] == 42
    assert api.state()["sent"] == [{"seq": 1, "chat_id": 42, "text": "hello"}]
    assert api.call("/bot123:token/sendPhoto", {})[0] == 400


def test_fake_bot_api_reads_form_and_json_parameters() -> None:
    form = telegram_api.parameters(
        "application/x-www-form-urlencoded", b"chat_id=42&text=a+b%0Ac", ""
    )
    assert form == {"chat_id": "42", "text": "a b\nc"}
    body = json.dumps({"chat_id": 42, "text": "x"}).encode()
    assert telegram_api.parameters("application/json", body, "offset=3") == {
        "offset": "3",
        "chat_id": "42",
        "text": "x",
    }


def test_synthetic_keys_match_the_platform_key_format() -> None:
    pattern = re.compile(r"cps_([a-z2-7]{12})_([A-Za-z0-9_-]{43})")
    for key in (support.product_key(), platform_admin.unknown_key()):
        assert pattern.fullmatch(key)
    key = support.product_key()
    assert support.key_id(key) == key.split("_")[1]


def test_workflow_steps_refuse_what_the_runner_cannot_execute_faithfully() -> None:
    workflow = yaml.safe_load(
        """
env: {DOCKER_BUILDKIT: "1"}
jobs:
  ci:
    env: {CI_LEVEL: unit}
    steps:
      - uses: actions/checkout@v4
      - name: Contract
        run: echo artifacts/env-${{ github.sha }}.json
        env: {COMMIT: "${{ github.sha }}"}
      - name: Login
        run: echo "${{ secrets.REGISTRY_PASSWORD }}"
      - name: Clean up
        if: always()
        run: make down
"""
    )
    steps = support.job_steps(workflow, "ci")
    assert [step.name for step in steps] == ["actions/checkout@v4", "Contract", "Login", "Clean up"]
    assert steps[0].run is None and steps[-1].always
    assert support.render_run(steps[1], "abc") == "echo artifacts/env-abc.json"
    assert support.render_env(steps[1], "abc") == {"COMMIT": "abc"}
    assert support.is_secret_step(steps[2]) and not support.is_secret_step(steps[1])
    with pytest.raises(support.WorkflowError, match="unsupported expression"):
        support.render_run(steps[2], "abc")
    assert support.workflow_env(workflow, "ci") == {"DOCKER_BUILDKIT": "1", "CI_LEVEL": "unit"}
    workflow["jobs"]["ci"]["steps"].append({"if": "github.event_name == 'push'", "run": "x"})
    with pytest.raises(support.WorkflowError, match="unsupported step condition"):
        support.job_steps(workflow, "ci")


def test_github_env_dotenv_and_redaction_helpers() -> None:
    assert support.read_github_env("A=1\nB<<EOF\nx\ny\nEOF\n") == {"A": "1", "B": "x\ny"}
    with pytest.raises(support.WorkflowError):
        support.read_github_env("not an assignment")
    rendered = support.render_dotenv("# c\nA=1\nB=2\n", {"B": "x", "C": "y"})
    assert rendered == "# c\nA=1\nB=x\nC=y\n"
    assert support.redact(
        "key cps_abc_123456 and abc_123456", ["abc_123456", "cps_abc_123456"]
    ) == ("key <redacted> and <redacted>")
    assert support.redact("short", ["sho"]) == "short"


def test_log_parsers_read_fixture_records_caddy_access_and_pushed_digests() -> None:
    logs = 'noise\nrunner-reader {"event": "request", "status": 200}\n'
    assert support.json_lines(logs, "runner-reader ") == [{"event": "request", "status": 200}]
    access = "\n".join(
        json.dumps(entry)
        for entry in (
            {
                "logger": "http.log.access.log0",
                "request": {"uri": "/tg-reader/v1/posts"},
                "status": 401,
            },
            {"logger": "http.log.access.log0", "request": {"uri": "/healthz"}, "status": 200},
            {"logger": "http.handlers", "request": {"uri": "/tg-reader/v1/posts"}, "status": 500},
        )
    )
    assert support.caddy_requests(access + "\nplain", "/tg-reader/v1/posts") == [401]
    query = json.dumps(
        {
            "logger": "http.log.access.log0",
            "request": {"uri": "/tg-reader/v1/posts?channels=runner_fixture&limit=200"},
            "status": 200,
        }
    )
    assert support.caddy_requests(query, "/tg-reader/v1/posts") == [200]
    digests = ["127.0.0.1:5000/p-backend@sha256:" + "a" * 64, "other/p@sha256:" + "b" * 64]
    assert support.pushed_digest(digests, "127.0.0.1:5000/p-backend") == "sha256:" + "a" * 64
    with pytest.raises(ValueError, match="one pushed digest"):
        support.pushed_digest(digests[1:], "127.0.0.1:5000/p-backend")


def test_runner_workflow_guards_the_platform_key_and_requires_the_evidence() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/runner-proof.yml").read_text())
    triggers = workflow[True]
    assert "paths" not in triggers["pull_request"] and "paths" not in triggers["push"]
    assert triggers["push"]["branches"] == ["main"]
    assert "PLATFORM_SERVICES_DEPLOY_KEY" in triggers["workflow_call"]["secrets"]
    job = workflow["jobs"]["fresh-product-runner"]
    assert "if" not in job, "a skipped required check would read as green"
    steps = {step["name"]: step for step in job["steps"]}
    guard = steps["Require a trusted source and the platform deploy key"]
    assert "pull_request.head.repo.full_name" in guard["env"]["HEAD_REPOSITORY"]
    assert '[ "$REF" = refs/heads/main ]' in guard["run"] and "exit 1" in guard["run"]
    assert "workflow_dispatch" not in triggers
    platform = steps["Check out the pinned platform with the read-only deploy key"]["with"]
    assert platform["repository"] == "vladmesh/codegen-platform-services"
    assert platform["ssh-key"] == "${{ secrets.PLATFORM_SERVICES_DEPLOY_KEY }}"
    assert platform["persist-credentials"] is False and platform["path"] == "platform"
    upload = steps["Preserve runner proof evidence"]
    assert upload["if"] == "always()" and upload["with"]["if-no-files-found"] == "error"
    assert upload["with"]["path"] == "${{ runner.temp }}/runner-proof"
    assert "pull_request_target" not in triggers
    assert 'evidence["status"] == "passed"' in steps["Require passed SHA-bound evidence"]["run"]


def _pending() -> dict:
    return yaml.safe_load((ROOT / support.PENDING_RELEASES).read_text())


def test_pending_release_is_the_runner_default_and_refuses_ambiguity() -> None:
    workflow = (ROOT / ".github/workflows/runner-proof.yml").read_text()
    default = re.search(r"inputs\.package_version \|\| '([^']+)'", workflow)
    release = support.pending_release(_pending(), "tg-channels")

    assert default is not None and release["version"] == default.group(1)
    assert release["catalog_entry"]["tag"] == f"packages/tg-channels/v{release['version']}"
    for broken in (
        {"format_version": 2, "releases": [release]},
        {"format_version": 1, "releases": [release, release]},
        {"format_version": 1, "releases": []},
        {
            "format_version": 1,
            "releases": [release | {"catalog_entry": release["catalog_entry"] | {"tag": "v9"}}],
        },
    ):
        with pytest.raises(ValueError, match="pending releases"):
            support.pending_release(broken, "tg-channels")


def test_fixture_catalog_appends_only_the_pending_entry() -> None:
    published = yaml.safe_load((ROOT / "packages/catalog.yaml").read_text())
    before = json.dumps(published, sort_keys=True)
    release = support.pending_release(_pending(), "tg-channels")

    snapshot = support.fixture_catalog(published, release)

    assert json.dumps(published, sort_keys=True) == before
    channels = next(item for item in snapshot["packages"] if item["name"] == "tg-channels")
    original = next(item for item in published["packages"] if item["name"] == "tg-channels")
    assert channels["versions"] == [*original["versions"], release["catalog_entry"]]
    assert [item for item in snapshot["packages"] if item["name"] != "tg-channels"] == [
        item for item in published["packages"] if item["name"] != "tg-channels"
    ]
    listed = original["versions"][-1]
    with pytest.raises(ValueError, match="already in the catalog"):
        support.fixture_catalog(published, release | {"version": listed["version"]})
    with pytest.raises(ValueError, match="another distribution"):
        support.fixture_catalog(published, release | {"distribution": "other"})


def test_catalog_fixture_routes_raw_paths_to_served_refs_only() -> None:
    refs = ["HEAD", "packages/tg-channels/v0.1.2", "packages/tg"]
    assert support.fixture_resource("/HEAD/packages/catalog.yaml", refs) == (
        "HEAD",
        "packages/catalog.yaml",
    )
    assert support.fixture_resource(
        "/packages/tg-channels/v0.1.2/packages/codegen-kit-tg-channels/x/package.yaml", refs
    ) == ("packages/tg-channels/v0.1.2", "packages/codegen-kit-tg-channels/x/package.yaml")
    for path in ("/main/packages/catalog.yaml", "/HEAD/", "/HEAD/../secret", "HEAD/x"):
        assert support.fixture_resource(path, refs) is None


def test_runner_workflow_runs_the_explicit_mode_release_matrix() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/runner-proof.yml").read_text())
    inputs = workflow[True]["workflow_call"]["inputs"]
    job = workflow["jobs"]["fresh-product-runner"]
    steps = {step["name"]: step for step in job["steps"]}

    assert inputs["proof_mode"]["required"] is True
    assert inputs["package_version"]["required"] is True
    assert job["env"]["PROOF_MODE"] == "${{ inputs.proof_mode || 'candidate_release' }}"
    assert job["strategy"]["fail-fast"] is False
    assert {item["leg"]: item["packages"] for item in job["strategy"]["matrix"]["include"]} == {
        "fresh": "tg-channels",
        "coexistence": "reminders,tg-channels",
    }
    runner = steps["Run the fresh product runner proof"]["run"]
    for argument in ("--proof-mode", "--package-version", "--packages"):
        assert argument in runner
    guard = steps["Require a trusted source and the platform deploy key"]["run"]
    assert "candidate_release|published_release" in guard
    required = steps["Require passed SHA-bound evidence"]["run"]
    for check in (
        'evidence["proof_mode"] == release["mode"] == mode',
        'release["source_sha"] == release["tag_target"] == pinned["kit"]',
        'evidence["module"]["version"] == release["version"] == version',
        'scenario["reminders"]',
    ):
        assert check in required
    assert "matrix.leg" in steps["Preserve runner proof evidence"]["with"]["name"]
