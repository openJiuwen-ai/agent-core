"""Credits, losses, discrete gamma updates, and interleaved training."""

from __future__ import annotations

import copy
import asyncio
import hashlib
import json
import math
import os
import random
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Protocol

import torch
from torch import nn

from .config import Config
from .resources import BudgetExhausted, AccountingFailure
from .encoders import ENCODER_ARCHITECTURE_ID
from .policies import (
    ANSWER_STATE_ARCHITECTURE_ID,
    ROUTING_ARCHITECTURE_ID,
    WEIGHTING_ARCHITECTURE_ID,
    ROUTING_SEED_DERIVATION_ID,
    RoutingRolloutIdentity,
    _direct_candidate_mass_nll,
)
from .prompts import (
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
from .reproducibility import (
    configured_encoder_metadata,
    scientific_configuration,
    validate_scientific_compatibility,
)
from .schemas import (
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

        expected_success = local["informative_sessions"] > 0
        if successful != expected_success:
            raise ValueError(
                "a W update is successful exactly when its minibatch contains " "an informative completed session"
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
        informative = any(
            gold_answer in set(_session_answers(item)) and len(set(_session_answers(item))) > 1 for item in sessions
        )
        self.record_attempt(sessions, gold_answer, successful=informative)
        if informative:
            self.successful_w_updates -= 1
        else:
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
            raise ValueError("attempted W updates must equal successful plus skipped updates")


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


def _decision_fields(decision: Any) -> tuple[Any, Any, torch.Tensor, bool]:
    edge = getattr(decision, "edge", None)
    if edge is None:
        edge = (getattr(decision, "initiator"), getattr(decision, "receiver"))
    label = getattr(
        decision,
        "scheme",
        getattr(decision, "label", getattr(decision, "action", None)),
    )
    log_probability = getattr(decision, "log_probability")
    free = bool(getattr(decision, "free", not getattr(decision, "forced_zero", False)))
    return edge, label, log_probability, free


def standard_alpha_loss(actions: Sequence[Any], returns: Sequence[float]) -> torch.Tensor:
    """Specification Step 15: separate standard constrained REINFORCE baseline."""

    terms: list[torch.Tensor] = []
    for action, complete_return in zip(actions, returns, strict=True):
        for decision in action.decisions:
            _, _, log_probability, free = _decision_fields(decision)
            if free:
                terms.append(-float(complete_return) * log_probability)
    return torch.stack(terms).sum() if terms else torch.zeros((), requires_grad=True)


def shaped_alpha_loss(
    actions: Sequence[Any],
    credits: Sequence[Mapping[Any, float]],
    future_returns: Sequence[float],
) -> torch.Tensor:
    """Specification Step 18: nonzero D+F, free-zero F, forced-zero none."""

    terms: list[torch.Tensor] = []
    for action, local, future in zip(actions, credits, future_returns, strict=True):
        for decision in action.decisions:
            edge, label, log_probability, free = _decision_fields(decision)
            if not free:
                continue
            coefficient = float(future)
            if not _zero_label(label):
                tag = getattr(decision, "tag", None)
                if tag is None:
                    scheme = label if isinstance(label, Scheme) else Scheme(getattr(label, "value", label))
                    tag = ConversationTag(
                        int(getattr(decision, "session")),
                        int(edge[0]),
                        int(edge[1]),
                        scheme,
                    )
                if tag not in local and edge not in local:
                    raise ValueError("selected nonzero routing decision requires its conversation credit")
                coefficient += float(local[tag] if tag in local else local[edge])
            terms.append(-coefficient * log_probability)
    return torch.stack(terms).sum() if terms else torch.zeros((), requires_grad=True)


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
) -> float:
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


def update_weighting(
    model: nn.Module,
    optimizer: torch.optim.Optimizer,
    sessions: Sequence[Any],
    gold_answer: str,
    answer_order: Sequence[str] = (),
    *,
    diagnostics: WeightingDiagnostics | None = None,
    gradient_clip_norm: float | None = 1.0,
) -> float | None:
    loss, informative = weighting_loss_from_sessions(sessions, gold_answer, answer_order)
    if not informative or loss is None:
        if diagnostics is not None:
            diagnostics.record_attempt(sessions, gold_answer, successful=False)
        return None
    try:
        result = _numerical_step(optimizer, loss, gradient_clip_norm)
    except NumericalFailure:
        if diagnostics is not None:
            diagnostics.record_failure(sessions, gold_answer)
        raise
    if diagnostics is not None:
        diagnostics.record_attempt(sessions, gold_answer, successful=True)
    return result


def update_routing(
    optimizer: torch.optim.Optimizer,
    actions: Sequence[Any],
    credits: Sequence[Mapping[Any, float]],
    future_returns: Sequence[float],
    *,
    baseline: bool = False,
    complete_returns: Sequence[float] = (),
    gradient_clip_norm: float | None = 1.0,
) -> float | None:
    if not any(_decision_fields(decision)[3] for action in actions for decision in action.decisions):
        return None
    loss = (
        standard_alpha_loss(actions, complete_returns)
        if baseline
        else shaped_alpha_loss(actions, credits, future_returns)
    )
    return _numerical_step(optimizer, loss, gradient_clip_norm)


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


class PromptOptimizer:
    """A local TextGrad proposal followed by one complete, strictly paired comparison."""

    def __init__(self, proposer: PromptProposalBackend) -> None:
        self.proposer = proposer
        self.condition: str | None = None
        self.plan: EvaluationPlan | None = None
        self.cache: dict[str, EvaluationRecord] = {}

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
        self.condition, self.plan, self.cache = condition, plan, {}
        return await self._evaluate(collection, evaluate)

    def state_dict(self) -> dict[str, Any]:
        return {
            "condition": self.condition,
            "plan": None if self.plan is None else self.plan.to_dict(),
            "cache": {key: record.to_dict() for key, record in self.cache.items()},
        }

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
        self.validate_state(value)
        self.condition = value.get("condition")
        self.plan = EvaluationPlan.from_dict(value["plan"]) if value.get("plan") else None
        self.cache = {key: EvaluationRecord.from_dict(record) for key, record in value.get("cache", {}).items()}

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
        except Exception as error:  # Provider/editor failure cannot mutate the accepted collection.
            proposal = PromptProposal(
                None,
                error=f"{type(error).__name__}: {error}",
                metadata={
                    "operational_stop": isinstance(error, AccountingFailure)
                    or bool(getattr(error, "operational_stop", False)),
                    "budget_exhausted": isinstance(error, BudgetExhausted)
                    or bool(getattr(error, "budget_exhausted", False)),
                },
            )
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
        except Exception as error:
            failed_proposal = replace(
                proposal,
                error=f"validation failed: {type(error).__name__}: {error}",
                metadata={
                    **proposal.metadata,
                    "operational_stop": isinstance(error, AccountingFailure)
                    or bool(getattr(error, "operational_stop", False)),
                    "budget_exhausted": isinstance(error, BudgetExhausted)
                    or bool(getattr(error, "budget_exhausted", False)),
                },
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

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> TrainerState:
        data = copy.deepcopy(dict(value))
        data["weighting_diagnostics"] = WeightingDiagnostics(**data["weighting_diagnostics"])
        return cls(**data)


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

    schema_version = 11

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
        payload = {
            "schema_version": cls.schema_version,
            "request_boundary_pending": request_boundary_pending,
            "encoder_metadata": metadata,
            "encoder_fingerprint": _fingerprint(metadata),
            "scientific_configuration": condition.to_dict(),
            "architecture_identifiers": _architecture_identifiers(),
            "prompt_backend_metadata": dict(prompt_backend_metadata or {}),
            "alpha": alpha.state_dict(),
            "weighting": weighting.state_dict(),
            "gamma": gamma.to_dict(),
            "alpha_optimizer": alpha_optimizer.state_dict(),
            "weighting_optimizer": weighting_optimizer.state_dict(),
            "trainer_state": asdict(state),
            "config": config.to_dict(),
            "declared_config": declared.to_dict(),
            "best_system": copy.deepcopy(best_system),
            "hook_state": copy.deepcopy(hook_state),
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
        prompt_backend_metadata: Mapping[str, Any] | None = None,
    ) -> tuple[PromptCollection, TrainerState, dict[str, Any]]:
        import numpy as np

        payload = torch.load(Path(path), map_location="cpu", weights_only=False)
        if not isinstance(payload, dict) or payload.get("schema_version") != cls.schema_version:
            raise ValueError(f"unsupported checkpoint schema; expected {cls.schema_version}")
        if payload.get("request_boundary_pending") is not False:
            raise ValueError(
                "checkpoint has an unresolved physical request boundary; operator reconciliation is required before resume"
            )
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
        gamma = PromptCollection.from_dict(payload["gamma"])
        active_config = Config.from_dict(payload["config"])
        saved_declared = Config.from_dict(payload["declared_config"])
        saved_projection = scientific_configuration(
            saved_declared,
            encoder_metadata=metadata,
            architecture_identifiers=_architecture_identifiers(),
        )
        if saved_projection.to_dict() != payload["scientific_configuration"]:
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
            PromptCollection.from_dict(best["gamma"])
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

    def resume_from_checkpoint(self, path: str | Path, *, hooks: TrainingHooks | None = None) -> dict[str, Any]:
        gamma, state, payload = CheckpointManager.load(
            path,
            alpha=self.alpha,
            weighting=self.weighting,
            alpha_optimizer=self.alpha_optimizer,
            weighting_optimizer=self.weighting_optimizer,
            config=self.declared_config,
            encoder_metadata=self.encoder_metadata,
            hook_validator=hooks.validate_state if hooks is not None else None,
            prompt_backend_metadata=self.prompt_optimizer.metadata(),
        )
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
            hooks.load_state_dict(payload["hook_state"])
            self.hooks = hooks
        return payload

    def save_checkpoint(self, path: str | Path) -> None:
        CheckpointManager.write_payload(path, self.checkpoint_payload())

    def checkpoint_payload(self) -> dict[str, Any]:
        self.state.prompt_cache = self.prompt_optimizer.state_dict()
        return CheckpointManager.capture(
            alpha=self.alpha,
            weighting=self.weighting,
            gamma=self.gamma,
            alpha_optimizer=self.alpha_optimizer,
            weighting_optimizer=self.weighting_optimizer,
            state=self.state,
            config=self.config,
            declared_config=self.declared_config,
            encoder_metadata=self.encoder_metadata,
            best_system=self.best_system,
            hook_state=self.hooks.state_dict() if self.hooks is not None else None,
            prompt_backend_metadata=self.prompt_optimizer.metadata(),
            request_boundary_pending=self._request_boundary_pending,
        )

    async def checkpoint_async(self, *, safe_boundary: bool = True) -> None:
        """Publish a consistent boundary without blocking active stream cleanup."""
        from .persistence import write_checkpoint_async

        self._request_boundary_pending = not safe_boundary
        if self.checkpoint_path is None:
            return
        try:
            if self.durable_journal is not None:
                await self.durable_journal.flush()
            payload = self.checkpoint_payload()
            if self.durable_journal is not None:
                await self.durable_journal.checkpoint(payload, Path(self.checkpoint_path), safe=safe_boundary)
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
                self.save_checkpoint(self.checkpoint_path)
                if self.durable_journal is not None:
                    self.durable_journal.publish(
                        self.hooks.expenditure_ledger.state_dict(), Path(self.checkpoint_path), safe=safe_boundary
                    )
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
                "experiment": self.hooks.state_dict().get("condition"),
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

    async def _numerical_phase(self, component: str, *, bootstrap: bool = False) -> None:
        training = self.config.training
        phase = getattr(training, f"{component}_bootstrap" if bootstrap else f"{component}_phase")
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
            with frozen(self.alpha if component == "weighting" else self.weighting):
                trajectory = await self._trajectory(component)
                if not trajectory.completed:
                    status = getattr(trajectory, "status", "execution-failed")
                    progress["failures"].append({"attempt": progress["attempts"], "status": status})
                    if component == "weighting":
                        self.state.weighting_diagnostics.record_failure(
                            trajectory.sessions, trajectory.example.gold_answer
                        )
                    if fatal_workflow(trajectory, getattr(self.hooks, "expenditure_ledger", None)):
                        self._checkpoint()
                        raise WorkflowExecutionFailure(
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
                            )
                        elif (
                            trajectory.routing_eligible_for(training.routing_objective)
                            if hasattr(trajectory, "routing_eligible_for")
                            else getattr(trajectory, "routing_eligible", True)
                        ):
                            baseline = training.routing_objective == "standard_reinforce"
                            loss = update_routing(
                                self.alpha_optimizer,
                                [item.routing for item in trajectory.sessions],
                                [item.conversation_credits for item in trajectory.sessions],
                                () if baseline else trajectory.future_returns,
                                baseline=baseline,
                                complete_returns=trajectory.complete_returns if baseline else (),
                                gradient_clip_norm=training.gradient_clip_norm,
                            )
                        else:
                            progress["failures"].append(
                                {"attempt": progress["attempts"], "status": "required_routing_credit_unavailable"}
                            )
                    except NumericalFailure as error:
                        progress["reason"] = "numerical_failure"
                        progress["failures"].append(
                            {
                                "attempt": progress["attempts"],
                                "status": "numerical_failure",
                                "error": str(error),
                            }
                        )
                        self._checkpoint()
                        break
            if loss is not None:
                progress["updates"] += 1
                self.state.updates[component] += 1
                self._checkpoint()  # The Adam step is durable before its separately scheduled validation.
                if progress["updates"] - progress["last_validated_update"] >= phase.validation_interval:
                    await self._validate(component)
            self._checkpoint()
        if progress["reason"] != "numerical_failure" and progress["updates"] > progress["last_validated_update"]:
            await self._validate(component)
            self._checkpoint()
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
        keys = tuple(self.gamma.components)
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
                    result = await self.prompt_optimizer.step(
                        self.gamma,
                        component,
                        evidence,
                        condition=condition,
                        iteration_id=iteration_id,
                        evaluate=self.hooks.evaluate_prompts,
                    )
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
                raise WorkflowExecutionFailure("prompt iteration monetary budget exhausted", budget_exhausted=True)
            if result.proposal is not None and result.proposal.metadata.get("operational_stop"):
                raise WorkflowExecutionFailure("prompt iteration execution stopped", operational_stop=True)
        self.state.phase_records.append(
            {
                "phase": "prompt",
                "outer_cycle": self.state.outer_cycle,
                **copy.deepcopy(progress),
            }
        )

    def _joint_state(self, record: EvaluationRecord) -> dict[str, Any]:
        import numpy as np

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
            "hook_state": copy.deepcopy(self.hooks.state_dict()),
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
            self.gamma = PromptCollection.from_dict(self.best_system["gamma"])
            saved = Config.from_dict(self.best_system["config"])
            self.config = replace(saved, training=self.config.training)
            self.hooks.restore_selected_state(self.best_system["hook_state"])
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
            from .failure_policy import cancellation_cause
            from .shutdown import note_cleanup_failure

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

    async def fit(self, hooks: TrainingHooks, *, checkpoint_path: str | Path | None = None) -> TrainerState:
        try:
            return await self._fit(hooks, checkpoint_path=checkpoint_path)
        except WorkflowExecutionFailure as error:
            self.state.execution_failures.append(
                {
                    "phase": self.state.phase,
                    "error": str(error),
                    "budget_exhausted": error.budget_exhausted,
                }
            )
            self.state.phase = "failed"
            self._checkpoint()
            return self.state

    async def _fit(self, hooks: TrainingHooks, *, checkpoint_path: str | Path | None = None) -> TrainerState:
        self.hooks, self.checkpoint_path = hooks, checkpoint_path
        hooks.bind(self)
        training = self.config.training
        require_development = getattr(hooks, "require_development", None)
        if require_development is not None:
            if not self.state.pilot_complete:
                raise ValueError("run the dedicated pilot-only stage before development")
            require_development()
            if self.state.phase == "pilot_complete":
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
                    (
                        self.prompt_optimizer.condition,
                        self.prompt_optimizer.plan,
                        self.prompt_optimizer.cache,
                    ) = (
                        None,
                        None,
                        {},
                    )
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
