# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Input and output validation boundaries for EV-05."""

from __future__ import annotations

from collections.abc import Mapping
from unittest.mock import MagicMock

import pytest

import openjiuwen.core.events.bus as event_bus_module
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import ValidationError, raise_error
from openjiuwen.core.events.bus import ExtensionEventBus
from openjiuwen.core.events.event_next import EventNext
from openjiuwen.core.events.types import OPS_BUILD_EVENT_TYPE, Event, HandlerLevel, PackageMetadata


def _matches(event_input: object, pattern: Mapping[str, object]) -> bool:
    return isinstance(event_input, Mapping) and all(event_input.get(key) == value for key, value in pattern.items())


async def _built_bus() -> ExtensionEventBus:
    bus = ExtensionEventBus()
    await bus.trigger(Event(OPS_BUILD_EVENT_TYPE, None))
    return bus


def _skip_log_count(mock_logger: MagicMock) -> int:
    assert mock_logger.exception.call_count == 0
    return mock_logger.error.call_count


class _HandlerFailure(Exception):
    """Representative extension handler failure."""


class _DownstreamFailure(Exception):
    """Representative downstream failure."""


@pytest.mark.asyncio
async def test_initial_input_validation_stops_before_matching() -> None:
    bus = await _built_bus()
    trace: list[str] = []

    def input_validator(event_input: object) -> bool:
        del event_input
        trace.append("input")
        return False

    def matcher(event_input: object, pattern: Mapping[str, object]) -> bool:
        del event_input, pattern
        trace.append("matcher")
        return True

    async def core(event: Event, ops: object | None, event_next: EventNext) -> object:
        del event, ops, event_next
        trace.append("core")
        return "core"

    bus.register_event_type("demo.invalid_input", matcher, core, input_validator=input_validator)

    with pytest.raises(ValidationError) as invalid:
        await bus.trigger(Event("demo.invalid_input", {}))

    assert invalid.value.status is StatusCode.EVENTS_PAYLOAD_INVALID
    assert trace == ["input"]


@pytest.mark.asyncio
@pytest.mark.parametrize("handler_level", [HandlerLevel.USER, HandlerLevel.BUILTIN])
@pytest.mark.parametrize(
    "failure_mode",
    ["exception", "validation-error", "non-awaitable", "invalid-output", "invalid-forward", "caught-invalid-forward"],
)
async def test_extension_fault_before_downstream_skips_handler(
    monkeypatch: pytest.MonkeyPatch,
    handler_level: HandlerLevel,
    failure_mode: str,
) -> None:
    bus = await _built_bus()
    mock_logger = MagicMock()
    monkeypatch.setattr(event_bus_module, "logger", mock_logger)
    core_inputs: list[object] = []

    def input_validator(event_input: object) -> bool:
        return event_input != "invalid-forward"

    def output_validator(event_output: object | None) -> bool:
        return event_output != "invalid-output"

    async def core(event: Event, ops: object | None, event_next: EventNext) -> object:
        del ops, event_next
        core_inputs.append(event.event_input)
        return "core"

    if failure_mode == "non-awaitable":

        def handler(event: Event, ops: object | None, event_next: EventNext) -> object:
            del event, ops, event_next
            return "not-awaitable"

    else:

        async def handler(event: Event, ops: object | None, event_next: EventNext) -> object:
            del ops
            if failure_mode == "exception":
                raise _HandlerFailure("handler failed")
            if failure_mode == "validation-error":
                raise_error(StatusCode.EVENTS_PAYLOAD_INVALID, reason="handler rejected its input")
            if failure_mode == "invalid-forward":
                return await event_next(Event(event.event_type, "invalid-forward"))
            if failure_mode == "caught-invalid-forward":
                try:
                    await event_next(Event(event.event_type, "invalid-forward"))
                except ValidationError:
                    return "handler"
            return "invalid-output"

    bus.register_event_type(
        "demo.pre_fault",
        _matches,
        core,
        input_validator=input_validator,
        output_validator=output_validator,
    )
    handler_id = bus.register_event_handler(
        "demo.pre_fault",
        handler,
        {},
        handler_level,
        package_metadata=PackageMetadata("demo.package"),
    )
    original_input = {"request": "sensitive-business-value"}

    assert await bus.trigger(Event("demo.pre_fault", original_input)) == "core"
    assert core_inputs == [original_input]
    assert _skip_log_count(mock_logger) == 1
    log_call = mock_logger.error.call_args
    assert "demo.pre_fault" in log_call.args
    assert handler_id in log_call.args
    assert handler_level.value in log_call.args
    assert "demo.package" in log_call.args
    assert "sensitive-business-value" not in " ".join(str(argument) for argument in log_call.args)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_mode", ["exception", "invalid-output"])
