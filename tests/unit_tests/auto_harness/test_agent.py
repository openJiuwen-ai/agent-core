# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""test_agent — auto-harness agent 工厂测试。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from openjiuwen.auto_harness.agent import (
    create_assess_agent,
    create_auto_harness_agent,
)
from openjiuwen.auto_harness.rails.context_rail import (
    AutoHarnessContextRail,
)
from openjiuwen.auto_harness.schema import (
    AutoHarnessConfig,
)
from openjiuwen.core.sys_operation import SysOperation
from openjiuwen.harness.cli.rails.tool_tracker import (
    ToolTrackingRail,
)
from openjiuwen.harness.rails.lsp_rail import (
    LspRail,
)
from openjiuwen.harness.rails.skill_use_rail import (
    SkillUseRail,
)
from openjiuwen.harness.schema.config import (
    SubAgentConfig,
)
from openjiuwen.harness.tools import (
    WebFetchWebpageTool,
    WebFreeSearchTool,
)


def test_create_auto_harness_agent_includes_tool_tracker():
    """主 agent 应挂载 ToolTrackingRail。"""
    captured = {}

    def _fake_create_deep_agent(**kwargs):
        captured.update(kwargs)
        return object()

    with patch(
        "openjiuwen.auto_harness.agent.create_deep_agent",
        side_effect=_fake_create_deep_agent,
    ):
        create_auto_harness_agent(
            AutoHarnessConfig(model=MagicMock()),
        )

    rails = captured["rails"]
    assert any(
        isinstance(rail, ToolTrackingRail)
        for rail in rails
    )
    assert any(
        isinstance(rail, AutoHarnessContextRail)
        for rail in rails
    )
    assert any(
        isinstance(rail, LspRail)
        for rail in rails
    )
    skill_rails = [
        rail for rail in rails
        if isinstance(rail, SkillUseRail)
    ]
    assert len(skill_rails) == 1
    assert any(
        path.endswith("/implement")
        for path in skill_rails[0].skills_dir
    )
    assert not any(
        path.endswith("/evolve")
        for path in skill_rails[0].skills_dir
    )
    assert captured["enable_async_subagent"] is True
    subagents = captured["subagents"]
    assert any(
        isinstance(spec, SubAgentConfig)
        and spec.agent_card.name == "explore_agent"
        for spec in subagents
    )
    assert any(
        isinstance(spec, SubAgentConfig)
        and spec.agent_card.name == "browser_agent"
        for spec in subagents
    )
    assert isinstance(captured["sys_operation"], SysOperation)
    assert (
        captured["sys_operation"]._run_config.shell_allowlist
        is None
    )
    assert (
        captured["sys_operation"]._run_config.restrict_to_sandbox
        is False
    )


def test_create_auto_harness_agent_honors_workspace_override():
    """主 agent 应优先绑定显式传入的 worktree workspace。"""
    captured = {}

    def _fake_create_deep_agent(**kwargs):
        captured.update(kwargs)
        return object()

    with patch(
        "openjiuwen.auto_harness.agent.create_deep_agent",
        side_effect=_fake_create_deep_agent,
    ):
        create_auto_harness_agent(
            AutoHarnessConfig(
                model=MagicMock(),
                workspace="/repo/default",
            ),
            workspace_override="/repo/worktrees/task-1",
        )

    assert (
        captured["workspace"]
        == "/repo/worktrees/task-1"
    )


def test_create_assess_agent_includes_tool_tracker():
    """只读阶段 agent 也应挂载 ToolTrackingRail。"""
    captured = {}

    def _fake_create_deep_agent(**kwargs):
        captured.update(kwargs)
        return object()

    with patch(
        "openjiuwen.auto_harness.agent.create_deep_agent",
        side_effect=_fake_create_deep_agent,
    ):
        create_assess_agent(
            AutoHarnessConfig(model=MagicMock()),
        )

    rails = captured["rails"]
    assert any(
        isinstance(rail, ToolTrackingRail)
        for rail in rails
    )
    assert any(
        isinstance(rail, AutoHarnessContextRail)
        for rail in rails
    )
    assert any(
        isinstance(rail, LspRail)
        for rail in rails
    )
    assert captured["enable_async_subagent"] is True
    subagents = captured["subagents"]
    assert any(
        isinstance(spec, SubAgentConfig)
        and spec.agent_card.name == "explore_agent"
        for spec in subagents
    )
    assert isinstance(captured["sys_operation"], SysOperation)
    assert (
        captured["sys_operation"]._run_config.shell_allowlist
        is None
    )
    assert (
        captured["sys_operation"]._run_config.restrict_to_sandbox
        is False
    )


def test_create_assess_agent_includes_web_research_tools():
    """评估阶段应具备网页搜索和抓取能力。"""
    captured = {}

    def _fake_create_deep_agent(**kwargs):
        captured.update(kwargs)
        return object()

    with patch(
        "openjiuwen.auto_harness.agent.create_deep_agent",
        side_effect=_fake_create_deep_agent,
    ):
        create_assess_agent(
            AutoHarnessConfig(model=MagicMock()),
        )

    tools = captured["tools"]
    assert any(
        isinstance(tool, WebFreeSearchTool)
        for tool in tools
    )
    assert any(
        isinstance(tool, WebFetchWebpageTool)
        for tool in tools
    )
