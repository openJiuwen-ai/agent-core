# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Full-coverage tests for environment-context injection via ctx.extra.

Verifies that rails write to ctx.extra["environment_context"], and
_railed_model_call consumes it with pop(), wraps as UserMessage with
<environment_context> XML tags — preventing multi-turn accumulation
and preserving KV cache prefix stability.
"""
import os
import unittest
from unittest.mock import patch

from openjiuwen.core.foundation.llm.schema.message import (
    SystemMessage,
    UserMessage,
)
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

ENV_CTX_TAG_OPEN = "<environment_context>\n"
ENV_CTX_TAG_CLOSE = "\n</environment_context>"


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

class EnvironmentContextRail(AgentRail):
    """Rail that writes one environment-context entry into ctx.extra."""

    def __init__(self, content: str, source: str = "test_rail", priority: int = 50):
        super().__init__()
        self._content = content
        self._source = source
        self.priority = priority

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        ctx.extra.setdefault("environment_context", []).append({
            "content": self._content,
            "source": self._source,
        })


class MultiContextRail(AgentRail):
    """Rail that writes multiple environment-context entries."""

    def __init__(self, entries: list[dict[str, str]], priority: int = 50):
        super().__init__()
        self._entries = entries
        self.priority = priority

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        contexts = ctx.extra.setdefault("environment_context", [])
        for entry in self._entries:
            contexts.append(entry)


class InspectAfterModelCallRail(AgentRail):
    """Rail that captures ctx.inputs.messages in after_model_call."""

    def __init__(self):
        super().__init__()
        self.captured_messages = []

    async def after_model_call(self, ctx: AgentCallbackContext) -> None:
        self.captured_messages.append(list(ctx.inputs.messages))


class ContextPlusBuilderRail(AgentRail):
    """Rail that writes environment_context AND adds a section to prompt builder."""

    def __init__(self, ctx_content: str, section_name: str, section_content: str, section_priority: int = 30):
        super().__init__()
        self._ctx_content = ctx_content
        self._section_name = section_name
        self._section_content = section_content
        self._section_priority = section_priority

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        ctx.extra.setdefault("environment_context", []).append({
            "content": self._ctx_content,
            "source": "dual_rail",
        })
        builder = getattr(ctx.agent, "prompt_builder", None) or getattr(ctx.agent, "system_prompt_builder", None)
        if builder:
            builder.add_section(PromptSection(
                name=self._section_name,
                content={"cn": self._section_content, "en": self._section_content},
                priority=self._section_priority,
            ))


def _wrap(content: str) -> str:
    """Wrap content in <environment_context> tags as _railed_model_call does."""
    return ENV_CTX_TAG_OPEN + content + ENV_CTX_TAG_CLOSE


# ============================================================
# 1. Protocol Structure Tests
# ============================================================

class TestProtocolStructure(unittest.TestCase):
    """Validate environment_context key format in AgentCallbackContext.extra."""

    def test_ctx_extra_defaults_to_empty_dict(self):
        """ctx.extra is an empty dict by default."""
        agent, _ = _make_agent()
        ctx = AgentCallbackContext(agent=agent)
        assert isinstance(ctx.extra, dict)
        assert "environment_context" not in ctx.extra

    def test_ctx_extra_accepts_environment_context_list_of_dicts(self):
        """ctx.extra['environment_context'] stores list[dict[str, str]]."""
        agent, _ = _make_agent()
        ctx = AgentCallbackContext(agent=agent)
        ctx.extra["environment_context"] = [
            {"content": "时间：10:00", "source": "time_rail"},
            {"content": "安全提示", "source": "security_rail"},
        ]
        assert len(ctx.extra["environment_context"]) == 2
        assert ctx.extra["environment_context"][0]["content"] == "时间：10:00"
        assert ctx.extra["environment_context"][0]["source"] == "time_rail"

    def test_ctx_extra_setdefault_creates_empty_list(self):
        """setdefault creates an empty list if key is missing."""
        agent, _ = _make_agent()
        ctx = AgentCallbackContext(agent=agent)
        result = ctx.extra.setdefault("environment_context", [])
        assert result == []
        assert "environment_context" in ctx.extra

    def test_ctx_extra_setdefault_preserves_existing_list(self):
        """setdefault returns existing list if key already present."""
        agent, _ = _make_agent()
        ctx = AgentCallbackContext(agent=agent)
        ctx.extra["environment_context"] = [{"content": "x", "source": "y"}]
        result = ctx.extra.setdefault("environment_context", [])
        assert result == [{"content": "x", "source": "y"}]
        result.append({"content": "z", "source": "w"})
        assert len(ctx.extra["environment_context"]) == 2

    def test_ctx_extra_empty_context_list(self):
        """Empty environment_context list means no tail message appended."""
        agent, _ = _make_agent()
        ctx = AgentCallbackContext(agent=agent)
        ctx.extra["environment_context"] = []
        assert len(ctx.extra["environment_context"]) == 0


# ============================================================
# 2. Single Context Injection Tests
# ============================================================

class TestSingleContextInjection(unittest.IsolatedAsyncioTestCase):
    """Tests for single environment_context becoming a tail UserMessage."""

    async def test_single_context_appended_as_tail_user_message(self):
        """Single environment_context becomes a UserMessage at messages tail."""
        agent, _ = _make_agent()
        rail = EnvironmentContextRail("当前时间：2026-05-23 10:00:00")
        await agent.register_rail(rail)

        mock_llm = MockLLMModel()
        mock_llm.set_responses([create_text_response("ok")])
        with patch.object(agent, "_get_llm", return_value=mock_llm):
            await agent.invoke({"query": "hello"})

        messages = mock_llm.call_history[0]
        tail = messages[-1]
        assert isinstance(tail, UserMessage)
        assert "当前时间" in tail.content

    async def test_single_context_content_has_environment_context_prefix(self):
        """Context content is wrapped in <environment_context> tags."""
        agent, _ = _make_agent()
        content = "# 当前日期与时间\n\n- 当前时间：2026-05-23 10:00:00\n- 当前年份：2026"
        rail = EnvironmentContextRail(content)
        await agent.register_rail(rail)

        mock_llm = MockLLMModel()
        mock_llm.set_responses([create_text_response("ok")])
        with patch.object(agent, "_get_llm", return_value=mock_llm):
            await agent.invoke({"query": "hello"})

        messages = mock_llm.call_history[0]
        tail = messages[-1]
        assert isinstance(tail, UserMessage)
        assert tail.content == _wrap(content)

    async def test_single_context_role_is_user(self):
        """Tail message role is 'user', not 'system'."""
        agent, _ = _make_agent()
        rail = EnvironmentContextRail("test content")
        await agent.register_rail(rail)

        mock_llm = MockLLMModel()
        mock_llm.set_responses([create_text_response("ok")])
        with patch.object(agent, "_get_llm", return_value=mock_llm):
            await agent.invoke({"query": "hello"})

        messages = mock_llm.call_history[0]
        tail = messages[-1]
        assert tail.role == "user"

    async def test_single_context_is_last_in_messages_list(self):
        """Context message is the absolute last element of messages."""
        agent, _ = _make_agent()
        rail = EnvironmentContextRail("尾部环境信息")
        await agent.register_rail(rail)

        mock_llm = MockLLMModel()
        mock_llm.set_responses([create_text_response("ok")])
        with patch.object(agent, "_get_llm", return_value=mock_llm):
            await agent.invoke({"query": "hello"})

        messages = mock_llm.call_history[0]
        assert isinstance(messages[-1], UserMessage)
        assert "尾部环境信息" in messages[-1].content

    async def test_context_not_in_first_position(self):
        """Context is NOT at messages[0]; that position is the system prompt."""
        agent, _ = _make_agent()
        rail = EnvironmentContextRail("时间：10:00")
        await agent.register_rail(rail)

        mock_llm = MockLLMModel()
        mock_llm.set_responses([create_text_response("ok")])
        with patch.object(agent, "_get_llm", return_value=mock_llm):
            await agent.invoke({"query": "hello"})

        messages = mock_llm.call_history[0]
        assert isinstance(messages[0], SystemMessage)
        assert "时间：10:00" not in messages[0].content


# ============================================================
# 3. Multiple Contexts — Merge and Ordering Tests
# ============================================================

class TestMultipleContexts(unittest.IsolatedAsyncioTestCase):
    """Tests for merging multiple environment_context entries into one UserMessage."""

    async def test_two_entries_merged_with_double_newline(self):
        """Two entries joined with \\n\\n separator inside <environment_context>."""
        agent, _ = _make_agent()
        rail = MultiContextRail([
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
        assert isinstance(tail, UserMessage)
        assert "时间：10:00" in tail.content
        assert "平台：Linux" in tail.content
        assert tail.content == _wrap("时间：10:00\n\n平台：Linux")

    async def test_three_entries_merged_preserving_order(self):
        """Three entries preserve insertion order when merged."""
        agent, _ = _make_agent()
        rail = MultiContextRail([
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
        assert isinstance(tail, UserMessage)
        assert tail.content == _wrap("第一\n\n第二\n\n第三")

    async def test_priority_ordering_high_runs_first_in_context(self):
        """High-priority rail appends context before low-priority rail."""
        agent, _ = _make_agent()
        high_rail = EnvironmentContextRail("高优先级信息", source="high", priority=90)
        low_rail = EnvironmentContextRail("低优先级信息", source="low", priority=10)

        await agent.register_rail(low_rail)
        await agent.register_rail(high_rail)

        mock_llm = MockLLMModel()
        mock_llm.set_responses([create_text_response("ok")])
        with patch.object(agent, "_get_llm", return_value=mock_llm):
            await agent.invoke({"query": "hello"})

        messages = mock_llm.call_history[0]
        tail = messages[-1]
        assert isinstance(tail, UserMessage)
        inner = tail.content.replace(ENV_CTX_TAG_OPEN, "").replace(ENV_CTX_TAG_CLOSE, "")
        assert inner.startswith("高优先级信息")

    async def test_two_separate_rails_produce_merged_context(self):
        """Two independently registered rails each write one entry."""
        agent, _ = _make_agent()
        rail_a = EnvironmentContextRail("信息A", source="rail_a")
        rail_b = EnvironmentContextRail("信息B", source="rail_b")
        await agent.register_rail(rail_a)
        await agent.register_rail(rail_b)

        mock_llm = MockLLMModel()
        mock_llm.set_responses([create_text_response("ok")])
        with patch.object(agent, "_get_llm", return_value=mock_llm):
            await agent.invoke({"query": "hello"})

        messages = mock_llm.call_history[0]
        tail = messages[-1]
        assert isinstance(tail, UserMessage)
        assert "信息A" in tail.content
        assert "信息B" in tail.content

    async def test_multiple_entries_produce_single_user_message(self):
        """Multiple entries result in exactly ONE tail UserMessage, not many."""
        agent, _ = _make_agent()
        rail = MultiContextRail([
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
        tail_user_msgs = [m for m in messages if isinstance(m, UserMessage) and ENV_CTX_TAG_OPEN in m.content]
        assert len(tail_user_msgs) == 1
        assert "r1" in tail_user_msgs[0].content
        assert "r2" in tail_user_msgs[0].content
        assert "r3" in tail_user_msgs[0].content


# ============================================================
# 4. No-Context Baseline Tests
# ============================================================

class TestNoContextBaseline(unittest.IsolatedAsyncioTestCase):
    """Tests for zero side effects when no environment_context is present."""

    async def test_no_context_no_extra_user_message(self):
        """Without environment_context, no tail UserMessage is appended."""
        agent, _ = _make_agent()
        mock_llm = MockLLMModel()
        mock_llm.set_responses([create_text_response("ok")])
        with patch.object(agent, "_get_llm", return_value=mock_llm):
            await agent.invoke({"query": "hello"})

        messages = mock_llm.call_history[0]
        user_msgs_with_tag = [m for m in messages if isinstance(m, UserMessage) and ENV_CTX_TAG_OPEN in m.content]
        assert len(user_msgs_with_tag) == 0

    async def test_empty_context_list_no_tail_message(self):
        """Empty environment_context list produces no tail message."""
        agent, _ = _make_agent()

        class EmptyContextRail(AgentRail):
            async def before_model_call(self, ctx):
                ctx.extra["environment_context"] = []

        await agent.register_rail(EmptyContextRail())
        mock_llm = MockLLMModel()
        mock_llm.set_responses([create_text_response("ok")])
        with patch.object(agent, "_get_llm", return_value=mock_llm):
            await agent.invoke({"query": "hello"})

        messages = mock_llm.call_history[0]
        # pop() returns empty list, which is truthy-ish but len==0 → no append
        user_msgs_with_tag = [m for m in messages if isinstance(m, UserMessage) and ENV_CTX_TAG_OPEN in m.content]
        assert len(user_msgs_with_tag) == 0

    async def test_no_context_message_count_unchanged(self):
        """Without environment_context, messages length matches context window output."""
        agent, _ = _make_agent()
        mock_llm = MockLLMModel()
        mock_llm.set_responses([create_text_response("ok")])
        with patch.object(agent, "_get_llm", return_value=mock_llm):
            await agent.invoke({"query": "hello"})

        messages = mock_llm.call_history[0]
        assert len(messages) == 2  # [SystemMessage(prompt), UserMessage(query)]

    async def test_no_context_system_prompt_content_unchanged(self):
        """System prompt content is unaffected when no environment_context."""
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
    """Tests that context appended after context window, bypassing trimming."""

    async def test_context_appended_after_get_context_window(self):
        """Context is not part of context_window output; appended separately."""
        agent, _ = _make_agent()
        rail = EnvironmentContextRail("不可裁剪的内容")
        await agent.register_rail(rail)

        mock_llm = MockLLMModel()
        mock_llm.set_responses([create_text_response("ok")])
        with patch.object(agent, "_get_llm", return_value=mock_llm):
            await agent.invoke({"query": "hello"})

        messages = mock_llm.call_history[0]
        tail = messages[-1]
        assert isinstance(tail, UserMessage)
        assert "不可裁剪的内容" in tail.content

    async def test_context_not_in_context_window_system_messages(self):
        """Context is NOT inside context_window.system_messages."""
        agent, _ = _make_agent()
        rail = EnvironmentContextRail("独立信息")
        await agent.register_rail(rail)

        mock_llm = MockLLMModel()
        mock_llm.set_responses([create_text_response("ok")])

        with patch.object(agent, "_get_llm", return_value=mock_llm):
            await agent.invoke({"query": "hello"})

        messages = mock_llm.call_history[0]
        tail = messages[-1]
        assert "独立信息" in tail.content
        non_tail_system_msgs = [m for m in messages[:-1] if isinstance(m, SystemMessage)]
        assert all("独立信息" not in m.content for m in non_tail_system_msgs)

    async def test_context_survives_even_if_context_trims_history(self):
        """Context always delivered, even if context engine trims earlier messages."""
        agent, _ = _make_agent()
        rail = EnvironmentContextRail("始终送达的信息")
        await agent.register_rail(rail)

        mock_llm = MockLLMModel()
        mock_llm.set_responses([
            create_tool_call_response("add", '{"a": 1, "b": 2}'),
            create_text_response("done"),
        ])
        with patch.object(agent, "_get_llm", return_value=mock_llm):
            await agent.invoke({"query": "1+2"})

        for call_messages in mock_llm.call_history:
            tail = call_messages[-1]
            assert isinstance(tail, UserMessage)
            assert "始终送达的信息" in tail.content


# ============================================================
# 6. Edge-Case Content Tests
# ============================================================

class TestEdgeCaseContent(unittest.IsolatedAsyncioTestCase):
    """Tests for edge-case environment_context content."""

    async def test_context_with_xml_tags_in_content(self):
        """Content with XML tags is preserved as-is inside <environment_context>."""
        agent, _ = _make_agent()
        content = "<system-reminder>\n当前时间：2026-05-23\n</system-reminder>"
        rail = EnvironmentContextRail(content)
        await agent.register_rail(rail)

        mock_llm = MockLLMModel()
        mock_llm.set_responses([create_text_response("ok")])
        with patch.object(agent, "_get_llm", return_value=mock_llm):
            await agent.invoke({"query": "hello"})

        messages = mock_llm.call_history[0]
        tail = messages[-1]
        assert "<system-reminder>" in tail.content
        assert "当前时间" in tail.content

    async def test_context_with_unicode_content(self):
        """Unicode content in context is preserved without corruption."""
        agent, _ = _make_agent()
        content = "当前时间：2026年5月23日 🕐 日本語テスト"
        rail = EnvironmentContextRail(content)
        await agent.register_rail(rail)

        mock_llm = MockLLMModel()
        mock_llm.set_responses([create_text_response("ok")])
        with patch.object(agent, "_get_llm", return_value=mock_llm):
            await agent.invoke({"query": "hello"})

        messages = mock_llm.call_history[0]
        tail = messages[-1]
        assert "2026年5月23日" in tail.content
        assert "日本語テスト" in tail.content

    async def test_context_with_newlines_in_content(self):
        """Content with embedded newlines is preserved in the UserMessage."""
        agent, _ = _make_agent()
        content = "# 时间\n\n- 当前时间：10:00\n- 当前年份：2026"
        rail = EnvironmentContextRail(content)
        await agent.register_rail(rail)

        mock_llm = MockLLMModel()
        mock_llm.set_responses([create_text_response("ok")])
        with patch.object(agent, "_get_llm", return_value=mock_llm):
            await agent.invoke({"query": "hello"})

        messages = mock_llm.call_history[0]
        tail = messages[-1]
        assert "# 时间" in tail.content
        assert "当前时间：10:00" in tail.content

    async def test_context_with_empty_string_content(self):
        """Empty-string context content still produces a UserMessage with tags."""
        agent, _ = _make_agent()
        rail = EnvironmentContextRail("")
        await agent.register_rail(rail)

        mock_llm = MockLLMModel()
        mock_llm.set_responses([create_text_response("ok")])
        with patch.object(agent, "_get_llm", return_value=mock_llm):
            await agent.invoke({"query": "hello"})

        messages = mock_llm.call_history[0]
        tail = messages[-1]
        assert isinstance(tail, UserMessage)
        assert tail.content == _wrap("")

    async def test_context_with_long_content(self):
        """Long context content (1000+ chars) is preserved intact."""
        agent, _ = _make_agent()
        long_content = "详细信息：" + "A" * 1000
        rail = EnvironmentContextRail(long_content)
        await agent.register_rail(rail)

        mock_llm = MockLLMModel()
        mock_llm.set_responses([create_text_response("ok")])
        with patch.object(agent, "_get_llm", return_value=mock_llm):
            await agent.invoke({"query": "hello"})

        messages = mock_llm.call_history[0]
        tail = messages[-1]
        assert isinstance(tail, UserMessage)
        inner = tail.content.replace(ENV_CTX_TAG_OPEN, "").replace(ENV_CTX_TAG_CLOSE, "")
        assert len(inner) >= 1000

    async def test_context_with_markdown_table_content(self):
        """Markdown table content in context is preserved."""
        agent, _ = _make_agent()
        content = (
            "# 运行时\n\n"
            "| 操作 | Windows | Linux |\n"
            "|------|---------|-------|\n"
            "| 创建目录 | mkdir | mkdir -p |\n"
        )
        rail = EnvironmentContextRail(content)
        await agent.register_rail(rail)

        mock_llm = MockLLMModel()
        mock_llm.set_responses([create_text_response("ok")])
        with patch.object(agent, "_get_llm", return_value=mock_llm):
            await agent.invoke({"query": "hello"})

        messages = mock_llm.call_history[0]
        tail = messages[-1]
        assert "| 操作" in tail.content
        assert "|------" in tail.content

    async def test_merge_separator_between_entries_with_newlines(self):
        """When entries have internal newlines, merge uses \\n\\n."""
        agent, _ = _make_agent()
        rail = MultiContextRail([
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
        assert tail.content == _wrap("第一行\n第二行\n\n第三行\n第四行")


# ============================================================
# 7. Multi-Call Lifecycle Tests (pop prevents accumulation)
# ============================================================

class TestMultiCallLifecycle(unittest.IsolatedAsyncioTestCase):
    """Tests for environment_context across tool-call loops and multiple invocations."""

    async def test_context_in_tool_call_loop_first_and_second_model_call(self):
        """Context present in both model calls of a tool-call loop."""
        agent, _ = _make_agent()
        rail = EnvironmentContextRail("时间：10:00")
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
            assert isinstance(tail, UserMessage)
            assert "时间：10:00" in tail.content

    async def test_context_refreshes_on_each_model_call_no_accumulation(self):
        """pop() clears after each call; no stale content from previous call."""
        agent, _ = _make_agent()
        call_contents = ["第一次信息", "第二次信息"]
        call_count = {"n": 0}

        class DynamicContextRail(AgentRail):
            async def before_model_call(self, ctx):
                idx = min(call_count["n"], len(call_contents) - 1)
                ctx.extra.setdefault("environment_context", []).append({
                    "content": call_contents[idx],
                    "source": "dynamic",
                })
                call_count["n"] += 1

        await agent.register_rail(DynamicContextRail())

        mock_llm = MockLLMModel()
        mock_llm.set_responses([
            create_tool_call_response("add", '{"a": 1, "b": 2}'),
            create_text_response("3"),
        ])
        with patch.object(agent, "_get_llm", return_value=mock_llm):
            await agent.invoke({"query": "1+2"})

        first_call = mock_llm.call_history[0]
        assert "第一次信息" in first_call[-1].content
        # Only first info in first call (pop cleared it)
        assert "第二次信息" not in first_call[-1].content

        second_call = mock_llm.call_history[1]
        assert "第二次信息" in second_call[-1].content

    async def test_context_in_separate_invoke_calls(self):
        """Each separate invoke gets its own context (ctx.extra is per-invoke)."""
        agent, _ = _make_agent()
        rail = EnvironmentContextRail("独立信息")
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
            assert isinstance(tail, UserMessage)
            assert "独立信息" in tail.content

    async def test_ctx_extra_is_per_invoke_not_persistent(self):
        """ctx.extra['environment_context'] does not persist across invokes."""
        agent, _ = _make_agent()
        captured_extras = []

        class CaptureExtraRail(AgentRail):
            async def before_invoke(self, ctx):
                captured_extras.append(dict(ctx.extra))
            async def before_model_call(self, ctx):
                ctx.extra.setdefault("environment_context", []).append({
                    "content": "信息", "source": "test",
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

        assert "environment_context" not in captured_extras[0]
        assert "environment_context" not in captured_extras[1]


# ============================================================
# 8. Prompt Builder Separation Tests
# ============================================================

class TestPromptBuilderSeparation(unittest.IsolatedAsyncioTestCase):
    """Tests that environment_context content is NOT in system prompt builder output."""

    async def test_context_content_not_in_system_prompt(self):
        """Context content does NOT appear in the system prompt (messages[0])."""
        agent, _ = _make_agent()
        rail = EnvironmentContextRail("时间信息：2026-05-23")
        await agent.register_rail(rail)

        mock_llm = MockLLMModel()
        mock_llm.set_responses([create_text_response("ok")])
        with patch.object(agent, "_get_llm", return_value=mock_llm):
            await agent.invoke({"query": "hello"})

        messages = mock_llm.call_history[0]
        system_msg = messages[0]
        assert isinstance(system_msg, SystemMessage)
        assert "时间信息" not in system_msg.content

    async def test_context_and_builder_section_both_present(self):
        """A rail can inject both a PromptSection AND environment_context."""
        agent, _ = _make_agent()
        rail = ContextPlusBuilderRail(
            ctx_content="独立信息内容",
            section_name="extra_section",
            section_content="额外的section内容",
        )
        await agent.register_rail(rail)

        mock_llm = MockLLMModel()
        mock_llm.set_responses([create_text_response("ok")])
        with patch.object(agent, "_get_llm", return_value=mock_llm):
            await agent.invoke({"query": "hello"})

        messages = mock_llm.call_history[0]
        system_msg = messages[0]
        assert "额外的section内容" in system_msg.content
        tail = messages[-1]
        assert isinstance(tail, UserMessage)
        assert "独立信息内容" in tail.content
        assert "独立信息内容" not in system_msg.content

    async def test_original_system_prompt_unaffected_by_context(self):
        """Original system prompt content is unchanged when context is added."""
        agent, _ = _make_agent()
        rail = EnvironmentContextRail("外部信息")
        await agent.register_rail(rail)

        agent_no_ctx, _ = _make_agent()

        mock_llm_1 = MockLLMModel()
        mock_llm_1.set_responses([create_text_response("ok")])

        mock_llm_2 = MockLLMModel()
        mock_llm_2.set_responses([create_text_response("ok")])

        with patch.object(agent, "_get_llm", return_value=mock_llm_1):
            await agent.invoke({"query": "hello"})

        with patch.object(agent_no_ctx, "_get_llm", return_value=mock_llm_2):
            await agent_no_ctx.invoke({"query": "hello"})

        sys_with = mock_llm_1.call_history[0][0].content
        sys_without = mock_llm_2.call_history[0][0].content
        assert sys_with == sys_without


# ============================================================
# 9. After-Model-Call Visibility Tests
# ============================================================

class TestAfterModelCallVisibility(unittest.IsolatedAsyncioTestCase):
    """Tests that after_model_call hooks can inspect the appended context."""

    async def test_after_model_call_sees_context_in_messages(self):
        """after_model_call hook can see the context in ctx.inputs.messages."""
        agent, _ = _make_agent()
        rail = EnvironmentContextRail("可见信息")
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
        assert isinstance(tail, UserMessage)
        assert "可见信息" in tail.content

    async def test_after_model_call_messages_matches_llm_call(self):
        """ctx.inputs.messages in after_model_call matches what LLM received."""
        agent, _ = _make_agent()
        rail = EnvironmentContextRail("一致性信息")
        inspect_rail = InspectAfterModelCallRail()
        await agent.register_rail(rail)
        await agent.register_rail(inspect_rail)

        mock_llm = MockLLMModel()
        mock_llm.set_responses([create_text_response("ok")])
        with patch.object(agent, "_get_llm", return_value=mock_llm):
            await agent.invoke({"query": "hello"})

        llm_messages = mock_llm.call_history[0]
        hook_messages = inspect_rail.captured_messages[0]
        assert len(hook_messages) == len(llm_messages)
        assert hook_messages[-1].content == llm_messages[-1].content


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v", "-s"])