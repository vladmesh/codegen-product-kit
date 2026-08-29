"""Tests for framework.spec.operations module."""

import pytest

from framework.generators.context import OperationContextBuilder
from framework.spec.operations import EventsConfig, OperationSpec, ParamSpec, RestConfig


class TestEventsConfig:
    """Tests for EventsConfig."""

    def test_subscribe_only(self) -> None:
        """EventsConfig with subscribe only is valid."""
        config = EventsConfig(subscribe="user.import.requested")
        assert config.subscribe == "user.import.requested"
        assert config.publish_on_success is None

    def test_publish_only(self) -> None:
        """EventsConfig with publish_on_success only is valid."""
        config = EventsConfig(publish_on_success="user.created")
        assert config.publish_on_success == "user.created"

    def test_both_subscribe_and_publish(self) -> None:
        """EventsConfig with both subscribe and publish is valid."""
        config = EventsConfig(
            subscribe="user.import.requested",
            publish_on_success="user.import.completed",
        )
        assert config.subscribe is not None
        assert config.publish_on_success is not None

    def test_neither_subscribe_nor_publish_fails(self) -> None:
        """EventsConfig with neither subscribe nor publish fails."""
        with pytest.raises(ValueError, match="subscribe.*or.*publish_on_success"):
            EventsConfig()

    def test_publish_on_error_optional(self) -> None:
        """publish_on_error is optional and works with subscribe."""
        config = EventsConfig(
            subscribe="user.import.requested",
            publish_on_error="user.import.failed",
        )
        assert config.publish_on_error == "user.import.failed"

    def test_message_model_is_rejected(self) -> None:
        """Removed message_model configuration is rejected explicitly."""
        with pytest.raises(ValueError, match="message_model"):
            EventsConfig(
                subscribe="user.import.requested",
                message_model="CustomBatchPayload",
            )

    def test_supported_fields_together(self) -> None:
        """All supported EventsConfig fields work together."""
        config = EventsConfig(
            subscribe="user.import.requested",
            publish_on_success="user.import.completed",
            publish_on_error="user.import.failed",
        )
        assert config.subscribe == "user.import.requested"
        assert config.publish_on_success == "user.import.completed"
        assert config.publish_on_error == "user.import.failed"


class TestOperationSpecValidation:
    """Tests for OperationSpec validation."""

    def test_subscribe_requires_input_model(self) -> None:
        """Operation with events.subscribe must have input model."""
        with pytest.raises(ValueError, match="must have 'input' model"):
            OperationSpec(
                name="process_import",
                events=EventsConfig(subscribe="user.import.requested"),
            )

    def test_subscribe_with_input_valid(self) -> None:
        """Operation with events.subscribe and input model is valid."""
        op = OperationSpec(
            name="process_import",
            input_model="UserImportBatch",
            events=EventsConfig(subscribe="user.import.requested"),
        )
        assert op.events is not None
        assert op.events.subscribe == "user.import.requested"
        assert op.input_model == "UserImportBatch"

    def test_publish_only_no_input_valid(self) -> None:
        """Operation with only publish_on_success doesn't require input."""
        op = OperationSpec(
            name="create_user",
            output_model="UserRead",
            rest=RestConfig(method="POST"),
            events=EventsConfig(publish_on_success="user.created"),
        )
        assert op.events is not None
        assert op.events.publish_on_success == "user.created"

    def test_publish_on_success_requires_output_model(self) -> None:
        """publish_on_success publishes the controller result, so output is required."""
        with pytest.raises(ValueError, match="publish_on_success.*'output' model"):
            OperationSpec(
                name="create_user",
                rest=RestConfig(method="POST"),
                events=EventsConfig(publish_on_success="user.created"),
            )

    def test_operation_must_have_transport(self) -> None:
        """Operation without transport fails."""
        with pytest.raises(ValueError, match="at least one transport"):
            OperationSpec(name="orphan_op", input_model="SomeModel")

    def test_rest_only_operation(self) -> None:
        """REST-only operation is valid."""
        op = OperationSpec(
            name="get_user",
            output_model="UserRead",
            rest=RestConfig(method="GET", path="/{user_id}"),
        )
        assert op.rest is not None
        assert op.events is None

    def test_events_only_operation(self) -> None:
        """Events-only operation is valid."""
        op = OperationSpec(
            name="process_import",
            input_model="ImportBatch",
            output_model="ImportResult",
            events=EventsConfig(
                subscribe="import.requested",
                publish_on_success="import.completed",
            ),
        )
        assert op.events is not None
        assert op.rest is None

    def test_dual_transport_operation(self) -> None:
        """Operation with both REST and Events is valid."""
        op = OperationSpec(
            name="create_user",
            input_model="UserCreate",
            output_model="UserRead",
            rest=RestConfig(method="POST", status=201),
            events=EventsConfig(publish_on_success="user.created"),
        )
        assert op.rest is not None
        assert op.events is not None


