"""Reported eligibility uses the same defined target components as mixed loss."""

from types import SimpleNamespace

import pytest

from examples.adaptive_multi_agent_collab.schemas import ConversationTag, QueryExample, Scheme, Trajectory


@pytest.mark.parametrize("missing", [None, float("nan"), float("inf")])
@pytest.mark.parametrize("field", ["future_return", "conversation_credits", "routing_local_labels", "conversation_costs"])
def test_undefined_required_target_is_ineligible(missing, field):
    tag = ConversationTag(1, 0, 1, Scheme.DEBATE)
    decision = SimpleNamespace(forced_zero=False, scheme=Scheme.DEBATE, initiator_id=0, receiver_id=1)
    session = SimpleNamespace(session=1, routing=SimpleNamespace(decisions=(decision,)), future_return=0.,
        conversation_credits={tag: .1}, routing_local_labels={tag: 1.}, conversation_costs={tag: .2})
    trajectory = Trajectory(QueryExample("synthetic", "Choose", ("A", "B"), "A"), (), (),
                            sessions=[session], completed=True)
    assert trajectory.routing_eligible
    if field == "future_return":
        session.future_return = missing
    else:
        getattr(session, field)[tag] = missing
    assert not trajectory.routing_eligible
    if field in {"future_return", "conversation_credits"}:
        trajectory.routing_mixture = 0.
    else:
        trajectory.routing_mixture = 1.
    assert trajectory.routing_eligible


def test_free_zero_missing_label_and_forced_zero_exclusion():
    decision = SimpleNamespace(forced_zero=False, scheme=Scheme.ZERO, initiator_id=0, receiver_id=1)
    session = SimpleNamespace(session=1, routing=SimpleNamespace(decisions=(decision,)),
                              future_return=0., routing_zero_labels={0: None})
    trajectory = Trajectory(QueryExample("synthetic", "Choose", ("A", "B"), "A"), (), (),
                            sessions=[session], completed=True)
    assert not trajectory.routing_eligible
    assert trajectory.routing_eligible_for("mixed_q", p_mix=1.)
    decision.forced_zero = True
    session.future_return = None
    assert trajectory.routing_eligible
