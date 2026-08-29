"""Shared fixtures for copier template tests."""

import atexit
from pathlib import Path
import shutil
import subprocess
import tempfile

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
VENV_COPIER = REPO_ROOT / ".venv" / "bin" / "copier"

BASE_DATA = {
    "project_name": "test-project",
    "project_description": "Test project description",
    "author_name": "Test Author",
    "author_email": "test@example.com",
    "python_version": "3.12",
}


_TEMPLATE_SOURCE: Path | None = None


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)  # noqa: S603


def template_source() -> Path:
    """The template repository copier reads: this checkout, or a committed snapshot of it.

    Copier records ``git describe`` of the source in ``.copier-answers.yml`` and checks that ref
    out again on ``copier update``. On a dirty checkout it describes a throwaway commit it made in
    its own temporary clone, so the recorded ref exists nowhere and ``update`` fails. The tests
    still have to see uncommitted edits — that is what a worker is validating — so a dirty checkout
    is snapshotted once per session into a temporary clone with the working tree committed on top.
    A clean checkout is used as is.
    """
    global _TEMPLATE_SOURCE
    if _TEMPLATE_SOURCE is not None:
        return _TEMPLATE_SOURCE
    status = _git("status", "--porcelain", "--untracked-files=all", cwd=REPO_ROOT).stdout
    if not status.strip():
        _TEMPLATE_SOURCE = REPO_ROOT
        return _TEMPLATE_SOURCE
    snapshot_root = Path(tempfile.mkdtemp(prefix="service-template-snapshot-"))
    atexit.register(shutil.rmtree, snapshot_root, True)
    clone = snapshot_root / "template"
    _git("clone", "--quiet", "--no-hardlinks", str(REPO_ROOT), str(clone), cwd=REPO_ROOT)
    _git(
        "checkout",
        "--quiet",
        "--detach",
        _git("rev-parse", "HEAD", cwd=REPO_ROOT).stdout.strip(),
        cwd=clone,
    )
    diff = subprocess.run(  # noqa: S603
        ["git", "diff", "--binary", "HEAD"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    if diff:
        subprocess.run(
            ["git", "apply", "--index"],
            cwd=clone,
            input=diff,
            check=True,
            text=True,
            capture_output=True,
        )  # noqa: S603
    untracked = _git("ls-files", "--others", "--exclude-standard", cwd=REPO_ROOT).stdout.split("\n")
    for relative in filter(None, untracked):
        target = clone / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / relative, target)
    _git("add", "-A", cwd=clone)
    _git(
        "-c",
        "user.email=tests@example.com",
        "-c",
        "user.name=Template tests",
        "commit",
        "--quiet",
        "-m",
        "Working tree snapshot",
        cwd=clone,
    )
    _TEMPLATE_SOURCE = clone
    return _TEMPLATE_SOURCE


@pytest.fixture(scope="session", autouse=True)
def copier_available():
    """Check if copier is available in the project venv."""
    if not VENV_COPIER.exists():
        pytest.skip(f"copier not found at {VENV_COPIER} (run 'make setup')")


def run_copier_command(
    dest: Path, modules: str, *, trust: bool = False
) -> tuple[Path, subprocess.CompletedProcess[str]]:
    """Run copier copy and return the output directory plus process result."""
    output_dir = dest / "output"
    output_dir.mkdir(exist_ok=True)

    cmd = [
        str(VENV_COPIER),
        "copy",
        str(template_source()),
        str(output_dir),
        "--defaults",
        "--vcs-ref=HEAD",
        *(f"--data={k}={v}" for k, v in BASE_DATA.items()),
        f"--data=modules={modules}",
    ]
    if trust:
        cmd.insert(4, "--trust")

    result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(REPO_ROOT))  # noqa: S603
    return output_dir, result


def run_copier(dest: Path, modules: str, *, trust: bool = False) -> Path:
    """Run copier copy and return the output directory."""
    output_dir, result = run_copier_command(dest, modules, trust=trust)
    if result.returncode != 0:
        pytest.fail(f"Copier failed:\nstdout: {result.stdout}\nstderr: {result.stderr}")

    return output_dir


def check_no_jinja_artifacts(directory: Path) -> list[str]:
    """Check that no Jinja artifacts remain in generated files."""
    errors = []
    extensions = {".py", ".yml", ".yaml", ".md", ".toml", ".json", ".sh"}

    for file in directory.rglob("*"):
        if file.is_file() and file.suffix in extensions:
            try:
                content = file.read_text()
                if "{{" in content and "{%" in content:
                    if "{{ project_name }}" in content or "{{ _has_" in content:
                        errors.append(f"Jinja artifact in {file.relative_to(directory)}")
            except UnicodeDecodeError:
                pass

    return errors


@pytest.fixture(scope="session")
def project_backend(tmp_path_factory):
    """Generate a backend-only project (once per session)."""
    return run_copier(tmp_path_factory.mktemp("backend"), "backend")


@pytest.fixture(scope="session")
def project_standalone(tmp_path_factory):
    """Generate a standalone tg_bot project (once per session)."""
    return run_copier(tmp_path_factory.mktemp("standalone"), "tg_bot")


@pytest.fixture(scope="session")
def project_notifications(tmp_path_factory):
    """Generate a standalone notifications project (once per session)."""
    return run_copier(tmp_path_factory.mktemp("notifications"), "notifications")


@pytest.fixture(scope="session")
def project_frontend(tmp_path_factory):
    """Generate a standalone frontend project (once per session)."""
    return run_copier(tmp_path_factory.mktemp("frontend"), "frontend")


@pytest.fixture(scope="session")
def project_backend_tg_bot(tmp_path_factory):
    """Generate a backend+tg_bot project (once per session)."""
    return run_copier(tmp_path_factory.mktemp("backend_tg_bot"), "backend,tg_bot")


@pytest.fixture(scope="session")
def project_fullstack(tmp_path_factory):
    """Generate a fullstack project (once per session)."""
    return run_copier(tmp_path_factory.mktemp("fullstack"), "backend,tg_bot,notifications,frontend")
