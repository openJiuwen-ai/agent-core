# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""The ``tool.register`` event and its executable-tool CORE handler."""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypeAlias

from jsonschema import SchemaError
from jsonschema.validators import validator_for

from openjiuwen.core.events.event_next import EventNext
from openjiuwen.core.events.ops import EventHandlerOps
from openjiuwen.core.events.types import Event, MatchPattern
from openjiuwen.core.foundation.tool import (
    LocalFunction,
    RestfulApi,
    RestfulApiCard,
    Tool,
    ToolCard,
    tool as build_local_function,
)

if TYPE_CHECKING:
    from openjiuwen.core.events.bus import ExtensionEventBus

TOOL_REGISTER = "tool.register"
ToolRegisterSource: TypeAlias = Tool | RestfulApiCard | Callable[..., Any]
_TOOL_SOURCE_ERROR = "source must be a Tool, RestfulApiCard, or callable"


def _snapshot_json_value(value: object) -> Any:
    """Copy JSON data while rejecting executable or other runtime objects."""
    if callable(value):
        raise TypeError("input_schema must not contain callable values")
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if math.isfinite(value):
            return value
        raise TypeError("input_schema must contain only JSON-compatible values")
    if isinstance(value, Mapping):
        snapshot: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("input_schema object keys must be strings")
            snapshot[key] = _snapshot_json_value(item)
        return snapshot
    if isinstance(value, (list, tuple)):
        return [_snapshot_json_value(item) for item in value]
    raise TypeError("input_schema must contain only JSON-compatible values")


@dataclass(frozen=True, slots=True)
class ToolRegisterSpec:
    """Source description accepted by ``ops.tool.register``.

    ``source`` is deliberately a single extension point: existing concrete
    ``Tool`` instances (including ``LocalFunction`` and ``RestfulApi``) pass
    through unchanged, a ``RestfulApiCard`` remains a card until CORE creates
    its ``RestfulApi``, and a callable remains available for CORE to wrap as a
    ``LocalFunction``.  ``card`` is optional metadata for the callable case;
    future concrete ``Tool`` subclasses do not require another field in this
    event contract.
    """

    source: ToolRegisterSource
    card: ToolCard | None = None


@dataclass(frozen=True, slots=True)
class ToolRegisterInput:
    """Validated event input containing a source for one executable ``Tool``."""

    source: ToolRegisterSource
    card: ToolCard | None = None


@dataclass(frozen=True, slots=True)
class ToolRegisterValue:
    """Successful tool registration result."""

    tool: str

    def __post_init__(self) -> None:
        if not isinstance(self.tool, str) or not self.tool:
            raise ValueError("tool must be a nonempty string")


@dataclass(frozen=True, slots=True)
class ToolRegisterOutput:
    """CORE result; exactly one of ``value`` and ``deny`` must be present."""

    value: ToolRegisterValue | None = None
    deny: str | None = None

    def __post_init__(self) -> None:
        if (self.value is None) == (self.deny is None):
            raise ValueError("tool.register output requires exactly one of value or deny")
        if self.value is not None and not isinstance(self.value, ToolRegisterValue):
            raise TypeError("value must be ToolRegisterValue")
        if self.deny is not None and not isinstance(self.deny, str):
            raise TypeError("deny must be a string")

    def validate(self) -> None:
        """Validate the output after a handler has possibly rewritten it."""
        self.__post_init__()


def _validate_tool_card(card: ToolCard, *, mark_exposure_declared: bool = False) -> None:
    """Validate the model-facing metadata carried by one concrete tool."""
    if not isinstance(card, ToolCard):
        raise TypeError("card must be ToolCard")
    if not isinstance(card.name, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,64}", card.name) is None:
        raise ValueError("invalid tool name")
    if not isinstance(card.description, str) or not card.description.strip():
        raise ValueError("description must be a nonempty string")

    input_params = card.input_params
    if isinstance(input_params, Mapping):
        schema = _snapshot_json_value(dict(input_params))
        if schema and schema.get("type") != "object":
            raise ValueError("input_schema must describe an object")
        try:
            validator_for(schema or {"type": "object"}).check_schema(schema or {"type": "object"})
        except SchemaError as error:
            raise ValueError("input_schema is not a valid JSON Schema") from error
    if mark_exposure_declared:
        card.set_exposure_declared(True)