async def test_post_downstream_fault_reuses_last_success_without_replay(
    monkeypatch: pytest.MonkeyPatch,
    failure_mode: str,
) -> None:
    bus = await _built_bus()
    mock_logger = MagicMock()
    monkeypatch.setattr(event_bus_module, "logger", mock_logger)
    core_inputs: list[object] = []

    async def core(event: Event, ops: object | None, event_next: EventNext) -> object | None:
        del ops, event_next
        core_inputs.append(event.event_input)
        return None if event.event_input == "second" else event.event_input

    async def user(event: Event, ops: object | None, event_next: EventNext) -> object:
        del ops
        await event_next(Event(event.event_type, "first"))
        await event_next(Event(event.event_type, "second"))
        if failure_mode == "exception":
            raise _HandlerFailure("post-processing failed")
        return "invalid-output"

    bus.register_event_type(
        "demo.post_fault",
        _matches,
        core,
        output_validator=lambda output: output != "invalid-output",
    )
    bus.register_event_handler("demo.post_fault", user, {}, HandlerLevel.USER)

    assert await bus.trigger(Event("demo.post_fault", {})) is None
    assert core_inputs == ["first", "second"]
    assert _skip_log_count(mock_logger) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("aftermath", ["propagate", "valid-output", "exception", "invalid-output"])
async def test_last_downstream_failure_is_not_replayed_or_replaced(
    monkeypatch: pytest.MonkeyPatch,
    aftermath: str,
) -> None:
    bus = await _built_bus()
    mock_logger = MagicMock()
    monkeypatch.setattr(event_bus_module, "logger", mock_logger)
    downstream_error = _DownstreamFailure("downstream failed")
    core_inputs: list[object] = []

    async def core(event: Event, ops: object | None, event_next: EventNext) -> object:
        del ops, event_next
        core_inputs.append(event.event_input)
        if event.event_input == "second":
            raise downstream_error
        return "first-result"

    async def user(event: Event, ops: object | None, event_next: EventNext) -> object:
        del ops
        await event_next(Event(event.event_type, "first"))
        try:
            await event_next(Event(event.event_type, "second"))
        except _DownstreamFailure:
            if aftermath == "propagate":
                raise
        if aftermath == "valid-output":
            return "recovered"
        if aftermath == "exception":
            raise _HandlerFailure("handler failed after downstream")
        return "invalid-output"

    bus.register_event_type(
        "demo.downstream_failure",
        _matches,
        core,
        output_validator=lambda output: output != "invalid-output",
    )
    bus.register_event_handler("demo.downstream_failure", user, {}, HandlerLevel.USER)

    if aftermath == "valid-output":
        assert await bus.trigger(Event("demo.downstream_failure", {})) == "recovered"
        assert _skip_log_count(mock_logger) == 0
    else:
        with pytest.raises(_DownstreamFailure) as propagated:
            await bus.trigger(Event("demo.downstream_failure", {}))
        assert propagated.value is downstream_error
        assert _skip_log_count(mock_logger) == (0 if aftermath == "propagate" else 1)
    assert core_inputs == ["first", "second"]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_mode", ["exception", "non-bool"])
