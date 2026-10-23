"""Declared engineering pilots and complete shared-prefix termination selection."""

from __future__ import annotations

import copy
import hashlib
import json
import math
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field, fields, replace
from typing import Any

from ..config import Config, SafetyConfig, TerminationConfig
from .evaluation import EvaluationPlan, EvaluationRecord, SELECTION_OBJECTIVE_ID
from ..schemas import Scheme, Trajectory


TERMINATION_CALIBRATION_OBJECTIVE_ID = SELECTION_OBJECTIVE_ID


def compatibility_plan(config: Config, condition_fingerprint: str) -> dict[str, Any]:
    """A reviewable, finite synthetic request plan; constructing it performs no execution."""

    if len(condition_fingerprint) != 64:
        raise ValueError("compatibility planning requires the frozen experiment condition")
    diagnostic_requests = [
        {"agent_id": agent, "call_kind": kind, "probe_id": f"tuning-{agent}-{kind}", "evidence_role": "tuning"}
        for agent in config.agent_ids
        for kind in ("initial", "role")
    ]
    verification_requests = [
        {
            "agent_id": agent,
            "call_kind": "revision",
            "probe_id": f"verification-{agent}-{shape}",
            "evidence_role": "verification",
            "shape": shape,
            "target_input_fraction": fraction,
        }
        for agent in config.agent_ids
        for shape, fraction in (
            ("long_json_unicode", 0.65),
            ("near_limit_json_unicode", 0.92),
            ("repair_json_unicode", 0.85),
        )
    ]
    logical_calls = len(diagnostic_requests) + len(verification_requests) + 3
    plan = {
        "protocol": "bounded_synthetic_counter_verification_v2",
        "condition_fingerprint": condition_fingerprint,
        "input_source": "synthetic_only",
        "synthetic_example_id": "compatibility-synthetic-arithmetic",
        "synthetic_query": "Does two plus two equal four?",
        "synthetic_candidates": ["yes", "no"],
        "synthetic_gold_answer": "yes",
        "agent_requests": diagnostic_requests + verification_requests,
        "probe_construction": "complete_request_counter_bounded_json_unicode_repetition_v1",
        "counter_adjustments_during_execution": False,
        "tuning_evidence_may_establish_verification": False,
        "stop_rule": "one_declared_pass_or_first_failed_check_or_shared_attempt_or_money_limit",
        "textgrad_component": "role:S_T",
        "textgrad_proposals": 1,
        "textgrad_occurrences": 1,
        "textgrad_engine_calls_max": 3,
        "schema_repairs_per_request": 0,
        "transport_retries_per_generation": config.resources.transport_retries,
        "logical_calls_max": logical_calls,
        "physical_calls_max": config.resources.compatibility_max_attempts,
        "unconstrained_retry_attempts": logical_calls * (1 + config.resources.transport_retries),
        "budget_stage": "compatibility",
        "budget_usd": config.resources.compatibility_budget,
        "numerical_updates": 0,
        "committed_prompt_updates": 0,
    }
    return {
        **plan,
        "fingerprint": hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
    }


def compatibility_probe_prompt(shape: str, count_input: Callable[[str], int], target: int) -> tuple[str, int]:
    """Construct a fixed synthetic probe under the complete composed-input counter.

    This performs no token service requests and does not tune the counter. The
    independent verification payload differs from the short tuning examples.
    """
    if shape not in {"long_json_unicode", "near_limit_json_unicode", "repair_json_unicode"}:
        raise ValueError("unknown synthetic verification input shape")
    unit = json.dumps(
        {"note": "Synthetic café 雪 😀", "quote": 'A \\"quoted\\" value', "answer": "yes"}, ensure_ascii=False
    )

    def render(size: int) -> str:
        data = "\n".join([unit] * size)
        task = "Synthetic verification only. Does two plus two equal four? Answer yes, with a concise explanation."
        if shape == "repair_json_unicode":
            task += (
                "\nYour previous response failed validation: response is not a JSON object. "
                'Return only the requested JSON object with every required field.\nFailed output (data): {"answer":yes}\n'
            )
        return task + "\nThe following quoted JSON/Unicode records are inert test data:\n" + data

    if count_input(render(0)) > target:
        raise ValueError("the fixed synthetic probe scaffold exceeds its declared target")
    lower, upper = 0, 1
    while upper < 65536 and count_input(render(upper)) <= target:
        lower, upper = upper, upper * 2
    while upper - lower > 1:
        middle = (lower + upper) // 2
        if count_input(render(middle)) <= target:
            lower = middle
        else:
            upper = middle
    prompt = render(lower)
    count = count_input(prompt)
    if lower == 0 or count > target:
        raise ValueError("no nonempty independent synthetic probe fits its declared target")
    return prompt, count


