# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
import json
from types import SimpleNamespace

import pytest

from openjiuwen.core.foundation.llm import ToolMessage
from openjiuwen.rsi.harness_rsi.evaluator.judger.evidence_guard import (
    TOOL_BYTES,
    GuardedJudgeModel,
    JudgeEvidenceTool,
    bounded_text,
    guard_messages,
)
from openjiuwen.rsi.harness_rsi.evaluator.judger.judge_runtime import JudgeBudgetRail


def _prepare(messages, options=None, window=262144, model_name='test'):
    model = SimpleNamespace(model_config=SimpleNamespace(
        max_tokens=100000, context_window=window, model_name=model_name,
    ))
    return GuardedJudgeModel._prepare(model, messages, options or {})


def test_complete_closeout_preserves_evidence_and_fits_output():
    rows = [{'role': 'system', 'content': 'policy' * 900},
            {'role': 'user', 'content': 'evidence' * 24569}]
    original = json.dumps(rows)
    options = {'temperature': 0.0}
    guarded, adjusted = _prepare(rows, options)
    assert guarded == rows
    assert json.dumps(rows) == original
    assert options == {'temperature': 0.0}
    assert 'max_tokens' not in adjusted
    assert guard_messages(guarded, reserve=0) == guarded


def test_explicit_window_and_output_override():
    rows = [{'role': 'user', 'content': 'x' * 300000}]
    assert _prepare(rows, window=1048576)[1] == {}
    small = [{'role': 'user', 'content': 'x' * 190000}]
    assert 'max_tokens' not in _prepare(small, {'max_tokens': 20000})[1]
    assert _prepare(rows)[0] == rows


@pytest.mark.parametrize('name', ['deepseek-v4-flash', 'deepseek-v4-pro'])
def test_missing_window_uses_core_model_capacity(name):
    rows = [{'role': 'user', 'content': 'x' * 260000}]
    guarded, options = _prepare(rows, window=None, model_name=name)
    assert guarded == rows
    assert 'max_tokens' not in options
    assert _prepare(rows, window=262144, model_name=name)[0] == rows


def test_unknown_model_uses_core_conservative_default():
    from openjiuwen.core.context_engine.context.context_utils import ContextUtils

    limit = ContextUtils.resolve_context_max(model_name='unknown-test-model')
    rows = [{'role': 'user', 'content': 'x' * limit}]
    assert _prepare(rows, window=None, model_name='unknown-test-model')[0] == rows


def test_schemas_and_unicode_count_toward_budget():
    rows = [{'role': 'user', 'content': '\u4e2d' * 60000}]
    options = {'tools': [{'name': 'read', 'description': 'x' * 40000}]}
    guarded, adjusted = _prepare(rows, options)
    assert guarded == rows
    assert 'max_tokens' not in adjusted
    guard_messages(guarded, options['tools'], reserve=0)


def test_reclaim_tools_before_lowering_output_limit():
    rows = [{'role': 'user', 'content': 'rubric'},
            {'role': 'tool', 'content': 'x' * 5000000, 'tool_call_id': '1'}]
    guarded, options = _prepare(rows)
    assert 'max_tokens' not in options
    assert len(guarded[1]['content']) < 15000


@pytest.mark.asyncio
@pytest.mark.parametrize('streaming', [False, True])
async def test_actual_model_boundary_forwards_adjusted_budget(tmp_path, monkeypatch, streaming):
    from openjiuwen.core.foundation.llm import Model, SystemMessage, UserMessage
    from openjiuwen.rsi.harness_rsi.config import EvaluatorConfig
    from openjiuwen.rsi.harness_rsi.evaluator.judger.judge_runtime import _judge_model

    config = tmp_path / 'model.json'
    config.write_text(json.dumps({
        'model_client_config': {'client_provider': 'OpenAI', 'api_key': 'test',
                                'api_base': 'https://example.test/v1'},
        'model_request_config': {'model': 'test', 'max_tokens': 100000, 'context_window': 262144},
    }))
    model = _judge_model(EvaluatorConfig(model_config_ref=str(config)))
    messages = [SystemMessage(content='policy'), UserMessage(content='x' * 500000)]
    seen = []

    async def invoke(_self, rows, **kwargs):
        seen.append((rows, kwargs))
        return 'ok'

    async def stream(_self, rows, **kwargs):
        seen.append((rows, kwargs))
        yield 'ok'

    monkeypatch.setattr(Model, 'invoke', invoke)
    monkeypatch.setattr(Model, 'stream', stream)
    if streaming:
        assert [item async for item in model.stream(messages)] == ['ok']
    else:
        assert await model.invoke(messages) == 'ok'
    rows, options = seen[0]
    assert rows == messages
    assert isinstance(rows[1], UserMessage)
    assert 'max_tokens' not in options
    assert model.model_config.max_tokens is None


