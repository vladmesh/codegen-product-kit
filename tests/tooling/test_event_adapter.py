"""Tests for EventAdapterGenerator."""

from pathlib import Path
import tempfile

import pytest

from framework.generators.event_adapter import EventAdapterGenerator
from framework.spec.loader import load_specs


@pytest.fixture
def temp_repo() -> Path:
    """Create a temporary repo structure for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        # Create directories
        (root / "shared" / "spec").mkdir(parents=True)
        (root / "services" / "worker" / "spec").mkdir(parents=True)
        (root / "services" / "worker" / "src" / "generated").mkdir(parents=True)

        yield root


class TestEventAdapterGenerator:
    """Tests for EventAdapterGenerator."""

    def test_generates_event_adapter_for_subscribe_operations(self, temp_repo: Path) -> None:
        """Generator creates event_adapter.py for services with subscriptions."""
        models_yaml = """
models:
  ImportBatch:
    fields:
      items:
        type: {type: list, of: {type: string}}
  ImportResult:
    fields:
      processed:
        type: int
"""
        (temp_repo / "shared" / "spec" / "models.yaml").write_text(models_yaml)

        domain_yaml = """
domain: imports
config:
  rest:
    prefix: "/imports"

operations:
  process_import:
    input: ImportBatch
    output: ImportResult
    events:
      subscribe: import.requested
      publish_on_success: import.completed
"""
        (temp_repo / "services" / "worker" / "spec" / "imports.yaml").write_text(domain_yaml)

        specs = load_specs(temp_repo)
        generator = EventAdapterGenerator(specs, temp_repo)
        generated = generator.generate()

        assert len(generated) == 1
        output_file = generated[0]
        assert output_file.exists()
        assert "event_adapter.py" in str(output_file)

        content = output_file.read_text()
        assert "ImportsControllerProtocol" in content
        assert "StreamSub(" in content
        assert '"import.requested",' in content
        assert 'CONSUMER_GROUP = "events:worker"' in content
        assert "group=CONSUMER_GROUP" in content
        assert 'consumer=consumer_name("worker.live")' in content
        assert 'consumer=consumer_name("worker.reclaim")' in content
        assert "min_idle_time=reclaim_idle_ms" in content
        assert "polling_interval=reclaim_polling_interval_ms" in content
        assert "EventEnvelope[ImportBatch]" in content
        assert "handle_process_import" in content
        assert "get_session" in content
        compile(content, str(output_file), "exec")

    def test_includes_publish_on_success_in_handler(self, temp_repo: Path) -> None:
        """Generated handler publishes result when publish_on_success is set."""
        models_yaml = """
models:
  UserEvent:
    fields:
      user_id:
        type: int
"""
        (temp_repo / "shared" / "spec" / "models.yaml").write_text(models_yaml)

        domain_yaml = """
domain: notifications
operations:
  send_notification:
    input: UserEvent
    output: UserEvent
    events:
      subscribe: user.created
      publish_on_success: notification.sent
