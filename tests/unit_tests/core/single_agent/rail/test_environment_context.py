# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Full-coverage tests for environment-context injection via ctx.extra.

Verifies that rails write to ctx.extra["environment_context"], and
_railed_model_call consumes it with pop() and folds it into the system
prompt (appended after prompt_builder.build() output, wrapped in
<environment_context> XML tags). E sits at the end of the system string so
the stable base-prompt prefix stays KV-cache-hit when env content changes;
pop() prevents multi-turn accumulation.
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
        """Empty environment_context list means no env block folded into system."""
        agent, _ = _make_agent()
        ctx = AgentCallbackContext(agent=agent)
        ctx.extra["environment_context"] = []
        assert len(ctx.extra["environment_context"]) == 0


# ============================================================
# 2. Single Context Injection Tests
# ============================================================

class TestSingleContextInjection(unittest.IsolatedAsyncioTestCase):
    """Tests for single environment_context being folded into the system prompt."""

    async def test_single_context_folded_into_system_message(self):
        """Single environment_context is folded into the system message (messages[0])."""
        agent, _ = _make_agent()
        rail = EnvironmentContextRail("当前时间：2026-05-23 10:00:00")
        await agent.register_rail(rail)

        mock_llm = MockLLMModel()
        mock_llm.set_responses([create_text_response("ok")])
        with patch.object(agent, "_get_llm", return_value=mock_llm):
            await agent.invoke({"query": "hello"})

        messages = mock_llm.call_history[0]
        system_msg = messages[0]
        assert isinstance(system_msg, SystemMessage)
        assert "当前时间" in system_msg.content

    async def test_single_context_content_has_environment_context_tag(self):
        """Context content is wrapped in <environment_context> tags inside system."""
        agent, _ = _make_agent()
        content = "# 当前日期与时间\n\n- 当前时间：2026-05-23 10:00:00\n- 当前年份：2026"
        rail = EnvironmentContextRail(content)
        await agent.register_rail(rail)

        mock_llm = MockLLMModel()
        mock_llm.set_responses([create_text_response("ok")])
        with patch.object(agent, "_get_llm", return_value=mock_llm):
            await agent.invoke({"query": "hello"})

        messages = mock_llm.call_history[0]
        system_msg = messages[0]
        assert isinstance(system_msg, SystemMessage)
        assert _wrap(content) in system_msg.content

    async def test_single_context_role_is_system(self):
        """The message carrying context is the system message, role 'system'."""
        agent, _ = _make_agent()
        rail = EnvironmentContextRail("test content")
        await agent.register_rail(rail)

        mock_llm = MockLLMModel()
        mock_llm.set_responses([create_text_response("ok")])
        with patch.object(agent, "_get_llm", return_value=mock_llm):
            await agent.invoke({"query": "hello"})

        messages = mock_llm.call_history[0]
        system_msg = messages[0]
        assert isinstance(system_msg, SystemMessage)
        assert system_msg.role == "system"

    async def test_single_context_in_system_not_at_tail(self):
        """Context lives in messages[0] (system); the tail is the user query, not env."""
        agent, _ = _make_agent()
        rail = EnvironmentContextRail("尾部环境信息")
        await agent.register_rail(rail)

        mock_llm = MockLLMModel()
        mock_llm.set_responses([create_text_response("ok")])
        with patch.object(agent, "_get_llm", return_value=mock_llm):
            await agent.invoke({"query": "hello"})

        messages = mock_llm.call_history[0]
        assert isinstance(messages[0], SystemMessage)
        assert "尾部环境信息" in messages[0].content
        # Tail is the user query, not an env-context user message.
        assert ENV_CTX_TAG_OPEN not in messages[-1].content

    async def test_context_in_first_position_system(self):
        """Context is folded into messages[0], which is the system prompt."""
        agent, _ = _make_agent()
        rail = EnvironmentContextRail("时间：10:00")
        await agent.register_rail(rail)

        mock_llm = MockLLMModel()
        mock_llm.set_responses([create_text_response("ok")])
        with patch.object(agent, "_get_llm", return_value=mock_llm):
            await agent.invoke({"query": "hello"})

        messages = mock_llm.call_history[0]
        assert isinstance(messages[0], SystemMessage)
        assert "时间：10:00" in messages[0].content