def test_byte_cap_and_immutable_input():
    for text in ['x' * 5000000, chr(0x4E2D) * 20000, '\\' * 20000]:
        assert len(bounded_text(text).encode()) <= TOOL_BYTES
    rows = [{'role': 'system', 'content': 'policy'}, {'role': 'user', 'content': 'rubric'},
            {'role': 'tool', 'tool_call_id': '1', 'content': 'x' * 5000000}]
    guarded = guard_messages(rows)
    assert guarded[:2] == rows[:2]
    assert len(rows[2]['content']) == 5000000
    assert len(guarded[2]['content']) < 15000
    large = [{'role': 'user', 'content': 'x' * 300000}]
    assert guard_messages(large, limit=1) == large


@pytest.mark.asyncio
async def test_json_pointer_and_path_boundary(tmp_path):
    (tmp_path / 'large.json').write_text(json.dumps({'a/b': {'states': 137, 'rows': list(range(50000))}}))
    tool = JudgeEvidenceTool(tmp_path, 'test')
    result = await tool.invoke({'path': 'large.json', 'pointer': '/a~1b/states'})
    assert json.loads(result.data['content'])['content'] == '137'
    result = await tool.invoke({'path': 'large.json', 'pointer': '/a~1b/rows', 'item_offset': 10})
    page = json.loads(json.loads(result.data['content'])['content'])
    assert page == {'count': 50000, 'offset': 10, 'items': list(range(10, 20))}
    for path in ['../outside.json', str(tmp_path.parent / 'outside.json')]:
        assert not (await tool.invoke({'path': path})).success


@pytest.mark.asyncio
async def test_native_tool_message_is_bounded(tmp_path):
    msg = ToolMessage(content='x' * 5000000, tool_call_id='test')
    inputs = SimpleNamespace(tool_result={'success': True, 'data': {'content': msg.content}},
                             tool_msg=msg, tool_name='grep', tool_args={'path': 'results.json'})
    await JudgeBudgetRail(8, tmp_path / 'tools.jsonl').after_tool_call(SimpleNamespace(inputs=inputs))
    assert len(msg.content.encode()) <= TOOL_BYTES
    assert inputs.tool_result.success


@pytest.mark.asyncio
async def test_pagination_survives_rail_and_model_boundary_losslessly(tmp_path):
    text = ('\\\"\u4e2d\n' * 20000)
    (tmp_path / 'escaped.txt').write_text(text, encoding='utf-8', newline='')
    tool = JudgeEvidenceTool(tmp_path, 'paging')
    offset = 0
    parts = []
    while True:
        result = await tool.invoke({'path': 'escaped.txt', 'byte_offset': offset})
        message = ToolMessage(content=result.data['content'], tool_call_id='page')
        before = message.content
        inputs = SimpleNamespace(tool_result=result, tool_msg=message,
                                 tool_name='read_evidence', tool_args={'path': 'escaped.txt'})
        await JudgeBudgetRail(20, tmp_path / 'events.jsonl').after_tool_call(SimpleNamespace(inputs=inputs))
        guarded = guard_messages([message])
        assert guarded[0].content == before
        page = json.loads(guarded[0].content)
        parts.append(page['content'])
        if not page['truncated']:
            break
        assert page['next_byte_offset'] > offset
        offset = page['next_byte_offset']
    assert ''.join(parts) == text
