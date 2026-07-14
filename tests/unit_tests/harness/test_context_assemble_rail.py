# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from zoneinfo import ZoneInfo
import pytest

from openjiuwen.core.foundation.llm import (
    SystemMessage,
)
from openjiuwen.core.foundation.tool.base import ToolCard
from openjiuwen.core.runner.runner import Runner
from openjiuwen.core.single_agent.ability_manager import AbilityManager
from openjiuwen.core.single_agent.prompts.builder import PromptSection
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ModelCallInputs, RunKind
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.sys_operation import LocalWorkConfig, OperationMode, SysOperationCard
from openjiuwen.harness import Workspace, DeepAgentConfig, DeepAgent
from openjiuwen.harness.factory import create_deep_agent
from openjiuwen.harness.rails.context_engineer.context_assemble_rail import ContextAssembleRail
from openjiuwen.harness.prompts.sections.workspace import build_workspace_section
from openjiuwen.harness.prompts.sections.context import build_context_section, build_tools_content
from openjiuwen.core.foundation.llm.model import init_model


class _DummyResponse:
    def __init__(self, content: str):
        self.content = content


class _DummyModel:
    def __init__(self, content: str = ""):
        self._content = content
        self.calls = []
        self.model_client_config = None
        self.model_config = None
        self.model_request_config = None

    async def invoke(self, *args, **kwargs):
        self.calls.append({"args": args, "kwargs": kwargs})
        return _DummyResponse(self._content)


def _make_sys_operation(tmp_path: Path):
    card = SysOperationCard(
        id=f"test_context_rail_sysop_{tmp_path.name}",
        mode=OperationMode.LOCAL,
        work_config=LocalWorkConfig(work_dir=str(tmp_path)),
    )
    Runner.resource_mgr.add_sys_operation(card)
    return Runner.resource_mgr.get_sys_operation(card.id)


def _make_agent(sys_operation, workspace):
    model = init_model(
        provider="OpenAI",
        model_name="dummy-model",
        api_key="dummy-key",
        api_base="https://example.com/v1",
        verify_ssl=False,
    )
    return create_deep_agent(
        model=model,
        card=AgentCard(name="test", description="test"),
        system_prompt="You are a test assistant.",
        max_iterations=3,
        enable_task_loop=False,
        workspace=workspace,
        sys_operation=sys_operation,
    )


def _make_model_call_context(agent):
    return AgentCallbackContext(
        agent=agent,
        inputs=ModelCallInputs(
            messages=[SystemMessage(content="You are a test assistant."), {"role": "user", "content": "test"}]
        ),
        session=SimpleNamespace(get_session_id=lambda: "sess1"),
        extra={},
    )


async def _attachment(agent, item_id: str):
    return await agent.prompt_attachment_manager.get_by_id(item_id, session_id="sess1")


class _MockModelContext:
    def __init__(self, messages=None):
        self._messages = list(messages) if messages else []
        self.added_messages = []
        self.popped_messages: list = []

    def get_messages(self):
        return self._messages

    def pop_messages(self, size=None, with_history=True):
        if size is None:
            self.popped_messages = list(self._messages)
            self._messages = []
        else:
            self.popped_messages = self._messages[:size]
            self._messages = self._messages[size:]
        return self.popped_messages

    async def add_messages(self, message):
        if isinstance(message, list):
            self.added_messages.extend(message)
        else:
            self.added_messages.append(message)
        return self.added_messages


# =============================================================================
# Section Builder Tests
# =============================================================================


@pytest.mark.asyncio
async def test_build_workspace_section(tmp_path: Path):
    sys_operation = _make_sys_operation(tmp_path)
    workspace = Workspace(root_path=str(tmp_path))
    await sys_operation.fs().write_file(f"{workspace.root_path}/README.md", "# Test")

    section_cn = await build_workspace_section(sys_operation, workspace, "cn")
    content_cn = section_cn.render("cn")
    assert str(tmp_path) in content_cn
    assert content_cn
    assert section_cn.render("en")

    section_en = await build_workspace_section(sys_operation, workspace, "en")
    assert "# Workspace" in section_en.render("en")
    assert f"Your working directory is: `{tmp_path}`" in section_en.render("en")
    assert "# Workspace" in section_en.render("cn")  # fallback to en


@pytest.mark.asyncio
async def test_build_workspace_section_returns_none_when_workspace_is_none():
    assert await build_workspace_section(None, None, "cn") is None


