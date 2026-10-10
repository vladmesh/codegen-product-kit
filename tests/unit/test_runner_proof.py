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


#: A pending release as packages/pending-releases.yaml pins one; nothing is pending now that
#: tg-channels 0.1.2 is published, so the candidate_release helpers are checked on this sample.
SAMPLE_PENDING = {
    "package": "tg-channels",
    "distribution": "codegen-kit-tg-channels",
    "path": "packages/codegen-kit-tg-channels",
    "version": "0.1.3",
    "supersedes": "0.1.2",
    "notes": "docs/releases/tg-channels-0.1.3.md",
    "catalog_entry": {
        "version": "0.1.3",
        "tag": "packages/tg-channels/v0.1.3",
        "requires_core": ">=2.4,<3",
    },
}
PIN = support.PUBLISHED_RELEASES[("tg-channels", "0.1.2")]


def test_runner_default_is_the_pinned_newest_published_release() -> None:
    workflow = (ROOT / ".github/workflows/runner-proof.yml").read_text()
    default = re.search(r"inputs\.package_version \|\| '([^']+)'", workflow)
    catalog = yaml.safe_load((ROOT / "packages/catalog.yaml").read_text())
    newest = support.catalog_package(catalog, "tg-channels")["versions"][-1]
    pending = yaml.safe_load((ROOT / support.PENDING_RELEASES).read_text())

    assert default is not None and newest["version"] == default.group(1)
    assert support.published_release("tg-channels", default.group(1)) == PIN
    assert newest == {"version": "0.1.2", "tag": PIN.tag, "requires_core": ">=2.4,<3"}
    assert pending == {"format_version": 1, "releases": []}
    with pytest.raises(ValueError, match="pending releases: expected one tg-channels entry"):
        support.pending_release(pending, "tg-channels")
    with pytest.raises(ValueError, match="not a pinned published release"):
        support.published_release("tg-channels", "0.1.1")


def test_pending_release_refuses_ambiguity() -> None:
    release = SAMPLE_PENDING
    assert support.pending_release({"format_version": 1, "releases": [release]}, "tg-channels") == (
        release
    )
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
    release = support.pending_release(
        {"format_version": 1, "releases": [SAMPLE_PENDING]}, "tg-channels"
    )

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


def test_catalog_modes_pair_only_with_their_proof_mode() -> None:
    for proof_mode, catalog_modes in support.ADMITTED_CATALOG_MODES.items():
        for catalog_mode in support.CATALOG_MODES:
            if catalog_mode in catalog_modes:
                support.check_modes(proof_mode, catalog_mode)
            else:
                with pytest.raises(ValueError, match="admits catalog modes"):
                    support.check_modes(proof_mode, catalog_mode)
    assert support.ADMITTED_CATALOG_MODES["published_release"] == (
        "remote_head",
        "candidate_snapshot",
    )
    with pytest.raises(ValueError, match="unknown proof mode"):
        support.check_modes("published", "remote_head")


def _remote(**changes: str) -> dict[str, str]:
    listing = (
        f"{PIN.tag_object}\trefs/tags/{PIN.tag}\n"
        f"{PIN.target}\trefs/tags/{PIN.tag}^{{}}\n"
        "448a2ad6cbfcf562c8ea96ef9e57d0003cc7f8de\trefs/tags/packages/tg-channels/v0.1.1\n"
        "644a0d253a5f669dce9ac46537c92e628131fd8e\trefs/tags/packages/tg-channels/v0.1.1^{}\n"
    )
    remote = support.ls_remote_refs(listing)
    for key, value in changes.items():
        ref = f"refs/tags/{PIN.tag}" + ("^{}" if key == "target" else "")
        if value:
            remote[ref] = value
        else:
            remote.pop(ref)
    return remote


def test_published_tag_must_be_the_pinned_remote_tag_and_its_fetched_copy() -> None:
    fetched = {"type": "tag", "tag_object": PIN.tag_object, "target": PIN.target, "tree": PIN.tree}
    other = "0" * 40

    assert support.verify_published_tag(PIN, _remote(), fetched) == {"tag": PIN.tag, **fetched}
    with pytest.raises(ValueError, match="has no tag"):
        support.verify_published_tag(PIN, _remote(tag_object=""), fetched)
    for remote in (_remote(tag_object=other), _remote(target=other), _remote(target="")):
        with pytest.raises(ValueError, match="not the pinned release"):
            support.verify_published_tag(PIN, remote, fetched)
    for key, value in (("type", "commit"), ("tree", other), ("target", other), ("tree", None)):
        with pytest.raises(ValueError, match="the fetched"):
            support.verify_published_tag(PIN, _remote(), fetched | {key: value})


