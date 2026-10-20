# coding: utf-8
"""Tests for the run-level orchestration: diagnosis and attribution."""

from __future__ import annotations

import asyncio
import re
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
    assert captured["context_budget_tokens"] == 600_000
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
    assert budget_rails[0]._budget == 600_000


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
                self._runtime.record_attribution(signal.signal_id, [{"text_md": "cause", "start_node_id": "main:1"}])
            self._runtime.record_artifact_improvement("improve it")
        return {}


class _FakeRail:
    def __init__(self) -> None:
        self.exceeded = False
        self.spent = 0


def _patch_build(monkeypatch: Any, fake_agent: _FakeDeepAgent, fake_rail: _FakeRail) -> None:
    def fake_build(**kwargs: Any) -> tuple[Any, _FakeRail]:
        del kwargs
        return fake_agent, fake_rail

    monkeypatch.setattr(run_module, "build_attribution_agent", fake_build)


class _FakeSession:
    pass


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
    entry = runtime.attributions[signals[0].signal_id][0]
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
    assert "pending_signal_ids" in fake_agent.prompts[1]


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
    assert outcome.pending_signal_ids == [signals[0].signal_id]
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
    assert set(runtime.attributions) == {fair.signal_id, weak.signal_id, uncertain.signal_id}
    assert perfect.signal_id not in runtime.attributions
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
