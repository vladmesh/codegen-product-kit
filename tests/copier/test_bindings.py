"""Generated bindings from actual locally installed components; remote proof is CI-only."""

from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys

import pytest
import yaml

from framework import host_contract
from framework.bindings import BindingError
from framework.cli import bind_package
from framework.generate import generate_all
from framework.spec.loader import SpecValidationError

ROOT = Path(__file__).parents[2]


def _snapshot(root):
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
        and not any(part.startswith((".venv", ".git", ".pytest")) for part in path.parts)
        and "__pycache__" not in path.parts
    }


@pytest.fixture(scope="module")
def bound_product(project_backend_tg_bot, tmp_path_factory):
    product = tmp_path_factory.mktemp("binding-product") / "product"
    shutil.copytree(project_backend_tg_bot, product)
    for service in ("backend", "tg_bot"):
        result = subprocess.run(
            ["uv", "sync", "--project", f"services/{service}", "--frozen"],
            cwd=product,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr
    for service, package in (("backend", "reminders"), ("tg_bot", "textparse")):
        result = subprocess.run(
            [
                "uv",
                "pip",
                "install",
                "--python",
                str(product / f"services/{service}/.venv/bin/python"),
                str(ROOT / f"packages/codegen-kit-{package}"),
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr
    manifest = product / "services/backend/manifest.yaml"
    data = yaml.safe_load(manifest.read_text())
    data["packages"] = ["reminders"]
    data["settings_schema"]["properties"]["unrelated"] = {"type": "integer"}
    manifest.write_text(yaml.safe_dump(data, sort_keys=False))
    bind_package("reminders", product)
    return product


@pytest.fixture(scope="module")
def bound_product_v2(bound_product, tmp_path_factory):
    product = tmp_path_factory.mktemp("binding-v2-product") / "product"
    shutil.copytree(bound_product, product, symlinks=True)
    result = subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--python",
            str(product / "services/backend/.venv/bin/python"),
            str(ROOT / "tests/fixtures/binding_v2_package"),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    manifest = product / "services/backend/manifest.yaml"
    data = yaml.safe_load(manifest.read_text())
    data["packages"].append("binding-notes")
    manifest.write_text(yaml.safe_dump(data, sort_keys=False))
    assert bind_package("binding-notes", product) == "bound"
    return product


@pytest.fixture(scope="module")
def bound_product_v2_only(bound_product_v2, tmp_path_factory):
    product = tmp_path_factory.mktemp("binding-v2-only-product") / "product"
    shutil.copytree(bound_product_v2, product, symlinks=True)
    (product / "services/tg_bot/bindings/reminders.yaml").unlink()
    generate_all(product)
    return product


def test_generated_default_handlers_execute_real_parser(bound_product):
    product = bound_product
    result = subprocess.run(
        [
            str(product / "services/tg_bot/.venv/bin/python"),
            str(ROOT / "tests/copier/binding_scenarios.py"),
        ],
        cwd=product,
        env=__import__("os").environ | {"PYTHONPATH": f"{product}:{product / 'shared'}"},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert '"real_parser": true' in result.stdout


def test_generated_relay_unit_transport_and_lifecycle(bound_product):
    product = bound_product
    result = subprocess.run(
        [
            str(product / "services/tg_bot/.venv/bin/python"),
            str(ROOT / "tests/copier/binding_relay_unit.py"),
        ],
        cwd=product,
        env=__import__("os").environ | {"PYTHONPATH": f"{product}:{product / 'shared'}"},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "generated relay unit transport and lifecycle passed" in result.stdout


def test_bound_product_handler_unit_tests_need_no_redis(bound_product):
    """The product's own lifecycle unit test must isolate its installed binding relay."""
    product = bound_product
    result = subprocess.run(
        [
            str(product / "services/tg_bot/.venv/bin/pytest"),
            "services/tg_bot/tests/unit/test_command_handler.py",
            "-q",
        ],
        cwd=product,
        env=__import__("os").environ
        | {
            "PYTHONPATH": f"{product}:{product / 'shared'}",
            "REDIS_URL": "redis://redis.invalid:6379",
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _git(product, *args):
    return subprocess.run(["git", *args], cwd=product, capture_output=True, text=True, check=False)


def _generated_bytes(product):
    trees = [product / "shared/shared/generated", *product.glob("services/*/src/generated")]
    return {
        str(path.relative_to(product)): path.read_bytes()
        for tree in trees
        for path in sorted(tree.rglob("*.py"))
    }


@pytest.mark.parametrize("fixture", ["bound_product", "bound_product_v2", "bound_product_v2_only"])
def test_bound_product_generation_passes_its_own_drift_and_lint(request, fixture, tmp_path):
    """Product CI: generation, drift diff, then make lint's ruff and xenon steps, twice."""
    product = tmp_path / "product"
    shutil.copytree(request.getfixturevalue(fixture), product, symlinks=True)
    # The product generator resolves ruff from the root venv `make setup` creates; use the
    # kit's pinned ruff there so the real formatter and fixer run, not a silent skip.
    (product / ".venv/bin").mkdir(parents=True)
    for tool in ("ruff", "xenon"):
        executable = Path(sys.executable).with_name(tool)
        assert executable.is_file(), executable
        (product / f".venv/bin/{tool}").symlink_to(executable)
    (product / ".gitignore").write_text(".venv/\n**/.venv/\n__pycache__/\n")
    lint = re.search(r"^lint:\n((?:\t.*\n)+)", (product / "Makefile").read_text(), re.M)
    assert lint is not None
    lint_steps = [
        line.strip().replace("$(VENV)", ".venv/bin")
        for line in lint.group(1).splitlines()
        if line.strip().startswith(("$(VENV)/ruff ", "$(VENV)/xenon "))
    ]
    assert [step.split()[0] for step in lint_steps] == [
        ".venv/bin/ruff",
        ".venv/bin/ruff",
        ".venv/bin/xenon",
    ]
    assert [step.split()[1] for step in lint_steps[:2]] == ["format", "check"]

    for step in (
        ("init", "-q"),
        ("config", "user.email", "test@example.com"),
        ("config", "user.name", "Test User"),
    ):
        assert _git(product, *step).returncode == 0
    generate_all(product)
    serialized = "bindings.py" if fixture == "bound_product" else "bindings_v1.py"
    assert (
        "DATA = json.loads(" in (product / "services/tg_bot/src/generated" / serialized).read_text()
    )
    if fixture == "bound_product":
        for name in ("bindings.py", "binding_relay.py"):
            expected = ROOT / "tests/fixtures/binding_v1_baseline" / (name + ".txt")
            assert (
                product / "services/tg_bot/src/generated" / name
            ).read_bytes() == expected.read_bytes()
    assert _git(product, "add", "-A").returncode == 0
    assert _git(product, "commit", "-qm", "bound product").returncode == 0

    outputs = []
    for _ in range(2):
        generate_all(product)
        outputs.append(_generated_bytes(product))
        drift = _git(
            product,
            "diff",
            "--exit-code",
            "--",
            "shared/shared/generated/",
            "services/*/src/generated/",
        )
        assert drift.returncode == 0, drift.stdout + drift.stderr
        for step in lint_steps:
            result = subprocess.run(
                shlex.split(step), cwd=product, capture_output=True, text=True, check=False
            )
            assert result.returncode == 0, f"{step}\n{result.stdout}{result.stderr}"
    assert outputs[0] == outputs[1]
    assert "services/tg_bot/src/generated/bindings.py" in outputs[0]
    assert "services/tg_bot/src/generated/commands.py" in outputs[0]
    assert host_contract.check_product(product).violations == []


def test_binding_idempotence_timezone_and_product_override(bound_product):
    product = bound_product
    before = _snapshot(product)
    assert bind_package("reminders", product) == "unchanged"
    assert _snapshot(product) == before
    registry = (product / "services/backend/src/generated/settings_schemas.py").read_text()
    assert "x-iana-tz" in registry and "unrelated" in registry
    file = product / "services/tg_bot/bindings/reminders.yaml"
    original = file.read_text()
    data = yaml.safe_load(original)
    data["commands"][0]["command"] = "remember"
    data["commands"][0]["reply"]["parts"][0] = "Product reply for "
    try:
        file.write_text(yaml.safe_dump(data, sort_keys=False))
        before = _snapshot(product)
        with pytest.raises(BindingError, match="BindingOwnedFileError"):
            bind_package("reminders", product)
        assert _snapshot(product) == before
        generate_all(product)
        generated = (product / "services/tg_bot/src/generated/bindings.py").read_text()
        assert "remember" in generated and "Product reply for" in generated
        test_generated_default_handlers_execute_real_parser(product)
    finally:
        file.write_text(original)
        generate_all(product)


@pytest.mark.parametrize(
    "bad", ["duplicate", "reserved", "mapping", "recipient", "timezone", "library"]
)
def test_regeneration_refuses_before_output_changes(bound_product, bad):
    product = bound_product
    file = product / "services/tg_bot/bindings/reminders.yaml"
    original = file.read_text()
    extra = file.with_name("duplicate.yaml")
    data = yaml.safe_load(original)
    library = product / "services/tg_bot/.venv"
    hidden = library.with_name(".venv-hidden")
    try:
        if bad == "duplicate":
            extra.write_text(original)
        elif bad == "reserved":
            data["commands"][0]["command"] = "start"
        elif bad == "mapping":
            data["commands"][0]["args"]["text"] = "$parsed.at"
        elif bad == "recipient":
            data["events"][0]["to"] = "$event.text"
        elif bad == "timezone":
            data["timezone"]["key"] = "undeclared"
        else:
            library.rename(hidden)
        file.write_text(yaml.safe_dump(data, sort_keys=False))
        before = _snapshot(product)
        with pytest.raises(BindingError):
            generate_all(product)
        assert _snapshot(product) == before
    finally:
        file.write_text(original)
        extra.unlink(missing_ok=True)
        if hidden.exists():
            hidden.rename(library)


def test_no_binding_seeds_are_importable_without_channel_or_parser(
    project_backend_tg_bot, project_standalone
):
    for product in (project_backend_tg_bot, project_standalone):
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "from services.tg_bot.src.generated import bindings; "
                "import sys; assert 'codegen_kit_textparse' not in sys.modules; "
                "assert 'faststream' not in sys.modules; bindings.register(object())",
            ],
            cwd=product,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr


def _installed_file(product, service, expression):
    result = subprocess.run(
        [str(product / f"services/{service}/.venv/bin/python"), "-I", "-c", expression],
        capture_output=True,
        text=True,
        check=True,
    )
    return Path(result.stdout.strip())


@pytest.mark.parametrize(
    "bad",
    [
        "default",
        "actions",
        "core",
        "version",
        "library-version",
        "library-module",
        "library-signature",
        "library-missing",
        "product-core",
        "malformed",
        "settings",
    ],
)
def test_bind_refusals_are_nonmutating(bound_product, tmp_path, bad):  # noqa: PLR0912, C901
    product = bound_product
    installed = _installed_file(
        product,
        "backend",
        "from importlib.metadata import distribution; "
        "print(distribution('codegen-kit-reminders').locate_file('codegen_kit_reminders/package.yaml'))",
    )
    source = product / "services/tg_bot/bindings/reminders.yaml"
    changes = {}
    binding_file = None
    try:
        if bad in {"default", "actions", "core", "version"}:
            changes[installed] = installed.read_bytes()
            data = yaml.safe_load(installed.read_text())
            if bad == "default":
                data.pop("default_binding")
            elif bad == "actions":
                data["actions"] = []
            elif bad == "core":
                data["requires_core"] = ">=3,<4"
            else:
                data["version"] = "0.4.0"
            installed.write_text(yaml.safe_dump(data, sort_keys=False))
        elif bad.startswith("library"):
            expression = (
                "from importlib.metadata import distribution; "
                "d=distribution('codegen-kit-textparse'); "
                "print(d.locate_file(next(f for f in d.files if f.name == 'METADATA')))"
            )
            if bad in {"library-module", "library-signature"}:
                expression = (
                    "from importlib.metadata import distribution; "
                    "print(distribution('codegen-kit-textparse').locate_file("
                    "'codegen_kit_textparse/__init__.py'))"
                )
            path = _installed_file(product, "tg_bot", expression)
            changes[path] = path.read_bytes()
            if bad in {"library-module", "library-missing"}:
                path.unlink()
            elif bad == "library-signature":
                path.write_text("raise AssertionError('tooling must not import this')\n")
            else:
                path.write_text(path.read_text().replace("Version: 0.1.0", "Version: 9.0.0"))
        elif bad == "product-core":
            path = product / "codegen_kit/packages.py"
            changes[path] = path.read_bytes()
            path.write_text(
                path.read_text().replace('CORE_VERSION = "2.5.0"', 'CORE_VERSION = "2.1.0"')
            )
        elif bad == "malformed":
            binding_file = tmp_path / "bad.yaml"
            binding_file.write_text("binding_version: 9000\n")
        else:
            path = product / "services/tg_bot/manifest.yaml"
            changes[path] = path.read_bytes()
            data = yaml.safe_load(path.read_text())
            data["settings_schema"]["properties"]["timezone"] = {"type": "integer"}
            path.write_text(yaml.safe_dump(data, sort_keys=False))
        before = _snapshot(product)
        with pytest.raises((BindingError, SpecValidationError)):
            bind_package("reminders", product, binding_file=binding_file)
        assert _snapshot(product) == before
        assert source.is_file()
    finally:
        for path, content in changes.items():
            path.write_bytes(content)


def test_bind_rejects_host_environment_and_supports_explicit_override(bound_product, tmp_path):
    product = bound_product
    environment = product / "services/tg_bot/.venv"
    config = environment / "pyvenv.cfg"
    original = config.read_bytes()
    try:
        config.unlink()
        before = _snapshot(product)
        with pytest.raises(BindingError, match="BindingEnvironmentError"):
            bind_package("reminders", product)
        assert _snapshot(product) == before
    finally:
        config.write_bytes(original)
    file = product / "services/tg_bot/bindings/reminders.yaml"
    original = file.read_text()
    custom = yaml.safe_load(original)
    custom["commands"][0]["command"] = "remember"
    override = tmp_path / "product-override.yaml"
    override.write_text(yaml.safe_dump(custom, sort_keys=False))
    try:
        assert bind_package("reminders", product, binding_file=override) == "bound"
        assert file.read_text() == override.read_text()
    finally:
        file.write_text(original)
        generate_all(product)


def test_default_resource_containment_and_tooling_does_not_execute_runtime(bound_product, tmp_path):
    product = bound_product
    module = _installed_file(
        product,
        "backend",
        "from importlib.metadata import distribution; "
        "print(distribution('codegen-kit-reminders').locate_file('codegen_kit_reminders'))",
    )
    resource = module / "bindings/default.yaml"
    original = resource.read_bytes()
    outside = tmp_path / "outside.yaml"
    outside.write_bytes(original)
    try:
        resource.unlink()
        resource.symlink_to(outside)
        before = _snapshot(product)
        with pytest.raises(BindingError, match="BindingResourceError"):
            bind_package("reminders", product)
        assert _snapshot(product) == before
    finally:
        resource.unlink()
        resource.write_bytes(original)
    runtime = module / "__init__.py"
    original = runtime.read_bytes()
    try:
        runtime.write_text("raise AssertionError('tooling must not import package runtime')\n")
        assert bind_package("reminders", product) == "unchanged"
    finally:
        runtime.write_bytes(original)


def test_bind_rejects_backendless_shape_without_mutation(project_standalone):
    before = _snapshot(project_standalone)
    with pytest.raises(BindingError, match="BindingProductShapeError"):
        bind_package("reminders", project_standalone)
    assert _snapshot(project_standalone) == before
