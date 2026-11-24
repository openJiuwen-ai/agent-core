# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Lifecycle aclose / round-end cleanup regressions for issue#5240 (!3890 review)."""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.agent_teams.harness.native_harness import NativeHarness
from openjiuwen.agent_teams.harness.state import HarnessInternalState, HarnessState
from openjiuwen.core.runner.resources_manager.base import Error
from openjiuwen.core.single_agent.ability_manager import AbilityManager
from openjiuwen.core.single_agent.base import BaseAgent
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.deep_agent import DeepAgent
from openjiuwen.harness.schema.config import DeepAgentConfig


@pytest.mark.asyncio
async def test_deep_agent_aclose_stops_before_clearing_registrations() -> None:
    """Permanent teardown must stop loops before yanking tools/callbacks."""
    order: list[str] = []

    with patch(
        "openjiuwen.harness.deep_agent.schedule_image_support_probe",
        MagicMock(),
    ):
        agent = DeepAgent(AgentCard(name="deep", description="test")).configure(
            DeepAgentConfig(enable_task_loop=False)
        )

    async def _stop() -> None:
        order.append("stop")

    async def _super_aclose(_self) -> None:
        order.append("super_aclose")

    async def _release(*_args, **_kwargs) -> None:
        order.append("subagents")

    agent.stop = AsyncMock(side_effect=_stop)  # type: ignore[method-assign]
    agent._react_agent = None
    agent._loop_controller = None
    agent._controller = None

    with (
        patch.object(BaseAgent, "aclose", _super_aclose),
        patch(
            "openjiuwen.harness.tools.subagent._control_registry.release_all_subagent_controls",
            _release,
        ),
    ):
        await agent.aclose()

    assert order[:2] == ["stop", "super_aclose"]
    assert "subagents" in order


@pytest.mark.asyncio
async def test_native_harness_stop_clears_parent_and_react_callbacks() -> None:
    """Round-end stop must clear both DeepAgent and bridged react namespaces."""
    parent_clear = AsyncMock()
    react_clear = AsyncMock()

    async def _ack_stop(cmd) -> None:
        if not cmd.ack.done():
            cmd.ack.set_result(None)

    async def _done_supervisor() -> None:
        return None

    harness = object.__new__(NativeHarness)
    harness._st = HarnessInternalState()
    harness._st.phase = HarnessState.IDLE
    harness._async_tool_runtime = None
    harness._control = SimpleNamespace(put=AsyncMock(side_effect=_ack_stop))
    harness._session = None
    harness._owns_session = False
    harness._forwarder_task = None
    harness._events = SimpleNamespace(unregister_namespace=AsyncMock())
    harness.ability_manager = SimpleNamespace(teardown_tools=MagicMock())
    harness._agent_callback_manager = SimpleNamespace(clear=parent_clear)
    harness._react_agent = SimpleNamespace(
        agent_callback_manager=SimpleNamespace(clear=react_clear)
    )
    harness._loop_controller = None
    harness._bound_session_id = "sess-lifecycle"
    harness._st.supervisor_task = asyncio.create_task(_done_supervisor())

    await NativeHarness.stop(harness)

    parent_clear.assert_awaited_once()
    react_clear.assert_awaited_once()


def test_teardown_tools_logs_tag_remove_failures(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Owner-tag sweep failures must not stay silent on the release path."""

    class _FakeResourceMgr:
        def remove_tool(self, *args, **kwargs):
            return Error("boom")

    import openjiuwen.core.runner as runner_mod

    monkeypatch.setattr(runner_mod.Runner, "resource_mgr", _FakeResourceMgr())

    am = AbilityManager(owner_id="agent-x")
    with caplog.at_level(logging.WARNING):
        am.teardown_tools()

    assert any(
        "remove_tool failed" in record.message and "ability-owner:agent-x" in record.message
        for record in caplog.records
    )