@pytest.mark.asyncio
async def test_build_context_section(tmp_path: Path):
    sys_operation = _make_sys_operation(tmp_path)
    date = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")
    await sys_operation.fs().write_file(f"{tmp_path}/AGENT.md", "# Agent Config\nreal body")
    await sys_operation.fs().write_file(f"{tmp_path}/SOUL.md", "# Soul Content\nreal body")
    await sys_operation.fs().write_file(f"{tmp_path}/memory/daily_memory/{date}.md", "# Today")

    workspace = Workspace(root_path=str(tmp_path))
    section_cn = await build_context_section(sys_operation, workspace, "cn", timezone="Asia/Shanghai")
    assert section_cn.priority == 80
    cn_content = section_cn.render("cn")
    assert "## AGENT.md - 智能体配置" in cn_content
    assert "以下文件已加载到上下文中，无需再次读取。" in cn_content
    assert "# Agent Config" in cn_content
    assert "## SOUL.md" in cn_content
    assert "# Today" not in cn_content
    assert "## daily_memory/" not in cn_content
    assert "read_memory" in cn_content
    assert "memory_search" in cn_content
    assert "memory/daily_memory/YYYY-MM-DD.md" in cn_content
    await sys_operation.fs().write_file(f"{tmp_path}/memory/daily_memory/{date}.md", "# Changed Today")
    section_cn_after_memory_write = await build_context_section(
        sys_operation, workspace, "cn", timezone="Asia/Shanghai"
    )
    assert section_cn_after_memory_write.render("cn") == cn_content
    section_en = await build_context_section(sys_operation, workspace, "en", timezone="Asia/Shanghai")
    en_content = section_en.render("en")
    assert "## AGENT.md - Agent Configuration" in en_content
    assert "already loaded into context" in en_content
    assert "# Today" not in en_content
    assert "Daily memory is not automatically injected" in en_content
    assert "memory/daily_memory/YYYY-MM-DD.md" in en_content


@pytest.mark.asyncio
async def test_build_context_section_returns_none_when_workspace_is_none():
    assert await build_context_section(None, None, "cn") is None


@pytest.mark.asyncio
async def test_build_context_section_includes_stable_daily_memory_guidance_with_empty_dir(tmp_path: Path):
    sys_operation = _make_sys_operation(tmp_path)
    await sys_operation.fs().write_file(f"{tmp_path}/AGENT.md", "# Agent Config\nreal body")
    (tmp_path / "memory" / "daily_memory").mkdir(parents=True, exist_ok=True)

    workspace = Workspace(root_path=str(tmp_path))
    section_cn = await build_context_section(sys_operation, workspace, "cn", timezone="Asia/Shanghai")
    cn_content = section_cn.render("cn")
    assert "# Agent Config" in cn_content
    assert "## daily_memory/" not in cn_content
    assert "read_memory" in cn_content
    assert "memory_search" in cn_content


@pytest.mark.asyncio
async def test_build_context_section_never_reads_daily_memory_files(tmp_path: Path):
    sys_operation = _make_sys_operation(tmp_path)
    await sys_operation.fs().write_file(f"{tmp_path}/AGENT.md", "# Agent Config\nreal body")
    await sys_operation.fs().write_file(f"{tmp_path}/memory/daily_memory/2026-04-02.md", "# Yesterday")

    workspace = Workspace(root_path=str(tmp_path))
    section_cn = await build_context_section(sys_operation, workspace, "cn", timezone="Asia/Shanghai")
    cn_content = section_cn.render("cn")
    assert "# Agent Config" in cn_content
    assert "# Yesterday" not in cn_content
    assert "## daily_memory/" not in cn_content
    assert "memory/daily_memory/YYYY-MM-DD.md" in cn_content


@pytest.mark.asyncio
async def test_build_context_section_can_exclude_daily_memory(tmp_path: Path):
    """build_context_section can skip daily-memory guidance for lightweight runs."""
    sys_operation = _make_sys_operation(tmp_path)
    date = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")
    await sys_operation.fs().write_file(f"{tmp_path}/AGENT.md", "# Agent Config\nreal body")
    await sys_operation.fs().write_file(f"{tmp_path}/memory/daily_memory/{date}.md", "# Today")

    workspace = Workspace(root_path=str(tmp_path))
    section_cn = await build_context_section(
        sys_operation,
        workspace,
        "cn",
        timezone="Asia/Shanghai",
        include_daily_memory=False,
    )
    cn_content = section_cn.render("cn")
    assert "# Agent Config" in cn_content
    assert "# Today" not in cn_content
    assert "## daily_memory/" not in cn_content
    assert "read_memory" not in cn_content
    assert "memory_search" not in cn_content


