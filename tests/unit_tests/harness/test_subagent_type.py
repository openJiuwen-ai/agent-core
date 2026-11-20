# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Regression tests for model-provided ``subagent_type`` name variants."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.foundation.tool import ToolCard
from openjiuwen.core.session.agent import Session
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.deep_agent import DeepAgent
from openjiuwen.harness.schema.config import SubAgentConfig
from openjiuwen.harness.subagent_runtime.models import SpawnResult, SubagentStatus
from openjiuwen.harness.subagent_type import (
    canonical_subagent_key,
    normalize_subagent_type,
    registered_subagent_name,
    resolve_subagent_type_name,
)
from openjiuwen.harness.tools.subagent.session_tools import SessionToolkit, SessionsSpawnTool
from openjiuwen.harness.tools.subagent.subagent_tools import SubagentSpawnTool
from openjiuwen.harness.tools.subagent.task_tool import TaskTool


def _spec(name: str) -> SubAgentConfig:
    return SubAgentConfig(agent_card=AgentCard(name=name), system_prompt="test")


def _parent(*names: str) -> DeepAgent:
    parent = DeepAgent(AgentCard(name="parent"))
    parent._deep_config = SimpleNamespace(subagents=[_spec(name) for name in names])
    return parent


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (" general-purpose ", "general-purpose"),
        ("general\u2011purpose", "general-purpose"),
        ("general\u2010purpose", "general-purpose"),
        ("general\u2013purpose", "general-purpose"),
        ("general\u2014purpose", "general-purpose"),
        ("general\u00a0purpose", "general purpose"),
        ("general\u00adpurpose", "generalpurpose"),
        ("", ""),
        (None, ""),
    ],
)
def test_normalize_subagent_type(value: object, expected: str) -> None:
    assert normalize_subagent_type(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "general-purpose",
        "general_purpose",
        "GENERAL PURPOSE",
        "general\u2011purpose",
        " general-purpose ",
    ],
)
def test_canonical_subagent_key_ignores_case_and_separators(value: str) -> None:
    assert canonical_subagent_key(value) == "generalpurpose"


def test_registered_subagent_name_rejects_non_string_names() -> None:
    spec = SimpleNamespace(agent_card=SimpleNamespace(name=MagicMock()))
    assert registered_subagent_name(spec) == ""


def test_resolve_uses_registered_name_before_allowed_list() -> None:
    parent = _parent("general-purpose")
    assert resolve_subagent_type_name(parent, "general_purpose", {"general-purpose"}) == "general-purpose"


def test_resolve_falls_back_to_unique_allowed_match() -> None:
    parent = SimpleNamespace()  # host/mock parent without _find_subagent_spec
    assert resolve_subagent_type_name(parent, "GENERAL_PURPOSE", {"general-purpose"}) == "general-purpose"


def test_resolve_keeps_normalized_input_when_allowed_is_ambiguous() -> None:
    parent = SimpleNamespace()
    assert (
        resolve_subagent_type_name(parent, "FOO_BAR", {"foo-bar", "foo_bar"})
        == "FOO_BAR"
    )


def test_resolve_does_not_guess_unknown_names() -> None:
    parent = _parent("general-purpose")
    assert resolve_subagent_type_name(parent, "general-purpos", {"general-purpose"}) == "general-purpos"


def test_deep_agent_finds_exact_unicode_and_canonical_variants() -> None:
    parent = _parent("general-purpose", "explore_agent")

    assert parent._find_subagent_spec("general-purpose") is not None
    assert parent._find_subagent_spec("general\u2011purpose") is not None
    assert parent._find_subagent_spec("general_purpose") is not None
    assert parent._find_subagent_spec("GENERAL-PURPOSE") is not None
    assert parent._find_subagent_spec("explore_agent") is not None


def test_deep_agent_finds_nested_deep_agent_by_canonical_name() -> None:
    nested = DeepAgent(AgentCard(name="nested-agent"))
    parent = DeepAgent(AgentCard(name="parent"))
    parent._deep_config = SimpleNamespace(subagents=[nested])

    assert parent._find_subagent_spec("NESTED_AGENT") is nested


def test_deep_agent_rejects_ambiguous_canonical_match() -> None:
    parent = _parent("writer-v1", "writer_v1")

    assert parent._find_subagent_spec("writer-v1") is not None
    assert parent._find_subagent_spec("WRITER_V1") is None