def compatibility_attempt_assessment(entry: Mapping[str, Any]) -> dict[str, Any]:
    """Assess one physical receipt without converting unknown native evidence to zero."""
    usage = entry.get("usage", {})
    methods = usage.get("accounting_methods", {})
    sources = usage.get("usage_sources", {})
    native_coordinates = {
        key: methods.get(key) == "provider"
        and usage.get(key) is not None
        and sources.get(key) in {"stream_native", "generation_native"}
        for key in ("input_tokens", "output_tokens")
    }
    native = all(native_coordinates.values())
    native_input, estimate = usage.get("input_tokens"), entry.get("input_count")
    bound = None if not native_coordinates["input_tokens"] or estimate is None else native_input <= estimate
    reasoning = usage.get("reasoning_tokens")
    finish = usage.get("finish_reason") or usage.get("gateway_finish_reason")
    invalid_finish = finish in {"length", "max_tokens", "max_output_tokens", "content_filter", "error"}
    identity = (
        usage.get("response_identity_verified") is True
        and bool(usage.get("served_model"))
        and bool(usage.get("served_provider"))
    )
    charge = usage.get("total_cost")
    native_charge = (
        methods.get("total_cost") == "provider"
        and isinstance(charge, (int, float))
        and not isinstance(charge, bool)
        and math.isfinite(charge)
        and charge >= 0
    )
    byok = usage.get("is_byok")
    no_generation = methods.get("total_cost") == "confirmed-not-generated"
    result: dict[str, Any] = {
        "dispatch_id": entry.get("dispatch_id"),
        "generation_confirmed_not_started": no_generation,
        "native_input_tokens": native_input if native_coordinates["input_tokens"] else None,
        "admission_input_bound": estimate,
        "native_output_tokens": usage.get("output_tokens") if native_coordinates["output_tokens"] else None,
        "native_reasoning_tokens": reasoning,
        "actual_paid_usd": charge if native_charge else None,
        "observed_billing_mode": "byok" if byok is True else ("credits" if byok is False else "unknown"),
        "finish_reason": finish,
        "native_finish_reason": usage.get("native_finish_reason"),
        "served_model": usage.get("served_model"),
        "served_provider": usage.get("served_provider"),
        "checks": {
            "route_identity": "validated" if identity else "unavailable",
            "non_thinking": "unavailable" if reasoning is None else ("validated" if reasoning == 0 else "failed"),
            "token_counter": "unavailable" if bound is None else ("validated" if bound else "failed"),
            "native_usage": "validated" if native else "unavailable",
            "billing": "validated" if native_charge and isinstance(byok, bool) else "unavailable",
            "finish": "failed" if invalid_finish else ("validated" if finish else "unavailable"),
        },
    }
    if entry.get("status") not in {"completed"} and not no_generation:
        result["checks"]["native_usage"] = "failed"
    if no_generation:
        result["checks"] = {key: "not_generated" for key in result["checks"]}
    result["failed"] = any(value == "failed" for value in result["checks"].values())
    return result


