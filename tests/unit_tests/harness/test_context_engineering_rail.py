# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import Mock
from zoneinfo import ZoneInfo
import pytest

from openjiuwen.core.context_engine import MessageSummaryOffloaderConfig, CurrentRoundCompressorConfig, \
    RoundLevelCompressorConfig
from openjiuwen.core.context_engine.processor.compressor.dialogue_compressor import (
    DialogueCompressorConfig,
)
from openjiuwen.core.foundation.llm import (
    SystemMessage,
    AssistantMessage,
    ToolMessage,
    UserMessage,
)
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.foundation.tool.base import ToolCard
from openjiuwen.core.runner.runner import Runner
from openjiuwen.core.single_agent.ability_manager import AbilityManager
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ModelCallInputs
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.sys_operation import LocalWorkConfig, OperationMode, SysOperationCard
from openjiuwen.harness import Workspace, DeepAgentConfig, DeepAgent
from openjiuwen.harness.factory import create_deep_agent
from openjiuwen.harness.rails.context_engineering_rail import ContextEngineeringRail
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
        provider="OpenAI", model_name="dummy-model", api_key="dummy-key",
        api_base="https://example.com/v1", verify_ssl=False,
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
            messages=[
                SystemMessage(content="You are a test assistant."),
                {"role": "user", "content": "test"}
            ]
        ),
        session=None,
    )


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
# Preset Processor Tests
# =============================================================================

@pytest.mark.asyncio
async def test_init_processors_merge(tmp_path: Path):
    """init should merge preset and custom processors correctly."""
    cases = [
        # (preset, processors, expected_keys)
        (False, None, []),
        (False, [("custom", DialogueCompressorConfig(messages_threshold=25))], ["custom"]),
        (False, [("d", DialogueCompressorConfig(messages_to_keep=5))], ["d"]),
        (True, None, ["MessageSummaryOffloader", "DialogueCompressor", "CurrentRoundCompressor", "RoundLevelCompressor"]),
        (True, [("d", DialogueCompressorConfig(messages_threshold=99))],
         ["MessageSummaryOffloader", "DialogueCompressor", "CurrentRoundCompressor", "RoundLevelCompressor", "d"]),
        (True, [("c", DialogueCompressorConfig(messages_to_keep=5))],
         ["MessageSummaryOffloader", "DialogueCompressor", "CurrentRoundCompressor", "RoundLevelCompressor", "c"]),
        (True, [("DialogueCompressor", DialogueCompressorConfig(messages_threshold=99))],
         ["MessageSummaryOffloader", "DialogueCompressor", "CurrentRoundCompressor", "RoundLevelCompressor"]),
    ]
    for preset, processors, expected_keys in cases:
        sys_operation = _make_sys_operation(tmp_path)
        workspace = Workspace(root_path=str(tmp_path))
        agent = _make_agent(sys_operation, workspace)
        rail = ContextEngineeringRail(preset=preset, processors=processors)
        await agent.register_rail(rail)
        await agent.ensure_initialized()
        keys = [k for k, _ in agent.react_config.context_processors or []]
        assert keys == expected_keys, f"preset={preset}, processors={processors}"


@pytest.mark.asyncio
async def test_init_preset_defaults(tmp_path: Path):
    """Preset processors should have correct default config values."""
    sys_operation = _make_sys_operation(tmp_path)
    workspace = Workspace(root_path=str(tmp_path))
    agent = _make_agent(sys_operation, workspace)
    rail = ContextEngineeringRail(preset=True)
    await agent.register_rail(rail)
    await agent.ensure_initialized()

    procs = dict(agent.react_config.context_processors)

    # MessageSummaryOffloader tests
    off = procs.get("MessageSummaryOffloader")
    assert off is not None
    assert off.messages_threshold is None
    assert off.tokens_threshold == 60000
    assert off.large_message_threshold == 20000
    assert off.offload_message_type == ["tool"]
    assert off.protected_tool_names == ["view_file", "reload_original_context_messages"]
    assert off.enable_adaptive_compression is True
    assert off.summary_max_tokens == 900

    # DialogueCompressor tests
    comp = procs.get("DialogueCompressor")
    assert comp is not None
    assert comp.messages_threshold is None
    assert comp.tokens_threshold == 100000
    assert comp.messages_to_keep == 10
    assert comp.keep_last_round is False
    assert comp.compression_target_tokens == 1800

    # CurrentRoundCompressor tests
    curr = procs.get("CurrentRoundCompressor")
    assert curr is not None
    assert curr.tokens_threshold == 100000
    assert curr.messages_to_keep == 6
    assert curr.compression_target_tokens == 4000

    # RoundLevelCompressor tests
    round_lvl = procs.get("RoundLevelCompressor")
    assert round_lvl is not None
    assert round_lvl.rounds_threshold == 2
    assert round_lvl.tokens_threshold == 230000
    assert round_lvl.target_total_tokens == 160000
    assert round_lvl.keep_last_round is True


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
    assert "## 工作空间" in content_cn
    assert "## 工作空间" in section_cn.render("en")  # fallback to cn

    section_en = await build_workspace_section(sys_operation, workspace, "en")
    assert "## Workspace" in section_en.render("en")
    assert "## Workspace" in section_en.render("cn")  # fallback to en