"""
        (temp_repo / "services" / "worker" / "spec" / "notifications.yaml").write_text(domain_yaml)

        specs = load_specs(temp_repo)
        generator = EventAdapterGenerator(specs, temp_repo)
        generated = generator.generate()

        assert len(generated) == 1
        content = generated[0].read_text()

        # Should contain publish call
        assert "notification.sent" in content
        assert "publish_event" in content
        assert "if processed:" in content
        assert content.index("await session.commit()") < content.rindex(
            'await publish_event("notification.sent", result)'
        )

    def test_no_generation_for_publish_only_operations(self, temp_repo: Path) -> None:
        """Operations with only publish_on_success (no subscribe) don't create handlers."""
        models_yaml = """
models:
  User:
    fields:
      id:
        type: int
"""
        (temp_repo / "shared" / "spec" / "models.yaml").write_text(models_yaml)

        domain_yaml = """
domain: users
config:
  rest:
    prefix: "/users"
operations:
  create_user:
    input: User
    output: User
    rest:
      method: POST
    events:
      publish_on_success: user.created
"""
        (temp_repo / "services" / "worker" / "spec" / "users.yaml").write_text(domain_yaml)

        specs = load_specs(temp_repo)
        generator = EventAdapterGenerator(specs, temp_repo)
        generated = generator.generate()

        # No event adapter generated (no subscribe operations)
        assert len(generated) == 0

    def test_no_generation_for_rest_only_services(self, temp_repo: Path) -> None:
        """Services without event operations don't get event_adapter.py."""
        models_yaml = """
models:
  User:
    fields:
      id:
        type: int
"""
        (temp_repo / "shared" / "spec" / "models.yaml").write_text(models_yaml)

        domain_yaml = """
domain: users
config:
  rest:
    prefix: "/users"
operations:
  get_user:
    output: User
    rest:
      method: GET
      path: "/{id}"
"""
        (temp_repo / "services" / "worker" / "spec" / "users.yaml").write_text(domain_yaml)

        specs = load_specs(temp_repo)
        generator = EventAdapterGenerator(specs, temp_repo)
        generated = generator.generate()

        assert len(generated) == 0

    def test_includes_publish_on_error_with_try_except(self, temp_repo: Path) -> None:
        """Generated handler includes try/except and publishes to error channel."""
        models_yaml = """
models:
  ImportBatch:
    fields:
      items:
        type: {type: list, of: {type: string}}
  ImportResult:
    fields:
      count:
        type: int
"""
        (temp_repo / "shared" / "spec" / "models.yaml").write_text(models_yaml)

        domain_yaml = """
domain: imports
operations:
  process_import:
    input: ImportBatch
    output: ImportResult
    events:
      subscribe: import.requested
      publish_on_success: import.completed
      publish_on_error: import.failed
"""
        (temp_repo / "services" / "worker" / "spec" / "imports.yaml").write_text(domain_yaml)

        specs = load_specs(temp_repo)
        generator = EventAdapterGenerator(specs, temp_repo)
        generated = generator.generate()

        assert len(generated) == 1
        content = generated[0].read_text()

        # Should contain error handling
        assert "try:" in content
        assert "except Exception as e:" in content
        assert "import.failed" in content
        assert '"error": str(e)' in content
        assert "raise" in content  # Re-raises after publishing error

    def test_requires_idempotency_guard_for_every_handler(self, temp_repo: Path) -> None:
        """A subscribe handler cannot be created without the transactional guard."""
        models_yaml = """
models:
  Ping:
    fields:
      id:
        type: int
"""
        (temp_repo / "shared" / "spec" / "models.yaml").write_text(models_yaml)

        domain_yaml = """
domain: pings
operations:
  on_ping:
    input: Ping
    events:
      subscribe: ping
"""
        (temp_repo / "services" / "worker" / "spec" / "pings.yaml").write_text(domain_yaml)

        specs = load_specs(temp_repo)
        generator = EventAdapterGenerator(specs, temp_repo)
        generated = generator.generate()

        assert len(generated) == 1
        content = generated[0].read_text()

        # Both dependencies are required, so generated adapters have no unguarded path.
        assert (
            "get_session: Callable[[], AbstractAsyncContextManager[AsyncSession]]"
            in content
        )
        assert "consume_once: IdempotentConsumer," in content
        assert "if get_session is None:" not in content
        assert "if consume_once is None:" not in content
        assert "on_ping(None, payload=event.payload)" not in content

    def test_includes_session_commit_and_rollback(self, temp_repo: Path) -> None:
        """Generated handler includes explicit session.commit() and session.rollback()."""
        models_yaml = """
models:
  TaskPayload:
    fields:
      task_id:
        type: int
"""
        (temp_repo / "shared" / "spec" / "models.yaml").write_text(models_yaml)

        domain_yaml = """
domain: tasks
operations:
  process_task:
    input: TaskPayload
    events:
      subscribe: task.requested
"""
        (temp_repo / "services" / "worker" / "spec" / "tasks.yaml").write_text(domain_yaml)

        specs = load_specs(temp_repo)
        generator = EventAdapterGenerator(specs, temp_repo)
        generated = generator.generate()

        assert len(generated) == 1
        content = generated[0].read_text()

        # Should contain session management
        assert "await session.commit()" in content
        assert "await session.rollback()" in content
        assert "processed = await consume_once(" in content
        assert "session, CONSUMER_GROUP, event.event_id, effect" in content
        # Should use AbstractAsyncContextManager type
        assert "AbstractAsyncContextManager[AsyncSession]" in content