def compatibility_report_assessment(
    requests: Sequence[Mapping[str, Any]], textgrad_receipts: Sequence[Mapping[str, Any]], agent_ids: Sequence[int]
) -> dict[str, Any]:
    """Separate diagnostics from independent counter verification and retain insufficiency."""
    dimensions = ("route_identity", "non_thinking", "token_counter", "native_usage", "billing")

    def combine(rows: Sequence[Mapping[str, Any]], dimension: str) -> dict[str, Any]:
        states = [row["checks"][dimension] for row in rows if not row.get("generation_confirmed_not_started")]
        status = (
            "failed"
            if "failed" in states
            else ("validated" if states and all(value == "validated" for value in states) else "unavailable")
        )
        return {"status": status, "evidence_dispatch_ids": [row["dispatch_id"] for row in rows]}

    backends = {}
    for agent_id in agent_ids:
        selected = [request for request in requests if request["probe"]["agent_id"] == agent_id]
        all_rows = [row for request in selected for row in request.get("assessment_receipts", ())]
        verification = [
            row
            for request in selected
            if request["probe"]["evidence_role"] == "verification"
            for row in request.get("assessment_receipts", ())
        ]
        backends[str(agent_id)] = {
            dimension: combine(verification if dimension == "token_counter" else all_rows, dimension)
            for dimension in dimensions
        }
        shapes = {
            request["probe"].get("shape") for request in selected if request["probe"]["evidence_role"] == "verification"
        }
        if shapes != {"long_json_unicode", "near_limit_json_unicode", "repair_json_unicode"}:
            backends[str(agent_id)]["token_counter"]["status"] = "unavailable"
    tg_dimensions = {dimension: combine(textgrad_receipts, dimension) for dimension in dimensions}
    tg_passed = sum(not row.get("generation_confirmed_not_started") for row in textgrad_receipts) == 3 and all(
        item["status"] == "validated" for item in tg_dimensions.values()
    )
    textgrad = {
        "status": (
            "validated"
            if tg_passed
            else ("failed" if any(item["status"] == "failed" for item in tg_dimensions.values()) else "unavailable")
        ),
        "evidence_dispatch_ids": [row["dispatch_id"] for row in textgrad_receipts],
        "roles": ["evaluation", "backward", "proposal"],
        "dimensions": tg_dimensions,
    }
    return {
        "backends": backends,
        "textgrad": textgrad,
        "passed": tg_passed
        and all(item["status"] == "validated" for backend in backends.values() for item in backend.values()),
        "scope": "observed_frozen_inputs_only_not_a_universal_token_bound_proof",
    }


def termination_presets(bootstrap: TerminationConfig) -> tuple[TerminationConfig, ...]:
    """The five registered presets plus the unchanged bootstrap configuration."""

    values = (
        (1, 1, 2, 0.80, 0.60, 0.05, 0.0),
        (1, 3, 2, 0.70, 0.40, 0.10, math.log(1.25)),
        (1, 3, 2, 0.80, 0.60, 0.05, 0.0),
        (2, 3, 3, 0.90, 0.80, 0.02, 0.0),
        (3, 3, 2, 0.80, 0.60, 0.05, 0.0),
    )
    return tuple(dict.fromkeys((bootstrap, *(TerminationConfig(*value) for value in values))))


@dataclass(frozen=True, slots=True)
class TerminationCalibrationPlan:
    validation: EvaluationPlan
    candidates: tuple[TerminationConfig, ...]
    bootstrap_configuration: TerminationConfig
    agent_count: int
    outgoing_capacity: tuple[int, ...]
    incoming_capacity: tuple[int, ...]
    routing_labels: tuple[str, ...]
    objective_id: str = TERMINATION_CALIBRATION_OBJECTIVE_ID
    prefix_protocol: str = "shared_boundary_prefix_v1"

    def __post_init__(self) -> None:
        if self.validation.domain != "sysval" or self.validation.partition != "sys-val":
            raise ValueError("termination calibration must use system-validation and sysval seeds")
        if not self.candidates or len(set(self.candidates)) != len(self.candidates):
            raise ValueError("termination search requires distinct predeclared configurations")
        if self.bootstrap_configuration not in self.candidates:
            raise ValueError("termination candidates must include the unchanged bootstrap configuration")
        if self.objective_id != SELECTION_OBJECTIVE_ID or self.prefix_protocol != "shared_boundary_prefix_v1":
            raise ValueError("unsupported termination selection or prefix protocol")
        if self.agent_count < 1 or any(
            len(capacities) != self.agent_count
            or any(
                isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= self.agent_count
                for value in capacities
            )
            for capacities in (self.outgoing_capacity, self.incoming_capacity)
        ):
            raise ValueError("calibration capacities must describe the complete agent pool")
        if not self.routing_labels or len(set(self.routing_labels)) != len(self.routing_labels):
            raise ValueError("calibration routing labels must be nonempty and unique")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> TerminationCalibrationPlan:
        return cls(
            EvaluationPlan.from_dict(value["validation"]),
            tuple(TerminationConfig(**item) for item in value["candidates"]),
            TerminationConfig(**value["bootstrap_configuration"]),
            value["agent_count"],
            tuple(value["outgoing_capacity"]),
            tuple(value["incoming_capacity"]),
            tuple(value["routing_labels"]),
            value["objective_id"],
            value["prefix_protocol"],
        )