@pytest.mark.asyncio
async def test_build_context_section_injects_filled_identity_template(tmp_path: Path):
    """IDENTITY.md with a filled name should not be skipped as an unfilled template."""
    sys_operation = _make_sys_operation(tmp_path)
    identity = """# 身份

_在你们的第一次对话中填写。让它属于你。_

- **名字：** 青团
- **形态：**
  _(AI？机器人？精灵？)_
"""
    await sys_operation.fs().write_file(f"{tmp_path}/IDENTITY.md", identity)

    workspace = Workspace(root_path=str(tmp_path))
    section = await build_context_section(sys_operation, workspace, "cn")
    content = section.render("cn")

    assert "## IDENTITY.md - 身份凭证" in content
    assert "- **名字：** 青团" in content


# =============================================================================
# build_tools_content Tests
# =============================================================================


def test_build_tools_content():
    """build_tools_content should return correct format per language."""
    mock_manager = Mock()
    mock_manager.list.return_value = [
        ToolCard(name="free_search", description="verbose desc"),
        ToolCard(name="paid_search", description="paid verbose desc"),
        ToolCard(name="read_file", description="read"),
        ToolCard(name="write_file", description="write"),
        ToolCard(name="edit_file", description="edit"),
        ToolCard(name="bash", description="run shell commands"),
        ToolCard(name="code", description="run code"),
        ToolCard(name="list_skill", description="list"),
        ToolCard(name="task_tool", description="spawn subagents"),
        ToolCard(name="cron_list_jobs", description="legacy"),
        ToolCard(name="", description="skip - no name"),
        ToolCard(name="t2", description=""),
    ]

    # None manager
    assert build_tools_content(None, "cn") is None
    # Empty manager
    assert build_tools_content(AbilityManager(), "cn") is None
    # Valid cn
    cn = build_tools_content(mock_manager, "cn")
    assert cn is not None
    assert "- paid_search:" in cn
    assert cn.index("- paid_search:") < cn.index("- free_search:")
    assert "free_search" in cn
    assert "read_file / write_file / edit_file" in cn
    assert "bash" in cn
    assert "code" in cn
    assert "list_skill" in cn
    assert "task_tool" in cn
    assert cn.index("- bash:") < cn.index("## bash")
    assert cn.index("- list_skill:") < cn.index("## task_tool")
    assert "cron_list_jobs" not in cn
    assert "t2" not in cn
    assert "skip" not in cn
    assert cn.endswith("\n")
    # Valid en
    en = build_tools_content(mock_manager, "en")
    assert en is not None
    assert "# Available Tools\n" in en
    assert "- paid_search: Paid web search (preferred when configured)" in en
    assert "- free_search: Free web search" in en
    assert en.index("- paid_search:") < en.index("- free_search:")
    assert "- read_file / write_file / edit_file: Read, write, and edit files" in en
    assert "- bash: Run shell commands" in en
    assert "- code: Run Python or JavaScript code" in en
    assert "## bash Guidelines" in en
    assert "## task_tool Guidelines" in en


@pytest.mark.asyncio
async def test_build_context_section_with_tools_content(tmp_path: Path):
    """build_context_section should include language-specific tools content."""
    sys_operation = _make_sys_operation(tmp_path)
    workspace = Workspace(root_path=str(tmp_path))

    mock_manager = Mock()
    mock_manager.list.return_value = [ToolCard(name="MyTool", description="My desc.")]
    tools_cn = build_tools_content(mock_manager, "cn")
    tools_en = build_tools_content(mock_manager, "en")

    section_cn = await build_context_section(
        sys_operation,
        workspace,
        "cn",
        tools_content=tools_cn,
        timezone="Asia/Shanghai",
    )
    assert "# 可用工具" in section_cn.render("cn")
    assert "MyTool" in section_cn.render("cn")

    section_en = await build_context_section(
        sys_operation,
        workspace,
        "en",
        tools_content=tools_en,
        timezone="Asia/Shanghai",
    )
    assert "# Available Tools" in section_en.render("en")
    assert "MyTool" in section_en.render("en")