def test_copied_tags_and_ls_remote_listing_are_exact() -> None:
    remote = _remote()
    support.verify_copied_tags(remote, {PIN.tag: PIN.tag_object})
    with pytest.raises(ValueError, match="differ from the real remote"):
        support.verify_copied_tags(remote, {PIN.tag: PIN.target})
    with pytest.raises(ValueError, match="differ from the real remote"):
        support.verify_copied_tags(remote, {"packages/tg-channels/v0.1.3": PIN.tag_object})
    for listing in ("abc\trefs/tags/x", f"{PIN.target}\tHEAD\n{PIN.target}\tHEAD"):
        with pytest.raises(ValueError, match="unexpected ls-remote line"):
            support.ls_remote_refs(listing)
    catalog = yaml.safe_load((ROOT / "packages/catalog.yaml").read_text())
    assert PIN.tag in support.released_tags(catalog)
    assert "packages/textparse/v0.1.0" in support.released_tags(catalog)


def test_catalog_digest_follows_content_not_formatting() -> None:
    text = (ROOT / "packages/catalog.yaml").read_text()
    document = yaml.safe_load(text)
    digest = support.catalog_digest(text)

    assert support.catalog_digest(yaml.safe_dump(document, sort_keys=True)) == digest
    support.catalog_package(document, "tg-channels")["versions"].pop()
    assert support.catalog_digest(yaml.safe_dump(document)) != digest


def test_snapshot_reads_must_all_reach_the_snapshot_bytes() -> None:
    catalog = "/vladmesh/codegen-product-kit/HEAD/packages/catalog.yaml"
    binding = f"/vladmesh/codegen-product-kit/{PIN.tag}/packages/x/bindings/default.yaml"
    served = {"/HEAD/packages/catalog.yaml": "a" * 64}
    reads = [
        {"path": catalog, "status": 200, "sha256": "a" * 64},
        {"path": binding, "status": 200, "sha256": "b" * 64},
    ]

    support.verify_snapshot_reads(reads, served)
    with pytest.raises(ValueError, match="answered 404"):
        support.verify_snapshot_reads([*reads, {"path": binding, "status": 404}], served)
    with pytest.raises(ValueError, match="other bytes"):
        support.verify_snapshot_reads([reads[0] | {"sha256": "c" * 64}], served)
    with pytest.raises(ValueError, match="never read"):
        support.verify_snapshot_reads(reads[1:], served)


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
    assert inputs["catalog_mode"]["required"] is True
    assert inputs["package_version"]["required"] is True
    assert job["env"]["PROOF_MODE"] == "${{ inputs.proof_mode || 'published_release' }}"
    # Own runs: a pull request proves the prospective snapshot, a main push the real HEAD.
    assert job["env"]["CATALOG_MODE"] == (
        "${{ inputs.catalog_mode || (github.event_name == 'pull_request' && "
        "'candidate_snapshot') || 'remote_head' }}"
    )
    assert job["env"]["CALLED"] == "${{ inputs.kit_sha != '' }}"
    assert job["strategy"]["fail-fast"] is False
    assert {item["leg"]: item["packages"] for item in job["strategy"]["matrix"]["include"]} == {
        "fresh": "tg-channels",
        "coexistence": "reminders,tg-channels",
    }
    runner = steps["Run the fresh product runner proof"]["run"]
    for argument in ("--proof-mode", "--catalog-mode", "--package-version", "--packages"):
        assert argument in runner
    guard = steps["Require a trusted source and the platform deploy key"]["run"]
    assert "published_release/remote_head|published_release/candidate_snapshot)" in guard
    assert "candidate_release/pending_fixture)" in guard
    assert 'if [ "$EVENT" = pull_request ]; then expected=candidate_snapshot; fi' in guard
    assert '[ "$CATALOG_MODE" != "$expected" ]' in guard
    required = steps["Require passed SHA-bound evidence"]["run"]
    for check in (
        'evidence["proof_mode"] == release["mode"] == mode',
        'evidence["catalog_mode"] == catalog["mode"] == catalog_mode',
        'release["source_sha"] == release["tag_target"] == pinned["kit"]',
        'evidence["module"]["version"] == release["version"] == version',
        'pin = support.published_release("tg-channels", version)',
        'catalog["prospective"] is True and catalog["commit"] == pinned["kit"]',
        'catalog["prospective"] is False and "http_requests" not in catalog',
        'catalog["after_install"]["catalog_digest"] == catalog["catalog_digest"]',
        'scenario["reminders"]',
    ):
        assert check in required
    assert "matrix.leg" in steps["Preserve runner proof evidence"]["with"]["name"]


