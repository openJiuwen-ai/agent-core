# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Section 19 real core contracts and closed executor boundaries; no provider calls."""

import copy
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError, build_error
from openjiuwen.core.foundation.llm.schema.message import AssistantMessage, UsageMetadata
from openjiuwen.core.single_agent.agents.react_agent import ReActAgent
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, AgentCallbackEvent, rail
from openjiuwen.harness.tools.browser_move.decision.action_space import build_menu
from openjiuwen.harness.tools.browser_move.playwright_runtime.phase_contract import (
    BrowserPhaseTool, constrain_actions, task_state,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.page_state import BrowserPageState
from openjiuwen.harness.tools.browser_move.playwright_runtime.runtime import BrowserRuntimeRail as Rail
from tests.unit_tests.harness.tools.browser_move.test_browser_fact_handoff import cart_session
from tests.unit_tests.harness.tools.browser_move.test_browser_shared_state_delivery import persistent_session
from tests.unit_tests.harness.tools.browser_move.test_browser_source_continuity import register_control


def output_limit_error(finish='length'):
    response = AssistantMessage(content='', finish_reason=finish, usage_metadata=UsageMetadata(output_tokens=8192))
    with pytest.raises(BaseError) as caught:
        ReActAgent._raise_for_model_response_error(response)
    return caught.value


@pytest.mark.parametrize('finish', ['length', 'max_tokens'])
@pytest.mark.parametrize('wrapped', [False, True])
def test_real_core_output_limit_contract_survives_wrappers(finish, wrapped):
    exception = output_limit_error(finish)
    if wrapped:
        exception = build_error(StatusCode.MODEL_CALL_FAILED, error_msg='SDK envelope', cause=exception)
    failure = Rail._model_failure_details(exception)
    assert failure == {'code': 'model_output_incomplete', 'source': 'model', 'stage': 'output',
                       'retryable': True, 'finish_reason': finish, 'output_tokens': 8192}
    unrelated = Rail._model_failure_details(RuntimeError('length=8192 in unrelated request'))
    assert unrelated['code'] == 'model_error' and not unrelated['retryable']


@pytest.mark.asyncio
@pytest.mark.parametrize('remaining,resumes,can_resume', [(120, 0, True), (10, 0, False), (-1, 0, False), (120, 1, False)])
async def test_real_rail_output_limit_retains_facts_and_original_resume_boundary(remaining, resumes, can_resume):
    _, initial = cart_session()
    initial.update(deadline_at=time.time() + remaining, resume_count=resumes)
    session, _ = persistent_session(initial)
    runtime_rail = Rail(MagicMock())

    class Callbacks:
        async def execute(self, event, ctx):
            if event == AgentCallbackEvent.ON_MODEL_EXCEPTION:
                await runtime_rail.on_model_exception(ctx)
            elif event == AgentCallbackEvent.AFTER_MODEL_CALL:
                await runtime_rail.after_model_call(ctx)

    class Model:
        attempts = 0

        @rail(before=AgentCallbackEvent.BEFORE_MODEL_CALL, after=AgentCallbackEvent.AFTER_MODEL_CALL,
              on_exception=AgentCallbackEvent.ON_MODEL_EXCEPTION)
        async def invoke(self, ctx):
            self.attempts += 1
            raise output_limit_error()

    ctx = AgentCallbackContext(agent=SimpleNamespace(agent_callback_manager=Callbacks()), session=session,
                               inputs=SimpleNamespace(response=None),
                               extra={'__browser_task_deadline__': time.monotonic() + remaining})
    model = Model()
    result = (await model.invoke(ctx))['authoritative_browser_result']
    assert model.attempts == 1  # No automatic browser/model replay added.
    assert result['status'] == 'partial' and result['terminal_reason'] == 'model_output_incomplete'
    assert result['retryable'] is can_resume
    assert result['model_failure']['output_tokens'] == 8192
    assert '3 items' in str(result['observations'])
    assert task_state(session)['execution_journal'] == initial['execution_journal']
    assert 'Do not replay' in result['recommended_recovery']
    if can_resume:
        resumed = runtime_rail._resume_task_state(session, task_state(session), 'Summarize retained facts briefly')
        assert resumed['resume_count'] == 1 and resumed['deadline_at'] == initial['deadline_at']
        assert not task_state(session).get('model_failure')
        assert resumed['execution_journal'] == initial['execution_journal']


@pytest.mark.asyncio
async def test_only_exact_selected_business_control_is_available_after_persistent_handoff():
    _, initial = cart_session()
    session, _ = persistent_session(initial)
    page = BrowserPageState()
    page.advance(url='https://example.test/detail')
    selected = register_control(page, '#selected', 21)
    alternative = register_control(page, '#alternative', 22)
    controls = page.export_decision_observation(include_current_registry=True)['controls']
    runtime = SimpleNamespace(ensure_page_state=lambda: page)

    def menu_for(state):
        menu = build_menu(controls, 'Add selected object only', limit=20, allow_page_actions=True,
                          page={'url': page.url, 'page_guard': {'document': 'doc'}}, page_operations={'read_text'})
        constrain_actions(menu, state, controls)
        return menu

    menu = menu_for(task_state(session))
    assert not any(s['op'] == 'click' for s in menu.steps.values())
    assert menu.excluded['local_target_binding_required'] == 2
    assert any(s['op'] == 'read_text' for s in menu.steps.values())
    for targets in ([], [selected]):
        result = await BrowserPhaseTool(runtime).invoke(
            {'op': 'set', 'objective': 'Perform selected operation only',
             'target_ids': targets, 'allowed_operations': ['click']}, session=session,
        )
        assert result.success
        clicks = {s['target_id'] for s in menu_for(task_state(session)).steps.values() if s['op'] == 'click'}
        assert clicks == set(targets)
        assert alternative not in clicks
    stale = copy.deepcopy(task_state(session))
    stale['active_phase_contract']['handoff'] = {'reason': 'bound_document_changed'}
    assert not any(s['op'] == 'click' for s in menu_for(stale).steps.values())
    selected_control = next(c for c in controls if c['target_id'] == selected)
    selected_control['decision_state']['node_guard']['document'] = 'other-document'
    assert not any(s['op'] == 'click' for s in menu_for(task_state(session)).steps.values())


ZERO = '### Error\nError: "button[aria-label="关闭"]" does not match any elements.'
SENT = ('### Error\nError: locator.click: Timeout 2500ms exceeded.\nCall log:\n'
        '  - waiting for locator("button")\n  - performing click action\n'
        '  - click action done\n  - waiting for scheduled navigations to finish')


@pytest.mark.asyncio
@pytest.mark.parametrize('envelope', ['error', 'data', 'mapping', 'exception'])
@pytest.mark.parametrize('error,expected', [(ZERO, False), (SENT, None),
    ('### Error\nError: locator.click: Timeout 2500ms exceeded.\nCall log:\n' +
     '  - waiting for locator("button")\n' * 100 + '  - performing click action', None)])
async def test_primitive_preserves_full_native_preflight_receipt(envelope, error, expected):
    from openjiuwen.harness.tools.base_tool import ToolOutput
    from openjiuwen.harness.tools.browser_move.playwright_runtime import execution_journal as journal
    from tests.unit_tests.harness.tools.browser_move.test_browser_runtime_rail import _make_bare_runtime
    from tests.unit_tests.harness.tools.browser_move.test_browser_jev_phase_contract import call

    runtime = _make_bare_runtime()
    if envelope == 'data':
        result = ToolOutput(success=True, data=error)
    elif envelope == 'mapping':
        result = ToolOutput(success=True, data={'error': error})
    else:
        result = ToolOutput(success=False, error=error)
    native = SimpleNamespace(invoke=AsyncMock(return_value=result,
                             side_effect=RuntimeError(error) if envelope == 'exception' else None))
    runtime._get_playwright_mcp_tool = AsyncMock(return_value=native)
    session, _ = persistent_session(Rail._build_phase_state('Use observed control'))
    inputs = call('primitive', 'browser_batch_interact')
    inputs.tool_args = {'steps': [{'op': 'click', 'selector': 'button'}]}
    journal.prepare(session, inputs)
    with journal.execution_scope(session, inputs):
        result = await runtime._run_single_batch_primitive(inputs.tool_args['steps'][0])
    assert result['steps'][0]['executed'] is expected
    assert result['steps'][0]['error'] == error
    receipt = journal.record_result(session, inputs, {'success': False}, result)
    assert receipt['executed'] is expected
    assert receipt['execution_state'] == ('rejected_before_dispatch' if expected is False else 'dispatched_unknown')
    native.invoke.assert_awaited_once()


@pytest.mark.parametrize('path,executed', [('native', False), ('compact_preflight', False),
                                         ('compact_sent', None), ('script', None)])
def test_native_compact_and_script_receipts_keep_dispatch_boundary(path, executed):
    from openjiuwen.harness.tools.browser_move.playwright_runtime import execution_journal as journal
    from tests.unit_tests.harness.tools.browser_move.test_browser_jev_phase_contract import call

    session, _ = persistent_session(Rail._build_phase_state('Inspect controls'))
    name = 'browser_click' if path == 'native' else 'browser_evaluate' if path == 'script' else 'browser_batch_interact'
    inputs = call(path, name)
    inputs.tool_args = {'steps': [{'op': 'click', 'selector': 'button'}]} if path.startswith('compact') else {}
    journal.prepare(session, inputs)
    result = {'ok': False, 'error': ZERO}
    if path.startswith('compact'):
        result['steps'] = [{'index': 0, 'op': 'click', 'ok': False, 'error': ZERO,
                            'executed': False if path == 'compact_preflight' else True}]
    receipt = journal.record_result(session, inputs, {'success': False}, result)
    assert receipt['executed'] is executed


def registered_runtime(monkeypatch, names, allowed=None):
    from openjiuwen.core.runner import Runner
    from openjiuwen.harness.tools.browser_move.playwright_runtime.runtime import BrowserAgentRuntime

    registry = {'test-browser': set(names), 'other-browser': {'browser_tabs'}}
    monkeypatch.setattr(Runner.resource_mgr, 'get_mcp_tool_ids',
                        lambda server: [f'{server}.{name}' for name in registry.get(server, [])])
    monkeypatch.setattr(Runner.resource_mgr, 'get_tool',
                        lambda key: SimpleNamespace(card=SimpleNamespace(name=key.split('.')[-1])))
    runtime = BrowserAgentRuntime.__new__(BrowserAgentRuntime)
    runtime._service = SimpleNamespace(mcp_cfg=SimpleNamespace(server_id='test-browser'), allowed_tool_names=allowed)
    runtime.ensure_page_state().advance(url='https://example.test/')
    runtime._get_playwright_mcp_tool = AsyncMock()
    runtime._get_playwright_run_code_tool = AsyncMock()
    runtime.call_playwright_tool = AsyncMock(return_value={'ok': True})
    runtime.record_tool_reference_state = MagicMock()
    runtime.capture_browser_metadata = AsyncMock(return_value=({}, 'metadata unavailable'))
    return runtime, registry


@pytest.mark.asyncio
async def test_missing_tabs_menu_and_direct_tool_reject_before_dispatch_and_recover_on_registration(monkeypatch):
    from openjiuwen.harness.tools.browser_move.decision.policy_model import BrowserPolicyModel
    from openjiuwen.harness.tools.browser_move.playwright_runtime.policy_page_action import BrowserPageActionTool
    from openjiuwen.harness.tools.browser_move.playwright_runtime import policy_page_action as module

    runtime, registry = registered_runtime(monkeypatch, {'browser_navigate', 'browser_run_code_unsafe'})
    marked = MagicMock()
    monkeypatch.setattr(module, 'mark_dispatched', marked)
    policy = BrowserPolicyModel.__new__(BrowserPolicyModel)
    policy.runtime = runtime
    assert 'tabs' not in policy._page_operations() and 'select_tab' not in policy._page_operations()
    assert {'navigate', 'read_text', 'wait'} <= policy._page_operations()
    inputs = {'generation_id': runtime.generation_id, 'op': 'tabs'}
    result = await BrowserPageActionTool(runtime).invoke(inputs)
    assert result.data['executed'] is False and 'browser_tabs' in result.data['reason']
    assert 'tool_unavailable' in result.data['reason']
    marked.assert_not_called()
    runtime.call_playwright_tool.assert_not_awaited()
    registry['test-browser'].add('browser_tabs')
    assert 'tabs' in policy._page_operations()
    result = await BrowserPageActionTool(runtime).invoke(inputs)
    assert result.success and result.data['executed'] is True
    marked.assert_called_once()
    runtime._service.allowed_tool_names = ('browser_navigate',)
    assert 'tabs' not in policy._page_operations()
    denied = await BrowserPageActionTool(runtime).invoke(inputs)
    assert denied.data['executed'] is False and 'capability_denied' in denied.data['reason']
    assert runtime.call_playwright_tool.await_count == 1


@pytest.mark.asyncio
async def test_registry_resolution_failure_after_inventory_check_remains_undispatched(monkeypatch):
    from openjiuwen.harness.tools.browser_move.playwright_runtime import policy_page_action as module

    runtime, _ = registered_runtime(monkeypatch, {'browser_tabs'})
    runtime._get_playwright_mcp_tool.side_effect = RuntimeError('Registered Playwright MCP tool not found: browser_tabs')
    marked = MagicMock()
    monkeypatch.setattr(module, 'mark_dispatched', marked)
    result = await module.BrowserPageActionTool(runtime).invoke({'generation_id': runtime.generation_id, 'op': 'tabs'})
    assert result.data['executed'] is False
    assert result.data['execution_state'] == 'rejected_before_dispatch'
    assert 'tool not found: browser_tabs' in result.data['reason']
    marked.assert_not_called()
    runtime.call_playwright_tool.assert_not_awaited()


@pytest.mark.parametrize('registered,allowed,expected', [
    ({'browser_evaluate'}, None, {'wait'}),
    ({'browser_run_code'}, None, {'wait', 'read_text', 'find', 'scroll'}),
    ({'browser_run_code_unsafe'}, ('browser_navigate',), set()),
    ({'browser_tabs'}, (), set()),
])
def test_fixed_page_operations_follow_transport_and_permission_not_static_native_name(
    monkeypatch, registered, allowed, expected,
):
    from openjiuwen.harness.tools.browser_move.playwright_runtime.policy_page_action import page_operation_capabilities

    runtime, _ = registered_runtime(monkeypatch, registered, allowed)
    assert {op for op, reason in page_operation_capabilities(runtime).items() if not reason} == expected



@pytest.mark.asyncio
async def test_exact_bound_business_ack_is_not_replayed_and_reader_remains():
    from openjiuwen.harness.tools.browser_move.playwright_runtime import execution_journal as journal
    from openjiuwen.harness.tools.browser_move.playwright_runtime.phase_contract import set_phase
    from tests.unit_tests.harness.tools.browser_move.test_browser_jev_phase_contract import call
    from tests.unit_tests.harness.tools.browser_move.test_browser_jev_policy import (
        setup_policy, messages_for, choose_operation,
    )
    from tests.unit_tests.harness.tools.browser_move.test_browser_local_decision_loop import LOCAL_TOOLS

    policy, llm, client, runtime, context, captured = setup_policy(goal='Execute only the selected operation')
    page = runtime.ensure_page_state()
    page.decision_snapshot['page_guard'] = {'document': 'doc-a'}
    control = page.export_decision_targets()[0]
    page.get_target(control['target_id']).kind = ''
    control = page.export_decision_targets()[0]
    state = context.get_session_ref().get_state('')
    set_phase(state, {'objective': 'Execute selected operation', 'allowed_operations': ['click'],
                     'target_ids': [control['target_id']]}, [control], observation=page.export_decision_observation())
    choose_operation(client, 'CLICK')
    response = await policy.invoke(await messages_for(policy, context, captured), tools=LOCAL_TOOLS)
    assert response.tool_calls and response.tool_calls[0].id.startswith('jev_')
    inputs = call('ack_selected')
    inputs.tool_args = {'steps': [{'op': 'click', 'target_id': control['target_id']}]}
    journal.prepare(context.get_session_ref(), inputs, runtime)
    journal.record_result(context.get_session_ref(), inputs, {'success': True}, {'ok': True})
    page.decision_snapshot['capture_id'] = 'after-ack'
    choose_operation(client, 'EXTRACT_TEXT')
    response = await policy.invoke(await messages_for(policy, context, captured), tools=LOCAL_TOOLS)
    assert response.tool_calls and response.tool_calls[0].name == 'browser_page_action'
    assert 'CLICK' not in client.evaluate.call_args.args[0]['questions']['action']['criteria']
    assert response.metadata['browser_policy']['excluded']['acknowledged_action_recovery'] == 1
    llm.invoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_bound_but_not_actionable_target_reports_specific_exclusion_without_guessing():
    from openjiuwen.harness.tools.browser_move.playwright_runtime.phase_contract import set_phase, binding_targets
    from tests.unit_tests.harness.tools.browser_move.test_browser_jev_policy import (
        setup_policy, messages_for, choose_operation,
    )
    from tests.unit_tests.harness.tools.browser_move.test_browser_local_decision_loop import LOCAL_TOOLS

    policy, _, client, runtime, context, captured = setup_policy()
    page = runtime.ensure_page_state()
    page.decision_snapshot['page_guard'] = {'document': 'doc-a'}
    control = page.export_decision_targets()[0]
    state = context.get_session_ref().get_state('')
    set_phase(state, {'objective': 'Click selected control', 'target_ids': [control['target_id']],
                     'allowed_operations': ['click']}, [control])
    page.get_target(control['target_id']).actionable = False
    choose_operation(client, 'HANDOFF')
    response = await policy.invoke(await messages_for(policy, context, captured), tools=LOCAL_TOOLS)
    excluded = response.metadata['browser_policy']['bound_target_exclusions']
    assert excluded == [{'target_id': control['target_id'], 'reason': 'not_actionable',
                         'enabled': True, 'actionable': False, 'clickable': True}]
    assert not binding_targets(page.export_decision_targets())['targets'][0]['actionable']
    assert 'CLICK' not in client.evaluate.call_args.args[0]['questions']['action']['criteria']



def test_task_deadline_remains_authoritative_over_wrapped_output_limit():
    error = TimeoutError('browser_task_deadline_exhausted')
    error.__cause__ = output_limit_error()
    assert Rail._model_failure_details(error) == {
        'code': 'task_deadline_exhausted', 'source': 'runtime', 'stage': 'task', 'retryable': False,
    }


@pytest.mark.asyncio
async def test_explicit_native_dispatch_cannot_be_erased_by_preflight_like_text():
    from openjiuwen.harness.tools.base_tool import ToolOutput
    from tests.unit_tests.harness.tools.browser_move.test_browser_runtime_rail import _make_bare_runtime

    runtime = _make_bare_runtime()
    native = SimpleNamespace(invoke=AsyncMock(return_value=ToolOutput(
        success=False, error=ZERO, data={'executed': True},
    )))
    runtime._get_playwright_mcp_tool = AsyncMock(return_value=native)
    result = await runtime._run_single_batch_primitive({'op': 'click', 'selector': 'button'})
    assert result['executed'] is None and result['execution_state'] == 'dispatched_unknown'


def test_core_length_with_real_answer_is_not_an_empty_output_failure():
    answer = AssistantMessage(content='The observed cart contains three items.', finish_reason='length',
                              usage_metadata=UsageMetadata(output_tokens=8192))
    ReActAgent._raise_for_model_response_error(answer)
