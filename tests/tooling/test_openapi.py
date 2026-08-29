"""Tests for OpenAPI generator."""

import json

from framework.openapi import generator


def test_generate_openapi(fake_repo) -> None:
    """Test OpenAPI generation from specs."""
    root = fake_repo

    # Create specs
    spec_dir = root / "shared" / "spec"
    spec_dir.mkdir(parents=True)

    (spec_dir / "models.yaml").write_text(
        """
models:
  User:
    fields:
      id: int
      name: string
""",
        encoding="utf-8",
    )

    # Create service spec (NEW FORMAT)
    service_spec_dir = root / "services" / "backend" / "spec"
    service_spec_dir.mkdir(parents=True)

    (service_spec_dir / "users.yaml").write_text(
        """
domain: users
config:
  rest:
    prefix: "/users"
    tags: ["users"]

operations:
  get:
    output: User
    params:
      - name: id
        type: int
    rest:
      method: GET
      path: "/{id}"
""",
        encoding="utf-8",
    )

    # Generate
    output_path = root / "services" / "backend" / "docs" / "openapi.json"
    generator.generate_openapi(root, output_path, service_name="backend")

    assert output_path.exists()

    content = json.loads(output_path.read_text())
    assert content["openapi"] == "3.1.0"
    assert "/users/{id}" in content["paths"]
    assert "User" in content["components"]["schemas"]

    # int path param -> integer schema
    param = content["paths"]["/users/{id}"]["get"]["parameters"][0]
    assert param["schema"] == {"type": "integer"}

    # No-default fields are all required
    assert content["components"]["schemas"]["User"]["required"] == ["id", "name"]

    # Single-model response is a bare $ref (not wrapped in an array)
    resp_schema = content["paths"]["/users/{id}"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    assert resp_schema == {"$ref": "#/components/schemas/User"}


def _write_models(root) -> None:
    spec_dir = root / "shared" / "spec"
    spec_dir.mkdir(parents=True)
    (spec_dir / "models.yaml").write_text(
        """
models:
  User:
    fields:
      id: int
      name: string
""",
        encoding="utf-8",
    )


def _generate(root):
    output_path = root / "services" / "backend" / "docs" / "openapi.json"
    generator.generate_openapi(root, output_path, service_name="backend")
    return json.loads(output_path.read_text())


def test_openapi_list_response_is_array(fake_repo) -> None:
    """A list[...] output must produce an array schema, not a single $ref."""
    root = fake_repo
    _write_models(root)

    service_spec_dir = root / "services" / "backend" / "spec"
    service_spec_dir.mkdir(parents=True)
    (service_spec_dir / "users.yaml").write_text(
        """
domain: users
config:
  rest:
    prefix: "/users"
    tags: ["users"]

operations:
  list:
    output: list[User]
    rest:
      method: GET
      path: ""
""",
        encoding="utf-8",
    )

    content = _generate(root)
    resp_schema = content["paths"]["/users"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    assert resp_schema == {
        "type": "array",
        "items": {"$ref": "#/components/schemas/User"},
    }


def test_openapi_uuid_path_param(fake_repo) -> None:
    """A uuid path param must map to a primitive schema, not a dangling $ref."""
    root = fake_repo
    _write_models(root)

    service_spec_dir = root / "services" / "backend" / "spec"
    service_spec_dir.mkdir(parents=True)
    (service_spec_dir / "users.yaml").write_text(
        """
domain: users
config:
  rest:
    prefix: "/users"
    tags: ["users"]

operations:
  get:
    output: User
    params:
      - name: id
        type: uuid
    rest:
      method: GET
      path: "/{id}"
""",
        encoding="utf-8",
    )

    content = _generate(root)
    param = content["paths"]["/users/{id}"]["get"]["parameters"][0]
    assert param["schema"] == {"type": "string", "format": "uuid"}


def test_openapi_required_excludes_optional_and_defaulted(fake_repo) -> None:
    """required must drop field-level optional, variant-level optional, and defaulted fields."""
    root = fake_repo

    spec_dir = root / "shared" / "spec"
    spec_dir.mkdir(parents=True)
    (spec_dir / "models.yaml").write_text(
        """
models:
  User:
    fields:
      id:
        type: int
        readonly: true
      name:
        type: string
      nickname:
        type: string
        optional: true
      is_admin:
        type: bool
        default: false
    variants:
      Update:
        optional: [name]
      Read: {}
""",
        encoding="utf-8",
    )

    service_spec_dir = root / "services" / "backend" / "spec"
    service_spec_dir.mkdir(parents=True)
    (service_spec_dir / "users.yaml").write_text(
        """
domain: users
config:
  rest:
    prefix: "/users"
    tags: ["users"]

operations:
  get:
    output: User
    params:
      - name: id
        type: int
    rest:
      method: GET
      path: "/{id}"
""",
        encoding="utf-8",
    )

    schemas = _generate(root)["components"]["schemas"]

    # Base: required keeps mandatory fields, drops field-level optional and defaulted ones
    base = schemas["User"]
    assert base["required"] == ["id", "name"]
    # nickname is nullable via the OpenAPI 3.1 anyOf form (no 3.0 `nullable` key)
    assert "nullable" not in base["properties"]["nickname"]
    assert {"type": "null"} in base["properties"]["nickname"]["anyOf"]

    # Update variant: name is variant-level optional -> not required
    assert "name" not in schemas["UserUpdate"]["required"]
