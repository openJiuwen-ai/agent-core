# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for ReActAgent llm_call_kwargs merge/pop (thinking injection hook)."""

from __future__ import annotations

from types import SimpleNamespace

from openjiuwen.core.single_agent.agents.react_agent import ReActAgent


class TestLlmCallKwargsMerge:
    def test_deep_merge_nested_extra_body(self):
        base = {"extra_body": {"foo": 1}, "temperature": 0.5}
        overlay = {"extra_body": {"thinking": {"type": "disabled"}}}
        merged = ReActAgent._deep_merge_dicts(base, overlay)
        assert merged["temperature"] == 0.5
        assert merged["extra_body"]["foo"] == 1
        assert merged["extra_body"]["thinking"] == {"type": "disabled"}
        # base must stay unchanged
        assert "thinking" not in base["extra_body"]

    def test_apply_pops_and_merges(self):
        agent = object.__new__(ReActAgent)
        ctx = SimpleNamespace(extra={"llm_call_kwargs": {"extra_body": {"thinking": {"type": "disabled"}}}})
        out = agent._apply_llm_call_kwargs(ctx, {"max_tokens": 10})
        assert out["max_tokens"] == 10
        assert out["extra_body"]["thinking"]["type"] == "disabled"
        assert "llm_call_kwargs" not in ctx.extra

    def test_apply_noop_when_absent(self):
        agent = object.__new__(ReActAgent)
        ctx = SimpleNamespace(extra={})
        base = {"a": 1}
        out = agent._apply_llm_call_kwargs(ctx, base)
        assert out == base

    def test_apply_ignores_non_dict_payload(self):
        agent = object.__new__(ReActAgent)
        ctx = SimpleNamespace(extra={"llm_call_kwargs": "bad"})
        base = {"a": 1}
        out = agent._apply_llm_call_kwargs(ctx, base)
        assert out == base
        assert "llm_call_kwargs" not in ctx.extra

    def test_apply_never_raises_on_bad_ctx(self):
        agent = object.__new__(ReActAgent)
        base = {"a": 1}
        # missing extra attribute
        out = agent._apply_llm_call_kwargs(SimpleNamespace(), base)
        assert out == base
        # extra is not a mapping
        out2 = agent._apply_llm_call_kwargs(SimpleNamespace(extra="x"), base)
        assert out2 == base

    def test_filter_drops_non_allowlisted_top_level(self):
        agent = object.__new__(ReActAgent)
        ctx = SimpleNamespace(
            extra={
                "llm_call_kwargs": {
                    "temperature": 0.1,
                    "extra_body": {"thinking": {"type": "disabled"}},
                    "tools": [{"type": "function"}],
                }
            }
        )
        out = agent._apply_llm_call_kwargs(ctx, {"max_tokens": 8})
        assert out["max_tokens"] == 8
        assert out["extra_body"]["thinking"]["type"] == "disabled"
        assert "temperature" not in out
        assert "tools" not in out

    def test_filter_drops_non_allowlisted_extra_body_keys(self):
        agent = object.__new__(ReActAgent)
        ctx = SimpleNamespace(
            extra={
                "llm_call_kwargs": {
                    "extra_body": {
                        "thinking": {"type": "enabled"},
                        "enable_thinking": True,
                        "reasoning_effort": "high",
                        "evil": {"x": 1},
                        "stream": True,
                    }
                }
            }
        )
        out = agent._apply_llm_call_kwargs(ctx, {})
        assert out["extra_body"]["thinking"]["type"] == "enabled"
        assert out["extra_body"]["enable_thinking"] is True
        assert out["extra_body"]["reasoning_effort"] == "high"
        assert "evil" not in out["extra_body"]
        assert "stream" not in out["extra_body"]

    def test_filter_allows_top_level_reasoning_effort(self):
        agent = object.__new__(ReActAgent)
        ctx = SimpleNamespace(extra={"llm_call_kwargs": {"reasoning_effort": "high"}})
        out = agent._apply_llm_call_kwargs(ctx, {})
        assert out["reasoning_effort"] == "high"

    def test_filter_empty_after_drop_is_noop(self):
        agent = object.__new__(ReActAgent)
        base = {"a": 1}
        ctx = SimpleNamespace(extra={"llm_call_kwargs": {"temperature": 0.2}})
        out = agent._apply_llm_call_kwargs(ctx, base)
        assert out == base
