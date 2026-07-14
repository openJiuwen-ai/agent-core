# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for DeepAgent._normalize_inputs — raw_query extraction."""

from __future__ import annotations

import pytest

from openjiuwen.core.single_agent.rail.base import InvokeInputs, RunKind, RunContext
from openjiuwen.harness.deep_agent import DeepAgent


# ---------------------------------------------------------------------------
# Helpers — lightweight stubs so we can call _normalize_inputs without
# initializing a full DeepAgent.
# ---------------------------------------------------------------------------

class _StubDeepAgent:
    """Minimal stand-in that exposes _normalize_inputs."""

    # DeepAgent._normalize_inputs is a regular (non-classmethod) method that
    # only reads `inputs`, so we can borrow it directly.
    _normalize_inputs = DeepAgent._normalize_inputs


# ---------------------------------------------------------------------------
# Tests — raw_query extraction into RunContext.extra
# ---------------------------------------------------------------------------

class TestNormalizeInputsRawQuery:
    """Test that _normalize_inputs merges raw_query into RunContext.extra.

    Scenario matrix — each test covers a unique cell:
    ┌─────────────────────────────────────────┬──────────────────────────────────┐
    │ test                                     │ what it verifies                 │
    ├─────────────────────────────────────────┼──────────────────────────────────┤
    │ raw_query creates RunContext, no side    │ RunContext created, run_kind=None│
    │ effects on query/run_kind                │ query keeps wrapped prompt       │
    │ raw_query merges into existing cron      │ coexists with RunKind.CRON       │
    │ raw_query overrides stale raw_query      │ top-level wins over nested       │
    │ empty raw_query, no run                  │ no RunContext created            │
    │ baseline: no raw_query, no run           │ run_context stays None           │
    │ run without raw_query                    │ existing RunContext untouched    │
    │ string input                             │ non-dict input path works        │
    └─────────────────────────────────────────┴──────────────────────────────────┘
    """

    @pytest.fixture()
    def agent(self):
        return _StubDeepAgent()

    def test_raw_query_creates_run_context_without_side_effects(self, agent):
        """raw_query alone creates RunContext; run_kind stays None; query keeps wrapped prompt.

        Consolidates three previously-separate tests:
        - creates RunContext (was test_raw_query_creates_run_context)
        - does not affect run_kind (was test_raw_query_does_not_affect_run_kind)
        - query field preserves wrapped prompt (was test_raw_query_preserved_in_query_field)
        """
        inputs = {
            "query": '你收到一条消息：\n{"content": "帮我写一个排序函数"}',
            "raw_query": "帮我写一个排序函数",
        }
        result = agent._normalize_inputs(inputs)

        assert isinstance(result, InvokeInputs)
        # RunContext created with raw_query
        assert result.run_context is not None
        assert result.run_context.extra.get("raw_query") == "帮我写一个排序函数"
        # run_kind stays None (no "run" key → no RunKind created)
        assert result.run_kind is None
        # query field keeps the wrapped prompt, not the raw query
        assert result.query == '你收到一条消息：\n{"content": "帮我写一个排序函数"}'

    def test_raw_query_merges_into_existing_cron_run_context(self, agent):
        """raw_query merges into an existing RunContext from run.context.

        Verifies cron data is preserved and raw_query is added alongside it.
        """
        inputs = {
            "query": "some query",
            "run": {
                "kind": "cron",
                "context": {"extra": {"cron": {"id": "daily"}}},
            },
            "raw_query": "定时任务内容",
        }
        result = agent._normalize_inputs(inputs)

        assert result.run_kind == RunKind.CRON
        assert result.run_context is not None
        # Existing cron data preserved
        assert result.run_context.extra.get("cron") == {"id": "daily"}
        # raw_query merged in
        assert result.run_context.extra.get("raw_query") == "定时任务内容"

    def test_raw_query_overrides_stale_raw_query(self, agent):
        """If RunContext.extra already has raw_query, the top-level one wins."""
        inputs = {
            "query": "some query",
            "run": {
                "kind": "heartbeat",
                "context": {"extra": {"raw_query": "stale"}},
            },
            "raw_query": "fresh",
        }
        result = agent._normalize_inputs(inputs)

        assert result.run_context.extra.get("raw_query") == "fresh"

    def test_empty_raw_query_no_run_creates_no_run_context(self, agent):
        """Empty raw_query without run does not create a RunContext."""
        inputs = {
            "query": "some query",
            "raw_query": "",
        }
        result = agent._normalize_inputs(inputs)

        assert result.run_context is None

    def test_baseline_no_raw_query_no_run(self, agent):
        """No raw_query and no run → run_context and run_kind both stay None."""
        inputs = {"query": "some query"}
        result = agent._normalize_inputs(inputs)

        assert result.run_context is None
        assert result.run_kind is None

    def test_run_without_raw_query_preserves_original(self, agent):
        """Run with context but no raw_query preserves original RunContext untouched."""
        inputs = {
            "query": "some query",
            "run": {
                "kind": "cron",
                "context": {"extra": {"cron": {"id": "x"}}},
            },
        }
        result = agent._normalize_inputs(inputs)

        assert result.run_kind == RunKind.CRON
        assert result.run_context.extra.get("cron") == {"id": "x"}
        assert "raw_query" not in result.run_context.extra

    def test_string_input_no_run_context(self, agent):
        """String input (non-dict) → no run_context, query is the string itself."""
        result = agent._normalize_inputs("just a string")

        assert result.query == "just a string"
        assert result.run_context is None
