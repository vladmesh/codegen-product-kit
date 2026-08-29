"""Tests for framework.spec.types module."""

from pydantic import ValidationError
import pytest

from framework.spec.types import (
    DictType,
    EnumType,
    ListType,
    OptionalType,
    PrimitiveType,
    parse_type_spec,
    type_spec_to_json_schema,
    type_spec_to_python,
    type_spec_to_typescript,
)


class TestPrimitiveType:
    """Tests for primitive types."""

    @pytest.mark.parametrize(
        "type_name",
        ["int", "string", "bool", "float", "datetime", "uuid"],
    )
    def test_valid_primitives(self, type_name: str) -> None:
        """All primitive types should be valid."""
        spec = PrimitiveType(type=type_name)
        assert spec.type == type_name

    def test_invalid_primitive(self) -> None:
        """Invalid primitive type should raise."""
        with pytest.raises(ValidationError):
            PrimitiveType(type="invalid")

    def test_typo_primitive(self) -> None:
        """Common typos should fail validation."""
        with pytest.raises(ValidationError):
            PrimitiveType(type="strng")


class TestListType:
    """Tests for list types."""

    def test_list_of_primitive(self) -> None:
        """List of primitive should work."""
        spec = ListType(type="list", of=PrimitiveType(type="string"))
        assert spec.type == "list"
        assert isinstance(spec.of, PrimitiveType)
        assert spec.of.type == "string"

    def test_nested_list(self) -> None:
        """Nested lists should work."""
        spec = ListType(
            type="list",
            of=ListType(type="list", of=PrimitiveType(type="int")),
        )
        assert isinstance(spec.of, ListType)
        assert isinstance(spec.of.of, PrimitiveType)


class TestDictType:
    """Tests for dict types."""

    def test_dict_string_to_string(self) -> None:
        """Dict with string keys and values."""
        spec = DictType(
            type="dict",
            key=PrimitiveType(type="string"),
            value=PrimitiveType(type="string"),
        )
        assert spec.type == "dict"

    def test_dict_key_must_be_primitive(self) -> None:
        """Dict key must be a primitive type."""
        with pytest.raises(ValidationError, match="Dict key must be a primitive"):
            DictType(
                type="dict",
                key=ListType(type="list", of=PrimitiveType(type="string")),
                value=PrimitiveType(type="string"),
            )


class TestEnumType:
    """Tests for enum types."""

    def test_valid_enum(self) -> None:
        """Valid enum with values."""
        spec = EnumType(type="enum", values=["pending", "active", "disabled"])
        assert spec.values == ["pending", "active", "disabled"]
        assert spec.default is None

    def test_enum_with_default(self) -> None:
        """Enum with default value."""
        spec = EnumType(type="enum", values=["low", "medium", "high"], default="medium")
        assert spec.default == "medium"

    def test_enum_needs_at_least_two_values(self) -> None:
        """Enum must have at least 2 values."""
        with pytest.raises(ValidationError, match="at least 2"):
            EnumType(type="enum", values=["only_one"])

    def test_enum_values_must_be_unique(self) -> None:
        """Enum values must be unique."""
        with pytest.raises(ValidationError, match="unique"):
            EnumType(type="enum", values=["a", "b", "a"])

    def test_enum_default_must_be_in_values(self) -> None:
        """Enum default must be one of the values."""
        with pytest.raises(ValidationError, match="not in enum values"):
            EnumType(type="enum", values=["a", "b"], default="c")

    def test_enum_values_must_be_identifiers(self) -> None:
        """Enum values must be valid Python identifiers."""
        with pytest.raises(ValidationError, match="valid identifier"):
            EnumType(type="enum", values=["valid", "has space"])


class TestOptionalType:
    """Tests for optional types."""

    def test_optional_primitive(self) -> None:
        """Optional wrapping a primitive."""
        spec = OptionalType(type="optional", of=PrimitiveType(type="string"))
        assert spec.type == "optional"
        assert isinstance(spec.of, PrimitiveType)


class TestParseTypeSpec:
    """Tests for parse_type_spec function."""

    def test_parse_shorthand_string(self) -> None:
        """Shorthand string format should work."""
        spec = parse_type_spec("int")
        assert isinstance(spec, PrimitiveType)
        assert spec.type == "int"

    def test_parse_dict_format(self) -> None:
        """Full dict format should work."""
        spec = parse_type_spec({"type": "list", "of": {"type": "string"}})
        assert isinstance(spec, ListType)

    def test_parse_enum_dict(self) -> None:
        """Enum dict format should work."""
        spec = parse_type_spec({"type": "enum", "values": ["a", "b"]})
        assert isinstance(spec, EnumType)

    def test_parse_invalid_shorthand(self) -> None:
        """Invalid shorthand should raise."""
        with pytest.raises(ValueError, match="Unknown type shorthand"):
            parse_type_spec("invalid_type")


