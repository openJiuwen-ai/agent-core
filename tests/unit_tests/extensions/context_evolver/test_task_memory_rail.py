# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Unit tests for ContextEvolutionRail.

Tests are organised by component:

    TestInit                       — __init__ and startup behaviour
    TestBeforeTaskIteration        — memory retrieval and prompt injection
    TestAfterTaskIteration         — prompt restore and memories_used annotation
    TestAutoSummarize              — trajectory buffer and auto_summarize in after_task_iteration
    TestFormatTrajectory           — message-list → trajectory string
    TestSummarizeTrajectories      — trajectory → memory store update
    TestSummarizeTrajectoriesInput — dataclass defaults
    TestMemoryServiceProperties    — service properties exposed to the rail
    TestRoundTrip                  — full before → after cycle

Run:
    uv run python tests/unit_tests/extensions/context_evolver/test_context_evolution_rail.py
"""

import os
import sys
import tempfile
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

# ---------------------------------------------------------------------------
# Project root on sys.path — required for direct "uv run python" execution
# ---------------------------------------------------------------------------
_agent_core_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))
))))
if _agent_core_root not in sys.path:
    sys.path.append(_agent_core_root)

import pytest

from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackEvent,
    AgentCallbackContext,
)
from openjiuwen.core.foundation.llm import UserMessage, AssistantMessage, ToolMessage
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.harness.rails.evolution.context_evolution_rail import (
    ContextEvolutionRail,
)
from openjiuwen.extensions.context_evolver.service import (
    SummarizeTrajectoriesInput,
    format_trajectory,
    summarize_trajectories,
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_SYS_CONTENT = "You are a helpful assistant."
_USER_CONTENT = "{query}"


def _make_prompt_template():
    return [
        {"role": "system", "content": _SYS_CONTENT},
        {"role": "user", "content": _USER_CONTENT},
    ]


def _make_agent(prompt_template=None):
    agent = MagicMock()
    agent.config = MagicMock()
    agent.config.prompt_template = prompt_template or _make_prompt_template()
    agent.ability_manager = MagicMock()
    agent.ability_manager.add = MagicMock()
    # Hook uses getattr(agent, "react_agent", agent); point back to self so
    # inner_agent inherits the same config.prompt_template.
    agent.react_agent = agent
    return agent


def _make_ctx(query="test query", prompt_template=None, result=None):
    agent = _make_agent(prompt_template)
    # Use SimpleNamespace so getattr(ctx.inputs, "query", None) works correctly.
    inputs = SimpleNamespace(
        query=query,
        result=result,
        retrieval_query=None,
    )
    return AgentCallbackContext(
        agent=agent,
        event=AgentCallbackEvent.BEFORE_TASK_ITERATION,
        inputs=inputs,
    )


def _make_memory_service(
    memory_string="",
    retrieved_memory=None,
    summarize_result=None,
):
    service = MagicMock()
    service.retrieve = AsyncMock(
        return_value={
            "status": "success",
            "memory_string": memory_string,
            "retrieved_memory": retrieved_memory or [],
        }
    )
    service.summarize = AsyncMock(
        return_value=summarize_result or {"status": "success", "memory": []}
    )
    service.vector_store = MagicMock()
    service.vector_store.get_all = MagicMock(return_value=[])
    service.vector_store.load_node = MagicMock()
    # Properties added in updated TaskMemoryService
    service.summary_algorithm = "ACE"
    service.retrieval_algorithm = "ACE"
    service.persist_type = None
    service.persist_path = "./memories/{algo_name}/{user_id}.json"
    service.persistence_helper = None
    return service


@dataclass
class _SummarizeCfg:
    """Groups the two auto-summarize constructor params to keep _make_middleware ≤5 args."""
    enabled: bool = False
    matts_mode: str = "none"


def _make_middleware(
    _tmp_dir=None,  # kept for call-site compatibility; no longer used directly
    user_id="test_user",
    memory_service=None,
    inject=True,
    summarize_cfg: "_SummarizeCfg | None" = None,
):
    cfg = summarize_cfg or _SummarizeCfg()
    svc = memory_service or _make_memory_service()
    return ContextEvolutionRail(
        user_id=user_id,
        memory_service=svc,
        inject_memories_in_context=inject,
        auto_summarize=cfg.enabled,
        auto_summarize_matts_mode=cfg.matts_mode,
    )


# ===========================================================================
# TestInit
# ===========================================================================

class TestInit:
    @staticmethod
    def test_default_state():
        """Per-invocation fields are zero/None after construction."""
        with tempfile.TemporaryDirectory() as tmp:
            mw = _make_middleware(tmp, user_id="alice")
            assert mw.user_id == "alice"
            assert mw.inject_memories_in_context is True
            assert mw.memories_used == 0
            assert mw.original_prompt_template is None
            assert mw.last_retrieved_query is None
            assert mw.last_retrieval_result is None

    @staticmethod
    def test_inject_false_stored():
        """inject_memories_in_context=False is stored correctly."""
        with tempfile.TemporaryDirectory() as tmp:
            mw = _make_middleware(tmp, inject=False)
            assert mw.inject_memories_in_context is False

    @staticmethod
    def test_priority_lower_than_default():
        """Middleware priority is lower (numerically) than the default 100."""
        with tempfile.TemporaryDirectory() as tmp:
            mw = _make_middleware(tmp)
            assert mw.priority < 100

    @staticmethod
    def test_auto_summarize_defaults():
        """auto_summarize defaults to True; matts_mode defaults to 'none'."""
        svc = _make_memory_service()
        mw = ContextEvolutionRail(user_id="u", memory_service=svc)
        assert mw.auto_summarize is True
        assert mw.auto_summarize_matts_mode == "none"

    @staticmethod
    def test_auto_summarize_stored():
        """auto_summarize and matts_mode constructor args are stored."""
        with tempfile.TemporaryDirectory() as tmp:
            mw = _make_middleware(tmp, summarize_cfg=_SummarizeCfg(enabled=True, matts_mode="parallel"))
            assert mw.auto_summarize is True
            assert mw.auto_summarize_matts_mode == "parallel"

    @staticmethod
    def test_pending_tools_empty_at_init():
        """_pending_tools is empty and _tools_applied is False at construction."""
        with tempfile.TemporaryDirectory() as tmp:
            mw = _make_middleware(tmp)
            assert mw.pending_tools == []
            assert mw.tools_applied is False
            assert mw.agent is None

    @staticmethod
    def test_load_memories_called_on_init():
        """memory_service.load_memories is called with user_id during __init__."""
        svc = _make_memory_service()
        mw = ContextEvolutionRail(user_id="alice", memory_service=svc)
        svc.load_memories.assert_called_once_with("alice")


# ===========================================================================
# TestBeforeTaskIteration
# ===========================================================================

class TestBeforeTaskIteration:

    @staticmethod
    @pytest.mark.asyncio
    async def test_injects_memory_into_system_prompt():
        """Memories are prepended to every system-role entry in the template."""
        with tempfile.TemporaryDirectory() as tmp:
            svc = _make_memory_service(
                memory_string="Use pdb for debugging.",
                retrieved_memory=[{"content": "Use pdb."}],
            )
            mw = _make_middleware(tmp, memory_service=svc)
            ctx = _make_ctx(query="debug Python")
            original_sys = ctx.agent.config.prompt_template[0]["content"]

            await mw.before_task_iteration(ctx)

            new_sys = ctx.agent.config.prompt_template[0]["content"]
            assert "Use pdb for debugging." in new_sys
            assert mw.memories_used == 1
            # original template saved for restoration
            assert mw.original_prompt_template is not None
            assert mw.original_prompt_template[0]["content"] == original_sys

    @staticmethod
    @pytest.mark.asyncio
    async def test_retrieval_occurs_even_when_inject_disabled():
        """Retrieval runs and memories_used is set even if injection is off."""
        with tempfile.TemporaryDirectory() as tmp:
            svc = _make_memory_service(
                memory_string="Some memory.",
                retrieved_memory=[{"content": "memory"}],
            )
            mw = _make_middleware(tmp, memory_service=svc, inject=False)
            ctx = _make_ctx(query="test")
            original_sys = ctx.agent.config.prompt_template[0]["content"]

            await mw.before_task_iteration(ctx)

            assert mw.memories_used == 1
            assert mw.original_prompt_template is None  # no injection
            # system prompt must be unchanged
            assert ctx.agent.config.prompt_template[0]["content"] == original_sys

    @staticmethod
    @pytest.mark.asyncio
    async def test_no_injection_when_no_memories_retrieved():
        """When the service returns zero memories no injection takes place."""
        with tempfile.TemporaryDirectory() as tmp:
            svc = _make_memory_service(memory_string="", retrieved_memory=[])
            mw = _make_middleware(tmp, memory_service=svc)
            ctx = _make_ctx(query="test")

            await mw.before_task_iteration(ctx)

            assert mw.memories_used == 0
            assert mw.original_prompt_template is None

    @staticmethod
    @pytest.mark.asyncio
    async def test_empty_query_skips_retrieval():
        """An empty query causes before_task_iteration to return without calling retrieve."""
        with tempfile.TemporaryDirectory() as tmp:
            svc = _make_memory_service()
            mw = _make_middleware(tmp, memory_service=svc)

            await mw.before_task_iteration(_make_ctx(query=""))

            svc.retrieve.assert_not_called()
            assert mw.memories_used == 0

    @staticmethod
    @pytest.mark.asyncio
    async def test_caches_retrieval_for_same_query():
        """A second before_task_iteration with the identical query reuses cached results."""
        with tempfile.TemporaryDirectory() as tmp:
            svc = _make_memory_service(
                memory_string="cached memory",
                retrieved_memory=[{"content": "cached"}],
            )
            mw = _make_middleware(tmp, memory_service=svc)

            await mw.before_task_iteration(_make_ctx(query="same query"))
            calls_after_first = svc.retrieve.call_count

            await mw.before_task_iteration(_make_ctx(query="same query"))

            assert svc.retrieve.call_count == calls_after_first  # no second call

    @staticmethod
    @pytest.mark.asyncio
    async def test_cache_bypassed_for_different_query():
        """Different queries each trigger a fresh retrieve call."""
        with tempfile.TemporaryDirectory() as tmp:
            svc = _make_memory_service(
                memory_string="mem",
                retrieved_memory=[{"content": "mem"}],
            )
            mw = _make_middleware(tmp, memory_service=svc)

            await mw.before_task_iteration(_make_ctx(query="query A"))
            await mw.before_task_iteration(_make_ctx(query="query B"))

            assert svc.retrieve.call_count == 2

    @staticmethod
    @pytest.mark.asyncio
    async def test_skips_injection_when_agent_has_no_config():
        """Missing config on the agent is handled without raising."""
        class _BareAgent:
            pass

        with tempfile.TemporaryDirectory() as tmp:
            svc = _make_memory_service(
                memory_string="mem",
                retrieved_memory=[{"content": "mem"}],
            )
            mw = _make_middleware(tmp, memory_service=svc)
            ctx = _make_ctx(query="test")
            ctx.agent = _BareAgent()  # no config attribute

            await mw.before_task_iteration(ctx)  # must not raise

            assert mw.original_prompt_template is None

    @staticmethod
    @pytest.mark.asyncio
    async def test_preserves_non_system_messages():
        """Non-system entries in the template are copied verbatim."""
        with tempfile.TemporaryDirectory() as tmp:
            svc = _make_memory_service(
                memory_string="injected",
                retrieved_memory=[{"content": "injected"}],
            )
            mw = _make_middleware(tmp, memory_service=svc)
            template = [
                {"role": "system", "content": "System prompt."},
                {"role": "user", "content": "{query}"},
            ]
            ctx = _make_ctx(query="test", prompt_template=template)

            await mw.before_task_iteration(ctx)

            assert ctx.agent.config.prompt_template[1]["role"] == "user"
            assert ctx.agent.config.prompt_template[1]["content"] == "{query}"

    @staticmethod
    @pytest.mark.asyncio
    async def test_resets_per_invocation_state_at_start():
        """Stale memories_used and original_prompt_template are cleared first."""
        with tempfile.TemporaryDirectory() as tmp:
            svc = _make_memory_service()  # returns no memories
            mw = _make_middleware(tmp, memory_service=svc)
            mw.memories_used = 99
            mw.original_prompt_template = [{"role": "system", "content": "stale"}]

            await mw.before_task_iteration(_make_ctx(query="test"))

            assert mw.memories_used == 0
            assert mw.original_prompt_template is None

    @staticmethod
    @pytest.mark.asyncio
    async def test_uses_retrieval_query_when_provided():
        """inputs.retrieval_query is used for retrieval instead of query."""
        with tempfile.TemporaryDirectory() as tmp:
            svc = _make_memory_service(
                memory_string="mem",
                retrieved_memory=[{"content": "mem"}],
            )
            mw = _make_middleware(tmp, memory_service=svc)
            ctx = AgentCallbackContext(
                agent=_make_agent(),
                event=AgentCallbackEvent.BEFORE_TASK_ITERATION,
                inputs=SimpleNamespace(
                    query="general question",
                    retrieval_query="specific retrieval",
                    result=None,
                ),
            )

            await mw.before_task_iteration(ctx)

            call_kwargs = svc.retrieve.call_args.kwargs
            assert call_kwargs.get("query") == "specific retrieval"

    @staticmethod
    @pytest.mark.asyncio
    async def test_captures_agent_reference_on_first_call():
        """_agent is set to ctx.agent on the first before_task_iteration call."""
        with tempfile.TemporaryDirectory() as tmp:
            mw = _make_middleware(tmp)
            assert mw.agent is None
            ctx = _make_ctx(query="test")

            await mw.before_task_iteration(ctx)

            assert mw.agent is ctx.agent

    @staticmethod
    @pytest.mark.asyncio
    async def test_saves_current_query_for_after_task_iteration():
        """_current_query is set to the query extracted from inputs."""
        with tempfile.TemporaryDirectory() as tmp:
            mw = _make_middleware(tmp)
            ctx = _make_ctx(query="my question")

            await mw.before_task_iteration(ctx)

            assert mw.current_query == "my question"


# ===========================================================================
# TestAfterTaskIteration
# ===========================================================================

class TestAfterTaskIteration:

    @staticmethod
    @pytest.mark.asyncio
    async def test_restores_original_prompt_template():
        """after_task_iteration reverts the system prompt to its pre-injection state."""
        with tempfile.TemporaryDirectory() as tmp:
            svc = _make_memory_service(
                memory_string="injected mem",
                retrieved_memory=[{"content": "mem"}],
            )
            mw = _make_middleware(tmp, memory_service=svc)
            result = {}
            ctx = _make_ctx(query="test", result=result)
            original_sys = ctx.agent.config.prompt_template[0]["content"]

            await mw.before_task_iteration(ctx)
            assert "injected mem" in ctx.agent.config.prompt_template[0]["content"]

            await mw.after_task_iteration(ctx)

            assert ctx.agent.config.prompt_template[0]["content"] == original_sys
            assert mw.original_prompt_template is None

    @staticmethod
    @pytest.mark.asyncio
    async def test_attaches_memories_used_to_result():
        """after_task_iteration writes memories_used into the result dict."""
        with tempfile.TemporaryDirectory() as tmp:
            svc = _make_memory_service(
                memory_string="mem",
                retrieved_memory=[{"content": "a"}, {"content": "b"}],
            )
            mw = _make_middleware(tmp, memory_service=svc)
            result = {}
            ctx = _make_ctx(query="test", result=result)

            await mw.before_task_iteration(ctx)
            await mw.after_task_iteration(ctx)

            assert result.get("memories_used") == 2

    @staticmethod
    @pytest.mark.asyncio
    async def test_attaches_zero_memories_used_when_none_retrieved():
        """memories_used is 0 when the service returned no memories."""
        with tempfile.TemporaryDirectory() as tmp:
            svc = _make_memory_service()
            mw = _make_middleware(tmp, memory_service=svc)
            result = {}
            ctx = _make_ctx(query="test", result=result)

            await mw.before_task_iteration(ctx)
            await mw.after_task_iteration(ctx)

            assert result.get("memories_used") == 0

    @staticmethod
    @pytest.mark.asyncio
    async def test_after_task_iteration_safe_without_before():
        """after_task_iteration alone must not raise even with no prior injection."""
        with tempfile.TemporaryDirectory() as tmp:
            mw = _make_middleware(tmp)
            result = {}
            ctx = _make_ctx(query="test", result=result)

            await mw.after_task_iteration(ctx)  # called without before_task_iteration

            assert result.get("memories_used") == 0


# ===========================================================================
# TestAutoSummarize
# ===========================================================================

class TestAutoSummarize:

    @staticmethod
    @pytest.mark.asyncio
    async def test_no_auto_summarize_when_disabled():
        """_summarize_trajectories is NOT called when auto_summarize=False."""
        svc = _make_memory_service()
        mw = _make_middleware(memory_service=svc, summarize_cfg=_SummarizeCfg(enabled=False))
        with patch(
            "openjiuwen.harness.rails.evolution.context_evolution_rail._summarize_trajectories",
            new=AsyncMock(),
        ) as mock_summ:
            ctx = _make_ctx(query="test")
            await mw.before_task_iteration(ctx)
            await mw.after_task_iteration(ctx)
            mock_summ.assert_not_called()

    @staticmethod
    @pytest.mark.asyncio
    async def test_summarize_called_immediately_per_trajectory():
        """_summarize_trajectories is called immediately for each trajectory (no buffering)."""
        svc = _make_memory_service()
        mw = _make_middleware(memory_service=svc, summarize_cfg=_SummarizeCfg(enabled=True))
        mw.extract_trajectory = MagicMock(return_value="USER: test\nTHOUGHT: ok")
        with patch(
            "openjiuwen.harness.rails.evolution.context_evolution_rail._evaluate_trial",
            return_value=("success", 1),
        ), patch(
            "openjiuwen.harness.rails.evolution.context_evolution_rail._summarize_trajectories",
            new=AsyncMock(return_value={"memory": []}),
        ) as mock_summ:
            ctx = _make_ctx(query="my question")
            await mw.before_task_iteration(ctx)
            await mw.after_task_iteration(ctx)
            mock_summ.assert_called_once()
        call_params = mock_summ.call_args.args[2]
        assert call_params.query == "my question"
        assert call_params.trajectory == ["USER: test\nTHOUGHT: ok"]
        assert call_params.matts_mode == "none"

    @staticmethod
    @pytest.mark.asyncio
    async def test_summarize_called_per_iteration_not_batched():
        """Each after_task_iteration with a trajectory triggers one summarize call."""
        svc = _make_memory_service()
        mw = _make_middleware(memory_service=svc, summarize_cfg=_SummarizeCfg(enabled=True))
        mw.extract_trajectory = MagicMock(return_value="USER: test\nTHOUGHT: ok")
        with patch(
            "openjiuwen.harness.rails.evolution.context_evolution_rail._evaluate_trial",
            return_value=("success", 1),
        ), patch(
            "openjiuwen.harness.rails.evolution.context_evolution_rail._summarize_trajectories",
            new=AsyncMock(return_value={"memory": []}),
        ) as mock_summ:
            for i in range(2):
                ctx = _make_ctx(query=f"question {i}")
                await mw.before_task_iteration(ctx)
                await mw.after_task_iteration(ctx)
        assert mock_summ.call_count == 2

    @staticmethod
    @pytest.mark.asyncio
    async def test_summarize_uses_evaluate_trial_feedback_and_score():
        """feedback and score from _evaluate_trial are forwarded to _summarize_trajectories."""
        svc = _make_memory_service()
        mw = _make_middleware(memory_service=svc, summarize_cfg=_SummarizeCfg(enabled=True))
        mw.extract_trajectory = MagicMock(return_value="USER: q\nTHOUGHT: a")
        with patch(
            "openjiuwen.harness.rails.evolution.context_evolution_rail._evaluate_trial",
            return_value=("failure", 0),
        ) as mock_eval, patch(
            "openjiuwen.harness.rails.evolution.context_evolution_rail._summarize_trajectories",
            new=AsyncMock(return_value={"memory": []}),
        ) as mock_summ:
            ctx = _make_ctx(query="q")
            await mw.before_task_iteration(ctx)
            await mw.after_task_iteration(ctx)
        mock_eval.assert_called_once_with("q", "USER: q\nTHOUGHT: a")
        call_params = mock_summ.call_args.args[2]
        assert call_params.feedback == ["failure"]
        assert call_params.score == [0]
        assert call_params.matts_mode == "none"

    @staticmethod
    @pytest.mark.asyncio
    async def test_auto_summarize_skipped_when_no_trajectory():
        """When extract_trajectory returns None, _summarize_trajectories is not called."""
        svc = _make_memory_service()
        mw = _make_middleware(memory_service=svc, summarize_cfg=_SummarizeCfg(enabled=True))
        mw.extract_trajectory = MagicMock(return_value=None)
        with patch(
            "openjiuwen.harness.rails.evolution.context_evolution_rail._summarize_trajectories",
            new=AsyncMock(),
        ) as mock_summ:
            ctx = _make_ctx(query="test")
            await mw.before_task_iteration(ctx)
            await mw.after_task_iteration(ctx)
            mock_summ.assert_not_called()

    @staticmethod
    @pytest.mark.asyncio
    async def test_auto_summarize_skipped_when_empty_query():
        """Auto-summarize is not triggered when no query was saved."""
        svc = _make_memory_service()
        mw = _make_middleware(memory_service=svc, summarize_cfg=_SummarizeCfg(enabled=True))
        with patch(
            "openjiuwen.harness.rails.evolution.context_evolution_rail._summarize_trajectories",
            new=AsyncMock(),
        ) as mock_summ:
            ctx = _make_ctx(query="test")
            await mw.after_task_iteration(ctx)  # skip before so current_query stays empty
            mock_summ.assert_not_called()

    @staticmethod
    def test_extract_trajectory_returns_none_when_no_session():
        """extract_trajectory returns None when ctx.session is None."""
        with tempfile.TemporaryDirectory() as tmp:
            mw = _make_middleware(tmp)
            ctx = _make_ctx(query="test")
            ctx.session = None
            result = mw.extract_trajectory(ctx)
            assert result is None

    @staticmethod
    def test_extract_trajectory_returns_none_when_no_context_engine():
        """extract_trajectory returns None when agent lacks context_engine."""
        with tempfile.TemporaryDirectory() as tmp:
            mw = _make_middleware(tmp)
            ctx = _make_ctx(query="test")
            ctx.session = MagicMock()
            ctx.agent = MagicMock(spec=[])  # spec=[] means no attributes
            result = mw.extract_trajectory(ctx)
            assert result is None


# ===========================================================================
# TestFormatTrajectory
# ===========================================================================

class TestFormatTrajectory:

    @staticmethod
    def test_formats_user_message():
        result = format_trajectory([UserMessage(content="Hello!")])
        assert result == "USER: Hello!"

    @staticmethod
    def test_formats_assistant_thought():
        result = format_trajectory(
            [AssistantMessage(content="I need to think.", tool_calls=None)]
        )
        assert result == "THOUGHT: I need to think."

    @staticmethod
    def test_formats_assistant_tool_call():
        tc = ToolCall(id="call_1", type="function", name="search", arguments='{"q": "py"}')
        result = format_trajectory([AssistantMessage(content="", tool_calls=[tc])])
        assert 'ACTION: search({"q": "py"})' in result

    @staticmethod
    def test_formats_tool_message_as_observation():
        result = format_trajectory([ToolMessage(tool_call_id="call_1", content="42")])
        assert result == "OBSERVATION: 42"

    @staticmethod
    def test_formats_full_conversation_in_order():
        msgs = [
            UserMessage(content="How to debug Python?"),
            AssistantMessage(content="Use pdb.", tool_calls=None),
            ToolMessage(tool_call_id="c1", content="pdb result"),
        ]
        lines = format_trajectory(msgs).splitlines()
        assert lines[0].startswith("USER:")
        assert lines[1].startswith("THOUGHT:")
        assert lines[2].startswith("OBSERVATION:")

    @staticmethod
    def test_strips_task_prefix_from_user_message():
        result = format_trajectory([UserMessage(content="Task:\nFind the bug.")])
        assert "Task:" not in result
        assert "Find the bug." in result

    @staticmethod
    def test_strips_related_experience_prefix():
        content = (
            "What should I do?\n"
            "Some Related Experience to help you complete the task\nUse pdb."
        )
        result = format_trajectory([UserMessage(content=content)])
        assert "Some Related Experience" not in result
        assert "What should I do?" in result

    @staticmethod
    def test_empty_message_list():
        assert format_trajectory([]) == ""

    @staticmethod
    def test_multiple_tool_calls_in_one_assistant_message():
        tc1 = ToolCall(id="c1", type="function", name="tool_a", arguments="{}")
        tc2 = ToolCall(id="c2", type="function", name="tool_b", arguments="{}")
        result = format_trajectory([AssistantMessage(content="", tool_calls=[tc1, tc2])])
        assert "ACTION: tool_a({})" in result
        assert "ACTION: tool_b({})" in result


# ===========================================================================
# TestSummarizeTrajectories
# ===========================================================================

class TestSummarizeTrajectories:

    @staticmethod
    @pytest.mark.asyncio
    async def test_calls_service_summarize():
        """summarize_trajectories delegates to memory_service.summarize with correct kwargs."""
        svc = _make_memory_service(summarize_result={"status": "success", "memory": []})
        result = await summarize_trajectories(
            svc,
            "test_user",
            SummarizeTrajectoriesInput(
                query="debug Python",
                trajectory="USER: debug?\nTHOUGHT: Use pdb.",
                matts_mode="none",
                feedback=["success"],
            ),
        )
        svc.summarize.assert_called_once()
        kw = svc.summarize.call_args.kwargs
        assert kw["user_id"] == "test_user"
        assert kw["matts"] == "none"
        assert kw["query"] == "debug Python"
        assert kw["trajectories"] == ["USER: debug?\nTHOUGHT: Use pdb."]
        assert result is not None

    @staticmethod
    @pytest.mark.asyncio
    async def test_sequential_mode_uses_only_last_trajectory():
        """matts_mode='sequential' keeps only the last trajectory."""
        svc = _make_memory_service(summarize_result={"status": "success", "memory": []})
        await summarize_trajectories(
            svc,
            "test_user",
            SummarizeTrajectoriesInput(
                query="q",
                trajectory=["traj1", "traj2", "traj3"],
                matts_mode="sequential",
                feedback=["success", "failure", "success"],
            ),
        )
        kw = svc.summarize.call_args.kwargs
        assert kw["trajectories"] == ["traj3"]
        assert kw["matts"] == "sequential"
        assert kw["query"] == "q"
        assert kw["user_id"] == "test_user"

    @staticmethod
    @pytest.mark.asyncio
    async def test_summarize_returns_result_without_manual_persist():
        """summarize_trajectories returns the service result.

        Persistence is now delegated to PersistMemoryOp inside the
        TaskMemoryService summary pipeline — the rail no longer writes files.
        """
        svc = _make_memory_service(summarize_result={"status": "success", "memory": [{"content": "new"}]})
        result = await summarize_trajectories(
            svc,
            "test_user",
            SummarizeTrajectoriesInput(
                query="q", trajectory="t", matts_mode="none", feedback=["success"]
            ),
        )
        assert result == {"status": "success", "memory": [{"content": "new"}]}

    @staticmethod
    @pytest.mark.asyncio
    async def test_explicit_scores_used_when_provided():
        """When params.score is set it is forwarded to summarize (ReMe algo)."""
        svc = _make_memory_service(summarize_result={"memory": []})

        def _cfg_get(key, default=None):
            return "reme" if key == "SUMMARY_ALGO" else default

        with patch(
            "openjiuwen.extensions.context_evolver.service.trajectory_generator.memory_config"
        ) as mock_cfg:
            mock_cfg.get = _cfg_get
            await summarize_trajectories(
                svc,
                "test_user",
                SummarizeTrajectoriesInput(
                    query="q",
                    trajectory="t",
                    matts_mode="none",
                    feedback=["success"],
                    score=[5],
                ),
            )
        kw = svc.summarize.call_args.kwargs
        assert kw["score"] == [5]


# ===========================================================================
# TestSummarizeTrajectoriesInput
# ===========================================================================

class TestSummarizeTrajectoriesInput:

    @staticmethod
    def test_optional_fields_default_to_none():
        params = SummarizeTrajectoriesInput(query="q", trajectory="t", matts_mode="none")
        assert params.feedback is None
        assert params.score is None
        assert params.ground_truth is None

    @staticmethod
    def test_ground_truth_stored():
        params = SummarizeTrajectoriesInput(
            query="q", trajectory="t", matts_mode="none", ground_truth="expected answer"
        )
        assert params.ground_truth == "expected answer"

    @staticmethod
    def test_explicit_scores_stored():
        params = SummarizeTrajectoriesInput(
            query="q", trajectory="t", matts_mode="none", score=[1, 0, 1]
        )
        assert params.score == [1, 0, 1]

    @staticmethod
    def test_list_trajectory_accepted():
        params = SummarizeTrajectoriesInput(
            query="q", trajectory=["t1", "t2"], matts_mode="none"
        )
        assert params.trajectory == ["t1", "t2"]


# ===========================================================================
# TestMemoryServiceProperties — service properties exposed to the rail
# ===========================================================================

class TestMemoryServiceProperties:

    @staticmethod
    def test_mock_has_summary_algorithm():
        """_make_memory_service sets summary_algorithm used by _load_existing_memories."""
        svc = _make_memory_service()
        assert svc.summary_algorithm == "ACE"

    @staticmethod
    def test_mock_has_retrieval_algorithm():
        svc = _make_memory_service()
        assert svc.retrieval_algorithm == "ACE"

    @staticmethod
    def test_mock_persist_type_none_by_default():
        """persist_type is None when no persistence is configured."""
        svc = _make_memory_service()
        assert svc.persist_type is None

    @staticmethod
    def test_mock_persistence_helper_none_by_default():
        """persistence_helper is None when persist_type is not set."""
        svc = _make_memory_service()
        assert svc.persistence_helper is None

    @staticmethod
    def test_rail_calls_load_memories_on_init():
        """load_memories is called with user_id during ContextEvolutionRail construction."""
        svc = _make_memory_service()
        mw = ContextEvolutionRail(user_id="u", memory_service=svc)
        svc.load_memories.assert_called_once_with("u")


# ===========================================================================
# TestRoundTrip — before_task_iteration → after_task_iteration integration
# ===========================================================================

class TestRoundTrip:

    @staticmethod
    @pytest.mark.asyncio
    async def test_prompt_fully_restored_and_result_annotated():
        """Full cycle: memory injected, agent used, prompt restored, result updated."""
        with tempfile.TemporaryDirectory() as tmp:
            svc = _make_memory_service(
                memory_string="Injected memory content.",
                retrieved_memory=[{"content": "Injected memory content."}],
            )
            mw = _make_middleware(tmp, memory_service=svc)
            result = {}
            ctx = _make_ctx(query="How to debug?", result=result)
            original_sys = ctx.agent.config.prompt_template[0]["content"]

            await mw.before_task_iteration(ctx)
            assert "Injected memory content." in ctx.agent.config.prompt_template[0]["content"]

            await mw.after_task_iteration(ctx)
            assert ctx.agent.config.prompt_template[0]["content"] == original_sys
            assert mw.original_prompt_template is None
            assert result.get("memories_used") == 1

    @staticmethod
    @pytest.mark.asyncio
    async def test_multiple_sequential_invocations_are_independent():
        """State is clean after each full before → after cycle."""
        with tempfile.TemporaryDirectory() as tmp:
            svc = _make_memory_service(
                memory_string="mem",
                retrieved_memory=[{"content": "mem"}],
            )
            mw = _make_middleware(tmp, memory_service=svc)

            for i in range(3):
                result = {}
                ctx = _make_ctx(query=f"unique query {i}", result=result)
                # Flush cache so each invocation triggers a real retrieve
                mw.last_retrieved_query = None

                await mw.before_task_iteration(ctx)
                await mw.after_task_iteration(ctx)

                assert result.get("memories_used") == 1
                assert mw.original_prompt_template is None

    @staticmethod
    @pytest.mark.asyncio
    async def test_no_inject_cycle_leaves_prompt_clean():
        """With inject=False the prompt is never touched in either direction."""
        with tempfile.TemporaryDirectory() as tmp:
            svc = _make_memory_service(
                memory_string="mem",
                retrieved_memory=[{"content": "mem"}],
            )
            mw = _make_middleware(tmp, memory_service=svc, inject=False)
            result = {}
            ctx = _make_ctx(query="test", result=result)
            original_sys = ctx.agent.config.prompt_template[0]["content"]

            await mw.before_task_iteration(ctx)
            await mw.after_task_iteration(ctx)

            assert ctx.agent.config.prompt_template[0]["content"] == original_sys
            assert result.get("memories_used") == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