@pytest.mark.asyncio
async def test_build_workspace_section_returns_none_when_workspace_is_none():
    assert await build_workspace_section(None, None, "cn") is None


@pytest.mark.asyncio
async def test_build_context_section(tmp_path: Path):
    sys_operation = _make_sys_operation(tmp_path)
    date = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")
    await sys_operation.fs().write_file(f"{tmp_path}/AGENT.md", "# Agent Config")
    await sys_operation.fs().write_file(f"{tmp_path}/SOUL.md", "# Soul Content")
    await sys_operation.fs().write_file(f"{tmp_path}/memory/daily_memory/{date}.md", "# Today")

    workspace = Workspace(root_path=str(tmp_path))
    section_cn = await build_context_section(
        sys_operation, workspace, "cn", timezone="Asia/Shanghai"
    )
    assert section_cn.priority == 96
    cn_content = section_cn.render("cn")
    assert "## AGENT.md - 智能体配置" in cn_content
    assert "### 文件内容" in cn_content
    assert "# Agent Config" in cn_content
    assert "## SOUL.md" in cn_content
    assert "## daily_memory/" in cn_content
    section_en = await build_context_section(
        sys_operation, workspace, "en", timezone="Asia/Shanghai"
    )
    en_content = section_en.render("en")
    assert "## AGENT.md - Agent Configuration" in en_content
    assert "### File Contents" in en_content


@pytest.mark.asyncio
async def test_build_context_section_returns_none_when_workspace_is_none():
    assert await build_context_section(None, None, "cn") is None


@pytest.mark.asyncio
async def test_build_context_section_skips_empty_daily_memory_dir(tmp_path: Path):
    sys_operation = _make_sys_operation(tmp_path)
    await sys_operation.fs().write_file(f"{tmp_path}/AGENT.md", "# Agent Config")
    (tmp_path / "memory" / "daily_memory").mkdir(parents=True, exist_ok=True)

    workspace = Workspace(root_path=str(tmp_path))
    section_cn = await build_context_section(
        sys_operation, workspace, "cn", timezone="Asia/Shanghai"
    )
    cn_content = section_cn.render("cn")
    assert "# Agent Config" in cn_content
    assert "## daily_memory/" not in cn_content


@pytest.mark.asyncio
async def test_build_context_section_skips_when_today_daily_memory_missing(tmp_path: Path):
    sys_operation = _make_sys_operation(tmp_path)
    await sys_operation.fs().write_file(f"{tmp_path}/AGENT.md", "# Agent Config")
    await sys_operation.fs().write_file(f"{tmp_path}/memory/daily_memory/2026-04-02.md", "# Yesterday")

    workspace = Workspace(root_path=str(tmp_path))
    section_cn = await build_context_section(
        sys_operation, workspace, "cn", timezone="Asia/Shanghai"
    )
    cn_content = section_cn.render("cn")
    assert "# Agent Config" in cn_content
    assert "# Yesterday" not in cn_content
    assert "## daily_memory/" not in cn_content


# =============================================================================
# build_tools_content Tests
# =============================================================================

