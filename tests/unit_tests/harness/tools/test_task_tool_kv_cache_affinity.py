# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for TaskTool KV-cache affinity lifecycle."""

from __future__ import annotations

import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call, patch

import pytest

from openjiuwen.core.foundation.kv_cache import KVCacheAffinityConfig
from openjiuwen.core.foundation.tool import ToolCard
from openjiuwen.core.session.agent import Session
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.tools.subagent.task_tool import TaskTool


class _FakeSubAgent:
    def __init__(self, agent_id: str, *, output: str = "done", error: Exception | None = None) -> None:
        self.card = AgentCard(id=agent_id, name=agent_id, description=agent_id)
        self.output = output
        self.error = error
        self.inputs: list[dict] = []

    async def invoke(self, inputs: dict) -> dict:
        self.inputs.append(dict(inputs))
        if self.error:
            raise self.error
        return {"output": self.output}


def _make_tool(*, enabled: bool = True, subagent: _FakeSubAgent | None = None) -> tuple[TaskTool, object]:
    model = SimpleNamespace(name="model")

    def create_subagent(*_args, **_kwargs):
        return subagent or _FakeSubAgent("sub")

    parent = SimpleNamespace(
        deep_config=SimpleNamespace(
            model=model,
            kv_cache_affinity_config=KVCacheAffinityConfig(enable_kv_cache_affinity=enabled),
        ),
        create_subagent=create_subagent,
    )
    return TaskTool(ToolCard(id="task_tool", name="task_tool", description="task"), parent), model


@pytest.mark.asyncio
async def test_sticky_subagent_prefetches_before_invoke_and_offloads_after_success() -> None:
    subagent = _FakeSubAgent("browser")
    tool, model = _make_tool(subagent=subagent)

    with patch(
            "openjiuwen.harness.kv_cache.kv_cache_hooks.dispatch_session_kv_cache_signal",
        new=Mock(return_value=True),
    ) as dispatch_mock, patch(
            "openjiuwen.harness.kv_cache.kv_cache_hooks.evict_session_kv_cache",
        new=AsyncMock(return_value=True),
    ) as evict_mock:
        result = await tool.invoke(
            {"subagent_type": "browser_agent", "task_description": "run task"},
            session=Session(session_id="parent_session"),
        )

    assert result.success is True
    assert subagent.inputs[0]["conversation_id"] == "parent_session_sub_browser_agent"
    assert subagent.inputs[0]["parent_session_id"] == "parent_session"
    assert dispatch_mock.call_args_list == [
        call(
            model,
            "prefetch",
            session_id="parent_session_sub_browser_agent",
            parent_session_id="parent_session",
            enabled=True,
        ),
        call(
            model,
            "offload",
            session_id="parent_session_sub_browser_agent",
            parent_session_id="parent_session",
            enabled=True,
        ),
    ]
    evict_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_sticky_subagent_failure_evicts_and_keeps_original_exception() -> None:
    subagent = _FakeSubAgent("browser", error=RuntimeError("subagent boom"))
    tool, model = _make_tool(subagent=subagent)

    with patch(
            "openjiuwen.harness.kv_cache.kv_cache_hooks.dispatch_session_kv_cache_signal",
        new=Mock(return_value=True),
    ) as dispatch_mock, patch(
            "openjiuwen.harness.kv_cache.kv_cache_hooks.evict_session_kv_cache",
        new=AsyncMock(return_value=True),
    ) as evict_mock:
        with pytest.raises(Exception, match="subagent boom"):
            await tool.invoke(
                {"subagent_type": "browser_agent", "task_description": "run task"},
                session=Session(session_id="parent_session"),
            )

    dispatch_mock.assert_called_once_with(
        model,
        "prefetch",
        session_id="parent_session_sub_browser_agent",
        parent_session_id="parent_session",
        enabled=True,
    )
    evict_mock.assert_awaited_once_with(
        model,
        session_id="parent_session_sub_browser_agent",
        parent_session_id="parent_session",
        enabled=True,
    )


