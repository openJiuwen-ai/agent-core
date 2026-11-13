"""Accepted alternating revisions, not feedback, control Step33's guard."""
import asyncio
import json
from pathlib import Path

import pytest

from examples.adaptive_multi_agent_collab.config import Config, SafetyConfig, TerminationConfig
from examples.adaptive_multi_agent_collab.schemas import (
    ConversationStatus, MessageFormat, QueryExample, SafeguardTrigger, Scheme,
)
from examples.adaptive_multi_agent_collab.tests.test_runtime import _runtime, _matrix, _example
from examples.adaptive_multi_agent_collab.workflows.tie_view import tie_view


ALTERNATING_SCHEMES = (Scheme.SELF, Scheme.DEBATE, Scheme.TEACHER, Scheme.REVIEWER)


def _edge(scheme, initiator=0):
    return initiator, initiator if scheme is Scheme.SELF else (initiator + 1) % 3


@pytest.mark.parametrize('agent', [0, 1, 2])
@pytest.mark.parametrize('scheme', ALTERNATING_SCHEMES)
def test_three_unchanged_revisions_stop_before_next_role_for_every_backend(agent, scheme):
    answer = chr(65 + agent)
    initiator, receiver = _edge(scheme, agent)
    scripts = {initiator: {
        'revision': [{'answer': answer, 'explanation': f'new explanation {i}'} for i in range(5)],
    }}
    scripts.setdefault(receiver, {})['role'] = [
        {'status': 'continue', 'feedback': f'different correction {i}'} for i in range(5)
    ]
    runtime, agents = _runtime(3, scripts, {1: _matrix(3, {(initiator, receiver): scheme})})
    trajectory = asyncio.run(runtime.run(_example()))
    trace = trajectory.sessions[0].traces[0]
    assert trace.unchanged_revisions == 3
    assert trace.safeguard_trigger is SafeguardTrigger.ANSWER_STABILITY
    assert [c.kind for c in agents[receiver].calls].count('role') == 3
    assert [c.kind for c in agents[initiator].calls].count('revision') == 3
    assert trace.messages[-1].format is MessageFormat.REVISION_RESPONSE
    assert trace.messages[-1].explanation == 'new explanation 2'
    assert len(trajectory.sessions[0].raw_terminal_answers[agent]) == 1
    assert trajectory.execution_counters['safeguards']['answer-stability'] == 1
    view = tie_view((answer,), (trace.terminal,), {trace.tag: trace})
    contribution = view['answers'][0]['contributions'][0]
    assert view['pairs'][contribution['terminal_pair_ref']]['explanation'] == 'new explanation 2'


@pytest.mark.parametrize('scheme', ALTERNATING_SCHEMES)
def test_answer_change_resets_counter_and_repair_counts_once(scheme):
    initiator, receiver = _edge(scheme)
    scripts = {initiator: {
        'revision': [
            {'answer': 'A', 'explanation': 'one'},
            {'answer': 'A', 'explanation': 'two'},
            {'answer': 'B', 'explanation': 'change'},
            {'answer': 'B', 'explanation': 'one'},
            {'answer': 'invalid', 'explanation': 'rejected'},
            {'answer': 'B', 'explanation': 'repair two'},
            {'answer': 'B', 'explanation': ''},
        ],
    }}
    scripts.setdefault(receiver, {})['role'] = [
        {'status': 'continue', 'feedback': f'issue {i}'} for i in range(7)
    ]
    runtime, agents = _runtime(3, scripts, {1: _matrix(3, {(initiator, receiver): scheme})})
    trace = asyncio.run(runtime.run(_example())).sessions[0].traces[0]
    assert trace.unchanged_revisions == 3
    assert trace.terminal.answer == 'B' and trace.messages[-1].explanation == ''
    assert len([m for m in trace.messages if m.format is MessageFormat.REVISION_RESPONSE]) == 6
    assert len([c for c in agents[0].calls if c.kind == 'revision']) == 7
    assert trace.safeguard_trigger is SafeguardTrigger.ANSWER_STABILITY


def test_judge_is_excluded_and_passive_carries_are_not_revisions():
    runtime, agents = _runtime(3, {1: {'judge': {'answer': 'A', 'explanation': 'selection'}}},
                               {1: _matrix(3, {(0, 1): Scheme.JUDGE})})
    trace = asyncio.run(runtime.run(_example())).sessions[0].traces[0]
    assert trace.unchanged_revisions == 0
    assert trace.safeguard_trigger is None
    assert trace.completion_status is ConversationStatus.COMPLETE
    assert all(c.kind not in {'role', 'revision'} for agent in agents.values() for c in agent.calls)