def test_build_tools_content():
    """build_tools_content should return correct format per language."""
    mock_manager = Mock()
    mock_manager.list.return_value = [
        ToolCard(name="t1", description="d1"),
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
    assert "## 可用工具\n" in cn
    assert "**t1**" in cn
    assert "**t2**" not in cn
    assert "skip" not in cn
    assert cn.endswith("\n")
    # Valid en
    en = build_tools_content(mock_manager, "en")
    assert en is not None
    assert "## Available Tools\n" in en


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
    assert "## 可用工具" in section_cn.render("cn")
    assert "**MyTool**" in section_cn.render("cn")

    section_en = await build_context_section(
        sys_operation,
        workspace,
        "en",
        tools_content=tools_en,
        timezone="Asia/Shanghai",
    )
    assert "## Available Tools" in section_en.render("en")
    assert "**MyTool**" in section_en.render("en")


@pytest.mark.asyncio
async def test_build_context_section_without_tools(tmp_path: Path):
    """build_context_section without tools_content should not include tools section."""
    sys_operation = _make_sys_operation(tmp_path)
    await sys_operation.fs().write_file(f"{tmp_path}/AGENT.md", "# AGENT")
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
    assert "## 可用工具" not in content
    assert "## Available Tools" not in content


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
    agent.configure(DeepAgentConfig(
        model=_DummyModel(),
        workspace=workspace,
        sys_operation=sys_operation,
        auto_create_workspace=True,
        enable_task_loop=False,
    ))
    await agent.ensure_initialized()

    ctx = _make_model_call_context(agent)
    rail = ContextEngineeringRail()
    await agent.register_rail(rail)
    await rail.before_invoke(ctx)
    await rail.before_model_call(ctx)

    builder = agent.system_prompt_builder
    ws = builder.get_section("workspace")
    ctx_section = builder.get_section("context")
    assert ws is not None
    assert ctx_section is not None
    assert "## 工作空间" in ws.render("cn")
    assert "## AGENT.md" in ctx_section.render("cn")
    assert "## 可用工具" not in ctx_section.render("cn")  # no tools


@pytest.mark.asyncio
async def test_before_model_call_removes_sections_when_workspace_is_none(tmp_path: Path):
    """before_model_call should remove sections when workspace is None."""
    sys_operation = _make_sys_operation(tmp_path)
    workspace = Workspace(root_path=str(tmp_path))
    agent = _make_agent(sys_operation, workspace)
    await agent.ensure_initialized()

    ctx = _make_model_call_context(agent)
    rail = ContextEngineeringRail()
    await agent.register_rail(rail)
    await rail.before_invoke(ctx)
    await rail.before_model_call(ctx)

    builder = agent.system_prompt_builder
    assert builder.has_section("workspace")
    assert builder.has_section("context")

    rail.workspace = None
    await rail.before_model_call(ctx)
    assert not builder.has_section("workspace")
    assert not builder.has_section("context")


@pytest.mark.asyncio
async def test_uninit_removes_sections(tmp_path: Path):
    """uninit should remove workspace and context sections."""
    sys_operation = _make_sys_operation(tmp_path)
    workspace = Workspace(root_path=str(tmp_path))
    agent = _make_agent(sys_operation, workspace)
    await agent.ensure_initialized()

    builder = agent.system_prompt_builder
    builder.add_section(await build_workspace_section(
        sys_operation, workspace, "cn"))
    builder.add_section(await build_context_section(
        sys_operation, workspace, "cn", timezone="Asia/Shanghai"))
    assert builder.has_section("workspace")
    assert builder.has_section("context")

    rail = ContextEngineeringRail()
    await agent.register_rail(rail)
    rail.uninit(agent)
    assert not builder.has_section("workspace")
    assert not builder.has_section("context")


# =============================================================================
# fix_incomplete_tool_context Tests
# =============================================================================

def _make_fix_ctx(agent, messages):
    return AgentCallbackContext(
        agent=agent,
        inputs=ModelCallInputs(messages=[]),
        session=None,
        context=_MockModelContext(messages=messages),
    )


@pytest.mark.asyncio
async def test_fix_incomplete_tool_context(tmp_path: Path):
    """fix_incomplete_tool_context should fill missing ToolMessages."""
    cases = [
        # (messages, expected_added_count, expected_placeholder_ids)
        ([], 0, []),
        ([SystemMessage(content="sys"), UserMessage(content="user")], 2, []),
        ([AssistantMessage(content="no tools")], 1, []),
        ([UserMessage(content="user")], 1, []),
        ([AssistantMessage(content="call",
            tool_calls=[ToolCall(id="tc1", type="function", name="t", arguments="{}")
        ])], 2, ["tc1"]),
        ([AssistantMessage(content="call",
            tool_calls=[ToolCall(id="", type="function", name="t", arguments="{}")])], 2, [""]),
        (
            [
                AssistantMessage(content="c1",
                    tool_calls=[ToolCall(id="a", type="function", name="t", arguments="{}")]),
                UserMessage(content="user"),
            ],
            3,
            ["a"],
        ),
        (
            [
                AssistantMessage(content="c1",
                    tool_calls=[ToolCall(id="a", type="function", name="t1", arguments="{}")]),
                AssistantMessage(content="c2",
                    tool_calls=[ToolCall(id="b", type="function", name="t2", arguments="{}")]),
            ],
            4,
            ["a", "b"],
        ),
        (
            [
                AssistantMessage(content="call", tool_calls=[
                    ToolCall(id="x", type="function", name="t1", arguments="{}"),
                    ToolCall(id="y", type="function", name="t2", arguments="{}"),
                ]),
                ToolMessage(content="res", tool_call_id="x"),
            ],
            3,
            ["y"],
        ),
        (
            [
                ToolMessage(content="res", tool_call_id="old"),
                AssistantMessage(
                    content="call",
                    tool_calls=[ToolCall(id="new", type="function", name="t", arguments="{}")]),
            ],
            3,
            ["new"],
        ),
    ]

    for i, (msgs, exp_count, exp_ids) in enumerate(cases):
        sys_operation = _make_sys_operation(tmp_path)
        workspace = Workspace(root_path=str(tmp_path))
        agent = _make_agent(sys_operation, workspace)
        await agent.ensure_initialized()

        ctx = _make_fix_ctx(agent, msgs)
        rail = ContextEngineeringRail()
        await rail.fix_incomplete_tool_context(ctx)

        added = ctx.context.added_messages
        assert len(added) == exp_count, f"case {i}: expected {exp_count}, got {len(added)}"

        placeholders = [m.tool_call_id for m in added
                        if isinstance(m, ToolMessage) and "[工具执行被中断]" in m.content]
        assert (sorted(placeholders) == sorted(exp_ids)), \
            f"case {i}: expected ids {exp_ids}, got {placeholders}"


@pytest.mark.asyncio
async def test_fix_incomplete_tool_context_null_context(tmp_path: Path):
    """fix_incomplete_tool_context should not crash when context is None."""
    sys_operation = _make_sys_operation(tmp_path)
    workspace = Workspace(root_path=str(tmp_path))
    agent = _make_agent(sys_operation, workspace)
    await agent.ensure_initialized()

    ctx = AgentCallbackContext(
        agent=agent,
        inputs=ModelCallInputs(messages=[]),
        session=None,
        context=None
    )
    rail = ContextEngineeringRail()
    await rail.fix_incomplete_tool_context(ctx)  # should not raise


@pytest.mark.asyncio
async def test_before_invoke_and_on_exception_call_fix_context(tmp_path: Path):
    """before_invoke and on_model_exception should call fix_incomplete_tool_context."""
    sys_operation = _make_sys_operation(tmp_path)
    workspace = Workspace(root_path=str(tmp_path))
    agent = _make_agent(sys_operation, workspace)
    await agent.ensure_initialized()

    tool_call = ToolCall(id="tc", type="function", name="t", arguments="{}")
    ctx = _make_fix_ctx(
        agent,
        [
            AssistantMessage(content="call", tool_calls=[tool_call]),
            UserMessage(content="u")
        ]
    )

    rail = ContextEngineeringRail()
    await rail.before_invoke(ctx)
    placeholders = [m for m in ctx.context.added_messages
                    if isinstance(m, ToolMessage) and "[工具执行被中断]" in m.content]
    assert len(placeholders) == 1

    ctx2 = _make_fix_ctx(
        agent,
        [
            AssistantMessage(
                content="call",
                tool_calls=[ToolCall(id="tc2", type="function", name="t", arguments="{}")]
            ),
            UserMessage(content="u")
        ]
    )
    await rail.on_model_exception(ctx2)
    placeholders2 = [m for m in ctx2.context.added_messages
                     if isinstance(m, ToolMessage) and "[工具执行被中断]" in m.content]
    assert len(placeholders2) == 1