def test_host_problems_require_core_language_and_the_exact_registry() -> None:
    found = {
        "settings": {"language": "core", "timezone": "tg_bot"},
        "bindings": {"tg-channels": {"commands": ["channel", "channels", "digest"]}},
        "registry": [
            ["start", "core"],
            ["command", "core"],
            ["channel", "package:tg-channels"],
            ["channels", "package:tg-channels"],
            ["digest", "package:tg-channels"],
        ],
        "host_violations": [],
    }
    assert support.host_problems(found) == []
    assert support.host_problems(found | {"settings": {"language": "tg_bot"}}) == [
        "language owner is 'tg_bot', not core"
    ]
    assert support.host_problems(found | {"registry": found["registry"][:-1]})
    assert support.host_problems(found | {"host_violations": ["command_collision: x"]})


def test_language_probes_have_their_own_chats_and_replies_are_associated_by_chat() -> None:
    chats = [
        support.probe_chat(locale, kind)
        for locale in support.LANGUAGES
        for kind, _ in support.LANGUAGE_PROBES
    ]
    assert len(set(chats)) == 6 and 424242001 not in chats
    probe = support.probe_chat("en", "unknown_command")
    previous = support.probe_chat("ru", "channel_without_name")
    sent = [
        {"seq": 4, "chat_id": probe, "text": "before the input"},
        # A late reply to the previous input lands after this input's watermark: not this one's.
        {"seq": 6, "chat_id": previous, "text": "Введите имя публичного канала."},
        {
            "seq": 7,
            "chat_id": probe,
            "text": "Не понимаю это сообщение. Доступные команды: /channel",
        },
        {"seq": 8, "chat_id": probe, "text": "I don't understand this message. /channel"},
    ]
    replies = support.probe_replies(sent, probe, watermark=5)
    # The first reply is the evidence even in the wrong language; nothing is filtered by text.
    assert [item["seq"] for item in replies] == [7, 8]
    assert support.language_reply_problems("unknown_command", "en", replies[0]) == [
        'expected the en unknown reply "I don\'t understand this message. Available commands: "'
    ]
    assert support.language_reply_problems("unknown_command", "ru", replies[0]) == []
    assert support.language_reply_problems("channel_without_name", "ru", None) == [
        "no reply in the probe chat"
    ]
    recorded = [
        {"locale": "en", "kind": "unknown_command", "chat_id": probe, "watermark": 5},
        {"locale": "ru", "kind": "channel_without_name", "chat_id": previous, "watermark": 2},
    ]
    sent.append({"seq": 9, "chat_id": 424242001, "text": "New post: @runner_fixture"})
    sent.append({"seq": 10, "chat_id": 31337, "text": "stray"})
    ledger = support.language_ledger(sent, recorded, watermark=3, background=[424242001])
    assert list(ledger["duplicates"]) == ["en/unknown_command"]
    assert [item["seq"] for item in ledger["duplicates"]["en/unknown_command"]] == [7, 8]
    assert ledger["unmatched"] == [{"seq": 10, "chat_id": 31337, "text": "stray"}]
    assert ledger["background"] == [{"seq": 9, "chat_id": 424242001}]


class _LanguageProduct:
    """The settings/users API and a bot answering through the real fixture `BotApi`, offline."""

    def __init__(self, *, stale_reply: str | None = None, late: bool = False) -> None:
        self.api = telegram_api.BotApi("123:token")
        self.language = "en"
        self.readback: str | None = None  # A readback that disagrees with the stored value.
        self.stale_reply = stale_reply  # "en/unknown_command": answered in the other language.
        self.late = late  # Each input also brings a late second reply to the previous chat.
        self.granted: set[str] = set()
        self.previous: int | None = None

    def __call__(self, method, url, body=None, headers=None):
        from urllib.parse import parse_qs, urlsplit

        target = urlsplit(url)
        if target.path in ("/settings/set", "/settings/get"):
            if body["scope"] != "product":
                return 422, {"detail": "Setting is only available in product scope"}
            if target.path == "/settings/set":
                self.language = body["value"]
            return 200, {**body, "value": self.readback or self.language}
        if target.path == "/users/grant":
            self.granted.add(body["external_id"])
            return 200, {"status": "active"}
        if target.path == "/users/access":
            granted = parse_qs(target.query)["external_id"][0] in self.granted
            return 200, {"status": "active" if granted else "inactive"}
        if target.path == "/control/state":
            return 200, self.api.state()
        assert target.path == "/control/messages"
        update_id = self.api.queue(body["user_id"], body["text"])
        if self.late and self.previous is not None:
            self.api.message(self.previous, "late second reply")
        self._answer(body["user_id"], body["text"])
        self.previous = body["user_id"]
        return 200, {"update_id": update_id}

    def _answer(self, chat: int, text: str) -> None:
        kind = next(kind for kind, probe in support.LANGUAGE_PROBES if probe == text)
        language = self.language
        if self.stale_reply == f"{language}/{kind}":
            language = "ru" if language == "en" else "en"
        replies = support.LANGUAGE_REPLIES[language]
        if kind == "channel_without_name":
            self.api.message(chat, replies["channel"])
        else:
            self.api.message(chat, replies["unknown"] + "/start, /command, /channel")


