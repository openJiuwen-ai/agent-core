# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import inspect
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import FrozenInstanceError

import pytest

import openjiuwen.core.events.protocol as events_protocol
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import ValidationError, build_error
from openjiuwen.core.events import EventHandlerOps, EventHandlerOpsBuilder, ExtensionEventBus
from openjiuwen.core.events.event_next import EventNext
from openjiuwen.core.events.ops import EventHandlerOps as ConcreteEventHandlerOps
from openjiuwen.core.events.ops import EventHandlerOpsBuilder as ConcreteEventHandlerOpsBuilder
from openjiuwen.core.events.protocol import EventHandler, ExtensionEventBusProtocol
from openjiuwen.core.events.types import (
    OPS_BUILD_EVENT_TYPE,
    Event,
    EventInput,
    EventInputValidator,
    EventOutput,
    EventOutputValidator,
    EventType,
    HandlerId,
    HandlerLevel,
    MatchPattern,
    MatchPatternFunc,
    OpsMethod,
    PackageMetadata,
    SessionContext,
)

_EVENTS_STATUS_CODES = {
    "EVENTS_REGISTRATION_INVALID": 200001,
    "EVENTS_UNKNOWN_EVENT_INVALID": 200002,
    "EVENTS_STATE_INVALID": 200003,
    "EVENTS_PAYLOAD_INVALID": 200004,
    "EVENTS_MATCH_INVALID": 200005,
    "EVENTS_NEXT_INVALID": 200006,
    "EVENTS_OPS_INVALID": 200007,
}


def test_events_status_codes_names_range_and_uniqueness():
    owners = {}
    for member in StatusCode:
        owners.setdefault(member.code, []).append(member.name)

    for name, code in _EVENTS_STATUS_CODES.items():
        status = StatusCode[name]
        assert status.name == name
        assert status.code == code
        assert owners[code] == [name]


def test_events_status_codes_resolve_to_non_recoverable_validation_error():
    for name in _EVENTS_STATUS_CODES:
        error = build_error(StatusCode[name], reason="contract rejected")
        assert isinstance(error, ValidationError)
        assert error.recoverable is False


def test_events_models_aliases_and_handler_level():
    assert EventType is str
    assert EventInput is object
    assert EventOutput is object
    assert HandlerId is str
    assert OPS_BUILD_EVENT_TYPE == "ops.build"
    assert {level.value for level in HandlerLevel} == {"user", "builtin", "core"}
    assert all(isinstance(level, str) for level in HandlerLevel)
    assert MatchPattern == Mapping[str, object]
    assert MatchPatternFunc == Callable[[EventInput, MatchPattern], bool]
    assert EventInputValidator == Callable[[EventInput], bool]
    assert EventOutputValidator == Callable[[EventOutput | None], bool]
    assert OpsMethod == Callable[..., Awaitable[object]]


def test_events_models_frozen_fields_reject_assignment():
    event = Event(event_type="ops.build", event_input=None)
    context = SessionContext(session_id="session")
    package = PackageMetadata(package_id="package")

    with pytest.raises(FrozenInstanceError):
        event.event_input = "changed"
    with pytest.raises(FrozenInstanceError):
        context.session_id = "other"
    with pytest.raises(FrozenInstanceError):
        package.package_id = "other"


def test_events_models_default_metadata_is_not_shared():
    first = SessionContext(session_id="first")
    second = SessionContext(session_id="second")

    assert first.cwd is None
    assert first.metadata is not second.metadata
    assert dict(first.metadata) == {}
    assert PackageMetadata(package_id="package").package_version is None
    with pytest.raises(TypeError):
        first.metadata["key"] = "value"


def _method_signature(method):
    signature = inspect.signature(method)
    parameters = [parameter for parameter in signature.parameters.values() if parameter.name != "self"]
    return parameters, signature.return_annotation


def _contract_functions(cls):
    return {
        name
        for name, value in vars(cls).items()
        if inspect.isfunction(value) and (not name.startswith("_") or name == "__call__")
    }


