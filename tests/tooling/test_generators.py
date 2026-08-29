"""Tests for modular code generators."""

import importlib
from pathlib import Path
import shutil

from framework import generate
from framework.lib.fs import GENERATED_FILE_MODE


def test_generate_all_creates_files(fake_repo) -> None:
    """Test that generate_all creates all expected files."""
    root = fake_repo

    # Copy templates to fake repo
    real_templates = Path("framework/templates").absolute()
    fake_templates = root / "framework" / "templates"
    fake_templates.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(real_templates, fake_templates, dirs_exist_ok=True)

    # Reload generate module to pick up new root
    importlib.reload(generate)

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
    variants:
      Create: {}
  UserCreated:
    fields:
      user_id: int
""",
        encoding="utf-8",
    )

    (spec_dir / "events.yaml").write_text(
        """
events:
  UserCreated:
    message: UserCreated
    publish: true
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
  create:
    input: UserCreate
    output: User
    rest:
      method: POST
      path: ""
      status: 201
""",
        encoding="utf-8",
    )

    # Run generation
    generate.generate_all(root)

    # Check shared generated files
    shared_gen = root / "shared" / "shared" / "generated"
    assert (shared_gen / "schemas.py").exists()
    assert (shared_gen / "events.py").exists()

    # Check content
    schemas = (shared_gen / "schemas.py").read_text()
    assert "class User(BaseModel):" in schemas
    assert "class UserCreate(BaseModel):" in schemas

    assert "class UserCreated(BaseModel):" in schemas

    events = (shared_gen / "events.py").read_text()
    assert "broker = RedisBroker" in events
    assert 'async def publish_UserCreated(message: UserCreated)' in events
    assert '_pub_UserCreated = get_broker().publisher("UserCreated")' in events

    # Check service generated files (protocols)
    backend_gen = root / "services" / "backend" / "src" / "generated"
    assert (backend_gen / "protocols.py").exists()

    generated_files = [*shared_gen.glob("*.py"), *backend_gen.rglob("*.py")]
    assert generated_files
    assert all(path.stat().st_mode & 0o777 == GENERATED_FILE_MODE for path in generated_files)

    protocols = (backend_gen / "protocols.py").read_text()
    assert "class UsersControllerProtocol(Protocol):" in protocols
    assert "async def create" in protocols

    # Create backend service dir for controllers
    backend_src = root / "services" / "backend" / "src"
    (backend_src / "controllers").mkdir(parents=True, exist_ok=True)

    # Run again to generate controllers
    generate.generate_all(root)

    assert (backend_src / "controllers" / "users.py").exists()
    controller = (backend_src / "controllers" / "users.py").read_text()
    assert "class UsersController" in controller
    assert "async def create" in controller

    # CRITICAL: Verify methods are INSIDE the class (properly indented)
    # Bug was: methods generated with wrong indentation, ending up outside class
    lines = controller.split("\n")
    in_class = False
    method_inside_class = False
    for line in lines:
        if line.startswith("class UsersController"):
            in_class = True
        elif in_class and line.startswith("    async def create"):
            # Method properly indented with 4 spaces = inside class
            method_inside_class = True
            break
        elif in_class and not line.startswith(" ") and line.strip() and not line.startswith("#"):
            # Non-indented non-empty line after class = left class body
            in_class = False

    assert method_inside_class, (
        "Controller method 'create' is not properly indented inside class body. "
        "This indicates a code generation bug in controller.py.j2 template."
    )
