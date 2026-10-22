# coding: utf-8
"""Tests for attribution prompts, wake text, budget rail and status rail."""

from __future__ import annotations

import json
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest

from openjiuwen.agent_evolving.attribution.agent import (
    ContextBudgetRail,
    LlmStatusRail,
    build_attribution_initial_prompt,
    build_attribution_system_prompt,
    build_wake_text,
    render_signal_block,
    render_subjects_block,
)
from openjiuwen.agent_evolving.attribution.model import (
    DiagnosticSignal,
    RewardOverall,
    SignalSource,
    SubjectRef,
)
from openjiuwen.core.single_agent.rail.base import ModelCallInputs

pytestmark = pytest.mark.asyncio


def _signal(node_id: str = "main:1") -> DiagnosticSignal:
    return DiagnosticSignal.create("发生了错误", source=SignalSource.KEYWORD_TOOL, node_ids=[node_id])


def _subjects() -> list[SubjectRef]:
    return [SubjectRef(id="main", display_name="Main Agent", kind="main_agent")]


def test_build_attribution_system_prompt_languages_and_swarm() -> None:
    zh = build_attribution_system_prompt("zh")
    en = build_attribution_system_prompt("en")
    assert "轨迹归因 Agent" in zh
    assert "trajectory attribution agent" in en
    assert "swarm" not in zh
    swarm_zh = build_attribution_system_prompt("zh", swarm_mode=True)
    assert "多 Agent 协作" in swarm_zh
    assert swarm_zh.index("轨迹归因 Agent") < swarm_zh.index("多 Agent 协作")
    swarm_en = build_attribution_system_prompt("en", swarm_mode=True)
    assert "Multi-Agent (swarm)" in swarm_en


def test_render_signal_block_verbatim() -> None:
    block = render_signal_block([_signal()])
    parsed = json.loads(block)
    assert parsed["signal_id"].startswith("diag-")
    assert parsed["node_ids"] == ["main:1"]


def test_render_subjects_block() -> None:
    block = render_subjects_block(_subjects())
    assert block == "- main | Main Agent | main_agent"


def test_build_initial_prompt_zh_and_en() -> None:
    reward = RewardOverall(comment="整体不错", total_score=87)
    zh = build_attribution_initial_prompt(
        task_description="写报告",
        reward_overall=reward,
        signals=[_signal()],
        subjects=_subjects(),
        language="zh",
    )
    assert "## 任务描述" in zh
    assert "总分：87" in zh
    assert "## 待归因诊断信号" in zh
    assert "main | Main Agent" in zh
    assert "all_done=true" in zh
    en = build_attribution_initial_prompt(
        task_description="write report",
        reward_overall=reward,
        signals=[_signal()],
        subjects=_subjects(),
        language="en",
    )
    assert "## Task Description" in en
    assert "Total score: 87" in en


def test_build_initial_prompt_without_reward() -> None:
    zh = build_attribution_initial_prompt(
        task_description="写报告",
        reward_overall=None,
        signals=[],
        subjects=_subjects(),
        language="zh",
    )
    assert "本次无 Reward 总评" in zh
    assert "（无）" in zh
    en = build_attribution_initial_prompt(
        task_description="x",
        reward_overall=None,
        signals=[],
        subjects=_subjects(),
        language="en",
    )
    assert "(no reward overall this run)" in en


def test_build_wake_text_contains_check_json() -> None:
    check = {"all_done": False, "pending_signal_ids": ["diag-a"], "missing_artifact_improvement": True}
    zh = build_wake_text(check, "zh")
    assert '"pending_signal_ids": ["diag-a"]' in zh
    assert "原样复述" in zh
    en = build_wake_text(check, "en")
    assert "pending_signal_ids" in en
    assert "echo that" in en


@dataclass
class _Usage:
    total_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class _Response:
    usage_metadata: _Usage | None = None
    content: str = ""
    tool_calls: list[Any] | None = None


class _FakeCtx:
    def __init__(self, response: _Response | None = None, iteration: int = 1) -> None:
        self.inputs = ModelCallInputs(response=response, react_iteration=iteration)
        self._force_finish = None

    def request_force_finish(self, payload: dict[str, Any]) -> None:
        self._force_finish = payload


async def test_context_budget_rail_bills_and_force_finishes() -> None:
    rail = ContextBudgetRail(1000)
    ctx = _FakeCtx(_Response(usage_metadata=_Usage(total_tokens=600)))
    await rail.after_model_call(ctx)
    assert rail.spent == 600
    assert not rail.exceeded
    await rail.after_model_call(ctx)
    assert rail.exceeded
    assert ctx._force_finish is not None
    assert "exceeded" in str(ctx._force_finish)


async def test_context_budget_rail_refuses_when_exceeded() -> None:
    rail = ContextBudgetRail(100)
    rail.exceeded = True
    ctx = _FakeCtx()
    await rail.before_model_call(ctx)
    assert ctx._force_finish is not None
    assert "refused" in str(ctx._force_finish)


def test_context_budget_rail_rejects_nonpositive() -> None:
    with pytest.raises(ValueError):
        ContextBudgetRail(0)


class _CollectingSink:
    def __init__(self) -> None:
        self.events: list[Any] = []

    def report(self, event: Any) -> None:
        self.events.append(event)


async def test_llm_status_rail_running_then_succeeded() -> None:
    sink = _CollectingSink()
    rail = LlmStatusRail(sink, stage="attribution", agent_id="attribution-agent", model="m1", counter=[0])
    ctx = _FakeCtx(_Response(content="hello world" * 50, usage_metadata=_Usage(total_tokens=12)), iteration=3)
    await rail.before_model_call(ctx)
    assert len(sink.events) == 1
    running = sink.events[0]
    assert running.status == "running"
    assert running.request_id == "llm-1"
    assert running.stage == "attribution"
    assert running.call_kind == "agent"
    assert running.iteration == 3
    await rail.after_model_call(ctx)
    succeeded = sink.events[1]
    assert succeeded.status == "succeeded"
    assert succeeded.request_id == "llm-1"
    assert succeeded.response_preview == ("hello world" * 50)[:100]
    assert succeeded.token_usage["total_tokens"] == 12


async def test_llm_status_rail_failure_path() -> None:
    sink = _CollectingSink()
    rail = LlmStatusRail(sink, stage="reward", agent_id="reward-agent", model="m2", counter=[0])
    ctx = _FakeCtx()
    await rail.before_model_call(ctx)
    failed_ctx = SimpleNamespace(error="boom", inputs=ctx.inputs)
    await rail.on_model_exception(failed_ctx)
    failed = sink.events[1]
    assert failed.status == "failed"
    assert failed.error == "boom"


class _RaisingSink:
    def report(self, event: Any) -> None:
        del event
        raise RuntimeError("sink broken")


async def test_llm_status_rail_sink_failure_ignored() -> None:
    rail = LlmStatusRail(_RaisingSink(), stage="attribution", agent_id="a", model="m", counter=[0])
    ctx = _FakeCtx(_Response(content="x"))
    await rail.before_model_call(ctx)
    await rail.after_model_call(ctx)
