# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Formatting recovery must preserve the original grading contract."""
import json
from unittest.mock import AsyncMock

import pytest

from openjiuwen.rsi.harness_rsi.config import EvaluatorConfig
from openjiuwen.rsi.harness_rsi.evaluator.errors import EvaluationInfrastructureError
from openjiuwen.rsi.harness_rsi.evaluator.judger import llm_as_judge as judge
from openjiuwen.rsi.harness_rsi.member_optimizer.agents.output import parse_yaml_or_json_object_response


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
    monkeypatch.setattr(judge.JudgeBudgetRail, "closeout", AsyncMock(return_value=malformed))
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
