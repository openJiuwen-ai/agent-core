# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Judge iteration recovery always starts from the complete frozen snapshot."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openjiuwen.core.foundation.llm import AssistantMessage, Model, SystemMessage, ToolCall
from openjiuwen.rsi.harness_rsi.config import EvaluatorConfig
from openjiuwen.rsi.harness_rsi.evaluator.case_backend import CaseExecutionResult
from openjiuwen.rsi.harness_rsi.evaluator.errors import EvaluationInfrastructureError
from openjiuwen.rsi.harness_rsi.evaluator.judger import LlmAsJudgeJudger, judge_runtime, llm_as_judge
from openjiuwen.rsi.harness_rsi.evaluator.judger.judge_runtime import JudgeIterationLimitError


def _verdict(score=0.5):
    return json.dumps({
        "status": "completed",
        "overall_reason": "Observed work",
        "behaviors": [{
            "id": "rubric_001",
            "score": score,
            "reason": "Criterion inspected",
            "evidence": "artifacts/answer.txt",
        }],
        "forbidden_hits": [],
    })


def _config(tmp_path):
    config_path = tmp_path / "model.json"
    config_path.write_text(json.dumps({
        "model_client_config": {
            "client_provider": "OpenAI",
            "api_key": "test",
            "api_base": "https://example.test/v1",
        },
        "model_request_config": {"model": "test", "max_tokens": 100000},
    }), encoding="utf-8")
    return EvaluatorConfig(
        judge_model_config_ref=str(config_path),
        judge_agent_max_iterations=1,
        judge_max_retries=0,
    )


def _arguments(tmp_path):
    return {
        "case": {
            "case_id": "one",
            "input": "Deliver a report",
            "reference": {"rubric": ["A report"]},
        },
        "execution_result": CaseExecutionResult("done", "passed"),
        "output_dir": str(tmp_path),
    }


def test_judge_budget_default_and_override():
    assert EvaluatorConfig().judge_agent_max_iterations == 20
    assert EvaluatorConfig.from_dict({"judge_agent_max_iterations": 30}).judge_agent_max_iterations == 30


@pytest.mark.asyncio
async def test_iteration_limit_regrades_once_from_same_frozen_workspace(tmp_path, monkeypatch):
    agent = AsyncMock(side_effect=JudgeIterationLimitError("reading limit"))
    closeout_workspaces = []

    async def closeout(_config, workspace):
        request = json.loads((workspace / "request.json").read_text(encoding="utf-8"))
        assert request["response"] == "done"
        closeout_workspaces.append(workspace)
        return _verdict()

    monkeypatch.setattr(llm_as_judge, "run_judge_agent", agent)
    monkeypatch.setattr(llm_as_judge, "run_judge_closeout", closeout)
    result = await LlmAsJudgeJudger(_config(tmp_path)).judge(**_arguments(tmp_path))
    assert agent.await_count == 1
    assert len(closeout_workspaces) == 1
    assert result.metadata["attempt"] == 2
    assert result.metadata["recovery"] == "complete_frozen_evidence"
    assert result.metadata["parsed"]["overall_score"] == 0.5


@pytest.mark.asyncio
async def test_runtime_iteration_limit_uses_complete_snapshot_without_history(tmp_path, monkeypatch):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    complete_text = "complete frozen evidence\n" * 4000
    (artifacts / "answer.txt").write_text(complete_text, encoding="utf-8")
    calls = []

    async def invoke(_model, messages, **kwargs):
        calls.append((messages, kwargs))
        if kwargs.get("tools") is not None:
            request_path = next(tmp_path.rglob("request.json"))
            return AssistantMessage(content="", tool_calls=[ToolCall(
                id="read-request",
                type="function",
                name="read_file",
                arguments=json.dumps({"file_path": str(request_path)}),
            )])
        if isinstance(messages[0], SystemMessage):
            assert len(messages) == 2
            payload = json.loads(messages[1].content)
            assert payload["evidence_files"]["artifacts/answer.txt"] == complete_text
        return AssistantMessage(content=_verdict())

    monkeypatch.setattr(Model, "invoke", invoke)
    result = await LlmAsJudgeJudger(_config(tmp_path)).judge(**_arguments(tmp_path))
    judge_calls = [
        call for call in calls
        if isinstance(call[0][0], SystemMessage) and call[1].get("tools") is None
    ]
    assert len(judge_calls) == 1
    assert any(call[1].get("tools") is not None for call in calls)
    assert result.metadata["recovery"] == "complete_frozen_evidence"


