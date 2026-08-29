"""Tests for Frontend generator."""

from framework.frontend import generator


def test_generate_typescript(fake_repo) -> None:
    """Test TypeScript generation from specs."""
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
      role:
        type:
          type: enum
          values: [admin, user]
""",
        encoding="utf-8",
    )

    # Generate
    output_path = root / "frontend" / "types.ts"
    generator.generate_typescript(root, output_path)

    assert output_path.exists()

    content = output_path.read_text()
    assert "export interface User {" in content
    assert "id: number;" in content
    assert "name: string;" in content
    # Enum field is a named string-literal type alias, referenced by the interface
    assert 'export type UserRole = "admin" | "user";' in content
    assert "role: UserRole;" in content
    assert "export enum" not in content