@pytest.mark.parametrize('scheme', ALTERNATING_SCHEMES)
def test_complete_role_before_third_revision_is_ordinary_completion(scheme):
    initiator, receiver = _edge(scheme)
    runtime, _ = _runtime(3, {receiver: {
        'role': [{'status': 'continue', 'feedback': 'issue 1'},
                 {'status': 'continue', 'feedback': 'issue 2'},
                 {'status': 'complete', 'feedback': 'addressed'}],
    }}, {1: _matrix(3, {(initiator, receiver): scheme})})
    trace = asyncio.run(runtime.run(_example())).sessions[0].traces[0]
    assert trace.unchanged_revisions == 2 and trace.safeguard_trigger is None


@pytest.mark.parametrize('scheme', ALTERNATING_SCHEMES)
def test_canonical_answer_comparison_preserves_latest_pair(scheme):
    initiator, receiver = _edge(scheme)
    scripts = {i: {'initial': {'answer': 'yes', 'explanation': ''}} for i in range(3)}
    scripts[receiver]['role'] = [
        {'status': 'continue', 'feedback': f'novel correction {i}'} for i in range(3)
    ]
    scripts[initiator]['revision'] = [
        {'answer': value, 'explanation': f'changed {i}'}
        for i, value in enumerate(('YES', ' yes ', 'Yes'))
    ]
    runtime, _ = _runtime(3, scripts, {1: _matrix(3, {(initiator, receiver): scheme})})
    trajectory = asyncio.run(runtime.run(QueryExample('canonical', 'Synthetic question?', ('yes', 'no'))))
    trace = trajectory.sessions[0].traces[0]
    assert trace.safeguard_trigger is SafeguardTrigger.ANSWER_STABILITY
    assert trace.terminal.answer == 'yes' and trace.messages[-1].explanation == 'changed 2'
    assert trace.unchanged_revisions == 3


def test_counter_resets_between_sessions_and_other_tags_are_isolated():
    scripts = {
        1: {'role': [{'status': 'continue', 'feedback': f'novel {i}'} for i in range(6)]},
        2: {'role': {'status': 'complete', 'feedback': 'addressed'}},
    }
    matrix = _matrix(3, {(0, 1): Scheme.TEACHER, (0, 2): Scheme.REVIEWER})
    runtime, agents = _runtime(3, scripts, {1: matrix, 2: matrix},
                               termination=TerminationConfig(min_sessions=2, max_sessions=2))
    trajectory = asyncio.run(runtime.run(_example()))
    assert len(trajectory.sessions) == 2
    for session in trajectory.sessions:
        teaching = next(t for t in session.traces if t.tag.scheme is Scheme.TEACHER)
        review = next(t for t in session.traces if t.tag.scheme is Scheme.REVIEWER)
        assert teaching.unchanged_revisions == 3 and review.unchanged_revisions == 0
        assert len(session.raw_terminal_answers[0]) == 2
    assert sum(c.kind == 'revision' for c in agents[0].calls) == 6


def test_recorded_pilot4_teaching_prefix_stops_at_third_accepted_revision():
    fixture = json.loads((Path(__file__).parent / 'fixtures' / 'pilot4_teaching_stability.json').read_text())
    assert fixture['provenance']['original_safeguard'] == 'round-limit'
    assert fixture['provenance']['recorded_original_terminal_round'] == 30
    runtime, agents = _runtime(3, {
        0: {'initial': fixture['initial'], 'revision': fixture['revision']},
        1: {'initial': fixture['initial'], 'role': fixture['role']},
        2: {'initial': fixture['initial']},
    }, {1: _matrix(3, {(0, 1): Scheme.TEACHER})})
    example = QueryExample(fixture['provenance']['example_id'], fixture['query'], ('yes', 'no'))
    trajectory = asyncio.run(runtime.run(example))
    trace = trajectory.sessions[0].traces[0]
    assert trace.safeguard_trigger is SafeguardTrigger.ANSWER_STABILITY
    assert trace.messages[-1].round_index == 6
    assert trace.messages[-1].explanation == fixture['revision'][-1]['explanation']
    assert len(trace.messages) == 7 and len(trajectory.sessions[0].raw_terminal_answers[0]) == 1
    assert sum(c.kind == 'role' for c in agents[1].calls) == 3
    assert sum(c.kind == 'revision' for c in agents[0].calls) == 3