@dataclass(frozen=True, slots=True)
class TerminationCandidateRecord:
    configuration: TerminationConfig
    evaluation: EvaluationRecord

    @property
    def mean_quality(self) -> float:
        return self.evaluation.accuracy

    @property
    def mean_normalized_cost(self) -> float:
        value = self.evaluation.mean_normalized_cost
        if value is None:
            raise ValueError("termination calibration collaboration cost is unavailable")
        return value

    @property
    def objective(self) -> float:
        return float(self.evaluation)


def _termination_key(configuration: TerminationConfig) -> tuple[Any, ...]:
    return tuple(getattr(configuration, item.name) for item in fields(configuration))


@dataclass(frozen=True, slots=True)
class TerminationCalibrationRecord:
    plan: TerminationCalibrationPlan
    candidates: tuple[TerminationCandidateRecord, ...]
    selected_index: int
    shared_executions: tuple[Mapping[str, Any], ...] = ()

    def __post_init__(self) -> None:
        if len(self.candidates) != len(self.plan.candidates):
            raise ValueError("calibration must retain every planned configuration--query pair")
        for configuration, record in zip(self.plan.candidates, self.candidates, strict=True):
            if (
                configuration != record.configuration
                or record.evaluation.plan.fingerprint != self.plan.validation.fingerprint
            ):
                raise ValueError("calibration candidate order or fixed validation plan changed")
            if record.evaluation.objective != "selection" or not math.isfinite(float(record.evaluation)):
                raise ValueError("calibration requires complete finite shared selection scores")
            if any(item.stopping_session > configuration.max_sessions for item in record.evaluation.examples):
                raise ValueError("candidate prefix extends beyond its declared horizon")
        selected = min(
            range(len(self.candidates)),
            key=lambda index: (
                -self.candidates[index].objective,
                self.candidates[index].mean_normalized_cost,
                _termination_key(self.candidates[index].configuration),
            ),
        )
        if self.selected_index != selected:
            raise ValueError("calibration selection violates score, cost, or lexicographic tie order")
        if (
            self.shared_executions
            and tuple(item["example_id"] for item in self.shared_executions) != self.plan.validation.ordered_example_ids
        ):
            raise ValueError("shared execution records must retain every calibration query in order")

    @property
    def selected_configuration(self) -> TerminationConfig:
        return self.candidates[self.selected_index].configuration

    @property
    def selected_evaluation(self) -> EvaluationRecord:
        return self.candidates[self.selected_index].evaluation

    def configuration_for_test(
        self,
        *,
        agent_count: int,
        outgoing_capacity: Sequence[int],
        incoming_capacity: Sequence[int],
        routing_labels: Sequence[str],
    ) -> TerminationConfig:
        if (
            agent_count != self.plan.agent_count
            or tuple(outgoing_capacity) != self.plan.outgoing_capacity
            or tuple(incoming_capacity) != self.plan.incoming_capacity
            or tuple(routing_labels) != self.plan.routing_labels
        ):
            raise ValueError("changed agent pool, capacities, or routing labels require termination recalibration")
        return self.selected_configuration

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan": asdict(self.plan),
            "candidates": [
                {
                    "configuration": asdict(item.configuration),
                    "evaluation": item.evaluation.to_dict(),
                }
                for item in self.candidates
            ],
            "selected_index": self.selected_index,
            "shared_executions": list(self.shared_executions),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> TerminationCalibrationRecord:
        return cls(
            TerminationCalibrationPlan.from_dict(value["plan"]),
            tuple(
                TerminationCandidateRecord(
                    TerminationConfig(**item["configuration"]),
                    EvaluationRecord.from_dict(item["evaluation"]),
                )
                for item in value["candidates"]
            ),
            value["selected_index"],
            tuple(value["shared_executions"]),
        )