def _validate_tool_register_source(source: object, card: ToolCard | None) -> None:
    """Validate the source/card relationship without constructing a runtime Tool."""
    if isinstance(source, RestfulApiCard):
        if card is not None and card is not source:
            raise ValueError("card must be the same object as source for a RestfulApiCard")
        _validate_tool_card(source)
        return

    if isinstance(source, Tool):
        if card is not None and card is not source.card:
            raise ValueError("card must be the same object as source.card for an existing Tool")
        _validate_tool_card(source.card)
        return

    if callable(source):
        if isinstance(card, RestfulApiCard):
            raise TypeError("a callable cannot use RestfulApiCard metadata")
        if card is not None:
            _validate_tool_card(card)
        return

    raise TypeError(_TOOL_SOURCE_ERROR)


def prepare_tool_register_input(data: ToolRegisterInput) -> ToolRegisterInput:
    """Validate one event input without materializing runtime tool objects."""
    if not isinstance(data, ToolRegisterInput):
        raise TypeError("tool.register requires ToolRegisterInput")
    _validate_tool_register_source(data.source, data.card)
    return data


def build_tool_register_input(spec: ToolRegisterSpec) -> ToolRegisterInput:
    """Build the event input while leaving runtime Tool construction to CORE."""
    if not isinstance(spec, ToolRegisterSpec):
        raise TypeError("tool.register requires ToolRegisterSpec")
    return prepare_tool_register_input(ToolRegisterInput(source=spec.source, card=spec.card))


def _build_local_function(source: Callable[..., Any], card: ToolCard | None) -> LocalFunction:
    """Create a LocalFunction for a callable source inside CORE."""
    if card is None:
        return build_local_function(source)
    return LocalFunction(card=card, func=source)


def _materialize_tool(data: ToolRegisterInput) -> Tool:
    """Materialize a source after CORE has resolved the bound DeepAgent."""
    source = data.source
    if isinstance(source, RestfulApiCard):
        # A REST card is only a declaration; its executable instance belongs to CORE.
        tool = RestfulApi(source)
    elif isinstance(source, Tool):
        tool = source
    elif callable(source):
        tool = _build_local_function(source, data.card)
    else:
        raise TypeError(_TOOL_SOURCE_ERROR)

    _validate_tool_card(tool.card, mark_exposure_declared=True)
    return tool


def _require_session_context(ops: EventHandlerOps | None):
    if ops is None or ops.session_context is None or not ops.session_context.session_id:
        raise RuntimeError("tool.register requires a session")
    return ops.session_context


def match_pattern_func(value: object, pattern: MatchPattern) -> bool:
    """Match valid tool registration requests with an empty pattern."""
    return isinstance(value, ToolRegisterInput) and not pattern


def _is_valid_input(value: object) -> bool:
    """Return whether the event input is a complete executable-tool request."""
    if not isinstance(value, ToolRegisterInput):
        return False
    try:
        prepare_tool_register_input(value)
    except (TypeError, ValueError):
        return False
    return True


def _is_valid_output(value: object) -> bool:
    """Return whether a value satisfies the complete tool output contract."""
    if not isinstance(value, ToolRegisterOutput):
        return False
    try:
        value.validate()
    except (TypeError, ValueError):
        return False
    return True


def register(bus: ExtensionEventBus) -> None:
    """Register ``tool.register`` and its executable-tool CORE handler on ``bus``."""

    async def core_handler(
        event: Event,
        ops: EventHandlerOps | None,
        event_next: EventNext,
    ) -> ToolRegisterOutput:
        """Bind and expose a real executable Tool on the live DeepAgent."""
        del event_next
        session = _require_session_context(ops)
        data = prepare_tool_register_input(event.event_input)

        from openjiuwen.core.single_agent.agent_runtime import get_agent_runtime
        from openjiuwen.harness.deep_agent import DeepAgent

        agent = get_agent_runtime().get(session.session_id)
        if agent is None:
            raise RuntimeError("session agent is not bound or has been released")
        if not isinstance(agent, DeepAgent):
            raise RuntimeError("tool.register requires a bound DeepAgent")

        tool = _materialize_tool(data)
        await agent.load_plugin_ability(tools=tool)
        return ToolRegisterOutput(value=ToolRegisterValue(tool=tool.card.name))

    bus.register_event_type(
        TOOL_REGISTER,
        match_pattern_func,
        core_handler,
        input_validator=_is_valid_input,
        output_validator=_is_valid_output,
    )
