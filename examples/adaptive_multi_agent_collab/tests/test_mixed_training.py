"""Pooled mixed-value targets and restricted fresh optimizer updates."""
from types import SimpleNamespace

import pytest
import torch

from examples.adaptive_multi_agent_collab.schemas import ConversationTag, Scheme
from examples.adaptive_multi_agent_collab.learning.training import MissingRoutingTarget, mixed_alpha_loss, update_routing


def fixture(p_mix=.75):
    q = torch.tensor([.2, -.1, .9], requires_grad=True)
    tag = ConversationTag(1, 0, 1, Scheme.DEBATE)
    decisions = tuple(SimpleNamespace(edge=(0, index % 2), scheme=Scheme.DEBATE if index == 0 else Scheme.ZERO,
                                     tag=tag if index == 0 else None, free=index != 2,
                                     q_value=q[index]) for index in range(3))
    values = dict(actions=[SimpleNamespace(decisions=decisions)], credits=[{tag: .3}], future_returns=[.4],
                  local_labels=[{tag: 1.}], zero_labels=[{0: 0.}], conversation_costs=[{tag: .2}],
                  p_mix=p_mix, lambda_conversation=.5)
    return q, tag, values


def test_common_cost_pooled_gradient_and_forced_zero():
    q, _, values = fixture()
    loss = mixed_alpha_loss(**values)
    # D=.3 and cost=.1 imply w*(u-u_without)=.4. Cost is paid exactly once.
    targets = torch.tensor([.25 + .75 * (.4 + .4) - .1, .75*.4])
    assert loss.item() == pytest.approx(float(((q[:2].detach() - targets)**2).sum()/4))
    loss.backward()
    assert torch.allclose(q.grad[:2], (q[:2].detach() - targets) / 2)
    assert q.grad[2] == 0


@pytest.mark.parametrize('p_mix', [0., 1.])
def test_zero_weight_components_are_not_required(p_mix):
    _, _, values = fixture(p_mix)
    if p_mix == 0:
        values['credits'] = []
        values['future_returns'] = []
    else:
        values['local_labels'] = []
        values['zero_labels'] = []
        values['conversation_costs'] = []
    assert torch.isfinite(mixed_alpha_loss(**values))


def test_one_missing_record_rejects_whole_update():
    q, tag, values = fixture()
    optimizer = torch.optim.Adam([q])
    values['conversation_costs'][0][tag] = None
    with pytest.raises(MissingRoutingTarget):
        update_routing(optimizer, **values)
    assert not optimizer.state
    assert q.grad is None


def test_target_tensors_are_detached_and_no_inherited_graph():
    q, tag, values = fixture()
    target = torch.tensor(.3, requires_grad=True)
    values['credits'][0][tag] = target
    mixed_alpha_loss(**values).backward()
    assert target.grad is None
    assert q.grad is not None


def test_no_records_skips_adam_moments():
    q, _, values = fixture()
    values['actions'][0].decisions = values['actions'][0].decisions[-1:]
    optimizer = torch.optim.Adam([q])
    assert update_routing(optimizer, **values) is None
    assert not optimizer.state


def test_pool_spans_sessions_without_category_reweighting():
    q, tag, values = fixture()
    values['actions'].append(SimpleNamespace(decisions=(values['actions'][0].decisions[1],)))
    for key in ('credits','local_labels','zero_labels','conversation_costs'):
        values[key].append(values[key][0])
    values['future_returns'].append(0.)
    loss = mixed_alpha_loss(**values)
    target = torch.tensor([.75, .3, 0.])
    expected = ((torch.stack((q[0], q[1], q[1])) - target)**2).sum()/6
    assert torch.allclose(loss, expected)


def test_no_selected_records_does_not_require_unavailable_future():
    q, _, values = fixture()
    values['actions'][0].decisions = values['actions'][0].decisions[-1:]
    values['future_returns'] = [None]
    assert mixed_alpha_loss(**values) is None


def test_missing_future_in_empty_session_does_not_invalidate_other_records():
    q, _, values = fixture()
    values['actions'].insert(0,SimpleNamespace(decisions=()))
    for key in ('credits','local_labels','zero_labels','conversation_costs'):
        values[key].insert(0,{})
    values['future_returns'].insert(0,None)
    assert torch.isfinite(mixed_alpha_loss(**values))


def test_only_zero_decisions_do_not_require_nonzero_credit_or_cost():
    _, _, values = fixture()
    values['actions'][0].decisions = values['actions'][0].decisions[1:]
    values['credits'] = values['local_labels'] = values['conversation_costs'] = []
    assert torch.isfinite(mixed_alpha_loss(**values))