# ============================================================
# 3. Multiple Contexts — Merge and Ordering Tests
# ============================================================

class TestMultipleContexts(unittest.IsolatedAsyncioTestCase):
    """Tests for merging multiple environment_context entries into the system prompt."""

    async def test_two_entries_merged_with_double_newline(self):
        """Two entries joined with \\n\\n separator inside <environment_context> in system."""
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
        system_msg = messages[0]
        assert isinstance(system_msg, SystemMessage)
        assert _wrap("时间：10:00\n\n平台：Linux") in system_msg.content

    async def test_three_entries_merged_preserving_order(self):
        """Three entries preserve insertion order when merged into system."""
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
        system_msg = messages[0]
        assert isinstance(system_msg, SystemMessage)
        assert _wrap("第一\n\n第二\n\n第三") in system_msg.content

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
        system_msg = messages[0]
        assert isinstance(system_msg, SystemMessage)
        # Extract the env block body (between the XML tags) to check ordering.
        sys_content = system_msg.content
        start = sys_content.index(ENV_CTX_TAG_OPEN) + len(ENV_CTX_TAG_OPEN)
        end = sys_content.index(ENV_CTX_TAG_CLOSE, start)
        env_body = sys_content[start:end]
        assert env_body.startswith("高优先级信息")

    async def test_two_separate_rails_produce_merged_context(self):
        """Two independently registered rails each write one entry, both in system."""
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
        system_msg = messages[0]
        assert isinstance(system_msg, SystemMessage)
        assert "信息A" in system_msg.content
        assert "信息B" in system_msg.content

    async def test_multiple_entries_folded_into_single_system_message(self):
        """Multiple entries result in exactly ONE system message containing all."""
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
        system_msgs = [m for m in messages if isinstance(m, SystemMessage)]
        assert len(system_msgs) == 1
        sys_content = system_msgs[0].content
        assert "r1" in sys_content
        assert "r2" in sys_content
        assert "r3" in sys_content
        # No env-context user message is appended at the tail.
        env_user_msgs = [m for m in messages if isinstance(m, UserMessage) and ENV_CTX_TAG_OPEN in m.content]
        assert len(env_user_msgs) == 0


# ============================================================
# 4. No-Context Baseline Tests
# ============================================================

class TestNoContextBaseline(unittest.IsolatedAsyncioTestCase):
    """Tests for zero side effects when no environment_context is present."""

    async def test_no_context_no_env_block_in_system(self):
        """Without environment_context, no env block is folded into system."""
        agent, _ = _make_agent()
        mock_llm = MockLLMModel()
        mock_llm.set_responses([create_text_response("ok")])
        with patch.object(agent, "_get_llm", return_value=mock_llm):
            await agent.invoke({"query": "hello"})

        messages = mock_llm.call_history[0]
        assert ENV_CTX_TAG_OPEN not in messages[0].content
        env_user_msgs = [m for m in messages if isinstance(m, UserMessage) and ENV_CTX_TAG_OPEN in m.content]
        assert len(env_user_msgs) == 0

    async def test_empty_context_list_no_env_block(self):
        """Empty environment_context list produces no env block in system."""
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
        # pop() returns empty list, which is falsy → no env block appended.
        assert ENV_CTX_TAG_OPEN not in messages[0].content

    async def test_no_context_message_count_unchanged(self):
        """Without environment_context, messages length matches context window output."""
        agent, _ = _make_agent()
        mock_llm = MockLLMModel()
        mock_llm.set_responses([create_text_response("ok")])
        with patch.object(agent, "_get_llm", return_value=mock_llm):
            await agent.invoke({"query": "hello"})

        messages = mock_llm.call_history[0]
        assert len(messages) == 2  # [SystemMessage(prompt), UserMessage(query)]

    async def test_no_context_base_system_prompt_preserved(self):
        """Base system prompt content is unaffected when no environment_context."""
        agent, _ = _make_agent()
        mock_llm = MockLLMModel()
        mock_llm.set_responses([create_text_response("ok")])
        with patch.object(agent, "_get_llm", return_value=mock_llm):
            await agent.invoke({"query": "hello"})

        messages = mock_llm.call_history[0]
        system_msg = messages[0]
        assert isinstance(system_msg, SystemMessage)
        assert "测试助手" in system_msg.content
        assert ENV_CTX_TAG_OPEN not in system_msg.content


