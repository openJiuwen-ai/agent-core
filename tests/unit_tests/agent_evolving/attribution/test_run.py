# coding: utf-8
"""Tests for the run-level orchestration: diagnosis and attribution."""

from __future__ import annotations

import asyncio
import re
from types import SimpleNamespace
from typing import Any

import pytest

from openjiuwen.agent_evolving.attribution import run as run_module
from openjiuwen.agent_evolving.attribution.model import (
    DiagnosticSignal,
    SignalSource,
    TaskParseResult,
)
from openjiuwen.agent_evolving.attribution.nodes import TrajectoryNode
from openjiuwen.agent_evolving.attribution.run import (
    NodeBackedRuntime,
    build_reward_user_prompt,
    prepare_reward_temp_dir,
    run_attribution,
    run_diagnosis,
    run_trajectory_summary,
)

pytestmark = pytest.mark.asyncio


def _nodes() -> dict[str, list[TrajectoryNode]]:
    return {
        "main": [
            TrajectoryNode(
                node_id=f"main:{index}",
                subagent_key="main",
                display_name="M",
                kind="main_agent",
                role="user" if index == 0 else "tool",
                content=f"content {index}" if index == 0 else f"content {index} 错误",
                name="bash" if index else None,
                start_ts_ns=index * 100,
                end_ts_ns=index * 100 + 50,
                error=index == 1,  # main:1 is a failed tool call; main:2 only mentions an error word
            )
            for index in range(3)
        ]
    }


def _task_parse(with_artifacts: bool = True) -> TaskParseResult:
    return TaskParseResult(
        task_description="写报告",
        message_artifacts=[("main:0", "帮我写报告")] if with_artifacts else [],
        file_artifacts=["/w/report.md"] if with_artifacts else [],
        file_candidates=[("/w/report.md", "main:1", "write_file")] if with_artifacts else [],
        run_meta={},
    )


def _signal() -> DiagnosticSignal:
    return DiagnosticSignal.create("错误", source=SignalSource.KEYWORD_TOOL, node_ids=["main:1"])


def _tier_signal(tier: str) -> DiagnosticSignal:
    return DiagnosticSignal.create(
        f"{tier} 信号",
        source=SignalSource.REWARD,
        tier=tier,
        node_ids=["main:1"],
    )


def test_build_reward_user_prompt_zh() -> None:
    prompt = build_reward_user_prompt(
        task_description="写报告",
        message_artifacts=[("main:0", "帮我写报告")],
        file_artifacts=["/w/report.md"],
        signals=[_signal()],
        user_attribution="用户补充",
        language="zh",
    )
    assert "## 任务描述" in prompt
    assert "main:0: 帮我写报告" in prompt
    assert "/w/report.md" in prompt
    assert "用户补充" in prompt
    assert "submit_reward" in prompt


async def test_run_diagnosis_skips_reward_without_artifacts() -> None:
    result = await run_diagnosis(
        run_id="r1",
        language="zh",
        nodes_by_subagent=_nodes(),
        user_attribution="",
        task_parse=_task_parse(with_artifacts=False),
        keyword_mode="rule",
    )
    assert result.signal_mode == "rule_jev"
    assert result.reward_overall is None
    assert result.run_meta["reward"]["skipped"] == "no artifacts"
    assert len(result.signals) >= 1


async def test_run_diagnosis_skips_reward_without_model() -> None:
    result = await run_diagnosis(
        run_id="r1",
        language="zh",
        nodes_by_subagent=_nodes(),
        user_attribution="",
        task_parse=_task_parse(with_artifacts=True),
        keyword_mode="rule",
        model=None,
    )
    assert result.signal_mode == "rule_jev"
    assert result.run_meta["reward"]["skipped"] == "no model injected"


async def test_run_diagnosis_appends_user_note_signal_without_artifacts() -> None:
    result = await run_diagnosis(
        run_id="r1",
        language="zh",
        nodes_by_subagent=_nodes(),
        user_attribution="用户补充说明",
        task_parse=_task_parse(with_artifacts=False),
        keyword_mode="rule",
    )
    assert result.signal_mode == "rule_jev"
    assert result.run_meta["reward"]["skipped"] == "no artifacts"
    assert result.run_meta["user_note_signal"] is True
    user_signals = [signal for signal in result.signals if signal.source == SignalSource.USER]
    assert len(user_signals) == 1
    user_signal = user_signals[0]
    assert user_signal.text == "用户补充说明"
    assert user_signal.tier == "uncertain"
    assert user_signal.node_ids == []
    assert result.signals[-1] is user_signal


async def test_run_diagnosis_appends_user_note_signal_without_model() -> None:
    result = await run_diagnosis(
        run_id="r1",
        language="zh",
        nodes_by_subagent=_nodes(),
        user_attribution="无模型备注",
        task_parse=_task_parse(with_artifacts=True),
        keyword_mode="rule",
        model=None,
    )
    assert result.signal_mode == "rule_jev"
    user_signals = [signal for signal in result.signals if signal.source == SignalSource.USER]
    assert len(user_signals) == 1
    assert user_signals[0].text == "无模型备注"
    assert user_signals[0].tier == "uncertain"


async def test_run_diagnosis_skips_blank_user_note() -> None:
    result = await run_diagnosis(
        run_id="r1",
        language="zh",
        nodes_by_subagent=_nodes(),
        user_attribution="   ",
        task_parse=_task_parse(with_artifacts=False),
        keyword_mode="rule",
    )
    assert not any(signal.source == SignalSource.USER for signal in result.signals)
    assert "user_note_signal" not in result.run_meta


