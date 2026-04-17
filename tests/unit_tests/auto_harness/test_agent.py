# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""test_agent — auto-harness agent 工厂测试。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from openjiuwen.auto_harness.agents import (
    create_assess_agent,
    create_auto_harness_agent,
    create_commit_agent,
    create_learnings_agent,
    create_plan_agent,
    create_select_pipeline_agent,
)
from openjiuwen.auto_harness.rails.context_rail import (
    AutoHarnessContextRail,
)
from openjiuwen.auto_harness.schema import (
    AutoHarnessConfig,
)
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
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
        "openjiuwen.auto_harness.agents.factory.create_deep_agent",
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
        Path(path).name == "skills"
        for path in skill_rails[0].skills_dir
    )
    assert set(skill_rails[0].enabled_skills) == {
        "implement",
        "verify",
        "communicate",
    }
    assert "commit" not in skill_rails[0].enabled_skills
    assert "evolve" not in skill_rails[0].enabled_skills
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
        "openjiuwen.auto_harness.agents.factory.create_deep_agent",
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
    subagents = captured["subagents"]
    for spec in subagents:
        assert spec.workspace == "/repo/worktrees/task-1"


def test_create_commit_agent_only_exposes_commit_skills():
    """提交阶段 agent 只应挂载 commit/communicate skills。"""
    captured = {}

    def _fake_create_deep_agent(**kwargs):
        captured.update(kwargs)
        return object()

    with patch(
        "openjiuwen.auto_harness.agents.factory.create_deep_agent",
        side_effect=_fake_create_deep_agent,
    ):
        create_commit_agent(
            AutoHarnessConfig(
                model=MagicMock(),
                workspace="/repo/default",
            ),
            workspace_override="/repo/worktrees/task-1",
        )

    skill_rails = [
        rail for rail in captured["rails"]
        if isinstance(rail, SkillUseRail)
    ]
    assert len(skill_rails) == 1
    assert any(
        Path(path).name == "skills"
        for path in skill_rails[0].skills_dir
    )
    assert set(skill_rails[0].enabled_skills) == {
        "commit",
        "communicate",
    }
    assert "implement" not in skill_rails[0].enabled_skills


@pytest.mark.asyncio
async def test_create_commit_agent_loads_commit_skill(tmp_path: Path):
    """提交阶段 agent 应实际加载到 commit/communicate skills。"""
    agent = create_commit_agent(
        AutoHarnessConfig(
            model=MagicMock(),
            workspace=str(tmp_path),
        ),
        workspace_override=str(tmp_path / "task-1"),
    )

    await agent.ensure_initialized()

    skill_rails = [
        rail for rail in agent._registered_rails
        if isinstance(rail, SkillUseRail)
    ]
    assert len(skill_rails) == 1

    ctx = AgentCallbackContext(
        agent=agent,
        inputs=None,
        session=None,
    )
    await skill_rails[0].before_invoke(ctx)

    assert {skill.name for skill in skill_rails[0].skills} == {
        "commit",
        "communicate",
    }


def test_create_assess_agent_includes_tool_tracker():
    """只读阶段 agent 也应挂载 ToolTrackingRail。"""
    captured = {}

    def _fake_create_deep_agent(**kwargs):
        captured.update(kwargs)
        return object()

    with patch(
        "openjiuwen.auto_harness.agents.factory.create_deep_agent",
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
        "openjiuwen.auto_harness.agents.factory.create_deep_agent",
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


def test_create_learnings_agent_formats_prompt_without_tools():
    """反思阶段应注入 prompt 上下文，且不依赖缺失的 memory tool。"""
    captured = {}

    def _fake_create_deep_agent(**kwargs):
        captured.update(kwargs)
        return object()

    with patch(
        "openjiuwen.auto_harness.agents.factory.create_deep_agent",
        side_effect=_fake_create_deep_agent,
    ):
        create_learnings_agent(
            AutoHarnessConfig(model=MagicMock()),
            session_results="- task-1 (success=True, reverted=False)",
            existing_memories="- [insight] topic: summary",
        )

    assert captured.get("tools") is None
    assert "{session_results}" not in captured["system_prompt"]
    assert "{existing_memories}" not in captured["system_prompt"]
    assert "task-1" in captured["system_prompt"]
    assert "topic: summary" in captured["system_prompt"]


def test_create_plan_agent_uses_plan_skill():
    """规划阶段应挂载 plan skill，而不是 assess skill。"""
    captured = {}

    def _fake_create_deep_agent(**kwargs):
        captured.update(kwargs)
        return object()

    with patch(
        "openjiuwen.auto_harness.agents.factory.create_deep_agent",
        side_effect=_fake_create_deep_agent,
    ):
        create_plan_agent(
            AutoHarnessConfig(model=MagicMock()),
        )

    skill_rails = [
        rail for rail in captured["rails"]
        if isinstance(rail, SkillUseRail)
    ]
    assert len(skill_rails) == 1
    assert any(
        Path(path).name == "skills"
        for path in skill_rails[0].skills_dir
    )
    assert "plan" in skill_rails[0].enabled_skills


def test_create_select_pipeline_agent_uses_selector_skill():
    """selector agent 应挂载 select_pipeline skill。"""
    captured = {}

    def _fake_create_deep_agent(**kwargs):
        captured.update(kwargs)
        return object()

    with patch(
        "openjiuwen.auto_harness.agents.factory.create_deep_agent",
        side_effect=_fake_create_deep_agent,
    ):
        create_select_pipeline_agent(
            AutoHarnessConfig(model=MagicMock()),
        )

    skill_rails = [
        rail for rail in captured["rails"]
        if isinstance(rail, SkillUseRail)
    ]
    assert len(skill_rails) == 1
    assert any(
        Path(path).name == "skills"
        for path in skill_rails[0].skills_dir
    )
    assert "select_pipeline" in skill_rails[0].enabled_skills