@pytest.mark.asyncio
async def test_build_context_section_without_tools(tmp_path: Path):
    """build_context_section without tools_content should not include tools section."""
    sys_operation = _make_sys_operation(tmp_path)
    await sys_operation.fs().write_file(f"{tmp_path}/AGENT.md", "# AGENT\nreal body")
    workspace = Workspace(root_path=str(tmp_path))
    section = await build_context_section(
        sys_operation,
        workspace,
        "cn",
        tools_content=None,
        timezone="Asia/Shanghai",
    )
    content = section.render("cn")
    assert "## AGENT.md" in content
    assert "# 可用工具" not in content
    assert "# Available Tools" not in content


# =============================================================================
# before_model_call Integration Tests
# =============================================================================


@pytest.mark.asyncio
async def test_before_model_call_injects_sections(tmp_path: Path):
    """before_model_call should inject workspace and context sections."""
    sys_operation = _make_sys_operation(tmp_path)
    card = AgentCard(name="test", description="test")
    workspace = Workspace(root_path=str(tmp_path))
    agent = DeepAgent(card)
    agent.configure(
        DeepAgentConfig(
            model=_DummyModel(),
            workspace=workspace,
            sys_operation=sys_operation,
            auto_create_workspace=True,
            enable_task_loop=False,
        )
    )
    await agent.ensure_initialized()

    ctx = _make_model_call_context(agent)
    rail = ContextAssembleRail()
    await agent.register_rail(rail)
    await rail.before_invoke(ctx)
    await rail.before_model_call(ctx)

    builder = agent.system_prompt_builder
    ws = builder.get_section("workspace")
    agent_section = builder.get_section("context.agent")
    soul_section = builder.get_section("context.soul")
    assert ws is not None
    assert agent_section is not None
    assert soul_section is not None
    assert not builder.has_section("context")
    assert "# 工作空间" in ws.render("cn")
    assert "## AGENT.md" in agent_section.render("cn")
    assert "## SOUL.md" in soul_section.render("cn")
    assert await _attachment(agent, "session.sess1.context") is None
    assert await _attachment(agent, "session.sess1.context.daily_memory") is None


@pytest.mark.asyncio
async def test_before_model_call_heartbeat_uses_lightweight_context(tmp_path: Path):
    """Heartbeat runs keep HEARTBEAT.md context but skip daily memory."""
    sys_operation = _make_sys_operation(tmp_path)
    date = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")
    await sys_operation.fs().write_file(f"{tmp_path}/AGENT.md", "# Agent Config\nreal body")
    await sys_operation.fs().write_file(f"{tmp_path}/SOUL.md", "# Soul Content\nreal body")
    await sys_operation.fs().write_file(f"{tmp_path}/HEARTBEAT.md", "# Heartbeat Tasks\nreal body")
    await sys_operation.fs().write_file(f"{tmp_path}/memory/daily_memory/{date}.md", "# Today")

    workspace = Workspace(root_path=str(tmp_path))
    agent = _make_agent(sys_operation, workspace)
    await agent.ensure_initialized()

    ctx = _make_model_call_context(agent)
    ctx.extra["run_kind"] = RunKind.HEARTBEAT
    rail = ContextAssembleRail()
    await agent.register_rail(rail)
    await rail.before_invoke(ctx)
    await rail.before_model_call(ctx)

    builder = agent.system_prompt_builder
    heartbeat_section = await _attachment(agent, "session.sess1.context.heartbeat")
    daily_section = await _attachment(agent, "session.sess1.context.daily_memory")
    assert builder.get_section("workspace") is not None
    assert builder.get_section("context.agent") is not None
    assert builder.get_section("context.soul") is not None
    assert heartbeat_section is not None
    assert not builder.has_section("context")
    assert "# Agent Config" in builder.get_section("context.agent").render("cn")
    assert "# Soul Content" in builder.get_section("context.soul").render("cn")
    assert "# Heartbeat Tasks" in (heartbeat_section.content or "")
    assert not builder.has_section("context.daily_memory")
    assert daily_section is None


