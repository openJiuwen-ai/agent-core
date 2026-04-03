# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
"""Unit tests for HeartbeatRail."""

# pylint: disable=protected-access
from __future__ import annotations

from unittest.mock import (
    AsyncMock,
    MagicMock,
)

import pytest

from openjiuwen.core.session.agent import Session
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.rail.base import (
    InvokeInputs,
    RunKind,
    RunContext,
    HeartbeatReason,
)
from openjiuwen.core.sys_operation import (
    SysOperationCard,
    OperationMode,
)
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.deep_agent import DeepAgent
from openjiuwen.harness.workspace.workspace import Workspace
from openjiuwen.harness.rails.heartbeat_rail import (
    HeartbeatRail,
)
from openjiuwen.harness.schema.config import (
    DeepAgentConfig,
)


def _make_operation():
    """Create a SysOperation for tests."""
    card_id = "test_heartbeat_rail_op"
    card = SysOperationCard(id=card_id, mode=OperationMode.LOCAL)
    Runner.resource_mgr.add_sys_operation(card)
    return Runner.resource_mgr.get_sys_operation(card.id)


def _make_rail() -> HeartbeatRail:
    """Build a HeartbeatRail with test defaults."""
    op = _make_operation()
    rail = HeartbeatRail()
    rail.set_sys_operation(op)
    return rail


def _make_agent() -> DeepAgent:
    """Build a DeepAgent with optional workspace."""
    agent = DeepAgent(AgentCard(name="deep", description="test"))
    agent.configure(
        DeepAgentConfig(
            enable_task_loop=True,
            workspace=Workspace(),
        )
    )
    return agent


def _make_ctx(session=None, inputs=None):
    """Build a minimal AgentCallbackContext mock."""
    ctx = MagicMock()
    if not session:
        session = Session()
    ctx.session = session
    ctx.inputs = inputs
    ctx.extra = {}
    return ctx


def test_init_sets_system_prompt_builder() -> None:
    """init sets system_prompt_builder from agent."""
    rail = _make_rail()
    agent = _make_agent()

    rail.init(agent)

    assert rail.system_prompt_builder is not None


def test_init_sets_sys_operation() -> None:
    """init sets sys_operation from agent.deep_config."""
    rail = _make_rail()
    agent = _make_agent()

    rail.init(agent)

    assert rail.sys_operation is not None


def test_uninit_removes_heartbeat_section() -> None:
    """uninit removes heartbeat section from system_prompt_builder."""
    rail = _make_rail()
    agent = _make_agent()
    rail.init(agent)

    mock_builder = MagicMock()
    rail.system_prompt_builder = mock_builder

    rail.uninit(agent)

    mock_builder.remove_section.assert_called_once_with("heartbeat")


def test_uninit_without_system_prompt_builder() -> None:
    """uninit is safe when system_prompt_builder is None."""
    rail = _make_rail()
    agent = _make_agent()

    rail.uninit(agent)


def test_priority_is_80() -> None:
    """HeartbeatRail has priority 80."""
    rail = _make_rail()
    assert rail.priority == 80



@pytest.mark.asyncio
async def test_before_invoke_skips_non_heartbeat() -> None:
    """before_invoke skips saving context for non-heartbeat runs."""
    rail = _make_rail()
    agent = _make_agent()
    rail.init(agent)

    inputs = InvokeInputs(query="test query", run_kind=RunKind.NORMAL, run_context=None)
    ctx = _make_ctx(inputs=inputs)

    await rail.before_invoke(ctx)

    assert "run_kind" not in ctx.extra
    assert "run_context" not in ctx.extra


@pytest.mark.asyncio
async def test_before_invoke_skips_non_invoke_inputs() -> None:
    """before_invoke skips when inputs is not InvokeInputs."""
    rail = _make_rail()
    agent = _make_agent()
    rail.init(agent)

    ctx = _make_ctx(inputs={"query": "test"})

    await rail.before_invoke(ctx)

    assert "run_kind" not in ctx.extra


@pytest.mark.asyncio
async def test_before_model_call_injects_heartbeat_section() -> None:
    """before_model_call injects heartbeat section for heartbeat runs."""
    rail = _make_rail()
    agent = _make_agent()
    rail.init(agent)

    run_context = RunContext(reason=HeartbeatReason.INTERVAL, session_id="test-session")
    inputs = InvokeInputs(query="", run_kind=RunKind.HEARTBEAT, run_context=run_context)
    ctx = _make_ctx(inputs=inputs)
    ctx.extra["run_kind"] = RunKind.HEARTBEAT

    mock_builder = MagicMock()
    rail.system_prompt_builder = mock_builder

    mock_fs = MagicMock()
    mock_read_result = MagicMock()
    mock_read_result.code = 0
    mock_read_result.data.content = "Test heartbeat content"
    mock_fs.read_file = AsyncMock(return_value=mock_read_result)
    rail.sys_operation.fs = MagicMock(return_value=mock_fs)

    await rail.before_model_call(ctx)

    mock_builder.add_section.assert_called_once()