def test_deep_agent_keeps_unknown_names_unresolved() -> None:
    parent = _parent("general-purpose")

    assert parent._find_subagent_spec("general-purpos") is None
    assert parent._find_subagent_spec("writer") is None


def _task_tool(parent: DeepAgent, allowed: set[str]) -> TaskTool:
    return TaskTool(
        ToolCard(id="task", name="task_tool", description=""),
        parent_agent=parent,
        allowed_subagent_types=allowed,
    )


def test_task_tool_resolves_variant_before_allowed_validation() -> None:
    tool = _task_tool(_parent("general-purpose"), {"general-purpose"})

    resolved, description, resume_id, capabilities = tool._parse_invocation_inputs(
        {"subagent_type": "general_purpose", "task_description": "run"}
    )

    assert resolved == "general-purpose"
    assert description == "run"
    assert resume_id == ""
    assert capabilities is None


def test_task_tool_still_rejects_unknown_or_disallowed_types() -> None:
    tool = _task_tool(_parent("general-purpose", "writer"), {"general-purpose"})

    with pytest.raises(Exception, match="not available through task_tool"):
        tool._parse_invocation_inputs(
            {"subagent_type": "general-purpos", "task_description": "run"}
        )

    with pytest.raises(Exception, match="not available through task_tool"):
        tool._parse_invocation_inputs(
            {"subagent_type": "writer", "task_description": "run"}
        )


@pytest.mark.asyncio
async def test_subagent_spawn_resolves_variant_before_allowed_validation() -> None:
    parent = _parent("general-purpose")
    tool = SubagentSpawnTool(
        ToolCard(id="subagent_spawn", name="subagent_spawn", description="spawn"),
        parent,
        allowed_subagent_types={"general-purpose"},
    )
    control = SimpleNamespace(
        spawn=AsyncMock(
            return_value=SpawnResult(
                subagent_id="parent_sub_general-purpose",
                task_id="task1",
                status=SubagentStatus.pending_init(),
            )
        ),
        emit_status_update=AsyncMock(),
    )
    session = Session(session_id="parent_sess")

    with patch(
        "openjiuwen.harness.tools.subagent.subagent_tools.get_subagent_control",
        return_value=control,
    ):
        result = await tool.invoke(
            {
                "subagent_type": "GENERAL_PURPOSE",
                "task_description": "hello",
                "display_name": "General",
                "role": "worker",
            },
            session=session,
        )

    control.spawn.assert_awaited_once_with(
        "general-purpose",
        "hello",
        display_name="General",
        role="worker",
        browser_capabilities=None,
    )
    assert result.success is True


@pytest.mark.asyncio
async def test_subagent_spawn_still_rejects_unknown_type() -> None:
    parent = _parent("general-purpose")
    tool = SubagentSpawnTool(
        ToolCard(id="subagent_spawn", name="subagent_spawn", description="spawn"),
        parent,
        allowed_subagent_types={"general-purpose"},
    )
    session = Session(session_id="parent_sess")

    with pytest.raises(Exception, match="not available through subagent_spawn"):
        await tool.invoke(
            {
                "subagent_type": "general-purpos",
                "task_description": "hello",
                "display_name": "General",
                "role": "worker",
            },
            session=session,
        )


@pytest.mark.asyncio
async def test_sessions_spawn_resolves_browser_variant_before_capability_check() -> None:
    task_manager = MagicMock()
    task_manager.add_task = AsyncMock()
    browser_spec = SimpleNamespace(agent_card=SimpleNamespace(name="browser_agent"))
    parent = SimpleNamespace(
        deep_config=SimpleNamespace(enable_task_loop=True),
        event_handler=SimpleNamespace(task_manager=task_manager),
        _find_subagent_spec=MagicMock(return_value=browser_spec),
    )
    tool = SessionsSpawnTool(
        parent_agent=parent,
        toolkit=SessionToolkit(),
        language="en",
    )
    session = MagicMock(spec=Session)
    session.get_session_id.return_value = "session-1"

    await tool.invoke(
        {
            "subagent_type": "BROWSER_AGENT",
            "task_description": "save the page",
            "browser_capabilities": ["pdf"],
        },
        session=session,
    )

    task = task_manager.add_task.await_args.args[0]
    assert task.metadata["subagent_type"] == "browser_agent"
    assert task.metadata["browser_capabilities"] == ["pdf"]