@pytest.mark.asyncio
async def test_iteration_closeout_failure_produces_no_score(tmp_path, monkeypatch):
    monkeypatch.setattr(
        llm_as_judge,
        "run_judge_agent",
        AsyncMock(side_effect=JudgeIterationLimitError("reading limit")),
    )
    monkeypatch.setattr(
        llm_as_judge,
        "run_judge_closeout",
        AsyncMock(side_effect=EvaluationInfrastructureError("complete evidence exceeds context")),
    )
    with pytest.raises(EvaluationInfrastructureError, match="exceeds context"):
        await LlmAsJudgeJudger(_config(tmp_path)).judge(**_arguments(tmp_path))
    assert not list(tmp_path.rglob("assessment.json"))


@pytest.mark.asyncio
async def test_closeout_has_no_consumed_state(tmp_path, monkeypatch):
    (tmp_path / "request.json").write_text(
        '{"response":"done","evidence_files":[]}',
        encoding="utf-8",
    )
    model = AsyncMock()
    model.invoke.return_value = AssistantMessage(content=_verdict())
    monkeypatch.setattr(judge_runtime, "_judge_model", lambda _config: model)
    assert await judge_runtime.run_judge_closeout(EvaluatorConfig(), tmp_path) == _verdict()
    assert await judge_runtime.run_judge_closeout(EvaluatorConfig(), tmp_path) == _verdict()
    assert model.invoke.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", [
    'Assessment:\n```json\n{"score":0}\n```\n```json\n{"score":1}\n```',
    '{"overall_reason":"return "ok""}',
    '{"score":0,"score":1}',
])
@pytest.mark.parametrize("repair_succeeds", [True, False])
async def test_runtime_format_error_reaches_persisted_recovery(tmp_path, monkeypatch, raw, repair_succeeds):
    from openjiuwen.core.runner import Runner

    agent = SimpleNamespace(
        card=SimpleNamespace(id="judge-test", name="evaluator_agent"),
        configured_rails=lambda: [],
        cleanup_task_resources=AsyncMock(),
    )
    monkeypatch.setattr(judge_runtime, "inline_evidence", lambda *args, **kwargs: None)
    monkeypatch.setattr(judge_runtime, "build_judge_agent", lambda *args, **kwargs: agent)
    monkeypatch.setattr(Runner, "run_agent", AsyncMock(return_value={"output": raw}))
    repaired = _verdict(0.9) if repair_succeeds else raw
    repair = AsyncMock(return_value=repaired)
    monkeypatch.setattr(llm_as_judge, "repair_judge_json", repair)
    judger = LlmAsJudgeJudger(_config(tmp_path))
    if repair_succeeds:
        result = await judger.judge(**_arguments(tmp_path))
        assert result.passed is True
        assert result.metadata["parsed"]["overall_score"] == 0.9
    else:
        with pytest.raises(EvaluationInfrastructureError, match="Unusable LLM evaluation"):
            await judger.judge(**_arguments(tmp_path))
        assert not list(tmp_path.rglob("assessment.json"))
        assert list(tmp_path.rglob("validation_error_1.json"))
    response_path, = tmp_path.rglob("response_1.json")
    assert json.loads(response_path.read_text(encoding="utf-8"))["raw_output"] == raw
    repair.assert_awaited_once()
    assert repair.call_args.args[1] == raw
    agent.cleanup_task_resources.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("has_primary_error", [False, True])
async def test_cleanup_failure_does_not_replace_verdict_or_execution_error(tmp_path, monkeypatch, has_primary_error):
    from openjiuwen.core.runner import Runner

    agent = SimpleNamespace(
        card=SimpleNamespace(id="judge-cleanup", name="evaluator_agent"),
        configured_rails=lambda: [],
        cleanup_task_resources=AsyncMock(side_effect=RuntimeError("cleanup unavailable")),
    )
    monkeypatch.setattr(judge_runtime, "inline_evidence", lambda *args, **kwargs: None)
    monkeypatch.setattr(judge_runtime, "build_judge_agent", lambda *args, **kwargs: agent)
    call = AsyncMock(side_effect=TimeoutError("original timeout")) if has_primary_error else AsyncMock(
        return_value={"output": _verdict()},
    )
    monkeypatch.setattr(Runner, "run_agent", call)
    removed = []
    monkeypatch.setattr(Runner.resource_mgr, "remove_sys_operation", removed.append)
    if has_primary_error:
        with pytest.raises(TimeoutError, match="original timeout"):
            await judge_runtime.run_judge_agent(_config(tmp_path), tmp_path, "", tmp_path / "tools.jsonl")
    else:
        assert await judge_runtime.run_judge_agent(
            _config(tmp_path), tmp_path, "", tmp_path / "tools.jsonl",
        ) == _verdict()
    assert removed == ["evaluator_agent_judge-cleanup"]
