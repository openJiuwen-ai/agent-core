# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""单元测试：ExternalMemoryRail"""

from __future__ import annotations

import pytest

from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    InvokeInputs,
    ModelCallInputs,
    RunContext,
    RunKind,
)
from openjiuwen.harness.deep_agent import DeepAgent
from openjiuwen.harness.rails.memory.external_memory_rail import ExternalMemoryRail
from openjiuwen.core.memory.external.provider import MemoryProvider
from openjiuwen.harness.prompts.prompt_attachment_manager import PromptAttachmentManager


class MockInputs:
    """测试用 Mock Inputs"""

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class MockCallbackContext:
    """测试用 Mock AgentCallbackContext"""

    def __init__(self, inputs):
        self.inputs = inputs
        self.extra = {}
        self.agent = None
        self.session = None


class MockSession:
    def get_session_id(self) -> str:
        return "sess1"


class MockAgent:
    def __init__(self) -> None:
        self.prompt_attachment_manager = PromptAttachmentManager()


class MockPromptBuilder:
    language = "cn"

    def __init__(self) -> None:
        self.added_sections = []
        self.removed_sections = []

    def add_section(self, section):
        self.added_sections.append(section)

    def remove_section(self, section_name):
        self.removed_sections.append(section_name)


class MockMemoryProvider(MemoryProvider):
    """测试用 Mock Provider"""

    def __init__(self):
        self._initialized = False
        self.prefetch_calls = []
        self.sync_turn_calls = []

    @property
    def name(self) -> str:
        return "mock_provider"

    def is_available(self) -> bool:
        return self._initialized

    async def initialize(self, **kwargs) -> None:
        self._initialized = True

    def get_tool_schemas(self) -> list[dict]:
        return [
            {
                "name": "memory_search",
                "description": "Search memory",
                "parameters": {"type": "object", "properties": {}}
            },
        ]

    async def handle_tool_call(self, tool_name: str, args: dict) -> str:
        return '{"result": "success"}'

    async def prefetch(self, query: str, **kwargs) -> str:
        self.prefetch_calls.append({"query": query, "kwargs": kwargs})
        return f"Memory context for: {query}"

    async def sync_turn(self, user_msg: str, assistant_msg: str, **kwargs) -> None:
        self.sync_turn_calls.append({
            "user_msg": user_msg,
            "assistant_msg": assistant_msg,
            "kwargs": kwargs
        })

    def system_prompt_block(self) -> str:
        return "Use memory_search tool."

    @property
    def is_initialized(self) -> bool:
        return self._initialized


class TestResolveUserTextForMemory:
    """Test _resolve_user_text_for_memory method."""

    def test_only_query(self):
        """Scenario 1: Only query field."""
        inputs = MockInputs(query="test query")
        ctx = MockCallbackContext(inputs)

        result = ExternalMemoryRail._resolve_user_text_for_memory(ctx)
        assert result == "test query"

    def test_only_messages(self):
        """Scenario 2: Only messages user messages."""      
        inputs = MockInputs(messages=[
            {"role": "assistant", "content": "response"},
            {"role": "user", "content": "test message"}
        ])
        ctx = MockCallbackContext(inputs)

        result = ExternalMemoryRail._resolve_user_text_for_memory(ctx)
        assert result == "test message"

    def test_both_query_and_messages(self):
        """Scenario 3: Both query and messages, priority query."""
        inputs = MockInputs(
            query="query value",
            messages=[
                {"role": "user", "content": "message value"}
            ]
        )
        ctx = MockCallbackContext(inputs)

        result = ExternalMemoryRail._resolve_user_text_for_memory(ctx)
        assert result == "query value"

    def test_both_empty(self):
        """Scenario 4: Both query and messages are empty."""
        inputs = MockInputs()
        ctx = MockCallbackContext(inputs)

        result = ExternalMemoryRail._resolve_user_text_for_memory(ctx)
        assert result == ""

    def test_messages_with_list_content(self):
        """Scenario 5: messages content is a list."""
        inputs = MockInputs(messages=[
            {"role": "user", "content": [
                {"type": "text", "text": "hello world"}
            ]}
        ])
        ctx = MockCallbackContext(inputs)

        result = ExternalMemoryRail._resolve_user_text_for_memory(ctx)
        assert result == "hello world"

    def test_messages_with_multiple_user_take_last(self):
        """Scenario 6: Multiple user messages, take last one."""
        inputs = MockInputs(messages=[
            {"role": "user", "content": "first"},
            {"role": "user", "content": "last"}
        ])
        ctx = MockCallbackContext(inputs)

        result = ExternalMemoryRail._resolve_user_text_for_memory(ctx)
        assert result == "last"


