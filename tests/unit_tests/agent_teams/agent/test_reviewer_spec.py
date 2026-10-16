# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for narrowing a member spec into a temp reviewer base spec."""

import pytest

from openjiuwen.agent_teams.agent.scheduling.reviewer_spec import build_reviewer_agent_spec
from openjiuwen.agent_teams.schema.deep_agent_spec import (
    BuiltinToolSpec,
    DeepAgentSpec,
    RailSpec,
    SubAgentSpec,
)
from openjiuwen.core.foundation.tool import ToolCard
from openjiuwen.core.single_agent.schema.agent_card import AgentCard


def _member_spec() -> DeepAgentSpec:
    return DeepAgentSpec(
        rails=[
            RailSpec(type="core.sys_operation", params={"bash_deny_patterns": ["rm -rf"]}),
            RailSpec(type="host.heartbeat"),
        ],
        tools=[
            BuiltinToolSpec(type="host.web_fetch"),
            BuiltinToolSpec(type="host.cron"),
            ToolCard(id="raw", name="raw_tool", description="raw"),
        ],
        subagents=[SubAgentSpec(agent_card=AgentCard(name="helper"), system_prompt="")],
        add_general_purpose_agent=True,
        enable_async_subagent=True,
        enable_task_planning=True,
    )


@pytest.mark.level0
def test_framework_trimming_applies_without_allow_lists():
    base = _member_spec()

    spec = build_reviewer_agent_spec(base)

    assert [rail.type for rail in spec.rails] == ["core.sys_operation", "host.heartbeat"]
    assert spec.rails[0].params == {"bash_deny_patterns": ["rm -rf"], "read_only": True}
    assert len(spec.tools) == 3
    assert spec.subagents is None
    assert spec.add_general_purpose_agent is False
    assert spec.enable_async_subagent is False
    assert spec.enable_task_planning is False
    # The source spec is left untouched.
    assert "read_only" not in base.rails[0].params
    assert base.enable_task_planning is True


@pytest.mark.level0
def test_allow_lists_keep_only_named_rails_and_builtin_tools():
    spec = build_reviewer_agent_spec(
        _member_spec(),
        rail_types={"core.sys_operation"},
        tool_types={"host.web_fetch"},
    )

    assert [rail.type for rail in spec.rails] == ["core.sys_operation"]
    # Raw tool cards cannot be classified by type and are inherited.
    assert [getattr(tool, "type", None) or tool.name for tool in spec.tools] == ["host.web_fetch", "raw_tool"]