def _language_runner(monkeypatch: pytest.MonkeyPatch, product: _LanguageProduct):
    import importlib
    from types import SimpleNamespace

    monkeypatch.syspath_prepend(str(ROOT / "tests/runner"))
    fresh_product = importlib.import_module("fresh_product")
    monkeypatch.setattr(fresh_product, "http", product)
    runner = fresh_product.Runner.__new__(fresh_product.Runner)
    runner.evidence = {"scenario": {}}
    deployment = SimpleNamespace(
        port=8000, values={"SETTINGS_WRITE_CAPABILITY": "w", "USERS_GRANT_CAPABILITY": "g"}
    )
    product.api.message(424242001, "New post: @runner_fixture")  # The earlier delivery.
    return fresh_product, runner, deployment


def test_language_scenario_records_readback_inputs_and_replies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    product = _LanguageProduct()
    fresh_product, runner, deployment = _language_runner(monkeypatch, product)
    runner.language_scenario(deployment, "http://control")
    languages = runner.evidence["scenario"]["languages"]
    assert languages["user_scope"] == {"set": {"status": 422}, "get": {"status": 422}}
    assert languages["watermark"] == 1 and len(languages["chats"]) == 6
    inputs = {item["update_id"]: item for item in product.api.state()["inputs"]}
    for locale in support.LANGUAGES:
        phase = languages[locale]
        assert phase["setting"]["status"] == 200
        assert phase["readback"]["body"]["value"] == locale
        for kind, text in support.LANGUAGE_PROBES:
            probe = phase["probes"][kind]
            assert inputs[probe["update_id"]] == {
                "update_id": probe["update_id"],
                "chat_id": support.probe_chat(locale, kind),
                "text": text,
            }
            assert probe["reply"]["chat_id"] == probe["chat_id"] and probe["problems"] == []
    assert languages["ledger"]["duplicates"] == {} and languages["ledger"]["unmatched"] == []
    assert "fixture_state" not in languages


def test_wrong_locale_reply_fails_and_keeps_the_partial_phase(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    product = _LanguageProduct(stale_reply="en/unknown_command")
    fresh_product, runner, deployment = _language_runner(monkeypatch, product)
    with pytest.raises(fresh_product.ProofError, match="en unknown_command reply is wrong"):
        runner.language_scenario(deployment, "http://control")
    languages = runner.evidence["scenario"]["languages"]
    assert all(not probe["problems"] for probe in languages["ru"]["probes"].values())
    en = languages["en"]
    assert en["readback"]["body"]["value"] == "en"
    assert list(en["probes"]) == ["unknown_command"]  # Nothing after the failing probe.
    probe = en["probes"]["unknown_command"]
    assert probe["reply"]["text"].startswith("Не понимаю")  # Kept, not filtered out.
    assert probe["problems"] and isinstance(probe["update_id"], int)
    state = languages["fixture_state"]
    assert [item["chat_id"] for item in state["sent"]][-1] == probe["chat_id"]
    assert state["inputs"][-1]["update_id"] == probe["update_id"]


def test_a_late_reply_to_a_previous_probe_is_a_duplicate_not_the_next_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    product = _LanguageProduct(late=True)
    fresh_product, runner, deployment = _language_runner(monkeypatch, product)
    with pytest.raises(fresh_product.ProofError, match="duplicate or unmatched"):
        runner.language_scenario(deployment, "http://control")
    languages = runner.evidence["scenario"]["languages"]
    for locale in support.LANGUAGES:
        for probe in languages[locale]["probes"].values():
            assert probe["reply"]["text"] != "late second reply" and probe["problems"] == []
    assert len(languages["ledger"]["duplicates"]) == 5  # Every probe chat but the last.
    assert languages["fixture_state"]["sent"]


def test_a_readback_that_disagrees_stops_before_any_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    product = _LanguageProduct()
    product.readback = "en"
    fresh_product, runner, deployment = _language_runner(monkeypatch, product)
    with pytest.raises(fresh_product.ProofError, match="core language ru was not stored"):
        runner.language_scenario(deployment, "http://control")
    ru = runner.evidence["scenario"]["languages"]["ru"]
    assert ru["setting"]["status"] == 200 and ru["readback"]["body"]["value"] == "en"
    assert ru["probes"] == {} and product.api.state()["inputs"] == []