class TestOperationContextTransportFlags:
    """Tests for OperationContext transport type flags."""

    def test_rest_only_flags(self) -> None:
        """REST-only operation has correct flags."""
        op = OperationSpec(
            name="get_user",
            output_model="UserRead",
            rest=RestConfig(method="GET"),
        )
        builder = OperationContextBuilder()
        ctx = builder.build_for_protocol(op)

        assert ctx.has_rest is True
        assert ctx.has_events is False
        assert ctx.is_rest_only is True
        assert ctx.is_events_only is False
        assert ctx.is_dual_transport is False

    def test_events_only_flags(self) -> None:
        """Events-only operation has correct flags."""
        op = OperationSpec(
            name="process_import",
            input_model="ImportBatch",
            events=EventsConfig(subscribe="import.requested"),
        )
        builder = OperationContextBuilder()
        ctx = builder.build_for_protocol(op)

        assert ctx.has_rest is False
        assert ctx.has_events is True
        assert ctx.is_rest_only is False
        assert ctx.is_events_only is True
        assert ctx.is_dual_transport is False

    def test_dual_transport_flags(self) -> None:
        """Dual-transport operation has correct flags."""
        op = OperationSpec(
            name="create_user",
            input_model="UserCreate",
            output_model="UserRead",
            rest=RestConfig(method="POST"),
            events=EventsConfig(publish_on_success="user.created"),
        )
        builder = OperationContextBuilder()
        ctx = builder.build_for_protocol(op)

        assert ctx.has_rest is True
        assert ctx.has_events is True
        assert ctx.is_rest_only is False
        assert ctx.is_events_only is False
        assert ctx.is_dual_transport is True

    def test_publish_on_error_in_context(self) -> None:
        """publish_on_error is included in events context."""
        op = OperationSpec(
            name="process_import",
            input_model="ImportBatch",
            events=EventsConfig(
                subscribe="import.requested",
                publish_on_error="import.failed",
            ),
        )
        builder = OperationContextBuilder()
        ctx = builder.build_for_events(op)

        assert ctx.subscribe_channel == "import.requested"
        assert ctx.publish_on_error_channel == "import.failed"


class TestParamTypeConversion:
    """Tests for parameter type conversion in OperationContextBuilder."""

    def test_uuid_param_converted_to_python_type(self) -> None:
        """Param with type: uuid should produce UUID in context."""
        op = OperationSpec(
            name="get_todo",
            output_model="TodoRead",
            params=[ParamSpec(name="todo_id", type="uuid")],
            rest=RestConfig(method="GET", path="/{todo_id}"),
        )
        builder = OperationContextBuilder()
        ctx = builder.build_for_protocol(op)

        assert ctx.params[0].type == "UUID"
        assert "from uuid import UUID" in ctx.param_type_imports

    def test_datetime_param_converted_to_python_type(self) -> None:
        """Param with type: datetime should produce AwareDatetime in context."""
        op = OperationSpec(
            name="list_events",
            output_model="list[EventRead]",
            params=[ParamSpec(name="since", type="datetime", source="query")],
            rest=RestConfig(method="GET"),
        )
        builder = OperationContextBuilder()
        ctx = builder.build_for_protocol(op)

        assert ctx.params[0].type == "AwareDatetime"
        assert "from pydantic import AwareDatetime" in ctx.param_type_imports

    def test_int_param_no_extra_imports(self) -> None:
        """Param with type: int needs no extra imports."""
        op = OperationSpec(
            name="get_user",
            output_model="UserRead",
            params=[ParamSpec(name="user_id", type="int")],
            rest=RestConfig(method="GET", path="/{user_id}"),
        )
        builder = OperationContextBuilder()
        ctx = builder.build_for_protocol(op)

        assert ctx.params[0].type == "int"
        assert len(ctx.param_type_imports) == 0

    def test_string_param_converted(self) -> None:
        """Param with type: string should produce str."""
        op = OperationSpec(
            name="get_by_slug",
            output_model="ItemRead",
            params=[ParamSpec(name="slug", type="string")],
            rest=RestConfig(method="GET", path="/{slug}"),
        )
        builder = OperationContextBuilder()
        ctx = builder.build_for_protocol(op)

        assert ctx.params[0].type == "str"

    def test_default_param_type_is_str(self) -> None:
        """Param without explicit type defaults to str."""
        op = OperationSpec(
            name="get_item",
            output_model="ItemRead",
            params=[ParamSpec(name="item_id")],
            rest=RestConfig(method="GET", path="/{item_id}"),
        )
        builder = OperationContextBuilder()
        ctx = builder.build_for_protocol(op)

        assert ctx.params[0].type == "str"