def test_rejected_revision_and_its_failed_repair_do_not_advance_counter():
    runtime, agents = _runtime(3, {
        0: {'revision': [
            {'answer': 'A', 'explanation': 'one'},
            {'answer': 'A', 'explanation': 'two'},
            {'answer': 'invalid', 'explanation': 'rejected'},
            {'answer': 'invalid', 'explanation': 'rejected repair'},
        ]},
        1: {'role': [{'status': 'continue', 'feedback': f'issue {i}'} for i in range(3)]},
    }, {1: _matrix(3, {(0, 1): Scheme.TEACHER})})
    trajectory = asyncio.run(runtime.run(_example()))
    trace = trajectory.sessions[0].traces[0]
    assert trace.unchanged_revisions == 2
    assert trace.safeguard_trigger is SafeguardTrigger.VALIDATION_EXHAUSTED
    assert len([m for m in trace.messages if m.format is MessageFormat.REVISION_RESPONSE]) == 2
    assert sum(c.kind == 'revision' for c in agents[0].calls) == 4
    assert len(trajectory.sessions[0].raw_terminal_answers[0]) == 1
    view = tie_view(('A',), (trace.terminal,), {trace.tag: trace})
    contribution = view['answers'][0]['contributions'][0]
    assert view['pairs'][contribution['terminal_pair_ref']]['explanation'] == 'two'


def test_registered_alternating_extension_uses_the_same_guard():
    from examples.adaptive_multi_agent_collab.providers.adapter import DeterministicFakeAgent
    from examples.adaptive_multi_agent_collab.config import SchemeRegistration
    from examples.adaptive_multi_agent_collab.models.encoders import FixedBaseEncoders
    from examples.adaptive_multi_agent_collab.models.policies import WeightingModel
    from examples.adaptive_multi_agent_collab.workflows.prompts import seed_prompt_collection
    from examples.adaptive_multi_agent_collab.workflows.runtime import Runtime, ScriptedRoutingPolicy
    from examples.adaptive_multi_agent_collab.tests.test_runtime import _offline_config

    contract = SchemeRegistration('S_check', ((0, 1),), 'Review this pair.',
                                  'Find supported corrections.', 'Return the best answer.',
                                  'Audit a supported material issue.')
    config = _offline_config(n_agents=2, scheme_registry=(contract,),
                             termination=TerminationConfig(min_sessions=1, max_sessions=1))
    encoders = FixedBaseEncoders(2, configuration=config.encoders, scheme_order=config.scheme_order)
    agents = {i: DeterministicFakeAgent(i, {
        'role': [{'status': 'continue', 'feedback': f'novel issue {j}'} for j in range(4)],
        'revision': {'answer': 'A', 'explanation': ''},
    }, default_answer='A') for i in range(2)}
    runtime = Runtime(
        config, agents, ScriptedRoutingPolicy(2, {1: _matrix(2, {(0, 1): Scheme('S_check')})}),
        WeightingModel(2, encoders.history_dimension, configuration=config.network),
        encoders=encoders, prompts=seed_prompt_collection(config.scheme_registry),
    )
    trace = asyncio.run(runtime.run(_example())).sessions[0].traces[0]
    assert trace.unchanged_revisions == 3
    assert trace.safeguard_trigger is SafeguardTrigger.ANSWER_STABILITY
    assert sum(c.kind == 'role' for c in agents[1].calls) == 3


def test_safety_serialization_and_guard_fingerprint_reject_old_scope():
    from examples.adaptive_multi_agent_collab.learning.evaluation import Experiment
    from examples.adaptive_multi_agent_collab.schemas import ANSWER_STABILITY_GUARD_ID

    config = Config()
    assert Config.from_dict(config.to_dict()).safety == SafetyConfig()
    assert config.to_dict()['safety']['answer_stability_patience'] == 3
    old = config.to_dict()
    old['safety']['self_reflection_patience'] = old['safety'].pop('answer_stability_patience')
    with pytest.raises(TypeError, match='self_reflection_patience'):
        Config.from_dict(old)
    assert ANSWER_STABILITY_GUARD_ID == 'canonical_alternating_revision_patience_v2'
    assert Experiment._workflow_protocol()['answer_stability'] == ANSWER_STABILITY_GUARD_ID
