# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Judge deadlines stay within the existing transient retry boundary."""

import asyncio
import json
from functools import partial
from unittest.mock import AsyncMock

import pytest

from openjiuwen.rsi.harness_rsi.evaluator.case_backend import CaseExecutionResult
from openjiuwen.rsi.harness_rsi.evaluator.errors import EvaluationInfrastructureError
from openjiuwen.rsi.harness_rsi.evaluator.judger import LlmAsJudgeJudger, llm_as_judge
from openjiuwen.rsi.harness_rsi.model_call import run_model_call_with_retries
from tests.unit_tests.rsi.test_evaluator_agent import _case, _config, _output


@pytest.mark.asyncio
@pytest.mark.parametrize("recovers", [True, False])
async def test_timeout_retries_frozen_evidence_and_never_fabricates_grade(tmp_path, monkeypatch, recovers):
    calls = []
    cancelled = []

    async def run(_config, workspace, prompt, _log):
        calls.append((workspace, prompt, (workspace / "request.json").read_bytes()))
        if len(calls) == 1 or not recovers:
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                cancelled.append(True)
                raise
        return json.dumps(_output((0.0, 0.0)))

    monkeypatch.setattr(llm_as_judge, "run_judge_agent", run)
    monkeypatch.setattr(
        llm_as_judge,
        "run_model_call_with_retries",
        partial(
            run_model_call_with_retries,
            initial_retry_delay_seconds=0,
            max_retry_delay_seconds=0,
        ),
    )
    judger = LlmAsJudgeJudger(_config(judge_timeout_sec=1, judge_max_retries=1))
    kwargs = dict(case=_case(), execution_result=CaseExecutionResult("done", "passed"), output_dir=str(tmp_path))
    if recovers:
        result = await judger.judge(**kwargs)
        assert result.score == 0.0
        assert result.metadata["parsed"]["overall_score"] == 0.0
    else:
        with pytest.raises(EvaluationInfrastructureError):
            await judger.judge(**kwargs)
        assert not list(tmp_path.rglob("assessment.json"))
        error = json.loads(next(tmp_path.rglob("error.json")).read_text(encoding="utf-8"))
        assert "timed out after 1s" in error["message"]
    assert len(calls) == 2
    assert calls[0] == calls[1]
    assert len(cancelled) == (1 if recovers else 2)


@pytest.mark.asyncio
async def test_valid_verdict_is_not_retried_and_prompt_has_actual_path_and_ids(tmp_path, monkeypatch):
    call = AsyncMock(return_value=json.dumps(_output()))
    monkeypatch.setattr(llm_as_judge, "run_judge_agent", call)
    await LlmAsJudgeJudger(_config()).judge(
        case=_case(),
        execution_result=CaseExecutionResult("done", "passed"),
        output_dir=str(tmp_path),
    )
    assert call.await_count == 1
    _, workspace, prompt, _ = call.call_args.args
    assert str(workspace.resolve() / "request.json") in prompt
    assert '"behavior_ids": ["rubric_001", "rubric_002"]' in prompt
    assert '"forbidden_ids": []' in prompt
