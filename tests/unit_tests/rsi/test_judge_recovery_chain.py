# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Exercise native reading, complete-evidence recovery and transport retry together."""
import json

import pytest
from PIL import Image

from openjiuwen.core.foundation.llm import AssistantMessage, Model
from openjiuwen.rsi.harness_rsi.config import EvaluatorConfig
from openjiuwen.rsi.harness_rsi.evaluator.case_backend import CaseExecutionResult
from openjiuwen.rsi.harness_rsi.evaluator.errors import EvaluationInfrastructureError
from openjiuwen.rsi.harness_rsi.evaluator.judger import LlmAsJudgeJudger


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['text', 'image', 'no_vision', 'retry', 'retry_503',
                                 'retry_empty', 'retry_exhausted'])
async def test_large_native_closeout_chain(tmp_path, monkeypatch, mode):
    model_path = tmp_path / 'model.json'
    model_path.write_text(json.dumps({
        'model_client_config': {'client_provider': 'OpenAI', 'api_key': 'test',
                                'api_base': 'https://example.test/v1', 'supports_vision': mode != 'no_vision'},
        'model_request_config': {'model': 'test', 'context_window': 1048576, 'max_tokens': 100000},
    }))
    artifacts = tmp_path / 'artifacts'
    artifacts.mkdir()
    text = 'complete evidence\n' * 18000
    (artifacts / 'answer.txt').write_text(text, encoding='utf-8', newline='')
    has_image = mode in {'image', 'no_vision'}
    if has_image:
        Image.new('RGB', (4, 4), 'red').save(artifacts / 'image.png')
    calls = []

    async def invoke(_self, messages, **kwargs):
        if len(messages) == 1:
            return AssistantMessage(content='red')
        calls.append(messages)
        if len(calls) == 1:
            return AssistantMessage(content='Read [A] before returning the verdict.')
        content = messages[1].content
        if has_image:
            assert isinstance(content, list)
            assert any(block['type'] == 'image_url' for block in content)
            payload = json.loads(content[0]['text'])
        else:
            payload = json.loads(content)
        assert payload['evidence_files']['artifacts/answer.txt'] == text
        if mode == 'retry_exhausted' or (mode == 'retry' and len(calls) == 2):
            raise TimeoutError('temporary transport timeout')
        if mode == 'retry_503' and len(calls) == 2:
            raise RuntimeError('HTTP 503 Service Unavailable')
        if mode == 'retry_empty' and len(calls) == 2:
            return AssistantMessage(content='')
        return AssistantMessage(content='Checked [A]. ' + json.dumps({
            'status': 'completed', 'overall_reason': 'Complete evidence inspected',
            'behaviors': [{'id': 'rubric_001', 'score': .5, 'reason': 'partial',
                           'evidence': 'artifacts/answer.txt'}], 'forbidden_hits': [],
        }))

    monkeypatch.setattr(Model, 'invoke', invoke)
    judger = LlmAsJudgeJudger(EvaluatorConfig(
        judge_model_config_ref=str(model_path), judge_agent_max_iterations=1, judge_max_retries=1,
    ))
    kwargs = dict(case={'case_id': 'test', 'input': 'Report', 'reference': {'rubric': ['Complete report']}},
                  execution_result=CaseExecutionResult('submitted', 'passed'), output_dir=str(tmp_path))
    if mode in {'no_vision', 'retry_exhausted'}:
        with pytest.raises(EvaluationInfrastructureError):
            await judger.judge(**kwargs)
        assert not list(tmp_path.rglob('assessment.json'))
    else:
        result = await judger.judge(**kwargs)
        assert result.metadata['parsed']['overall_score'] == .5
        assert result.metadata['attempt'] == 2
    assert len(calls) == (1 if mode == 'no_vision' else 3 if mode.startswith('retry') else 2)
    if mode.startswith('retry'):
        assert calls[1] == calls[2]
