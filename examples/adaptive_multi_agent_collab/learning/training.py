"""Credits, losses, discrete gamma updates, and interleaved training."""

from __future__ import annotations

from ..infrastructure.storage import load_checkpoint, startup_scope, current_startup

import copy
import asyncio
import hashlib
import json
import math
import os
import random
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, fields, replace
import time
from pathlib import Path
from typing import Any, Protocol, cast

import torch
from torch import nn

from ..config import Config, PhaseConfig
from ..providers.resources import BudgetExhausted, AccountingFailure, PolicyStopped
from ..workflows.recovery import WorkflowRecoveryStopped
from ..models.encoders import ENCODER_ARCHITECTURE_ID
from ..models.policies import (
    ANSWER_STATE_ARCHITECTURE_ID,
    ROUTING_ARCHITECTURE_ID,
    WEIGHTING_ARCHITECTURE_ID,
    ROUTING_SEED_DERIVATION_ID,
    RoutingRolloutIdentity,
    _direct_candidate_mass_nll,
)
from ..workflows.prompts import (
    PromptProposal,
    PromptProposalBackend,
    PromptCollection,
    PromptOccurrence,
    failed_evidence,
)
from .evaluation import (
    EvaluationPlan,
    EvaluationRecord,
    SELECTION_OBJECTIVE_ID,
    WorkflowExecutionFailure,
    fatal_workflow,
)
from ..infrastructure.reproducibility import (
    _canonical_json,
    configured_encoder_metadata,
    scientific_configuration,
    validate_scientific_compatibility,
)
from ..schemas import (
    ANSWER_STABILITY_GUARD_ID,
    CROSS_AGENT_SCHEMES,
    SCHEME_ORDER,
    SELF_SCHEMES,
    ConversationTag,
    Scheme,
)

WEIGHTING_OBJECTIVE_ID = "direct_candidate_mass_nll_v1"
PROMPT_COLLECTION_ARCHITECTURE_ID = "component_local_textgrad_single_proposal_v1"


@dataclass(frozen=True, slots=True)
class SupportComponents:
    rho: float
    mu: float
    support: float
    modes: tuple[str, ...]


def _answer(value: Any) -> str:
    return str(getattr(value, "answer", value))


def _session_answers(session: Any) -> list[str]:
    resolved = getattr(session, "resolved_answers")
    return (
        [str(resolved[key]) for key in sorted(resolved)]
        if isinstance(resolved, Mapping)
        else [str(answer) for answer in resolved]
    )


@dataclass(slots=True)
class WeightingDiagnostics:
    """Checkpointed W-session categories, update outcomes, and zero-safe rates."""

    total_completed_sessions: int = 0
    unanimous_sessions: int = 0
    gold_absent_sessions: int = 0
    informative_sessions: int = 0
    unanimous_correct: int = 0
    unanimous_wrong: int = 0
    disagreement_all_wrong: int = 0
    attempted_w_updates: int = 0
    successful_w_updates: int = 0
    skipped_w_updates: int = 0
    failed_w_updates: int = 0

    def __post_init__(self) -> None:
        self._validate()

    @staticmethod
    def _rate(numerator: int, denominator: int) -> float:
        return 0.0 if denominator == 0 else numerator / denominator

    @property
    def unanimous_rate(self) -> float:
        return self._rate(self.unanimous_sessions, self.total_completed_sessions)

    @property
    def gold_absent_rate(self) -> float:
        return self._rate(self.gold_absent_sessions, self.total_completed_sessions)

    @property
    def informative_rate(self) -> float:
        return self._rate(self.informative_sessions, self.total_completed_sessions)

    @property
    def successful_update_rate(self) -> float:
        return self._rate(self.successful_w_updates, self.attempted_w_updates)

    @property
    def skipped_update_rate(self) -> float:
        return self._rate(self.skipped_w_updates, self.attempted_w_updates)

    def to_dict(self) -> dict[str, int | float]:
        """Return all reportable counts and rates as one training summary."""

        return {
            "total_completed_sessions": self.total_completed_sessions,
            "unanimous_sessions": self.unanimous_sessions,
            "gold_absent_sessions": self.gold_absent_sessions,
            "informative_sessions": self.informative_sessions,
            "unanimous_correct": self.unanimous_correct,
            "unanimous_wrong": self.unanimous_wrong,
            "disagreement_all_wrong": self.disagreement_all_wrong,
            "attempted_w_updates": self.attempted_w_updates,
            "successful_w_updates": self.successful_w_updates,
            "skipped_w_updates": self.skipped_w_updates,
            "failed_w_updates": self.failed_w_updates,
            "unanimous_rate": self.unanimous_rate,
            "gold_absent_rate": self.gold_absent_rate,
            "informative_rate": self.informative_rate,
            "successful_update_rate": self.successful_update_rate,
            "skipped_update_rate": self.skipped_update_rate,
        }

    def record_attempt(
        self,
        sessions: Sequence[Any],
        gold_answer: str,
        *,
        successful: bool,
    ) -> None:
        """Record every completed labelled session and one W minibatch attempt."""

        if gold_answer is None:
            raise ValueError("W-session diagnostics require a labelled trajectory")
        local = {
            "unanimous_sessions": 0,
            "gold_absent_sessions": 0,
            "informative_sessions": 0,
            "unanimous_correct": 0,
            "unanimous_wrong": 0,
            "disagreement_all_wrong": 0,
        }
        for session in sessions:
            candidates = set(_session_answers(session))
            if not candidates:
                raise ValueError("completed W sessions must contain resolved answers")
            unanimous = len(candidates) == 1
            gold_present = gold_answer in candidates
            local["unanimous_sessions"] += int(unanimous)
            local["gold_absent_sessions"] += int(not gold_present)
            if unanimous and gold_present:
                local["unanimous_correct"] += 1
            elif unanimous:
                local["unanimous_wrong"] += 1
            elif gold_present:
                local["informative_sessions"] += 1
            else:
                local["disagreement_all_wrong"] += 1

        # Informative supervision is necessary, but a permitted parameter scope
        # can be frozen or disconnected from the loss and therefore skip Adam.
        if successful and not local["informative_sessions"]:
            raise ValueError(
                "a successful W update requires an informative completed session"
            )

        self.total_completed_sessions += len(sessions)
        for name, value in local.items():
            setattr(self, name, getattr(self, name) + value)
        self.attempted_w_updates += 1
        if successful:
            self.successful_w_updates += 1
        else:
            self.skipped_w_updates += 1
        self._validate()

    def record_failure(self, sessions: Sequence[Any], gold_answer: str) -> None:
        self.record_attempt(sessions, gold_answer, successful=False)
        self.skipped_w_updates -= 1
        self.failed_w_updates += 1
        self._validate()

    def _validate(self) -> None:
        counts = (
            self.total_completed_sessions,
            self.unanimous_sessions,
            self.gold_absent_sessions,
            self.informative_sessions,
            self.unanimous_correct,
            self.unanimous_wrong,
            self.disagreement_all_wrong,
            self.attempted_w_updates,
            self.successful_w_updates,
            self.skipped_w_updates,
            self.failed_w_updates,
        )
        if any(value < 0 for value in counts):
            raise ValueError("weighting diagnostic counters must be non-negative")
        if self.unanimous_sessions != self.unanimous_correct + self.unanimous_wrong:
            raise ValueError("unanimous W-session counters are inconsistent")
        if self.gold_absent_sessions != self.unanimous_wrong + self.disagreement_all_wrong:
            raise ValueError("gold-absent W-session counters are inconsistent")
        four_way_total = (
            self.unanimous_correct + self.unanimous_wrong + self.disagreement_all_wrong + self.informative_sessions
        )
        if self.total_completed_sessions != four_way_total:
            raise ValueError("four-way W-session counters must sum to the completed total")
        if self.attempted_w_updates != self.successful_w_updates + self.skipped_w_updates + self.failed_w_updates:
            raise ValueError("attempted W updates must equal successful plus skipped plus failed updates")


def modal_answers(answers: Sequence[Any], answer_order: Sequence[str] = ()) -> tuple[str, ...]:
    values = [_answer(value) for value in answers]
    if not values:
        raise ValueError("modal answer requires a non-empty effective list")
    counts = {value: values.count(value) for value in set(values)}
    maximum = max(counts.values())
    order = list(dict.fromkeys([*map(str, answer_order), *values]))
    return tuple(value for value in order if counts.get(value) == maximum)


def support_components(
    answers: Sequence[Any],
    gold_answer: str,
    answer_order: Sequence[str] = (),
) -> SupportComponents:
    """Specification Step 10: proportional, tie-aware modal, and combined support."""

    values = [_answer(value) for value in answers]
    if not values:
        raise ValueError("support requires a non-empty effective terminal list")
    modes = modal_answers(values, answer_order)
    rho = sum(value == gold_answer for value in values) / len(values)
    mu = (1.0 / len(modes)) if gold_answer in modes else 0.0
    return SupportComponents(rho, mu, 0.5 * (rho + mu), modes)


def effective_answers(raw_records: Sequence[Any], fallback: str) -> list[str]:
    return [_answer(item) for item in raw_records] if raw_records else [fallback]


def remove_terminal_by_tag(records: Sequence[Any], tag: Any) -> list[Any]:
    """Remove exactly one tagged result, retaining equal answers from other tags."""

    removed = False
    result: list[Any] = []
    for record in records:
        if not removed and getattr(record, "tag", None) == tag:
            removed = True
            continue
        result.append(record)
    if not removed:
        raise KeyError(f"terminal record not found for tag {tag}")
    return result


def system_support(weights: torch.Tensor | Sequence[float], supports: Sequence[float]) -> torch.Tensor:
    weights_tensor = weights if isinstance(weights, torch.Tensor) else torch.tensor(weights)
    support_tensor = torch.as_tensor(supports, dtype=weights_tensor.dtype, device=weights_tensor.device)
    if weights_tensor.numel() != support_tensor.numel():
        raise ValueError("one support value is required for each agent weight")
    return torch.sum(weights_tensor * support_tensor)


def conversation_credits(
    raw_by_agent: Mapping[int, Sequence[Any]],
    carried_answers: Mapping[int, str],
    weights: torch.Tensor | Sequence[float],
    gold_answer: str,
    costs: Mapping[Any, float],
    *,
    lambda_conversation: float,
    answer_order: Sequence[str] = (),
) -> tuple[dict[Any, float], list[SupportComponents], float]:
    """Specification Step 12: cached leave-one-conversation-out credits."""

    detached = torch.as_tensor(weights).detach().cpu().tolist()
    agents = sorted(carried_answers)
    observed = [
        support_components(
            effective_answers(raw_by_agent.get(agent, ()), carried_answers[agent]),
            gold_answer,
            answer_order,
        )
        for agent in agents
    ]
    value = sum(detached[position] * item.support for position, item in enumerate(observed))
    credits: dict[Any, float] = {}
    for position, agent in enumerate(agents):
        for record in raw_by_agent.get(agent, ()):
            tag = getattr(record, "tag")
            reduced = remove_terminal_by_tag(raw_by_agent[agent], tag)
            counterfactual = support_components(
                effective_answers(reduced, carried_answers[agent]),
                gold_answer,
                answer_order,
            )
            credits[tag] = detached[position] * (
                observed[position].support - counterfactual.support
            ) - lambda_conversation * float(costs.get(tag, 0.0))
    return credits, observed, value


def allocate_shared_judge_cost(total_cost: float, tags: Sequence[Any]) -> dict[Any, float]:
    if not tags:
        if total_cost:
            raise ValueError("cannot allocate judge cost without judge-labelled edges")
        return {}
    share = float(total_cost) / len(tags)
    return {tag: share for tag in tags}


def session_rewards(
    values: Sequence[float],
    costs: Sequence[float],
    lambda_session: float,
) -> list[float]:
    """Values must include V^(0), followed by one value per completed session."""

    if len(values) != len(costs) + 1:
        raise ValueError("values must contain V^(0) plus one value per session cost")
    return [
        float(values[index + 1]) - float(values[index]) - lambda_session * float(costs[index])
        for index in range(len(costs))
    ]


def returns_and_future(
    rewards: Sequence[float | None],
) -> tuple[list[float | None], list[float | None]]:
    """G_s needs R_s onward; F_s needs only later rewards, with F_S = 0."""

    returns: list[float | None] = [None] * len(rewards)
    future: list[float | None] = [None] * len(rewards)
    running: float | None = 0.0
    for index in range(len(rewards) - 1, -1, -1):
        future[index] = running
        reward = rewards[index]
        running = None if reward is None or running is None else running + float(reward)
        returns[index] = running
    return returns, future


def _zero_label(label: Any) -> bool:
    value = getattr(label, "value", label)
    return value in {0, "0", "ZERO", "zero"}