# ============================================================
# 4b. No-Accumulation Regression Tests
# ============================================================

class TestNoAccumulation(unittest.IsolatedAsyncioTestCase):
    """Env block must NOT accumulate in the system string across turns.

    Each model call rebuilds system = prompt_builder.build() + ONE env block
    (sourced from ctx.extra, cleared by pop()). The context window treats
    system_messages as a per-call replacement, not append state. So the env
    block must appear exactly once in every call's system message, regardless
    of how many model calls or invokes precede it.
    """

    @staticmethod
    def _env_block_count(system_content: str) -> int:
        return system_content.count(ENV_CTX_TAG_OPEN)

    async def test_no_accumulation_across_tool_call_loop(self):
        """Two model calls in one invoke: each system has exactly one env block."""
        agent, _ = _make_agent()
        rail = EnvironmentContextRail("环境信息")
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
            system_msg = call_messages[0]
            assert isinstance(system_msg, SystemMessage)
            assert self._env_block_count(system_msg.content) == 1

    async def test_no_accumulation_across_separate_invokes(self):
        """Two separate invokes: each system has exactly one env block."""
        agent, _ = _make_agent()
        rail = EnvironmentContextRail("环境信息")
        await agent.register_rail(rail)

        mock_llm = MockLLMModel()
        mock_llm.set_responses([
            create_text_response("answer 1"),
            create_text_response("answer 2"),
        ])
        with patch.object(agent, "_get_llm", return_value=mock_llm):
            await agent.invoke({"query": "q1"})
            await agent.invoke({"query": "q2"})

        assert mock_llm.call_count == 2
        for call_messages in mock_llm.call_history:
            system_msg = call_messages[0]
            assert isinstance(system_msg, SystemMessage)
            assert self._env_block_count(system_msg.content) == 1

    async def test_no_accumulation_with_dynamic_content(self):
        """Changing env content each call still yields exactly one block per call."""
        agent, _ = _make_agent()
        call_count = {"n": 0}

        class DynamicContextRail(AgentRail):
            async def before_model_call(self, ctx):
                idx = min(call_count["n"], 2)
                ctx.extra.setdefault("environment_context", []).append({
                    "content": f"第{idx + 1}次信息",
                    "source": "dynamic",
                })
                call_count["n"] += 1

        await agent.register_rail(DynamicContextRail())

        mock_llm = MockLLMModel()
        mock_llm.set_responses([
            create_tool_call_response("add", '{"a": 1, "b": 2}'),
            create_tool_call_response("add", '{"a": 3, "b": 4}'),
            create_text_response("done"),
        ])
        with patch.object(agent, "_get_llm", return_value=mock_llm):
            await agent.invoke({"query": "1+2"})

        assert mock_llm.call_count == 3
        for call_messages in mock_llm.call_history:
            system_msg = call_messages[0]
            assert isinstance(system_msg, SystemMessage)
            assert self._env_block_count(system_msg.content) == 1


# ============================================================
# 5. System Prompt Delivery (not subject to history trimming)
# ============================================================

