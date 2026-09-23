# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Native Judge recovery must deliver complete evidence or produce no grade."""

import asyncio
import json
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from openjiuwen.core.foundation.llm import AssistantMessage, Model, ToolCall
from openjiuwen.rsi.harness_rsi.config import EvaluatorConfig
from openjiuwen.rsi.harness_rsi.evaluator.case_backend import CaseExecutionResult
from openjiuwen.rsi.harness_rsi.evaluator.errors import EvaluationInfrastructureError
from openjiuwen.rsi.harness_rsi.evaluator.judger import LlmAsJudgeJudger
from openjiuwen.rsi.harness_rsi.evaluator.judger.direct_evidence import MAX_CLOSEOUT_BYTES
from openjiuwen.rsi.harness_rsi.evaluator.judger.judge_runtime import JudgeBudgetRail


def _verdict(score):
    return json.dumps({"status": "completed", "overall_reason": "Observed work", "behaviors": [
        {"id": "rubric_001", "score": score, "reason": "Criterion inspected", "evidence": "artifacts/answer.txt"}
    ], "forbidden_hits": []})


def test_judge_budget_default_and_override():
    assert EvaluatorConfig().judge_agent_max_iterations == 20
    assert EvaluatorConfig.from_dict({"judge_agent_max_iterations": 30}).judge_agent_max_iterations == 30


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", [
    "recovered", "format_recovered", "invalid", "timeout", "cancel", "too_large", "non_text", "unreadable",
    "valid_zero",
])
async def test_native_unread_artifact_is_completed_before_closeout(tmp_path, monkeypatch, outcome):
    config_path = tmp_path / "model.json"
    config_path.write_text(json.dumps({
        "model_client_config": {"client_provider": "OpenAI", "api_key": "test", "api_base": "https://example.test/v1"},
        "model_request_config": {"model": "test", "max_tokens": 100000},
    }), encoding="utf-8")
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "evidence.jsonl").write_text('{"step": 1}\n{"step": 2}\n', encoding="utf-8")
    (artifacts / "answer.txt").write_text("VERIFIED_EVIDENCE = 1729\n", encoding="utf-8")
    # Exceed the direct route, so a real native reader gets only one turn.
    (artifacts / "scratch.txt").write_text("scratch\n" * 12000, encoding="utf-8")
    if outcome == "too_large":
        (artifacts / "scratch.txt").write_text("x" * MAX_CLOSEOUT_BYTES, encoding="utf-8")
    if outcome == "non_text":
        (artifacts / "image.png").write_bytes(b"not a text artifact")
    if outcome == "unreadable":
        (artifacts / "answer.txt").write_bytes(b"\xff")
    calls = []

    async def invoke(instance, messages, **kwargs):
        if any(isinstance(item.content, list) for item in messages):
            return AssistantMessage(content="red")
        calls.append((deepcopy(messages), kwargs))
        if len(calls) == 1:
            if outcome == "valid_zero":
                return AssistantMessage(content=_verdict(0))
            if outcome == "format_recovered":
                return AssistantMessage(content="Reading more. <tool_calls>read_file</tool_calls>")
            return AssistantMessage(content="", tool_calls=[ToolCall(
                id="read-request", type="function", name="read_file",
                arguments=json.dumps({"file_path": str(next(tmp_path.rglob("request.json")))}),
            )])
        assert len(calls) == 2
        assert kwargs.get("tools") is None
        assert len(messages) == 2
        payload = json.loads(messages[1].content)
        assert payload["evidence_files"]["artifacts/evidence.jsonl"] == '{"step": 1}\n{"step": 2}\n'
        if outcome == "unreadable":
            assert "artifacts/answer.txt" not in payload["evidence_files"]
            assert payload["unavailable_evidence_files"] == [
                {"path": "artifacts/answer.txt", "reason": "not valid UTF-8"},
            ]
        else:
            assert payload["evidence_files"]["artifacts/answer.txt"] == "VERIFIED_EVIDENCE = 1729\n"
        if outcome == "non_text":
            assert payload["unavailable_evidence_files"] == [
                {"path": "artifacts/image.png", "reason": "cannot read file (UnidentifiedImageError)"},
            ]
        expected_scratch = "x" * MAX_CLOSEOUT_BYTES if outcome == "too_large" else "scratch\n" * 12000
        assert payload["evidence_files"]["artifacts/scratch.txt"] == expected_scratch
        if outcome == "timeout":
            raise TimeoutError("closeout timeout")
        if outcome == "cancel":
            raise asyncio.CancelledError()
        return AssistantMessage(content="not JSON" if outcome == "invalid" else _verdict(.5))

    monkeypatch.setattr(Model, "invoke", invoke)
    judger = LlmAsJudgeJudger(EvaluatorConfig(
        judge_model_config_ref=str(config_path), judge_agent_max_iterations=1, judge_max_retries=0,
    ))
    arguments = {
        "case": {"case_id": "one", "input": "Deliver a report", "reference": {"rubric": ["A report"]}},
        "execution_result": CaseExecutionResult("Max iterations reached without completion", "passed"),
        "output_dir": str(tmp_path),
    }
    if outcome in {"recovered", "format_recovered", "valid_zero", "too_large", "non_text", "unreadable"}:
        result = await judger.judge(**arguments)
        assert result.metadata["parsed"]["overall_score"] == (0 if outcome == "valid_zero" else .5)
    else:
        with pytest.raises(asyncio.CancelledError if outcome == "cancel" else EvaluationInfrastructureError):
            await judger.judge(**arguments)
        assert not list(tmp_path.rglob("assessment.json"))
    assert len(calls) == (1 if outcome == "valid_zero" else 2)


@pytest.mark.asyncio
async def test_closeout_is_request_local_and_bounded(tmp_path):
    rail = JudgeBudgetRail(8, tmp_path / "tools.jsonl")
    rail.continuation = AsyncMock(return_value=_verdict(0))
    await rail.closeout('<tool_calls><invoke name="read_file">secret</invoke></tool_calls>')
    rail.continuation.assert_awaited_once_with()
    with pytest.raises(EvaluationInfrastructureError, match="already consumed"):
        await rail.closeout("still invalid")