def _decision_fields(decision: Any) -> tuple[Any, Any, torch.Tensor | None, bool]:
    edge = getattr(decision, "edge", None)
    if edge is None:
        edge = (getattr(decision, "initiator_id"), getattr(decision, "receiver_id"))
    label = getattr(decision, "scheme", getattr(decision, "label", None))
    free = bool(getattr(decision, "free", not getattr(decision, "forced_zero", False)))
    return edge, label, getattr(decision, "q_value", None), free


class MissingRoutingTarget(ValueError):
    """One unavailable required input invalidates the entire fresh trajectory."""


def _target_number(value: Any, label: str) -> float:
    if value is None:
        raise MissingRoutingTarget(label + " unavailable")
    number = float(value.detach() if isinstance(value, torch.Tensor) else value)
    if not math.isfinite(number):
        raise MissingRoutingTarget(label + " is nonfinite")
    return number


def mixed_alpha_loss(
    actions: Sequence[Any],
    credits: Sequence[Mapping[Any, float | None]],
    future_returns: Sequence[float | None],
    *,
    local_labels: Sequence[Mapping[Any, float | None]],
    zero_labels: Sequence[Mapping[int, float | None]],
    conversation_costs: Sequence[Mapping[Any, float | None]],
    p_mix: float,
    lambda_conversation: float,
) -> torch.Tensor | None:
    """Steps 13--19: one pooled raw-Q regression, with detached mixed targets.

    ``credits`` are the existing D values, already charged the allocated current
    conversation cost. Mixing (Y-lambda*C) with (D+F) therefore charges it once.
    Zero-weight components are never read. No unselected/forced decision enters
    the pool; absence of a required field rejects the entire update.
    """
    if not 0.0 <= p_mix <= 1.0 or not math.isfinite(lambda_conversation) or lambda_conversation < 0:
        raise ValueError("invalid mixed-routing coefficients")
    count = len(actions)
    choices = [_decision_fields(decision) for action in actions for decision in action.decisions]
    nonzero = any(not _zero_label(item[1]) for item in choices)
    free_zero = any(_zero_label(item[1]) and item[3] for item in choices)
    if not nonzero and not free_zero:
        return None
    required = []
    if p_mix > 0:
        required.append(future_returns)
        if nonzero:
            required.append(credits)
    if p_mix < 1:
        if free_zero:
            required.append(zero_labels)
        if nonzero:
            required.append(local_labels)
            if lambda_conversation:
                required.append(conversation_costs)
    if any(len(items) != count for items in required):
        raise MissingRoutingTarget("required routing target records must align with every completed session")
    terms: list[torch.Tensor] = []
    for index, action in enumerate(actions):
        for decision in action.decisions:
            edge, label, prediction, free = _decision_fields(decision)
            zero = _zero_label(label)
            if zero and not free:
                continue
            if prediction is None or prediction.numel() != 1:
                raise MissingRoutingTarget("selected raw Q prediction unavailable")
            target = 0.0
            if zero:
                if p_mix < 1:
                    target += (1.0 - p_mix) * _target_number(zero_labels[index].get(edge[0]), "zero correctness")
                if p_mix > 0:
                    target += p_mix * _target_number(future_returns[index], "future return")
            else:
                tag = getattr(decision, "tag", None)
                if tag is None:
                    tag = ConversationTag(int(getattr(decision, "session")), int(edge[0]), int(edge[1]), Scheme(label))
                if p_mix < 1:
                    correct = _target_number(local_labels[index].get(tag), "terminal correctness")
                    cost = (_target_number(conversation_costs[index].get(tag), "allocated conversation cost")
                            if lambda_conversation else 0.0)
                    target += (1.0 - p_mix) * (correct - lambda_conversation * cost)
                if p_mix > 0:
                    credit = _target_number(credits[index].get(tag), "conversation credit")
                    target += p_mix * (credit + _target_number(future_returns[index], "future return"))
            terms.append((prediction.reshape(()) - prediction.new_tensor(target).detach()).square())
    return torch.stack(terms).sum() / (2 * len(terms)) if terms else None


def resolved_candidate_scores(
    weights: torch.Tensor,
    answers: Sequence[str],
    answer_order: Sequence[str] = (),
) -> tuple[tuple[str, ...], torch.Tensor]:
    order = tuple(dict.fromkeys([*map(str, answer_order), *map(str, answers)]))
    candidates = tuple(candidate for candidate in order if candidate in answers)
    scores = torch.stack(
        [
            torch.sum(
                weights
                * torch.tensor(
                    [answer == candidate for answer in answers],
                    dtype=weights.dtype,
                    device=weights.device,
                )
            )
            for candidate in candidates
        ]
    )
    return candidates, scores


def weighting_loss_from_sessions(
    sessions: Sequence[Any],
    gold_answer: str,
    answer_order: Sequence[str] = (),
) -> tuple[torch.Tensor | None, int]:
    """Steps 22--24: mean direct candidate-mass NLL over informative sessions."""

    losses: list[torch.Tensor] = []
    for session in sessions:
        answers = _session_answers(session)
        candidates = set(answers)
        if gold_answer not in candidates or len(candidates) <= 1:
            continue
        labels, scores = resolved_candidate_scores(session.weights, answers, answer_order)
        target = labels.index(gold_answer)
        logits = getattr(session, "weight_logits", None)
        if logits is not None:
            losses.append(_direct_candidate_mass_nll(logits, answers, gold_answer))
        else:
            # ``scores[target]`` is s_gold. The clamp only protects synthetic
            # weight-only inputs from floating-point underflow at exact zero.
            tiny = torch.finfo(scores.dtype).tiny
            losses.append(-torch.log(scores[target].clamp_min(tiny)))
    return (torch.stack(losses).mean(), len(losses)) if losses else (None, 0)


@contextmanager
def frozen(*modules: nn.Module):
    states = [[parameter.requires_grad for parameter in module.parameters()] for module in modules]
    try:
        for module in modules:
            for parameter in module.parameters():
                parameter.requires_grad_(False)
        yield
    finally:
        for module, flags in zip(modules, states, strict=True):
            for parameter, flag in zip(module.parameters(), flags, strict=True):
                parameter.requires_grad_(flag)


class NumericalFailure(RuntimeError):
    """No parameter or optimizer-state mutation is committed on a nonfinite update."""


def _numerical_step(
    optimizer: torch.optim.Optimizer,
    loss: torch.Tensor,
    gradient_clip_norm: float | None,
) -> float | None:
    parameters = [parameter for group in optimizer.param_groups for parameter in group["params"]]
    if loss.numel() != 1 or not bool(torch.isfinite(loss).all()):
        raise NumericalFailure("nonfinite numerical loss")
    optimizer.zero_grad(set_to_none=True)
    try:
        loss.backward()
        if any(
            parameter.grad is not None and not bool(torch.isfinite(parameter.grad).all()) for parameter in parameters
        ):
            raise NumericalFailure("nonfinite numerical gradient")
        if gradient_clip_norm is not None:
            torch.nn.utils.clip_grad_norm_(parameters, gradient_clip_norm, norm_type=2.0, error_if_nonfinite=True)
    except RuntimeError as error:
        optimizer.zero_grad(set_to_none=True)
        if isinstance(error, NumericalFailure):
            raise
        raise NumericalFailure(f"numerical differentiation failed: {error}") from error
    if not any(parameter.grad is not None for parameter in parameters):
        return None
    before = [parameter.detach().clone() for parameter in parameters]
    state = copy.deepcopy(optimizer.state_dict())
    try:
        optimizer.step()
        if any(not bool(torch.isfinite(parameter).all()) for parameter in parameters):
            raise NumericalFailure("nonfinite parameters after optimizer step")
    except Exception as error:
        with torch.no_grad():
            for parameter, previous in zip(parameters, before, strict=True):
                parameter.copy_(previous)
        optimizer.load_state_dict(state)
        optimizer.zero_grad(set_to_none=True)
        raise NumericalFailure(f"optimizer step failed: {error}") from error
    return float(loss.detach())


def _capture_loss(
    record: dict[str, Any] | None,
    loss: torch.Tensor | None,
    count: int,
    denominator: int,
) -> None:
    """Detach only the scalar already computed for this update, never its graph."""
    if record is None:
        return
    scalar = float(loss.detach()) if loss is not None and loss.numel() == 1 else None
    record.update(
        loss=scalar if scalar is not None and math.isfinite(scalar) else None,
        example_count=count,
        normalization_denominator=denominator,
        status="skipped",
        reason="no_trainable_gradient",
    )


def update_weighting(
    model: nn.Module,
    optimizer: torch.optim.Optimizer,
    sessions: Sequence[Any],
    gold_answer: str,
    answer_order: Sequence[str] = (),
    *,
    diagnostics: WeightingDiagnostics | None = None,
    gradient_clip_norm: float | None = 1.0,
    loss_record: dict[str, Any] | None = None,
) -> float | None:
    loss, informative = weighting_loss_from_sessions(sessions, gold_answer, answer_order)
    _capture_loss(loss_record, loss, informative, informative)
    if not informative or loss is None or not loss.requires_grad:
        if loss_record is not None and not informative:
            loss_record["reason"] = "no_informative_sessions"
        if diagnostics is not None:
            diagnostics.record_attempt(sessions, gold_answer, successful=False)
        return None
    try:
        result = _numerical_step(optimizer, loss, gradient_clip_norm)
    except NumericalFailure as error:
        if loss_record is not None:
            loss_record.update(status="failed", reason="numerical_failure", error=str(error))
        if diagnostics is not None:
            diagnostics.record_failure(sessions, gold_answer)
        raise
    if loss_record is not None and result is not None:
        loss_record.update(status="updated", reason=None)
    if diagnostics is not None:
        diagnostics.record_attempt(sessions, gold_answer, successful=result is not None)
    return result


def update_routing(
    optimizer: torch.optim.Optimizer,
    actions: Sequence[Any],
    credits: Sequence[Mapping[Any, float | None]],
    future_returns: Sequence[float | None],
    *,
    local_labels: Sequence[Mapping[Any, float | None]],
    zero_labels: Sequence[Mapping[int, float | None]],
    conversation_costs: Sequence[Mapping[Any, float | None]],
    p_mix: float,
    lambda_conversation: float,
    gradient_clip_norm: float | None = 1.0,
    loss_record: dict[str, Any] | None = None,
) -> float | None:
    count = _routing_example_count(actions)
    _capture_loss(loss_record, None, count, 2 * count)
    try:
        loss = mixed_alpha_loss(
            actions, credits, future_returns, local_labels=local_labels,
            zero_labels=zero_labels, conversation_costs=conversation_costs,
            p_mix=p_mix, lambda_conversation=lambda_conversation,
        )
    except MissingRoutingTarget as error:
        if loss_record is not None:
            loss_record.update(reason="required_routing_credit_unavailable", error=str(error))
        raise
    _capture_loss(loss_record, loss, count, 2 * count)
    if loss is None or not loss.requires_grad:
        if loss_record is not None and not count:
            loss_record["reason"] = "no_eligible_decisions"
        return None
    try:
        result = _numerical_step(optimizer, loss, gradient_clip_norm)
    except NumericalFailure as error:
        if loss_record is not None:
            loss_record.update(status="failed", reason="numerical_failure", error=str(error))
        raise
    if loss_record is not None and result is not None:
        loss_record.update(status="updated", reason=None)
    return result


def _routing_example_count(actions: Sequence[Any]) -> int:
    return sum(
        not _zero_label(label) or free
        for action in actions
        for decision in action.decisions
        for _, label, _, free in (_decision_fields(decision),)
    )


def validation_plateaued(history: Sequence[float | None], patience: int, epsilon: float) -> bool:
    if patience <= 0 or len(history) <= patience:
        return False
    window = history[-patience - 1 :]
    if any(value is None or not math.isfinite(value) for value in window):
        return False
    finite = [value for value in history if value is not None and math.isfinite(value)]
    earlier = [value for value in history[:-patience] if value is not None and math.isfinite(value)]
    return max(finite) - max(earlier) <= epsilon


@dataclass(frozen=True, slots=True)
class PromptStepResult:
    collection: PromptCollection
    accepted: bool
    current_score: float
    proposed_score: float | None
    reason: str
    baseline: EvaluationRecord
    proposed: EvaluationRecord | None = None
    proposal: PromptProposal | None = None

    @property
    def retained_score(self) -> float:
        return self.proposed_score if self.accepted else self.current_score

    @property
    def paired_improvements(self) -> tuple[float, ...]:
        if self.proposed is None:
            return ()
        if self.proposed.score is None or self.baseline.score is None:
            raise ValueError("paired improvements require every planned query cost")
        return tuple(
            new - old
            for new, old in zip(
                self.proposed.per_example_scores,
                self.baseline.per_example_scores,
                strict=True,
            )
            if new is not None and old is not None
        )


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _module_fingerprint(module: nn.Module) -> str:
    digest = hashlib.sha256()
    for name, tensor in module.state_dict().items():
        digest.update(name.encode())
        digest.update(str((tensor.dtype, tuple(tensor.shape))).encode())
        digest.update(tensor.detach().cpu().contiguous().numpy().tobytes())
    return digest.hexdigest()


