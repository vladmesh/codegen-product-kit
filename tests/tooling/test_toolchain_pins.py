"""Every setup-uv step must pin the versions declared in framework/toolchain.py."""

from __future__ import annotations

from pathlib import Path
import re

import pytest

from framework.toolchain import SETUP_UV_ACTION, UV_LINUX_X86_64_CHECKSUM, UV_VERSION

REPO_ROOT = Path(__file__).resolve().parents[2]

WORKFLOWS = [
    REPO_ROOT / ".github" / "workflows" / "ci.yml",
    REPO_ROOT / ".github" / "workflows" / "test-template.yml",
    REPO_ROOT / "template" / ".github" / "workflows" / "ci.yml.jinja",
]

# The template workflow is Jinja, not YAML, so match on text rather than parse.
SETUP_UV_USES = re.compile(r"uses:\s*astral-sh/setup-uv@\S+")
PINNED_SETUP_UV_STEP = re.compile(
    r"uses:\s*(?P<action>astral-sh/setup-uv@\S+)\n\s*with:\n\s*version:\s*\"(?P<version>[^\"]+)\""
    r"\n\s*checksum:\s*\"(?P<checksum>[^\"]+)\""
)


def _workflow_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


@pytest.mark.parametrize("workflow", WORKFLOWS, ids=lambda p: p.name)
def test_setup_uv_steps_are_pinned(workflow: Path) -> None:
    text = _workflow_text(workflow)
    steps = PINNED_SETUP_UV_STEP.findall(text)

    assert len(SETUP_UV_USES.findall(text)) == len(steps), (
        f"{workflow.relative_to(REPO_ROOT)} has a setup-uv step without a pinned version"
    )
    assert steps, f"{workflow.relative_to(REPO_ROOT)} no longer sets up uv"

    for action, version, checksum in steps:
        assert action == SETUP_UV_ACTION, (
            f"{workflow.relative_to(REPO_ROOT)} uses {action}, expected {SETUP_UV_ACTION}"
        )
        assert version == UV_VERSION, (
            f"{workflow.relative_to(REPO_ROOT)} pins uv {version}, expected {UV_VERSION}"
        )
        assert checksum == UV_LINUX_X86_64_CHECKSUM, (
            f"{workflow.relative_to(REPO_ROOT)} checks {checksum}, "
            f"expected {UV_LINUX_X86_64_CHECKSUM}"
        )


def test_framework_and_template_workflows_agree() -> None:
    """ci.yml and test-template.yml must not drift apart from each other."""
    pins = {
        workflow.name: set(PINNED_SETUP_UV_STEP.findall(_workflow_text(workflow)))
        for workflow in WORKFLOWS
    }

    assert len(set().union(*pins.values())) == 1, f"setup-uv pins diverge across workflows: {pins}"
