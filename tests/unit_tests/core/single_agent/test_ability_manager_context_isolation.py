"""Each tool call may edit callback-local state without affecting its peers."""
import asyncio
import json
import uuid
from types import SimpleNamespace

import pytest

from openjiuwen.core.foundation.llm import ToolCall
from openjiuwen.core.foundation.tool import LocalFunction, ToolCard
from openjiuwen.core.session.agent import Session
from openjiuwen.core.single_agent.ability_manager import AbilityManager
from openjiuwen.core.single_agent.agent_callback_manager import AgentCallbackManager
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, AgentCallbackEvent


@pytest.mark.asyncio
@pytest.mark.parametrize("parallel", [True, False])
async def test_tool_local_markers_do_not_rewrite_peer_arguments(parallel):
    callbacks = AgentCallbackManager(uuid.uuid4().hex)
    both_started = asyncio.Event()
    started = []

    async def stamp(ctx):
        ctx.extra["marker"] = ctx.inputs.tool_call.id
        started.append(ctx.inputs.tool_call.id)
        if len(started) == 2:
            both_started.set()
        if parallel:
            await asyncio.wait_for(both_started.wait(), 2)
        ctx.inputs.tool_args = json.dumps({"label": ctx.extra["marker"]})

    await callbacks.register_callback(AgentCallbackEvent.BEFORE_TOOL_CALL, stamp)
    manager = AbilityManager(owner_id=uuid.uuid4().hex)
    tool = LocalFunction(ToolCard(id="echo", name="echo", description="test", input_params={
        "type": "object", "properties": {"label": {"type": "string"}}, "required": ["label"],
    }), lambda label: label)
    manager.add_ability(tool.card, tool)
    session = Session(session_id="tool-state")
    ctx = AgentCallbackContext(agent=SimpleNamespace(agent_callback_manager=callbacks),
                               session=session, extra={"run_id": "inherited"})
    try:
        results = await manager.execute(ctx, [
            ToolCall(id=marker, name="echo", type="function", arguments="{}") for marker in ("A", "B")
        ], session, parallel_tool_calls=parallel)
        assert [result for result, _ in results] == ["A", "B"]
        assert ctx.extra == {"run_id": "inherited"}
    finally:
        manager.teardown_tools()