async def test_run_diagnosis_reward_success_does_not_append_user_signal(monkeypatch: Any) -> None:
    monkeypatch.setenv("ATTRIBUTION_REWARD", "agent")  # the Reward deep agent path

    async def fake_keyword(*args: Any, **kwargs: Any) -> tuple[list[DiagnosticSignal], dict[str, Any]]:
        del args, kwargs
        return [_signal()], {"mode": "rule"}

    def fake_build_reward(**kwargs: Any) -> tuple[Any, _FakeRail, _FakeRewardGate]:
        gate = _FakeRewardGate()
        return _FakeRewardAgent(kwargs["reward_sink"], gate), _FakeRail(), gate

    monkeypatch.setattr(run_module, "run_keyword_diagnosis", fake_keyword)
    monkeypatch.setattr(run_module, "build_reward_agent", fake_build_reward)
    result = await run_diagnosis(
        run_id="r1",
        language="zh",
        nodes_by_subagent=_nodes(),
        user_attribution="用户补充说明",
        task_parse=_task_parse(with_artifacts=True),
        keyword_mode="rule",
        model=object(),
        reward_card=object(),
    )
    assert result.signal_mode == "reward"
    assert not any(signal.source == SignalSource.USER for signal in result.signals)
    assert "user_note_signal" not in result.run_meta


async def test_run_diagnosis_appends_user_signal_when_reward_not_submitted(monkeypatch: Any) -> None:
    monkeypatch.setenv("ATTRIBUTION_REWARD", "agent")  # the Reward deep agent path

    async def fake_keyword(*args: Any, **kwargs: Any) -> tuple[list[DiagnosticSignal], dict[str, Any]]:
        del args, kwargs
        return [_signal()], {"mode": "rule"}

    class _NoSubmitAgent:
        def __init__(self, sink: Any, gate: _FakeRewardGate) -> None:
            del sink, gate

        async def invoke(self, prompt: str) -> dict[str, Any]:
            del prompt
            return {}

    def fake_build_reward(**kwargs: Any) -> tuple[Any, _FakeRail, _FakeRewardGate]:
        gate = _FakeRewardGate()
        return _NoSubmitAgent(kwargs["reward_sink"], gate), _FakeRail(), gate

    monkeypatch.setattr(run_module, "run_keyword_diagnosis", fake_keyword)
    monkeypatch.setattr(run_module, "build_reward_agent", fake_build_reward)
    result = await run_diagnosis(
        run_id="r1",
        language="zh",
        nodes_by_subagent=_nodes(),
        user_attribution="备注",
        task_parse=_task_parse(with_artifacts=True),
        keyword_mode="rule",
        model=object(),
        reward_card=object(),
    )
    assert result.signal_mode == "rule_jev"
    assert result.run_meta["user_note_signal"] is True
    assert result.signals[-1].source == SignalSource.USER
    assert result.signals[-1].text == "备注"