async def calibrate_termination(
    plan: TerminationCalibrationPlan,
    evaluate_shared: Callable[
        [TerminationCalibrationPlan],
        Awaitable[tuple[Sequence[EvaluationRecord], Sequence[Mapping[str, Any]]]],
    ],
) -> TerminationCalibrationRecord:
    evaluations, executions = await evaluate_shared(plan)
    if len(evaluations) != len(plan.candidates):
        raise ValueError("shared calibration must evaluate every candidate exactly once")
    if any(record.score is None for record in evaluations):
        from .evaluation import WorkflowExecutionFailure

        raise WorkflowExecutionFailure(
            "termination calibration cost is unavailable for the complete planned query batch"
        )
    records = tuple(
        TerminationCandidateRecord(config, record) for config, record in zip(plan.candidates, evaluations, strict=True)
    )
    selected = min(
        range(len(records)),
        key=lambda index: (
            -records[index].objective,
            records[index].mean_normalized_cost,
            _termination_key(records[index].configuration),
        ),
    )
    return TerminationCalibrationRecord(plan, records, selected, tuple(executions))


@dataclass(frozen=True, slots=True)
class SafetyCalibrationPlan:
    ordered_example_ids: tuple[str, ...]
    seed: int
    partition: str = "train"
    horizon: int = 3
    profile_id: str = "declared_profile_explicit_stage_review_v1"

    def __post_init__(self) -> None:
        if self.partition != "train":
            raise ValueError("safety pilots may use only the training partition")
        if not self.ordered_example_ids or len(set(self.ordered_example_ids)) != len(self.ordered_example_ids):
            raise ValueError("pilot requires unique ordered original query IDs")
        if self.horizon != 3 or self.seed < 0 or self.profile_id != "declared_profile_explicit_stage_review_v1":
            raise ValueError("pilot requires a recorded seed and fixed three-session horizon")


@dataclass(frozen=True, slots=True)
class PilotObservation:
    example_id: str
    internal_rounds: tuple[int, ...]
    call_seconds: tuple[float, ...]
    session_seconds: tuple[float, ...]
    successful: bool
    safeguard_count: int = 0
    resource_limit_hits: tuple[str, ...] = ()
    call_types: tuple[str, ...] = ()
    schemes: tuple[str, ...] = ()
    call_records: tuple[Mapping[str, Any], ...] = ()
    usage_missing: bool = False
    sessions_completed: int = 0
    status: str = "completed"
    usage: Mapping[str, Any] = field(default_factory=dict)
    execution_counters: Mapping[str, Any] = field(default_factory=dict)
    fallback_records: tuple[Mapping[str, Any], ...] | None = None
    partial_session_traces: tuple[Mapping[str, Any], ...] = ()
    error: str | None = None
    operational_halt: Mapping[str, Any] | None = None
    completed_session_records: tuple[Mapping[str, Any], ...] = ()
    partial_history: tuple[Mapping[str, Any], ...] = ()
    accounting_complete: bool | None = None
    scientific_eligibility: Mapping[str, bool] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.example_id or any(value < 0 for value in self.internal_rounds) or self.safeguard_count < 0:
            raise ValueError("pilot identities and counters must be valid")
        if any(not math.isfinite(value) or value < 0 for value in (*self.call_seconds, *self.session_seconds)):
            raise ValueError("pilot timings must be finite and nonnegative")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> PilotObservation:
        converted = dict(value)
        for key in (
            "internal_rounds",
            "call_seconds",
            "session_seconds",
            "resource_limit_hits",
            "call_types",
            "schemes",
            "call_records",
            "partial_session_traces",
            "completed_session_records",
            "partial_history",
        ):
            converted[key] = tuple(converted.get(key, ()))
        if converted.get("fallback_records") is not None:
            converted["fallback_records"] = tuple(converted["fallback_records"])
        return cls(**converted)


