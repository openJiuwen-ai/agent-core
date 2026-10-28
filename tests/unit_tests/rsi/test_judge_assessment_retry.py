# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unusable grades retry from frozen evidence without manufacturing a score."""

import asyncio
import json
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from openjiuwen.core.foundation.llm import AssistantMessage, Model, UsageMetadata
from openjiuwen.rsi.harness_rsi.evaluator.errors import EvaluationInfrastructureError
from openjiuwen.rsi.harness_rsi.evaluator.judger import LlmAsJudgeJudger, llm_as_judge
from tests.unit_tests.rsi.test_evaluator_agent import _case, _config, _output
from openjiuwen.rsi.harness_rsi.evaluator.case_backend import CaseExecutionResult


def _arguments(tmp_path):
    return dict(case=_case(), execution_result=CaseExecutionResult('done', 'passed'), output_dir=str(tmp_path))


@pytest.mark.asyncio
@pytest.mark.parametrize('defect', ['repair_unavailable', 'repair_invalid', 'invalid_schema', 'unavailable'])
async def test_unusable_assessment_regrades_same_snapshot(tmp_path, monkeypatch, defect):
    invalid = _output()
    invalid['behaviors'].pop()
    first = {'invalid_schema': json.dumps(invalid),
             'unavailable': '{"status":"unavailable","reason":"temporary evaluator limitation"}'}
    workspaces = []

    async def run(_config, workspace, *_args):
        workspaces.append((workspace, (workspace / 'request.json').read_bytes()))
        return first.get(defect, 'Long analysis without a grading object')

    async def closeout(_config, workspace):
        workspaces.append((workspace, (workspace / 'request.json').read_bytes()))
        return json.dumps(_output((0.5, 0.5)))

    repair = AsyncMock(return_value='{"repair_unavailable":true}' if defect == 'repair_unavailable' else 'still invalid')
    monkeypatch.setattr(llm_as_judge, 'run_judge_agent', run)
    monkeypatch.setattr(llm_as_judge, 'run_judge_closeout', closeout)
    monkeypatch.setattr(llm_as_judge, 'repair_judge_json', repair)
    result = await LlmAsJudgeJudger(_config(judge_max_retries=1)).judge(**_arguments(tmp_path))
    assert workspaces[0] == workspaces[1]
    assert len(workspaces) == 2
    assert result.metadata['parsed']['overall_score'] == .5
    assert result.metadata['assessment_attempts'] == 2
    error = json.loads(next(tmp_path.rglob('validation_error_1.json')).read_text())
    assert error['failure_kind'] == defect


@pytest.mark.asyncio
async def test_invalid_assessment_exhaustion_keeps_all_attempt_diagnostics(tmp_path, monkeypatch):
    agent = AsyncMock(return_value='{"status":"unavailable","reason":"verification unavailable"}')
    closeout = AsyncMock(return_value='{"status":"unavailable","reason":"verification unavailable"}')
    monkeypatch.setattr(llm_as_judge, 'run_judge_agent', agent)
    monkeypatch.setattr(llm_as_judge, 'run_judge_closeout', closeout)
    with pytest.raises(EvaluationInfrastructureError, match='unavailable'):
        await LlmAsJudgeJudger(_config(judge_max_retries=2)).judge(**_arguments(tmp_path))
    assert agent.await_count == 1
    assert closeout.await_count == 2
    assert len(list(tmp_path.rglob('validation_error_*.json'))) == 3
    assert not list(tmp_path.rglob('assessment.json'))


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [asyncio.CancelledError(), RuntimeError('authentication failed')])
async def test_cancellation_and_permanent_service_failures_do_not_regrade(tmp_path, monkeypatch, failure):
    agent = AsyncMock(side_effect=failure)
    closeout = AsyncMock()
    monkeypatch.setattr(llm_as_judge, 'run_judge_agent', agent)
    monkeypatch.setattr(llm_as_judge, 'run_judge_closeout', closeout)
    expected = asyncio.CancelledError if isinstance(failure, asyncio.CancelledError) else EvaluationInfrastructureError
    with pytest.raises(expected):
        await LlmAsJudgeJudger(_config(judge_max_retries=2)).judge(**_arguments(tmp_path))
    assert agent.await_count == 1
    closeout.assert_not_awaited()


@pytest.mark.asyncio
async def test_truncated_model_output_is_persisted_and_regraded(tmp_path, monkeypatch):
    path = tmp_path / 'model.json'
    path.write_text(json.dumps({
        'model_client_config': {'client_provider': 'OpenAI', 'api_key': 'test', 'api_base': 'https://example.test/v1'},
        'model_request_config': {'model': 'test', 'max_tokens': 8192},
    }))
    calls = []

    async def invoke(_self, messages, **kwargs):
        calls.append(messages)
        if len(calls) == 1:
            return AssistantMessage(content='unfinished assessment', finish_reason='length',
                                    usage_metadata=UsageMetadata(output_tokens=8192))
        return AssistantMessage(content=json.dumps(_output()), finish_reason='stop',
                                usage_metadata=UsageMetadata(output_tokens=200))

    monkeypatch.setattr(Model, 'invoke', invoke)
    repair = AsyncMock()
    monkeypatch.setattr(llm_as_judge, 'repair_judge_json', repair)
    result = await LlmAsJudgeJudger(replace(_config(judge_max_retries=1), judge_model_config_ref=str(path))).judge(
        **_arguments(tmp_path),
    )
    assert len(calls) == 2
    repair.assert_not_awaited()
    assert result.metadata['parsed']['overall_score'] == .5
    error = json.loads(next(tmp_path.rglob('validation_error_1.json')).read_text())
    assert error['failure_kind'] == 'truncated'
    diagnostic = json.loads(next(tmp_path.rglob('diagnostics_1.json')).read_text())
    assert diagnostic['model_calls'][0]['finish_reason'] == 'length'
    assert diagnostic['model_calls'][0]['usage']['output_tokens'] == 8192


@pytest.mark.asyncio
async def test_truncated_format_repair_regrades_instead_of_becoming_invalid_schema(tmp_path, monkeypatch):
    from openjiuwen.rsi.harness_rsi.evaluator.judger.judge_diagnostics import record_judge_model_response

    monkeypatch.setattr(llm_as_judge, 'run_judge_agent', AsyncMock(return_value='Malformed original output'))

    async def repair(*_args):
        record_judge_model_response(AssistantMessage(content='{"status":', finish_reason='length',
                                                     usage_metadata=UsageMetadata(output_tokens=8192)))
        return '{"status":'

    monkeypatch.setattr(llm_as_judge, 'repair_judge_json', repair)
    monkeypatch.setattr(llm_as_judge, 'run_judge_closeout', AsyncMock(return_value=json.dumps(_output())))
    result = await LlmAsJudgeJudger(_config(judge_max_retries=1)).judge(**_arguments(tmp_path))
    assert result.metadata['parsed']['overall_score'] == .5
    error = json.loads(next(tmp_path.rglob('validation_error_1.json')).read_text())
    assert error['failure_kind'] == 'truncated'