@pytest.mark.asyncio
async def test_before_model_call_skips_non_heartbeat() -> None:
    """before_model_call skips injection for non-heartbeat runs."""
    rail = _make_rail()
    agent = _make_agent()
    rail.init(agent)

    inputs = InvokeInputs(query="test query", run_kind=RunKind.NORMAL)
    ctx = _make_ctx(inputs=inputs)
    ctx.extra["run_kind"] = RunKind.NORMAL

    mock_builder = MagicMock()
    rail.system_prompt_builder = mock_builder

    await rail.before_model_call(ctx)

    mock_builder.add_section.assert_not_called()


@pytest.mark.asyncio
async def test_before_model_call_without_system_prompt_builder() -> None:
    """before_model_call returns early when system_prompt_builder is None."""
    rail = _make_rail()
    agent = _make_agent()
    rail.init(agent)

    run_context = RunContext(reason=HeartbeatReason.INTERVAL)
    inputs = InvokeInputs(query="", run_kind=RunKind.HEARTBEAT, run_context=run_context)
    ctx = _make_ctx(inputs=inputs)
    ctx.extra["run_kind"] = RunKind.HEARTBEAT

    rail.system_prompt_builder = None

    await rail.before_model_call(ctx)


def test_invoke_inputs_is_heartbeat() -> None:
    """InvokeInputs.is_heartbeat returns True for heartbeat runs."""
    run_context = RunContext(reason=HeartbeatReason.INTERVAL)
    inputs = InvokeInputs(query="", run_kind=RunKind.HEARTBEAT, run_context=run_context)

    assert inputs.is_heartbeat() is True


def test_invoke_inputs_is_not_heartbeat() -> None:
    """InvokeInputs.is_heartbeat returns False for normal runs."""
    inputs = InvokeInputs(query="test query", run_kind=RunKind.NORMAL)

    assert inputs.is_heartbeat() is False


def test_invoke_inputs_is_lightweight_context() -> None:
    """InvokeInputs.is_lightweight_context returns True for lightweight mode."""
    run_context = RunContext(reason=HeartbeatReason.INTERVAL, context_mode="lightweight")
    inputs = InvokeInputs(query="", run_kind=RunKind.HEARTBEAT, run_context=run_context)

    assert inputs.is_lightweight_context() is True


def test_invoke_inputs_is_not_lightweight_context() -> None:
    """InvokeInputs.is_lightweight_context returns False for non-lightweight mode."""
    run_context = RunContext(reason=HeartbeatReason.INTERVAL, context_mode="full")
    inputs = InvokeInputs(query="", run_kind=RunKind.HEARTBEAT, run_context=run_context)

    assert inputs.is_lightweight_context() is False


def test_invoke_inputs_is_lightweight_context_without_context() -> None:
    """InvokeInputs.is_lightweight_context returns False when run_context is None."""
    inputs = InvokeInputs(query="", run_kind=RunKind.HEARTBEAT, run_context=None)

    assert inputs.is_lightweight_context() is False


def test_run_kind_enum() -> None:
    """RunKind enum has correct values."""
    assert RunKind.NORMAL.value == "normal"
    assert RunKind.HEARTBEAT.value == "heartbeat"


def test_heartbeat_reason_enum() -> None:
    """HeartbeatReason enum has correct values."""
    assert HeartbeatReason.INTERVAL.value == "interval"
    assert HeartbeatReason.MANUAL.value == "manual"


def test_run_context_dataclass() -> None:
    """RunContext dataclass can be instantiated."""
    context = RunContext(
        reason=HeartbeatReason.INTERVAL, session_id="test-session", context_mode="lightweight", extra={"key": "value"}
    )

    assert context.reason == HeartbeatReason.INTERVAL
    assert context.session_id == "test-session"
    assert context.context_mode == "lightweight"
    assert context.extra == {"key": "value"}


def test_run_context_defaults() -> None:
    """RunContext dataclass has correct defaults."""
    context = RunContext()

    assert context.reason is None
    assert context.session_id is None
    assert context.context_mode is None
    assert context.extra == {}
