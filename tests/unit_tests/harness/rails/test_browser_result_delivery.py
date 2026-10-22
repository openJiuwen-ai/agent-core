# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Bounded browser sources survive the real parent renderer and tool offloader."""

import json
from types import SimpleNamespace

import pytest

from openjiuwen.core.context_engine.processor.forked.offloader.message_offloader import (
    MessageSummaryOffloader, MessageSummaryOffloaderConfig,
)
from openjiuwen.core.foundation.llm import AssistantMessage, ToolMessage
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.harness.rails.context_engineer.context_processor_rail import ContextProcessorRail
from openjiuwen.harness.tools.browser_move.playwright_runtime.runtime import BrowserRuntimeRail
from openjiuwen.harness.tools.subagent.task_tool import TaskTool


pytestmark = pytest.mark.level0


@pytest.mark.asyncio
@pytest.mark.parametrize('name,factory', [('browser_agent', None), ('custom_browser', 'browser_agent')])
async def test_bounded_browser_delivery_stays_inline_without_protecting_other_subagent_outputs(name, factory):
    supplied = MessageSummaryOffloaderConfig(protected_tool_names=['read_file', 'read_pdf'])
    rail = ContextProcessorRail([('MessageSummaryOffloader', supplied)], preset=False)
    config = SimpleNamespace()
    agent = SimpleNamespace(react_agent=SimpleNamespace(_config=config), deep_config=SimpleNamespace(subagents=[
        SimpleNamespace(agent_card=SimpleNamespace(name=name), factory_name=factory),
    ]))
    rail.init(agent)
    effective = dict(config.context_processors)['MessageSummaryOffloader']
    assert supplied.protected_tool_names == ['read_file', 'read_pdf']
    assert effective.protected_tool_names == ['read_file', 'read_pdf', f'task_tool:{name}']
    # Re-initialization does not accumulate rules or mutate the caller's config.
    rail.init(agent)
    assert dict(config.context_processors)['MessageSummaryOffloader'] == effective

    state = BrowserRuntimeRail._build_phase_state('Compare retained sources')
    state['structured_evidence'] = [
        {'source': f'https://example.test/{i}', 'kind': 'page_observation', 'raw_text': f'SOURCE_{i}: ' + 'x' * 3000}
        for i in range(20)
    ]
    result = BrowserRuntimeRail._authoritative_terminal_payload(state)
    output = json.dumps({'browser_result': result})
    data = TaskTool._build_result_data({'authoritative_browser_result': result}, output, agent_id='browser',
                                     subagent_type='browser_agent', sub_session_id='child')
    rendered = TaskTool.render_for_llm(TaskTool.__new__(TaskTool), SimpleNamespace(data=data))
    assert all(rendered.count(f'SOURCE_{i}:') == 1 for i in range(20))
    assert 'resume_context' not in rendered
    message = ToolMessage(content=rendered, tool_call_id='browser-result')
    parent_call = AssistantMessage(content='', tool_calls=[ToolCall(
        id='browser-result', type='function', name='task_tool', arguments=json.dumps({'subagent_type': name}),
    )])
    offloader = MessageSummaryOffloader(effective)
    # Exercise the actual per-message path with a deliberately tiny threshold.
    processed = await offloader._process_added_message(
        message, SimpleNamespace(), [parent_call, message], threshold_tokens=1, max_chars=10,
    )
    assert processed is message and processed.content == rendered
    other_call = parent_call.model_copy(deep=True)
    other_call.tool_calls[0].arguments = json.dumps({'subagent_type': 'research_agent'})
    assert not offloader._is_protected_tool_message(message, [other_call, message])


def test_parent_without_browser_preserves_existing_offload_configuration():
    supplied = MessageSummaryOffloaderConfig(protected_tool_names=['read_file'])
    rail = ContextProcessorRail([('MessageSummaryOffloader', supplied)], preset=False)
    config = SimpleNamespace()
    agent = SimpleNamespace(react_agent=SimpleNamespace(_config=config), deep_config=SimpleNamespace(subagents=[]))
    rail.init(agent)
    assert dict(config.context_processors)['MessageSummaryOffloader'].protected_tool_names == ['read_file']