def parameter_fingerprints(trainer: Any) -> dict[str, str]:
    return {
        "alpha": _module_fingerprint(trainer.alpha),
        "weighting": _module_fingerprint(trainer.weighting),
        "gamma": hashlib.sha256(json.dumps(trainer.gamma.to_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest(),
    }


def _prompt_failure(error: BaseException) -> PromptProposal:
    """Retain graph-call receipts and the originating stop disposition."""
    attached = getattr(error, "prompt_proposal", None)
    proposal = attached if isinstance(attached, PromptProposal) else PromptProposal(None)
    disposition = (
        "cancelled" if isinstance(error, asyncio.CancelledError) else
        "policy-stop" if isinstance(error, PolicyStopped) else
        "accounting-stop" if isinstance(error, AccountingFailure) else
        "budget-exhausted" if isinstance(error, BudgetExhausted) else
        getattr(error, "failure_disposition", None)
    )
    metadata = dict(proposal.metadata)
    metadata.update(
        operational_stop=bool(metadata.get("operational_stop") or
            isinstance(error, (AccountingFailure, PolicyStopped, asyncio.CancelledError)) or
            getattr(error, "operational_stop", False)),
        budget_exhausted=bool(metadata.get("budget_exhausted") or
            (isinstance(error, BudgetExhausted) and not isinstance(error, PolicyStopped)) or
            getattr(error, "budget_exhausted", False)),
    )
    if disposition is not None:
        metadata["failure_disposition"] = disposition
    return replace(proposal, template=None, error=proposal.error or f"{type(error).__name__}: {error}",
                   metadata=metadata)


class PromptOptimizer:
    """A local TextGrad proposal followed by one complete, strictly paired comparison."""

    def __init__(self, proposer: PromptProposalBackend) -> None:
        from ..infrastructure.incremental import TrackedDict
        self.proposer = proposer
        self.condition: str | None = None
        self.plan: EvaluationPlan | None = None
        self.cache: dict[str, EvaluationRecord] = TrackedDict()
        self._json_state = TrackedDict({"condition": None, "plan": None, "cache": {}})

    def metadata(self) -> dict[str, Any]:
        return self.proposer.metadata()

    def _key(self, collection: PromptCollection) -> str:
        return _fingerprint(collection.to_dict())

    async def _evaluate(
        self,
        collection: PromptCollection,
        evaluate: Callable[[PromptCollection, EvaluationPlan], Awaitable[EvaluationRecord]],
    ) -> EvaluationRecord:
        if self.plan is None:
            raise RuntimeError("establish the prompt phase before evaluating a collection")
        key = self._key(collection)
        if key not in self.cache:
            record = await evaluate(collection, self.plan)
            if not isinstance(record, EvaluationRecord) or record.plan.fingerprint != self.plan.fingerprint:
                raise ValueError("prompt validation must return the entire unchanged evaluation plan")
            if record.objective != "selection" or (record.score is None or not math.isfinite(record.score)):
                raise ValueError("prompt validation requires finite complete returns")
            self.cache[key] = record
            # Produce the JSON history once when this completed record enters
            # the cache, not repeatedly at every subsequent checkpoint.
            self._json_state["cache"][key] = record.to_dict()
        # Cached records retain their original workflow costs; reuse never sets cost to zero.
        return self.cache[key]

    async def begin_phase(
        self,
        collection: PromptCollection,
        plan: EvaluationPlan,
        condition: str,
        evaluate: Callable[[PromptCollection, EvaluationPlan], Awaitable[EvaluationRecord]],
    ) -> EvaluationRecord:
        if plan.domain != "promptval":
            raise ValueError("prompt optimization requires the promptval domain")
        from ..infrastructure.incremental import TrackedDict
        self.condition, self.plan, self.cache = condition, plan, TrackedDict()
        self._json_state = TrackedDict({"condition": condition, "plan": plan.to_dict(), "cache": {}})
        return await self._evaluate(collection, evaluate)

    def state_dict(self) -> dict[str, Any]:
        return {
            "condition": self.condition,
            "plan": None if self.plan is None else self.plan.to_dict(),
            "cache": {key: record.to_dict() for key, record in self.cache.items()},
        }

    def checkpoint_state_dict(self, store: Any) -> dict[str, Any]:
        from ..infrastructure.incremental import snapshot_collection
        return {"condition": self.condition,
                "plan": copy.deepcopy(self._json_state["plan"]),
                "cache": snapshot_collection(store, self._json_state["cache"])}

    def recorded_state(self) -> dict[str, Any]:
        """The owned JSON projection shared explicitly with TrainerState."""
        return self._json_state

    @staticmethod
    def validate_state(value: Mapping[str, Any]) -> None:
        plan = EvaluationPlan.from_dict(value["plan"]) if value.get("plan") is not None else None
        for record in value.get("cache", {}).values():
            parsed = EvaluationRecord.from_dict(record)
            if plan is None or parsed.plan.fingerprint != plan.fingerprint or parsed.objective != "selection":
                raise ValueError("corrupt prompt-validation cache")
        if plan is not None and (plan.domain != "promptval" or not value.get("condition")):
            raise ValueError("corrupt prompt phase condition")

    def load_state_dict(self, value: Mapping[str, Any]) -> None:
        from ..infrastructure.incremental import TrackedDict
        self.validate_state(value)
        self.condition = value.get("condition")
        self.plan = EvaluationPlan.from_dict(value["plan"]) if value.get("plan") else None
        self.cache = TrackedDict({key: EvaluationRecord.from_dict(record) for key, record in value.get("cache", {}).items()})
        self._json_state = TrackedDict({"condition": self.condition, "plan": value.get("plan"),
                                       "cache": value.get("cache", {})})

    async def step(
        self,
        collection: PromptCollection,
        component: str,
        occurrences: Sequence[PromptOccurrence],
        *,
        condition: str,
        iteration_id: str,
        evaluate: Callable[[PromptCollection, EvaluationPlan], Awaitable[EvaluationRecord]],
    ) -> PromptStepResult:
        if self.condition != condition:
            raise ValueError("numerical parameters or execution condition changed within the prompt phase")
        baseline = await self._evaluate(collection, evaluate)
        if any(item.split != "train" for item in occurrences):
            raise ValueError("textual optimization evidence must originate in the training partition")
        failed = failed_evidence(component, occurrences)
        if not failed:
            return PromptStepResult(collection, False, baseline.score, None, "no_failure_evidence", baseline)
        try:
            proposal = await self.proposer.propose(collection.components[component], failed, iteration_id=iteration_id)
        except asyncio.CancelledError as error:
            error.prompt_proposal = _prompt_failure(error)
            raise
        except Exception as error:  # An unsuccessful graph cannot mutate accepted prompts.
            proposal = _prompt_failure(error)
        if proposal.metadata.get("operational_stop") or proposal.metadata.get("budget_exhausted"):
            return PromptStepResult(collection, False, baseline.score, None,
                                    "execution_stopped", baseline, proposal=proposal)
        if proposal.error or not isinstance(proposal.template, str):
            return PromptStepResult(
                collection,
                False,
                baseline.score,
                None,
                "proposal_failed",
                baseline,
                proposal=proposal,
            )
        if proposal.template == collection.components[component].template:
            return PromptStepResult(
                collection,
                False,
                baseline.score,
                None,
                "unchanged",
                baseline,
                proposal=proposal,
            )
        try:
            candidate = collection.replace_template(component, proposal.template)
        except (ValueError, TypeError):
            return PromptStepResult(
                collection,
                False,
                baseline.score,
                None,
                "invalid_structure",
                baseline,
                proposal=proposal,
            )
        try:
            proposed = await self._evaluate(candidate, evaluate)
        except asyncio.CancelledError as error:
            error.prompt_proposal = replace(proposal, **{
                "template": None, "error": f"validation cancelled: {error}",
                "metadata": {**proposal.metadata, **_prompt_failure(error).metadata},
            })
            raise
        except Exception as error:
            failed_proposal = replace(
                proposal, template=None, error=f"validation failed: {type(error).__name__}: {error}",
                metadata={**proposal.metadata, **_prompt_failure(error).metadata},
            )
            return PromptStepResult(
                collection,
                False,
                baseline.score,
                None,
                "validation_failed",
                baseline,
                proposal=failed_proposal,
            )
        accepted = proposed.score > baseline.score
        return PromptStepResult(
            candidate if accepted else collection,
            accepted,
            baseline.score,
            proposed.score,
            "accepted" if accepted else "not_strictly_better",
            baseline,
            proposed,
            proposal,
        )


class TrainingHooks(Protocol):
    """Concrete Experiment binds original-query partitions to Runtime workflows."""

    def bind(self, trainer: InterleavedTrainer) -> None: ...
    def state_dict(self) -> dict[str, Any]: ...
    def validate_state(self, state: Mapping[str, Any]) -> None: ...
    def load_state_dict(self, state: Mapping[str, Any]) -> None: ...
    def restore_selected_state(self, state: Mapping[str, Any]) -> None: ...
    async def pilot_safety(self) -> Any: ...
    async def calibrate_termination(self) -> Any: ...
    async def trajectory(self, component: str, split: str, *, routing_identity: RoutingRolloutIdentity) -> Any: ...
    async def validate(self, component: str) -> EvaluationRecord: ...
    async def validate_supplement_routing(self, *, weighting_changed: bool) -> EvaluationRecord: ...
    async def prompt_evidence(self, component: str) -> Sequence[PromptOccurrence]: ...
    async def prompt_validation_batch(self) -> EvaluationPlan: ...
    async def evaluate_prompts(self, collection: PromptCollection, batch: EvaluationPlan) -> EvaluationRecord: ...
    async def system_validation(self) -> EvaluationRecord: ...


@dataclass
class TrainerState:
    phase: str = "unstarted"
    outer_cycle: int = 0
    updates: dict[str, int] = field(default_factory=lambda: {"weighting": 0, "routing": 0, "prompt": 0})
    attempts: dict[str, int] = field(default_factory=lambda: {"weighting": 0, "routing": 0, "prompt": 0})
    weighting_diagnostics: WeightingDiagnostics = field(default_factory=WeightingDiagnostics)
    validation_history: dict[str, list[float | None]] = field(
        default_factory=lambda: {
            "weighting": [],
            "routing": [],
            "prompt": [],
            "system": [],
        }
    )
    phase_records: list[dict[str, Any]] = field(default_factory=list)
    phase_progress: dict[str, Any] = field(default_factory=dict)
    prompt_validation_records: list[dict[str, Any]] = field(default_factory=list)
    system_validation_records: list[dict[str, Any]] = field(default_factory=list)
    prompt_cursor: int = 0
    prompt_cache: dict[str, Any] = field(default_factory=dict)
    best_system_score: float | None = None
    bootstrap_complete: dict[str, bool] = field(default_factory=lambda: {"weighting": False, "routing": False})
    pilot_complete: bool = False
    calibration_complete: bool = False
    outer_stale: int = 0
    workflow_attempt_counter: int = 0
    execution_failures: list[dict[str, Any]] = field(default_factory=list)
    eligible_checkpoints: list[int] = field(default_factory=list)
    training_loss_records: list[dict[str, Any]] = field(default_factory=list)
    weighting_supplement: dict[str, Any] = field(default_factory=dict)

    def __setattr__(self, name: str, value: Any) -> None:
        from ..infrastructure.incremental import TrackedDict, TrackedList
        # Installing listeners at assignment avoids detached/stale phase-progress
        # references when callers keep the mapping across a checkpoint.
        if isinstance(value, dict) and not isinstance(value, TrackedDict):
            value = TrackedDict(value)
        elif isinstance(value, list) and not isinstance(value, TrackedList):
            value = TrackedList(value)
        object.__setattr__(self, name, value)

    def __post_init__(self) -> None:
        if (
            min(
                self.outer_cycle,
                self.outer_stale,
                self.workflow_attempt_counter,
                self.prompt_cursor,
            )
            < 0
        ):
            raise ValueError("trainer progress counters must be non-negative")
        if self.phase not in {
            "unstarted",
            "pilot",
            "pilot_complete",
            "weighting_bootstrap",
            "routing_bootstrap",
            "weighting_supplement",
            "routing_supplement_validation",
            "calibration",
            "system_baseline",
            "failed",
            "weighting",
            "routing",
            "prompt",
            "system",
            "complete",
        }:
            raise ValueError("invalid trainer phase")
        for counters in (self.updates, self.attempts):
            if set(counters) != {"weighting", "routing", "prompt"} or any(value < 0 for value in counters.values()):
                raise ValueError("invalid trainer update/attempt counters")
        if self.prompt_cache:
            PromptOptimizer.validate_state(self.prompt_cache)
        plans = set()
        for value in self.system_validation_records:
            record = EvaluationRecord.from_dict(value)
            if record.plan.domain != "sysval" or record.objective != "selection":
                raise ValueError("invalid stored system validation record")
            plans.add(record.plan.fingerprint)
        if len(plans) > 1:
            raise ValueError("system validation plan changed across checkpoints")
        seen_attempts: set[int] = set()
        for record in self.training_loss_records:
            _validate_training_loss_record(record)
            attempt = record["workflow_attempt_id"]
            if attempt in seen_attempts or attempt >= self.workflow_attempt_counter:
                raise ValueError("duplicate or unallocated training loss workflow attempt")
            seen_attempts.add(attempt)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> TrainerState:
        data = copy.deepcopy(dict(value))
        data["weighting_diagnostics"] = WeightingDiagnostics(**data["weighting_diagnostics"])
        return cls(**data)


def _validate_training_loss_record(record: Mapping[str, Any]) -> None:
    """Validate additive diagnostic state before any checkpoint restoration."""
    component = record.get("component")
    if (
        record.get("protocol") != "numerical_preupdate_loss_v1"
        or component not in {"weighting", "routing"}
        or record.get("phase") not in {component, f"{component}_bootstrap", f"{component}_supplement"}
        or record.get("status") not in {"updated", "skipped", "failed"}
        or (record.get("query_id") is not None and not isinstance(record["query_id"], str))
    ):
        raise ValueError("invalid training loss record identity or outcome")
    for key in (
        "workflow_attempt_id", "outer_cycle", "phase_attempt", "component_attempt",
        "update_index_before", "update_index_after", "phase_update_index_before", "phase_update_index_after",
    ):
        if type(record.get(key)) is not int or record[key] < 0:
            raise ValueError("invalid training loss progress counter")
    if record["phase_attempt"] < 1 or record["component_attempt"] < 1:
        raise ValueError("training loss record requires a consumed attempt")
    delta = int(record["status"] == "updated")
    if (
        record["update_index_after"] != record["update_index_before"] + delta
        or record["phase_update_index_after"] != record["phase_update_index_before"] + delta
    ):
        raise ValueError("training loss outcome disagrees with update counters")
    value = record.get("loss")
    if value is not None and (type(value) not in {int, float} or not math.isfinite(value)):
        raise ValueError("training loss must be a finite detached scalar or unavailable")
    count, denominator = record.get("example_count"), record.get("normalization_denominator")
    if count is None:
        if denominator is not None or value is not None:
            raise ValueError("unavailable training loss has inconsistent normalization")
    elif (
        type(count) is not int or count < 0 or type(denominator) is not int
        or denominator != count * (2 if component == "routing" else 1)
        or (value is not None and not count)
    ):
        raise ValueError("invalid training loss normalization")
    if delta and (value is None or not count or record.get("reason") is not None):
        raise ValueError("successful update requires an available loss")
    if not delta and not isinstance(record.get("reason"), str):
        raise ValueError("skipped or failed loss record requires a reason")


def _architecture_identifiers() -> dict[str, str]:
    from .evaluation import Experiment

    return {
        **{f"workflow_{key}": value for key, value in Experiment._workflow_protocol().items()},
        "answer_state_architecture_id": ANSWER_STATE_ARCHITECTURE_ID,
        "routing_architecture_id": ROUTING_ARCHITECTURE_ID,
        "weighting_architecture_id": WEIGHTING_ARCHITECTURE_ID,
        "encoder_architecture_id": ENCODER_ARCHITECTURE_ID,
        "prompt_collection_architecture_id": PROMPT_COLLECTION_ARCHITECTURE_ID,
        "routing_seed_derivation_id": ROUTING_SEED_DERIVATION_ID,
        "routing_scheme_order": ",".join(scheme.value for scheme in SCHEME_ORDER),
        "self_edge_legal_schemes": ",".join(scheme.value for scheme in SELF_SCHEMES),
        "cross_edge_legal_schemes": ",".join(scheme.value for scheme in CROSS_AGENT_SCHEMES),
        "weighting_objective_id": WEIGHTING_OBJECTIVE_ID,
        "routing_objective_id": "pooled_mixed_q_regression_v1",
        "answer_stability": ANSWER_STABILITY_GUARD_ID,
        "validation_protocol": "complete_selection_trajectory_v1",
        "selection_objective": SELECTION_OBJECTIVE_ID,
        "optimizer_protocol": "adam_global_l2_finite_transaction_v1",
        "training_schedule": "explicit_pilot_fatal_boundary_shared_calibration_checkpoint_zero_v2",
    }


def _validated_encoder_metadata(config: Config, encoder_metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    configured = configured_encoder_metadata(config)
    if encoder_metadata is not None and dict(encoder_metadata) != configured:
        raise ValueError("injected fixed-encoder metadata does not match Config.encoders")
    return configured


def _validate_optimizer_state(optimizer: torch.optim.Optimizer, state: Mapping[str, Any]) -> None:
    candidate = copy.deepcopy(optimizer)
    candidate.load_state_dict(state)
    for group in candidate.param_groups:
        for parameter in group["params"]:
            for name, value in candidate.state.get(parameter, {}).items():
                if isinstance(value, torch.Tensor):
                    if name == "step" and value.numel() == 1:
                        continue
                    if value.shape != parameter.shape:
                        raise ValueError(f"incompatible optimizer tensor shape for {name}")


class CheckpointManager:
    """Validate the entire checkpoint before restoring any mutable model, optimizer, or RNG."""

    schema_version = 12

    @classmethod
    def save(
        cls,
        path: str | Path,
        *,
        alpha: nn.Module,
        weighting: nn.Module,
        gamma: PromptCollection,
        alpha_optimizer: torch.optim.Optimizer,
        weighting_optimizer: torch.optim.Optimizer,
        state: TrainerState,
        config: Config,
        encoder_metadata: Mapping[str, Any] | None = None,
        best_system: Mapping[str, Any] | None = None,
        declared_config: Config | None = None,
        hook_state: Mapping[str, Any] | None = None,
        prompt_backend_metadata: Mapping[str, Any] | None = None,
        request_boundary_pending: bool = False,
    ) -> None:
        cls.write_payload(path, cls.capture(
            alpha=alpha, weighting=weighting, gamma=gamma,
            alpha_optimizer=alpha_optimizer, weighting_optimizer=weighting_optimizer,
            state=state, config=config, encoder_metadata=encoder_metadata,
            best_system=best_system, declared_config=declared_config, hook_state=hook_state,
            prompt_backend_metadata=prompt_backend_metadata, request_boundary_pending=request_boundary_pending,
        ))

    @classmethod
    def capture(
        cls,
        *,
        alpha: nn.Module,
        weighting: nn.Module,
        gamma: PromptCollection,
        alpha_optimizer: torch.optim.Optimizer,
        weighting_optimizer: torch.optim.Optimizer,
        state: TrainerState,
        config: Config,
        encoder_metadata: Mapping[str, Any] | None = None,
        best_system: Mapping[str, Any] | None = None,
        declared_config: Config | None = None,
        hook_state: Mapping[str, Any] | None = None,
        prompt_backend_metadata: Mapping[str, Any] | None = None,
        request_boundary_pending: bool = False,
        component_payload: Mapping[str, Any] | None = None,
        state_payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        import numpy as np

        if not isinstance(request_boundary_pending, bool):
            raise ValueError("physical request boundary state must be boolean")
        declared = declared_config or config
        metadata = _validated_encoder_metadata(declared, encoder_metadata)
        condition = scientific_configuration(
            declared,
            encoder_metadata=metadata,
            architecture_identifiers=_architecture_identifiers(),
        )
        components = component_payload or {}
        payload = {
            "schema_version": cls.schema_version,
            "request_boundary_pending": request_boundary_pending,
            "encoder_metadata": metadata,
            "encoder_fingerprint": _fingerprint(metadata),
            "scientific_configuration": condition.to_dict(),
            "architecture_identifiers": _architecture_identifiers(),
            "prompt_backend_metadata": dict(prompt_backend_metadata or {}),
            "extension_architecture": copy.deepcopy(getattr(alpha, "extension_metadata", None)),
            "alpha": components["alpha"] if "alpha" in components else alpha.state_dict(),
            "weighting": components["weighting"] if "weighting" in components else weighting.state_dict(),
            "gamma": gamma.to_dict(),
            "alpha_optimizer": components["alpha_optimizer"] if "alpha_optimizer" in components else alpha_optimizer.state_dict(),
            "weighting_optimizer": components["weighting_optimizer"] if "weighting_optimizer" in components else weighting_optimizer.state_dict(),
            "trainer_state": state_payload if state_payload is not None else asdict(state),
            "config": config.to_dict(),
            "declared_config": declared.to_dict(),
            "best_system": best_system,
            "hook_state": hook_state,
            "python_random_state": random.getstate(),
            "numpy_random_state": np.random.get_state(),
            "torch_random_state": torch.random.get_rng_state(),
            "cuda_random_states": (torch.cuda.get_rng_state_all() if torch.cuda.is_available() else []),
        }
        return copy.deepcopy(payload)

    @classmethod
    def write_payload(cls, path: str | Path, payload: Mapping[str, Any], *, before_publish: Any = None) -> None:
        """Serialize an isolated snapshot; optional guard rejects late publication."""
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".pending")
        try:
            with temporary.open("wb") as stream:
                torch.save(payload, stream)
                stream.flush()
                os.fsync(stream.fileno())
            if before_publish is not None:
                before_publish()
            temporary.replace(target)
            directory_fd = os.open(target.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            temporary.unlink(missing_ok=True)

    @classmethod
    def load(
        cls,
        path: str | Path,
        *,
        alpha: nn.Module,
        weighting: nn.Module,
        alpha_optimizer: torch.optim.Optimizer,
        weighting_optimizer: torch.optim.Optimizer,
        config: Config,
        encoder_metadata: Mapping[str, Any] | None = None,
        hook_validator: Callable[[Mapping[str, Any]], None] | None = None,
        hook_commit: Callable[[], None] | None = None,
        prompt_backend_metadata: Mapping[str, Any] | None = None,
    ) -> tuple[PromptCollection, TrainerState, dict[str, Any]]:
        import numpy as np

        payload = load_checkpoint(Path(path))
        if not isinstance(payload, dict) or payload.get("schema_version") != cls.schema_version:
            raise ValueError(f"unsupported checkpoint schema; expected {cls.schema_version}")
        if payload.get("request_boundary_pending") is not False:
            raise ValueError(
                "checkpoint has an unresolved physical request boundary; operator reconciliation is required before resume"
            )
        if payload.get("extension_architecture") != getattr(alpha, "extension_metadata", None):
            raise ValueError("checkpoint append-only block architecture differs")
        metadata = _validated_encoder_metadata(config, encoder_metadata)
        active = scientific_configuration(
            config,
            encoder_metadata=metadata,
            architecture_identifiers=_architecture_identifiers(),
        )
        validate_scientific_compatibility(payload.get("scientific_configuration", {}), active)
        if payload.get("architecture_identifiers") != _architecture_identifiers():
            raise ValueError("incompatible numerical or training architecture")
        if payload.get("encoder_metadata") != metadata or payload.get("encoder_fingerprint") != _fingerprint(metadata):
            raise ValueError("incompatible or corrupt fixed-encoder metadata fingerprint")
        if payload.get("prompt_backend_metadata") != dict(prompt_backend_metadata or {}):
            raise ValueError("incompatible TextGrad engines or optimization configuration")
        state = TrainerState.from_dict(payload["trainer_state"])
        gamma = PromptCollection.from_dict(payload["gamma"], registrations=config.scheme_registry)
        active_config = Config.from_dict(payload["config"])
        saved_declared = Config.from_dict(payload["declared_config"])
        saved_projection = scientific_configuration(
            saved_declared,
            encoder_metadata=metadata,
            architecture_identifiers=_architecture_identifiers(),
        )
        # This declared projection is a JSON contract: storage may restore
        # arrays as lists although legacy torch payloads retained tuples.
        # Compare its existing canonical representation only; tensor/optimizer
        # state and all remaining checkpoint objects retain their own validators.
        if _canonical_json(saved_projection.to_dict()) != _canonical_json(payload["scientific_configuration"]):
            raise ValueError("checkpoint declared configuration disagrees with its fingerprint")
        # Pilot and termination calibration may change only their declared configuration groups.
        if (
            replace(
                active_config,
                safety=saved_declared.safety,
                termination=saved_declared.termination,
            )
            != saved_declared
        ):
            raise ValueError("checkpoint active configuration changed outside calibration")
        if not state.pilot_complete and active_config.safety != saved_declared.safety:
            raise ValueError("checkpoint changed safeguards before completing the training pilot")
        if not state.calibration_complete and active_config.termination != saved_declared.termination:
            raise ValueError("checkpoint changed termination before completing calibration")
        if payload.get("hook_state") is not None:
            if hook_validator is None:
                raise ValueError("resume requires the recorded experiment data and protocol")
            hook_validator(payload["hook_state"])
            from .calibration import (
                SafetyCalibrationRecord,
                TerminationCalibrationRecord,
            )

            saved_hooks = payload["hook_state"]
            if "safety_record" in saved_hooks and state.pilot_complete:
                if saved_hooks["safety_record"] is None:
                    raise ValueError("completed pilot is missing its frozen safeguard record")
                safety_record = SafetyCalibrationRecord.from_dict(saved_hooks["safety_record"])
                if not safety_record.execution_complete:
                    raise ValueError("completed pilot state contains an incomplete or halted report")
                if active_config.safety != safety_record.configuration:
                    raise ValueError("active safeguards disagree with the frozen pilot record")
            if "termination_record" in saved_hooks and state.calibration_complete:
                if saved_hooks["termination_record"] is None:
                    raise ValueError("completed termination calibration is missing its selected record")
                termination_record = TerminationCalibrationRecord.from_dict(saved_hooks["termination_record"])
                if active_config.termination != termination_record.selected_configuration:
                    raise ValueError("active termination disagrees with the frozen calibration record")
        # Loading isolated copies catches missing tensors, shapes, optimizer groups, and bad RNG
        # before any caller-owned object is touched.
        copy.deepcopy(alpha).load_state_dict(payload["alpha"])
        copy.deepcopy(weighting).load_state_dict(payload["weighting"])
        _validate_optimizer_state(alpha_optimizer, payload["alpha_optimizer"])
        _validate_optimizer_state(weighting_optimizer, payload["weighting_optimizer"])
        random.Random().setstate(payload["python_random_state"])
        np.random.RandomState().set_state(payload["numpy_random_state"])
        torch.Generator().set_state(payload["torch_random_state"])
        if payload["cuda_random_states"] and not torch.cuda.is_available():
            raise ValueError("CUDA RNG state cannot be resumed in this CPU-only environment")
        best = payload.get("best_system")
        if best is not None:
            selected_record = EvaluationRecord.from_dict(best["evaluation"])
            if (
                selected_record.plan.domain != "sysval"
                or selected_record.objective != "selection"
                or selected_record.score != best["score"]
                or best["score"] != state.best_system_score
                or best["evaluation"] not in state.system_validation_records
            ):
                raise ValueError("best joint checkpoint evaluation does not match its selected system score")
            copy.deepcopy(alpha).load_state_dict(best["alpha"])
            copy.deepcopy(weighting).load_state_dict(best["weighting"])
            _validate_optimizer_state(alpha_optimizer, best["alpha_optimizer"])
            _validate_optimizer_state(weighting_optimizer, best["weighting_optimizer"])
            PromptCollection.from_dict(best["gamma"], registrations=config.scheme_registry)
            random.Random().setstate(best["python_random_state"])
            np.random.RandomState().set_state(best["numpy_random_state"])
            torch.Generator().set_state(best["torch_random_state"])
            if best["cuda_random_states"] and not torch.cuda.is_available():
                raise ValueError("selected CUDA RNG state cannot be resumed in this CPU-only environment")
            if hook_validator is not None:
                hook_validator(best["hook_state"])
            if min(best["workflow_attempt_counter"], best["prompt_cursor"]) < 0:
                raise ValueError("invalid selected checkpoint protocol counters")
            best_config = Config.from_dict(best["config"])
            if (
                replace(
                    best_config,
                    training=replace(
                        best_config.training,
                        max_outer_cycles=active_config.training.max_outer_cycles,
                    ),
                )
                != active_config
            ):
                raise ValueError("best joint checkpoint has incompatible frozen configuration")
        if hook_commit is not None:
            hook_commit()
        alpha.load_state_dict(payload["alpha"])
        weighting.load_state_dict(payload["weighting"])
        alpha_optimizer.load_state_dict(payload["alpha_optimizer"])
        weighting_optimizer.load_state_dict(payload["weighting_optimizer"])
        random.setstate(payload["python_random_state"])
        np.random.set_state(payload["numpy_random_state"])
        torch.random.set_rng_state(payload["torch_random_state"])
        if payload["cuda_random_states"]:
            torch.cuda.set_rng_state_all(payload["cuda_random_states"])
        return gamma, state, payload


@contextmanager
def evaluating(*modules: nn.Module):
    modes = tuple(module.training for module in modules)
    try:
        for module in modules:
            module.eval()
        with torch.no_grad():
            yield
    finally:
        for module, mode in zip(modules, modes, strict=True):
            module.train(mode)


def _numerical_version(value: Any) -> Any:
    """Mutation counters and scalar configuration, without reading tensor bytes."""
    if isinstance(value, torch.Tensor):
        return (id(value), value._version, tuple(value.shape), value.dtype, value.device)
    if isinstance(value, Mapping):
        return tuple((key, _numerical_version(item)) for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return tuple(_numerical_version(item) for item in value)
    if value is None or isinstance(value, (bool, str, int, float)):
        return value
    raise TypeError(f"unsupported numerical component metadata: {type(value).__name__}")


def _tensor_bytes(value: Any) -> int:
    if isinstance(value, torch.Tensor):
        return value.numel() * value.element_size()
    if isinstance(value, Mapping):
        return sum(_tensor_bytes(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return sum(_tensor_bytes(item) for item in value)
    return 0


class _NumericalSnapshots:
    """Owner-checked immutable component refs, with no retained mutable tensor."""

    def __init__(self) -> None:
        self._references: dict[Any, tuple[Any, Any]] = {}

    def capture(self, store: Any, name: str, value: Any, *, epoch: int = 0) -> Any:
        version = (epoch, _numerical_version(value))
        key = (store.directory, name)
        previous = self._references.get(key)
        if previous is not None and previous[0] == version:
            store.metrics["objects_reused"] += 1
            return previous[1]
        started = time.perf_counter()
        isolated = copy.deepcopy(value)
        store.metrics["tensor_capture_seconds"] += time.perf_counter() - started
        store.metrics["tensor_copied_bytes"] += _tensor_bytes(value)
        if version != (epoch, _numerical_version(value)):
            from ..infrastructure.storage import StorageFailure
            raise StorageFailure("numerical component mutated during checkpoint capture")
        reference = store.put_tensor(isolated)
        if version != (epoch, _numerical_version(value)):
            from ..infrastructure.storage import StorageFailure
            raise StorageFailure("numerical component mutated during durable publication")
        self._references[key] = (version, reference)
        return reference


class InterleavedTrainer:
    """Step 32 schedule with completed-iteration persistence and joint checkpoint selection."""

    def __init__(
        self,
        *,
        alpha: nn.Module,
        weighting: nn.Module,
        gamma: PromptCollection,
        alpha_optimizer: torch.optim.Optimizer,
        weighting_optimizer: torch.optim.Optimizer,
        prompt_optimizer: PromptOptimizer,
        config: Config,
        answer_order: Sequence[str] = (),
        encoder_metadata: Mapping[str, Any] | None = None,
    ) -> None:
        if not isinstance(config, Config):
            raise TypeError("InterleavedTrainer requires the package Config")
        if {id(p) for p in alpha.parameters()} & {id(p) for p in weighting.parameters()}:
            raise ValueError("alpha and W must have disjoint trainable parameter sets")
        self.alpha, self.weighting, self.gamma = alpha, weighting, gamma
        self.alpha_optimizer, self.weighting_optimizer = (
            alpha_optimizer,
            weighting_optimizer,
        )
        self.prompt_optimizer = prompt_optimizer
        self.config = self.declared_config = config
        self.answer_order = tuple(answer_order)
        self.encoder_metadata = _validated_encoder_metadata(config, encoder_metadata)
        self.state = TrainerState()
        self.best_system: dict[str, Any] | None = None
        self.hooks: TrainingHooks | None = None
        self.checkpoint_path: str | Path | None = None
        self._request_boundary_pending = False
        self.durable_journal = None
        self._numerical_snapshots = _NumericalSnapshots()
        self._numerical_epochs = {"alpha": 0, "weighting": 0}
        self._initialized_components = (
            _module_fingerprint(alpha),
            _module_fingerprint(weighting),
            gamma.to_dict(),
        )

    def apply_configuration(self, config: Config) -> None:
        if replace(config, safety=self.config.safety, termination=self.config.termination) != self.config:
            raise ValueError("calibration may change only safeguards and termination thresholds")
        if self.state.pilot_complete and config.safety != self.config.safety:
            raise ValueError("training-only pilot safeguards are frozen")
        if self.state.calibration_complete and config.termination != self.config.termination:
            raise ValueError("post-bootstrap termination thresholds are frozen")
        self.config = config

    @startup_scope
    def resume_from_checkpoint(self, path: str | Path, *, hooks: TrainingHooks | None = None) -> dict[str, Any]:
        prepared_hooks: list[Any] = []
        prepare_hooks = getattr(hooks, "prepare_restore", None)
        apply_hooks = getattr(hooks, "apply_restore", None)
        def validate_hooks(value: Mapping[str, Any]) -> None:
            if callable(prepare_hooks) and callable(apply_hooks) and not prepared_hooks:
                prepared_hooks.append(prepare_hooks(value))
            elif hooks is not None:
                hooks.validate_state(value)
        def commit_hooks() -> None:
            verification = current_startup()
            if verification is not None:
                verification.assert_fresh()
            if prepared_hooks:
                apply_hooks(prepared_hooks[0])
        if hasattr(self, "extension_controller"):
            candidate = load_checkpoint(Path(path))
            self.extension_controller.validate_state(candidate.get("extension", {}))
        gamma, state, payload = CheckpointManager.load(
            path,
            alpha=self.alpha,
            weighting=self.weighting,
            alpha_optimizer=self.alpha_optimizer,
            weighting_optimizer=self.weighting_optimizer,
            config=self.declared_config,
            encoder_metadata=self.encoder_metadata,
            hook_validator=validate_hooks if hooks is not None else None,
            hook_commit=commit_hooks,
            prompt_backend_metadata=self.prompt_optimizer.metadata(),
        )
        if hasattr(self, "extension_controller"):
            self.extension_controller.load_state_dict(payload["extension"])
        self.gamma, self.state = gamma, state
        self._request_boundary_pending = False
        saved = Config.from_dict(payload["config"])
        self.config = replace(
            saved,
            training=replace(
                saved.training,
                max_outer_cycles=self.declared_config.training.max_outer_cycles,
            ),
        )
        self.best_system = copy.deepcopy(payload.get("best_system"))
        if state.prompt_cache:
            self.prompt_optimizer.load_state_dict(state.prompt_cache)
        if hooks is not None:
            hooks.bind(self)
            if not prepared_hooks:
                hooks.load_state_dict(payload["hook_state"])
            self.hooks = hooks
        return payload

    def save_checkpoint(self, path: str | Path) -> None:
        CheckpointManager.write_payload(path, self.checkpoint_payload())

    def checkpoint_payload(self, *, compact_hook: Mapping[str, Any] | None = None) -> dict[str, Any]:
        from ..infrastructure.incremental import TrackedDict, TrackedList, snapshot_collection
        from ..infrastructure.storage import COMPONENT_PROTOCOL

        store = self.durable_journal.evidence_store if compact_hook is not None else None
        components = None
        state_payload = None
        if store is None:
            self.state.prompt_cache = self.prompt_optimizer.state_dict()
        else:
            self.state.prompt_cache = self.prompt_optimizer.recorded_state()
            components = {}
            for name, model, optimizer in (("alpha", self.alpha, self.alpha_optimizer),
                                           ("weighting", self.weighting, self.weighting_optimizer)):
                # keep_vars preserves parameter identities/version counters;
                # detached copies are made only when this component changed.
                components[name] = self._numerical_snapshots.capture(
                    store, name, model.state_dict(keep_vars=True), epoch=self._numerical_epochs[name])
                components[name + "_optimizer"] = self._numerical_snapshots.capture(
                    store, name + "_optimizer", optimizer.state_dict(), epoch=self._numerical_epochs[name])
            state_payload = {}
            for item in fields(self.state):
                value = getattr(self.state, item.name)
                if item.name == "prompt_cache":
                    value = self.prompt_optimizer.checkpoint_state_dict(store)
                elif isinstance(value, (TrackedDict, TrackedList)):
                    value = snapshot_collection(store, value)
                elif item.name == "weighting_diagnostics":
                    value = asdict(value)
                state_payload[item.name] = value
        best_system = self.best_system
        if best_system is not None and store is not None:
            numerical = {"alpha", "weighting", "alpha_optimizer", "weighting_optimizer"}
            best_system = dict(best_system)
            for name in numerical:
                best_system[name] = self._numerical_snapshots.capture(
                    store, "best:" + name, self.best_system[name])
            for name in ("hook_state", "evaluation"):
                if not isinstance(self.best_system[name], TrackedDict):
                    self.best_system[name] = TrackedDict(self.best_system[name])
                best_system[name] = snapshot_collection(store, self.best_system[name])
        payload = CheckpointManager.capture(
            alpha=self.alpha,
            weighting=self.weighting,
            gamma=self.gamma,
            alpha_optimizer=self.alpha_optimizer,
            weighting_optimizer=self.weighting_optimizer,
            state=self.state,
            config=self.config,
            declared_config=self.declared_config,
            encoder_metadata=self.encoder_metadata,
            best_system=best_system,
            hook_state=(compact_hook if compact_hook is not None else
                        self.hooks.state_dict() if self.hooks is not None else None),
            prompt_backend_metadata=self.prompt_optimizer.metadata(),
            request_boundary_pending=self._request_boundary_pending,
            component_payload=components,
            state_payload=state_payload,
        )
        if hasattr(self, "extension_controller"):
            payload["extension"] = self.extension_controller.state_dict()
        if compact_hook is not None:
            payload["storage_protocol"] = COMPONENT_PROTOCOL
        return payload

    def _checkpoint_hook_state(self) -> Any:
        if self.hooks is None:
            return None
        method = getattr(self.hooks, "checkpoint_state_dict", None)
        if callable(method):
            return cast(Callable[[Any], Any], method)(self.durable_journal.evidence_store)
        if method is not None:
            raise TypeError("checkpoint_state_dict must be callable")
        return self.hooks.state_dict()

    def _prepare_checkpoint_payload(self) -> dict[str, Any]:
        """One timed writer capture before the separate commit-order check.

        Accounting submitted during capture is ordered ahead of the eventual
        checkpoint operation and invalidates its expected journal snapshot.
        """
        return self.checkpoint_payload(compact_hook=self._checkpoint_hook_state())

    async def checkpoint_async(self, *, safe_boundary: bool = True) -> None:
        """Publish a consistent boundary without blocking active stream cleanup."""
        from ..infrastructure.persistence import write_checkpoint_async

        self._request_boundary_pending = not safe_boundary
        if self.checkpoint_path is None:
            return
        try:
            if self.durable_journal is not None:
                await self.durable_journal.flush()
                payload, snapshot = await self.durable_journal.prepare_checkpoint_state(
                    self._prepare_checkpoint_payload, bind_publication=True)
            else:
                payload = self.checkpoint_payload()
            if self.durable_journal is not None:
                await self.durable_journal.checkpoint(payload, Path(self.checkpoint_path),
                    safe=safe_boundary, expected_snapshot=snapshot)
            else:
                await write_checkpoint_async(Path(self.checkpoint_path), payload)
        except BaseException:
            self._request_boundary_pending = True
            raise

    def _request_checkpoint(self) -> None:
        """Retain every expense while marking the enclosing workflow result as not yet durable."""

        self._request_boundary_pending = True
        if self.durable_journal is None:
            self._checkpoint(safe_boundary=False)

    def _checkpoint(self, *, safe_boundary: bool = True) -> None:
        self._request_boundary_pending = not safe_boundary
        if self.checkpoint_path is not None:
            if self.durable_journal is not None:
                self.durable_journal.flush_sync()
            try:
                if self.durable_journal is not None:
                    future = self.durable_journal.submit("prepare-checkpoint-state", {"factory":
                        self._prepare_checkpoint_payload, "bind_publication": True})
                    self.durable_journal.flush_sync()
                    payload, snapshot = future.result()
                    self.durable_journal.submit("checkpoint", {"payload": payload,
                        "checkpoint": str(self.checkpoint_path), "safe": safe_boundary,
                        "expected_snapshot": snapshot})
                    self.durable_journal.flush_sync()
                else:
                    self.save_checkpoint(self.checkpoint_path)
            except BaseException:
                self._request_boundary_pending = True
                raise

    def _advance(self, phase: str) -> None:
        self.state.phase = phase
        self.state.phase_progress = {}
        self._checkpoint()

    def allocate_workflow_attempt(self) -> int:
        attempt = self.state.workflow_attempt_counter
        self.state.workflow_attempt_counter += 1
        return attempt

    async def _trajectory(self, component: str) -> Any:
        attempt = self.allocate_workflow_attempt()
        identity = RoutingRolloutIdentity(self.config.seed, f"train:{component}:{attempt}", attempt)
        return await self.hooks.trajectory(component, "train", routing_identity=identity)

    def _condition(self, component: str) -> str:
        configuration = self.config.to_dict()
        configuration["training"].pop("max_outer_cycles")
        return _fingerprint(
            {
                "config": configuration,
                "alpha": (_module_fingerprint(self.alpha) if component != "routing" else None),
                "W": (_module_fingerprint(self.weighting) if component != "weighting" else None),
                "gamma": self.gamma.to_dict() if component != "prompt" else None,
                "experiment": (self.hooks._condition() if hasattr(self.hooks, "_condition")
                               else self.hooks.state_dict().get("condition")),
            }
        )

    async def _validate(self, component: str) -> EvaluationRecord:
        with evaluating(self.alpha, self.weighting):
            record = await self.hooks.validate(component)
        if not isinstance(record, EvaluationRecord):
            raise TypeError("validation must retain complete per-query records and original workflow costs")
        expected = "alphaval" if component == "routing" else "wval"
        if record.plan.domain != expected:
            raise ValueError("incorrect validation randomness domain")
        expected_objective = "selection" if component == "routing" else "negative_weighting_nll"
        if record.objective != expected_objective:
            raise ValueError("incorrect component validation objective")
        progress = self.state.phase_progress
        if progress.get("plan") not in {None, record.plan.fingerprint}:
            raise ValueError("validation minibatch or execution settings changed within a numerical phase")
        progress["plan"] = record.plan.fingerprint
        progress["history"].append(record.score)
        progress["records"].append(record.to_dict())
        progress["last_validated_update"] = progress["updates"]
        self.state.validation_history[component].append(record.score)
        return record

    def _record_numerical_loss(
        self,
        component: str,
        record: dict[str, Any],
        trajectory: Any,
    ) -> None:
        """Publish scalar diagnostics in the same snapshot as the Adam outcome."""
        example = getattr(trajectory, "example", None)
        execution_id = getattr(trajectory, "training_execution_id", None)
        if execution_id is not None:
            record["slot_original_execution_id"] = record["workflow_attempt_id"]
            record["workflow_attempt_id"] = execution_id
        record.update(
            query_id=getattr(example, "example_id", None),
            update_index_after=self.state.updates[component],
            phase_update_index_after=self.state.phase_progress["updates"],
        )
        _validate_training_loss_record(record)
        if any(
            previous["workflow_attempt_id"] == record["workflow_attempt_id"]
            for previous in self.state.training_loss_records
        ):
            raise ValueError("training loss workflow attempt already recorded")
        self.state.training_loss_records.append(copy.deepcopy(record))

    async def _numerical_phase(
        self, component: str, *, bootstrap: bool = False, phase_config: PhaseConfig | None = None,
    ) -> None:
        training = self.config.training
        phase = phase_config or getattr(training, f"{component}_bootstrap" if bootstrap else f"{component}_phase")
        if phase.max_updates == 0:
            return
        progress = self.state.phase_progress
        condition = self._condition(component)
        if not progress:
            progress.update(
                attempts=0,
                updates=0,
                history=[],
                records=[],
                condition=condition,
                last_validated_update=-1,
                failures=[],
                reason="budget",
                parameter_fingerprints_entry=parameter_fingerprints(self),
            )
        if progress["condition"] != condition:
            raise ValueError("fixed numerical phase condition changed")
        if progress["last_validated_update"] < 0:
            await self._validate(component)
            self._checkpoint()
        if progress["updates"] - progress["last_validated_update"] >= phase.validation_interval:
            await self._validate(component)
            self._checkpoint()
        attempts_limit = phase.max_updates if component == "routing" else phase.max_attempts
        while progress["updates"] < phase.max_updates and progress["attempts"] < attempts_limit:
            if validation_plateaued(progress["history"], phase.patience, phase.min_improvement):
                progress["reason"] = "validation_plateau"
                break
            progress["attempts"] += 1
            self.state.attempts[component] += 1
            self.alpha.train()
            self.weighting.train()
            loss = None
            trajectory = None
            fatal_error = None
            loss_record = {
                "protocol": "numerical_preupdate_loss_v1",
                "phase": self.state.phase,
                "component": component,
                "outer_cycle": self.state.outer_cycle,
                "workflow_attempt_id": self.state.workflow_attempt_counter,
                "phase_attempt": progress["attempts"],
                "component_attempt": self.state.attempts[component],
                "update_index_before": self.state.updates[component],
                "phase_update_index_before": progress["updates"],
                "loss": None,
                "example_count": None,
                "normalization_denominator": None,
                "status": "failed",
                "reason": "trajectory_unavailable",
            }
            try:
                with frozen(self.alpha if component == "weighting" else self.weighting):
                    trajectory = await self._trajectory(component)
                    if not trajectory.completed:
                        status = getattr(trajectory, "status", "execution-failed")
                        loss_record.update(status="failed", reason=status, error=getattr(trajectory, "error", None))
                        progress["failures"].append({"attempt": progress["attempts"], "status": status})
                        if component == "weighting":
                            self.state.weighting_diagnostics.record_failure(
                                trajectory.sessions, trajectory.example.gold_answer
                            )
                        if fatal_workflow(trajectory, getattr(self.hooks, "expenditure_ledger", None)):
                            fatal_error = WorkflowExecutionFailure(
                                getattr(trajectory, "error", None) or "development workflow halted: " + status,
                                budget_exhausted=status == "budget-exhausted",
                                operational_stop=True,
                            )
                    else:
                        try:
                            if component == "weighting":
                                loss = update_weighting(
                                    self.weighting,
                                    self.weighting_optimizer,
                                    trajectory.sessions,
                                    trajectory.example.gold_answer,
                                    self.answer_order,
                                    diagnostics=self.state.weighting_diagnostics,
                                    gradient_clip_norm=training.gradient_clip_norm,
                                    loss_record=loss_record,
                                )
                            elif (
                                trajectory.routing_eligible_for(
                                    training.routing_objective, p_mix=training.p_mix,
                                    lambda_conversation=self.config.costs.lambda_conversation,
                                )
                                if hasattr(trajectory, "routing_eligible_for")
                                else getattr(trajectory, "routing_eligible", True)
                            ):
                                loss = update_routing(
                                    self.alpha_optimizer,
                                    [item.routing for item in trajectory.sessions],
                                    [item.conversation_credits for item in trajectory.sessions],
                                    [item.future_return for item in trajectory.sessions] if training.p_mix > 0 else (),
                                    local_labels=[item.routing_local_labels for item in trajectory.sessions],
                                    zero_labels=[item.routing_zero_labels for item in trajectory.sessions],
                                    conversation_costs=[item.conversation_costs for item in trajectory.sessions],
                                    p_mix=training.p_mix,
                                    lambda_conversation=self.config.costs.lambda_conversation,
                                    gradient_clip_norm=training.gradient_clip_norm,
                                    loss_record=loss_record,
                                )
                            else:
                                count = _routing_example_count([item.routing for item in trajectory.sessions])
                                _capture_loss(loss_record, None, count, 2 * count)
                                loss_record.update(status="skipped", reason="required_routing_credit_unavailable")
                                progress["failures"].append(
                                    {"attempt": progress["attempts"], "status": "required_routing_credit_unavailable"}
                                )
                        except MissingRoutingTarget as error:
                            progress["failures"].append({"attempt": progress["attempts"], "status": str(error)})
                        except NumericalFailure as error:
                            progress["reason"] = "numerical_failure"
                            progress["failures"].append(
                                {
                                    "attempt": progress["attempts"],
                                    "status": "numerical_failure",
                                    "error": str(error),
                                }
                            )
            except BaseException as error:
                # Preserve partial diagnostics for the existing unsafe shutdown
                # publisher; this neither publishes nor declares a safe boundary.
                if isinstance(error, WorkflowRecoveryStopped):
                    trajectory = error.trajectory
                loss_record.update(status="failed", reason=type(error).__name__, error=str(error))
                self._record_numerical_loss(component, loss_record, trajectory)
                raise
            if loss is not None:
                self._numerical_epochs["weighting" if component == "weighting" else "alpha"] += 1
                progress["updates"] += 1
                self.state.updates[component] += 1
            self._record_numerical_loss(component, loss_record, trajectory)
            if fatal_error is not None:
                self._checkpoint()
                raise fatal_error
            if progress["reason"] == "numerical_failure":
                self._checkpoint()
                break
            if loss is not None:
                self._checkpoint()  # The Adam step is durable before its separately scheduled validation.
                if progress["updates"] - progress["last_validated_update"] >= phase.validation_interval:
                    await self._validate(component)
            self._checkpoint()
        if progress["reason"] != "numerical_failure" and progress["updates"] > progress["last_validated_update"]:
            await self._validate(component)
            self._checkpoint()
        progress["parameter_fingerprints_exit"] = parameter_fingerprints(self)
        self.state.phase_records.append(
            {
                "phase": self.state.phase,
                "outer_cycle": self.state.outer_cycle,
                **copy.deepcopy(progress),
            }
        )

    async def _prompt_phase(self) -> None:
        phase = self.config.training.prompt_phase
        if phase.max_iterations == 0:
            return
        progress = self.state.phase_progress
        condition = self._condition("prompt")
        if not progress:
            with (
                evaluating(self.alpha, self.weighting),
                frozen(self.alpha, self.weighting),
            ):
                plan = await self.hooks.prompt_validation_batch()
                try:
                    baseline = await self.prompt_optimizer.begin_phase(
                        self.gamma, plan, condition, self.hooks.evaluate_prompts
                    )
                except Exception as error:
                    if (
                        isinstance(error, (BudgetExhausted, AccountingFailure))
                        or bool(getattr(error, "budget_exhausted", False))
                        or bool(getattr(error, "operational_stop", False))
                    ):
                        raise WorkflowExecutionFailure(
                            "prompt baseline execution stopped",
                            budget_exhausted=isinstance(error, BudgetExhausted)
                            or bool(getattr(error, "budget_exhausted", False)),
                            operational_stop=True,
                        ) from error
                    self.state.phase_records.append(
                        {
                            "phase": "prompt",
                            "outer_cycle": self.state.outer_cycle,
                            "reason": f"baseline_failed: {type(error).__name__}: {error}",
                        }
                    )
                    return
            progress.update(iterations=0, stale=0, condition=condition, baseline=baseline.to_dict())
            self._checkpoint()
        if progress["condition"] != condition or self.prompt_optimizer.condition != condition:
            raise ValueError("stale prompt phase cache after a changed numerical or execution condition")
        keys = tuple(getattr(self, "prompt_components", self.gamma.components))
        if not keys or any(key not in self.gamma.components for key in keys):
            raise ValueError("prompt phase requires registered permitted components")
        while progress["iterations"] < phase.max_iterations and progress["stale"] < phase.patience:
            component = keys[self.state.prompt_cursor % len(keys)]
            iteration_id = f"cycle:{self.state.outer_cycle + 1}:iteration:{progress['iterations']}:{component}"
            with (
                evaluating(self.alpha, self.weighting),
                frozen(self.alpha, self.weighting),
            ):
                try:
                    evidence = await self.hooks.prompt_evidence(component)
                except WorkflowExecutionFailure as error:
                    baseline = EvaluationRecord.from_dict(progress["baseline"])
                    result = PromptStepResult(
                        self.gamma,
                        False,
                        float(baseline),
                        None,
                        "evidence_failed",
                        baseline,
                        proposal=PromptProposal(
                            None,
                            error=str(error),
                            metadata={
                                "budget_exhausted": error.budget_exhausted,
                                "operational_stop": error.operational_stop,
                            },
                        ),
                    )
                else:
                    try:
                        result = await self.prompt_optimizer.step(
                            self.gamma, component, evidence, condition=condition,
                            iteration_id=iteration_id, evaluate=self.hooks.evaluate_prompts,
                        )
                    except asyncio.CancelledError as error:
                        proposal = _prompt_failure(error)
                        self.state.prompt_validation_records.append({
                            "iteration_id": iteration_id, "component": component,
                            "accepted": False, "reason": "interrupted", "completed": False,
                            "baseline": copy.deepcopy(progress["baseline"]), "proposed": None,
                            "proposal": asdict(proposal),
                        })
                        raise

            self.gamma = result.collection
            progress["iterations"] += 1
            progress["stale"] = 0 if result.accepted else progress["stale"] + 1
            progress["baseline"] = (result.proposed if result.accepted else result.baseline).to_dict()
            self.state.prompt_cursor = (self.state.prompt_cursor + 1) % len(keys)
            self.state.attempts["prompt"] += 1
            self.state.updates["prompt"] += int(result.accepted)
            self.state.validation_history["prompt"].append(result.retained_score)
            self.state.prompt_validation_records.append(
                {
                    "iteration_id": iteration_id,
                    "component": component,
                    "accepted": result.accepted,
                    "reason": result.reason,
                    "baseline": result.baseline.to_dict(),
                    "proposed": (None if result.proposed is None else result.proposed.to_dict()),
                    "paired_improvements": result.paired_improvements,
                    "proposal": (None if result.proposal is None else asdict(result.proposal)),
                }
            )
            self._checkpoint()  # Exactly one durable boundary per completed iteration.
            if result.proposal is not None and result.proposal.metadata.get("budget_exhausted"):
                failure = WorkflowExecutionFailure("prompt iteration monetary budget exhausted", budget_exhausted=True)
                failure.failure_disposition = result.proposal.metadata.get("failure_disposition", "budget-exhausted")
                raise failure
            if result.proposal is not None and result.proposal.metadata.get("operational_stop"):
                failure = WorkflowExecutionFailure("prompt iteration execution stopped", operational_stop=True)
                failure.failure_disposition = result.proposal.metadata.get("failure_disposition", "operational-stop")
                raise failure
        self.state.phase_records.append(
            {
                "phase": "prompt",
                "outer_cycle": self.state.outer_cycle,
                **copy.deepcopy(progress),
            }
        )

    def _joint_state(self, record: EvaluationRecord) -> dict[str, Any]:
        import numpy as np

        if self.durable_journal is not None:
            future = self.durable_journal.submit("prepare-checkpoint-state", {"factory": self._checkpoint_hook_state})
            self.durable_journal.flush_sync()
            hook_state = future.result()
        else:
            hook_state = self.hooks.state_dict()

        return {
            "alpha": copy.deepcopy(self.alpha.state_dict()),
            "weighting": copy.deepcopy(self.weighting.state_dict()),
            "gamma": self.gamma.to_dict(),
            "config": self.config.to_dict(),
            "alpha_optimizer": copy.deepcopy(self.alpha_optimizer.state_dict()),
            "weighting_optimizer": copy.deepcopy(self.weighting_optimizer.state_dict()),
            "score": record.score,
            "cycle": self.state.outer_cycle,
            "evaluation": record.to_dict(),
            "hook_state": copy.deepcopy(hook_state),
            "workflow_attempt_counter": self.state.workflow_attempt_counter,
            "prompt_cursor": self.state.prompt_cursor,
            "python_random_state": random.getstate(),
            "numpy_random_state": np.random.get_state(),
            "torch_random_state": torch.random.get_rng_state(),
            "cuda_random_states": (torch.cuda.get_rng_state_all() if torch.cuda.is_available() else []),
        }

    def _restore_best(self) -> None:
        import numpy as np

        if self.best_system is not None:
            self.alpha.load_state_dict(self.best_system["alpha"])
            self.weighting.load_state_dict(self.best_system["weighting"])
            self.alpha_optimizer.load_state_dict(self.best_system["alpha_optimizer"])
            self.weighting_optimizer.load_state_dict(self.best_system["weighting_optimizer"])
            self._numerical_epochs["alpha"] += 1
            self._numerical_epochs["weighting"] += 1
            self.gamma = PromptCollection.from_dict(self.best_system["gamma"], registrations=self.config.scheme_registry)
            saved = Config.from_dict(self.best_system["config"])
            self.config = replace(saved, training=self.config.training)
            selected_hooks = self.best_system["hook_state"]
            if self.durable_journal is not None:
                selected_hooks = self.durable_journal.evidence_store.resolve(selected_hooks)
            self.hooks.restore_selected_state(selected_hooks)
            self.state.workflow_attempt_counter = max(
                self.state.workflow_attempt_counter,
                self.best_system["workflow_attempt_counter"],
            )
            random.setstate(self.best_system["python_random_state"])
            np.random.set_state(self.best_system["numpy_random_state"])
            torch.random.set_rng_state(self.best_system["torch_random_state"])
            if self.best_system["cuda_random_states"]:
                torch.cuda.set_rng_state_all(self.best_system["cuda_random_states"])
            self.state.prompt_cursor = self.best_system["prompt_cursor"]

    async def run_pilot(self, hooks: TrainingHooks, *, checkpoint_path: str | Path | None = None) -> TrainerState:
        """Execute only the declared engineering pilot and return a durable pre-bootstrap boundary."""

        self.hooks, self.checkpoint_path = hooks, checkpoint_path
        hooks.bind(self)
        if self.state.phase not in {"unstarted", "pilot", "pilot_complete"}:
            raise ValueError("pilot-only execution requires an initialized pre-bootstrap checkpoint")
        if any(self.state.updates.values()) or any(self.state.attempts.values()) or self.state.calibration_complete:
            raise ValueError("pilot cannot execute after any development update or attempt")
        if self.state.phase == "pilot_complete":
            return self.state
        before = (
            _module_fingerprint(self.alpha),
            _module_fingerprint(self.weighting),
            self.gamma.to_dict(),
        )
        if before != self._initialized_components or self.alpha_optimizer.state or self.weighting_optimizer.state:
            raise ValueError("pilot requires unchanged initialized numerical parameters, optimizers, and seed prompts")
        if self.state.phase == "unstarted":
            self._advance("pilot")
        try:
            with (
                evaluating(self.alpha, self.weighting),
                frozen(self.alpha, self.weighting),
            ):
                pilot_record = await hooks.pilot_safety()
                if hasattr(pilot_record, "execution_complete") and not pilot_record.execution_complete:
                    raise WorkflowExecutionFailure("pilot stopped before a complete safe attempt boundary")
        except asyncio.CancelledError as error:
            from ..infrastructure.failure_policy import cancellation_cause
            from ..infrastructure.shutdown import note_cleanup_failure

            self.state.execution_failures.append(
                {"phase": "pilot", "error": cancellation_cause(error), "interrupted": True}
            )
            self.state.phase = "interrupted"
            try:
                await self.checkpoint_async(safe_boundary=False)
            except Exception as publication_error:
                note_cleanup_failure(error, "trainer interrupted checkpoint", publication_error)
            raise
        except WorkflowExecutionFailure as error:
            self.state.execution_failures.append(
                {
                    "phase": "pilot",
                    "error": str(error),
                    "budget_exhausted": error.budget_exhausted,
                    "operational_stop": error.operational_stop,
                    "failure_disposition": getattr(error, "failure_disposition", None),
                }
            )
            self.state.phase = "failed"
            self._checkpoint()
            return self.state
        if before != (
            _module_fingerprint(self.alpha),
            _module_fingerprint(self.weighting),
            self.gamma.to_dict(),
        ):
            raise ValueError("engineering pilot changed numerical parameters or seed prompts")
        self.state.pilot_complete = True
        self._advance("pilot_complete")
        return self.state

    async def fit(
        self,
        hooks: TrainingHooks,
        *,
        checkpoint_path: str | Path | None = None,
        bootstrap_only: bool = False,
        weighting_supplement: bool = False,
        calibration_only: bool = False,
    ) -> TrainerState:
        """Run development, optionally stopping at the durable pre-calibration boundary."""
        try:
            if sum((bootstrap_only, weighting_supplement, calibration_only)) > 1:
                raise ValueError("bootstrap, supplementary, and calibration scopes are mutually exclusive")
            if weighting_supplement:
                return await self._fit_weighting_supplement(hooks, checkpoint_path=checkpoint_path)
            return await self._fit(
                hooks, checkpoint_path=checkpoint_path,
                bootstrap_only=bootstrap_only, calibration_only=calibration_only,
            )
        except WorkflowRecoveryStopped as error:
            self.state.execution_failures.append(
                {
                    "phase": self.state.phase,
                    "error": str(error),
                    "budget_exhausted": error.budget_exhausted,
                    "operational_stop": True,
                }
            )
            self.state.phase = "failed"
            try:
                await self.checkpoint_async(safe_boundary=False)
            except Exception as publication_error:
                from ..infrastructure.shutdown import note_cleanup_failure

                note_cleanup_failure(error, "workflow recovery stopped checkpoint", publication_error)
                raise error from publication_error
            return self.state
        except WorkflowExecutionFailure as error:
            self.state.execution_failures.append(
                {
                    "phase": self.state.phase,
                    "error": str(error),
                    "budget_exhausted": error.budget_exhausted,
                    "operational_stop": error.operational_stop,
                    "failure_disposition": getattr(error, "failure_disposition", None),
                }
            )
            self.state.phase = "failed"
            self._checkpoint()
            return self.state

    async def _fit_weighting_supplement(
        self, hooks: TrainingHooks, *, checkpoint_path: str | Path | None,
    ) -> TrainerState:
        """Prospective W continuation, then matching joint validation; never calibrate.

        Completed bootstrap phases remain immutable. The additional allowance is
        calculated once from their consumed slots/updates and persisted, so a
        restart cannot replenish it or revisit previously skipped trajectories.
        """
        state, training = self.state, self.config.training
        if (
            not state.pilot_complete or not all(state.bootstrap_complete.values())
            or state.calibration_complete or state.outer_cycle or state.system_validation_records
            or state.attempts["prompt"] or not self.config.workflow_recovery.enabled
            or state.phase not in {"calibration", "weighting_supplement", "routing_supplement_validation"}
        ):
            raise ValueError("weighting supplement requires a completed pre-calibration bootstrap")
        self.hooks, self.checkpoint_path = hooks, checkpoint_path
        hooks.bind(self)
        require_development = getattr(hooks, "require_development", None)
        if require_development is not None:
            require_development(weighting_supplement=True)
        supplement = state.weighting_supplement
        if not supplement:
            if state.phase != "calibration" or state.phase_progress:
                raise ValueError("supplement must start from the safe pre-calibration boundary")
            records = [record for record in state.phase_records if record["phase"] == "weighting_bootstrap"]
            if len(records) != 1:
                raise ValueError("supplement requires the original completed weighting phase")
            original = records[0]
            used_slots, used_updates = original["attempts"], original["updates"]
            if (used_slots, used_updates) != (state.attempts["weighting"], state.updates["weighting"]):
                raise ValueError("supplement counters disagree with original bootstrap evidence")
            slots = min(20, training.bootstrap_weighting_attempts - used_slots)
            updates = min(2, training.bootstrap_weighting_updates - used_updates)
            if slots < 0 or updates < 0 or (updates and not slots):
                raise ValueError("supplement exceeds revised cumulative bootstrap allowances")
            supplement.update(
                protocol="prospective_weighting_supplement_v1", complete=False,
                original_slots=used_slots, original_updates=used_updates,
                max_slots=slots, max_updates=updates,
                condition=self._condition("weighting"),
                parameter_fingerprints_entry=parameter_fingerprints(self),
                routing_attempts_entry=state.attempts["routing"],
                routing_updates_entry=state.updates["routing"],
                original_phase_count=len(state.phase_records),
                original_phase_fingerprint=_fingerprint(state.phase_records),
            )
            self._advance("weighting_supplement")
        if supplement.get("protocol") != "prospective_weighting_supplement_v1":
            raise ValueError("invalid weighting supplement protocol")
        if supplement["condition"] != self._condition("weighting"):
            raise ValueError("fixed supplementary weighting condition changed")
        before = supplement["parameter_fingerprints_entry"]
        current = parameter_fingerprints(self)
        if (
            any(before[key] != current[key] for key in ("alpha", "gamma"))
            or state.attempts["routing"] != supplement["routing_attempts_entry"]
            or state.updates["routing"] != supplement["routing_updates_entry"]
            or _fingerprint(state.phase_records[:supplement["original_phase_count"]])
            != supplement["original_phase_fingerprint"]
        ):
            raise ValueError("supplement changed frozen components or original phase evidence")
        if (
            supplement["max_slots"] != min(20, training.bootstrap_weighting_attempts - supplement["original_slots"])
            or supplement["max_updates"] != min(2, training.bootstrap_weighting_updates - supplement["original_updates"])
            or not 0 <= state.attempts["weighting"] - supplement["original_slots"] <= supplement["max_slots"]
            or not 0 <= state.updates["weighting"] - supplement["original_updates"] <= supplement["max_updates"]
        ):
            raise ValueError("supplement exceeded or changed its persisted cumulative allowance")
        if supplement.get("complete"):
            if state.phase != "calibration":
                raise ValueError("completed supplement is not at the pre-calibration boundary")
            return state
        if state.phase == "weighting_supplement":
            phase = replace(
                training.weighting_bootstrap,
                max_updates=supplement["max_updates"], max_attempts=max(1, supplement["max_slots"]),
            )
            await self._numerical_phase("weighting", phase_config=phase)
            if state.phase_progress.get("reason") == "numerical_failure":
                raise WorkflowExecutionFailure("supplement stopped on numerical failure")
            supplement["new_slots"] = state.attempts["weighting"] - supplement["original_slots"]
            supplement["new_updates"] = state.updates["weighting"] - supplement["original_updates"]
            supplement["weighting_changed"] = parameter_fingerprints(self)["weighting"] != before["weighting"]
            self._advance("routing_supplement_validation")
        if state.phase == "routing_supplement_validation":
            # This is a new matching joint evaluation, not a replacement of the
            # historical routing phase or another routing optimizer step.
            with evaluating(self.alpha, self.weighting), frozen(self.alpha, self.weighting):
                record = await hooks.validate_supplement_routing(
                    weighting_changed=supplement["weighting_changed"]
                )
            if (
                not isinstance(record, EvaluationRecord) or record.plan.domain != "alphaval"
                or record.objective != "selection"
            ):
                raise ValueError("supplement requires a complete planned routing-validation record")
            supplement["routing_validation"] = record.to_dict()
            supplement["parameter_fingerprints_exit"] = parameter_fingerprints(self)
            supplement["complete"] = True
            supplement["reason"] = "completed" if record.score is not None else "selection_cost_unavailable"
            self._advance("calibration")
        return state

    async def _fit(
        self,
        hooks: TrainingHooks,
        *,
        checkpoint_path: str | Path | None = None,
        bootstrap_only: bool = False,
        calibration_only: bool = False,
    ) -> TrainerState:
        if self.state.phase in {"weighting_supplement", "routing_supplement_validation"}:
            raise ValueError("unfinished supplementary phase requires its dedicated continuation scope")
        if bootstrap_only:
            expected_bootstrap = {
                "pilot_complete": (False, False),
                "weighting_bootstrap": (False, False),
                "routing_bootstrap": (True, False),
                "calibration": (True, True),
            }
            completed = (
                self.state.bootstrap_complete["weighting"],
                self.state.bootstrap_complete["routing"],
            )
            if (
                not self.state.pilot_complete
                or self.state.phase not in expected_bootstrap
                or completed != expected_bootstrap[self.state.phase]
                or self.state.calibration_complete
                or self.state.outer_cycle
                or self.state.system_validation_records
                or self.state.attempts["prompt"]
            ):
                raise ValueError("bootstrap-only execution requires a completed pilot before calibration")
        if calibration_only:
            state = self.state
            checkpoint_zero = (
                state.phase == "weighting" and state.calibration_complete
                and state.eligible_checkpoints == [0]
                and len(state.system_validation_records) == 1 and self.best_system is not None
            )
            if (
                not state.pilot_complete or not all(state.bootstrap_complete.values())
                or state.outer_cycle or state.attempts["prompt"] or state.phase_progress
                or (state.weighting_supplement and not state.weighting_supplement.get("complete"))
                or not (
                    (state.phase == "calibration" and not state.calibration_complete
                     and not state.system_validation_records and not state.eligible_checkpoints)
                    or (state.phase == "system_baseline" and state.calibration_complete
                        and not state.system_validation_records and not state.eligible_checkpoints)
                    or checkpoint_zero
                )
            ):
                raise ValueError("calibration-only execution requires the pre-calibration or checkpoint-zero boundary")
        self.hooks, self.checkpoint_path = hooks, checkpoint_path
        hooks.bind(self)
        training = self.config.training
        require_development = getattr(hooks, "require_development", None)
        if require_development is not None:
            if not self.state.pilot_complete:
                raise ValueError("run the dedicated pilot-only stage before development")
            if calibration_only:
                require_development(calibration_only=True)
            else:
                require_development(bootstrap_only=bootstrap_only)
            if self.state.phase == "pilot_complete":
                self._advance("weighting_bootstrap")
        elif bootstrap_only and self.state.phase == "pilot_complete":
            self._advance("weighting_bootstrap")
        if self.state.phase == "failed":
            raise ValueError("failed training stage requires a reviewed execution condition before resumption")
        if self.state.phase == "unstarted":
            self._advance("pilot")
        if self.state.phase == "complete":
            if self.state.outer_cycle >= training.max_outer_cycles or self.state.outer_stale >= training.outer_patience:
                return self.state
            self._advance("weighting")
        while self.state.phase != "complete":
            phase = self.state.phase
            if calibration_only and phase == "weighting":
                # Checkpoint zero is already published by _advance below. An
                # idempotent resumed invocation may return it, never enter cycle 1.
                return self.state
            if bootstrap_only and phase == "calibration":
                # _advance published routing's completed exit validation and
                # optimizer/sampler state before reaching this boundary. A
                # resumed bootstrap-only invocation returns without replay.
                return self.state
            if phase == "pilot":
                with (
                    evaluating(self.alpha, self.weighting),
                    frozen(self.alpha, self.weighting),
                ):
                    await hooks.pilot_safety()
                self.state.pilot_complete = True
                self._advance("weighting_bootstrap")
            elif phase == "weighting_bootstrap":
                await self._numerical_phase("weighting", bootstrap=True)
                self.state.bootstrap_complete["weighting"] = True
                self._advance("routing_bootstrap")
            elif phase == "routing_bootstrap":
                await self._numerical_phase("routing", bootstrap=True)
                self.state.bootstrap_complete["routing"] = True
                self._advance("calibration")
            elif phase == "calibration":
                with (
                    evaluating(self.alpha, self.weighting),
                    frozen(self.alpha, self.weighting),
                ):
                    await hooks.calibrate_termination()
                self.state.calibration_complete = True
                self._advance("system_baseline")
            elif phase == "weighting":
                if (
                    self.state.outer_cycle >= training.max_outer_cycles
                    or self.state.outer_stale >= training.outer_patience
                ):
                    self._restore_best()
                    self.prompt_optimizer.load_state_dict({"condition": None, "plan": None, "cache": {}})
                    self._advance("complete")
                    continue
                await self._numerical_phase("weighting")
                self._advance("routing")
            elif phase == "routing":
                await self._numerical_phase("routing")
                self._advance("prompt")
            elif phase == "prompt":
                await self._prompt_phase()
                self._advance("system")
            elif phase in {"system", "system_baseline"}:
                baseline = phase == "system_baseline"
                with evaluating(self.alpha, self.weighting):
                    record = await hooks.system_validation()
                if (
                    not isinstance(record, EvaluationRecord)
                    or record.plan.domain != "sysval"
                    or record.objective != "selection"
                ):
                    raise ValueError("system validation requires complete finite sysval selection trajectories")
                if record.score is None:
                    self.state.phase_records.append(
                        {"phase": phase, "reason": "selection_cost_unavailable", "evaluation": record.to_dict()}
                    )
                    raise WorkflowExecutionFailure("system validation cost is unavailable for the planned query batch")
                if not math.isfinite(record.score):
                    raise ValueError("system validation requires complete finite sysval selection trajectories")
                if self.state.system_validation_records:
                    original = EvaluationRecord.from_dict(self.state.system_validation_records[0])
                    if record.plan.fingerprint != original.plan.fingerprint:
                        raise ValueError("system validation plan changed across outer checkpoints")
                self.state.system_validation_records.append(record.to_dict())
                if not baseline:
                    self.state.outer_cycle += 1
                self.state.eligible_checkpoints.append(self.state.outer_cycle)
                previous_best = self.state.best_system_score
                self.state.validation_history["system"].append(record.score)
                if previous_best is None or record.score > previous_best:
                    self.state.best_system_score = record.score
                    self.best_system = self._joint_state(record)
                if not baseline:
                    self.state.outer_stale = (
                        0
                        if previous_best is None or record.score > previous_best + training.outer_min_improvement
                        else self.state.outer_stale + 1
                    )
                self._advance("weighting")
        return self.state
