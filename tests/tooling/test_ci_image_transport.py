"""CI jobs that pull public images configure the Docker Hub mirror before any container."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
MIRROR_STEP = "Pull Docker Hub images through the public mirror"

#: Every job that pulls, builds or runs a container, and the steps that do it.
CONTAINER_JOBS = {
    ("test-template.yml", "test-generation"): ["Run generated durable-event integration stack"],
    ("test-template.yml", "test-pytest"): ["Start the binding Redis", "Run copier tests (slow)"],
    ("runner-proof.yml", "fresh-product-runner"): ["Run the fresh product runner proof"],
}


def _job(workflow: str, job: str) -> dict:
    document = yaml.safe_load((REPO_ROOT / ".github/workflows" / workflow).read_text())
    return document["jobs"][job]


@pytest.mark.parametrize(("workflow", "job"), list(CONTAINER_JOBS))
def test_mirror_is_configured_before_the_job_uses_containers(workflow: str, job: str) -> None:
    definition = _job(workflow, job)
    # Service and job containers are pulled before the first step, past the mirror.
    assert "services" not in definition and "container" not in definition
    names = [step.get("name") for step in definition["steps"]]
    mirror = definition["steps"][names.index(MIRROR_STEP)]
    assert mirror["env"] == {"REGISTRY_MIRROR": "https://mirror.gcr.io"}
    # Keeps the other daemon.json keys, proves the effective mirror and the docker driver.
    assert 'config.get("registry-mirrors", [])' in mirror["run"]
    assert "sudo systemctl restart docker" in mirror["run"]
    assert "grep -F mirror.gcr.io" in mirror["run"]
    assert "'^Driver: +docker$'" in mirror["run"]
    for name in CONTAINER_JOBS[(workflow, job)]:
        assert names.index(MIRROR_STEP) < names.index(name), name


def test_binding_redis_is_owned_and_removed() -> None:
    steps = _job("test-template.yml", "test-pytest")["steps"]
    names = [step.get("name") for step in steps]
    start = steps[names.index("Start the binding Redis")]["run"]
    assert "--publish 6379:6379" in start and "redis:7-alpine" in start
    assert 'test "$status" = healthy' in start
    slow = next(step for step in steps if step.get("run") == "make test-copier-slow")
    assert slow["env"]["CODEGEN_BINDINGS_REDIS_URL"] == "redis://127.0.0.1:6379/14"
    cleanup = steps[-1]
    assert cleanup["name"] == "Remove the binding Redis" and cleanup["if"] == "always()"
    assert cleanup["run"] == "docker rm --force --volumes codegen-binding-redis || true"
