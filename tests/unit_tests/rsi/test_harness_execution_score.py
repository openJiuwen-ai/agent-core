# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Non-transport solver failures are continuous harness scores."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from openjiuwen.rsi.harness_rsi.evaluator import team_evaluator as team_evaluator_module
from openjiuwen.rsi.harness_rsi.evaluator.case_backend import CaseExecutionResult
from openjiuwen.rsi.harness_rsi.evaluator.case_runner import CaseRunner
from openjiuwen.rsi.harness_rsi.evaluator.errors import EvaluationInfrastructureError
from openjiuwen.rsi.harness_rsi.evaluator.judger import LlmAsJudgeJudger
from openjiuwen.rsi.harness_rsi.evaluator.team_evaluator import TeamEvaluator
from openjiuwen.rsi.harness_rsi.single_harness.iterative import _error_case_ids, _nonpassing_case_ids
from openjiuwen.rsi.harness_rsi.single_harness.promotion_policy import complete_scores
from tests.unit_tests.rsi.test_evaluator_agent import _case, _config

_OVERFLOW = (
    "Error code: 400 - maximum context length is 1048576 tokens. "
    "You requested 1050563 tokens."
)


class _FailingBackend:
    def __init__(self, error: str, *, status: str = "failed") -> None:
        self.error = error
        self.status = status
        self.calls = 0

    async def execute(self, *, output_dir: str, **_: Any) -> CaseExecutionResult:
        self.calls += 1
        return CaseExecutionResult(
            response="",
            execution_status=self.status,
            error=self.error,
            workspace_dir=_workspace(output_dir),
        )

    async def cleanup(self, *_: Any) -> None:
        return None


def _workspace(output_dir: str) -> str:
    workspace = Path(output_dir) / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    return str(workspace)


def _read_yaml(path: str) -> dict[str, Any]:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def _read_json(path: str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _evaluator(backend: _FailingBackend, *, score_mode: str) -> TeamEvaluator:
    config = _config(score_mode=score_mode)
    evaluator = TeamEvaluator(config)
    evaluator.case_runner = CaseRunner(backend=backend, judger=LlmAsJudgeJudger(config))
    return evaluator


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [
    _OVERFLOW,
    "RuntimeError: solver crashed while writing the kernel",
])
async def test_non_transport_execution_failure_is_a_continuous_harness_score(tmp_path, monkeypatch, error):
    monkeypatch.setattr(
        "openjiuwen.rsi.harness_rsi.evaluator.judger.llm_as_judge.run_judge_agent",
        pytest.fail,
    )
    backend = _FailingBackend(error, status="error")
    evaluator = _evaluator(backend, score_mode="continuous")
    ref_path = await evaluator.evaluate_batch([_case()], "", "", str(tmp_path / "eval"))

    assert complete_scores(ref_path, {"sample"}) == {"sample": 0.0}
    assert _nonpassing_case_ids(ref_path) == {"sample"}
    assert _error_case_ids(ref_path) == set()
    row = _read_yaml(ref_path)["cases"][0]
    assert row["status"] == "failed"
    assert row["score"] == 0.0
    assert row["metadata"]["score_mode"] == "continuous"
    result = _read_json(row["result_path"])
    assert result["status"] == "failed"
    assert result["execution_status"] == "failed"
    assert result["score"] == 0.0
    assert result["error"] == error
    assert result["evaluation"]["reason"] == error
    assert result["evaluation"]["method"] == "llm_as_judge"
    assert result["evaluation"]["metadata"]["score_mode"] == "continuous"
    assert "context_overflow" not in json.dumps(result)
    assert "failure_kind" not in json.dumps(result)
    assert 1 - result["score"] > 0.05
    assert backend.calls == 1


@pytest.mark.asyncio
async def test_transport_execution_failure_stays_invalid_after_retries(tmp_path, monkeypatch):
    async def _no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr(team_evaluator_module.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(
        "openjiuwen.rsi.harness_rsi.evaluator.judger.llm_as_judge.run_judge_agent",
        pytest.fail,
    )
    backend = _FailingBackend("http 429 too many requests")
    evaluator = _evaluator(backend, score_mode="continuous")
    ref_path = await evaluator.evaluate_batch([_case()], "", "", str(tmp_path / "eval"))

    assert backend.calls == 3
    row = _read_yaml(ref_path)["cases"][0]
    assert row["status"] == "error"
    with pytest.raises(EvaluationInfrastructureError, match="Invalid correctness score"):
        complete_scores(ref_path, {"sample"})


@pytest.mark.asyncio
async def test_binary_execution_failure_stays_invalid(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "openjiuwen.rsi.harness_rsi.evaluator.judger.llm_as_judge.run_judge_agent",
        pytest.fail,
    )
    backend = _FailingBackend(_OVERFLOW)
    evaluator = _evaluator(backend, score_mode="binary")
    ref_path = await evaluator.evaluate_batch([_case()], "", "", str(tmp_path / "eval"))
    row = _read_yaml(ref_path)["cases"][0]
    assert row["status"] == "error"
    assert backend.calls == 1
    with pytest.raises(EvaluationInfrastructureError):
        complete_scores(ref_path, {"sample"})