class TestResolveUserTextWithRunContext:
    """Test _resolve_user_text_for_memory with RunContext.extra["raw_query"]."""

    def test_after_invoke_path_ctx_inputs_run_context(self):
        """ctx.inputs.run_context.extra["raw_query"] is returned (after_invoke callback).

        At after_invoke, ctx.inputs is InvokeInputs which has a run_context field,
        so getattr(ctx.inputs, "run_context") returns it directly.
        """
        run_ctx = RunContext(extra={"raw_query": "question"})
        inputs = MockInputs(run_context=run_ctx)
        ctx = MockCallbackContext(inputs)

        result = ExternalMemoryRail._resolve_user_text_for_memory(ctx)
        assert result == "question"

    def test_before_model_call_path_ctx_extra(self):
        """ctx.extra["run_context"].extra["raw_query"] is returned (before_model_call callback).

        At before_model_call, ReActAgent replaces ctx.inputs with ModelCallInputs
        (which has no run_context attr). ReActAgent._inner_invoke bridges run_context
        into ctx.extra["run_context"] instead. Without this fallback, prefetch()
        would lose access to raw_query.
        """
        run_ctx = RunContext(extra={"raw_query": "hello from extra"})
        inputs = MockInputs()
        ctx = MockCallbackContext(inputs)
        ctx.extra = {"run_context": run_ctx}

        result = ExternalMemoryRail._resolve_user_text_for_memory(ctx)
        assert result == "hello from extra"

    def test_raw_query_priority_over_query_and_messages(self):
        """raw_query wins when BOTH query and messages are also present.

        This is the single priority test: raw_query > query > messages.
        No need for separate "over query" and "over messages" tests.
        """
        run_ctx = RunContext(extra={"raw_query": "clean query"})
        inputs = MockInputs(
            query='你收到一条消息：\n{"content": "wrapped"}',
            messages=[{"role": "user", "content": "message content"}],
            run_context=run_ctx,
        )
        ctx = MockCallbackContext(inputs)

        result = ExternalMemoryRail._resolve_user_text_for_memory(ctx)
        assert result == "clean query"

    def test_ctx_inputs_preferred_over_ctx_extra(self):
        """When both ctx.inputs.run_context and ctx.extra["run_context"] exist,
        ctx.inputs.run_context takes priority (after_invoke over before_model_call)."""
        run_ctx_inputs = RunContext(extra={"raw_query": "from inputs"})
        run_ctx_extra = RunContext(extra={"raw_query": "from extra"})
        inputs = MockInputs(run_context=run_ctx_inputs)
        ctx = MockCallbackContext(inputs)
        ctx.extra = {"run_context": run_ctx_extra}

        result = ExternalMemoryRail._resolve_user_text_for_memory(ctx)
        assert result == "from inputs"

    @pytest.mark.parametrize(
        ("raw_query_value", "extra_without_key", "description"),
        [
            # Sub-case A: raw_query is empty string → stripped to "" → skipped
            ("", False, "empty_string"),
            # Sub-case B: raw_query key absent from extra → .get returns "" → skipped
            ("__missing__", True, "missing_key"),
        ],
        ids=["empty_string", "missing_key"],
    )
    def test_absent_raw_query_falls_back_to_query(self, raw_query_value, extra_without_key, description):
        """When raw_query is absent or empty, resolution falls back to query field."""
        if extra_without_key:
            run_ctx = RunContext(extra={"unrelated": "data"})
        else:
            run_ctx = RunContext(extra={"raw_query": raw_query_value})

        inputs = MockInputs(
            query="wrapped query",
            run_context=run_ctx,
        )
        ctx = MockCallbackContext(inputs)

        result = ExternalMemoryRail._resolve_user_text_for_memory(ctx)
        assert result == "wrapped query"