def test_events_protocol_bus_signature():
    assert not hasattr(events_protocol, "Session")
    for name in ("state", "initialize", "close"):
        assert not hasattr(ExtensionEventBusProtocol, name)

    for bus_type in (ExtensionEventBusProtocol, ExtensionEventBus):
        register_type, register_type_return = _method_signature(bus_type.register_event_type)
        assert [parameter.name for parameter in register_type] == [
            "event_type",
            "match_pattern_func",
            "core_handler",
            "input_validator",
            "output_validator",
        ]
        assert [parameter.annotation for parameter in register_type] == [
            "EventType",
            "MatchPatternFunc",
            "EventHandler",
            "EventInputValidator | None",
            "EventOutputValidator | None",
        ]
        assert all(parameter.kind is inspect.Parameter.KEYWORD_ONLY for parameter in register_type[3:])
        assert all(parameter.default is None for parameter in register_type[3:])
        assert register_type_return == "None"

    unregister_type, unregister_type_return = _method_signature(ExtensionEventBusProtocol.unregister_event_type)
    assert [parameter.name for parameter in unregister_type] == ["event_type"]
    assert unregister_type[0].annotation == "EventType"
    assert unregister_type_return == "None"

    register_handler, register_handler_return = _method_signature(ExtensionEventBusProtocol.register_event_handler)
    assert [parameter.name for parameter in register_handler] == [
        "event_type",
        "handler",
        "match_pattern",
        "handler_level",
        "package_metadata",
    ]
    assert [parameter.annotation for parameter in register_handler] == [
        "EventType",
        "EventHandler",
        "MatchPattern",
        "HandlerLevel",
        "PackageMetadata | None",
    ]
    assert register_handler[3].default is HandlerLevel.USER
    assert register_handler[4].kind is inspect.Parameter.KEYWORD_ONLY
    assert register_handler[4].default is None
    assert register_handler_return == "HandlerId"

    unregister_handler, unregister_handler_return = _method_signature(
        ExtensionEventBusProtocol.unregister_event_handler
    )
    assert [parameter.name for parameter in unregister_handler] == ["handler_id"]
    assert unregister_handler[0].annotation == "HandlerId"
    assert unregister_handler_return == "None"

    trigger, trigger_return = _method_signature(ExtensionEventBusProtocol.trigger)
    assert [parameter.name for parameter in trigger] == ["event", "session"]
    assert trigger[0].annotation == "Event"
    assert trigger[1].annotation == "Session | SessionContext | None"
    assert trigger[1].default is None
    assert trigger_return == "EventOutput | None"
    assert list(inspect.signature(ExtensionEventBus.trigger).parameters) == ["self", "event", "session"]
    assert (
        EventHandler
        == Callable[
            [Event, EventHandlerOps | None, EventNext],
            Awaitable[EventOutput | None],
        ]
    )


def test_events_builder_ops_and_event_next_signatures():
    assert not getattr(EventHandlerOpsBuilder, "_is_protocol", False)
    assert not getattr(EventHandlerOps, "_is_protocol", False)
    assert _contract_functions(EventHandlerOpsBuilder) == {"add_namespace", "add_method", "build"}

    add_namespace, add_namespace_return = _method_signature(EventHandlerOpsBuilder.add_namespace)
    assert [parameter.name for parameter in add_namespace] == ["namespace"]
    assert add_namespace[0].annotation == "str"
    assert add_namespace_return == "None"

    add_method, add_method_return = _method_signature(EventHandlerOpsBuilder.add_method)
    assert [parameter.name for parameter in add_method] == ["namespace", "method_name", "method"]
    assert [parameter.annotation for parameter in add_method] == ["str", "str", "OpsMethod"]
    assert add_method_return == "None"

    build, build_return = _method_signature(EventHandlerOpsBuilder.build)
    assert [parameter.name for parameter in build] == ["package_metadata", "session_context"]
    assert [parameter.annotation for parameter in build] == ["PackageMetadata | None", "SessionContext | None"]
    assert all(parameter.default is None for parameter in build)
    assert build_return == "EventHandlerOps"

    assert not getattr(EventNext, "_is_protocol", False)
    assert _contract_functions(EventNext) == {"__call__", "to"}
    call, call_return = _method_signature(EventNext.__call__)
    assert [parameter.name for parameter in call] == ["event"]
    assert call[0].annotation == "Event"
    assert call_return == "EventOutput | None"
    to, to_return = _method_signature(EventNext.to)
    assert [parameter.name for parameter in to] == ["event", "handler_level"]
    assert [parameter.annotation for parameter in to] == ["Event", "HandlerLevel"]
    assert to_return == "EventOutput | None"

    ops_properties = {name for name, value in vars(EventHandlerOps).items() if isinstance(value, property)}
    assert ops_properties == {"package_metadata", "session_context"}
    assert "__call__" not in vars(EventHandlerOps)
    assert inspect.signature(EventHandlerOps.package_metadata.fget).return_annotation == "PackageMetadata | None"
    assert inspect.signature(EventHandlerOps.session_context.fget).return_annotation == "SessionContext | None"


_EVENTS_EXPORTS = [
    "EventType",
    "EventInput",
    "EventOutput",
    "HandlerId",
    "OPS_BUILD_EVENT_TYPE",
    "HandlerLevel",
    "Event",
    "SessionContext",
    "PackageMetadata",
    "MatchPattern",
    "MatchPatternFunc",
    "EventInputValidator",
    "EventOutputValidator",
    "EventHandler",
    "EventNext",
    "OpsMethod",
    "EventHandlerOpsBuilder",
    "EventHandlerOps",
    "ExtensionEventBusProtocol",
    "ExtensionEventBus",
]


def test_events_exports_match_contract():
    import openjiuwen.core.events as events
    from openjiuwen.core.events.bus import ExtensionEventBus

    assert list(events.__all__) == _EVENTS_EXPORTS
    assert events.EventHandlerOpsBuilder is ConcreteEventHandlerOpsBuilder
    assert events.EventHandlerOps is ConcreteEventHandlerOps
    assert events.ExtensionEventBus is ExtensionEventBus
    for name in _EVENTS_EXPORTS:
        assert hasattr(events, name)
    for name in ("EventDefinition", "EventsError"):
        assert not hasattr(events, name)