class TestTypeSpecToPython:
    """Tests for type_spec_to_python function."""

    def test_primitive_to_python(self) -> None:
        """Primitives map to Python types."""
        assert type_spec_to_python(PrimitiveType(type="int")) == "int"
        assert type_spec_to_python(PrimitiveType(type="string")) == "str"
        assert type_spec_to_python(PrimitiveType(type="datetime")) == "AwareDatetime"
        assert type_spec_to_python(PrimitiveType(type="uuid")) == "UUID"

    def test_list_to_python(self) -> None:
        """List maps to list[T]."""
        spec = ListType(type="list", of=PrimitiveType(type="int"))
        assert type_spec_to_python(spec) == "list[int]"

    def test_optional_to_python(self) -> None:
        """Optional maps to T | None."""
        spec = OptionalType(type="optional", of=PrimitiveType(type="string"))
        assert type_spec_to_python(spec) == "str | None"

    def test_dict_to_python(self) -> None:
        """Dict maps to dict[K, V]."""
        spec = DictType(
            type="dict",
            key=PrimitiveType(type="string"),
            value=PrimitiveType(type="int"),
        )
        assert type_spec_to_python(spec) == "dict[str, int]"


class TestTypeSpecToJsonSchema:
    """Tests for type_spec_to_json_schema function."""

    def test_int_to_schema(self) -> None:
        """Int maps to integer."""
        schema = type_spec_to_json_schema(PrimitiveType(type="int"))
        assert schema == {"type": "integer"}

    def test_datetime_to_schema(self) -> None:
        """Datetime maps to string with format."""
        schema = type_spec_to_json_schema(PrimitiveType(type="datetime"))
        assert schema == {"type": "string", "format": "date-time"}

    def test_list_to_schema(self) -> None:
        """List maps to array."""
        spec = ListType(type="list", of=PrimitiveType(type="string"))
        schema = type_spec_to_json_schema(spec)
        assert schema == {"type": "array", "items": {"type": "string"}}

    def test_enum_to_schema(self) -> None:
        """Enum maps to string with enum."""
        spec = EnumType(type="enum", values=["a", "b"], default="a")
        schema = type_spec_to_json_schema(spec)
        assert schema == {"type": "string", "enum": ["a", "b"], "default": "a"}

    def test_optional_to_schema(self) -> None:
        """Optional maps to anyOf with a null branch (OpenAPI 3.1 form)."""
        spec = OptionalType(type="optional", of=PrimitiveType(type="string"))
        schema = type_spec_to_json_schema(spec)
        assert schema == {"anyOf": [{"type": "string"}, {"type": "null"}]}

    def test_primitive_result_is_independent(self) -> None:
        """Mutating one primitive schema must not affect later calls."""
        first = type_spec_to_json_schema(PrimitiveType(type="int"))
        first["minimum"] = 0
        second = type_spec_to_json_schema(PrimitiveType(type="int"))
        assert second == {"type": "integer"}


class TestTypeSpecToTypeScript:
    """Tests for type_spec_to_typescript function."""

    def test_primitive_to_typescript(self) -> None:
        """Primitives map to TypeScript types."""
        assert type_spec_to_typescript(PrimitiveType(type="int")) == "number"
        assert type_spec_to_typescript(PrimitiveType(type="float")) == "number"
        assert type_spec_to_typescript(PrimitiveType(type="string")) == "string"
        assert type_spec_to_typescript(PrimitiveType(type="bool")) == "boolean"
        assert type_spec_to_typescript(PrimitiveType(type="datetime")) == "string"
        assert type_spec_to_typescript(PrimitiveType(type="uuid")) == "string"

    def test_list_to_typescript(self) -> None:
        """List maps to T[]."""
        spec = ListType(type="list", of=PrimitiveType(type="int"))
        assert type_spec_to_typescript(spec) == "number[]"

    def test_dict_to_typescript(self) -> None:
        """Dict maps to Record<K, V>."""
        spec = DictType(
            type="dict",
            key=PrimitiveType(type="string"),
            value=PrimitiveType(type="int"),
        )
        assert type_spec_to_typescript(spec) == "Record<string, number>"

    def test_optional_to_typescript(self) -> None:
        """Optional maps to T | null."""
        spec = OptionalType(type="optional", of=PrimitiveType(type="string"))
        assert type_spec_to_typescript(spec) == "string | null"

    def test_enum_to_typescript(self) -> None:
        """Enum maps to an inline union of string literals."""
        spec = EnumType(type="enum", values=["a", "b"])
        assert type_spec_to_typescript(spec) == '"a" | "b"'

    def test_nested_list_optional(self) -> None:
        """Nested variants fold through the single traversal."""
        spec = ListType(
            type="list",
            of=OptionalType(type="optional", of=PrimitiveType(type="int")),
        )
        assert type_spec_to_typescript(spec) == "number | null[]"


class TestFoldTypeSpec:
    """Tests for the shared fold that all converters share."""

    def test_unknown_spec_raises(self) -> None:
        """An object outside the TypeSpec union raises instead of silently passing."""
        # Every converter funnels through fold_type_spec, so the public entrypoint
        with pytest.raises(ValueError, match="Unknown type spec"):
            type_spec_to_typescript(object())
