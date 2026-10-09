# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Formatting recovery must preserve the original grading contract."""
import json
from functools import partial
from unittest.mock import AsyncMock

import pytest

from openjiuwen.rsi.harness_rsi.config import EvaluatorConfig
from openjiuwen.rsi.harness_rsi.evaluator.errors import EvaluationInfrastructureError
from openjiuwen.rsi.harness_rsi.evaluator.judger import llm_as_judge as judge
from openjiuwen.rsi.harness_rsi.member_optimizer.agents.output import parse_yaml_or_json_object_response
from openjiuwen.rsi.harness_rsi.model_call import run_model_call_with_retries


def test_nested_code_fences_are_json_string_data():
    value = {"evidence": [{"quote": '```cpp\nreturn "ok";\n```'}]}
    assert parse_yaml_or_json_object_response('```json\n' + json.dumps(value) + '\n```') == value


@pytest.mark.asyncio
@pytest.mark.parametrize("score", [0, 0.9, 2])
async def test_repair_uses_existing_strict_scoring(tmp_path, monkeypatch, score):
    malformed = '{"status":"completed","overall_reason":"return "ok""}'
    verdict = {"status": "completed", "overall_reason": 'return "ok"', "behaviors": [
        {"id": "criterion", "score": score, "reason": "checked", "evidence": "answer.txt"}
    ], "forbidden_hits": []}
    monkeypatch.setattr(judge, "run_judge_agent", AsyncMock(return_value=malformed))
    repair = AsyncMock(return_value=json.dumps(verdict))
    monkeypatch.setattr(judge, "repair_judge_json", repair)
    runner = judge.LlmAsJudgeJudger(EvaluatorConfig(judge_model_config_ref="unused", judge_max_retries=0))
    args = (tmp_path, tmp_path, [{"id": "criterion", "description": "criterion", "weight": 1}], [])
    if score > 1:
        with pytest.raises(EvaluationInfrastructureError):
            await runner._evaluate(*args)
    else:
        result = await runner._evaluate(*args)
        assert result.passed is (score >= 0.8)
        assert result.metadata["parsed"]["overall_score"] == score
    repair.assert_awaited_once()
    assert repair.call_args.args[1] == malformed


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [TimeoutError("timeout"), RuntimeError("HTTP 503"), ""])
async def test_format_repair_retries_transient_failure_with_original_output(tmp_path, monkeypatch, failure):
    raw = '{"overall_reason":"return "ok""}'
    repaired = '{"overall_reason":"return \\"ok\\""}'
    repair = AsyncMock(side_effect=[failure, repaired])
    monkeypatch.setattr(judge, "repair_judge_json", repair)
    monkeypatch.setattr(judge, "run_model_call_with_retries", partial(
        run_model_call_with_retries, initial_retry_delay_seconds=0, max_retry_delay_seconds=0,
    ))
    config = EvaluatorConfig(judge_model_config_ref="unused", judge_max_retries=1)
    parsed = await judge._parse_with_format_repair(config, tmp_path, raw)
    assert parsed == {"overall_reason": 'return "ok"'}
    assert repair.await_count == 2
    assert repair.await_args_list[0] == repair.await_args_list[1]
    assert repair.call_args.args[1] == raw
    assert json.loads((tmp_path / "format_error.json").read_text(encoding="utf-8"))["message"]


@pytest.mark.asyncio
async def test_format_repair_does_not_retry_authentication_failure(tmp_path, monkeypatch):
    repair = AsyncMock(side_effect=RuntimeError("invalid API key"))
    monkeypatch.setattr(judge, "repair_judge_json", repair)
    with pytest.raises(RuntimeError, match="invalid API key"):
        await judge._parse_with_format_repair(
            EvaluatorConfig(judge_model_config_ref="unused", judge_max_retries=1), tmp_path, '{"broken":',
        )
    repair.assert_awaited_once()
    assert (tmp_path / "format_error.json").is_file()


@pytest.mark.asyncio
async def test_exhausted_repair_preserves_original_without_score(tmp_path, monkeypatch):
    raw = '{"overall_reason":"return "ok""}'
    monkeypatch.setattr(judge, "run_judge_agent", AsyncMock(return_value=raw))
    repair = AsyncMock(side_effect=TimeoutError("repair transport timeout"))
    monkeypatch.setattr(judge, "repair_judge_json", repair)
    monkeypatch.setattr(judge, "run_model_call_with_retries", partial(
        run_model_call_with_retries, initial_retry_delay_seconds=0, max_retry_delay_seconds=0,
    ))
    runner = judge.LlmAsJudgeJudger(EvaluatorConfig(judge_model_config_ref="unused", judge_max_retries=1))
    with pytest.raises(TimeoutError, match="repair transport timeout"):
        await runner._evaluate(tmp_path, tmp_path, [], [])
    assert repair.await_count == 2
    assert json.loads((tmp_path / "response_1.json").read_text(encoding="utf-8"))["raw_output"] == raw
    assert (tmp_path / "format_error.json").is_file()
    assert not (tmp_path / "assessment.json").exists()