@pytest.mark.asyncio
async def test_before_model_call_keeps_user_in_system_and_skips_daily_memory(tmp_path: Path):
    """USER.md should be system context; daily memory should not be injected."""
    sys_operation = _make_sys_operation(tmp_path)
    date = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")
    await sys_operation.fs().write_file(f"{tmp_path}/USER.md", "# User Profile\nreal body")
    await sys_operation.fs().write_file(f"{tmp_path}/memory/daily_memory/{date}.md", "# Today")

    workspace = Workspace(root_path=str(tmp_path))
    agent = _make_agent(sys_operation, workspace)
    await agent.ensure_initialized()

    ctx = _make_model_call_context(agent)
    rail = ContextAssembleRail()
    await agent.register_rail(rail)
    await rail.before_invoke(ctx)
    await rail.before_model_call(ctx)

    builder = agent.system_prompt_builder
    user_section = builder.get_section("context.user")
    assert user_section is not None
    assert "# User Profile" in user_section.render("cn")
    assert not builder.has_section("context.daily_memory")
    assert await _attachment(agent, "session.sess1.context.daily_memory") is None


@pytest.mark.asyncio
async def test_before_model_call_injects_filled_identity_in_system(tmp_path: Path):
    """Filled IDENTITY.md should be injected as system context every model call."""
    sys_operation = _make_sys_operation(tmp_path)
    await sys_operation.fs().write_file(
        f"{tmp_path}/IDENTITY.md",
        "# 身份\n\n_在你们的第一次对话中填写。让它属于你。_\n\n- **名字：** 青团\n",
    )

    workspace = Workspace(root_path=str(tmp_path))
    agent = _make_agent(sys_operation, workspace)
    await agent.ensure_initialized()

    ctx = _make_model_call_context(agent)
    rail = ContextAssembleRail()
    await agent.register_rail(rail)
    await rail.before_invoke(ctx)
    await rail.before_model_call(ctx)

    identity_section = agent.system_prompt_builder.get_section("context.identity")
    assert identity_section is not None
    assert "- **名字：** 青团" in identity_section.render("cn")


@pytest.mark.asyncio
async def test_before_model_call_normal_turn_replaces_heartbeat_context_attachment(tmp_path: Path):
    """Normal turns should replace context written by heartbeat runs."""
    sys_operation = _make_sys_operation(tmp_path)
    await sys_operation.fs().write_file(f"{tmp_path}/AGENT.md", "# Agent Config\nreal body")
    await sys_operation.fs().write_file(f"{tmp_path}/HEARTBEAT.md", "# Heartbeat Tasks\nreal body")

    workspace = Workspace(root_path=str(tmp_path))
    agent = _make_agent(sys_operation, workspace)
    await agent.ensure_initialized()

    rail = ContextAssembleRail()
    await agent.register_rail(rail)

    heartbeat_ctx = _make_model_call_context(agent)
    heartbeat_ctx.extra["run_kind"] = RunKind.HEARTBEAT
    await rail.before_invoke(heartbeat_ctx)
    await rail.before_model_call(heartbeat_ctx)
    heartbeat_attachment = await _attachment(agent, "session.sess1.context.heartbeat")
    assert heartbeat_attachment is not None
    assert "# Heartbeat Tasks" in (heartbeat_attachment.content or "")
    assert await _attachment(agent, "session.sess1.context") is None

    normal_ctx = _make_model_call_context(agent)
    await rail.before_invoke(normal_ctx)
    await rail.before_model_call(normal_ctx)

    normal_attachment = await _attachment(agent, "session.sess1.context.heartbeat")
    assert normal_attachment is not None
    assert "# Heartbeat Tasks" in (normal_attachment.content or "")
    assert "# Agent Config" in agent.system_prompt_builder.get_section("context.agent").render("cn")


@pytest.mark.asyncio
async def test_before_model_call_removes_sections_when_workspace_is_none(tmp_path: Path):
    """before_model_call should remove split sections when workspace is None."""
    sys_operation = _make_sys_operation(tmp_path)
    workspace = Workspace(root_path=str(tmp_path))
    agent = _make_agent(sys_operation, workspace)
    await agent.ensure_initialized()

    ctx = _make_model_call_context(agent)
    rail = ContextAssembleRail()
    await agent.register_rail(rail)
    await rail.before_invoke(ctx)
    await rail.before_model_call(ctx)

    builder = agent.system_prompt_builder
    assert builder.has_section("workspace")
    assert builder.has_section("context.agent")

    rail.workspace = None
    await rail.before_model_call(ctx)
    assert not builder.has_section("workspace")
    assert not builder.has_section("context")
    assert not builder.has_section("context.agent")
    assert not builder.has_section("context.soul")
    assert not builder.has_section("context.identity")
    assert not builder.has_section("context.user")
    assert await _attachment(agent, "session.sess1.context") is None
    assert await _attachment(agent, "session.sess1.context.heartbeat") is None


