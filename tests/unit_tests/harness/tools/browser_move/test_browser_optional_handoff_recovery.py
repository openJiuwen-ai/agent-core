# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Optional management failures must yield to real work without losing facts."""

import copy
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from jsonschema import Draft202012Validator

from openjiuwen.core.foundation.llm import AssistantMessage, ToolCall, ToolMessage, UserMessage
from openjiuwen.core.foundation.tool import ToolInfo
from openjiuwen.core.single_agent.agents.react_agent import ReActAgent, ReActAgentConfig
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ModelCallInputs, ToolCallInputs
from openjiuwen.harness.tools.browser_move.playwright_runtime.phase_contract import (
    BrowserPhaseTool, PHASE_KEY, validate_request,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.runtime import BrowserRuntimeRail as Rail
from tests.unit_tests.harness.tools.browser_move.test_browser_fact_handoff import cart_session
from tests.unit_tests.harness.tools.browser_move.test_browser_shared_state_delivery import persistent_session
from tests.unit_tests.harness.tools.browser_move.test_browser_jev_policy import setup_policy, messages_for


INVALID_REQUESTS = [
    {'op': 'set'},
    {'op': 'set', 'phase_version': 1, 'inspect_cart': False,
     'target_ids': ['t_g2_1'], 'bound_values': [{'target_id': 't_g2_1', 'value': 'Add to Cart'}]},
]


def round_messages(index, args=None, *, name='browser_phase', executed=False, error='invalid_phase_request'):
    call = ToolCall(id=f'call-{index}', type='function', name=name, arguments=json.dumps(args or {'op': 'set'}))
    payload = {'ok': not bool(error), 'executed': executed, 'state_changed': False, 'error': error}
    return [AssistantMessage(content='', tool_calls=[call]),
            ToolMessage(tool_call_id=call.id, content=json.dumps(payload))]


def recovery_context(messages):
    _, initial = cart_session()
    session, _ = persistent_session(initial)
    tools = [SimpleNamespace(name=name) for name in ('browser_phase', 'browser_batch_interact', 'browser_page_action')]
    ctx = AgentCallbackContext(agent=SimpleNamespace(system_prompt_builder=None), session=session,
                               inputs=SimpleNamespace(messages=list(messages), tools=tools), extra={})
    return ctx, initial


@pytest.mark.parametrize('args', INVALID_REQUESTS)
def test_common_schema_rejects_real_invalid_sets_but_keeps_a_small_handoff(args):
    tool = BrowserPhaseTool(MagicMock())
    validator = Draft202012Validator(tool.card.input_params)
    assert list(validator.iter_errors(args))
    validator.validate({'op': 'set', 'objective': 'Read the current results'})
    validator.validate({'op': 'verify'})
    validator.validate({'op': 'set', 'objective': 'Use the selected operation', 'target_ids': ['t_g2_1'],
                        'allowed_operations': ['click'], 'read_queries': ['Current selection']})
    assert not {'conditions', 'inspect_cart', 'phase_version'} & tool.card.input_params['properties'].keys()
    assert len(tool.card.description) < 1300


def test_legacy_explicit_verification_uses_strict_compatibility_schema():
    tool = BrowserPhaseTool(MagicMock())
    ctx = SimpleNamespace(agent=SimpleNamespace(ability_manager=SimpleNamespace(get=lambda _: tool)))
    Rail._validate_model_tool_args(ctx, 'browser_phase', {'op': 'verify', 'phase_version': 2, 'inspect_cart': True})
    with pytest.raises(ValueError):
        validate_request({'op': 'set', 'objective': 'Read', 'inspect_cart': True})


@pytest.mark.parametrize('args', INVALID_REQUESTS)
def test_repeated_failed_management_yields_to_ordinary_tools_without_touching_facts(args):
    messages = [UserMessage(content='Read current results')]
    for i in range(2):
        messages.extend(round_messages(i, args))
    ctx, initial = recovery_context(messages)
    rail = Rail(MagicMock())
    assert not rail._recover_failed_phase_calls(ctx, ctx.session, copy.deepcopy(initial))
    assert [tool.name for tool in ctx.inputs.tools] == ['browser_batch_interact', 'browser_page_action']
    assert 'ordinary browser tool' in ctx.inputs.messages[-1].content
    assert ctx.session.get_state(PHASE_KEY) == initial
    assert ctx.inputs.messages[:-1] == messages


def test_varied_invalid_arguments_cannot_evade_management_recovery():
    ctx, initial = recovery_context(round_messages(0, INVALID_REQUESTS[0]) + round_messages(1, INVALID_REQUESTS[1]))
    assert not Rail(MagicMock())._recover_failed_phase_calls(ctx, ctx.session, initial)
    assert all(tool.name != 'browser_phase' for tool in ctx.inputs.tools)


def test_actual_runtime_denial_messages_are_counted_without_requiring_a_new_error_shape():
    ctx, initial = recovery_context([])
    rail = Rail(MagicMock())
    for index, args in enumerate(INVALID_REQUESTS):
        assistant = round_messages(index, args)[0]
        inputs = ToolCallInputs(tool_name='browser_phase', tool_call=assistant.tool_calls[0], tool_args=args)
        tool_ctx = AgentCallbackContext(agent=ctx.agent, session=ctx.session, inputs=inputs)
        with pytest.raises(ValueError) as error:
            validate_request(args)
        rail._deny_tool_call(tool_ctx, error.value)
        ctx.inputs.messages.extend([assistant, inputs.tool_msg])
    assert rail._failed_phase_rounds(ctx) == 2
    assert not rail._recover_failed_phase_calls(ctx, ctx.session, initial)
    assert all(tool.name != 'browser_phase' for tool in ctx.inputs.tools)


@pytest.mark.parametrize('separator', [
    round_messages(2, name='browser_probe_interactives', executed=True, error=''),
    round_messages(2, args={'op': 'verify'}, executed=None, error='transport_timeout'),
    round_messages(2, executed=True, error=''),
    [UserMessage(content='Inspect a different field')],
])
def test_fresh_work_successful_handoff_unknown_read_or_new_input_ends_failure_streak(separator):
    messages = round_messages(0) + round_messages(1) + separator + round_messages(3)
    ctx, initial = recovery_context(messages)
    assert not Rail(MagicMock())._recover_failed_phase_calls(ctx, ctx.session, initial)
    assert any(tool.name == 'browser_phase' for tool in ctx.inputs.tools)
    assert ctx.inputs.messages == messages


def test_model_ignoring_ordinary_tool_recovery_uses_existing_bounded_protocol_terminal():
    messages = sum((round_messages(i) for i in range(4)), [])
    ctx, initial = recovery_context(messages)
    assert Rail(MagicMock())._recover_failed_phase_calls(ctx, ctx.session, initial)
    saved = ctx.session.get_state(PHASE_KEY)
    assert saved['terminal_reason'] == 'model_tool_protocol_error'
    assert saved['status'] == 'partial'
    assert saved['execution_journal'] == initial['execution_journal']
    assert saved['structured_evidence'] == initial['structured_evidence']
    assert saved['deadline_at'] == initial['deadline_at']


@pytest.mark.asyncio
async def test_runtime_before_model_filters_optional_management_after_real_failed_tool_rounds():
    ctx, initial = recovery_context(round_messages(0) + round_messages(1))
    runtime = MagicMock()
    runtime.llm_model = None
    rail = Rail(runtime)
    rail._sync_semantic_progress = MagicMock()
    await rail.before_model_call(ctx)
    assert all(tool.name != 'browser_phase' for tool in ctx.inputs.tools)
    assert ctx.session.get_state(PHASE_KEY)['execution_journal'] == initial['execution_journal']


@pytest.mark.asyncio
async def test_recovery_directive_survives_real_core_final_window_rebuild_and_then_retires():
    messages = round_messages(0) + round_messages(1)
    agent = ReActAgent(card=AgentCard(name='browser-recovery-window'))
    agent.configure(ReActAgentConfig(model_name='offline'))
    context = MagicMock()
    context.get_messages.side_effect = lambda: list(messages)

    async def window(**kwargs):
        return SimpleNamespace(get_messages=lambda: kwargs['system_messages'] + list(messages),
                               get_tools=lambda: kwargs['tools'])

    context.get_context_window = AsyncMock(side_effect=window)
    _, initial = cart_session()
    session, _ = persistent_session(initial)
    session.get_session_id = lambda: 'browser-recovery-window'
    tools = [ToolInfo(name=name, description=name, parameters={})
             for name in ('browser_phase', 'browser_page_action')]
    ctx = AgentCallbackContext(agent=agent, context=context, session=session,
                               inputs=ModelCallInputs(messages=messages, tools=tools))
    rail = Rail(MagicMock())
    rail._recover_failed_phase_calls(ctx, session, initial)
    await agent._build_model_context_window(ctx)
    assert 'ordinary browser tool' in ctx.inputs.messages[0].content
    assert [tool.name for tool in ctx.inputs.tools] == ['browser_page_action']
    messages.extend(round_messages(2, name='browser_probe_interactives', executed=True, error=''))
    ctx.inputs.tools = tools
    rail._recover_failed_phase_calls(ctx, session, initial)
    await agent._build_model_context_window(ctx)
    assert 'last consecutive browser_phase requests' not in ctx.inputs.messages[0].content
    assert any(tool.name == 'browser_phase' for tool in ctx.inputs.tools)


@pytest.mark.parametrize('live_risk', [False, True])
def test_approved_resume_retires_iteration_exit_only_and_can_complete(live_risk):
    _, initial = cart_session()
    initial.update(status='partial', terminal_reason='max_iterations_reached',
                   blockers=['max_iterations_reached'])
    if live_risk:
        initial['execution_journal'][0].update(execution_state='dispatched_unknown', executed=None)
        initial['execution_journal'][0]['steps'][0].update(execution_state='dispatched_unknown', executed=None)
    session, collection = persistent_session(initial)
    rail = Rail(MagicMock())
    resumed = rail._resume_task_state(session, session.get_state(PHASE_KEY), 'Use retained facts and finish')
    assert resumed['deadline_at'] == initial['deadline_at']
    assert resumed['resume_count'] == 1
    assert not resumed['blockers']
    assert collection.get_global('sibling') == {'keep': 'parent context'}
    Rail._apply_worker_progress_to_task_state(session, {'status': 'completed'}, 'Cart contains three items.')
    final = session.get_state(PHASE_KEY)
    assert final['status'] == ('partial' if live_risk else 'completed')
    assert final['execution_journal'] == initial['execution_journal']


def test_expired_deadline_cannot_be_retired_by_resume():
    _, initial = cart_session()
    initial.update(status='partial', terminal_reason='max_iterations_reached',
                   blockers=['max_iterations_reached'], deadline_at=time.time() - 1)
    session, _ = persistent_session(initial)
    Rail(MagicMock())._resume_task_state(session, session.get_state(PHASE_KEY), 'Continue')
    assert session.get_state(PHASE_KEY) == initial


def test_live_captcha_blocker_does_not_get_resume_authorization_or_get_cleared():
    _, initial = cart_session()
    initial.update(status='partial', terminal_reason='max_iterations_reached',
                   blockers=['max_iterations_reached', 'captcha_observed'])
    session, _ = persistent_session(initial)
    Rail(MagicMock())._resume_task_state(session, session.get_state(PHASE_KEY), 'Continue')
    assert session.get_state(PHASE_KEY) == initial


@pytest.mark.asyncio
async def test_unrelated_business_candidate_exclusion_does_not_promote_phase_over_reader_recovery(monkeypatch):
    from openjiuwen.harness.tools.browser_move.decision import policy_model as module

    constrain = module.constrain_actions

    def unrelated_exclusion(menu, state, controls):
        constrain(menu, state, controls)
        menu.excluded['local_target_binding_required'] = 8

    monkeypatch.setattr(module, 'constrain_actions', unrelated_exclusion)
    policy, _, client, _, context, captured = setup_policy()
    client.evaluate.side_effect = module.DecisionUnavailable('reader_already_observed')
    messages = await messages_for(policy, context, captured)
    _, diagnostic, _ = await policy._choose(messages, [{'name': 'browser_batch_interact'}, {'name': 'browser_phase'}])
    assert 'already supplied this range' in diagnostic['recovery']['next']
    assert 'bindings' not in diagnostic['recovery']