class TestRunContextTransformationChain:
    """Integration test: verify RunContext survives the REAL ReActAgent pipeline.

    Unlike the unit tests above which mock ctx state directly, these tests
    create a real ReActAgent, pass inputs through the actual _inner_invoke
    code path, and verify that run_context lands in ctx.extra at
    before_model_call time.

    Chain exercised:
        inputs dict with "run_context"
          → ReActAgent._inner_invoke()   → ctx.extra["run_context"]
            → ReActAgent._call_model()   → ctx.inputs = ModelCallInputs
              → before_model_call rail captures ctx state
    """

    @staticmethod
    def _make_agent():
        """Create a minimal ReActAgent with mock model config."""
        import os
        from openjiuwen.core.single_agent import AgentCard, ReActAgent, ReActAgentConfig
        from openjiuwen.core.foundation.llm import ModelRequestConfig, ModelClientConfig

        os.environ.setdefault("LLM_SSL_VERIFY", "false")
        card = AgentCard(description="test agent")
        config = ReActAgentConfig(
            model_config_obj=ModelRequestConfig(model="gpt-3.5-turbo"),
            model_client_config=ModelClientConfig(
                client_provider="OpenAI",
                api_key="test-key",
                api_base="http://test-base",
                verify_ssl=False,
            ),
            prompt_template=[{"role": "system", "content": "You are a test assistant."}],
        )
        return ReActAgent(card=card).configure(config)

    @pytest.mark.asyncio
    async def test_run_context_lands_in_ctx_extra_at_before_model_call(self):
        """run_context passed via invoke dict arrives in ctx.extra at before_model_call.

        This exercises the REAL code path in ReActAgent._inner_invoke (line 1292):
            ctx.extra["run_context"] = inputs.get("run_context", "")
        """
        from unittest.mock import patch
        from openjiuwen.core.single_agent.rail.base import AgentRail
        from tests.unit_tests.fixtures.mock_llm import MockLLMModel, create_text_response

        agent = self._make_agent()

        captured = []

        class CaptureRail(AgentRail):
            async def before_model_call(self, ctx):
                captured.append({
                    "inputs_type": type(ctx.inputs).__name__,
                    "has_run_context_attr": hasattr(ctx.inputs, "run_context"),
                    "extra_run_context": ctx.extra.get("run_context"),
                })

        await agent.register_rail(CaptureRail())

        run_ctx = RunContext(extra={"raw_query": "question"})
        mock_llm = MockLLMModel()
        mock_llm.set_responses([create_text_response("done")])

        with patch.object(agent, "_get_llm", return_value=mock_llm):
            await agent.invoke({"query": "test query", "run_context": run_ctx})

        assert len(captured) == 1
        state = captured[0]
        # ctx.inputs is ModelCallInputs at before_model_call (no run_context attr)
        assert state["inputs_type"] == "ModelCallInputs"
        assert state["has_run_context_attr"] is False
        # run_context arrived in ctx.extra through the real _inner_invoke path
        assert state["extra_run_context"] is run_ctx
        assert state["extra_run_context"].extra["raw_query"] == "question"

    @pytest.mark.asyncio
    async def test_resolve_user_text_reads_run_context_from_ctx_extra(self):
        """_resolve_user_text_for_memory reads raw_query from ctx.extra after real pipeline.

        End-to-end: invoke dict → _inner_invoke → ctx.extra → _resolve_user_text_for_memory.
        """
        from unittest.mock import patch
        from openjiuwen.core.single_agent.rail.base import AgentRail
        from tests.unit_tests.fixtures.mock_llm import MockLLMModel, create_text_response

        agent = self._make_agent()

        resolved = []

        class ResolveRail(AgentRail):
            async def before_model_call(self, ctx):
                resolved.append(
                    ExternalMemoryRail._resolve_user_text_for_memory(ctx)
                )

        await agent.register_rail(ResolveRail())

        run_ctx = RunContext(extra={"raw_query": "raw query"})
        mock_llm = MockLLMModel()
        mock_llm.set_responses([create_text_response("done")])

        with patch.object(agent, "_get_llm", return_value=mock_llm):
            await agent.invoke({"query": "test", "run_context": run_ctx})

        assert len(resolved) == 1
        assert resolved[0] == "raw query"

    @pytest.mark.asyncio
    async def test_no_run_context_falls_back_to_messages(self):
        """Without run_context, _resolve_user_text_for_memory falls back to messages.

        At before_model_call, ctx.inputs is ModelCallInputs which has messages
        (containing the user query) but no query attr. The method falls through
        to the messages path and returns the user message content.
        """
        from unittest.mock import patch
        from openjiuwen.core.single_agent.rail.base import AgentRail
        from tests.unit_tests.fixtures.mock_llm import MockLLMModel, create_text_response

        agent = self._make_agent()

        resolved = []

        class ResolveRail(AgentRail):
            async def before_model_call(self, ctx):
                resolved.append(
                    ExternalMemoryRail._resolve_user_text_for_memory(ctx)
                )

        await agent.register_rail(ResolveRail())

        mock_llm = MockLLMModel()
        mock_llm.set_responses([create_text_response("done")])

        with patch.object(agent, "_get_llm", return_value=mock_llm):
            await agent.invoke({"query": "test"})

        # No run_context → falls through to messages in ModelCallInputs → "test"
        assert resolved == ["test"]