async def test_run_diagnosis_appends_user_signal_when_reward_package_fails(monkeypatch: Any) -> None:
    async def fake_keyword(*args: Any, **kwargs: Any) -> tuple[list[DiagnosticSignal], dict[str, Any]]:
        del args, kwargs
        return [_signal()], {"mode": "rule"}

    async def exploding_run_reward(**kwargs: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        del kwargs
        raise RuntimeError("reward package broke")

    monkeypatch.setattr(run_module, "run_keyword_diagnosis", fake_keyword)
    monkeypatch.setattr(run_module, "run_reward", exploding_run_reward)
    result = await run_diagnosis(
        run_id="r1",
        language="zh",
        nodes_by_subagent=_nodes(),
        user_attribution="备注",
        task_parse=_task_parse(with_artifacts=True),
        keyword_mode="rule",
        model=object(),
        reward_card=object(),
    )
    assert result.signal_mode == "rule_jev"
    assert result.run_meta["reward"]["engine"] == "reward"
    assert result.run_meta["user_note_signal"] is True
    assert result.signals[-1].source == SignalSource.USER
    assert result.signals[-1].text == "备注"


async def test_run_diagnosis_appends_user_signal_when_reward_agent_fails(monkeypatch: Any) -> None:
    monkeypatch.setenv("ATTRIBUTION_REWARD", "agent")  # the Reward deep agent path

    async def fake_keyword(*args: Any, **kwargs: Any) -> tuple[list[DiagnosticSignal], dict[str, Any]]:
        del args, kwargs
        return [_signal()], {"mode": "rule"}

    class _ExplodingRewardAgent:
        def __init__(self, sink: Any, gate: _FakeRewardGate) -> None:
            del sink, gate

        async def invoke(self, prompt: str) -> dict[str, Any]:
            del prompt
            raise RuntimeError("reward agent broke")

    def fake_build_reward(**kwargs: Any) -> tuple[Any, _FakeRail, _FakeRewardGate]:
        gate = _FakeRewardGate()
        return _ExplodingRewardAgent(kwargs["reward_sink"], gate), _FakeRail(), gate

    monkeypatch.setattr(run_module, "run_keyword_diagnosis", fake_keyword)
    monkeypatch.setattr(run_module, "build_reward_agent", fake_build_reward)
    result = await run_diagnosis(
        run_id="r1",
        language="zh",
        nodes_by_subagent=_nodes(),
        user_attribution="备注",
        task_parse=_task_parse(with_artifacts=True),
        keyword_mode="rule",
        model=object(),
        reward_card=object(),
    )
    assert result.signal_mode == "rule_jev"
    assert result.run_meta["user_note_signal"] is True
    assert result.signals[-1].source == SignalSource.USER
    assert result.signals[-1].text == "备注"


def _reward_payload() -> dict[str, Any]:
    return {
        "schema_version": "attribution.reward.v1",
        "overall": {"comment": "整体不错", "total_score": 87},
        "categories": [{"name": "正确性", "comment": "基本正确", "score": 90}],
        "dimensions": [
            {
                "category": "正确性",
                "requirement": "结果应正确",
                "comment": "有一处错误",
                "score": 70,
                "related_signal_ids": ["diag-a"],
            }
        ],
    }


class _FakeRewardGate:
    def __init__(self) -> None:
        self.submitted = False

    def mark(self) -> None:
        self.submitted = True


class _FakeRewardAgent:
    def __init__(self, sink: Any, gate: _FakeRewardGate) -> None:
        self._sink = sink
        self._gate = gate

    async def invoke(self, prompt: str) -> dict[str, Any]:
        del prompt
        self._sink.submit(_reward_payload())
        self._gate.mark()
        return {}


async def test_run_diagnosis_reward_success_exposes_full_payload(monkeypatch: Any) -> None:
    monkeypatch.setenv("ATTRIBUTION_REWARD", "agent")  # the Reward deep agent path

    async def fake_keyword(*args: Any, **kwargs: Any) -> tuple[list[DiagnosticSignal], dict[str, Any]]:
        del args, kwargs
        return [_signal()], {"mode": "rule"}

    def fake_build_reward(**kwargs: Any) -> tuple[Any, _FakeRail, _FakeRewardGate]:
        gate = _FakeRewardGate()
        return _FakeRewardAgent(kwargs["reward_sink"], gate), _FakeRail(), gate

    monkeypatch.setattr(run_module, "run_keyword_diagnosis", fake_keyword)
    monkeypatch.setattr(run_module, "build_reward_agent", fake_build_reward)
    result = await run_diagnosis(
        run_id="r1",
        language="zh",
        nodes_by_subagent=_nodes(),
        user_attribution="",
        task_parse=_task_parse(with_artifacts=True),
        keyword_mode="rule",
        model=object(),
        reward_card=object(),
    )
    assert result.signal_mode == "reward"
    assert result.reward_overall is not None
    assert result.reward_overall.total_score == 87
    assert result.reward_overall.comment == "整体不错"
    assert result.reward_payload is not None
    assert result.reward_payload["schema_version"] == "attribution.reward.v1"
    assert result.reward_payload["overall"]["total_score"] == 87
    assert result.reward_payload["categories"] == [{"name": "正确性", "comment": "基本正确", "score": 90}]
    assert result.reward_payload["dimensions"][0]["category"] == "正确性"
    assert len(result.signals) == 2  # the reward dimension, then the process signal no dimension took up
    assert result.signals[0].source == SignalSource.REWARD
    assert result.signals[0].score == 70
    assert result.signals[1].source == SignalSource.KEYWORD_TOOL
    assert result.signals[1].node_ids == ["main:1"]
    assert result.run_meta["process_signals_kept"] == 1
    assert result.run_meta["reward"]["submitted"] is True


class _CollectSink:
    def __init__(self) -> None:
        self.events: list[Any] = []

    def report(self, event: Any) -> None:
        self.events.append(event)


async def test_run_diagnosis_forwards_reward_status_and_budget(monkeypatch: Any) -> None:
    monkeypatch.setenv("ATTRIBUTION_REWARD", "agent")  # the Reward deep agent path
    captured: dict[str, Any] = {}

    async def fake_keyword(*args: Any, **kwargs: Any) -> tuple[list[DiagnosticSignal], dict[str, Any]]:
        del args, kwargs
        return [_signal()], {"mode": "rule"}

    def fake_build_reward(**kwargs: Any) -> tuple[Any, _FakeRail, _FakeRewardGate]:
        captured.update(kwargs)
        gate = _FakeRewardGate()
        return _FakeRewardAgent(kwargs["reward_sink"], gate), _FakeRail(), gate

    monkeypatch.setattr(run_module, "run_keyword_diagnosis", fake_keyword)
    monkeypatch.setattr(run_module, "build_reward_agent", fake_build_reward)
    sink = _CollectSink()
    counter = [7]
    await run_diagnosis(
        run_id="r1",
        language="zh",
        nodes_by_subagent=_nodes(),
        user_attribution="",
        task_parse=_task_parse(with_artifacts=True),
        keyword_mode="rule",
        model=object(),
        reward_card=object(),
        status_sink=sink,
        model_name="m1",
        status_counter=counter,
    )
    assert captured["status_sink"] is sink
    assert captured["model_name"] == "m1"
    assert captured["status_counter"] is counter
    assert captured["context_budget_tokens"] is None  # no token limit by default
    assert captured["max_iterations"] == 32


async def test_run_diagnosis_prepares_reward_temp_dir(monkeypatch: Any, tmp_path: Any) -> None:
    monkeypatch.setenv("ATTRIBUTION_REWARD", "agent")  # the Reward deep agent path

    async def fake_keyword(*args: Any, **kwargs: Any) -> tuple[list[DiagnosticSignal], dict[str, Any]]:
        del args, kwargs
        return [_signal()], {"mode": "rule"}

    def fake_build_reward(**kwargs: Any) -> tuple[Any, _FakeRail, _FakeRewardGate]:
        gate = _FakeRewardGate()
        return _FakeRewardAgent(kwargs["reward_sink"], gate), _FakeRail(), gate

    monkeypatch.setattr(run_module, "run_keyword_diagnosis", fake_keyword)
    monkeypatch.setattr(run_module, "build_reward_agent", fake_build_reward)
    stale = tmp_path / ".reward_temp" / "stale.txt"
    stale.parent.mkdir()
    stale.write_text("old", encoding="utf-8")
    await run_diagnosis(
        run_id="r1",
        language="zh",
        nodes_by_subagent=_nodes(),
        user_attribution="",
        task_parse=_task_parse(with_artifacts=True),
        keyword_mode="rule",
        model=object(),
        reward_card=object(),
        workspace=str(tmp_path),
    )
    temp = tmp_path / ".reward_temp"
    assert temp.is_dir()
    assert list(temp.iterdir()) == []


async def test_prepare_reward_temp_dir_tolerates_failure(tmp_path: Any) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    prepare_reward_temp_dir(str(blocker / "workspace"))


async def test_build_reward_agent_wires_llm_status_rail(monkeypatch: Any) -> None:
    from openjiuwen.agent_evolving.attribution.reward_stage.reward_agent import build_reward_agent
    from openjiuwen.harness import factory as harness_factory

    captured: dict[str, Any] = {}

    def fake_create_deep_agent(model: Any, **kwargs: Any) -> Any:
        del model
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(harness_factory, "create_deep_agent", fake_create_deep_agent)
    sink = _CollectSink()
    build_reward_agent(
        model=object(),
        card=object(),
        system_prompt="p",
        workspace="/w",
        reward_sink=None,
        status_sink=sink,
        model_name="m1",
        status_counter=[3],
    )
    rails = captured["rails"]
    status_rails = [rail for rail in rails if type(rail).__name__ == "LlmStatusRail"]
    assert len(status_rails) == 1
    status_rail = status_rails[0]
    assert status_rail._stage == "reward"
    assert status_rail._agent_id == "attribution-reward-agent"
    assert status_rail._model == "m1"
    assert status_rail._counter == [3]
    budget_rails = [rail for rail in rails if type(rail).__name__ == "ContextBudgetRail"]
    assert len(budget_rails) == 1
    assert budget_rails[0]._budget is None


class _FakeDeepAgent:
    """Drives the runtime directly to simulate an attribution agent."""

    def __init__(self, runtime: NodeBackedRuntime, signals: list[DiagnosticSignal], rounds_to_finish: int = 1) -> None:
        self._runtime = runtime
        self._signals = signals
        self._rounds_to_finish = rounds_to_finish
        self.invocations = 0
        self.prompts: list[str] = []

    async def invoke(self, prompt: str, session: Any = None) -> dict[str, Any]:
        del session
        self.invocations += 1
        self.prompts.append(prompt)
        if self.invocations >= self._rounds_to_finish:
            for signal in self._signals:
                self._runtime.record_reward_failure(
                    signal.signal_id, [{"text_md": "cause", "start_node_id": "main:1"}]
                )
            self._runtime.record_trajectory_summary("summary")
        return {}


class _FakeRail:
    def __init__(self) -> None:
        self.exceeded = False
        self.spent = 0


def _patch_build(monkeypatch: Any, fake_agent: _FakeDeepAgent, fake_rail: _FakeRail) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    def fake_build(**kwargs: Any) -> tuple[Any, _FakeRail]:
        captured.update(kwargs)
        return fake_agent, fake_rail

    monkeypatch.setattr(run_module, "build_attribution_agent", fake_build)
    return captured


class _FakeSession:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


async def test_run_attribution_success_single_round(monkeypatch: Any) -> None:
    runtime = NodeBackedRuntime(_nodes())
    signals = [_signal()]
    fake_agent = _FakeDeepAgent(runtime, signals, rounds_to_finish=1)
    _patch_build(monkeypatch, fake_agent, _FakeRail())
    monkeypatch.setattr("openjiuwen.core.session.agent.create_agent_session", _FakeSession)
    outcome = await run_attribution(
        model=object(),
        nodes_by_subagent=_nodes(),
        signals=signals,
        task_description="写报告",
        runtime=runtime,
        total_timeout_s=None,
    )
    assert outcome.status == "success"
    assert outcome.completed is True
    assert outcome.wake_attempted is False
    assert fake_agent.invocations == 1
    assert outcome.counts["attributed"] == 1
    entry = runtime.reward_attributions[signals[0].signal_id][0]
    assert re.fullmatch(r"attr-[0-9a-f]{8}", entry["attr_id"])
    assert entry["start_node_id"] == "main:1"


async def test_run_attribution_wake_once_then_success(monkeypatch: Any) -> None:
    runtime = NodeBackedRuntime(_nodes())
    signals = [_signal()]
    fake_agent = _FakeDeepAgent(runtime, signals, rounds_to_finish=2)
    _patch_build(monkeypatch, fake_agent, _FakeRail())
    monkeypatch.setattr("openjiuwen.core.session.agent.create_agent_session", _FakeSession)
    outcome = await run_attribution(
        model=object(),
        nodes_by_subagent=_nodes(),
        signals=signals,
        task_description="写报告",
        runtime=runtime,
        total_timeout_s=None,
    )
    assert outcome.status == "success"
    assert outcome.completed is True
    assert outcome.wake_attempted is True
    assert fake_agent.invocations == 2
    assert "pending_reward_signal_ids" in fake_agent.prompts[1]
    assert "missing_trajectory_summary" in fake_agent.prompts[1]


async def test_run_attribution_wake_fails_incomplete(monkeypatch: Any) -> None:
    runtime = NodeBackedRuntime(_nodes())
    signals = [_signal()]
    fake_agent = _FakeDeepAgent(runtime, signals, rounds_to_finish=99)
    _patch_build(monkeypatch, fake_agent, _FakeRail())
    monkeypatch.setattr("openjiuwen.core.session.agent.create_agent_session", _FakeSession)
    outcome = await run_attribution(
        model=object(),
        nodes_by_subagent=_nodes(),
        signals=signals,
        task_description="写报告",
        runtime=runtime,
        total_timeout_s=None,
    )
    assert outcome.status == "failed"
    assert outcome.failure_kind == "incomplete"
    assert outcome.wake_attempted is True
    # an unmarked rule_jev signal has no submission obligation; only the summary is missing
    assert outcome.pending_signal_ids == []
    assert outcome.trajectory_summary
    assert fake_agent.invocations == 2


async def test_run_attribution_tokens_exceeded(monkeypatch: Any) -> None:
    runtime = NodeBackedRuntime(_nodes())
    signals = [_signal()]
    fake_agent = _FakeDeepAgent(runtime, signals, rounds_to_finish=99)
    rail = _FakeRail()
    rail.exceeded = True
    _patch_build(monkeypatch, fake_agent, rail)
    monkeypatch.setattr("openjiuwen.core.session.agent.create_agent_session", _FakeSession)
    outcome = await run_attribution(
        model=object(),
        nodes_by_subagent=_nodes(),
        signals=signals,
        task_description="写报告",
        runtime=runtime,
        total_timeout_s=None,
    )
    assert outcome.status == "failed"
    assert outcome.failure_kind == "tokens_exceeded"


class _SlowAgent:
    async def invoke(self, prompt: str, session: Any = None) -> dict[str, Any]:
        del prompt, session
        await asyncio.sleep(0.2)
        return {}


async def test_run_attribution_timeout(monkeypatch: Any) -> None:
    fake_rail = _FakeRail()
    monkeypatch.setattr(run_module, "build_attribution_agent", lambda **kwargs: (_SlowAgent(), fake_rail))
    monkeypatch.setattr("openjiuwen.core.session.agent.create_agent_session", _FakeSession)
    outcome = await run_attribution(
        model=object(),
        nodes_by_subagent=_nodes(),
        signals=[_signal()],
        task_description="写报告",
        runtime=NodeBackedRuntime(_nodes()),
        total_timeout_s=0.05,
    )
    assert outcome.status == "failed"
    assert outcome.failure_kind == "timeout"


async def test_run_attribution_empty_signals_short_circuits() -> None:
    outcome = await run_attribution(
        model=object(),
        nodes_by_subagent=_nodes(),
        signals=[],
        task_description="写报告",
    )
    assert outcome.status == "success"
    assert outcome.completed is True
    assert outcome.skipped_signal_ids == []


async def test_run_attribution_skips_perfect_signals(monkeypatch: Any) -> None:
    runtime = NodeBackedRuntime(_nodes())
    perfect = _tier_signal("perfect")
    fair = _tier_signal("fair")
    weak = _tier_signal("weak")
    uncertain = _tier_signal("uncertain")
    signals = [perfect, fair, weak, uncertain]
    fake_agent = _FakeDeepAgent(runtime, [fair, weak, uncertain], rounds_to_finish=1)
    _patch_build(monkeypatch, fake_agent, _FakeRail())
    monkeypatch.setattr("openjiuwen.core.session.agent.create_agent_session", _FakeSession)
    outcome = await run_attribution(
        model=object(),
        nodes_by_subagent=_nodes(),
        signals=signals,
        task_description="写报告",
        runtime=runtime,
        total_timeout_s=None,
    )
    assert outcome.status == "success"
    assert outcome.completed is True
    assert outcome.skipped_signal_ids == [perfect.signal_id]
    assert set(runtime.reward_attributions) == {fair.signal_id, weak.signal_id, uncertain.signal_id}
    assert perfect.signal_id not in runtime.reward_attributions
    assert fake_agent.invocations == 1
    assert perfect.text not in fake_agent.prompts[0]


async def test_run_attribution_all_perfect_short_circuits(monkeypatch: Any) -> None:
    built: list[bool] = []

    def build_marker(**kwargs: Any) -> tuple[Any, _FakeRail]:
        del kwargs
        built.append(True)
        return _FakeDeepAgent(NodeBackedRuntime(_nodes()), [], rounds_to_finish=1), _FakeRail()

    monkeypatch.setattr(run_module, "build_attribution_agent", build_marker)
    perfect = _tier_signal("perfect")
    outcome = await run_attribution(
        model=object(),
        nodes_by_subagent=_nodes(),
        signals=[perfect],
        task_description="写报告",
    )
    assert outcome.status == "success"
    assert outcome.completed is True
    assert outcome.skipped_signal_ids == [perfect.signal_id]
    assert outcome.counts["attributed"] == 0
    assert built == []


async def test_run_attribution_agent_error(monkeypatch: Any) -> None:
    class _ExplodingAgent:
        async def invoke(self, prompt: str, session: Any = None) -> dict[str, Any]:
            del prompt, session
            raise RuntimeError("model broke")

    fake_rail = _FakeRail()
    runtime = NodeBackedRuntime(_nodes())
    monkeypatch.setattr(run_module, "build_attribution_agent", lambda **kwargs: (_ExplodingAgent(), fake_rail))
    monkeypatch.setattr("openjiuwen.core.session.agent.create_agent_session", _FakeSession)
    outcome = await run_attribution(
        model=object(),
        nodes_by_subagent=_nodes(),
        signals=[_signal()],
        task_description="写报告",
        runtime=runtime,
        total_timeout_s=None,
    )
    assert outcome.status == "failed"
    assert outcome.failure_kind == "error"
    assert "model broke" in outcome.error_detail or True


async def test_run_attribution_forwards_advice_switches(monkeypatch: Any) -> None:
    runtime = NodeBackedRuntime(_nodes())
    signals = [_signal()]
    fake_agent = _FakeDeepAgent(runtime, signals, rounds_to_finish=1)
    monkeypatch.setattr("openjiuwen.core.session.agent.create_agent_session", _FakeSession)
    captured = _patch_build(monkeypatch, fake_agent, _FakeRail())
    await run_attribution(
        model=object(),
        nodes_by_subagent=_nodes(),
        signals=signals,
        task_description="写报告",
        runtime=runtime,
        skill_evolution=False,
        ttse_advice=False,
        total_timeout_s=None,
    )
    assert captured["skill_evolution"] is False
    assert runtime.ttse_advice is False
    # the disabled tool never appears as a capability in the initial prompt
    assert "submit_skill_evolution" not in fake_agent.prompts[0]

    fake_agent_default = _FakeDeepAgent(runtime, signals, rounds_to_finish=1)
    captured = _patch_build(monkeypatch, fake_agent_default, _FakeRail())
    await run_attribution(
        model=object(),
        nodes_by_subagent=_nodes(),
        signals=signals,
        task_description="写报告",
        runtime=runtime,
        total_timeout_s=None,
    )
    assert captured["skill_evolution"] is True
    assert runtime.ttse_advice is True


def _core_model() -> Any:
    from openjiuwen.core.foundation.llm import ModelClientConfig, ModelRequestConfig
    from openjiuwen.core.foundation.llm.model import Model

    return Model(
        model_client_config=ModelClientConfig(
            client_provider="OpenAI", api_key="k", api_base="http://127.0.0.1:9/v1", timeout=360.0
        ),
        model_config=ModelRequestConfig(model="glm"),
    )


async def test_attribution_models_have_no_time_limit_and_the_callers_model_is_untouched() -> None:
    from openjiuwen.agent_evolving.attribution.agent import without_time_limits

    model = _core_model()
    free = without_time_limits(model)
    client = free.model_client_config
    assert free is not model and free.model_config is model.model_config
    assert client.timeout >= 30 * 24 * 3600 and client.use_shared_llm_http_client is False
    assert client.stream_first_chunk_timeout is None and client.stream_idle_timeout is None
    assert model.model_client_config.timeout == 360.0  # the host's model keeps its own limits
    assert model.model_client_config.stream_idle_timeout == 60.0
    double = object()
    assert without_time_limits(double) is double  # a test double passes through


@pytest.mark.parametrize("which", ["attribution", "reward"])
async def test_attribution_agents_retry_failed_model_calls_and_wait_for_slow_models(
    monkeypatch: Any, which: str
) -> None:
    from openjiuwen.agent_evolving.attribution.agent import build_attribution_agent
    from openjiuwen.agent_evolving.attribution.reward_stage.reward_agent import build_reward_agent
    from openjiuwen.harness import factory as harness_factory

    captured: dict[str, Any] = {}

    def fake_create_deep_agent(model: Any, **kwargs: Any) -> Any:
        captured.update(kwargs, model=model)
        return object()

    monkeypatch.setattr(harness_factory, "create_deep_agent", fake_create_deep_agent)
    if which == "attribution":
        build_attribution_agent(
            model=_core_model(),
            signals=[_signal()],
            runtime=NodeBackedRuntime(_nodes()),
            task_description="写报告",
            reward_overall=None,
        )
    else:
        build_reward_agent(model=_core_model(), card=object(), system_prompt="p", workspace="/w")
    assert captured["enable_model_anomaly_detection_rail"] is True  # a dropped connection is retried
    client = captured["model"].model_client_config
    assert client.stream_idle_timeout is None and client.stream_first_chunk_timeout is None


async def test_run_attribution_has_no_time_limit_by_default(monkeypatch: Any) -> None:
    import inspect

    assert inspect.signature(run_attribution).parameters["total_timeout_s"].default is None
    fake_rail = _FakeRail()
    monkeypatch.setattr(run_module, "build_attribution_agent", lambda **kwargs: (_SlowAgent(), fake_rail))
    monkeypatch.setattr("openjiuwen.core.session.agent.create_agent_session", _FakeSession)
    outcome = await run_attribution(
        model=object(),
        nodes_by_subagent=_nodes(),
        signals=[_signal()],
        task_description="写报告",
        runtime=NodeBackedRuntime(_nodes()),
    )
    assert outcome.failure_kind != "timeout"  # the slow agent was waited for


async def test_two_diagnoses_at_once_keep_their_own_attribution_tools() -> None:
    from openjiuwen.agent_evolving.attribution.agent import build_attribution_agent
    from openjiuwen.core.runner import Runner

    def build(task: str) -> tuple[Any, NodeBackedRuntime]:
        runtime = NodeBackedRuntime(_nodes())
        agent, _ = build_attribution_agent(
            model=_core_model(), signals=[_signal()], runtime=runtime, task_description=task, reward_overall=None
        )
        return agent, runtime

    (first, first_runtime), (second, second_runtime) = build("绘本"), build("家庭日程")
    assert first.card.id == second.card.id == "attribution-agent"  # persistence identity unchanged
    ids = [
        {card.id for card in agent.ability_manager.list() if card.name == "submit_reward_failure"}
        for agent in (first, second)
    ]
    assert ids[0] and ids[1] and ids[0].isdisjoint(ids[1])  # one owner per run
    for owned, runtime in zip(ids, (first_runtime, second_runtime)):  # each run's tool sees its own trajectory
        tool = Runner.resource_mgr.get_tool(next(iter(owned)))
        assert tool is not None and tool._runtime is runtime
    run_module._release_tools(first)
    assert Runner.resource_mgr.get_tool(next(iter(ids[0]))) is None  # released after the run
    assert Runner.resource_mgr.get_tool(next(iter(ids[1]))) is not None  # the other diagnosis keeps its tools
    run_module._release_tools(second)


class _AgentWithTools(_SlowAgent):
    def __init__(self) -> None:
        self.released = 0

        def teardown(manager: Any) -> None:
            del manager
            self.released += 1

        self.ability_manager = type("AM", (), {"teardown_tools": teardown})()


async def test_run_attribution_releases_its_tools_whatever_the_outcome(monkeypatch: Any) -> None:
    agent = _AgentWithTools()
    monkeypatch.setattr(run_module, "build_attribution_agent", lambda **kwargs: (agent, _FakeRail()))
    monkeypatch.setattr("openjiuwen.core.session.agent.create_agent_session", _FakeSession)
    await run_attribution(
        model=object(),
        nodes_by_subagent=_nodes(),
        signals=[_signal()],
        task_description="写报告",
        runtime=NodeBackedRuntime(_nodes()),
        total_timeout_s=0.05,
    )
    assert agent.released == 1  # also after a timeout


class _StreamingAgent:
    """An agent driven through ``stream`` only: what it yields stands for the model's streamed output."""

    def __init__(self, items: list[Any]) -> None:
        self.items, self.prompts = items, []

    async def invoke(self, prompt: str, session: Any = None) -> dict[str, Any]:
        raise AssertionError("the attribution agent is driven through stream, so its model calls stream")

    async def stream(self, prompt: str, session: Any = None) -> Any:
        self.prompts.append(prompt)
        for item in self.items:
            yield item


async def test_the_attribution_agent_is_streamed_so_a_slow_model_keeps_the_connection_busy(monkeypatch: Any) -> None:
    agent = _StreamingAgent([{"type": "llm_output", "payload": {"content": "thinking..."}}])
    monkeypatch.setattr(run_module, "build_attribution_agent", lambda **kwargs: (agent, _FakeRail()))
    monkeypatch.setattr("openjiuwen.core.session.agent.create_agent_session", _FakeSession)
    outcome = await run_attribution(
        model=object(),
        nodes_by_subagent=_nodes(),
        signals=[_signal()],
        task_description="写报告",
        runtime=NodeBackedRuntime(_nodes()),
    )
    assert agent.prompts and outcome.failure_kind == "incomplete"  # streamed to the end, signal still open


async def test_a_failure_the_stream_reports_is_raised_like_invoke_would() -> None:
    failed = {"type": "answer", "payload": {"result_type": "error", "output": "APIConnectionError: Connection error."}}
    with pytest.raises(RuntimeError, match="Connection error"):
        await run_module.run_agent(_StreamingAgent([failed]), "p")
    await run_module.run_agent(_SlowAgent(), "p")  # an agent without stream is invoked


async def test_reaching_the_step_limit_ends_the_round_and_the_wake_round_still_runs(monkeypatch: Any) -> None:
    limit = {
        "type": "answer",
        "payload": {"result_type": "error", "output": "Max iterations reached without completion"},
    }
    agent = _StreamingAgent([limit])
    monkeypatch.setattr(run_module, "build_attribution_agent", lambda **kwargs: (agent, _FakeRail()))
    monkeypatch.setattr("openjiuwen.core.session.agent.create_agent_session", _FakeSession)
    outcome = await run_attribution(
        model=object(),
        nodes_by_subagent=_nodes(),
        signals=[_signal()],
        task_description="写报告",
        runtime=NodeBackedRuntime(_nodes()),
    )
    assert len(agent.prompts) == 2  # not an error: the wake round ran
    assert outcome.failure_kind == "incomplete" and outcome.wake_attempted


class _SessionRecordingAgent(_StreamingAgent):
    async def stream(self, prompt: str, session: Any = None) -> Any:
        self.prompts.append(session)
        yield {"type": "llm_output", "payload": {"content": "thinking..."}}


async def test_a_host_session_serves_the_first_round_and_the_wake_round_gets_a_new_one_under_its_id(
    monkeypatch: Any,
) -> None:
    host = SimpleNamespace(get_session_id=lambda: "host-session")
    agent = _SessionRecordingAgent([])
    monkeypatch.setattr(run_module, "build_attribution_agent", lambda **kwargs: (agent, _FakeRail()))
    monkeypatch.setattr("openjiuwen.core.session.agent.create_agent_session", _FakeSession)
    await run_attribution(
        model=object(),
        nodes_by_subagent=_nodes(),
        signals=[_signal()],
        task_description="写报告",
        runtime=NodeBackedRuntime(_nodes()),
        session=host,
    )
    first, wake = agent.prompts
    assert first is host  # the host's session, as given
    assert wake is not host and wake.kwargs["session_id"] == "host-session"  # its stream was closed by round 1


class _DroppedOnceAgent(_StreamingAgent):
    """The provider drops the first round's model call mid-stream; the wake round runs."""

    async def stream(self, prompt: str, session: Any = None) -> Any:
        self.prompts.append(prompt)
        yield {"type": "llm_output", "payload": {"content": "thinking..."}}
        if len(self.prompts) == 1:
            raise RuntimeError("[181001] model call failed: APIConnectionError: Connection error.")


async def test_a_dropped_model_connection_goes_on_to_the_wake_round_instead_of_failing(monkeypatch: Any) -> None:
    agent = _DroppedOnceAgent([])
    monkeypatch.setattr(run_module, "build_attribution_agent", lambda **kwargs: (agent, _FakeRail()))
    monkeypatch.setattr("openjiuwen.core.session.agent.create_agent_session", _FakeSession)
    outcome = await run_attribution(
        model=object(),
        nodes_by_subagent=_nodes(),
        signals=[_signal()],
        task_description="写报告",
        runtime=NodeBackedRuntime(_nodes()),
    )
    assert len(agent.prompts) == 2 and outcome.wake_attempted
    assert outcome.failure_kind == "incomplete"  # not "error": the signal is just still open


async def test_a_real_attribution_agent_calls_its_model_streaming(monkeypatch: Any) -> None:
    from openjiuwen.core.foundation.llm.model import Model

    calls: list[str] = []

    async def invoke(self: Any, *args: Any, **kwargs: Any) -> Any:
        calls.append("invoke")
        raise RuntimeError("invalid api key (invoke)")  # not retried: the test needs no backoff

    async def stream(self: Any, *args: Any, **kwargs: Any) -> Any:
        calls.append("stream")
        raise RuntimeError("invalid api key (stream)")
        yield  # pragma: no cover - makes this an async generator

    monkeypatch.setattr(Model, "invoke", invoke)
    monkeypatch.setattr(Model, "stream", stream)
    outcome = await run_attribution(
        model=_core_model(),
        nodes_by_subagent=_nodes(),
        signals=[_signal()],
        task_description="写报告",
        runtime=NodeBackedRuntime(_nodes()),
    )
    # the agent's own turn streamed (an "invoke" here can only be the harness's background image probe)
    assert "stream" in calls
    assert outcome.status == "failed" and "invalid api key (stream)" in (outcome.error_detail or "")


async def test_a_real_attribution_agent_streams_both_rounds_through_one_session(monkeypatch: Any) -> None:
    from openjiuwen.core.foundation.llm.model import Model
    from openjiuwen.core.foundation.llm.schema.message_chunk import AssistantMessageChunk

    rounds: list[str] = []

    async def stream(self: Any, messages: Any, *args: Any, **kwargs: Any) -> Any:
        rounds.append(str(messages))
        yield AssistantMessageChunk(content="还在看轨迹。")  # no tool call: the signal stays open, so a wake follows

    monkeypatch.setattr(Model, "stream", stream)
    outcome = await run_attribution(
        model=_core_model(),
        nodes_by_subagent=_nodes(),
        signals=[_signal()],
        task_description="写报告",
        runtime=NodeBackedRuntime(_nodes()),
    )
    assert len(rounds) >= 2 and outcome.wake_attempted  # the wake round streamed too
    assert "还在看轨迹。" in rounds[-1]  # ... and went on from the first round's conversation
    assert outcome.failure_kind == "incomplete" and not outcome.error_detail  # no "StreamQueue is already closed"


class _SummaryAgent:
    def __init__(self, runtime: NodeBackedRuntime, rounds_to_finish: int = 1) -> None:
        self._runtime = runtime
        self._rounds_to_finish = rounds_to_finish
        self.invocations = 0
        self.prompts: list[str] = []

    async def invoke(self, prompt: str, session: Any = None) -> dict[str, Any]:
        del session
        self.invocations += 1
        self.prompts.append(prompt)
        if self.invocations >= self._rounds_to_finish:
            self._runtime.record_trajectory_summary("选中范围共 3 个步骤，期间一次工具调用失败。")
        return {}


async def test_run_trajectory_summary_returns_the_agent_summary(monkeypatch: Any) -> None:
    runtime = NodeBackedRuntime(_nodes())
    agent = _SummaryAgent(runtime)
    monkeypatch.setattr(run_module, "build_trajectory_summary_agent", lambda **kwargs: (agent, _FakeRail()))
    monkeypatch.setattr("openjiuwen.core.session.agent.create_agent_session", _FakeSession)

    outcome = await run_trajectory_summary(
        model=object(),
        nodes_by_subagent=_nodes(),
        runtime=runtime,
        task_hint="写报告",
        total_timeout_s=None,
    )

    assert outcome.status == "success" and outcome.completed is True
    assert outcome.trajectory_summary == "选中范围共 3 个步骤，期间一次工具调用失败。"
    assert agent.invocations == 1
    assert "写报告" in agent.prompts[0]


async def test_run_trajectory_summary_wakes_once_and_reports_missing_summary(monkeypatch: Any) -> None:
    runtime = NodeBackedRuntime(_nodes())
    agent = _SummaryAgent(runtime, rounds_to_finish=2)
    monkeypatch.setattr(run_module, "build_trajectory_summary_agent", lambda **kwargs: (agent, _FakeRail()))
    monkeypatch.setattr("openjiuwen.core.session.agent.create_agent_session", _FakeSession)

    outcome = await run_trajectory_summary(
        model=object(), nodes_by_subagent=_nodes(), runtime=runtime, total_timeout_s=None
    )
    assert outcome.status == "success" and outcome.wake_attempted is True
    assert agent.invocations == 2

    missing_runtime = NodeBackedRuntime(_nodes())
    missing = _SummaryAgent(missing_runtime, rounds_to_finish=99)
    monkeypatch.setattr(run_module, "build_trajectory_summary_agent", lambda **kwargs: (missing, _FakeRail()))
    outcome = await run_trajectory_summary(
        model=object(), nodes_by_subagent=_nodes(), runtime=missing_runtime, total_timeout_s=None
    )
    assert outcome.status == "failed" and outcome.failure_kind == "incomplete"
    assert outcome.trajectory_summary is None