async def test_handler_output_validator_failure_is_not_skipped(
    monkeypatch: pytest.MonkeyPatch,
    failure_mode: str,
) -> None:
    bus = await _built_bus()
    mock_logger = MagicMock()
    monkeypatch.setattr(event_bus_module, "logger", mock_logger)

    def output_validator(event_output: object | None) -> bool:
        if event_output == "core":
            return True
        if failure_mode == "exception":
            raise LookupError("validator failed")
        return 1

    async def core(event: Event, ops: object | None, event_next: EventNext) -> object:
        del event, ops, event_next
        return "core"

    async def user(event: Event, ops: object | None, event_next: EventNext) -> object:
        del ops
        await event_next(event)
        return "handler"

    bus.register_event_type("demo.validator_failure", _matches, core, output_validator=output_validator)
    bus.register_event_handler("demo.validator_failure", user, {}, HandlerLevel.USER)

    if failure_mode == "exception":
        with pytest.raises(LookupError, match="validator failed"):
            await bus.trigger(Event("demo.validator_failure", {}))
    else:
        with pytest.raises(ValidationError) as invalid:
            await bus.trigger(Event("demo.validator_failure", {}))
        assert invalid.value.status is StatusCode.EVENTS_REGISTRATION_INVALID
    assert _skip_log_count(mock_logger) == 0


@pytest.mark.asyncio
async def test_validator_definition_failures_propagate() -> None:
    raising_bus = await _built_bus()

    def raising_validator(event_input: object) -> bool:
        del event_input
        raise LookupError("validator failed")

    async def core(event: Event, ops: object | None, event_next: EventNext) -> object:
        del event, ops, event_next
        return "core"

    raising_bus.register_event_type("demo.raising_validator", _matches, core, input_validator=raising_validator)
    with pytest.raises(LookupError, match="validator failed"):
        await raising_bus.trigger(Event("demo.raising_validator", {}))

    non_bool_bus = await _built_bus()

    def non_bool_validator(event_input: object) -> object:
        del event_input
        return 1

    non_bool_bus.register_event_type("demo.non_bool_validator", _matches, core, input_validator=non_bool_validator)
    with pytest.raises(ValidationError) as invalid:
        await non_bool_bus.trigger(Event("demo.non_bool_validator", {}))
    assert invalid.value.status is StatusCode.EVENTS_REGISTRATION_INVALID

    caught_bus = await _built_bus()

    def forwarding_validator(event_input: object) -> bool:
        if event_input == "forwarded":
            raise LookupError("forward validator failed")
        return True

    async def catching_handler(event: Event, ops: object | None, event_next: EventNext) -> object:
        del ops
        try:
            await event_next(Event(event.event_type, "forwarded"))
        except LookupError:
            return "handler"
        return "unexpected"

    caught_bus.register_event_type("demo.caught_validator", _matches, core, input_validator=forwarding_validator)
    caught_bus.register_event_handler("demo.caught_validator", catching_handler, {}, HandlerLevel.USER)
    with pytest.raises(LookupError, match="forward validator failed"):
        await caught_bus.trigger(Event("demo.caught_validator", {}))


@pytest.mark.asyncio
async def test_core_output_validation_failure_stops_trigger() -> None:
    bus = await _built_bus()

    async def core(event: Event, ops: object | None, event_next: EventNext) -> object:
        del event, ops, event_next
        return "invalid"

    bus.register_event_type(
        "demo.invalid_core_output",
        _matches,
        core,
        output_validator=lambda output: output == "valid",
    )

    with pytest.raises(ValidationError) as invalid:
        await bus.trigger(Event("demo.invalid_core_output", {}))

    assert invalid.value.status is StatusCode.EVENTS_PAYLOAD_INVALID
