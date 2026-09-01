"""Tests for framework.spec.loader module."""

from pathlib import Path
import tempfile

import pytest

from framework.spec.loader import (
    SpecValidationError,
    load_specs,
    validate_specs_cli,
)


@pytest.fixture
def temp_repo() -> Path:
    """Create a temporary repo structure for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        # Create directories
        (root / "shared" / "spec").mkdir(parents=True)
        (root / "services" / "backend" / "spec").mkdir(parents=True)

        yield root


class TestLoadSpecs:
    """Tests for load_specs function."""

    def test_load_valid_specs(self, temp_repo: Path) -> None:
        """Load valid specs successfully."""
        # Write models.yaml
        models_yaml = """
models:
  User:
    fields:
      id:
        type: int
        readonly: true
      name:
        type: string
    variants:
      Create: {}
      Read: {}
"""
        (temp_repo / "shared" / "spec" / "models.yaml").write_text(models_yaml)

        # Write domain spec (new format)
        domain_yaml = """
domain: users
config:
  rest:
    prefix: "/users"
    tags: ["users"]

operations:
  create_user:
    input: UserCreate
    output: UserRead
    rest:
      method: POST
      path: ""
      status: 201
"""
        (temp_repo / "services" / "backend" / "spec" / "users.yaml").write_text(domain_yaml)

        specs = load_specs(temp_repo)

        assert "User" in specs.models.models
        assert "backend/users" in specs.domains

    def test_missing_models_yaml_returns_empty(self, temp_repo: Path) -> None:
        """Missing models.yaml should return empty specs (graceful)."""
        specs = load_specs(temp_repo)

        assert specs.models.models == {}
        assert specs.events.events == []
        assert specs.domains == {}

    def test_legacy_manifest_is_ignored(self, temp_repo: Path) -> None:
        """Existing manifest.yaml files do not affect domain spec loading."""
        (temp_repo / "shared" / "spec" / "models.yaml").write_text(
            "models:\n  Example:\n    fields:\n      id:\n        type: int\n"
        )
        (temp_repo / "services" / "backend" / "spec" / "manifest.yaml").write_text(
            "this is not valid: [yaml"
        )

        specs = load_specs(temp_repo)

        assert specs.domains == {}

    def test_explicit_service_manifest_is_loaded_outside_domain_specs(
        self, temp_repo: Path
    ) -> None:
        (temp_repo / "shared" / "spec" / "models.yaml").write_text(
            "models:\n  Example:\n    fields:\n      id: int\n"
        )
        (temp_repo / "services" / "backend" / "manifest.yaml").write_text(
            "version: 1\n"
            "settings_schema:\n"
            "  $schema: https://json-schema.org/draft/2020-12/schema\n"
            "  type: object\n"
            "  properties:\n"
            "    languages:\n"
            "      type: array\n"
            "      items: {type: string}\n"
            "  additionalProperties: false\n"
        )

        specs = load_specs(temp_repo)

        assert specs.domains == {}
        assert specs.manifests["backend"].settings_schema["properties"]["languages"] == {
            "type": "array",
            "items": {"type": "string"},
        }

    @pytest.mark.parametrize(
        ("manifest", "message"),
        [
            ("version: 2\nsettings_schema: {}\n", "version"),
            (
                "version: 1\nsettings_schema:\n"
                "  $schema: https://json-schema.org/draft/2020-12/schema\n"
                "  type: object\n  properties: {}\n  additionalProperties: true\n",
                "additionalProperties",
            ),
            (
                "version: 1\nsettings_schema:\n"
                "  $schema: https://json-schema.org/draft/2020-12/schema\n"
                "  type: object\n  properties: {}\n  additionalProperties: false\n"
                "  required: [missing]\n",
                "required",
            ),
            (
                "version: 1\nsettings_schema:\n"
                "  $schema: https://json-schema.org/draft/2020-12/schema\n"
                "  type: object\n  properties: {enabled: true}\n"
                "  additionalProperties: false\n",
                "object schemas",
            ),
        ],
    )
    def test_invalid_service_manifest_fails_closed(
        self, temp_repo: Path, manifest: str, message: str
    ) -> None:
        (temp_repo / "services" / "backend" / "manifest.yaml").write_text(manifest)

        with pytest.raises(SpecValidationError, match=message):
            load_specs(temp_repo)

    def test_duplicate_manifest_setting_is_rejected(self, temp_repo: Path) -> None:
        (temp_repo / "services" / "other" / "spec").mkdir(parents=True)
        (temp_repo / "services" / "backend" / "manifest.yaml").write_text(
            "version: 1\nsettings_schema:\n"
            "  $schema: https://json-schema.org/draft/2020-12/schema\n"
            "  type: object\n  properties: {city: {type: string}}\n"
            "  additionalProperties: false\n"
        )
        (temp_repo / "services" / "other" / "manifest.yaml").write_text(
            "version: 1\nsettings_schema:\n"
            "  $schema: https://json-schema.org/draft/2020-12/schema\n"
            "  type: object\n  properties: {city: {type: string}}\n"
            "  additionalProperties: false\n"
        )

        with pytest.raises(SpecValidationError, match="declared by both"):
            load_specs(temp_repo)

    def test_invalid_yaml_syntax(self, temp_repo: Path) -> None:
        """Invalid YAML syntax should fail."""
        (temp_repo / "shared" / "spec" / "models.yaml").write_text("invalid: yaml: syntax:")

        with pytest.raises(SpecValidationError, match="Invalid YAML"):
            load_specs(temp_repo)

    def test_unknown_model_reference(self, temp_repo: Path) -> None:
        """Reference to unknown model should fail."""
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
operations:
  get_user:
    output: UnknownModel
    rest:
      method: GET
      path: ""
"""
        (temp_repo / "services" / "backend" / "spec" / "users.yaml").write_text(domain_yaml)

        with pytest.raises(SpecValidationError, match="Unknown output model 'UnknownModel'"):
            load_specs(temp_repo)

    def test_events_optional(self, temp_repo: Path) -> None:
        """Events.yaml is optional."""
        models_yaml = """
models:
  User:
    fields:
      id:
        type: int
"""
        (temp_repo / "shared" / "spec" / "models.yaml").write_text(models_yaml)

        specs = load_specs(temp_repo)

        assert len(specs.events.events) == 0

    def test_load_events(self, temp_repo: Path) -> None:
        """Load events successfully."""
        models_yaml = """
models:
  UserEvent:
    fields:
      user_id:
        type: int
"""
        (temp_repo / "shared" / "spec" / "models.yaml").write_text(models_yaml)

        events_yaml = """
events:
  user_created:
    message: UserEvent
    publish: true
"""
        (temp_repo / "shared" / "spec" / "events.yaml").write_text(events_yaml)

        specs = load_specs(temp_repo)

        assert len(specs.events.events) == 1
        assert specs.events.events[0].name == "user_created"

    def test_global_event_subscribe_is_rejected(self, temp_repo: Path) -> None:
        """Removed global event subscribe metadata is rejected explicitly."""
        (temp_repo / "shared" / "spec" / "models.yaml").write_text(
            "models:\n  UserEvent:\n    fields:\n      user_id:\n        type: int\n"
        )
        (temp_repo / "shared" / "spec" / "events.yaml").write_text(
            "events:\n"
            "  user_created:\n"
            "    message: UserEvent\n"
            "    publish: true\n"
            "    subscribe: true\n"
        )

        with pytest.raises(SpecValidationError, match="subscribe"):
            load_specs(temp_repo)


class TestValidateSpecsCli:
    """Tests for CLI-friendly validation."""

    def test_valid_specs_pass(self, temp_repo: Path) -> None:
        """Valid specs return success."""
        models_yaml = """
models:
  User:
    fields:
      id:
        type: int
"""
        (temp_repo / "shared" / "spec" / "models.yaml").write_text(models_yaml)

        success, message = validate_specs_cli(temp_repo)

        assert success is True
        assert "PASSED" in message
        assert "Models: 1" in message

    def test_no_specs_skips(self, temp_repo: Path) -> None:
        """Missing specs return success with skip message."""
        success, message = validate_specs_cli(temp_repo)

        assert success is True
        assert "No specs found" in message
