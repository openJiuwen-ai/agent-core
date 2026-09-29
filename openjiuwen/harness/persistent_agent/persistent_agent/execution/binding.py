# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Bound native calls, not a second model loop or authoritative execution API."""

import asyncio
from contextlib import aclosing, asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass
from typing import TYPE_CHECKING

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.runner import Runner

if TYPE_CHECKING:
    from openjiuwen.harness.deep_agent import DeepAgent

    from ..schema.build_context import PersistentBuildContext


def _declarations(agent):
    """Immutable selected declarations, excluding mutable model/rail internals."""
    config = agent.deep_config
    return (
        agent.card.id,
        config.tool_owner_id,
        config.system_prompt,
        config.cwd,
        config.project_root,
        config.enable_task_loop,
        id(config.stream_task_lifecycle),
        config.max_iterations,
        config.auto_create_workspace,
        config.completion_timeout,
        tuple(config.skills or ()) if not isinstance(config.skills, str) else config.skills,
        tuple((card.id, card.name, card.stateless) for card in config.tools or ()),
    )


@dataclass
class InvocationState:
    """Live state owned by exactly one construction binding, never serialized."""

    config: object
    model: object
    abilities: object
    declarations: tuple
    owned_tools: tuple
    loop: asyncio.AbstractEventLoop | None = None
    active_task: asyncio.Task | None = None
    stopped: bool = False
    stopping: bool = False
    closing: bool = False
    closed: bool = False

    @classmethod
    def capture(cls, agent: "DeepAgent", owner_id: str):
        """Capture runtime identities and tools owned by this binding."""
        owned = []
        for card in agent.deep_config.tools or ():
            if not card.stateless and card.id == f"{card.name}_{owner_id}":
                owned.append((card.name, card.id, Runner.resource_mgr.get_tool(card.id)))
        return cls(
            config=agent.deep_config,
            model=agent.deep_config.model,
            abilities=agent.ability_manager,
            declarations=_declarations(agent),
            owned_tools=tuple(owned),
        )

    def validate_agent(self, agent):
        """Reject mutation or replacement of the bound runtime configuration."""
        if (
            agent.deep_config is not self.config
            or agent.deep_config.model is not self.model
            or agent.ability_manager is not self.abilities
            or _declarations(agent) != self.declarations
        ):
            raise build_error(StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR, error_msg="Persistent runtime config drift")


def _validate(agent: "DeepAgent", context: "PersistentBuildContext") -> InvocationState:
    """Validate that the supplied context still owns this native agent."""
    from ..schema.build_context import PersistentBuildContext

    if not isinstance(context, PersistentBuildContext):
        raise build_error(StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR, error_msg="Persistent context required")
    context.validate_binding(agent)
    state = context.binding.runtime
    if not isinstance(state, InvocationState):
        raise build_error(StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR, error_msg="Persistent runtime binding absent")
    state.validate_agent(agent)
    loop = asyncio.get_running_loop()
    if state.loop is not None and state.loop is not loop:
        raise build_error(StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR, error_msg="Persistent event loop mismatch")
    return state


@asynccontextmanager
async def invocation_scope(agent: "DeepAgent", *, context: "PersistentBuildContext"):
    """Reserve a native call before it can initialize or write Session state."""
    state = _validate(agent, context)
    task = asyncio.current_task()
    bound = context.binding
    with bound.lock:
        _validate(agent, context)
        if state.closed or state.closing or state.stopped or state.active_task is not None:
            raise build_error(StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR, error_msg="Persistent call unavailable")
        state.loop = asyncio.get_running_loop()
        state.active_task = task
    try:
        yield bound.session
    finally:
        with bound.lock:
            if state.active_task is task:
                state.active_task = None


async def invoke_bound(agent: "DeepAgent", inputs, *, context: "PersistentBuildContext"):
    """Internal native result; no events, authorization, budget or commit claim."""
    async with invocation_scope(agent, context=context) as session:
        result = await agent.invoke(deepcopy(inputs), session=session)
        state = _validate(agent, context)
        if state.stopped:
            raise asyncio.CancelledError
        return result


async def stream_bound(agent: "DeepAgent", inputs, *, context: "PersistentBuildContext", stream_modes=None):
    """Retain occupancy until owned native stream producers have exited."""
    async with invocation_scope(agent, context=context) as session:
        async with aclosing(agent.stream(deepcopy(inputs), session=session, stream_modes=stream_modes)) as chunks:
            async for chunk in chunks:
                state = _validate(agent, context)
                if state.stopped:
                    raise asyncio.CancelledError
                yield chunk


async def abort_bound(agent: "DeepAgent", *, context: "PersistentBuildContext") -> None:
    """Request this binding's stop; not a receipt of tool/process convergence."""
    state = _validate(agent, context)
    bound = context.binding
    with bound.lock:
        if state.closed or state.closing or state.stopping:
            raise build_error(StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR, error_msg="Persistent call closed")
        task = state.active_task
        if task is asyncio.current_task():
            raise build_error(
                StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR, error_msg="stop requires a separate control task"
            )
        state.stopped = True
        state.stopping = True
        state.loop = asyncio.get_running_loop()
        if task is not None:
            task.cancel()
    try:
        await agent.abort(session=bound.session)
    finally:
        with bound.lock:
            state.stopping = False


async def close_bound(agent: "DeepAgent", *, context: "PersistentBuildContext") -> None:
    """Release captured owner resources only after the native call has exited."""
    state = _validate(agent, context)
    with context.binding.lock:
        if state.closed:
            return
        if state.active_task is not None or state.closing or state.stopping:
            raise build_error(StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR, error_msg="Persistent call still active")
        state.closing = True
    try:
        await agent.cleanup_task_resources()
        for name, tool_id, tool in state.owned_tools:
            # Do not remove a replacement resource or another invocation's tool.
            if tool is not None and Runner.resource_mgr.get_tool(tool_id) is tool:
                Runner.resource_mgr.remove_tool(tool_id)
                card = state.abilities.get(name)
                if card is not None and card.id == tool_id:
                    state.abilities.remove(name)
        state.closed = True
    finally:
        state.closing = False
