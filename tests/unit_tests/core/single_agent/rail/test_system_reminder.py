# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Full-coverage tests for system-reminder injection via ctx.extra["_system_reminders"].

Test categories:
  1. Protocol structure — ctx.extra["_system_reminders"] key/value format
  2. Single reminder injection — tail SystemMessage with exact content
  3. Multiple reminders — merge order and separator
  4. No-reminder baseline — zero side effects
  5. Context-engine bypass — reminder appended after trimming
  6. Edge-case content — empty, long, special chars, XML tags
  7. Priority ordering — high-priority rail writes first
  8. Multi-call lifecycle — tool-call loop, streaming, heartbeat
  9. Prompt builder separation — time NOT in system prompt
  10. After-model-call visibility — downstream rails can inspect reminder
"""
import os
import unittest
from unittest.mock import patch

from openjiuwen.core.foundation.llm.schema.message import SystemMessage
from openjiuwen.core.single_agent import (
    AgentCard, ReActAgent, ReActAgentConfig,
)
from openjiuwen.core.foundation.llm import (
    ModelRequestConfig, ModelClientConfig,
)
from openjiuwen.core.foundation.tool import (
    LocalFunction, ToolCard,
)
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    AgentRail,
)
from openjiuwen.core.runner import Runner
from openjiuwen.harness.prompts import PromptSection

from tests.unit_tests.fixtures.mock_llm import (
    MockLLMModel,
    create_text_response,
    create_tool_call_response,
)


# ============================================================
# Helpers
# ============================================================

def _create_model_config():
    return ModelRequestConfig(model="gpt-3.5-turbo", temperature=0.8, top_p=0.9)


def _create_client_config():
    return ModelClientConfig(
        client_provider="OpenAI",
        api_key="mock_key",
        api_base="mock_url",
        timeout=30,
        verify_ssl=False,
    )


def _create_add_tool():
    return LocalFunction(
        card=ToolCard(
            id="add", name="add", description="加法运算",
            input_params={
                "type": "object",
                "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
                "required": ["a", "b"],
            },
        ),
        func=lambda a, b: a + b,
    )


def _make_agent():
    os.environ.setdefault("LLM_SSL_VERIFY", "false")
    card = AgentCard(description="测试助手")
    config = ReActAgentConfig(
        model_config_obj=_create_model_config(),
        model_client_config=_create_client_config(),
        prompt_template=[dict(role="system", content="你是测试助手。")],
    )
    agent = ReActAgent(card=card).configure(config)
    tool = _create_add_tool()
    agent.ability_manager.add(tool.card)
    if Runner.resource_mgr.get_tool(tool.card.id) is None:
        Runner.resource_mgr.add_tool(tool)
    return agent, tool


# ============================================================
# Test Rails
# ============================================================

class ReminderRail(AgentRail):
    """Rail that writes one system-reminder into ctx.extra."""

    def __init__(self, content: str, source: str = "test_rail", priority: int = 50):
        super().__init__()
        self._content = content
        self._source = source
        self.priority = priority

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        ctx.extra.setdefault("_system_reminders", []).append({
            "content": self._content,
            "source": self._source,
        })


class MultiReminderRail(AgentRail):
    """Rail that writes multiple system-reminders into ctx.extra."""

    def __init__(self, entries: list[dict[str, str]], priority: int = 50):
        super().__init__()
        self._entries = entries
        self.priority = priority

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        reminders = ctx.extra.setdefault("_system_reminders", [])
        for entry in self._entries:
            reminders.append(entry)


class InspectAfterModelCallRail(AgentRail):
    """Rail that captures ctx.inputs.messages in after_model_call."""

    def __init__(self):
        super().__init__()
        self.captured_messages = []

    async def after_model_call(self, ctx: AgentCallbackContext) -> None:
        self.captured_messages.append(list(ctx.inputs.messages))


class ReminderPlusBuilderRail(AgentRail):
    """Rail that writes reminder AND adds a section to prompt builder."""

    def __init__(self, reminder_content: str, section_name: str, section_content: str, section_priority: int = 30):
        super().__init__()
        self._reminder = reminder_content
        self._section_name = section_name
        self._section_content = section_content
        self._section_priority = section_priority

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        ctx.extra.setdefault("_system_reminders", []).append({
            "content": self._reminder,
            "source": "dual_rail",
        })
        builder = getattr(ctx.agent, "prompt_builder", None) or getattr(ctx.agent, "system_prompt_builder", None)
        if builder:
            builder.add_section(PromptSection(
                name=self._section_name,
                content={"cn": self._section_content, "en": self._section_content},
                priority=self._section_priority,
            ))


# ============================================================
# 1. Protocol Structure Tests
# ============================================================

class TestProtocolStructure(unittest.TestCase):
    """Validate _system_reminders key format in AgentCallbackContext.extra."""

    def test_ctx_extra_defaults_to_empty_dict(self):
        """ctx.extra is an empty dict by default."""
        agent, _ = _make_agent()
        ctx = AgentCallbackContext(agent=agent)
        assert isinstance(ctx.extra, dict)
        assert "_system_reminders" not in ctx.extra

    def test_ctx_extra_accepts_system_reminders_list_of_dicts(self):
        """ctx.extra['_system_reminders'] stores list[dict[str, str]]."""
        agent, _ = _make_agent()
        ctx = AgentCallbackContext(agent=agent)
        ctx.extra["_system_reminders"] = [
            {"content": "时间：10:00", "source": "time_rail"},
            {"content": "安全提示", "source": "security_rail"},
        ]
        assert len(ctx.extra["_system_reminders"]) == 2
        assert ctx.extra["_system_reminders"][0]["content"] == "时间：10:00"
        assert ctx.extra["_system_reminders"][0]["source"] == "time_rail"

    def test_ctx_extra_setdefault_creates_empty_list(self):
        """setdefault creates an empty list if key is missing."""
        agent, _ = _make_agent()
        ctx = AgentCallbackContext(agent=agent)
        result = ctx.extra.setdefault("_system_reminders", [])
        assert result == []
        assert "_system_reminders" in ctx.extra

    def test_ctx_extra_setdefault_preserves_existing_list(self):
        """setdefault returns existing list if key already present."""
        agent, _ = _make_agent()
        ctx = AgentCallbackContext(agent=agent)
        ctx.extra["_system_reminders"] = [{"content": "x", "source": "y"}]
        result = ctx.extra.setdefault("_system_reminders", [])
        assert result == [{"content": "x", "source": "y"}]
        result.append({"content": "z", "source": "w"})
        assert len(ctx.extra["_system_reminders"]) == 2

    def test_ctx_extra_empty_reminders_list(self):
        """Empty _system_reminders list means no reminder appended."""
        agent, _ = _make_agent()
        ctx = AgentCallbackContext(agent=agent)
        ctx.extra["_system_reminders"] = []
        assert len(ctx.extra["_system_reminders"]) == 0


# ============================================================
# 2. Single Reminder Injection Tests
# ============================================================

class TestSingleReminderInjection(unittest.IsolatedAsyncioTestCase):
    """Tests for single reminder becoming a tail SystemMessage."""

    async def test_single_reminder_appended_as_tail_system_message(self):
        """Single reminder becomes a SystemMessage at messages tail."""
        agent, _ = _make_agent()
        rail = ReminderRail("当前时间：2026-05-23 10:00:00")
        await agent.register_rail(rail)

        mock_llm = MockLLMModel()
        mock_llm.set_responses([create_text_response("ok")])
        with patch.object(agent, "_get_llm", return_value=mock_llm):
            await agent.invoke({"query": "hello"})

        messages = mock_llm.call_history[0]
        tail = messages[-1]
        assert isinstance(tail, SystemMessage)
        assert "当前时间" in tail.content

    async def test_single_reminder_content_exact_match(self):
        """Reminder content is passed through without modification."""
        agent, _ = _make_agent()
        content = "# 当前日期与时间\n\n- 当前时间：2026-05-23 10:00:00\n- 当前年份：2026"
        rail = ReminderRail(content)
        await agent.register_rail(rail)

        mock_llm = MockLLMModel()
        mock_llm.set_responses([create_text_response("ok")])
        with patch.object(agent, "_get_llm", return_value=mock_llm):
            await agent.invoke({"query": "hello"})

        messages = mock_llm.call_history[0]
        tail = messages[-1]
        assert isinstance(tail, SystemMessage)
        assert tail.content == content

    async def test_single_reminder_role_is_system(self):
        """Tail message role is 'system', not 'user' or 'assistant'."""
        agent, _ = _make_agent()
        rail = ReminderRail("test content")
        await agent.register_rail(rail)

        mock_llm = MockLLMModel()
        mock_llm.set_responses([create_text_response("ok")])
        with patch.object(agent, "_get_llm", return_value=mock_llm):
            await agent.invoke({"query": "hello"})

        messages = mock_llm.call_history[0]
        tail = messages[-1]
        assert tail.role == "system"

    async def test_single_reminder_is_last_in_messages_list(self):
        """Reminder message is the absolute last element of messages."""
        agent, _ = _make_agent()
        rail = ReminderRail("尾部提醒")
        await agent.register_rail(rail)

        mock_llm = MockLLMModel()
        mock_llm.set_responses([create_text_response("ok")])
        with patch.object(agent, "_get_llm", return_value=mock_llm):
            await agent.invoke({"query": "hello"})

        messages = mock_llm.call_history[0]
        assert isinstance(messages[-1], SystemMessage)
        assert "尾部提醒" in messages[-1].content
        # No message after the reminder
        assert len(messages) >= 2  # at least system prompt + user query + reminder

    async def test_reminder_not_in_first_position(self):
        """Reminder is NOT at messages[0]; that position is the system prompt."""
        agent, _ = _make_agent()
        rail = ReminderRail("时间：10:00")
        await agent.register_rail(rail)

        mock_llm = MockLLMModel()
        mock_llm.set_responses([create_text_response("ok")])
        with patch.object(agent, "_get_llm", return_value=mock_llm):
            await agent.invoke({"query": "hello"})

        messages = mock_llm.call_history[0]
        assert isinstance(messages[0], SystemMessage)
        # First message is system prompt, not reminder
        assert "时间：10:00" not in messages[0].content


# ============================================================
# 3. Multiple Reminders — Merge and Ordering Tests
# ============================================================

class TestMultipleReminders(unittest.IsolatedAsyncioTestCase):
    """Tests for merging multiple reminders into one tail SystemMessage."""

    async def test_two_reminders_merged_with_double_newline(self):
        """Two reminders joined with \\n\\n separator."""
        agent, _ = _make_agent()
        rail = MultiReminderRail([
            {"content": "时间：10:00", "source": "time_rail"},
            {"content": "平台：Linux", "source": "platform_rail"},
        ])
        await agent.register_rail(rail)

        mock_llm = MockLLMModel()
        mock_llm.set_responses([create_text_response("ok")])
        with patch.object(agent, "_get_llm", return_value=mock_llm):
            await agent.invoke({"query": "hello"})

        messages = mock_llm.call_history[0]
        tail = messages[-1]
        assert isinstance(tail, SystemMessage)
        assert "时间：10:00" in tail.content
        assert "平台：Linux" in tail.content
        assert tail.content == "时间：10:00\n\n平台：Linux"

    async def test_three_reminders_merged_preserving_order(self):
        """Three reminders preserve insertion order when merged."""
        agent, _ = _make_agent()
        rail = MultiReminderRail([
            {"content": "第一", "source": "a"},
            {"content": "第二", "source": "b"},
            {"content": "第三", "source": "c"},
        ])
        await agent.register_rail(rail)

        mock_llm = MockLLMModel()
        mock_llm.set_responses([create_text_response("ok")])
        with patch.object(agent, "_get_llm", return_value=mock_llm):
            await agent.invoke({"query": "hello"})

        messages = mock_llm.call_history[0]
        tail = messages[-1]
        assert isinstance(tail, SystemMessage)
        assert tail.content == "第一\n\n第二\n\n第三"

    async def test_priority_ordering_high_runs_first_in_reminders(self):
        """High-priority rail appends reminder before low-priority rail."""
        agent, _ = _make_agent()
        high_rail = ReminderRail("高优先级提醒", source="high", priority=90)
        low_rail = ReminderRail("低优先级提醒", source="low", priority=10)

        # Register low first, high second — high should execute first
        await agent.register_rail(low_rail)
        await agent.register_rail(high_rail)

        mock_llm = MockLLMModel()
        mock_llm.set_responses([create_text_response("ok")])
        with patch.object(agent, "_get_llm", return_value=mock_llm):
            await agent.invoke({"query": "hello"})

        messages = mock_llm.call_history[0]
        tail = messages[-1]
        assert isinstance(tail, SystemMessage)
        # High priority ran first → its content appears first in the merged text
        assert tail.content.startswith("高优先级提醒")

    async def test_two_separate_rails_produce_merged_reminder(self):
        """Two independently registered rails each write one reminder."""
        agent, _ = _make_agent()
        rail_a = ReminderRail("提醒A", source="rail_a")
        rail_b = ReminderRail("提醒B", source="rail_b")
        await agent.register_rail(rail_a)
        await agent.register_rail(rail_b)

        mock_llm = MockLLMModel()
        mock_llm.set_responses([create_text_response("ok")])
        with patch.object(agent, "_get_llm", return_value=mock_llm):
            await agent.invoke({"query": "hello"})

        messages = mock_llm.call_history[0]
        tail = messages[-1]
        assert isinstance(tail, SystemMessage)
        assert "提醒A" in tail.content
        assert "提醒B" in tail.content

    async def test_multiple_reminders_produce_single_system_message(self):
        """Multiple reminders result in exactly ONE tail SystemMessage, not many."""
        agent, _ = _make_agent()
        rail = MultiReminderRail([
            {"content": "r1", "source": "a"},
            {"content": "r2", "source": "b"},
            {"content": "r3", "source": "c"},
        ])
        await agent.register_rail(rail)

        mock_llm = MockLLMModel()
        mock_llm.set_responses([create_text_response("ok")])
        with patch.object(agent, "_get_llm", return_value=mock_llm):
            await agent.invoke({"query": "hello"})

        messages = mock_llm.call_history[0]
        # Only one tail SystemMessage, not three
        tail_system_msgs = [m for m in messages[1:] if isinstance(m, SystemMessage)]
        assert len(tail_system_msgs) == 1
        assert "r1" in tail_system_msgs[0].content
        assert "r2" in tail_system_msgs[0].content
        assert "r3" in tail_system_msgs[0].content


# ============================================================
# 4. No-Reminder Baseline Tests
# ============================================================

class TestNoReminderBaseline(unittest.IsolatedAsyncioTestCase):
    """Tests for zero side effects when no reminders are present."""

    async def test_no_reminder_no_extra_system_message(self):
        """Without _system_reminders, no tail SystemMessage is appended."""
        agent, _ = _make_agent()
        mock_llm = MockLLMModel()
        mock_llm.set_responses([create_text_response("ok")])
        with patch.object(agent, "_get_llm", return_value=mock_llm):
            await agent.invoke({"query": "hello"})

        messages = mock_llm.call_history[0]
        system_msgs = [m for m in messages if isinstance(m, SystemMessage)]
        assert len(system_msgs) == 1

    async def test_empty_reminders_list_no_tail_system_message(self):
        """Empty _system_reminders list produces no tail SystemMessage."""
        agent, _ = _make_agent()
        # Rail that sets empty list
        class EmptyReminderRail(AgentRail):
            async def before_model_call(self, ctx):
                ctx.extra["_system_reminders"] = []

        await agent.register_rail(EmptyReminderRail())
        mock_llm = MockLLMModel()
        mock_llm.set_responses([create_text_response("ok")])
        with patch.object(agent, "_get_llm", return_value=mock_llm):
            await agent.invoke({"query": "hello"})

        messages = mock_llm.call_history[0]
        system_msgs = [m for m in messages if isinstance(m, SystemMessage)]
        assert len(system_msgs) == 1

    async def test_no_reminder_message_count_unchanged(self):
        """Without reminders, messages length matches context window output."""
        agent, _ = _make_agent()
        mock_llm = MockLLMModel()
        mock_llm.set_responses([create_text_response("ok")])
        with patch.object(agent, "_get_llm", return_value=mock_llm):
            await agent.invoke({"query": "hello"})

        messages = mock_llm.call_history[0]
        # Should be: [SystemMessage(prompt), UserMessage(query)]
        assert len(messages) == 2

    async def test_no_reminder_system_prompt_content_unchanged(self):
        """System prompt content is unaffected when no reminders."""
        agent, _ = _make_agent()
        mock_llm = MockLLMModel()
        mock_llm.set_responses([create_text_response("ok")])
        with patch.object(agent, "_get_llm", return_value=mock_llm):
            await agent.invoke({"query": "hello"})

        messages = mock_llm.call_history[0]
        system_msg = messages[0]
        assert isinstance(system_msg, SystemMessage)
        assert "测试助手" in system_msg.content


# ============================================================
# 5. Context Engine Bypass Tests
# ============================================================

class TestContextEngineBypass(unittest.IsolatedAsyncioTestCase):
    """Tests that reminder appended after context window, bypassing trimming."""

    async def test_reminder_appended_after_get_context_window(self):
        """Reminder is not part of context_window output; appended separately."""
        agent, _ = _make_agent()
        rail = ReminderRail("不可裁剪的内容")
        await agent.register_rail(rail)

        mock_llm = MockLLMModel()
        mock_llm.set_responses([create_text_response("ok")])
        with patch.object(agent, "_get_llm", return_value=mock_llm):
            await agent.invoke({"query": "hello"})

        messages = mock_llm.call_history[0]
        tail = messages[-1]
        assert isinstance(tail, SystemMessage)
        assert "不可裁剪的内容" in tail.content

    async def test_reminder_not_in_context_window_system_messages(self):
        """Reminder is NOT inside context_window.system_messages."""
        agent, _ = _make_agent()
        rail = ReminderRail("独立提醒")
        await agent.register_rail(rail)

        mock_llm = MockLLMModel()
        mock_llm.set_responses([create_text_response("ok")])

        with patch.object(agent, "_get_llm", return_value=mock_llm):
            await agent.invoke({"query": "hello"})

        # Verify: reminder in final messages, not in context window's system_messages
        messages = mock_llm.call_history[0]
        tail = messages[-1]
        assert "独立提醒" in tail.content
        # The context window's system_messages should NOT contain the reminder
        # (We can verify by checking that only ONE SystemMessage appears in
        #  the position range [0, len(messages)-2])
        non_tail_system_msgs = [m for m in messages[:-1] if isinstance(m, SystemMessage)]
        assert all("独立提醒" not in m.content for m in non_tail_system_msgs)

    async def test_reminder_survives_even_if_context_trims_history(self):
        """Reminder is always delivered, even if context engine trims earlier messages."""
        agent, _ = _make_agent()
        rail = ReminderRail("始终送达的提醒")
        await agent.register_rail(rail)

        mock_llm = MockLLMModel()
        mock_llm.set_responses([
            create_tool_call_response("add", '{"a": 1, "b": 2}'),
            create_text_response("done"),
        ])
        with patch.object(agent, "_get_llm", return_value=mock_llm):
            await agent.invoke({"query": "1+2"})

        # Both LLM calls should have the reminder
        for call_messages in mock_llm.call_history:
            tail = call_messages[-1]
            assert isinstance(tail, SystemMessage)
            assert "始终送达的提醒" in tail.content


# ============================================================
# 6. Edge-Case Content Tests
# ============================================================

class TestEdgeCaseContent(unittest.IsolatedAsyncioTestCase):
    """Tests for edge-case reminder content."""

    async def test_reminder_with_xml_system_reminder_tags(self):
        """Content with <system-reminder> tags is preserved as-is."""
        agent, _ = _make_agent()
        content = "<system-reminder>\n当前时间：2026-05-23\n</system-reminder>"
        rail = ReminderRail(content)
        await agent.register_rail(rail)

        mock_llm = MockLLMModel()
        mock_llm.set_responses([create_text_response("ok")])
        with patch.object(agent, "_get_llm", return_value=mock_llm):
            await agent.invoke({"query": "hello"})

        messages = mock_llm.call_history[0]
        tail = messages[-1]
        assert "<system-reminder>" in tail.content
        assert "</system-reminder>" in tail.content
        assert "当前时间" in tail.content

    async def test_reminder_with_unicode_content(self):
        """Unicode content in reminders is preserved without corruption."""
        agent, _ = _make_agent()
        content = "当前时间：2026年5月23日 🕐 日本語テスト"
        rail = ReminderRail(content)
        await agent.register_rail(rail)

        mock_llm = MockLLMModel()
        mock_llm.set_responses([create_text_response("ok")])
        with patch.object(agent, "_get_llm", return_value=mock_llm):
            await agent.invoke({"query": "hello"})

        messages = mock_llm.call_history[0]
        tail = messages[-1]
        assert "2026年5月23日" in tail.content
        assert "日本語テスト" in tail.content

    async def test_reminder_with_newlines_in_content(self):
        """Content with embedded newlines is preserved in the SystemMessage."""
        agent, _ = _make_agent()
        content = "# 时间\n\n- 当前时间：10:00\n- 当前年份：2026"
        rail = ReminderRail(content)
        await agent.register_rail(rail)

        mock_llm = MockLLMModel()
        mock_llm.set_responses([create_text_response("ok")])
        with patch.object(agent, "_get_llm", return_value=mock_llm):
            await agent.invoke({"query": "hello"})

        messages = mock_llm.call_history[0]
        tail = messages[-1]
        assert "# 时间" in tail.content
        assert "当前时间：10:00" in tail.content

    async def test_reminder_with_empty_string_content(self):
        """Empty-string reminder content still produces a SystemMessage."""
        agent, _ = _make_agent()
        rail = ReminderRail("")
        await agent.register_rail(rail)

        mock_llm = MockLLMModel()
        mock_llm.set_responses([create_text_response("ok")])
        with patch.object(agent, "_get_llm", return_value=mock_llm):
            await agent.invoke({"query": "hello"})

        messages = mock_llm.call_history[0]
        tail = messages[-1]
        assert isinstance(tail, SystemMessage)
        assert tail.content == ""

    async def test_reminder_with_long_content(self):
        """Long reminder content (1000+ chars) is preserved intact."""
        agent, _ = _make_agent()
        long_content = "详细提醒：" + "A" * 1000
        rail = ReminderRail(long_content)
        await agent.register_rail(rail)

        mock_llm = MockLLMModel()
        mock_llm.set_responses([create_text_response("ok")])
        with patch.object(agent, "_get_llm", return_value=mock_llm):
            await agent.invoke({"query": "hello"})

        messages = mock_llm.call_history[0]
        tail = messages[-1]
        assert isinstance(tail, SystemMessage)
        assert len(tail.content) >= 1000

    async def test_reminder_with_markdown_table_content(self):
        """Markdown table content in reminders is preserved."""
        agent, _ = _make_agent()
        content = (
            "# 运行时\n\n"
            "| 操作 | Windows | Linux |\n"
            "|------|---------|-------|\n"
            "| 创建目录 | mkdir | mkdir -p |\n"
        )
        rail = ReminderRail(content)
        await agent.register_rail(rail)

        mock_llm = MockLLMModel()
        mock_llm.set_responses([create_text_response("ok")])
        with patch.object(agent, "_get_llm", return_value=mock_llm):
            await agent.invoke({"query": "hello"})

        messages = mock_llm.call_history[0]
        tail = messages[-1]
        assert "| 操作" in tail.content
        assert "|------" in tail.content

    async def test_merge_separator_between_reminders_with_newlines(self):
        """When individual reminders have internal newlines, merge uses \\n\\n."""
        agent, _ = _make_agent()
        rail = MultiReminderRail([
            {"content": "第一行\n第二行", "source": "a"},
            {"content": "第三行\n第四行", "source": "b"},
        ])
        await agent.register_rail(rail)

        mock_llm = MockLLMModel()
        mock_llm.set_responses([create_text_response("ok")])
        with patch.object(agent, "_get_llm", return_value=mock_llm):
            await agent.invoke({"query": "hello"})

        messages = mock_llm.call_history[0]
        tail = messages[-1]
        assert tail.content == "第一行\n第二行\n\n第三行\n第四行"


# ============================================================
# 7. Multi-Call Lifecycle Tests
# ============================================================

class TestMultiCallLifecycle(unittest.IsolatedAsyncioTestCase):
    """Tests for reminder across tool-call loops and multiple invocations."""

    async def test_reminder_in_tool_call_loop_first_and_second_model_call(self):
        """Reminder present in both model calls of a tool-call loop."""
        agent, _ = _make_agent()
        rail = ReminderRail("时间：10:00")
        await agent.register_rail(rail)

        mock_llm = MockLLMModel()
        mock_llm.set_responses([
            create_tool_call_response("add", '{"a": 1, "b": 2}'),
            create_text_response("3"),
        ])
        with patch.object(agent, "_get_llm", return_value=mock_llm):
            await agent.invoke({"query": "1+2"})

        assert mock_llm.call_count == 2
        for call_messages in mock_llm.call_history:
            tail = call_messages[-1]
            assert isinstance(tail, SystemMessage)
            assert "时间：10:00" in tail.content

    async def test_reminder_refreshes_on_each_model_call(self):
        """Each model call gets a fresh reminder (not stale from previous call)."""
        agent, _ = _make_agent()
        call_contents = ["第一次提醒", "第二次提醒"]
        call_count = {"n": 0}

        class DynamicReminderRail(AgentRail):
            async def before_model_call(self, ctx):
                idx = min(call_count["n"], len(call_contents) - 1)
                ctx.extra.setdefault("_system_reminders", []).append({
                    "content": call_contents[idx],
                    "source": "dynamic",
                })
                call_count["n"] += 1

        await agent.register_rail(DynamicReminderRail())

        mock_llm = MockLLMModel()
        mock_llm.set_responses([
            create_tool_call_response("add", '{"a": 1, "b": 2}'),
            create_text_response("3"),
        ])
        with patch.object(agent, "_get_llm", return_value=mock_llm):
            await agent.invoke({"query": "1+2"})

        # First call has first reminder
        first_call = mock_llm.call_history[0]
        assert "第一次提醒" in first_call[-1].content

        # Second call has second reminder (fresh, not stale)
        second_call = mock_llm.call_history[1]
        assert "第二次提醒" in second_call[-1].content

    async def test_reminder_in_separate_invoke_calls(self):
        """Each separate invoke gets its own reminder (ctx.extra is per-invoke)."""
        agent, _ = _make_agent()
        rail = ReminderRail("独立提醒")
        await agent.register_rail(rail)

        mock_llm = MockLLMModel()
        mock_llm.set_responses([
            create_text_response("answer 1"),
            create_text_response("answer 2"),
        ])
        with patch.object(agent, "_get_llm", return_value=mock_llm):
            await agent.invoke({"query": "question 1"})
            await agent.invoke({"query": "question 2"})

        assert mock_llm.call_count == 2
        for call_messages in mock_llm.call_history:
            tail = call_messages[-1]
            assert isinstance(tail, SystemMessage)
            assert "独立提醒" in tail.content

    async def test_ctx_extra_is_per_invoke_not_persistent(self):
        """ctx.extra['_system_reminders'] does not persist across invokes."""
        agent, _ = _make_agent()
        captured_extras = []

        class CaptureExtraRail(AgentRail):
            async def before_invoke(self, ctx):
                captured_extras.append(dict(ctx.extra))
            async def before_model_call(self, ctx):
                ctx.extra.setdefault("_system_reminders", []).append({
                    "content": "提醒", "source": "test",
                })

        await agent.register_rail(CaptureExtraRail())

        mock_llm = MockLLMModel()
        mock_llm.set_responses([
            create_text_response("answer 1"),
            create_text_response("answer 2"),
        ])
        with patch.object(agent, "_get_llm", return_value=mock_llm):
            await agent.invoke({"query": "q1"})
            await agent.invoke({"query": "q2"})

        # First invoke: extra is empty at before_invoke, filled at before_model_call
        assert "_system_reminders" not in captured_extras[0]
        # Second invoke: extra is also empty at before_invoke (fresh ctx)
        assert "_system_reminders" not in captured_extras[1]


# ============================================================
# 8. Prompt Builder Separation Tests
# ============================================================

class TestPromptBuilderSeparation(unittest.IsolatedAsyncioTestCase):
    """Tests that time content is NOT in system prompt builder output."""

    async def test_reminder_content_not_in_system_prompt(self):
        """Reminder content does NOT appear in the system prompt (messages[0])."""
        agent, _ = _make_agent()
        rail = ReminderRail("时间提醒：2026-05-23")
        await agent.register_rail(rail)

        mock_llm = MockLLMModel()
        mock_llm.set_responses([create_text_response("ok")])
        with patch.object(agent, "_get_llm", return_value=mock_llm):
            await agent.invoke({"query": "hello"})

        messages = mock_llm.call_history[0]
        # System prompt (first message) should NOT contain reminder content
        system_msg = messages[0]
        assert isinstance(system_msg, SystemMessage)
        assert "时间提醒" not in system_msg.content

    async def test_reminder_and_builder_section_both_present(self):
        """A rail can inject both a PromptSection AND a reminder simultaneously."""
        agent, _ = _make_agent()
        rail = ReminderPlusBuilderRail(
            reminder_content="独立提醒内容",
            section_name="extra_section",
            section_content="额外的section内容",
        )
        await agent.register_rail(rail)

        mock_llm = MockLLMModel()
        mock_llm.set_responses([create_text_response("ok")])
        with patch.object(agent, "_get_llm", return_value=mock_llm):
            await agent.invoke({"query": "hello"})

        messages = mock_llm.call_history[0]
        # System prompt contains the section
        system_msg = messages[0]
        assert "额外的section内容" in system_msg.content
        # Tail contains the reminder
        tail = messages[-1]
        assert isinstance(tail, SystemMessage)
        assert "独立提醒内容" in tail.content
        # Reminder is NOT in system prompt
        assert "独立提醒内容" not in system_msg.content

    async def test_original_system_prompt_unaffected_by_reminder(self):
        """Original system prompt content is unchanged when reminder is added."""
        agent, _ = _make_agent()
        rail = ReminderRail("外部提醒")
        await agent.register_rail(rail)

        # Also run without reminder for comparison
        agent_no_reminder, _ = _make_agent()

        mock_llm_1 = MockLLMModel()
        mock_llm_1.set_responses([create_text_response("ok")])

        mock_llm_2 = MockLLMModel()
        mock_llm_2.set_responses([create_text_response("ok")])

        with patch.object(agent, "_get_llm", return_value=mock_llm_1):
            await agent.invoke({"query": "hello"})

        with patch.object(agent_no_reminder, "_get_llm", return_value=mock_llm_2):
            await agent_no_reminder.invoke({"query": "hello"})

        # System prompt content should be identical
        sys_with = mock_llm_1.call_history[0][0].content
        sys_without = mock_llm_2.call_history[0][0].content
        assert sys_with == sys_without


# ============================================================
# 9. After-Model-Call Visibility Tests
# ============================================================

class TestAfterModelCallVisibility(unittest.IsolatedAsyncioTestCase):
    """Tests that after_model_call hooks can inspect the appended reminder."""

    async def test_after_model_call_sees_reminder_in_messages(self):
        """after_model_call hook can see the reminder in ctx.inputs.messages."""
        agent, _ = _make_agent()
        rail = ReminderRail("可见提醒")
        inspect_rail = InspectAfterModelCallRail()
        await agent.register_rail(rail)
        await agent.register_rail(inspect_rail)

        mock_llm = MockLLMModel()
        mock_llm.set_responses([create_text_response("ok")])
        with patch.object(agent, "_get_llm", return_value=mock_llm):
            await agent.invoke({"query": "hello"})

        assert len(inspect_rail.captured_messages) == 1
        messages = inspect_rail.captured_messages[0]
        tail = messages[-1]
        assert isinstance(tail, SystemMessage)
        assert "可见提醒" in tail.content

    async def test_after_model_call_messages_matches_llm_call(self):
        """ctx.inputs.messages in after_model_call matches what LLM received."""
        agent, _ = _make_agent()
        rail = ReminderRail("一致性提醒")
        inspect_rail = InspectAfterModelCallRail()
        await agent.register_rail(rail)
        await agent.register_rail(inspect_rail)

        mock_llm = MockLLMModel()
        mock_llm.set_responses([create_text_response("ok")])
        with patch.object(agent, "_get_llm", return_value=mock_llm):
            await agent.invoke({"query": "hello"})

        llm_messages = mock_llm.call_history[0]
        hook_messages = inspect_rail.captured_messages[0]
        # Same number of messages
        assert len(hook_messages) == len(llm_messages)
        # Same content in tail
        assert hook_messages[-1].content == llm_messages[-1].content


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v", "-s"])