@pytest.mark.asyncio
async def test_uninit_removes_sections(tmp_path: Path):
    """uninit should remove workspace and context sections."""
    sys_operation = _make_sys_operation(tmp_path)
    workspace = Workspace(root_path=str(tmp_path))
    agent = _make_agent(sys_operation, workspace)
    await agent.ensure_initialized()

    builder = agent.system_prompt_builder
    builder.add_section(await build_workspace_section(sys_operation, workspace, "cn"))
    builder.add_section(await build_context_section(sys_operation, workspace, "cn", timezone="Asia/Shanghai"))
    builder.add_section(PromptSection("context.user", {"cn": "user"}))
    assert builder.has_section("workspace")
    assert builder.has_section("context")
    assert builder.has_section("context.user")

    rail = ContextAssembleRail()
    await agent.register_rail(rail)
    rail.uninit(agent)
    assert not builder.has_section("workspace")
    assert not builder.has_section("context")
    assert not builder.has_section("context.user")


# =============================================================================
# ContextAssembleRail Unit Tests Extension
# =============================================================================


@pytest.mark.asyncio
async def test_rail_init_captures_system_prompt_builder(tmp_path: Path):
    """init should capture system_prompt_builder from agent."""
    sys_operation = _make_sys_operation(tmp_path)
    workspace = Workspace(root_path=str(tmp_path))
    agent = _make_agent(sys_operation, workspace)
    await agent.ensure_initialized()

    rail = ContextAssembleRail()
    assert rail.system_prompt_builder is None
    assert rail._ability_manager is None

    await agent.register_rail(rail)
    rail.init(agent)

    assert rail.system_prompt_builder is agent.system_prompt_builder
    assert rail._ability_manager is agent.ability_manager


@pytest.mark.asyncio
async def test_rail_init_with_missing_attributes(tmp_path: Path):
    """init should handle agent without system_prompt_builder or ability_manager."""
    sys_operation = _make_sys_operation(tmp_path)
    card = AgentCard(name="test", description="test")
    agent = DeepAgent(card)
    agent.configure(
        DeepAgentConfig(
            model=_DummyModel(),
            workspace=None,
            sys_operation=sys_operation,
            enable_task_loop=False,
        )
    )
    await agent.ensure_initialized()

    # Simulate missing attributes after initialization.
    del agent.system_prompt_builder
    if hasattr(agent, "_ability_manager"):
        del agent._ability_manager

    rail = ContextAssembleRail()
    rail.init(agent)

    assert rail.system_prompt_builder is None
    assert rail._ability_manager is None


def test_rail_priority():
    """Rail should have correct priority value."""
    rail = ContextAssembleRail()
    assert rail.priority == 85


@pytest.mark.asyncio
async def test_before_model_call_returns_early_when_builder_is_none(tmp_path: Path):
    """before_model_call should return early when system_prompt_builder is None."""
    sys_operation = _make_sys_operation(tmp_path)
    workspace = Workspace(root_path=str(tmp_path))
    agent = _make_agent(sys_operation, workspace)
    await agent.ensure_initialized()

    ctx = _make_model_call_context(agent)
    rail = ContextAssembleRail()
    await agent.register_rail(rail)

    rail.system_prompt_builder = None
    await rail.before_model_call(ctx)


@pytest.mark.asyncio
async def test_before_model_call_with_empty_workspace(tmp_path: Path):
    """before_model_call should handle empty workspace directory."""
    sys_operation = _make_sys_operation(tmp_path)
    workspace = Workspace(root_path=str(tmp_path))
    agent = _make_agent(sys_operation, workspace)
    await agent.ensure_initialized()

    ctx = _make_model_call_context(agent)
    rail = ContextAssembleRail()
    await agent.register_rail(rail)
    await rail.before_invoke(ctx)
    await rail.before_model_call(ctx)

    builder = agent.system_prompt_builder
    ws = builder.get_section("workspace")
    assert ws is not None
    assert builder.has_section("workspace")