class TestSystemDelivery(unittest.IsolatedAsyncioTestCase):
    """Tests that environment_context folded into system is always delivered.

    The system message is separate from context-window history trimming, so
    the env block survives even when earlier history is trimmed.
    """

    async def test_context_folded_into_system_message(self):
        """Context content is present inside the system message."""
        agent, _ = _make_agent()
        rail = EnvironmentContextRail("不可裁剪的内容")
        await agent.register_rail(rail)

        mock_llm = MockLLMModel()
        mock_llm.set_responses([create_text_response("ok")])
        with patch.object(agent, "_get_llm", return_value=mock_llm):
            await agent.invoke({"query": "hello"})

        messages = mock_llm.call_history[0]
        system_msg = messages[0]
        assert isinstance(system_msg, SystemMessage)
        assert "不可裁剪的内容" in system_msg.content

    async def test_context_only_in_system_not_in_user_messages(self):
        """Context lives in the system message; no env user message is appended."""
        agent, _ = _make_agent()
        rail = EnvironmentContextRail("独立信息")
        await agent.register_rail(rail)

        mock_llm = MockLLMModel()
        mock_llm.set_responses([create_text_response("ok")])

        with patch.object(agent, "_get_llm", return_value=mock_llm):
            await agent.invoke({"query": "hello"})

        messages = mock_llm.call_history[0]
        assert "独立信息" in messages[0].content
        env_user_msgs = [m for m in messages if isinstance(m, UserMessage) and ENV_CTX_TAG_OPEN in m.content]
        assert len(env_user_msgs) == 0

    async def test_context_survives_even_if_context_trims_history(self):
        """Context always delivered via system, even if context engine trims earlier messages."""
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
            system_msg = call_messages[0]
            assert isinstance(system_msg, SystemMessage)
            assert "始终送达的信息" in system_msg.content


# ============================================================
# 6. Edge-Case Content Tests
# ============================================================

class TestEdgeCaseContent(unittest.IsolatedAsyncioTestCase):
    """Tests for edge-case environment_context content folded into system."""

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
        system_msg = messages[0]
        assert "<system-reminder>" in system_msg.content
        assert "当前时间" in system_msg.content

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
        system_msg = messages[0]
        assert "2026年5月23日" in system_msg.content
        assert "日本語テスト" in system_msg.content

    async def test_context_with_newlines_in_content(self):
        """Content with embedded newlines is preserved in the system message."""
        agent, _ = _make_agent()
        content = "# 时间\n\n- 当前时间：10:00\n- 当前年份：2026"
        rail = EnvironmentContextRail(content)
        await agent.register_rail(rail)

        mock_llm = MockLLMModel()
        mock_llm.set_responses([create_text_response("ok")])
        with patch.object(agent, "_get_llm", return_value=mock_llm):
            await agent.invoke({"query": "hello"})

        messages = mock_llm.call_history[0]
        system_msg = messages[0]
        assert "# 时间" in system_msg.content
        assert "当前时间：10:00" in system_msg.content

    async def test_context_with_empty_string_content_skipped(self):
        """Empty-string context content is skipped; no env block folded into system."""
        agent, _ = _make_agent()
        rail = EnvironmentContextRail("")
        await agent.register_rail(rail)

        mock_llm = MockLLMModel()
        mock_llm.set_responses([create_text_response("ok")])
        with patch.object(agent, "_get_llm", return_value=mock_llm):
            await agent.invoke({"query": "hello"})

        messages = mock_llm.call_history[0]
        system_msg = messages[0]
        assert isinstance(system_msg, SystemMessage)
        assert ENV_CTX_TAG_OPEN not in system_msg.content

    async def test_mixed_empty_and_non_empty_entries_only_non_empty_folded(self):
        """Entries with empty content are dropped; non-empty ones are folded in order."""
        agent, _ = _make_agent()
        rail = MultiContextRail([
            {"content": "有效信息", "source": "a"},
            {"content": "", "source": "b"},
            {"content": "另一条有效信息", "source": "c"},
        ])
        await agent.register_rail(rail)

        mock_llm = MockLLMModel()
        mock_llm.set_responses([create_text_response("ok")])
        with patch.object(agent, "_get_llm", return_value=mock_llm):
            await agent.invoke({"query": "hello"})

        messages = mock_llm.call_history[0]
        system_msg = messages[0]
        assert isinstance(system_msg, SystemMessage)
        # Only the two non-empty entries are joined (empty dropped), in order.
        assert _wrap("有效信息\n\n另一条有效信息") in system_msg.content

    async def test_context_with_long_content(self):
        """Long context content (1000+ chars) is preserved intact in system."""
        agent, _ = _make_agent()
        long_content = "详细信息：" + "A" * 1000
        rail = EnvironmentContextRail(long_content)
        await agent.register_rail(rail)

        mock_llm = MockLLMModel()
        mock_llm.set_responses([create_text_response("ok")])
        with patch.object(agent, "_get_llm", return_value=mock_llm):
            await agent.invoke({"query": "hello"})

        messages = mock_llm.call_history[0]
        system_msg = messages[0]
        assert isinstance(system_msg, SystemMessage)
        inner = system_msg.content.replace(ENV_CTX_TAG_OPEN, "").replace(ENV_CTX_TAG_CLOSE, "")
        assert len(inner) >= 1000

    async def test_context_with_markdown_table_content(self):
        """Markdown table content in context is preserved in system."""
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
        system_msg = messages[0]
        assert "| 操作" in system_msg.content
        assert "|------" in system_msg.content

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
        system_msg = messages[0]
        assert _wrap("第一行\n第二行\n\n第三行\n第四行") in system_msg.content


