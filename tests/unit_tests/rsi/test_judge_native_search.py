# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Judge searches stay read-only and bounded without spawning a sandboxed rg."""
import json

import pytest

from openjiuwen.rsi.harness_rsi.evaluator.judger.evidence_guard import TOOL_BYTES
from openjiuwen.rsi.harness_rsi.evaluator.judger.judge_search import JudgeSearchTool


@pytest.mark.asyncio
async def test_search_reads_frozen_files_without_external_executable(tmp_path, monkeypatch):
    import subprocess
    monkeypatch.setattr(subprocess, 'Popen', lambda *_a, **_k: pytest.fail('must not execute rg'))
    (tmp_path / 'answer.txt').write_text('before\nNeedle in evidence\nafter\n')
    tool = JudgeSearchTool(tmp_path, 'test')
    result = await tool.invoke({'pattern': 'needle', 'path': '.', 'ignore_case': True})
    assert result.success
    data = json.loads(result.data['content'])
    assert data['matches'][0]['path'] == 'answer.txt'
    assert data['matches'][0]['line'] == 2
    assert data['scan_complete']


@pytest.mark.asyncio
async def test_search_rejects_escape_and_marks_omitted_evidence(tmp_path):
    root = tmp_path / 'evidence'
    root.mkdir()
    secret = tmp_path / 'secret.txt'
    secret.write_text('secret needle')
    (root / 'link.txt').symlink_to(secret)
    tool = JudgeSearchTool(root, 'test')
    assert not (await tool.invoke({'pattern': 'needle', 'path': '../secret.txt'})).success
    result = await tool.invoke({'pattern': 'needle', 'path': '.'})
    data = json.loads(result.data['content'])
    assert not data['scan_complete']
    assert not data['matches']
    assert 'secret needle' not in result.data['content']


@pytest.mark.asyncio
async def test_search_result_limit_reports_incomplete_scan_and_keeps_byte_cap(tmp_path):
    (tmp_path / 'large.txt').write_text(('needle' + '中' * 1000 + '\n') * 1000)
    result = await JudgeSearchTool(tmp_path, 'test').invoke({'pattern': 'needle', 'max_results': 2})
    data = json.loads(result.data['content'])
    assert len(data['matches']) == 2
    assert not data['scan_complete']
    assert len(result.data['content'].encode()) <= TOOL_BYTES
    assert 'not proof of absence' in data['instruction']


@pytest.mark.asyncio
async def test_native_judge_can_use_search_and_finish_grading(tmp_path, monkeypatch):
    from openjiuwen.core.foundation.llm import AssistantMessage, Model, ToolCall, ToolMessage
    from openjiuwen.rsi.harness_rsi.config import EvaluatorConfig
    from openjiuwen.rsi.harness_rsi.evaluator.case_backend import CaseExecutionResult
    from openjiuwen.rsi.harness_rsi.evaluator.judger import LlmAsJudgeJudger
    from tests.unit_tests.rsi.test_evaluator_agent import _case, _output

    config = tmp_path / 'model.json'
    config.write_text(json.dumps({
        'model_client_config': {'client_provider': 'OpenAI', 'api_key': 'test', 'api_base': 'https://example.test/v1'},
        'model_request_config': {'model': 'test'},
    }))
    (tmp_path / 'artifacts').mkdir()
    (tmp_path / 'artifacts' / 'answer.txt').write_text('evidence line\n' * 7000 + 'needle at the end\n')
    search_results = []

    async def invoke(_self, messages, **kwargs):
        if kwargs.get('tools') is not None:
            tool_messages = [message for message in messages if isinstance(message, ToolMessage)]
            if not tool_messages:
                return AssistantMessage(content='', tool_calls=[ToolCall(
                    id='search', type='function', name='grep',
                    arguments=json.dumps({'pattern': 'needle', 'path': 'artifacts'}),
                )])
            search_results.append(tool_messages[-1].content)
        return AssistantMessage(content=json.dumps(_output()))

    monkeypatch.setattr(Model, 'invoke', invoke)
    result = await LlmAsJudgeJudger(EvaluatorConfig(
        judge_model_config_ref=str(config), judge_agent_max_iterations=3, judge_max_retries=0,
    )).judge(case=_case(), execution_result=CaseExecutionResult('done', 'passed'), output_dir=str(tmp_path))
    assert result.metadata['parsed']['overall_score'] == .5
    assert search_results
    assert 'needle at the end' in search_results[0]
    events = [json.loads(line) for line in next(tmp_path.rglob('tool_events.jsonl')).read_text().splitlines()]
    assert events[0]['tool'] == 'grep'
    assert events[0]['success']