@pytest.mark.asyncio
async def test_before_model_call_with_only_readme(tmp_path: Path):
    """before_model_call should include workspace section when README exists."""
    sys_operation = _make_sys_operation(tmp_path)
    workspace = Workspace(root_path=str(tmp_path))
    await sys_operation.fs().write_file(f"{workspace.root_path}/README.md", "# Test Project")

    agent = _make_agent(sys_operation, workspace)
    await agent.ensure_initialized()

    ctx = _make_model_call_context(agent)
    rail = ContextAssembleRail()
    await agent.register_rail(rail)
    await rail.before_invoke(ctx)
    await rail.before_model_call(ctx)

    builder = agent.system_prompt_builder
    ws = builder.get_section("workspace")
    assert ws is not None
    assert builder.has_section("workspace")


@pytest.mark.asyncio
async def test_before_model_call_adds_tools_section(tmp_path: Path):
    """before_model_call should add tools section when ability_manager has tools."""
    sys_operation = _make_sys_operation(tmp_path)
    workspace = Workspace(root_path=str(tmp_path))
    agent = _make_agent(sys_operation, workspace)
    await agent.ensure_initialized()

    agent.ability_manager.add(ToolCard(id="test-tool-1", name="test_tool", description="A test tool"))

    ctx = _make_model_call_context(agent)
    rail = ContextAssembleRail()
    await agent.register_rail(rail)
    await rail.before_invoke(ctx)
    await rail.before_model_call(ctx)

    builder = agent.system_prompt_builder
    tools_section = builder.get_section("tools")
    assert tools_section is not None
    assert "test_tool" in tools_section.render("cn")


@pytest.mark.asyncio
async def test_before_model_call_removes_tools_when_ability_manager_empty(tmp_path: Path):
    """before_model_call should remove tools section when ability_manager has no tools."""
    sys_operation = _make_sys_operation(tmp_path)
    workspace = Workspace(root_path=str(tmp_path))
    agent = _make_agent(sys_operation, workspace)
    await agent.ensure_initialized()

    empty_manager = AbilityManager()
    agent.ability_manager = empty_manager

    ctx = _make_model_call_context(agent)
    rail = ContextAssembleRail()
    await agent.register_rail(rail)
    rail._ability_manager = empty_manager
    await rail.before_invoke(ctx)
    await rail.before_model_call(ctx)

    builder = agent.system_prompt_builder
    assert not builder.has_section("tools")


@pytest.mark.asyncio
async def test_uninit_handles_none_builder(tmp_path: Path):
    """uninit should handle None system_prompt_builder safely."""
    sys_operation = _make_sys_operation(tmp_path)
    workspace = Workspace(root_path=str(tmp_path))
    agent = _make_agent(sys_operation, workspace)
    await agent.ensure_initialized()

    rail = ContextAssembleRail()
    rail.system_prompt_builder = None
    rail._ability_manager = None

    rail.uninit(agent)


@pytest.mark.asyncio
async def test_before_model_call_with_chinese_language(tmp_path: Path):
    """before_model_call should use Chinese language for section rendering."""
    sys_operation = _make_sys_operation(tmp_path)
    workspace = Workspace(root_path=str(tmp_path))
    await sys_operation.fs().write_file(f"{workspace.root_path}/README.md", "# Test")

    agent = _make_agent(sys_operation, workspace)
    agent.system_prompt_builder.language = "cn"
    await agent.ensure_initialized()

    ctx = _make_model_call_context(agent)
    rail = ContextAssembleRail()
    await agent.register_rail(rail)
    await rail.before_invoke(ctx)
    await rail.before_model_call(ctx)

    builder = agent.system_prompt_builder
    ws = builder.get_section("workspace")
    assert ws is not None
    assert builder.has_section("workspace")
    assert str(tmp_path) in ws.render("cn")


@pytest.mark.asyncio
async def test_before_model_call_with_english_language(tmp_path: Path):
    """before_model_call should use English language for section rendering."""
    sys_operation = _make_sys_operation(tmp_path)
    workspace = Workspace(root_path=str(tmp_path))
    await sys_operation.fs().write_file(f"{workspace.root_path}/README.md", "# Test")

    agent = _make_agent(sys_operation, workspace)
    agent.system_prompt_builder.language = "en"
    await agent.ensure_initialized()

    ctx = _make_model_call_context(agent)
    rail = ContextAssembleRail()
    await agent.register_rail(rail)
    await rail.before_invoke(ctx)
    await rail.before_model_call(ctx)

    builder = agent.system_prompt_builder
    ws = builder.get_section("workspace")
    assert ws is not None
    assert builder.has_section("workspace")
    assert "# Workspace" in ws.render("en")