class TestExtractAssistantOutput:
    """_extract_assistant_output 方法测试"""

    def test_result_with_output_key(self):
        """Scenario 1: result.output format."""
        inputs = MockInputs(result={"output": "assistant response"})
        ctx = MockCallbackContext(inputs)

        result = ExternalMemoryRail._extract_assistant_output(ctx)
        assert result == "assistant response"

    def test_result_with_message_content(self):
        """Scenario 2: result.message.content format."""
        inputs = MockInputs(result={
            "message": {"content": "assistant response"}
        })
        ctx = MockCallbackContext(inputs)

        result = ExternalMemoryRail._extract_assistant_output(ctx)
        assert result == "assistant response"

    def test_result_with_content_key(self):
        """Scenario 3: result.content format."""
        inputs = MockInputs(result={"content": "assistant response"})
        ctx = MockCallbackContext(inputs)

        result = ExternalMemoryRail._extract_assistant_output(ctx)
        assert result == "assistant response"

    def test_result_missing(self):
        """Scenario 4: result is missing."""
        inputs = MockInputs()
        ctx = MockCallbackContext(inputs)

        result = ExternalMemoryRail._extract_assistant_output(ctx)
        assert result == ""

    def test_result_with_unknown_keys(self):
        """Scenario 5: result has unknown keys."""
        inputs = MockInputs(result={"unknown": "value", "other": 123})
        ctx = MockCallbackContext(inputs)

        result = ExternalMemoryRail._extract_assistant_output(ctx)
        assert result == ""


class TestBuildMemoryContextBlock:
    """Test _build_memory_context_block method."""

    def test_build_memory_context(self):
        """Scenario 1: Build memory context block."""
        raw = "Previous conversation context"
        result = ExternalMemoryRail._build_memory_context_block(raw)

        assert "<memory-context>" in result
        assert "Previous conversation context" in result
        assert "</memory-context>" in result
        assert "NOT new user input" in result


@pytest.mark.asyncio
async def test_after_invoke_skips_heartbeat_runs():
    provider = MockMemoryProvider()
    rail = ExternalMemoryRail(provider)
    rail._initialized = True
    inputs = MockInputs(
        query="health check",
        result={"output": "healthy", "result_type": "answer"},
        run_kind=RunKind.HEARTBEAT,
    )

    await rail.after_invoke(MockCallbackContext(inputs))
    if rail._sync_task is not None:
        await rail._sync_task

    assert provider.sync_turn_calls == []


@pytest.mark.asyncio
async def test_after_invoke_skips_cron_runs():
    provider = MockMemoryProvider()
    rail = ExternalMemoryRail(provider)
    rail._initialized = True
    inputs = MockInputs(
        query="scheduled check",
        result={"output": "ok", "result_type": "answer"},
        run_kind=RunKind.CRON,
    )

    await rail.after_invoke(MockCallbackContext(inputs))
    if rail._sync_task is not None:
        await rail._sync_task

    assert provider.sync_turn_calls == []


@pytest.mark.asyncio
async def test_after_invoke_skips_empty_assistant_output():
    provider = MockMemoryProvider()
    rail = ExternalMemoryRail(provider)
    rail._initialized = True
    inputs = MockInputs(
        query="remember this",
        result={"unknown": "value"},
        run_kind=RunKind.NORMAL,
    )

    await rail.after_invoke(MockCallbackContext(inputs))
    if rail._sync_task is not None:
        await rail._sync_task

    assert provider.sync_turn_calls == []


@pytest.mark.asyncio
async def test_external_memory_prefetch_goes_to_prompt_attachment_not_system_section():
    provider = MockMemoryProvider()
    rail = ExternalMemoryRail(provider)
    agent = MockAgent()
    prompt_builder = MockPromptBuilder()
    ctx = MockCallbackContext(MockInputs(query="what did we decide?"))
    ctx.agent = agent
    ctx.session = MockSession()

    rail._initialized = True
    rail.system_prompt_builder = prompt_builder
    rail.attachment_manager = agent.prompt_attachment_manager

    await rail.before_model_call(ctx)

    assert "external_memory_prefetch" in prompt_builder.removed_sections
    assert prompt_builder.added_sections == []
    items = await agent.prompt_attachment_manager.collect_for_session("sess1")
    assert [item.id for item in items] == [
        "session.sess1.external_memory_prefetch"
    ]
    assert items[0].kind.value == "memory"
    assert items[0].source == "agent_core.external_memory_rail"
    assert "Memory context for: what did we decide?" in (items[0].content or "")
