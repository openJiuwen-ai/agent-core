# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Opt-in event boundary around one ability-manager tool batch."""

from contextvars import ContextVar
from functools import wraps
from inspect import signature

from openjiuwen.core.foundation.llm import ToolCall
from openjiuwen.core.single_agent.ability_manager import AbilityManager

_react_tool_call_owner: ContextVar[AbilityManager | None] = ContextVar("react_tool_call_owner", default=None)


def react_tool_call_scope(method):
    """Mark a ReAct batch; explicit Ops calls own their separate event boundary."""

    @wraps(method)
    async def scoped(agent, *args, **kwargs):
        token = _react_tool_call_owner.set(agent.ability_manager)
        try:
            return await method(agent, *args, **kwargs)
        finally:
            _react_tool_call_owner.reset(token)

    return scoped


def with_tool_call_event(execute):
    """Wrap a batch while preserving its complete execution options.

    The original executor remains the CORE callback, so retained tool facts,
    rail hooks and result processing run once through the same implementation.
    Managers outside the ReAct loop keep their original direct-call behavior.
    """
    parameters = signature(execute)

    @wraps(execute)
    async def dispatch(manager, *args, **kwargs):
        if not getattr(manager, "tool_call_events_enabled", False):
            return await execute(manager, *args, **kwargs)
        from openjiuwen.core.runner import Runner

        # Runner exposes its public entry points through a lazy facade.
        bus = getattr(Runner, "get_extension_bus")()
        if bus is None:
            return await execute(manager, *args, **kwargs)
        from openjiuwen.core.events import Event
        from openjiuwen.core.events.builtins.event_types.tool_call import (
            TOOL_CALL,
            ToolCallEventInput,
            ToolCallTriggerContext,
            apply_tool_call_outputs,
        )

        bound = parameters.bind(manager, *args, **kwargs)
        tool_call = bound.arguments["tool_call"]
        if isinstance(tool_call, list):
            calls = tool_call
        elif isinstance(tool_call, ToolCall):
            calls = [tool_call]
        else:
            # The shared executor owns validation and logging of invalid input.
            return await execute(manager, *args, **kwargs)

        async def invoke_original(final_tool_calls):
            bound.arguments["tool_call"] = final_tool_calls
            return await execute(*bound.args, **bound.kwargs)

        trigger_context = ToolCallTriggerContext(tool_calls=tuple(calls), invoke_original=invoke_original)
        event_input = tuple(
            ToolCallEventInput(tool_call_id=call.id, tool_name=call.name, arguments=call.arguments) for call in calls
        )
        outputs = await bus.trigger(
            Event(TOOL_CALL, event_input),
            bound.arguments["session"],
            trigger_context=trigger_context,
        )
        return apply_tool_call_outputs(trigger_context, outputs)

    @wraps(execute)
    async def wrapped(manager, *args, **kwargs):
        if _react_tool_call_owner.get() is not manager:
            return await execute(manager, *args, **kwargs)
        # CORE invokes the original executor. Explicit Ops calls made from
        # handlers already trigger tool.call themselves and must not re-enter.
        token = _react_tool_call_owner.set(None)
        try:
            return await dispatch(manager, *args, **kwargs)
        finally:
            _react_tool_call_owner.reset(token)

    return wrapped


class EventfulAbilityManager(AbilityManager):
    """ReAct executor whose CORE is the shared ability-manager implementation."""

    tool_call_events_enabled = True

    @with_tool_call_event
    @wraps(AbilityManager.execute)
    async def execute(self, ctx, tool_call, session, parallel_tool_calls=True, tag=None, **execution_options):
        """Run the shared batch once, forwarding all execution and fact options."""
        options = dict(execution_options, parallel_tool_calls=parallel_tool_calls)
        if tag is not None:
            options["tag"] = tag
        return await super().execute(ctx=ctx, tool_call=tool_call, session=session, **options)
