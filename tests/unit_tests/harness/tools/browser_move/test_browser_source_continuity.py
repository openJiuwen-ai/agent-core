# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Post-develop source, completion and local-handoff regressions, without providers."""

import copy
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from openjiuwen.core.foundation.llm.schema.message import AssistantMessage, ToolCall
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.tools.browser_move.decision.action_space import build_menu
from openjiuwen.harness.tools.browser_move.decision.policy_model import (
    BrowserPolicyModel, _Observation, _TaskPolicy,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.evidence import bounded_source_records
from openjiuwen.harness.tools.browser_move.playwright_runtime.page_state import BrowserPageState
from openjiuwen.harness.tools.browser_move.playwright_runtime.phase_contract import (
    BrowserPhaseTool, PHASE_KEY, constrain_actions, observe_conditions, project_phase, set_phase,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.policy_page_action import BrowserPageActionTool
from openjiuwen.harness.tools.browser_move.playwright_runtime.runtime import BrowserRuntimeRail as Rail
from tests.unit_tests.harness.tools.browser_move.test_browser_fact_handoff import cart_session
from tests.unit_tests.harness.tools.browser_move.test_browser_jev_phase_contract import control
from tests.unit_tests.harness.tools.browser_move.test_browser_observation_results import record


def test_latest_broad_source_survives_old_sources_and_later_scoped_finds():
    state = Rail._build_phase_state('Inspect candidates and add one selected item')
    for i in range(12):
        record(state, f'Candidate source {i}', f'https://example.test/item/{i}')
    record(state, 'Cart before: two items', 'https://example.test/cart')
    record(state, 'Cart after: three items including the selected object', 'https://example.test/cart')
    for i in range(30):
        Rail._record_structured_evidence(
            state, {'result': {'text': f'Local excerpt {i}'}, 'page_state': state['last_page'],
                    'recall_handle': f'find_{i}'},
            tool_name='browser_page_action', tool_args={'op': 'find', 'query': f'word {i}'},
        )
    retained = state['structured_evidence']
    excerpt = Rail._authoritative_terminal_payload(state)['observations']
    for records in [retained, excerpt]:
        assert 'Cart after: three items' in json.dumps(records)
        assert any(r.get('read_scope', {}).get('operation') == 'read_text' for r in records)
    assert len(retained) <= 20 and len(excerpt) <= 20
    assert len({r['source'] for r in excerpt}) > 1
    assert not state['evidence_slots']  # Source retention adds no business output contract.


def test_duplicate_read_updates_reference_and_narrow_scope_never_impersonates_broad_scope():
    state = Rail._build_phase_state('Read current source')
    page = {'url': 'https://example.test/cart', 'generation_id': 'g2', 'page_id': 'p', 'interaction_revision': 3}
    for op, handle in [('read_text', 'raw_old'), ('find', 'local'), ('read_text', 'raw_latest')]:
        Rail._record_structured_evidence(
            state, {'result': {'text': 'Same visible fact'}, 'page_state': page, 'recall_handle': handle},
            tool_name='browser_page_action', tool_args={'op': op, **({'query': 'fact'} if op == 'find' else {})},
        )
    sources = Rail._task_observations(state)
    same = [r for r in sources if r.get('kind') == 'page_observation']
    assert len(same) == 2
    broad = next(r for r in same if r['read_scope']['operation'] == 'read_text')
    assert broad['recall_handle'] == 'raw_latest'
    assert broad['observation_scope']['interaction_revision'] == 3
    assert not any(r.get('recall_handle') == 'raw_old' for r in sources)


@pytest.mark.parametrize('limit', [0, 1, 6, 20])
def test_bounded_selection_keeps_most_recent_source_when_sources_exceed_capacity(limit):
    records = [{'source': f'https://example.test/{i}', 'kind': 'page_observation', 'raw_text': str(i)}
               for i in range(25)]
    selected = bounded_source_records(records, limit)
    assert len(selected) == limit
    if limit:
        assert records[-1] in selected


@pytest.mark.asyncio
@pytest.mark.parametrize('finish_reason,expected', [
    ('length', 'model_output_incomplete'), ('stop', 'model_output_empty'),
])
async def test_reasoning_only_model_response_preserves_ack_and_sources_without_completion(finish_reason, expected):
    session, state = cart_session()
    journal = copy.deepcopy(state['execution_journal'])
    response = AssistantMessage(content='', reasoning_content='reasoning only', finish_reason=finish_reason)
    ctx = AgentCallbackContext(agent=MagicMock(), session=session,
                               inputs=SimpleNamespace(response=response), extra={})
    await Rail(MagicMock()).after_model_call(ctx)
    result = ctx.consume_force_finish().result['authoritative_browser_result']
    assert result['status'] == 'partial' and result['terminal_reason'] == expected
    assert result['model_failure']['source'] == 'model'
    assert result['retryable']  # Only explicit, existing bounded resume; no immediate tool replay.
    assert '3 items' in str(result['observations'])
    assert state['execution_journal'] == journal
    assert ctx.consume_retry_request() is None


@pytest.mark.asyncio
@pytest.mark.parametrize('response', [
    AssistantMessage(content='The current cart contains three items.', finish_reason='stop'),
    AssistantMessage(content='', finish_reason='tool_calls', tool_calls=[
        ToolCall(id='read', type='function', name='browser_page_action', arguments='{}'),
    ]),
])
async def test_real_answer_and_real_tool_output_keep_existing_lifecycle(response):
    session, _ = cart_session()
    ctx = AgentCallbackContext(agent=MagicMock(), session=session,
                               inputs=SimpleNamespace(response=response), extra={})
    await Rail(MagicMock()).after_model_call(ctx)
    assert ctx.consume_force_finish() is None


def test_page_metadata_cannot_supply_an_empty_worker_answer():
    session, state = cart_session()
    state['structured_evidence'] = [{'kind': 'page_metadata', 'source': 'https://example.test',
                                     'values': {'url': 'https://example.test', 'title': 'Observed'}}]
    Rail._apply_worker_progress_to_task_state(session, {'status': 'completed'}, '')
    assert state['status'] == 'partial' and state['terminal_reason'] == 'model_output_empty'


def observed(document='doc', url='https://example.test/list', controls=None):
    return {'capture_id': document + url, 'url': url, 'page_guard': {'document': document},
            'controls': controls or []}


def test_unbound_objective_expires_on_detail_page_and_read_path_stays_available():
    state = {'goal': 'Select a suitable result then read details'}
    set_phase(state, {'objective': 'Filter the list', 'allowed_operations': ['click', 'read_text', 'scroll']},
              [], observation=observed())
    crumb = control('crumb', 'Back to results', 2)
    crumb['decision_state']['node_guard']['document'] = 'detail'
    snapshot = observed('detail', 'https://example.test/detail', [crumb])
    observe_conditions(state, snapshot)
    assert project_phase(state)['handoff']['reason'] == 'bound_document_changed'
    menu = build_menu([crumb], state['goal'], limit=30, page={**snapshot, 'page_position': {'pixels_below': 800}},
                      page_operations={'read_text', 'scroll'})
    constrain_actions(menu, state, [crumb])
    assert any(s['op'] == 'read_text' for s in menu.steps.values())
    assert not any(s['op'] == 'click' for s in menu.steps.values())
    set_phase(state, {'objective': 'Read selected detail', 'allowed_operations': ['read_text']},
              [crumb], observation=snapshot)
    assert not project_phase(state)['handoff']


@pytest.mark.parametrize('new_url,expired', [
    ('https://example.test/list?sort=price&breakfast=true', False),
    ('https://example.test/detail/123', True),
])
def test_same_document_filter_changes_do_not_require_phase_per_action(new_url, expired):
    state = {}
    set_phase(state, {'objective': 'Apply selected filters'}, [], observation=observed())
    observe_conditions(state, observed(url=new_url))
    assert bool(project_phase(state)['handoff']) is expired


def register_control(page, selector, node, document='doc'):
    return page.register_interactives({
        'url': page.url, 'decision_snapshot': observed(document, page.url),
        'elements': [{'selector_hint': selector, 'selector_hint_validated': True, 'match_count': 1,
                      'role': 'button', 'accessible_name': selector, 'visible': True, 'enabled': True,
                      'actionable': True, 'clickable': True,
                      'decision_state': {'tag': 'button', 'node_guard': {'document': document, 'node': node}}}],
    })[0]['target_id']


def test_current_target_from_narrow_probe_is_shared_by_phase_menu_and_executor():
    page = BrowserPageState()
    page.advance(url='https://example.test/detail')
    selected = register_control(page, '#selected', 11)
    register_control(page, '#generic', 12)
    assert selected not in {c['target_id'] for c in page.export_decision_targets()}
    current = page.export_decision_observation(include_current_registry=True)['controls']
    assert selected in {c['target_id'] for c in current}
    state = {}
    set_phase(state, {'objective': 'Use selected operation', 'target_ids': [selected]}, current,
              observation=page.export_decision_observation())
    menu = build_menu(current, 'Use selected operation', limit=20)
    constrain_actions(menu, state, current)
    assert {s['target_id'] for s in menu.steps.values() if s['op'] == 'click'} == {selected}
    assert page.resolve_target(generation_id=page.generation_id, target_id=selected).selector == '#selected'


@pytest.mark.parametrize('change', ['revision', 'document', 'generation', 'invalidated', 'uncaptured'])
def test_registry_history_never_becomes_a_fresh_phase_target(change):
    page = BrowserPageState()
    page.advance(url='https://example.test/detail')
    old = register_control(page, '#selected', 11)
    if change == 'revision':
        page.interaction_revision += 1
    if change == 'generation':
        page.advance(url='https://example.test/next')
    register_control(page, '#generic', 12, 'other' if change == 'document' else 'doc')
    if change == 'invalidated':
        page.decision_snapshot['invalidated'] = 'browser_policy_target_changed'
    if change == 'uncaptured':
        page.decision_snapshot.pop('capture_id')
    assert old not in {c['target_id'] for c in
                       page.export_decision_observation(include_current_registry=True)['controls']}


@pytest.mark.asyncio
@pytest.mark.parametrize('change,expected', [('revision', 'stale_observation'), ('generation', 'stale_generation')])
async def test_phase_error_explains_registered_but_stale_target(change, expected):
    session, _ = cart_session()
    page = BrowserPageState()
    page.advance(url='https://example.test/detail')
    old = register_control(page, '#selected', 11)
    if change == 'revision':
        page.interaction_revision += 1
    else:
        page.advance(url='https://example.test/next')
    register_control(page, '#generic', 12)
    result = await BrowserPhaseTool(SimpleNamespace(ensure_page_state=lambda: page)).invoke(
        {'op': 'set', 'objective': 'Use selected operation', 'target_ids': [old]}, session=session,
    )
    assert not result.success and result.data['executed'] is False
    assert result.data['validation']['reason'] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize('args,missing,unexpected', [
    ({'op': 'select_tab', 'url': 'https://example.test', 'generation_id': 'g1'}, ['index'], []),
    ({'op': 'read_text', 'generation_id': 'g1', 'selector': '#x'}, [], ['selector']),
    ({'op': 'find', 'generation_id': 'g1'}, ['query'], []),
])
async def test_bad_page_arguments_report_correctable_fields_before_dispatch(args, missing, unexpected):
    runtime = SimpleNamespace(service=SimpleNamespace(allowed_tool_names=None))
    result = await BrowserPageActionTool(runtime).invoke(args)
    assert result.data['execution_state'] == 'rejected_before_dispatch'
    assert result.data['validation']['missing'] == missing
    assert result.data['validation']['unexpected'] == unexpected
    assert 'Nothing was dispatched' in result.data['recovery_hint']
    assert 'exact URL from tabs' in result.data['recovery_hint']


def observation_for(page, version=1):
    return _Observation('task', 'session', 100, {'phase': {'version': version}}, [], page)


def test_llm_return_suppresses_only_the_reversed_detour_until_new_intent():
    step = {'op': 'navigate', 'url': 'https://example.test/list'}
    key = BrowserPolicyModel._target_key(step, {})
    receipt = {'success': True, 'operation': 'navigate', 'target_key': key,
               'source_url': 'https://example.test/detail',
               'observed_url': step['url'], 'phase_version': 1, 'llm_navigation_call_id': 'initial'}
    task = _TaskPolicy(receipts=[receipt], llm_actions=[{'op': 'navigate', 'outcome': 'ok', 'call_id': 'restore'}])
    page = {'url': receipt['source_url']}
    BrowserPolicyModel._mark_reversed_navigation(task, page)
    assert receipt['reversed_by_llm']
    assert BrowserPolicyModel._local_continuity_reason(task, step, {}, observation_for(page))
    assert not BrowserPolicyModel._local_continuity_reason(task, {'op': 'read_text'}, {}, observation_for(page))
    assert not BrowserPolicyModel._local_continuity_reason(task, step, {}, observation_for(page, 2))
    other = {'op': 'navigate', 'url': 'https://example.test/other'}
    assert not BrowserPolicyModel._local_continuity_reason(task, other, {}, observation_for(page))


def test_an_older_llm_navigation_does_not_claim_it_reversed_a_later_jev_action():
    receipt = {'success': True, 'operation': 'navigate', 'target_key': 'key',
               'source_url': 'a', 'observed_url': 'b',
               'llm_navigation_call_id': 'earlier'}
    task = _TaskPolicy(receipts=[receipt], llm_actions=[{'op': 'navigate', 'outcome': 'ok', 'call_id': 'earlier'}])
    BrowserPolicyModel._mark_reversed_navigation(task, {'url': 'a'})
    assert not receipt.get('reversed_by_llm')


def test_scroll_round_trip_stops_repeated_direction_but_allows_reads_and_fresh_scope():
    base = {'success': True, 'operation': 'scroll', 'source_document': 'doc', 'phase_version': 1,
            'llm_call_id': None, 'scroll_layout': {'page_height': 5000, 'viewport_height': 900,
                                                  'viewport_width': 1200}}
    task = _TaskPolicy(receipts=[
        {**base, 'scroll_direction': 'down', 'scroll_from': 2201, 'scroll_to': 2947},
        {**base, 'scroll_direction': 'up', 'scroll_from': 2947, 'scroll_to': 2201},
    ])
    page = {'page_guard': {'document': 'doc'},
            'page_position': {'scroll_y': 2201, **base['scroll_layout']}}
    step = {'op': 'scroll', 'direction': 'down'}
    assert BrowserPolicyModel._local_continuity_reason(task, step, {}, observation_for(page)) == 'scroll_round_trip'
    assert not BrowserPolicyModel._local_continuity_reason(task, {'op': 'read_text'}, {}, observation_for(page))
    assert not BrowserPolicyModel._local_continuity_reason(task, step, {}, observation_for(page, 2))
    page['page_position']['page_height'] = 6000
    assert not BrowserPolicyModel._local_continuity_reason(task, step, {}, observation_for(page))
    page['page_position']['scroll_y'] = 4000
    assert not BrowserPolicyModel._local_continuity_reason(task, step, {}, observation_for(page))


def test_local_script_excerpt_cannot_replace_latest_known_page_read():
    records = [{'kind': 'page_observation', 'source': 'https://example.test/cart', 'raw_text': 'Complete page read',
                'read_scope': {'operation': 'read_text'}}]
    records.extend({'kind': 'page_observation', 'source': 'https://example.test/cart', 'raw_text': f'Diagnostic {i}',
                    'read_scope': {'operation': 'evaluate'}} for i in range(10))
    assert bounded_source_records(records, 1) == records[:1]


@pytest.mark.asyncio
async def test_explicit_current_registry_binding_reaches_jev_without_expanding_default_menu():
    from tests.unit_tests.harness.tools.browser_move.test_browser_jev_policy import (
        grouped_answer, messages_for, setup_policy,
    )
    from tests.unit_tests.harness.tools.browser_move.test_browser_local_decision_loop import LOCAL_TOOLS

    policy, llm, client, runtime, context, captured = setup_policy()
    page = runtime.ensure_page_state()
    selected = register_control(page, '#selected', 42, 'doc-a')
    page.register_interactives({
        'url': page.url, 'decision_snapshot': observed('doc-a', page.url),
        'elements': [{'selector_hint': f'#generic-{i}', 'selector_hint_validated': True, 'match_count': 1,
                      'role': 'button', 'accessible_name': f'Unrelated {i}', 'visible': True, 'enabled': True,
                      'actionable': True, 'clickable': True,
                      'decision_state': {'tag': 'button', 'node_guard': {'document': 'doc-a', 'node': 100 + i}}}
                     for i in range(40)],
    })
    assert len(page.export_decision_targets()) > policy.decision_config.candidate_limit
    assert selected not in {c['target_id'] for c in page.export_decision_targets()}
    state = context.get_session_ref().get_state(PHASE_KEY)
    result = await BrowserPhaseTool(runtime).invoke(
        {'op': 'set', 'objective': 'Click the selected operation', 'target_ids': [selected],
         'allowed_operations': ['click']}, session=context.get_session_ref(),
    )
    assert result.success, result.data
    client.evaluate.side_effect = lambda payload, **_: grouped_answer(payload, 'CLICK')
    response = await policy.invoke(await messages_for(policy, context, captured), tools=LOCAL_TOOLS)
    assert response.tool_calls[0].id.startswith('jev_')
    assert json.loads(response.tool_calls[0].arguments)['steps'][0]['target_id'] == selected
    assert not state['active_phase_contract'].get('handoff')
    llm.invoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_empty_model_output_cannot_complete_even_with_real_extracted_values():
    session, state = cart_session()
    state['structured_evidence'].append({'kind': 'extraction', 'values': {'title': 'Selected object'}})
    ctx = AgentCallbackContext(agent=MagicMock(), session=session, extra={},
                               inputs=SimpleNamespace(response=AssistantMessage(content='', finish_reason='length')))
    await Rail(MagicMock()).after_model_call(ctx)
    assert ctx.consume_force_finish().result['authoritative_browser_result']['status'] == 'partial'
    Rail._apply_worker_progress_to_task_state(session, {'status': 'completed'}, '')
    assert state['terminal_reason'] == 'model_output_incomplete'


@pytest.mark.asyncio
async def test_empty_terminal_synthesis_keeps_existing_deadline_reason():
    session, state = cart_session()
    state.update(status='partial', terminal_reason='task_deadline_exhausted',
                 blockers=['task_deadline_exhausted'])
    ctx = AgentCallbackContext(agent=MagicMock(), session=session, extra={},
                               inputs=SimpleNamespace(response=AssistantMessage(content='', finish_reason='length')))
    await Rail(MagicMock()).after_model_call(ctx)
    result = ctx.consume_force_finish().result['authoritative_browser_result']
    assert result['terminal_reason'] == 'task_deadline_exhausted'
    assert not result['retryable'] and not result['model_failure']


@pytest.mark.asyncio
async def test_explicit_llm_navigation_releases_only_its_existing_local_detour_record():
    from tests.unit_tests.harness.tools.browser_move.test_browser_jev_policy import setup_policy

    policy, _, _, runtime, context, _ = setup_policy()
    session = context.get_session_ref()
    phase = session.get_state(PHASE_KEY)
    task = policy._bind_task(session, phase)
    step = {'op': 'navigate', 'url': 'https://example.test/selected'}
    key = policy._target_key(step, {})
    task.receipts = [{'target_key': key, 'reversed_by_llm': True},
                     {'target_key': 'unrelated', 'reversed_by_llm': True}]
    inputs = SimpleNamespace(tool_call=SimpleNamespace(id='explicit-llm'), tool_name='browser_page_action',
                             tool_args=step)
    policy.record_execution(inputs, session, {'success': True})
    assert not task.receipts[0].get('reversed_by_llm')
    assert task.receipts[1]['reversed_by_llm']
