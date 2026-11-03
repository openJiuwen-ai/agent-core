"""Independent task caches survive approval and are evicted at terminal cleanup."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from openjiuwen.core.runner import Runner  # noqa: F401
from openjiuwen.core.foundation.tool import ToolCard
from openjiuwen.core.session import InteractiveInput
from openjiuwen.core.session.agent import Session
from openjiuwen.harness.tools.subagent.task_tool import TaskTool


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["general-purpose", "browser_agent", "verification_agent"])
@pytest.mark.parametrize("fails", [False, True])
async def test_independent_task_evicts_cache_after_success_or_failure(kind, fails):
    inputs_seen, cleaned = [], []
    async def invoke(inputs):
        inputs_seen.append(inputs)
        if fails:
            raise RuntimeError("boom")
        return {"output": "done"}
    async def cleanup():
        cleaned.append(True)
    model = object()
    parent = SimpleNamespace(
        deep_config=SimpleNamespace(model=model, kv_cache_affinity_config=SimpleNamespace(enable_kv_cache_affinity=True)),
        create_subagent=lambda *a, **kw: SimpleNamespace(invoke=invoke, cleanup_task_resources=cleanup),
    )
    tool = TaskTool(ToolCard(id="task", name="task_tool", description="test"), parent)
    with patch("openjiuwen.harness.kv_cache.kv_cache_hooks.evict_session_kv_cache", new_callable=AsyncMock) as evict, patch(
        "openjiuwen.harness.kv_cache.kv_cache_hooks.dispatch_session_kv_cache_signal", new_callable=Mock,
    ) as signal:
        try:
            result = await tool.invoke(
                {"action": "dispatch", "tasks": [{"subagent_type": kind, "task_description": "work"}]},
                session=Session(session_id="parent"), tool_call_id="work",
            )
            assert result.data["ready"][0]["status"] == ("failed" if fails else "completed")
            assert cleaned == [True]
            evict.assert_awaited_once_with(
                model, session_id=inputs_seen[0]["conversation_id"], parent_session_id="parent", enabled=True,
            )
            signal.assert_not_called()
        finally:
            await tool.close_task_groups("parent")


@pytest.mark.asyncio
async def test_approval_keeps_same_child_and_cache_until_resumed():
    children, cleaned = [], []
    async def invoke(inputs):
        if isinstance(inputs["query"], InteractiveInput):
            return {"output": "approved"}
        return {"result_type": "interrupt", "interrupt_ids": ["inner"], "state": []}
    async def cleanup():
        cleaned.append(True)
    def create(*args, **kwargs):
        child = SimpleNamespace(invoke=invoke, cleanup_task_resources=cleanup)
        children.append(child)
        return child
    parent = SimpleNamespace(
        deep_config=SimpleNamespace(model=object(), kv_cache_affinity_config=SimpleNamespace(enable_kv_cache_affinity=True)),
        create_subagent=create,
    )
    tool = TaskTool(ToolCard(id="task", name="task_tool", description="test"), parent)
    args = {"action": "dispatch", "tasks": [{"subagent_type": "verification_agent", "task_description": "verify"}]}
    session = Session(session_id="approval")
    with patch("openjiuwen.harness.kv_cache.kv_cache_hooks.evict_session_kv_cache", new_callable=AsyncMock) as evict:
        try:
            result = await tool.invoke(args, session=session, tool_call_id="original")
            assert result["result_type"] == "interrupt"
            evict.assert_not_awaited()
            assert not cleaned
            reply = InteractiveInput()
            reply.update(result["interrupt_ids"][0], {"approved": True})
            result = await tool.invoke({**args, "query": reply}, session=session, tool_call_id="original")
            assert result.data["ready"][0]["output"] == "approved"
            assert len(children) == 1 and cleaned == [True]
            evict.assert_awaited_once()
        finally:
            await tool.close_task_groups("approval")
