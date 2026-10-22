# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Real core lifecycle/persistence with offline browser and model boundaries."""

import copy
import json
import shutil
import subprocess
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from openjiuwen.core.foundation.llm.schema.message import AssistantMessage
from openjiuwen.core.foundation.llm.schema.config import ModelRequestConfig, ReasoningConfig
from openjiuwen.core.session.state.agent_state import StateCollection
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, AgentCallbackEvent, rail
from openjiuwen.harness.tools.browser_move.decision.action_space import build_menu
from openjiuwen.harness.tools.browser_move.decision.guard import DecisionGuard, canonical_arguments, validate_guard
from openjiuwen.harness.tools.browser_move.playwright_runtime import execution_journal, probes
from openjiuwen.harness.tools.browser_move.playwright_runtime.evidence import (
    PARENT_SOURCE_TEXT_BUDGET, SOURCE_TEXT_LIMIT, bounded_source_records, source_observations,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.page_state import BrowserPageState
from openjiuwen.harness.tools.browser_move.playwright_runtime.phase_contract import (
    BrowserPhaseTool, PHASE_KEY, constrain_actions, save, task_state,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.runtime import BrowserRuntimeRail as Rail
from openjiuwen.harness.subagents.browser_agent import _browser_model_with_temperature
from tests.unit_tests.harness.tools.browser_move.test_browser_fact_handoff import REPEAT, cart_session
from tests.unit_tests.harness.tools.browser_move.test_browser_observation_results import record
from tests.unit_tests.harness.tools.browser_move.test_browser_source_continuity import register_control


def persistent_session(state):
    collection = StateCollection()
    collection.update_global({PHASE_KEY: state, 'sibling': {'keep': 'parent context'}})
    return SimpleNamespace(get_state=collection.get_global, update_state=collection.update_global), collection


@pytest.mark.asyncio
@pytest.mark.parametrize('retry_allowed', [True, False])
async def test_real_rail_failed_attempt_preserves_retry_or_original_failure(retry_allowed):
    _, initial = cart_session()
    session, _ = persistent_session(initial)
    runtime_rail = Rail(MagicMock())

    class Callbacks:
        async def execute(self, event, ctx):
            if event == AgentCallbackEvent.ON_MODEL_EXCEPTION:
                if retry_allowed and ctx.retry_attempt == 0:
                    ctx.request_retry()
                await runtime_rail.on_model_exception(ctx)
            elif event == AgentCallbackEvent.AFTER_MODEL_CALL:
                await runtime_rail.after_model_call(ctx)

    class Model:
        attempts = 0

        @rail(before=AgentCallbackEvent.BEFORE_MODEL_CALL,
              after=AgentCallbackEvent.AFTER_MODEL_CALL,
              on_exception=AgentCallbackEvent.ON_MODEL_EXCEPTION)
        async def invoke(self, ctx):
            self.attempts += 1
            if self.attempts == 1:
                raise RuntimeError(REPEAT)
            ctx.inputs.response = AssistantMessage(content='The retained cart contains three items.')
            return ctx.inputs.response

    ctx = AgentCallbackContext(agent=SimpleNamespace(agent_callback_manager=Callbacks()), session=session,
                               inputs=SimpleNamespace(response=None),
                               extra={'__browser_task_deadline__': time.monotonic() + 120})
    model = Model()
    result = await model.invoke(ctx)
    if retry_allowed:
        assert model.attempts == 2 and isinstance(result, AssistantMessage)
        assert not task_state(session).get('model_failure')
    else:
        assert model.attempts == 1
        payload = result['authoritative_browser_result']
        assert payload['terminal_reason'] == 'model_repeated_output'
        assert '3 items' in str(payload['observations'])
        assert payload['execution']['business_effects'][0]['executed'] is True
    assert task_state(session)['execution_journal'] == initial['execution_journal']


@pytest.mark.asyncio
async def test_accepted_phase_survives_session_reload_and_restores_exact_click_candidate():
    _, initial = cart_session()
    initial['active_phase_contract'] = {
        'version': 1, 'objective': 'Old inspection', 'targets': [], 'bindings': [],
        'handoff': {'reason': 'bound_document_changed', 'offered': True},
    }
    session, collection = persistent_session(initial)
    page = BrowserPageState()
    page.advance(url='https://example.test/detail')
    target_id = register_control(page, '#chosen', 11)
    register_control(page, '#other', 12)
    result = await BrowserPhaseTool(SimpleNamespace(ensure_page_state=lambda: page)).invoke(
        {'op': 'set', 'objective': 'Click the selected operation',
         'target_ids': [target_id], 'allowed_operations': ['click', 'read_text']}, session=session,
    )
    assert result.success and not result.data['phase']['handoff']
    # Checkpoint restoration uses the real state class, not shared Python dict aliases.
    restored = StateCollection()
    restored.set_state(copy.deepcopy(collection.get_state()))
    persisted = restored.get_global(PHASE_KEY)
    assert not persisted['active_phase_contract'].get('handoff')
    controls = page.export_decision_observation(include_current_registry=True)['controls']
    menu = build_menu(controls, persisted['goal'], limit=20)
    constrain_actions(menu, persisted, controls)
    assert {s['target_id'] for s in menu.steps.values() if s['op'] == 'click'} == {target_id}
    assert persisted['execution_journal'] == initial['execution_journal']
    assert restored.get_global('sibling') == {'keep': 'parent context'}


def test_snapshot_save_removes_retired_fields_without_mutating_siblings_or_supplied_snapshot():
    old = {'goal': 'Read', 'model_failure': {'code': 'old'}, 'phase_invalidated_capture': 'old',
           'active_phase_contract': {'version': 1, 'handoff': {'offered': True}}}
    session, collection = persistent_session(old)
    replacement = {'goal': 'Read', 'active_phase_contract': {'version': 2}}
    save(session, replacement)
    assert task_state(session) == replacement
    replacement['active_phase_contract']['version'] = 99
    assert task_state(session)['active_phase_contract']['version'] == 2
    assert collection.get_global('sibling') == {'keep': 'parent context'}


def test_explicit_resume_really_clears_old_failure_and_synthesis_state():
    _, initial = cart_session()
    initial.update(status='partial', terminal_reason='model_transport_interrupted',
                   model_failure={'code': 'model_transport_interrupted', 'retryable': True},
                   last_worker_final='old incomplete answer', terminal_synthesis_started=True)
    session, _ = persistent_session(initial)
    runtime = Rail(MagicMock())
    resumed = runtime._resume_task_state(session, task_state(session), 'Read the current cart only')
    assert resumed['status'] == 'in_progress'
    assert not task_state(session).get('model_failure')
    assert 'last_worker_final' not in task_state(session)
    assert 'terminal_synthesis_started' not in task_state(session)
    assert task_state(session)['execution_journal'] == initial['execution_journal']


def test_new_task_and_clear_do_not_inherit_prior_task_handoff_errors_or_progress():
    _, initial = cart_session()
    initial.update(model_failure={'code': 'old'}, active_phase_contract={'handoff': {'offered': True}})
    session, collection = persistent_session(initial)
    session.get_session_id = lambda: 'browser-test'
    collection.update_global({'__browser_subagent_progress_state__': {'old_fields': ['old']}})
    runtime = Rail(MagicMock())
    new = runtime._ensure_task_state(session, 'Read another independent page')
    assert task_state(session) == new
    assert not task_state(session).get('active_phase_contract')
    assert not task_state(session).get('model_failure')
    assert not collection.get_global('__browser_subagent_progress_state__')
    runtime._clear_progress_state(session, preserve_task=True)
    assert task_state(session) == new
    runtime._clear_progress_state(session)
    assert task_state(session) == {}
    assert collection.get_global('sibling') == {'keep': 'parent context'}


def test_all_retained_pages_and_scopes_reach_parent_without_a_second_six_record_selection():
    state = Rail._build_phase_state('Read titles from two pages')
    for i in range(3):
        state['structured_evidence'].append({'kind': 'page_metadata', 'source': f'https://example.test/challenge/{i}',
                                             'values': {'url': f'https://example.test/challenge/{i}'}})
    for text, url, op in [('First page title', 'https://example.test/page/1', 'browser_evaluate'),
                          ('Pagination links', 'https://example.test/page/1', 'browser_evaluate'),
                          ('Second page title', 'https://example.test/page/2', 'browser_evaluate')]:
        state['last_page'] = {'url': url}
        Rail._record_structured_evidence(state, {'result': text}, tool_name=op, tool_args={'function': '() => null'})
    record(state, 'Later full-page body', 'https://example.test/cart')
    payload = Rail._authoritative_terminal_payload(state)
    text = str(payload['observations'])
    assert all(s in text for s in ('First page title', 'Second page title', 'Pagination links', 'Later full-page body'))
    assert len(payload['observations']) > 6
    assert not payload['observation_excerpt']['selection_truncated']


def test_source_text_is_less_compressed_and_truncation_is_explicit_with_recall_reference():
    state = Rail._build_phase_state('Read the full current source')
    body = 'Header\n' + 'a' * 5000 + '\nPreviously omitted fact\n' + 'b' * SOURCE_TEXT_LIMIT
    state['last_page'] = {'url': 'https://example.test/long'}
    Rail._record_structured_evidence(
        state, {'result': {'text': body}, 'recall_handle': 'retained_raw'},
        tool_name='browser_page_action', tool_args={'op': 'read_text'},
    )
    output = Rail._authoritative_terminal_payload(state)
    item = next(x for x in output['observations'] if x['kind'] == 'page_observation')
    assert '\nPreviously omitted fact\n' in item['raw_text']
    assert item['raw_text_length'] == len(body) and item['raw_text_truncated']
    assert item['recall_handle'] == 'retained_raw'
    assert item['observed_at_ms'] > 0
    assert output['observation_excerpt']['text_truncated_count'] >= 1


def test_parent_text_budget_keeps_all_source_identities_and_short_results():
    records = [{'kind': 'page_observation', 'source': f'https://example.test/{i}',
                'raw_text': str(i) + 'x' * SOURCE_TEXT_LIMIT, 'recall_handle': f'raw_{i}'} for i in range(20)]
    records.append({'kind': 'page_observation', 'source': 'https://example.test/short', 'raw_text': 'Short answer'})
    result = source_observations({'structured_evidence': records})
    assert len(result) == 21
    assert sum(len(item['raw_text']) for item in result) <= PARENT_SOURCE_TEXT_BUDGET
    assert result[-1]['raw_text'] == 'Short answer' and not result[-1]['raw_text_truncated']
    assert all(item['raw_text_truncated'] and item['recall_handle'] for item in result[:-1])


def test_metadata_only_sources_cannot_displace_substantive_read_history():
    result = {'kind': 'page_observation', 'source': 'https://example.test/result', 'raw_text': 'Actual read'}
    records = [result, *[{'kind': 'page_metadata', 'source': f'https://example.test/redirect/{i}'} for i in range(30)]]
    assert result in bounded_source_records(records, 20)


def test_legacy_text_without_original_length_does_not_claim_it_was_untruncated():
    state = Rail._build_phase_state('Continue from retained history')
    state['structured_evidence'] = [{'source': 'https://example.test', 'raw_text': 'Legacy text',
                                     'recall_handle': 'raw_old'}]
    payload = Rail._authoritative_terminal_payload(state)
    item = payload['observations'][0]
    assert item['raw_text'] == 'Legacy text' and item['recall_handle'] == 'raw_old'
    assert item['raw_text_length'] is None and item['raw_text_truncated'] is None
    assert payload['observation_excerpt']['text_completeness_unknown_count'] == 1


@pytest.mark.asyncio
async def test_missing_binding_reports_existing_probe_and_reobserved_control_can_be_bound():
    _, initial = cart_session()
    session, _ = persistent_session(initial)
    page = BrowserPageState()
    page.advance(url='https://example.test/detail')
    selected = register_control(page, '#chosen', 11)
    page.get_target(selected).decision_state.pop('node_guard')
    runtime = SimpleNamespace(ensure_page_state=lambda: page)
    tool = BrowserPhaseTool(runtime)
    args = {'op': 'set', 'objective': 'Use selected operation', 'target_ids': [selected]}
    result = await tool.invoke(args, session=session)
    assert not result.success and result.data['executed'] is False
    validation = result.data['validation']
    assert validation['reason'] == 'missing_identity'
    assert validation['recovery_probe']['tool'] == 'browser_probe_interactives'
    assert validation['recovery_probe']['arguments']['query'] == '#chosen'
    assert not task_state(session).get('active_phase_contract')
    # The caller chooses the fixed reader and explicitly uses its fresh target.
    selected = register_control(page, '#chosen', 11)
    args['target_ids'] = [selected]
    result = await tool.invoke(args, session=session)
    assert result.success


@pytest.mark.parametrize('line,expected', [
    ('element is not visible', 'target_not_visible'),
    ('element is not stable', 'target_unstable'),
    ('element is not enabled', 'target_disabled'),
    ('another node intercepts pointer events', 'pointer_intercepted'),
    ('waiting for element to be visible, enabled and stable', 'locator_or_actionability_wait'),
])
def test_unperformed_reason_survives_journal_projection_without_changing_dispatch(line, expected):
    error = 'TimeoutError: locator.click: Timeout 2500ms exceeded.\nCall log:\n- waiting for locator\n- ' + line
    initial = {'execution_journal': [{'call_id': 'failed-click', 'tool': 'browser_click',
                                    'execution_state': 'prepared', 'continue_on_error': False,
                                    'steps': [{'index': 0, 'op': 'click', 'impact': 'local_ui', 'optional': False}]}]}
    session, _ = persistent_session(initial)
    inputs = SimpleNamespace(tool_name='browser_click', tool_args={}, tool_call=SimpleNamespace(id='failed-click'))
    result = execution_journal.record_result(session, inputs, {'success': False}, {'error': error})
    assert result['execution_state'] == 'rejected_before_dispatch' and result['executed'] is False
    assert result['execution']['steps'][0]['failure_reason'] == expected
    assert execution_journal._unperformed_diagnostic(error + '\n- performing click action') == ''
    assert execution_journal._unperformed_diagnostic('page says element is not visible') == ''


@pytest.mark.asyncio
@pytest.mark.parametrize('operation,change,link,expected', [
    ('navigate', 'height', None, None), ('navigate', 'scroll', None, None),
    ('navigate', 'document', None, 'document'), ('navigate', 'url', None, 'url'),
    ('scroll', 'height', None, 'page_geometry'),
    ('navigate', 'height', 'first_result', None), ('navigate', 'link', 'first_result', 'first_result'),
    ('navigate', 'height', 'observed_link', None), ('navigate', 'link', 'observed_link', 'observed_link'),
])
async def test_generated_page_guard_checks_only_operation_dependencies(monkeypatch, operation, change, link, expected):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js required for the generated executor script')
    state = {'query_id': 'q', 'task_id': 't', 'deadline_started_at': 1}
    session, _ = persistent_session(state)
    session.get_session_id = lambda: 's'
    page = SimpleNamespace(page_id='p', generation_id='g1', url='https://example.test/source')
    args = {'generation_id': 'g1', 'op': operation, 'url': 'https://example.test/destination'}
    geometry = {'document': 'doc', 'history_length': 1, 'can_go_back': None,
                'scroll_x': 0, 'scroll_y': 0, 'width': 1000, 'height': 800, 'page_height': 2000}
    binding = {'title': 'Chosen result', 'href': args['url']} if link else None
    guard = DecisionGuard('s', 'p', 'g1', page.url, '', canonical_arguments(args), geometry, 'q:t:1', time.time() + 120,
                          tool_name='browser_page_action', first_result=binding if link == 'first_result' else None,
                          observed_link=binding if link == 'observed_link' else None)
    captured = []

    async def execute(script):
        captured.append(script)
        return {'ok': True}

    runtime = SimpleNamespace(ensure_page_state=lambda: page, call_playwright_run_code_unsafe=execute,
                              unwrap_mcp_text_result=lambda result: result)
    live_card = {'title': 'Chosen result', 'primary_link': args['url'], 'result_index': 1,
                 'order_known': True, 'is_ad': False, 'region': 'main_result'}
    if change == 'link':
        live_card['primary_link'] = 'https://example.test/different'
    monkeypatch.setattr(probes, 'build_card_probe_js', lambda **kwargs: 'async () => (' + json.dumps(
        {'ok': True, 'cards': [live_card]}) + ')')
    await validate_guard(runtime, guard, SimpleNamespace(tool_name='browser_page_action', tool_args=args), session)
    runner = r'''
const data=JSON.parse(require('fs').readFileSync(0,'utf8'));
global.window={__openjiuwenDecisionNodes:{document:data.change==='document'?'new':'doc'},
  scrollX:0,scrollY:data.change==='scroll'?10:0,innerWidth:1000,innerHeight:800};
global.history={length:1};
global.document={documentElement:{scrollHeight:data.change==='height'?2001:2000}};
const page={url:()=>data.change==='url'?'https://example.test/other':'https://example.test/source',
  evaluate:async(fn)=>fn()};
(async()=>console.log(JSON.stringify(await eval('('+data.script+')')(page))))();
'''
    result = subprocess.run([node, '-e', runner], input=json.dumps({'change': change, 'script': captured[0]}),
                            encoding='utf-8', capture_output=True, check=True, timeout=10)
    assert json.loads(result.stdout) == ({'ok': True} if expected is None else {'ok': False, 'mismatch': expected})


@pytest.mark.parametrize('configuration,expected', [
    ({}, 8192), ({'max_tokens': 2048}, 2048), ({'max_tokens': 32768}, 32768),
    ({'max_completion_tokens': 4096}, None), ({'max_output_tokens': 4096}, None),
    ({'reasoning': ReasoningConfig(mode='enabled', budget_tokens=12000)}, 13024),
])
def test_browser_uses_existing_output_budget_without_mutating_parent_or_explicit_limits(configuration, expected):
    parent = SimpleNamespace(model_config=ModelRequestConfig(model='test', **configuration), model_client_config=None)
    before = parent.model_config.model_dump()
    browser = _browser_model_with_temperature(parent, 0.4)
    assert browser.model_config.max_tokens == expected
    assert parent.model_config.model_dump() == before
    assert browser.model_config.reasoning == parent.model_config.reasoning
    for key in ('max_completion_tokens', 'max_output_tokens'):
        assert getattr(browser.model_config, key, None) == getattr(parent.model_config, key, None)