# ============================================================
# 7. Multi-Call Lifecycle Tests (pop prevents accumulation)
# ============================================================

class TestMultiCallLifecycle(unittest.IsolatedAsyncioTestCase):
    """Tests for environment_context across tool-call loops and multiple invocations."""

    async def test_context_in_tool_call_loop_first_and_second_model_call(self):
        """Context present in system of both model calls of a tool-call loop."""
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
            system_msg = call_messages[0]
            assert isinstance(system_msg, SystemMessage)
            assert "时间：10:00" in system_msg.content

    async def test_context_refreshes_on_each_model_call_no_accumulation(self):
        """pop() clears after each call; no stale content from previous call in system."""
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
        assert "第一次信息" in first_call[0].content
        # Only first info in first call (pop cleared it)
        assert "第二次信息" not in first_call[0].content

        second_call = mock_llm.call_history[1]
        assert "第二次信息" in second_call[0].content

    async def test_context_in_separate_invoke_calls(self):
        """Each separate invoke gets its own context folded into system."""
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
            system_msg = call_messages[0]
            assert isinstance(system_msg, SystemMessage)
            assert "独立信息" in system_msg.content

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
# 8. Base Prompt Preservation Tests
# ============================================================

class TestBasePromptPreservation(unittest.IsolatedAsyncioTestCase):
    """Tests that folding env context into system preserves the base prompt prefix.

    The base prompt (prompt_builder output) must remain a stable prefix of the
    system string; the env block is appended after it so cache prefix stability
    is maintained when env content changes.
    """

    async def test_context_folded_into_system_after_base_prompt(self):
        """Context content appears in the system message alongside the base prompt."""
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
        assert "时间信息" in system_msg.content

    async def test_context_and_builder_section_both_in_system(self):
        """A rail can inject both a PromptSection AND environment_context into system."""
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
        assert isinstance(system_msg, SystemMessage)
        assert "额外的section内容" in system_msg.content
        assert "独立信息内容" in system_msg.content

    async def test_base_system_prompt_prefix_preserved_with_context(self):
        """With context, system = base_prefix + env_block; base prefix is unchanged."""
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
        # Base prefix is preserved: system-with starts with system-without,
        # and the env block is appended after it.
        assert sys_with.startswith(sys_without)
        assert "外部信息" in sys_with
        assert "外部信息" not in sys_without


# ============================================================
# 9. After-Model-Call Visibility Tests
# ============================================================

class TestAfterModelCallVisibility(unittest.IsolatedAsyncioTestCase):
    """Tests that after_model_call hooks can inspect the context in ctx.inputs.messages."""

    async def test_after_model_call_sees_context_in_system(self):
        """after_model_call hook can see the context in ctx.inputs.messages[0] (system)."""
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
        system_msg = messages[0]
        assert isinstance(system_msg, SystemMessage)
        assert "可见信息" in system_msg.content

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
        assert hook_messages[0].content == llm_messages[0].content


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v", "-s"])