@pytest.mark.asyncio
async def test_random_subagent_success_evicts_generated_sub_session() -> None:
    subagent = _FakeSubAgent("code")
    tool, model = _make_tool(subagent=subagent)

    with patch(
            "openjiuwen.harness.kv_cache.kv_cache_hooks.dispatch_session_kv_cache_signal",
        new=Mock(return_value=True),
    ) as dispatch_mock, patch(
            "openjiuwen.harness.kv_cache.kv_cache_hooks.evict_session_kv_cache",
        new=AsyncMock(return_value=True),
    ) as evict_mock:
        result = await tool.invoke(
            {"subagent_type": "code", "task_description": "run task"},
            session=Session(session_id="parent_session"),
        )

    assert result.success is True
    sub_session_id = subagent.inputs[0]["conversation_id"]
    assert re.fullmatch(r"parent_session_sub_code_[0-9a-f]{8}", sub_session_id)
    dispatch_mock.assert_not_called()
    evict_mock.assert_awaited_once_with(
        model,
        session_id=sub_session_id,
        parent_session_id="parent_session",
        enabled=True,
    )


@pytest.mark.asyncio
async def test_random_subagent_failure_evicts_generated_sub_session_and_keeps_original_exception() -> None:
    subagent = _FakeSubAgent("code", error=RuntimeError("boom"))
    tool, model = _make_tool(subagent=subagent)

    with patch(
            "openjiuwen.harness.kv_cache.kv_cache_hooks.dispatch_session_kv_cache_signal",
        new=Mock(return_value=True),
    ) as dispatch_mock, patch(
            "openjiuwen.harness.kv_cache.kv_cache_hooks.evict_session_kv_cache",
        new=AsyncMock(return_value=True),
    ) as evict_mock:
        with pytest.raises(Exception, match="boom"):
            await tool.invoke(
                {"subagent_type": "code", "task_description": "run task"},
                session=Session(session_id="parent_session"),
            )

    sub_session_id = subagent.inputs[0]["conversation_id"]
    assert re.fullmatch(r"parent_session_sub_code_[0-9a-f]{8}", sub_session_id)
    dispatch_mock.assert_not_called()
    evict_mock.assert_awaited_once_with(
        model,
        session_id=sub_session_id,
        parent_session_id="parent_session",
        enabled=True,
    )


@pytest.mark.asyncio
async def test_kvc_helper_failure_does_not_override_success_result() -> None:
    subagent = _FakeSubAgent("browser", output="kept")
    tool, _ = _make_tool(subagent=subagent)

    with patch(
            "openjiuwen.harness.kv_cache.kv_cache_hooks.dispatch_session_kv_cache_signal",
        new=Mock(side_effect=RuntimeError("signal failed")),
    ), patch(
            "openjiuwen.harness.kv_cache.kv_cache_hooks.evict_session_kv_cache",
        new=AsyncMock(side_effect=RuntimeError("evict failed")),
    ):
        result = await tool.invoke(
            {"subagent_type": "browser_agent", "task_description": "run task"},
            session=Session(session_id="parent_session"),
        )

    assert result.success is True
    assert result.data["output"] == "kept"


@pytest.mark.asyncio
async def test_kvc_helper_failure_does_not_override_original_subagent_exception() -> None:
    subagent = _FakeSubAgent("code", error=RuntimeError("original subagent error"))
    tool, _ = _make_tool(subagent=subagent)

    with patch(
            "openjiuwen.harness.kv_cache.kv_cache_hooks.evict_session_kv_cache",
        new=AsyncMock(side_effect=RuntimeError("evict failed")),
    ):
        with pytest.raises(Exception, match="original subagent error"):
            await tool.invoke(
                {"subagent_type": "code", "task_description": "run task"},
                session=Session(session_id="parent_session"),
            )


@pytest.mark.asyncio
async def test_affinity_disabled_preserves_baseline_invoke_and_skips_helpers() -> None:
    subagent = _FakeSubAgent("browser")
    tool, _ = _make_tool(enabled=False, subagent=subagent)

    with patch(
            "openjiuwen.harness.kv_cache.kv_cache_hooks.dispatch_session_kv_cache_signal",
        new=Mock(return_value=False),
    ) as dispatch_mock, patch(
            "openjiuwen.harness.kv_cache.kv_cache_hooks.evict_session_kv_cache",
        new=AsyncMock(return_value=False),
    ) as evict_mock:
        result = await tool.invoke(
            {"subagent_type": "browser_agent", "task_description": "run task"},
            session=Session(session_id="parent_session"),
        )

    assert result.success is True
    assert subagent.inputs == [
        {
            "query": "run task",
            "conversation_id": "parent_session_sub_browser_agent",
        }
    ]
    dispatch_mock.assert_not_called()
    evict_mock.assert_not_awaited()