@dataclass(frozen=True, slots=True)
class SafetyCalibrationRecord:
    plan: SafetyCalibrationPlan
    observations: tuple[PilotObservation, ...]
    configuration: SafetyConfig
    approved: bool
    coverage: Mapping[str, bool] = field(default_factory=dict)
    review: Mapping[str, Any] | None = None
    operational_halt: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if (
            tuple(item.example_id for item in self.observations)
            != self.plan.ordered_example_ids[: len(self.observations)]
        ):
            raise ValueError("safety record requires a prefix of the declared pilot queries in order")
        if self.approved and not self.execution_complete:
            raise ValueError("an incomplete or halted pilot cannot authorize development")
        if self.approved and self.review is None:
            raise ValueError("every pilot requires explicit development-stage approval before bootstrap")
        if self.review is not None:
            self._validate_review(self.review, self.review.get("condition_fingerprint", ""))
            if not self.approved:
                raise ValueError("reviewed profile must be recorded as accepted")

    @property
    def fingerprint(self) -> str:
        value = asdict(self)
        value.pop("review")
        value.pop("approved")
        return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    @property
    def acceptance_method(self) -> str:
        if self.review is not None:
            return "reviewed_declared_profile"
        return "pending_development_approval"

    @property
    def attempted_all_queries(self) -> bool:
        return len(self.observations) == len(self.plan.ordered_example_ids)

    @property
    def execution_complete(self) -> bool:
        return (
            self.attempted_all_queries
            and self.operational_halt is None
            and not any(item.operational_halt is not None for item in self.observations)
        )

    def _validate_review(self, review: Mapping[str, Any], condition_fingerprint: str) -> None:
        if (
            review.get("protocol") != "experiment_stage_authorization_v2"
            or review.get("stage") != "development"
            or review.get("decision") != "approve_stage"
            or review.get("pilot_fingerprint") != self.fingerprint
            or review.get("condition_fingerprint") != condition_fingerprint
            or not isinstance(condition_fingerprint, str)
            or len(condition_fingerprint) != 64
            or any(not isinstance(review.get(key), str) or not review[key].strip() for key in ("reviewer", "rationale"))
        ):
            raise ValueError("pilot approval must bind this exact report to the explicit development authorization")

    def accept_review(self, review: Mapping[str, Any], *, condition_fingerprint: str) -> SafetyCalibrationRecord:
        if not self.execution_complete:
            raise ValueError("an incomplete or halted pilot cannot authorize development")
        self._validate_review(review, condition_fingerprint)
        return replace(self, approved=True, review=dict(review))

    @property
    def requires_review(self) -> bool:
        return not self.execution_complete or any(
            item.resource_limit_hits or item.usage_missing or not item.successful for item in self.observations
        )

    @property
    def observed_safeguard_rate(self) -> float:
        return (
            sum(item.safeguard_count > 0 for item in self.observations) / len(self.observations)
            if self.observations
            else 0.0
        )

    def to_dict(self) -> dict[str, Any]:
        counters = aggregate_pilot_counters(self.observations)
        return {
            **asdict(self),
            "acceptance_method": self.acceptance_method,
            "report_protocol": "pilot_attempts_outcomes_and_fatal_boundaries_v2",
            "status": "complete" if self.execution_complete else "incomplete",
            "stage_complete": self.execution_complete,
            "planned_workflow_attempts": len(self.plan.ordered_example_ids),
            "attempted_workflows": len(self.observations),
            "attempted_all_queries": self.attempted_all_queries,
            "completed_workflows": sum(item.successful for item in self.observations),
            "completed_sessions": sum(item.sessions_completed for item in self.observations),
            "partial_sessions": sum(bool(item.partial_session_traces) for item in self.observations),
            "human_accepted": self.approved,
            "accounting_complete_workflows": (
                sum(item.accounting_complete is True for item in self.observations)
                if all(item.accounting_complete is not None for item in self.observations)
                else None
            ),
            "execution_counters": counters,
            "coverage_by_call_type": {
                kind: {
                    level: None if counts is None else counts[level] > 0
                    for level in ("requested", "admitted", "dispatched", "valid")
                }
                for kind, counts in counters["call_types"].items()
            },
            "coverage_limitations": "requested, admitted, dispatched and valid output are separate; absent legacy evidence is unavailable; semantic correctness is not certified",
            "paid_query_costs_usd": [item.usage.get("paid_monetary_cost") for item in self.observations],
            "paid_query_cost_method": "observed_end_to_end_paid_usage_including_initialization",
            "paid_query_cost_limitations": {
                "failed_or_censored_query_ids": [
                    item.example_id for item in self.observations if not item.successful or item.sessions_completed != 3
                ],
                "unresolved_paid_query_ids": [
                    item.example_id for item in self.observations if item.usage.get("paid_monetary_cost") is None
                ],
                "initialized_pilot_is_not_a_learned_workflow_cost_estimate": True,
            },
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> SafetyCalibrationRecord:
        plan = {
            **value["plan"],
            "ordered_example_ids": tuple(value["plan"]["ordered_example_ids"]),
        }
        return cls(
            SafetyCalibrationPlan(**plan),
            tuple(PilotObservation.from_dict(item) for item in value["observations"]),
            SafetyConfig(**value["configuration"]),
            value["approved"],
            value["coverage"],
            value.get("review"),
            value.get("operational_halt"),
        )


def aggregate_pilot_counters(observations: Sequence[PilotObservation]) -> dict[str, Any]:
    """Aggregate explicit records only; old reports cannot establish unrecorded zero outcomes."""
    observed = [item.execution_counters for item in observations]
    available = bool(observed) and all(item.get("protocol") == "explicit_execution_outcomes_v1" for item in observed)
    call_types: dict[str, Any] = {}
    for kind in ("initial", "role", "revision", "judge", "tie"):
        groups = [item.get("call_types", {}).get(kind) for item in observed]
        if not available or any(group is None for group in groups):
            call_types[kind] = None
            continue
        names = (
            "requested",
            "admitted",
            "rejected",
            "admission_unavailable",
            "dispatched",
            "valid",
            "validity_unavailable",
        )
        call_types[kind] = {name: sum(group[name] for group in groups) for name in names}
        call_types[kind]["physical_dispatch_ids"] = sorted(
            {identity for group in groups for identity in group["physical_dispatch_ids"]}
        )
    counters: dict[str, Any] = {
        "protocol": "pilot_explicit_outcomes_v2",
        "available": available,
        "call_types": call_types,
    }
    for name in ("repair_requests", "repair_dispatches"):
        counters[name] = sum(item[name] for item in observed) if available else None
    for name in ("transport_retry_dispatch_ids", "truncated_dispatch_ids"):
        counters[name] = sorted({identity for item in observed for identity in item[name]}) if available else None
    for name in ("scheme_conversations", "safeguards", "fallbacks"):
        counters[name] = (
            {
                key: sum(item[name].get(key, 0) for item in observed)
                for key in sorted({key for item in observed for key in item[name]})
            }
            if available
            else None
        )
    return counters


def collect_pilot_observation(trajectory: Trajectory) -> PilotObservation:
    completed_traces = [trace for session in trajectory.sessions for trace in session.traces]
    partial_traces = tuple(getattr(trajectory, "failed_session_traces", ()))
    completed_tags = {str(trace.tag) for trace in completed_traces}
    partial_traces = tuple(trace for trace in partial_traces if str(trace.tag) not in completed_tags)
    traces = completed_traces + list(partial_traces)
    safeguards = tuple(str(trace.safeguard_reason) for trace in traces if trace.safeguard_reason is not None)
    calls = tuple(trajectory.call_records)

    def metadata_outcomes(value: Any) -> list[str]:
        if isinstance(value, Mapping):
            outcomes = [str(value["error"])] if value.get("error") else []
            if value.get("finish_reason") in {
                "length",
                "max_tokens",
                "max_output_tokens",
            }:
                outcomes.append(f"output-token-limit: finish_reason={value['finish_reason']}")
            for key in ("attempts", "last", "physical_attempts", "raw_metadata"):
                outcomes.extend(metadata_outcomes(value.get(key)))
            return outcomes
        if isinstance(value, (tuple, list)):
            return [outcome for item in value for outcome in metadata_outcomes(item)]
        return []

    error_outcomes = tuple(
        outcome
        for item in calls
        for outcome in (
            *([str(item["error"])] if item.get("error") else []),
            *metadata_outcomes(item.get("usage")),
        )
    )
    resource_hits = tuple(
        reason
        for reason in (*safeguards, *error_outcomes)
        if any(
            word in reason.lower()
            for word in (
                "round",
                "timeout",
                "deadline",
                "budget",
                "resource",
                "oversized",
                "truncat",
                "context",
                "token-limit",
                "token-cap",
                "accounting",
                "usage subcategory",
                "charge exceeded",
            )
        )
    )
    missing = any(
        bool(item.get("usage_missing"))
        or not isinstance(item.get("usage"), Mapping)
        or any(
            item["usage"].get(key) is None
            for key in (
                "input_tokens",
                "output_tokens",
                "monetary_cost",
                "paid_monetary_cost",
            )
        )
        for item in calls
    ) or any(session.normalized_cost is None for session in trajectory.sessions)
    return PilotObservation(
        trajectory.example.example_id,
        tuple(
            max((message.round_index for message in trace.messages), default=0)
            for trace in traces
            if trace.tag.scheme is not Scheme.JUDGE
        ),
        tuple(trajectory.call_durations),
        tuple(session.elapsed_seconds for session in trajectory.sessions),
        trajectory.completed and trajectory.error is None,
        len(safeguards),
        resource_hits,
        tuple(
            sorted(
                {
                    str(
                        item.get("logical_call", {}).get(
                            "call_type",
                            item.get(
                                "call_kind",
                                item.get("call_type", item.get("kind", "unknown")),
                            ),
                        )
                    )
                    for item in calls
                }
            )
        ),
        tuple(sorted({trace.tag.scheme.value for trace in traces})),
        calls,
        missing,
        len(trajectory.sessions),
        getattr(
            trajectory,
            "status",
            "completed" if trajectory.completed else "execution-failed",
        ),
        asdict(trajectory.usage) if hasattr(trajectory, "usage") else {},
        copy.deepcopy(getattr(trajectory, "execution_counters", {})),
        copy.deepcopy(getattr(trajectory, "fallback_records", None)),
        tuple(asdict(trace) for trace in partial_traces),
        trajectory.error,
        copy.deepcopy(getattr(trajectory, "operational_halt", None)),
        tuple(
            {
                "session": session.session,
                "resolved_answers": list(session.resolved_answers),
                "agent_weights": list(session.agent_weights),
                "candidate_scores": dict(session.candidate_scores),
                "weighted_answer": session.weighted_answer,
                "normalized_cost": session.normalized_cost,
                "usage": asdict(session.usage),
                "termination": asdict(session.termination),
                "traces": [asdict(trace) for trace in session.traces],
                "history": [asdict(event) for event in session.history],
            }
            for session in trajectory.sessions
            if hasattr(session, "session")
        ),
        tuple(asdict(event) for event in getattr(trajectory, "failed_history", ())),
        getattr(trajectory, "accounting_complete", None),
        {name: bool(getattr(trajectory, f"{name}_eligible", False)) for name in ("routing", "weighting", "selection")},
    )


def calibrate_safety(
    plan: SafetyCalibrationPlan,
    observations: Sequence[PilotObservation],
    provisional: SafetyConfig,
    *,
    operational_halt: Mapping[str, Any] | None = None,
) -> SafetyCalibrationRecord:
    observations = tuple(observations)
    call_types = {name for item in observations for name in item.call_types}
    schemes = {name for item in observations for name in item.schemes}
    coverage = {name: name in call_types for name in ("initial", "role", "revision", "judge", "tie")}
    coverage.update({scheme.value: scheme.value in schemes for scheme in Scheme if scheme is not Scheme.ZERO})
    # Observations never supply human approval, even when every workflow is clean.
    return SafetyCalibrationRecord(plan, observations, provisional, False, coverage, operational_halt=operational_halt)
