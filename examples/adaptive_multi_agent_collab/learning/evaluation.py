"""Complete single-trajectory validation and a concrete dataset/runtime bridge."""

from __future__ import annotations

from ..infrastructure.storage import load_checkpoint, read_json, read_bytes, file_sha256, current_startup, strict_json_equal, strict_json_fingerprint, FrozenEvidenceDict, FrozenEvidenceList

import copy
import asyncio
import hashlib
import json
import math
import statistics
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass, field, replace, fields, is_dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import torch

from ..datasets.data import DatasetPartitions
from ..infrastructure.randomness import GenerationContext, SEED_DERIVATION_ID, derive_seed
from ..schemas import QueryExample, RoutingMode, Trajectory
from ..workflows.recovery import WorkflowRecoveryStopped, run_workflow_slot, validate_recovery_records

if TYPE_CHECKING:
    from ..config import Config
    from ..workflows.prompts import PromptCollection, PromptOccurrence
    from ..workflows.runtime import Runtime


EVALUATION_PROTOCOL_ID = "complete_selection_trajectory_v1"
SELECTION_OBJECTIVE_ID = "final_accuracy_minus_reference_collaboration_cost_v1"
DOMAIN_PARTITION = {
    "promptval": "comp-val",
    "alphaval": "comp-val",
    "wval": "comp-val",
    "sysval": "sys-val",
}
TRAINING_GENERATION_POLICY = "component_query_global_attempt_logical_call_v1"
FATAL_WORKFLOW_STATUSES = frozenset(
    {"budget-exhausted", "accounting-failed", "binding-failed", "counting-failed", "campaign-halted", "policy-stopped"}
)


def ledger_stop_reason(ledger: Any) -> Any:
    if ledger is None:
        return None
    return getattr(ledger, "dispatch_stop_reason", getattr(ledger, "halted", None))


def fatal_workflow(trajectory: Any, ledger: Any = None) -> bool:
    return bool(
        getattr(trajectory, "status", None) in FATAL_WORKFLOW_STATUSES
        or getattr(trajectory, "operational_halt", None)
        or ledger_stop_reason(ledger)
    )


class WorkflowExecutionFailure(RuntimeError):
    """A recorded incomplete execution, distinct from unavailable conditional W evidence."""

    def __init__(self, message: str, *, budget_exhausted: bool = False, operational_stop: bool = False) -> None:
        super().__init__(message)
        self.budget_exhausted = budget_exhausted
        self.operational_stop = operational_stop


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"))


def _sum_available(values: Iterable[float | int | None]) -> float | None:
    materialized = tuple(values)
    return (
        None
        if any(value is None for value in materialized)
        else sum(value for value in materialized if value is not None)
    )


def scoring_configuration(config: Config) -> dict[str, Any]:
    """The single prescribed selection trade-off, independent of training credit."""

    value = config.selection.lambda_select
    if value is None:
        raise ValueError("lambda_select must be declared before validation or calibration")
    return {
        "scoring_id": SELECTION_OBJECTIVE_ID,
        "lambda_select": value,
        "cost_definition": {
            key: value
            for key, value in asdict(config.costs).items()
            if key not in {"lambda_session", "lambda_conversation"}
        },
    }


def _validate_scoring(configuration: Mapping[str, Any]) -> None:
    from ..config import CostConfig

    if set(configuration) != {"scoring_id", "lambda_select", "cost_definition"}:
        raise ValueError("evaluation requires the complete shared scoring configuration")
    value = configuration["lambda_select"]
    if configuration["scoring_id"] != SELECTION_OBJECTIVE_ID or (
        isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0
    ):
        raise ValueError("invalid selection scoring definition or lambda_select")
    costs = configuration["cost_definition"]
    expected = set(asdict(CostConfig())) - {"lambda_session", "lambda_conversation"}
    if set(costs) != expected:
        raise ValueError("selection must record every cost coefficient and normalization")
    CostConfig(**costs)


def selection_score(correct: bool, normalized_cost: float, configuration: Mapping[str, Any]) -> float:
    """One query's final correctness minus its full realized collaboration cost."""

    _validate_scoring(configuration)
    if not isinstance(correct, bool) or not math.isfinite(normalized_cost) or normalized_cost < 0:
        raise ValueError("selection requires a completed correctness outcome and available cost")
    return float(correct) - configuration["lambda_select"] * normalized_cost


@dataclass(frozen=True, slots=True)
class EvaluationPlan:
    """One complete trajectory per original query under a fixed recorded condition."""

    ordered_example_ids: tuple[str, ...]
    domain: str
    run_seed: int
    partition: str
    execution_configuration: Mapping[str, Any] = field(default_factory=dict)
    scoring_configuration: Mapping[str, Any] = field(default_factory=dict)
    protocol_id: str = EVALUATION_PROTOCOL_ID
    seed_derivation_id: str = SEED_DERIVATION_ID

    def __post_init__(self) -> None:
        if DOMAIN_PARTITION.get(self.domain) != self.partition:
            raise ValueError("evaluation domain must use its designated component/system validation partition")
        ids = tuple(self.ordered_example_ids)
        if not ids or any(not identifier for identifier in ids) or len(set(ids)) != len(ids):
            raise ValueError("validation requires unique nonempty ordered query IDs")
        if self.protocol_id != EVALUATION_PROTOCOL_ID or self.seed_derivation_id != SEED_DERIVATION_ID:
            raise ValueError("incompatible evaluation protocol or seed derivation")
        _validate_scoring(self.scoring_configuration)
        GenerationContext(self.run_seed, self.domain, ids[0])
        object.__setattr__(self, "ordered_example_ids", ids)
        object.__setattr__(
            self,
            "execution_configuration",
            json.loads(_canonical(self.execution_configuration)),
        )
        object.__setattr__(
            self,
            "scoring_configuration",
            json.loads(_canonical(self.scoring_configuration)),
        )

    @property
    def trajectories_per_example(self) -> int:
        return 1

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(_canonical(self.to_dict()).encode()).hexdigest()

    def generation_context(self, example_id: str) -> GenerationContext:
        if example_id not in self.ordered_example_ids:
            raise ValueError("generation context query is absent from the validation plan")
        return GenerationContext(self.run_seed, self.domain, example_id)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> EvaluationPlan:
        return cls(**dict(value))


@dataclass(frozen=True, slots=True)
class ExampleEvaluation:
    example_id: str
    prediction: str | None
    stopping_session: int
    normalized_cost: float | None
    correct: bool
    model_calls: int
    tokens: int | None
    complete_return: float | None = None  # Training-credit diagnostic only, never a selection score.
    informative_sessions: int = 0
    weighting_loss_sum: float = 0.0
    unanimous_sessions: int = 0
    gold_absent_sessions: int = 0
    generation_seeds: tuple[int, ...] = ()
    seed_replay_supported: bool = False
    status: str = "completed"
    error: str | None = None
    workflow_recovery: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Query-level caches are JSON evidence too; their sequence must restore
        # to the same typed value as complete EvaluationRecord restoration.
        object.__setattr__(self, "generation_seeds", tuple(self.generation_seeds))
        if (
            not self.example_id
            or (self.status == "completed" and (not self.prediction or self.stopping_session < 1))
            or self.stopping_session < 0
            or not isinstance(self.correct, bool)
        ):
            raise ValueError("evaluation needs a complete per-query prediction and stopping session")
        values = (self.weighting_loss_sum, *(() if self.normalized_cost is None else (self.normalized_cost,)))
        if not all(math.isfinite(value) and value >= 0 for value in values):
            raise ValueError("evaluation cost and loss must be finite and nonnegative")
        if self.complete_return is not None and not math.isfinite(self.complete_return):
            raise ValueError("training-return diagnostics must be finite when available")
        counts = (
            self.model_calls,
            self.informative_sessions,
            self.unanimous_sessions,
            self.gold_absent_sessions,
        )
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in counts):
            raise ValueError("evaluation usage and session counters cannot be negative")
        if self.tokens is not None and self.tokens < 0:
            raise ValueError("available token counts cannot be negative")
        if any(value > self.stopping_session for value in counts[1:]):
            raise ValueError("session categories cannot exceed completed sessions")
        if self.informative_sessions == 0 and self.weighting_loss_sum != 0:
            raise ValueError("uninformative queries cannot carry a weighting loss")


@dataclass(frozen=True, slots=True)
class EvaluationRecord:
    plan: EvaluationPlan
    examples: tuple[ExampleEvaluation, ...]
    objective: str = "selection"

    def __post_init__(self) -> None:
        if tuple(item.example_id for item in self.examples) != self.plan.ordered_example_ids:
            raise ValueError("comparison requires one complete outcome per planned query in declared order")
        if self.objective not in {"selection", "negative_weighting_nll"}:
            raise ValueError("unsupported component-validation objective; legacy mean-return scores cannot be compared")

    @property
    def score(self) -> float | None:
        if any(item.status != "completed" for item in self.examples):
            return None
        if self.objective == "negative_weighting_nll":
            eligible = [item for item in self.examples if item.informative_sessions]
            return (
                -sum(item.weighting_loss_sum / item.informative_sessions for item in eligible) / len(eligible)
                if eligible
                else None
            )
        values = self.per_example_scores
        return (
            None
            if any(value is None for value in values)
            else sum(value for value in values if value is not None) / len(self.examples)
        )

    @property
    def accuracy(self) -> float:
        return sum(item.correct for item in self.examples) / len(self.examples)

    @property
    def mean_normalized_cost(self) -> float | None:
        total = _sum_available(item.normalized_cost for item in self.examples)
        return None if total is None else total / len(self.examples)

    @property
    def eligible_query_fraction(self) -> float:
        return sum(item.informative_sessions > 0 for item in self.examples) / len(self.examples)

    @property
    def per_example_scores(self) -> tuple[float | None, ...]:
        return tuple(
            (
                None
                if item.normalized_cost is None or item.status != "completed"
                else selection_score(item.correct, item.normalized_cost, self.plan.scoring_configuration)
            )
            for item in self.examples
        )

    def __float__(self) -> float:
        if self.score is None:
            raise ValueError(
                "weighting validation has no eligible query"
                if self.objective == "negative_weighting_nll"
                else "validation objective is unavailable for the complete planned query batch"
            )
        return self.score

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["summary"] = {
            "score": self.score,
            "accuracy": self.accuracy,
            "mean_normalized_cost": self.mean_normalized_cost,
            "eligible_query_fraction": self.eligible_query_fraction,
        }
        return result

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> EvaluationRecord:
        result = cls(
            EvaluationPlan.from_dict(value["plan"]),
            tuple(
                ExampleEvaluation(
                    **{
                        **item,
                        "generation_seeds": tuple(item.get("generation_seeds", ())),
                    }
                )
                for item in value["examples"]
            ),
            value["objective"],
        )
        if value.get("summary") != result.to_dict()["summary"]:
            raise ValueError("evaluation summary differs from complete per-query outcomes")
        return result


@dataclass(frozen=True, slots=True)
class FinalTestPlan:
    """Predeclared full-test executions, independent of validation and selection."""

    ordered_example_ids: tuple[str, ...]
    run_seeds: tuple[int, ...]
    protocol_id: str = "frozen_checkpoint_full_test_v1"
    seed_derivation_id: str = SEED_DERIVATION_ID

    def __post_init__(self) -> None:
        ids, seeds = tuple(self.ordered_example_ids), tuple(self.run_seeds)
        if not ids or len(set(ids)) != len(ids) or any(not identifier for identifier in ids):
            raise ValueError("final test requires unique ordered original query IDs")
        if not seeds or len(set(seeds)) != len(seeds):
            raise ValueError("final test requires unique predeclared execution seeds")
        if self.protocol_id != "frozen_checkpoint_full_test_v1" or self.seed_derivation_id != SEED_DERIVATION_ID:
            raise ValueError("unsupported final-test execution protocol")
        for seed in seeds:
            GenerationContext(seed, "test", ids[0])
        object.__setattr__(self, "ordered_example_ids", ids)
        object.__setattr__(self, "run_seeds", seeds)


@dataclass(frozen=True, slots=True)
class TestPrediction:
    example_id: str
    run_seed: int
    answer: str | None
    correct: bool | None
    normalized_cost: float | None
    model_calls: int
    tokens: int | None
    generation_seeds: tuple[int, ...]
    seed_replay_supported: bool
    status: str = "completed"
    reference_collaboration_cost_usd: float | None = None
    paid_inference_cost_usd: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    error: str | None = None
    workflow_recovery: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class FinalTestRecord:
    plan: FinalTestPlan
    checkpoint_sha256: str
    protocol_fingerprint: str
    predictions: tuple[TestPrediction, ...]
    expenditure_by_stage: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        expected = tuple(
            (seed, identifier) for seed in self.plan.run_seeds for identifier in self.plan.ordered_example_ids
        )
        if tuple((item.run_seed, item.example_id) for item in self.predictions) != expected:
            raise ValueError("final test must retain every predeclared execution and query exactly once")
        for fingerprint in (self.checkpoint_sha256, self.protocol_fingerprint):
            if len(fingerprint) != 64 or any(character not in "0123456789abcdef" for character in fingerprint):
                raise ValueError("final test requires exact checkpoint and protocol SHA256 fingerprints")

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["stage_complete"] = not any(
            item.status in FATAL_WORKFLOW_STATUSES or item.status.startswith("unattempted-")
            for item in self.predictions
        )
        result["accuracy_by_run_seed"] = {
            str(seed): (
                sum(item.correct for item in self.predictions if item.run_seed == seed)
                / len(self.plan.ordered_example_ids)
                if all(item.correct is not None for item in self.predictions if item.run_seed == seed)
                else None
            )
            for seed in self.plan.run_seeds
        }
        accuracies = tuple(result["accuracy_by_run_seed"].values())
        complete_labels = all(value is not None for value in accuracies)
        result["accuracy_mean"] = statistics.mean(accuracies) if complete_labels else None
        result["accuracy_standard_deviation"] = (
            statistics.stdev(accuracies) if complete_labels and len(accuracies) > 1 else None
        )
        result["accuracy_standard_deviation_ddof"] = 1
        result["repeat_protocol"] = "inference_repeats_of_one_selected_joint_checkpoint"
        result["independent_question_count"] = len(self.plan.ordered_example_ids)
        result["prediction_count"] = len(self.predictions)
        result["runs"] = []
        for seed in self.plan.run_seeds:
            items = tuple(item for item in self.predictions if item.run_seed == seed)
            reference = _sum_available(item.reference_collaboration_cost_usd for item in items)
            result["runs"].append(
                {
                    "run_seed": seed,
                    "accuracy": result["accuracy_by_run_seed"][str(seed)],
                    "query_count": len(items),
                    "reference_collaboration_cost_usd": reference,
                    "mean_reference_collaboration_cost_usd": (None if reference is None else reference / len(items)),
                    "paid_inference_cost_usd_including_initialization": _sum_available(
                        item.paid_inference_cost_usd for item in items
                    ),
                    "model_calls": sum(item.model_calls for item in items),
                    "input_tokens": _sum_available(item.input_tokens for item in items),
                    "output_tokens": _sum_available(item.output_tokens for item in items),
                    "total_tokens": _sum_available(item.tokens for item in items),
                    "failures": {
                        status: sum(item.status == status for item in items)
                        for status in sorted({item.status for item in items if item.status != "completed"})
                    },
                }
            )
        return result


def _trajectory_evaluation(trajectory: Trajectory, *, seed_replay_supported: bool, allow_incomplete: bool = False) -> ExampleEvaluation:
    from .training import weighting_loss_from_sessions

    if not trajectory.completed or not trajectory.sessions or trajectory.example.gold_answer is None:
        if allow_incomplete and trajectory.example.gold_answer is not None:
            return ExampleEvaluation(
                trajectory.example.example_id, trajectory.final_answer, len(trajectory.sessions), None,
                trajectory.final_answer == trajectory.example.gold_answer,
                trajectory.usage.model_calls, trajectory.usage.total_tokens,
                seed_replay_supported=seed_replay_supported,
                status=trajectory.status, error=trajectory.error,
                workflow_recovery=copy.deepcopy(trajectory.workflow_recovery),
            )
        raise WorkflowExecutionFailure(
            "validation requires a complete labelled workflow trajectory",
            budget_exhausted=getattr(trajectory, "status", None) == "budget-exhausted",
            operational_stop=fatal_workflow(trajectory),
        )
    loss, count = weighting_loss_from_sessions(
        trajectory.sessions,
        trajectory.example.gold_answer,
        trajectory.example.candidates,
    )
    seeds = tuple(
        int(item["seed"])
        for item in trajectory.call_records
        if isinstance(item, Mapping) and item.get("seed") is not None
    )
    costs = [session.normalized_cost for session in trajectory.sessions]
    categories = [set(session.resolved_answers) for session in trajectory.sessions]
    return ExampleEvaluation(
        trajectory.example.example_id,
        trajectory.final_answer,
        len(trajectory.sessions),
        _sum_available(costs),
        trajectory.final_answer == trajectory.example.gold_answer,
        trajectory.usage.model_calls,
        trajectory.usage.total_tokens,
        trajectory.sessions[0].return_value,
        count,
        float(loss.detach()) * count if loss is not None else 0.0,
        sum(len(answers) == 1 for answers in categories),
        sum(trajectory.example.gold_answer not in answers for answers in categories),
        seeds,
        seed_replay_supported,
        workflow_recovery=copy.deepcopy(trajectory.workflow_recovery),
    )


async def evaluate_runtime(
    runtime: Runtime,
    dataset: DatasetPartitions,
    plan: EvaluationPlan,
    *,
    objective: str = "selection",
    execute_workflow: Callable[..., Any] | None = None,
    query_cache: dict[str, dict[str, Any]] | None = None,
    publish_query: Callable[[], Any] | None = None,
) -> EvaluationRecord:
    """Execute independent deterministic workflows; incomplete batches never produce a score."""

    examples = dataset.select(plan.partition, plan.ordered_example_ids)
    modules = (runtime.routing_policy, runtime.weighting_model)
    modes = tuple(module.training for module in modules)
    results: list[ExampleEvaluation] = []
    try:
        for module in modules:
            module.eval()
        with torch.no_grad():
            for example in examples:
                if query_cache is not None and example.example_id in query_cache:
                    results.append(ExampleEvaluation(**query_cache[example.example_id]))
                    continue
                call = runtime.run if execute_workflow is None else execute_workflow
                trajectory = await call(
                    example,
                    routing_mode=RoutingMode.EVALUATION,
                    generation_context=plan.generation_context(example.example_id),
                    partition=plan.partition,
                )
                dataset.require_origin(trajectory.example, plan.partition)
                if trajectory.example.example_id != example.example_id:
                    raise ValueError("runtime returned a different query during validation")
                results.append(
                    _trajectory_evaluation(
                        trajectory,
                        seed_replay_supported=all(
                            bool(getattr(agent, "supports_seed", False)) for agent in runtime.agents.values()
                        ),
                        allow_incomplete=execute_workflow is not None,
                    )
                )
                if query_cache is not None:
                    query_cache[example.example_id] = asdict(results[-1])
                    if publish_query is not None:
                        await publish_query()
    finally:
        for module, previous in zip(modules, modes, strict=True):
            module.train(previous)
    return EvaluationRecord(plan, tuple(results), objective)


class Experiment:
    """Concrete training hooks using only recorded partitions and actual Runtime execution."""

    _INCREMENTAL_FIELDS = frozenset({
        "evaluation_records", "evaluation_cache", "recovery_slots",
        "evaluation_query_cache", "calibration_query_cache", "pilot_observations",
        "prompt_evidence_records",
    })

    def __setattr__(self, name: str, value: Any) -> None:
        if name in self._INCREMENTAL_FIELDS:
            from ..infrastructure.incremental import track
            value = track(value)
        object.__setattr__(self, name, value)

    def __init__(
        self,
        dataset: DatasetPartitions,
        runtime_factory: Callable[[Config, PromptCollection], Runtime],
        *,
        plans: Mapping[str, EvaluationPlan],
        active_data_plan: Any,
        expenditure_ledger: Any = None,
        authorization_required: bool = True,
        prompt_train_batch_size: int = 2,
        pilot_plan: Any = None,
        termination_plan: Any = None,
        execution_metadata: Mapping[str, Any] | None = None,
        final_test_plan: FinalTestPlan | None = None,
    ) -> None:
        if set(plans) != {"weighting", "routing", "prompt", "system"}:
            raise ValueError("experiment requires weighting, routing, prompt, and system validation plans")
        expected = {
            "weighting": "wval",
            "routing": "alphaval",
            "prompt": "promptval",
            "system": "sysval",
        }
        for component, plan in plans.items():
            if plan.domain != expected[component]:
                raise ValueError(f"incorrect generation seed domain for {component}")
            dataset.select(plan.partition, plan.ordered_example_ids)
        if prompt_train_batch_size < 1:
            raise ValueError("prompt training minibatch size must be positive")
        active_data_plan.validate(dataset)
        if any(
            tuple(plans[key].ordered_example_ids) != tuple(active_data_plan.component_validation_ids)
            for key in ("weighting", "routing", "prompt")
        ) or tuple(plans["system"].ordered_example_ids) != tuple(active_data_plan.system_validation_ids):
            raise ValueError("validation plans must use the frozen active validation query IDs")
        self.expenditure_ledger = expenditure_ledger
        self.authorization_required = authorization_required
        # Process-local ownership of the campaign lock and durable journal cannot
        # be restored from a checkpoint or inferred from a saved stage approval.
        self.execution_session_guard: Callable[[], bool] | None = None
        self.stage_authorizations: dict[str, dict[str, Any]] = {}
        self.supplement_lineage: dict[str, Any] | None = None
        self.calibration_lineage: dict[str, Any] | None = None
        self.regular_lineage: dict[str, Any] | None = None
        self.compatibility_records: list[dict[str, Any]] = []
        self.prompt_evidence_records: list[dict[str, Any]] = []
        self.targeted_recheck: dict[str, Any] | None = None
        self.active_data_plan = active_data_plan
        self.dataset = dataset
        self.runtime_factory = runtime_factory
        self.plans = dict(plans)
        self.prompt_train_batch_size = prompt_train_batch_size
        self.pilot_plan = pilot_plan
        self.termination_plan = termination_plan
        self.execution_metadata = copy.deepcopy(dict(execution_metadata or {}))
        self.final_test_plan = final_test_plan
        if final_test_plan is not None and (
            final_test_plan.ordered_example_ids != tuple(example.example_id for example in dataset.test)
        ):
            raise ValueError("final-test plan must include the complete reserved test partition in recorded order")
        self.trainer: Any = None
        self.sample_counters = {"weighting": 0, "routing": 0, "prompt": 0}
        self.safety_record: Any = None
        self.extension_parent_acceptance: dict[str, Any] | None = None
        self._extension_parent_source_facts: dict[str, Any] | None = None
        self.pilot_observations: list[Any] = []
        self.termination_record: Any = None
        self.evaluation_records: list[dict[str, Any]] = []
        self.evaluation_cache: dict[str, EvaluationRecord] = {}
        self.recovery_slots: dict[str, dict[str, Any]] = {}
        self.evaluation_query_cache: dict[str, dict[str, dict[str, Any]]] = {}
        self.calibration_query_cache: dict[str, dict[str, dict[str, Any]]] = {}

    def bind(self, trainer: Any) -> None:
        self.trainer = trainer
        expected = scoring_configuration(trainer.config)
        if any(dict(plan.scoring_configuration) != expected for plan in self.plans.values()):
            raise ValueError("validation scoring definition differs from the frozen experiment configuration")

    @property
    def config(self) -> Config:
        if self.trainer is None:
            raise RuntimeError("bind the experiment to its trainer before executing workflows")
        return self.trainer.config

    def _runtime(self, collection: PromptCollection | None = None) -> Runtime:
        runtime = self.runtime_factory(self.config, collection if collection is not None else self.trainer.gamma)
        ledger = self.expenditure_ledger
        if ledger is None:
            return runtime
        from .calibration import collect_pilot_observation

        progress_calls = 0
        progress_history = 0
        progress_fallbacks = 0

        async def persist_progress(kind: str, partial: Trajectory) -> None:
            nonlocal progress_calls, progress_history, progress_fallbacks
            if kind == "workflow-started":
                progress_calls = progress_history = progress_fallbacks = 0
            observation = collect_pilot_observation(partial)
            payload: dict[str, Any] = {
                "example_id": partial.example.example_id,
                "status": observation.status,
                "completed_sessions": observation.sessions_completed,
                "workflow_complete": False,
                "call_records": list(observation.call_records[progress_calls:]),
                "fallback_records": list((observation.fallback_records or ())[progress_fallbacks:]),
            }
            if kind == "initialization-complete":
                payload["initial_outputs"] = [asdict(item) for item in partial.initial_outputs]
                payload["initial_answers"] = list(partial.initial_answers)
            elif kind == "completed-session":
                session_record = dict(observation.completed_session_records[-1])
                history = session_record.pop("history")
                payload["session_record"] = session_record
                payload["history_append"] = history[progress_history:]
                progress_history = len(history)
            elif kind != "workflow-started":
                payload["partial_session_traces"] = list(observation.partial_session_traces)
                payload["partial_history"] = list(observation.partial_history)[progress_history:]
                payload["error"] = observation.error
                payload["operational_halt"] = observation.operational_halt
            progress_calls = len(observation.call_records)
            progress_fallbacks = len(observation.fallback_records or ())
            await ledger.record_progress(kind, payload)

        runtime.progress_observer = persist_progress
        runtime.event_observer = ledger.record_progress
        return runtime

    def _training_example(self, component: str) -> QueryExample:
        import random

        count = self.sample_counters[component]
        active = self.dataset.select("train", self.active_data_plan.train_ids)
        epoch, offset = divmod(count, len(active))
        order = list(active)
        random.Random(derive_seed(self.config.seed, "data", component, epoch)).shuffle(order)
        return order[offset]

    def _training_generation_context(self, component: str, example: QueryExample, attempt_id: int) -> GenerationContext:
        return GenerationContext(
            self.config.seed,
            "train_generation",
            example.example_id,
            component,
            attempt_id,
        )

    def _recovery_condition(self, collection: PromptCollection | None = None) -> str:
        from .training import _module_fingerprint

        return hashlib.sha256(_canonical({
            "config": self.config.to_dict(),
            "alpha": _module_fingerprint(self.trainer.alpha),
            "weighting": _module_fingerprint(self.trainer.weighting),
            "gamma": (collection or self.trainer.gamma).to_dict(),
            "protocol": self.protocol_fingerprint,
        }).encode()).hexdigest()

    async def _publish_recovery(self, *, safe_boundary: bool = False, slot_id: str | None = None) -> None:
        # A request/whole-workflow intent is never a resumable training boundary.
        # Validation publishes its per-query selected projection before a safe
        # boundary, so completed queries need not be generated on resume.
        ledger = self.expenditure_ledger
        if not safe_boundary and slot_id is not None and getattr(ledger, "durable_journal", None) is not None:
            # Reuse the ordered, bounded WAL. A committed progress record clears
            # its previous safe boundary, so an old checkpoint cannot replay an
            # execution even when the crash precedes its first physical POST.
            self.trainer._request_checkpoint()
            await ledger.record_progress("workflow-recovery", {
                "slot": copy.deepcopy(self.recovery_slots[slot_id]),
                "sample_counters": dict(self.sample_counters),
                "workflow_attempt_counter": self.trainer.state.workflow_attempt_counter,
            })
            return
        checkpoint = getattr(self.trainer, "checkpoint_async", None)
        if checkpoint is not None:
            await checkpoint(safe_boundary=safe_boundary)
        else:
            self.trainer._checkpoint(safe_boundary=safe_boundary)

    async def _recover_workflow(
        self, *, slot_id: str, operation: str, execute: Callable[..., Any],
        allocate: Callable[[int], str], collection: PromptCollection | None = None,
        original: tuple[Any, str] | None = None,
        persist: Callable[[], Any] | None = None,
        original_observation: tuple[Any, Mapping[str, Any]] | None = None,
    ) -> tuple[Any, dict[str, Any]]:
        condition = self._recovery_condition(collection)

        def admitted() -> None:
            if ledger_stop_reason(self.expenditure_ledger):
                raise WorkflowExecutionFailure(str(ledger_stop_reason(self.expenditure_ledger)), operational_stop=True)
            if self._recovery_condition(collection) != condition:
                raise ValueError("workflow replacement changed its frozen numerical/prompt/resource condition")

        async def publish_slot() -> None:
            await self._publish_recovery(slot_id=slot_id)

        result, record = await run_workflow_slot(
            self.recovery_slots, slot_id=slot_id, condition=condition, operation=operation,
            execute=execute, allocate=allocate, persist=persist or publish_slot,
            assert_allowed=admitted, original=original, original_observation=original_observation,
        )
        if operation != "shared-prefix" and isinstance(result, Trajectory):
            result.workflow_recovery = record
        return result, record

    async def trajectory(self, component: str, split: str = "train", *, routing_identity: Any) -> Trajectory:
        self.require_development(calibration_only=False)
        if ledger_stop_reason(self.expenditure_ledger):
            raise WorkflowExecutionFailure(str(ledger_stop_reason(self.expenditure_ledger)), operational_stop=True)
        if split != "train" or component not in {"routing", "weighting"}:
            raise ValueError("numerical updates may request only the training partition")
        example = self._training_example(component)
        self.dataset.require_origin(example, "train")
        if not self.config.workflow_recovery.enabled:
            self.sample_counters[component] += 1  # Legacy scheduling is preserved.
        identity = replace(routing_identity, trajectory_id=example.example_id)
        if self.config.workflow_recovery.enabled:
            self.trainer._request_checkpoint()
        else:
            self.trainer._checkpoint()
        async def execute(index: int, execution_id: str) -> Trajectory:
            del index
            attempt_id = int(execution_id.split(":", 1)[1])
            trajectory = await self._runtime().run(
                example, routing_mode=RoutingMode.TRAIN,
                routing_identity=replace(identity, update_id=attempt_id), partition="train",
                generation_context=self._training_generation_context(component, example, attempt_id),
            )
            trajectory.training_execution_id = attempt_id
            return trajectory

        if not self.config.workflow_recovery.enabled:
            return await execute(0, f"train:{identity.update_id}")
        try:
            result, _ = await self._recover_workflow(
                slot_id=f"train:{component}:{identity.update_id}", operation=component, execute=execute,
                allocate=lambda index: f"train:{identity.update_id if index == 0 else self.trainer.allocate_workflow_attempt()}",
            )
        finally:
            if self.recovery_slots.get(f"train:{component}:{identity.update_id}", {}).get("status") == "complete":
                self.sample_counters[component] += 1
        return result

    def _cache_key(
        self,
        plan: EvaluationPlan,
        collection: PromptCollection | None = None,
        *,
        objective: str = "selection",
    ) -> str:
        from .training import _module_fingerprint

        configuration = self.config.to_dict()
        configuration["training"].pop("max_outer_cycles")
        condition = {
            "workflow_protocol": self._workflow_protocol(),
            "plan": plan.to_dict(),
            "objective": objective,
            "config": configuration,
            "alpha": _module_fingerprint(self.trainer.alpha),
            "weighting": _module_fingerprint(self.trainer.weighting),
            "gamma": (collection if collection is not None else self.trainer.gamma).to_dict(),
            "execution_metadata": self.execution_metadata,
        }
        return hashlib.sha256(_canonical(condition).encode()).hexdigest()

    async def _evaluate(
        self,
        plan: EvaluationPlan,
        *,
        collection: PromptCollection | None = None,
        objective: str = "selection",
    ) -> EvaluationRecord:
        self.require_development()
        from ..operations.preflight import BOOTSTRAP_SCOPE, SUPPLEMENT_SCOPE, CALIBRATION_SCOPE

        if (self.stage_authorizations.get("development", {}).get("scope") in {BOOTSTRAP_SCOPE, SUPPLEMENT_SCOPE}
                and plan.domain not in {"wval", "alphaval"}):
            raise ValueError("bootstrap-only authorization covers only numerical component validation")
        key = self._cache_key(plan, collection, objective=objective)
        if self.stage_authorizations.get("development", {}).get("scope") == CALIBRATION_SCOPE:
            if (self.trainer.state.phase != "system_baseline" or plan != self.plans["system"]
                    or objective != "selection" or collection is not None or key not in self.evaluation_cache
                    or self.termination_record is None
                    or self.evaluation_cache[key].to_dict() != self.termination_record.selected_evaluation.to_dict()):
                raise ValueError("calibration-only checkpoint zero must reuse its matching selected prefixes")
        if key not in self.evaluation_cache:
            if self.config.workflow_recovery.enabled:
                async def execute_workflow(example: QueryExample, **kwargs: Any) -> Trajectory:
                    slot_id = f"evaluation:{key}:{example.example_id}"
                    async def execute(index: int, identity: str) -> Trajectory:
                        del index, identity  # Identity must not alter registered evaluation seeds.
                        return await self._runtime(collection).run(example, **kwargs)
                    result, _ = await self._recover_workflow(
                        slot_id=slot_id, operation="weighting" if objective == "negative_weighting_nll" else "selection",
                        execute=execute, allocate=lambda index: f"{slot_id}:{index}", collection=collection,
                    )
                    return result
                async def publish_query() -> None:
                    await self._publish_recovery(safe_boundary=True)
                record = await evaluate_runtime(
                    self._runtime(collection), self.dataset, plan, objective=objective,
                    execute_workflow=execute_workflow,
                    query_cache=self.evaluation_query_cache.setdefault(key, {}), publish_query=publish_query,
                )
            else:
                record = await evaluate_runtime(self._runtime(collection), self.dataset, plan, objective=objective)
            self.evaluation_cache[key] = record
            self.evaluation_records.append(record.to_dict())
        return self.evaluation_cache[key]

    async def validate(self, component: str) -> EvaluationRecord:
        if component not in {"routing", "weighting"}:
            raise ValueError("unknown numerical validation component")
        return await self._evaluate(
            self.plans[component],
            objective=("negative_weighting_nll" if component == "weighting" else "selection"),
        )

    async def validate_supplement_routing(self, *, weighting_changed: bool) -> EvaluationRecord:
        """Evaluate one final tuple, or explicitly finish the linked old batch."""
        if weighting_changed:
            return await self.validate("routing")
        self.require_development()
        from .training import _module_fingerprint
        from ..workflows.recovery import RecoveryDecision
        from ..infrastructure.failure_policy import PROVIDER_FAILURES, FailureKind

        source = self.execution_metadata.get("supplement_routing_source")
        if not isinstance(source, Mapping):
            raise ValueError("unchanged-weighting validation reuse requires the audited source batch")
        original = EvaluationRecord.from_dict(source["evaluation"])
        plan = self.plans["routing"]
        previous_plan, current_plan = original.plan.to_dict(), plan.to_dict()
        # Recovery activation is the explicit amendment; all scientific pairing
        # coordinates and numerical/prompt values must still match exactly.
        for value in (previous_plan, current_plan):
            value["execution_configuration"].pop("workflow_recovery", None)
        registration = self.execution_metadata.get("registered_supplement", {})
        previous_spec = previous_plan["execution_configuration"].get("specification_sha256")
        current_spec = current_plan["execution_configuration"].get("specification_sha256")
        if previous_spec != current_spec:
            if (previous_spec != registration.get("parent", {}).get("specification_sha256")
                    or current_spec != registration.get("target_specification_sha256")):
                raise ValueError("linked validation amendment lacks its exact parent/target scientific binding")
            previous_plan["execution_configuration"].pop("specification_sha256")
            current_plan["execution_configuration"].pop("specification_sha256")
        if (
            previous_plan != current_plan
            or original.objective != "selection"
            or source.get("alpha_fingerprint") != _module_fingerprint(self.trainer.alpha)
            or source.get("weighting_fingerprint") != _module_fingerprint(self.trainer.weighting)
            or source.get("gamma") != self.trainer.gamma.to_dict()
            or not source.get("source_checkpoint_sha256")
        ):
            raise ValueError("linked routing validation changed its frozen source condition")
        unavailable = [item for item in original.examples if item.normalized_cost is None or item.status != "completed"]
        query_id = source.get("replacement_query_id", "1d33f1d899f3cbc6ba6f")
        if len(unavailable) != 1 or unavailable[0].example_id != query_id:
            raise ValueError("supplement permits only its registered incomplete original query")
        evidence = source.get("failure_evidence", {})
        allowed = {item.value for item in PROVIDER_FAILURES} | {FailureKind.MISSING.value}
        if evidence.get("dispatch_id") != 883 or evidence.get("usage", {}).get("failure_kind") not in allowed:
            raise ValueError("linked original query is missing its exact eligible receipt evidence")
        key = self._cache_key(plan)
        if key in self.evaluation_cache:
            return self.evaluation_cache[key]
        selected = self.evaluation_query_cache.setdefault(key, {})
        prior = unavailable[0]
        if query_id not in selected:
            example = self.dataset.select(plan.partition, (query_id,))[0]
            slot_id = f"evaluation:{key}:{query_id}"
            async def execute(index: int, identity: str) -> Trajectory:
                del index, identity
                return await self._runtime().run(
                    example, routing_mode=RoutingMode.EVALUATION,
                    generation_context=plan.generation_context(query_id), partition=plan.partition,
                )
            result, recovery = await self._recover_workflow(
                slot_id=slot_id, operation="selection", execute=execute,
                allocate=lambda index: f"{slot_id}:{index}",
                original=(prior, f"historical:{source['source_checkpoint_sha256']}:{query_id}"),
                original_observation=(
                    RecoveryDecision(False, True, "eligible-unavailable-information"),
                    {"projection": asdict(prior), "source_checkpoint_sha256": source["source_checkpoint_sha256"],
                     "failure_evidence": copy.deepcopy(evidence), "source_kind": "recorded-evaluation-projection"},
                ),
            )
            if isinstance(result, Trajectory):
                value = _trajectory_evaluation(
                    result, seed_replay_supported=all(bool(getattr(agent, "supports_seed", False))
                                                     for agent in self._runtime().agents.values()),
                    allow_incomplete=True,
                )
            else:
                value = replace(prior, workflow_recovery=recovery)
            selected[query_id] = asdict(value)
            await self._publish_recovery(safe_boundary=True)
        records = tuple(ExampleEvaluation(**selected[item.example_id]) if item.example_id == query_id else item
                        for item in original.examples)
        record = EvaluationRecord(plan, records, "selection")
        self.evaluation_cache[key] = record
        self.evaluation_records.append({**record.to_dict(), "recovery_source": {
            "source_checkpoint_sha256": source["source_checkpoint_sha256"],
            "original_record_sha256": hashlib.sha256(_canonical(source["evaluation"]).encode()).hexdigest(),
            "reused_query_ids": [item.example_id for item in original.examples if item.example_id != query_id],
        }})
        return record

    async def prompt_evidence(self, component: str) -> tuple[PromptOccurrence, ...]:
        self.require_development(bootstrap_only=False, weighting_supplement=False, calibration_only=False)
        result: list[PromptOccurrence] = []
        for _ in range(self.prompt_train_batch_size):
            if ledger_stop_reason(self.expenditure_ledger):
                raise WorkflowExecutionFailure(str(ledger_stop_reason(self.expenditure_ledger)), operational_stop=True)
            example = self._training_example("prompt")
            self.dataset.require_origin(example, "train")
            if not self.config.workflow_recovery.enabled:
                self.sample_counters["prompt"] += 1
            attempt_id = self.trainer.allocate_workflow_attempt()
            if self.config.workflow_recovery.enabled:
                self.trainer._request_checkpoint()
            else:
                self.trainer._checkpoint(safe_boundary=False)
            async def execute(index: int, execution_id: str) -> Trajectory:
                del index
                identifier = int(execution_id.split(":", 1)[1])
                trajectory = await self._runtime().run(
                    example, routing_mode=RoutingMode.EVALUATION, partition="train",
                    generation_context=self._training_generation_context("prompt", example, identifier),
                )
                trajectory.training_execution_id = identifier
                return trajectory
            if self.config.workflow_recovery.enabled:
                try:
                    trajectory, _ = await self._recover_workflow(
                        slot_id=f"prompt:{component}:{attempt_id}", operation="prompt", execute=execute,
                        allocate=lambda index: f"train:{attempt_id if index == 0 else self.trainer.allocate_workflow_attempt()}",
                    )
                finally:
                    if self.recovery_slots.get(f"prompt:{component}:{attempt_id}", {}).get("status") == "complete":
                        self.sample_counters["prompt"] += 1
            else:
                trajectory = await execute(0, f"train:{attempt_id}")
            self.prompt_evidence_records.append(
                {
                    "example_id": example.example_id,
                    "attempt_id": attempt_id,
                    "selected_execution_id": trajectory.training_execution_id,
                    "workflow_recovery": copy.deepcopy(getattr(trajectory, "workflow_recovery", {})),
                    "component": component,
                    "completed": trajectory.completed,
                    "status": trajectory.status,
                    "error": trajectory.error,
                    "accounting_complete": trajectory.accounting_complete,
                    "call_records": copy.deepcopy(trajectory.call_records),
                    "retained_occurrences": (
                        sum(
                            item.component == component and not item.trace.get("provider_only_failure", False)
                            for item in trajectory.prompt_occurrences
                        )
                        if trajectory.completed
                        else 0
                    ),
                }
            )
            if not trajectory.completed:
                if fatal_workflow(trajectory, self.expenditure_ledger):
                    raise WorkflowExecutionFailure(
                        trajectory.error or "prompt-evidence workflow stopped",
                        budget_exhausted=getattr(trajectory, "status", None) == "budget-exhausted",
                        operational_stop=True,
                    )
            else:
                result.extend(item for item in trajectory.prompt_occurrences
                              if item.component == component and not item.trace.get("provider_only_failure", False))
        return tuple(result)

    async def prompt_validation_batch(self) -> EvaluationPlan:
        return self.plans["prompt"]

    async def evaluate_prompts(self, collection: PromptCollection, batch: EvaluationPlan) -> EvaluationRecord:
        if batch.fingerprint != self.plans["prompt"].fingerprint:
            raise ValueError("prompt comparison changed the recorded phase-validation plan")
        return await self._evaluate(batch, collection=collection)

    async def system_validation(self) -> EvaluationRecord:
        return await self._evaluate(self.plans["system"])

    def _condition(self) -> dict[str, Any]:
        return {
            **({"workflow_recovery": asdict(self.config.workflow_recovery)}
               if self.trainer is not None and self.config.workflow_recovery.enabled else {}),
            "workflow_protocol": self._workflow_protocol(),
            "dataset_fingerprint": self.dataset.fingerprint,
            "active_data_plan": self.active_data_plan.to_dict(),
            "plans": {key: plan.to_dict() for key, plan in self.plans.items()},
            "prompt_train_batch_size": self.prompt_train_batch_size,
            "pilot_plan": None if self.pilot_plan is None else asdict(self.pilot_plan),
            "termination_plan": (None if self.termination_plan is None else asdict(self.termination_plan)),
            "execution_metadata": self.execution_metadata,
            "final_test_plan": (None if self.final_test_plan is None else asdict(self.final_test_plan)),
            "training_generation_policy": TRAINING_GENERATION_POLICY,
            "stage_protocol": "separate_stages_fatal_pilot_boundary_v2",
            "authorization_required": self.authorization_required,
            "pilot_generation_policy": "run_seed_pilot_domain_query_logical_call_v1",
        }

    @staticmethod
    def _workflow_protocol() -> dict[str, str]:
        from ..workflows.prompts import PROTOCOL_ID, MESSAGE_SERIALIZATION_ID, INITIAL_TEMPLATE_ID, ROLE_PROTOCOL_ID
        from ..workflows.repair import REPAIR_DIAGNOSTIC_ID
        from ..workflows.tie_view import TIE_VIEW_ID
        from ..providers.output_contract import ROLE_OUTPUT_PROTOCOL, ROLE_SCHEMA_ID, SCHEMA_COUNTING_ID
        from ..schemas import ANSWER_STABILITY_GUARD_ID

        return {
            "fixed_protocol": PROTOCOL_ID,
            "message_serialization": MESSAGE_SERIALIZATION_ID,
            "initial_template": INITIAL_TEMPLATE_ID,
            "repair_diagnostic": REPAIR_DIAGNOSTIC_ID,
            "tie_evidence": TIE_VIEW_ID,
            "repeated_feedback": "answer_whitespace_feedback_seen_twice_v1",
            "answer_stability": ANSWER_STABILITY_GUARD_ID,
            "routing_values": "pooled_mixed_q_regression_v1",
            "role_protocol": ROLE_PROTOCOL_ID,
            "role_output_protocol": ROLE_OUTPUT_PROTOCOL,
            "role_schema": ROLE_SCHEMA_ID,
            "schema_counting": SCHEMA_COUNTING_ID,
        }

    @property
    def protocol_fingerprint(self) -> str:
        return hashlib.sha256(_canonical(self._condition()).encode()).hexdigest()

    def state_dict(self) -> dict[str, Any]:
        return copy.deepcopy(
            {
                "schema_version": 4,
                **({"supplement_lineage": self.supplement_lineage} if self.supplement_lineage else {}),
                **({"calibration_lineage": self.calibration_lineage} if self.calibration_lineage else {}),
                **({"regular_lineage": self.regular_lineage} if self.regular_lineage else {}),
                "stage_authorizations": copy.deepcopy(self.stage_authorizations),
                "compatibility_records": copy.deepcopy(self.compatibility_records),
                "targeted_recheck": copy.deepcopy(self.targeted_recheck),
                "condition": self._condition(),
                "expenditure_ledger": (
                    None if self.expenditure_ledger is None else self.expenditure_ledger.state_dict()
                ),
                "sample_counters": self.sample_counters,
                "safety_record": (None if self.safety_record is None else self.safety_record.to_dict()),
                "pilot_observations": [asdict(item) for item in self.pilot_observations],
                "termination_record": (None if self.termination_record is None else self.termination_record.to_dict()),
                "evaluation_records": self.evaluation_records,
                "evaluation_cache": {key: record.to_dict() for key, record in self.evaluation_cache.items()},
                **({"prompt_evidence_records": self.prompt_evidence_records} if self.prompt_evidence_records else {}),
                **({"extension_parent_acceptance": self.extension_parent_acceptance} if self.extension_parent_acceptance else {}),
                **({"recovery_slots": self.recovery_slots,
                    "evaluation_query_cache": self.evaluation_query_cache}
                   if self.config.workflow_recovery.enabled else {}),
                **({"calibration_query_cache": self.calibration_query_cache} if self.calibration_query_cache else {}),
            }
        )

    def checkpoint_state_dict(self, store: Any) -> dict[str, Any]:
        """Freeze small mutable controls; reference completed immutable evidence.

        Called at a workflow boundary on the ordered writer. Completed records
        are never edited in place; a newly completed record has its own identity.
        Active recovery slots remain isolated snapshots until terminal.
        """
        if not hasattr(self, "_checkpoint_evidence"):
            self._checkpoint_evidence = {}
        from ..infrastructure.incremental import snapshot_collection

        def records(value: Any) -> Any:
            return snapshot_collection(store, value)

        def pilot_record(value: Any) -> Any:
            return frozen("pilot", value, lambda: asdict(value))

        def evaluation_record(value: Any) -> Any:
            return frozen("evaluation_cache", value, value.to_dict)

        def frozen(kind: str, value: Any, render: Callable[[], Any] | None = None) -> Any:
            key = (kind, id(value))
            existing = self._checkpoint_evidence.get(key)
            if existing is not None and existing[0] is value and existing[2] == store.directory:
                return existing[1]
            result = store.put(value if render is None else render())
            self._checkpoint_evidence[key] = (value, result, store.directory)
            return result

        state = {
            "schema_version": 4,
            **({"supplement_lineage": copy.deepcopy(self.supplement_lineage)} if self.supplement_lineage else {}),
            **({"calibration_lineage": copy.deepcopy(self.calibration_lineage)} if self.calibration_lineage else {}),
            **({"regular_lineage": copy.deepcopy(self.regular_lineage)} if self.regular_lineage else {}),
            "stage_authorizations": copy.deepcopy(self.stage_authorizations),
            "compatibility_records": [frozen("compatibility", item) if item.get("completion_reason")
                                      else copy.deepcopy(item) for item in self.compatibility_records],
            "targeted_recheck": copy.deepcopy(self.targeted_recheck),
            "condition": self._condition(),
            "expenditure_ledger": None if self.expenditure_ledger is None else self.expenditure_ledger.checkpoint_state_dict(store),
            "sample_counters": dict(self.sample_counters),
            "safety_record": None if self.safety_record is None else frozen("safety", self.safety_record, self.safety_record.to_dict),
            "pilot_observations": snapshot_collection(store, self.pilot_observations, pilot_record),
            "termination_record": None if self.termination_record is None else frozen("termination", self.termination_record, self.termination_record.to_dict),
            "evaluation_records": records(self.evaluation_records),
            "evaluation_cache": snapshot_collection(store, self.evaluation_cache, evaluation_record),
            **({"prompt_evidence_records": records(self.prompt_evidence_records)} if self.prompt_evidence_records else {}),
            **({"extension_parent_acceptance": copy.deepcopy(self.extension_parent_acceptance)} if self.extension_parent_acceptance else {}),
        }
        if self.config.workflow_recovery.enabled:
            state["recovery_slots"] = records(self.recovery_slots)
            state["evaluation_query_cache"] = snapshot_collection(store, self.evaluation_query_cache, records)
        if self.calibration_query_cache:
            state["calibration_query_cache"] = snapshot_collection(store, self.calibration_query_cache, records)
        return state

    def validate_state(
        self, state: Mapping[str, Any], *,
        _ledger_plans: list[Any] | None = None,
        _parent_facts: list[Any] | None = None,
    ) -> None:
        if state.get("schema_version") != 4 or _canonical(state.get("condition")) != _canonical(self._condition()):
            raise ValueError("incompatible experiment data, validation plans, or calibration protocol")
        regular_parent = self._validate_regular_lineage(state.get("regular_lineage"))
        calibration_parent = self._validate_calibration_lineage(state.get("calibration_lineage"), regular_parent=regular_parent)
        parent_payload = (
            self._validate_supplement_lineage(state.get("supplement_lineage"), calibration_parent=calibration_parent)
            if calibration_parent else self._validate_supplement_lineage(state.get("supplement_lineage"))
        )
        parent_hooks: dict[str, Any] = parent_payload["hook_state"] if parent_payload else {}
        saved_parent_acceptance = state.get("extension_parent_acceptance")
        # Validation may populate the auxiliary parent-evidence cache, but a
        # rejected restore must not mutate the caller's hook object.
        parent_validator = copy.copy(self)
        if saved_parent_acceptance is not None and _canonical(saved_parent_acceptance) != _canonical(parent_validator.expected_extension_parent_acceptance()):
            raise ValueError("extension parent safeguard acceptance changed its registered source")
        if self.execution_metadata.get("registered_extension") and saved_parent_acceptance is None:
            raise ValueError("extension checkpoint is missing its explicit inherited safeguard source")
        if state.get("targeted_recheck") is not None:
            raise ValueError("captured-state recheck checkpoints are retired and cannot resume")
        if state.get("expenditure_ledger") is not None:
            if state["expenditure_ledger"].get("halted"):
                saved_policy = state["expenditure_ledger"].get("failure_policy")
                current_policy = getattr(self.expenditure_ledger, "failure_policy", None)
                policy_resume = bool(
                    saved_policy
                    and current_policy
                    and self.execution_session_guard is not None
                    and self.execution_session_guard() is True
                    and saved_policy.get("bindings", {}).get("condition_fingerprint") == self.condition_fingerprint
                    and {
                        key: value
                        for key, value in saved_policy.items()
                        if key not in {"events", "historical_adoptions"}
                    }
                    == {
                        key: value
                        for key, value in current_policy.items()
                        if key not in {"events", "historical_adoptions"}
                    }
                )
                if policy_resume:
                    from ..providers.resources import PolicyStopped

                    try:
                        self.expenditure_ledger.assert_dispatch_allowed()
                    except PolicyStopped:
                        pass  # Loading a stopped report never reopens admissions.
                if not policy_resume:
                    raise ValueError(
                        "ordinary resume cannot restore an operationally halted campaign; reconciliation is required"
                    )
            if self.expenditure_ledger is None:
                raise ValueError("checkpoint requires its physical expenditure ledger")
            if _ledger_plans is None:
                self.expenditure_ledger.validate_restore(state["expenditure_ledger"])
            else:
                _ledger_plans.append(self.expenditure_ledger.prepare_restore(state["expenditure_ledger"]))
        counters = state.get("sample_counters", {})
        validate_recovery_records(state.get("recovery_slots", {}))
        if state.get("recovery_slots") and not self.config.workflow_recovery.enabled:
            raise ValueError("checkpoint recovery records require the registered amended condition")
        for queries in state.get("evaluation_query_cache", {}).values():
            if not isinstance(queries, dict):
                raise ValueError("invalid recovery validation query cache")
            for query_id, value in queries.items():
                if ExampleEvaluation(**value).example_id != query_id:
                    raise ValueError("recovery query cache changed its identity")
        for key, queries in state.get("calibration_query_cache", {}).items():
            if not isinstance(key, str) or not isinstance(queries, dict) or self.termination_plan is None:
                raise ValueError("invalid calibration selected-prefix cache")
            for query_id, item in queries.items():
                if (query_id not in self.termination_plan.validation.ordered_example_ids
                        or set(item) != {"examples", "shared_record"}
                        or len(item["examples"]) != len(self.termination_plan.candidates)
                        or item["shared_record"].get("example_id") != query_id):
                    raise ValueError("calibration selected-prefix cache changed its planned query or candidate set")
                for projection, candidate in zip(item["examples"], self.termination_plan.candidates, strict=True):
                    result = ExampleEvaluation(**projection)
                    if result.example_id != query_id or result.stopping_session > candidate.max_sessions:
                        raise ValueError("calibration selected-prefix cache changed its stopping boundary")
        if set(counters) != set(self.sample_counters) or any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in counters.values()
        ):
            raise ValueError("invalid experiment data-sampling state")
        evidence_records = state.get("prompt_evidence_records", [])
        if not isinstance(evidence_records, list) or any(
            not isinstance(item, dict)
            or item.get("example_id") not in self.active_data_plan.train_ids
            or not isinstance(item.get("attempt_id"), int)
            or item["attempt_id"] < 0
            or not isinstance(item.get("completed"), bool)
            for item in evidence_records
        ):
            raise ValueError("invalid prompt evidence attempt records")
        identities = [item["attempt_id"] for item in evidence_records]
        if len(identities) != len(set(identities)):
            raise ValueError("prompt evidence reused a global workflow attempt")
        expected_plans = {plan.domain: plan.fingerprint for plan in self.plans.values()}
        evidence_parent = (regular_parent["hook_state"] if regular_parent else
                           calibration_parent["hook_state"] if calibration_parent else parent_hooks)
        historic = evidence_parent.get("evaluation_records", []) if evidence_parent else []
        if not strict_json_equal(state.get("evaluation_records", [])[:len(historic)], historic):
            raise ValueError("supplement changed original validation evidence")
        if calibration_parent or regular_parent:
            for key, value in evidence_parent.get("recovery_slots", {}).items():
                if not strict_json_equal(state.get("recovery_slots", {}).get(key), value):
                    raise ValueError("amendment changed a historical recovery allowance")
        if regular_parent:
            if not strict_json_equal(state.get("termination_record"), evidence_parent.get("termination_record")):
                raise ValueError("regular amendment changed the frozen calibration outcome")
            for key, value in evidence_parent.get("calibration_query_cache", {}).items():
                if not strict_json_equal(state.get("calibration_query_cache", {}).get(key), value):
                    raise ValueError("regular amendment changed the preserved calibration prefixes")
        for record in (
            *state.get("evaluation_records", ())[len(historic):],
            *state.get("evaluation_cache", {}).values(),
        ):
            restored = EvaluationRecord.from_dict(record)
            if expected_plans.get(restored.plan.domain) != restored.plan.fingerprint:
                raise ValueError("saved validation record changed the declared experiment plan")
        from .calibration import (
            PilotObservation,
            SafetyCalibrationRecord,
            TerminationCalibrationRecord,
        )

        pilot_observations = tuple(PilotObservation.from_dict(item) for item in state.get("pilot_observations", ()))
        if pilot_observations and (
            self.pilot_plan is None
            or tuple(item.example_id for item in pilot_observations)
            != self.pilot_plan.ordered_example_ids[: len(pilot_observations)]
        ):
            raise ValueError("saved pilot progress changed the declared query order")
        if state.get("safety_record") is not None:
            safety = SafetyCalibrationRecord.from_dict(state["safety_record"])
            if safety.operational_halt is not None or any(item.operational_halt for item in safety.observations):
                raise ValueError("ordinary resume cannot continue an operationally halted pilot")
            if not strict_json_equal(state["safety_record"]["observations"], state.get("pilot_observations", ())):
                raise ValueError("saved pilot report differs from its recorded workflow observations")
            if self.pilot_plan is None or _canonical(asdict(safety.plan)) != _canonical(asdict(self.pilot_plan)):
                raise ValueError("saved safety calibration changed the declared training pilot")
            if safety.review is not None:
                safety._validate_review(safety.review,
                    hashlib.sha256(_canonical(parent_hooks["condition"]).encode()).hexdigest()
                    if parent_hooks else self.condition_fingerprint)
                if parent_hooks and not strict_json_equal(state["safety_record"], parent_hooks["safety_record"]):
                    raise ValueError("supplement rewrote historical pilot acceptance")
        from ..operations.preflight import validate_stage_authorization

        saved_safety = SafetyCalibrationRecord.from_dict(state["safety_record"]) if state.get("safety_record") else None
        for stage, artifact in state.get("stage_authorizations", {}).items():
            if parent_hooks:
                from ..operations.preflight import SUPPLEMENT_SCOPE, STAGE_SCOPES, CALIBRATION_SCOPE, REGULAR_SCOPE
                lineage = state["supplement_lineage"]
                plan = read_json(Path(lineage["plan_path"]))
                scope = artifact.get("scope")
                if scope == SUPPLEMENT_SCOPE:
                    if (stage != "development"
                            or artifact.get("supplement_plan_sha256") != lineage["plan_sha256"]
                            or artifact.get("supplement_forecast_sha256") != plan["references"]["forecast"]["sha256"]):
                        raise ValueError("supplement checkpoint carries unrelated stage approval")
                elif scope == REGULAR_SCOPE:
                    if stage != "development" or regular_parent is None:
                        raise ValueError("regular checkpoint lacks its amended approval lineage")
                elif scope == CALIBRATION_SCOPE:
                    if stage != "development" or calibration_parent is None:
                        raise ValueError("calibration checkpoint lacks its amended approval lineage")
                elif stage not in {"development", "test"} or scope != STAGE_SCOPES[stage]:
                    raise ValueError("supplement checkpoint carries unrelated stage approval")
            validate_stage_authorization(
                artifact,
                **parent_validator._authorization_bindings(
                    stage,
                    safety_record=saved_safety,
                    checkpoint_sha256=artifact.get("checkpoint_sha256"),
                    compatibility_plan_fingerprint=artifact.get("compatibility_plan_fingerprint"),
                    execution_scope=artifact.get("scope"),
                    bootstrap_forecast_sha256=artifact.get("bootstrap_forecast_sha256"),
                    supplement_plan_sha256=artifact.get("supplement_plan_sha256"),
                    supplement_forecast_sha256=artifact.get("supplement_forecast_sha256"),
                    calibration_plan_sha256=artifact.get("calibration_plan_sha256"),
                    calibration_forecast_sha256=artifact.get("calibration_forecast_sha256"),
                regular_plan_sha256=artifact.get("regular_plan_sha256"),
                regular_forecast_sha256=artifact.get("regular_forecast_sha256"),
                    calibration_lineage=state.get("calibration_lineage"),
                    regular_lineage=state.get("regular_lineage"),
                ),
            )
        if saved_safety is not None and saved_safety.approved:
            if saved_safety.review != (parent_hooks if parent_hooks else state).get("stage_authorizations", {}).get("development"):
                raise ValueError("approved pilot report is missing its matching explicit development authorization")
        compatibility = state.get("compatibility_records", [])
        if not isinstance(compatibility, list) or len(compatibility) > 1:
            raise ValueError("compatibility state must describe at most one declared bounded plan")
        for record in compatibility:
            expected = self.compatibility_plan()
            requests = record.get("requests", ())
            if (
                _canonical(record.get("plan")) != _canonical(expected)
                or record.get("status") not in {"running", "complete", "failed"}
                or not isinstance(requests, list)
                or len(requests) > len(expected["agent_requests"])
            ):
                raise ValueError("saved compatibility checks changed the declared bounded plan")
            for request, declared in zip(requests, expected["agent_requests"]):
                logical = request.get("logical_call", {})
                if request.get("probe") != declared or (
                    logical.get("agent_id"),
                    logical.get("call_type"),
                    logical.get("repair_attempt"),
                ) != (
                    declared["agent_id"],
                    declared["call_kind"],
                    0,
                ):
                    raise ValueError("saved compatibility requests differ from the declared ordered checks")
            if record["status"] == "complete" and (
                len(requests) != len(expected["agent_requests"])
                or not isinstance(record.get("proposal"), Mapping)
                or record["proposal"].get("error") is not None
                or record.get("committed_prompt_updates") != 0
            ):
                raise ValueError("completed compatibility checks require all outputs and an uncommitted proposal")
        if state.get("termination_record") is not None:
            termination = TerminationCalibrationRecord.from_dict(state["termination_record"])
            if self.termination_plan is None or _canonical(asdict(termination.plan)) != _canonical(
                asdict(self.termination_plan)
            ):
                raise ValueError("saved termination calibration changed the declared search protocol")
        if _parent_facts is not None:
            _parent_facts.append(copy.deepcopy(parent_validator._extension_parent_source_facts))

    @staticmethod
    def _restore_fields_token(values: Mapping[str, Any]) -> str:
        """One-process mutation guard; frozen audit nodes are immutable capabilities."""
        def encoded(value: Any) -> Any:
            if isinstance(value, (FrozenEvidenceDict, FrozenEvidenceList)):
                return ["immutable", id(value)]
            if is_dataclass(value) and not isinstance(value, type):
                return ["record", [(item.name, encoded(getattr(value, item.name))) for item in fields(value)]]
            if isinstance(value, Mapping):
                return ["mapping", [(key, encoded(value[key])) for key in sorted(value)]]
            if isinstance(value, (tuple, list)):
                return ["sequence", [encoded(item) for item in value]]
            return ["scalar", value]
        controls = {key: value for key, value in values.items() if key != "_checkpoint_evidence"}
        sources = [[list(key), id(item[0]), item[1], str(item[2])]
                   for key, item in sorted(values.get("_checkpoint_evidence", {}).items())]
        return strict_json_fingerprint([encoded(controls), sources])

    def prepare_restore(self, state: Mapping[str, Any]) -> dict[str, Any]:
        """Validate and construct detached hook state before numerical mutation."""
        ledger_plans: list[Any] = []
        parent_facts: list[Any] = []
        self.validate_state(state, _ledger_plans=ledger_plans, _parent_facts=parent_facts)
        candidate = copy.copy(self)
        candidate._restore_fields(state)
        candidate._extension_parent_source_facts = parent_facts[0]
        names = (
            "supplement_lineage", "calibration_lineage", "regular_lineage", "stage_authorizations",
            "extension_parent_acceptance", "compatibility_records", "prompt_evidence_records",
            "recovery_slots", "evaluation_query_cache", "calibration_query_cache",
            "pilot_observations", "sample_counters", "evaluation_records", "evaluation_cache",
            "safety_record", "termination_record", "_checkpoint_evidence",
            "_extension_parent_source_facts",
        )
        proposed = {name: getattr(candidate, name) for name in names}
        current = {name: getattr(self, name, None if name != "_checkpoint_evidence" else {}) for name in names}
        return {"owner": self, "source": state, "source_digest": strict_json_fingerprint(state),
                "condition": self.condition_fingerprint, "ledger": ledger_plans,
                "fields": proposed, "fields_digest": self._restore_fields_token(proposed),
                "current_digest": self._restore_fields_token(current), "used": False}

    def apply_restore(self, plan: dict[str, Any]) -> None:
        if (plan["used"] or plan["owner"] is not self
                or plan["condition"] != self.condition_fingerprint
                or plan["source_digest"] != strict_json_fingerprint(plan["source"])
                or plan["fields_digest"] != self._restore_fields_token(plan["fields"])
                or plan["current_digest"] != self._restore_fields_token({
                    name: getattr(self, name, None if name != "_checkpoint_evidence" else {})
                    for name in plan["fields"]})):
            raise ValueError("hook restore inputs changed after validation")
        # All construction/validation precedes this atomic, non-awaiting commit.
        # The ledger checks its own complete current/source fingerprints first.
        if plan["ledger"]:
            self.expenditure_ledger.apply_restore(plan["ledger"][0])
        self.__dict__.update(plan["fields"])
        plan["used"] = True

    def load_state_dict(self, state: Mapping[str, Any]) -> None:
        self.apply_restore(self.prepare_restore(state))

    def _restore_fields(self, state: Mapping[str, Any]) -> None:
        from .calibration import (
            PilotObservation,
            SafetyCalibrationRecord,
            TerminationCalibrationRecord,
        )

        self.supplement_lineage = copy.deepcopy(state.get("supplement_lineage"))
        self.calibration_lineage = copy.deepcopy(state.get("calibration_lineage"))
        self.regular_lineage = copy.deepcopy(state.get("regular_lineage"))
        self.stage_authorizations = dict(copy.deepcopy(state.get("stage_authorizations", {})))
        self.extension_parent_acceptance = copy.deepcopy(state.get("extension_parent_acceptance"))
        self.compatibility_records = list(copy.deepcopy(state.get("compatibility_records", [])))
        self.prompt_evidence_records = list(copy.deepcopy(state.get("prompt_evidence_records", [])))
        self.recovery_slots = dict(copy.deepcopy(state.get("recovery_slots", {})))
        self.evaluation_query_cache = {key: dict(copy.deepcopy(values)) for key, values in state.get("evaluation_query_cache", {}).items()}
        self.calibration_query_cache = {key: dict(copy.deepcopy(values)) for key, values in state.get("calibration_query_cache", {}).items()}
        self.pilot_observations = [PilotObservation.from_dict(item) for item in state.get("pilot_observations", ())]
        self.sample_counters = dict(state["sample_counters"])
        self.evaluation_records = list(copy.deepcopy(state.get("evaluation_records", [])))
        self.evaluation_cache = {
            key: EvaluationRecord.from_dict(value) for key, value in state.get("evaluation_cache", {}).items()
        }
        self.safety_record = (
            SafetyCalibrationRecord.from_dict(state["safety_record"]) if state.get("safety_record") else None
        )
        self.termination_record = (
            TerminationCalibrationRecord.from_dict(state["termination_record"])
            if state.get("termination_record")
            else None
        )
        # Dataclass restoration validates/reconstructs the public record but
        # would otherwise discard the verified source object's storage stamp.
        # Retain that immutable provenance so restart does not rerender all
        # completed observations/evaluations on its first ordinary checkpoint.
        self._checkpoint_evidence = {}

        def retain(kind: str, restored: Any, source: Any) -> None:
            stamp = getattr(source, "_evidence_stamp", None)
            if (restored is not None and stamp is not None
                    and getattr(source, "_evidence_root", False) and not stamp.dirty):
                self._checkpoint_evidence[(kind, id(restored))] = (
                    restored, stamp.reference, stamp.directory)

        for restored, source in zip(self.pilot_observations, state.get("pilot_observations", ())):
            retain("pilot", restored, source)
        for key, restored in self.evaluation_cache.items():
            retain("evaluation_cache", restored, state["evaluation_cache"][key])
        retain("safety", self.safety_record, state.get("safety_record"))
        retain("termination", self.termination_record, state.get("termination_record"))
        from ..infrastructure.incremental import retain_verified
        for name in self._INCREMENTAL_FIELDS:
            if name in state:
                retain_verified(getattr(self, name), state[name])

    def restore_selected_state(self, state: Mapping[str, Any]) -> None:
        """Restore sampling state without erasing completed evaluations or physical expenditure."""

        records, cache = self.evaluation_records, self.evaluation_cache
        recovery, query_cache = self.recovery_slots, self.evaluation_query_cache
        self.load_state_dict(state)
        self.evaluation_records = records
        self.evaluation_cache.update(cache)
        self.recovery_slots.update(recovery)
        self.evaluation_query_cache.update(query_cache)

    @property
    def condition_fingerprint(self) -> str:
        return hashlib.sha256(_canonical(self._condition()).encode()).hexdigest()

    def _authorization_bindings(
        self,
        stage: str,
        *,
        checkpoint_sha256: str | None = None,
        compatibility_plan_fingerprint: str | None = None,
        safety_record: Any = None,
        execution_scope: str | None = None,
        bootstrap_forecast_sha256: str | None = None,
        supplement_plan_sha256: str | None = None,
        supplement_forecast_sha256: str | None = None,
        calibration_plan_sha256: str | None = None,
        calibration_forecast_sha256: str | None = None,
        calibration_lineage: Mapping[str, Any] | None = None,
        regular_lineage: Mapping[str, Any] | None = None,
        regular_plan_sha256: str | None = None,
        regular_forecast_sha256: str | None = None,
    ) -> dict[str, Any]:
        if stage not in {"compatibility", "pilot", "development", "test"}:
            raise ValueError("unknown execution stage")
        from ..operations.preflight import SUPPLEMENT_SCOPE, CALIBRATION_SCOPE, REGULAR_SCOPE
        if execution_scope == SUPPLEMENT_SCOPE and self.supplement_lineage is not None:
            plan = read_json(Path(self.supplement_lineage["plan_path"]))
            if (supplement_plan_sha256 != self.supplement_lineage["plan_sha256"]
                    or supplement_forecast_sha256 != plan["references"]["forecast"]["sha256"]):
                raise ValueError("supplement approval differs from its audited plan or forecast")
        if execution_scope == CALIBRATION_SCOPE:
            lineage = calibration_lineage if calibration_lineage is not None else self.calibration_lineage
            if lineage is None:
                raise ValueError("calibration approval requires its audited amendment lineage")
            plan_path = Path(lineage["plan_path"])
            raw_plan = read_bytes(plan_path)
            if hashlib.sha256(raw_plan).hexdigest() != lineage["plan_sha256"]:
                raise ValueError("calibration approval plan changed its recorded hash")
            plan = json.loads(raw_plan)
            if (calibration_plan_sha256 != lineage["plan_sha256"]
                    or calibration_forecast_sha256 != plan["references"]["forecast"]["sha256"]):
                raise ValueError("calibration approval differs from its audited plan or forecast")
        if execution_scope == REGULAR_SCOPE:
            lineage = regular_lineage if regular_lineage is not None else self.regular_lineage
            if lineage is None:
                raise ValueError("regular approval requires its audited amendment lineage")
            raw_plan = read_bytes(Path(lineage["plan_path"]))
            if hashlib.sha256(raw_plan).hexdigest() != lineage["plan_sha256"]:
                raise ValueError("regular approval plan changed its recorded hash")
            plan = json.loads(raw_plan)
            if (regular_plan_sha256 != lineage["plan_sha256"]
                    or regular_forecast_sha256 != plan["references"]["forecast"]["sha256"]):
                raise ValueError("regular approval differs from its audited plan or forecast")
        record = self.safety_record if safety_record is None else safety_record
        budget_stage = stage
        return {
            "stage": stage,
            "manifest_fingerprint": self.execution_metadata.get("authorization_manifest_fingerprint"),
            "condition_fingerprint": self.condition_fingerprint,
            "resource_profile_fingerprint": self.execution_metadata.get("resource_profile_fingerprint"),
            "approved_budget_usd": getattr(self.config.resources, f"{budget_stage}_budget"),
            "pilot_fingerprint": (
                self.expected_extension_parent_acceptance()["parent_pilot_fingerprint"]
                if stage in {"development", "test"} and self.execution_metadata.get("registered_extension")
                else (record.fingerprint if record is not None and stage in {"development", "test"} else None)
            ),
            "checkpoint_sha256": checkpoint_sha256 if stage == "test" else None,
            "compatibility_plan_fingerprint": (compatibility_plan_fingerprint if stage == "compatibility" else None),
            "execution_scope": execution_scope,
            "bootstrap_forecast_sha256": bootstrap_forecast_sha256,
            "supplement_plan_sha256": supplement_plan_sha256,
            "supplement_forecast_sha256": supplement_forecast_sha256,
            "calibration_plan_sha256": calibration_plan_sha256,
            "calibration_forecast_sha256": calibration_forecast_sha256,
            "regular_plan_sha256": regular_plan_sha256,
            "regular_forecast_sha256": regular_forecast_sha256,
        }

    def _validate_regular_lineage(self, lineage: Mapping[str, Any] | None) -> dict[str, Any] | None:
        registered = self.execution_metadata.get("registered_regular")
        if lineage is None:
            if registered:
                raise ValueError("regular checkpoint lacks its audited lineage")
            return None
        if not registered:
            raise ValueError("unregistered regular-development lineage")
        from ..operations.regular_development import reconstruct_lineage
        from ..operations.experiment import planning_hooks
        parent_prepared, amended, plan = reconstruct_lineage(lineage)
        if (_canonical(planning_hooks(amended)._condition()) != _canonical(self._condition())
                or _canonical(registered) != _canonical(amended.specification_amendments[-1])):
            raise ValueError("regular condition differs from independently reconstructed plan")
        parent = load_checkpoint(plan["references"]["checkpoint"]["path"])
        if _canonical(parent["hook_state"]["condition"]) != _canonical(planning_hooks(parent_prepared)._condition()):
            raise ValueError("regular source differs from its recorded calibrated condition")
        return parent

    def _validate_calibration_lineage(
        self, lineage: Mapping[str, Any] | None, *, regular_parent: Mapping[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        registered = self.execution_metadata.get("registered_calibration")
        if lineage is None:
            if registered:
                raise ValueError("amended calibration checkpoint lacks its audited lineage")
            return None
        if (not registered or set(lineage) != {"protocol", "plan_path", "plan_sha256", "parent_checkpoint"}
                or lineage["protocol"] != "audited_calibration_limits_v1"):
            raise ValueError("unregistered calibration amendment lineage")
        from ..operations.calibration_launch import reconstruct_calibration_lineage
        from ..operations.experiment import planning_hooks

        parent_prepared, amended, plan = reconstruct_calibration_lineage(lineage)
        expected_condition = self._condition()
        if regular_parent is not None:
            if _canonical(lineage) != _canonical(regular_parent["hook_state"].get("calibration_lineage")):
                raise ValueError("regular amendment changed prior calibration lineage")
            expected_condition = regular_parent["hook_state"]["condition"]
        if _canonical(planning_hooks(amended)._condition()) != _canonical(expected_condition):
            raise ValueError("calibration condition differs from its independently reconstructed plan")
        parent = load_checkpoint(plan["references"]["checkpoint"]["path"])
        if _canonical(parent["hook_state"]["condition"]) != _canonical(planning_hooks(parent_prepared)._condition()):
            raise ValueError("calibration source was not the recorded pre-calibration condition")
        return parent

    def _validate_supplement_lineage(
        self, lineage: Mapping[str, Any] | None, *, calibration_parent: Mapping[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Validate historical acceptance against its original bytes, not new fingerprints."""
        registered = self.execution_metadata.get("registered_supplement")
        if lineage is None:
            if registered:
                raise ValueError("amended checkpoint lacks its audited supplement lineage")
            return None
        if not registered or set(lineage) != {"protocol", "plan_path", "plan_sha256", "parent_checkpoint"}:
            raise ValueError("unregistered supplementary lineage")
        if lineage["protocol"] != "audited_weighting_supplement_v1":
            raise ValueError("unknown supplementary lineage protocol")
        from ..operations.supplement import reconstruct_condition_lineage
        from ..operations.experiment import planning_hooks
        original, amended, plan = reconstruct_condition_lineage(lineage)
        expected_condition = self._condition()
        if calibration_parent is not None:
            if _canonical(lineage) != _canonical(calibration_parent["hook_state"].get("supplement_lineage")):
                raise ValueError("calibration changed its prior supplement lineage")
            expected_condition = calibration_parent["hook_state"]["condition"]
        if (_canonical(amended.continuation_registration) != _canonical(registered)
                or _canonical(planning_hooks(amended)._condition()) != _canonical(expected_condition)):
            raise ValueError("supplement condition differs from the independently reconstructed plan")
        parent = load_checkpoint(plan["references"]["checkpoint"]["path"])
        if _canonical(parent["hook_state"]["condition"]) != _canonical(planning_hooks(original)._condition()):
            raise ValueError("supplement source was not the declared original condition")
        return parent

    def apply_supplement(self, prepared: Any, plan: Mapping[str, Any], lineage: Mapping[str, Any]) -> None:
        """Prospective-only binding after normal original checkpoint restoration."""
        if self.supplement_lineage is not None or self.recovery_slots:
            raise ValueError("supplement cannot reset an existing continuation or recovery allowance")
        if self.condition_fingerprint != plan["parent"]["condition_fingerprint"]:
            raise ValueError("supplement was not loaded under its original condition")
        from ..operations.experiment import _execution_metadata
        self.trainer.config = self.trainer.declared_config = prepared.config
        self.plans = dict(prepared.plans)
        self.termination_plan = prepared.termination_plan
        self.execution_metadata = _execution_metadata(prepared, self.execution_metadata["textgrad"])
        self.supplement_lineage = copy.deepcopy(dict(lineage))
        parent = self._validate_supplement_lineage(self.supplement_lineage)
        if _canonical(self.state_dict()["evaluation_records"]) != _canonical(parent["hook_state"]["evaluation_records"]):
            raise ValueError("supplement changed original validation evidence")
        # Preserve immutable old records in place. They are not a matching cache
        # under the new policy; the one explicitly approved linked recovery is
        # validated separately from this general cache.
        self.evaluation_cache = {}
        self.evaluation_query_cache = {}
        self.stage_authorizations = {}

    def apply_calibration(self, prepared: Any, plan: Mapping[str, Any], lineage: Mapping[str, Any]) -> None:
        """Bind a reviewed prospective limit amendment without erasing historical work."""
        if self.calibration_lineage is not None or self.termination_record is not None:
            raise ValueError("calibration amendment cannot reset an existing activation or calibration")
        if self.condition_fingerprint != plan["parent"]["condition_fingerprint"]:
            raise ValueError("calibration amendment was not loaded under its original condition")
        from ..operations.experiment import _execution_metadata

        self.trainer.config = self.trainer.declared_config = prepared.config
        self.plans = dict(prepared.plans)
        self.termination_plan = prepared.termination_plan
        self.execution_metadata = _execution_metadata(prepared, self.execution_metadata["textgrad"])
        self.calibration_lineage = copy.deepcopy(dict(lineage))
        parent = self._validate_calibration_lineage(self.calibration_lineage)
        if (_canonical(self.evaluation_records) != _canonical(parent["hook_state"]["evaluation_records"])
                or _canonical(self.recovery_slots) != _canonical(parent["hook_state"].get("recovery_slots", {}))):
            raise ValueError("calibration amendment changed prior execution or recovery evidence")
        self._validate_supplement_lineage(self.supplement_lineage, calibration_parent=parent)
        self.evaluation_cache = {}
        self.evaluation_query_cache = {}
        self.stage_authorizations = {}

    def apply_regular(self, prepared: Any, plan: Mapping[str, Any], lineage: Mapping[str, Any]) -> None:
        """Bind prospective output repair without changing calibrated inference evidence."""
        if (self.regular_lineage is not None or self.termination_record is None
                or self.condition_fingerprint != plan["parent"]["condition_fingerprint"]):
            raise ValueError("regular amendment requires its exact calibrated parent")
        from ..operations.experiment import _execution_metadata
        from .textgrad_optimizer import TextGradConfig, textgrad_metadata
        proposer = self.trainer.prompt_optimizer.proposer
        proposer.config = TextGradConfig(**dict(prepared.textgrad["config"]))
        from ..providers.resources import BackendResourceProfile
        profiles = {role: asdict(BackendResourceProfile.from_dict(
            prepared.backends[prepared.textgrad[f"{role}_backend"]]["resources"]))
            for role in ("backward", "optimizer")}
        metadata = textgrad_metadata(proposer.config, profiles)
        self.execution_metadata = _execution_metadata(prepared, metadata)
        self.regular_lineage = copy.deepcopy(dict(lineage))
        parent = self._validate_regular_lineage(self.regular_lineage)
        if (_canonical(self.evaluation_records) != _canonical(parent["hook_state"]["evaluation_records"])
                or _canonical(self.termination_record.to_dict()) != _canonical(parent["hook_state"]["termination_record"])):
            raise ValueError("regular amendment changed calibration or validation evidence")
        self.stage_authorizations = {}

    def expected_extension_parent_acceptance(self) -> dict[str, Any]:
        """A reviewed parent profile is evidence, never a fabricated local pilot."""
        context = self.execution_metadata.get("registered_extension")
        if not context:
            raise ValueError("parent safeguard reuse requires an explicit registered extension")
        parent_checkpoint = Path(context["references"]["parent_checkpoint"])
        # A runtime memo is not permission to skip a new startup's dependency
        # check. The final supervisor freshness check covers later changes.
        if current_startup() is not None and file_sha256(parent_checkpoint) != context["parent_checkpoint_sha256"]:
            raise ValueError("reviewed parent checkpoint changed")
        if self._extension_parent_source_facts is None:
            from .calibration import SafetyCalibrationRecord
            from .training import CheckpointManager

            raw = read_bytes(parent_checkpoint)
            if hashlib.sha256(raw).hexdigest() != context["parent_checkpoint_sha256"]:
                raise ValueError("reviewed parent checkpoint changed")
            payload = load_checkpoint(context["references"]["parent_checkpoint"])
            state = payload.get("hook_state", {})
            if (payload.get("schema_version") != CheckpointManager.schema_version
                    or payload.get("request_boundary_pending") is not False
                    or payload.get("trainer_state", {}).get("phase") != "complete"
                    or payload.get("best_system") is None or not isinstance(state, Mapping)):
                raise ValueError("extension requires a complete selected parent and reviewed safeguards")
            if state.get("safety_record"):
                safety = SafetyCalibrationRecord.from_dict(state["safety_record"])
                if not safety.approved or safety.configuration != self.config.safety:
                    raise ValueError("extension does not have the same reviewed parent resource profile")
                reviewed_condition = state["condition"]
                if state.get("supplement_lineage"):
                    from ..operations.experiment import planning_hooks, prepare_extension_parent

                    if not context["references"].get("parent_supplement_plan"):
                        raise ValueError("extension amended parent requires its explicit supplement reference")
                    prepared = prepare_extension_parent(context["references"])
                    parent = planning_hooks(prepared)
                    original = parent._validate_supplement_lineage(state["supplement_lineage"])
                    if original is None or _canonical(state["safety_record"]) != _canonical(original["hook_state"]["safety_record"]):
                        raise ValueError("amended parent changed its historical pilot acceptance")
                    reviewed_condition = original["hook_state"]["condition"]
                condition = hashlib.sha256(_canonical(reviewed_condition).encode()).hexdigest()
                safety._validate_review(safety.review, condition)
                fingerprint = safety.fingerprint
            elif state.get("extension_parent_acceptance"):
                from ..operations.experiment import planning_hooks, prepare_experiment
                from ..operations.preflight import configure_extension_scope_hooks

                prepared = prepare_experiment(context["references"]["parent_manifest"])
                parent = planning_hooks(prepared)
                configure_extension_scope_hooks(prepared, parent, payload)
                inherited = parent.expected_extension_parent_acceptance()
                if (state["extension_parent_acceptance"] != inherited
                        or parent.config.safety != self.config.safety
                        or _canonical(state["condition"]) != _canonical(parent._condition())):
                    raise ValueError("selected parent extension has an incompatible accepted-profile lineage")
                fingerprint = inherited["parent_pilot_fingerprint"]
            else:
                raise ValueError("extension requires a reviewed parent safeguard profile")
            self._extension_parent_source_facts = {"parent_pilot_fingerprint": fingerprint}
        return {
            "protocol": "extension_parent_profile_acceptance_v1",
            **{name: context[name] for name in ("registration_fingerprint", "plan_fingerprint", "parent_condition", "extension_condition", "parent_checkpoint_sha256")},
            **self._extension_parent_source_facts,
            "resource_profile_fingerprint": self.execution_metadata["resource_profile_fingerprint"],
        }

    def _has_extension_parent_profile(self) -> bool:
        if self.extension_parent_acceptance is None:
            return False
        if _canonical(self.extension_parent_acceptance) != _canonical(self.expected_extension_parent_acceptance()):
            raise ValueError("extension safeguard reuse differs from the registered accepted parent")
        return True

    def _require_selected_extension(self) -> None:
        if (self.trainer.state.phase != "complete" or self.trainer.best_system is None
                or not self.trainer.state.calibration_complete or self.termination_record is None):
            raise ValueError("extension final testing requires its complete selected checkpoint and frozen calibration")

    def authorize_stage(self, artifact: Mapping[str, Any]) -> None:
        from ..operations.preflight import validate_stage_authorization

        stage = str(artifact.get("stage", ""))
        parent_profile = stage in {"development", "test"} and self._has_extension_parent_profile()
        if stage == "test" and parent_profile:
            self._require_selected_extension()
        if stage in {"development", "test"} and not parent_profile and (self.safety_record is None or not self.trainer.state.pilot_complete):
            raise ValueError("development and testing require the complete separately executed pilot report")
        validated = validate_stage_authorization(
            artifact,
            **self._authorization_bindings(
                stage,
                checkpoint_sha256=artifact.get("checkpoint_sha256"),
                compatibility_plan_fingerprint=artifact.get("compatibility_plan_fingerprint"),
                execution_scope=artifact.get("scope"),
                bootstrap_forecast_sha256=artifact.get("bootstrap_forecast_sha256"),
                supplement_plan_sha256=artifact.get("supplement_plan_sha256"),
                supplement_forecast_sha256=artifact.get("supplement_forecast_sha256"),
                calibration_plan_sha256=artifact.get("calibration_plan_sha256"),
                calibration_forecast_sha256=artifact.get("calibration_forecast_sha256"),
                regular_plan_sha256=artifact.get("regular_plan_sha256"),
                regular_forecast_sha256=artifact.get("regular_forecast_sha256"),
            ),
        )
        if stage == "development" and not parent_profile and self.supplement_lineage is None:
            self.safety_record = self.safety_record.accept_review(
                validated, condition_fingerprint=self.condition_fingerprint
            )
        self.stage_authorizations[stage] = validated

    def require_stage_authorization(
        self,
        stage: str,
        *,
        checkpoint_sha256: str | None = None,
        compatibility_plan_fingerprint: str | None = None,
    ) -> None:
        if not self.authorization_required:
            return
        if self.execution_session_guard is None or self.execution_session_guard() is not True:
            raise ValueError(
                "paid execution requires an active locked campaign session with a durable expenditure journal"
            )
        from ..operations.preflight import validate_stage_authorization

        if stage not in self.stage_authorizations:
            raise ValueError(f"explicit {stage} stage authorization is required before execution")
        if stage == "test" and self._has_extension_parent_profile():
            self._require_selected_extension()
        validate_stage_authorization(
            self.stage_authorizations[stage],
            **self._authorization_bindings(
                stage,
                checkpoint_sha256=checkpoint_sha256,
                compatibility_plan_fingerprint=compatibility_plan_fingerprint,
                execution_scope=self.stage_authorizations[stage].get("scope"),
                bootstrap_forecast_sha256=self.stage_authorizations[stage].get("bootstrap_forecast_sha256"),
                supplement_plan_sha256=self.stage_authorizations[stage].get("supplement_plan_sha256"),
                supplement_forecast_sha256=self.stage_authorizations[stage].get("supplement_forecast_sha256"),
                calibration_plan_sha256=self.stage_authorizations[stage].get("calibration_plan_sha256"),
                calibration_forecast_sha256=self.stage_authorizations[stage].get("calibration_forecast_sha256"),
                regular_plan_sha256=self.stage_authorizations[stage].get("regular_plan_sha256"),
                regular_forecast_sha256=self.stage_authorizations[stage].get("regular_forecast_sha256"),
            ),
        )
        if stage == "development" and not self._has_extension_parent_profile() and (
            not self.trainer.state.pilot_complete or self.safety_record is None or not self.safety_record.approved
        ):
            raise ValueError("development requires explicit approval of the complete pilot report and profile")

    def require_development(
        self, *, bootstrap_only: bool | None = None, weighting_supplement: bool | None = None,
        calibration_only: bool | None = None,
    ) -> None:
        if not self.authorization_required:
            if self.expenditure_ledger is not None:
                self.expenditure_ledger.stage = "development"
            return
        if not self._has_extension_parent_profile() and (not self.trainer.state.pilot_complete or self.safety_record is None):
            raise ValueError("run the dedicated pilot-only stage before development")
        self.require_stage_authorization("development")
        from ..operations.preflight import BOOTSTRAP_SCOPE, SUPPLEMENT_SCOPE, CALIBRATION_SCOPE, REGULAR_SCOPE

        if self.stage_authorizations["development"]["scope"] == REGULAR_SCOPE:
            if (bootstrap_only or weighting_supplement or calibration_only or self.regular_lineage is None
                    or not self.trainer.state.calibration_complete
                    or self.trainer.state.phase not in {"weighting", "routing", "prompt", "system", "complete"}):
                raise ValueError("regular-development approval starts after checkpoint0 and ends before testing")
        if self.stage_authorizations["development"]["scope"] == CALIBRATION_SCOPE:
            allowed = {"calibration", "system_baseline"}
            if calibration_only is True:
                allowed.add("weighting")  # Return an already published checkpoint zero only.
            if (calibration_only is False or bootstrap_only or weighting_supplement
                    or self.calibration_lineage is None or self.trainer.state.phase not in allowed):
                raise ValueError("calibration-only authorization ends at checkpoint zero before regular training")
        elif calibration_only:
            raise ValueError("calibration-only execution requires its scoped approval")

        if self.stage_authorizations["development"]["scope"] == SUPPLEMENT_SCOPE:
            if bootstrap_only or weighting_supplement is False or self.supplement_lineage is None:
                raise ValueError("supplement authorization cannot launch another development scope")
            allowed = {"weighting_supplement", "routing_supplement_validation"}
            if weighting_supplement is True:
                allowed.add("calibration")
            if self.trainer.state.phase not in allowed:
                raise ValueError("supplement authorization ends before calibration")
        elif weighting_supplement:
            raise ValueError("supplement requires its own prospective approval")
        if self.stage_authorizations["development"]["scope"] == BOOTSTRAP_SCOPE:
            if bootstrap_only is False:
                raise ValueError("bootstrap-only authorization cannot launch full development")
            allowed = {"pilot_complete", "weighting_bootstrap", "routing_bootstrap"}
            # A completed bootstrap may be reopened only to return its boundary;
            # ordinary workflow/calibration callers cannot dispatch from it.
            if bootstrap_only is True:
                allowed.add("calibration")
            if self.trainer.state.phase not in allowed:
                raise ValueError("bootstrap-only authorization ends before termination calibration")
        if self.expenditure_ledger is not None:
            self.expenditure_ledger.stage = "development"

    def compatibility_plan(self) -> dict[str, Any]:
        from .calibration import compatibility_plan

        return compatibility_plan(self.config, self.condition_fingerprint)

    async def run_compatibility(self, plan: Mapping[str, Any]) -> dict[str, Any]:
        """Run only the separately approved finite synthetic checks; discard every proposed edit."""

        from ..schemas import AgentCallKind, AgentRequest
        from ..workflows.prompts import PromptOccurrence, compose_backend_input, protocol_instructions
        from .calibration import (
            compatibility_probe_prompt,
            compatibility_attempt_assessment,
            compatibility_report_assessment,
        )
        from .training import _module_fingerprint, evaluating, frozen

        expected = self.compatibility_plan()
        if _canonical(plan) != _canonical(expected):
            raise ValueError("compatibility execution must match the complete approved synthetic plan")
        self.require_stage_authorization("compatibility", compatibility_plan_fingerprint=expected["fingerprint"])
        if self.trainer.state.phase != "unstarted" or any(self.trainer.state.updates.values()):
            raise ValueError("compatibility checks must precede pilot and development")
        matches = [
            item for item in self.compatibility_records if item["plan"]["fingerprint"] == expected["fingerprint"]
        ]
        if matches and matches[0]["status"] != "running":
            return copy.deepcopy(matches[0])
        record = (
            matches[0]
            if matches
            else {
                "plan": expected,
                "status": "running",
                "requests": [],
                "proposal": None,
            }
        )
        if not matches:
            self.compatibility_records.append(record)

        def parameter_receipt() -> dict[str, str]:
            return {
                "alpha_sha256": _module_fingerprint(self.trainer.alpha),
                "weighting_sha256": _module_fingerprint(self.trainer.weighting),
                "prompt_collection_sha256": hashlib.sha256(
                    _canonical(self.trainer.gamma.to_dict()).encode()
                ).hexdigest(),
            }

        before = parameter_receipt()
        record.setdefault("parameter_receipts", {"before": before})
        if record["parameter_receipts"]["before"] != before:
            raise ValueError("compatibility resumption changed initialized parameters or seed prompts")
        if self.expenditure_ledger is not None:
            self.expenditure_ledger.stage = "compatibility"
        runtime = self._runtime()
        example = QueryExample(
            expected["synthetic_example_id"],
            expected["synthetic_query"],
            tuple(expected["synthetic_candidates"]),
        )
        runtime._generation_context = GenerationContext(self.config.seed, "compatibility", example.example_id)
        component = expected["textgrad_component"]
        fields = {
            "receiver": 0,
            "tag": "synthetic-compatibility",
            "scheme": "S_T",
            "query": example.query,
            "answer": "no",
            "explanation": "Synthetic incorrect arithmetic claim for interface checking.",
            "message_text": "Check this synthetic claim.",
            "role_instruction": "Explain the arithmetic briefly.",
        }
        role_prompt = self.trainer.gamma.render(component, **fields)
        try:
            with (
                evaluating(self.trainer.alpha, self.trainer.weighting),
                frozen(self.trainer.alpha, self.trainer.weighting),
            ):
                for position, item in enumerate(expected["agent_requests"]):
                    if position < len(record["requests"]):
                        continue
                    if self.expenditure_ledger is not None:
                        self.expenditure_ledger.assert_dispatch_allowed()
                    initial = item["call_kind"] == "initial"
                    component_key = (
                        "initial" if initial else ("revision:S_T" if item["call_kind"] == "revision" else component)
                    )
                    context = {
                        "example_id": example.example_id,
                        "admissible_answers": example.candidates,
                        "round": position,
                    }
                    logical_agent = self.config.agent_pool[item["agent_id"]]
                    instructions = "\n".join(
                        value
                        for value in (logical_agent.system_configuration, logical_agent.role_configuration)
                        if value
                    )

                    def compose(prompt: str) -> tuple[Mapping[str, str], ...]:
                        return compose_backend_input(
                            instructions, protocol_instructions(component_key, context), prompt
                        )

                    controller = getattr(runtime.agents[item["agent_id"]], "resources", None)
                    if controller is None and self.config.encoders.encoder_type != "offline_fixture":
                        raise ValueError("compatibility requires the actual frozen backend input counter")

                    def count_input(prompt: str) -> int:
                        messages = compose(prompt)
                        return (
                            controller.counter.count_input(messages)
                            if controller is not None
                            # Only the explicitly offline fixture reaches this branch;
                            # its deterministic units are never native counter evidence.
                            else math.ceil(sum(len(message["content"].encode()) for message in messages) / 4)
                        )

                    prompt = (
                        runtime._initial_prompt(example)
                        if initial
                        else self.trainer.gamma.render(component, **{**fields, "receiver": item["agent_id"]})
                    )
                    if item["evidence_role"] == "verification":
                        prompt, _ = compatibility_probe_prompt(
                            item["shape"],
                            count_input,
                            math.floor(self.config.resources.caps(item["call_kind"])[0] * item["target_input_fraction"]),
                        )
                    admitted = count_input(prompt)
                    if controller is not None:
                        controller.preflight(compose(prompt), item["call_kind"])
                    record["prepared_request"] = {
                        "probe": copy.deepcopy(item),
                        "effective_input": copy.deepcopy(compose(prompt)),
                        "admission_input_bound": admitted,
                    }
                    self.trainer._checkpoint()
                    request = AgentRequest(
                        AgentCallKind(item["call_kind"]),
                        item["agent_id"],
                        prompt,
                        component_key,
                        context=context,
                        max_repair_generations=0,
                    )
                    entry_start = len(self.expenditure_ledger.entries) if self.expenditure_ledger is not None else 0
                    try:
                        output = await runtime._call(request)
                    finally:
                        if runtime._call_records:
                            entries = (
                                self.expenditure_ledger.entries[entry_start:]
                                if self.expenditure_ledger is not None
                                else ()
                            )
                            receipt = {
                                **copy.deepcopy(runtime._call_records[-1]),
                                "probe": copy.deepcopy(item),
                                "admission_input_bound": admitted,
                                "assessment_receipts": [compatibility_attempt_assessment(entry) for entry in entries],
                            }
                            record["requests"].append(receipt)
                            record.pop("prepared_request", None)
                    if output.error is not None or output.validation_exhausted:
                        receipt["protocol_validated"] = False
                        raise WorkflowExecutionFailure("synthetic backend compatibility generation failed")
                    validator = (
                        runtime._validate_role_output
                        if item["call_kind"] == "role"
                        else runtime._answer_context_validator(example, example.candidates, "synthetic compatibility")
                    )
                    _, protocol_error = validator(output)
                    receipt["protocol_validated"] = protocol_error is None
                    receipt["protocol_error"] = protocol_error
                    if protocol_error is not None:
                        raise WorkflowExecutionFailure("synthetic backend response violated the structured protocol")
                    if any(
                        state not in {"validated", "not_generated"}
                        for row in receipt["assessment_receipts"]
                        for state in row["checks"].values()
                    ):
                        if self.expenditure_ledger is not None:
                            self.expenditure_ledger.halt(
                                "synthetic compatibility receipt has failed or unavailable required evidence"
                            )
                        raise WorkflowExecutionFailure(
                            "synthetic compatibility receipt has failed or unavailable required evidence"
                        )
                    self.trainer._checkpoint()
                evidence = PromptOccurrence(
                    component,
                    role_prompt,
                    '{"feedback":"Two plus two is five.","status":"complete"}',
                    "no",
                    "yes",
                    {
                        "source": "synthetic_compatibility",
                        "not_a_training_trajectory": True,
                    },
                    0.0,
                    query=example.query,
                    example_id=example.example_id,
                    split="synthetic",
                    generation_id="compatibility-fixed-failure",
                )
                propose = getattr(
                    self.trainer.prompt_optimizer.proposer,
                    "propose_compatibility",
                    None,
                )
                if propose is None:
                    raise ValueError("compatibility requires an explicit synthetic-only TextGrad entry point")
                entry_start = len(self.expenditure_ledger.entries) if self.expenditure_ledger is not None else 0
                proposal = await propose(
                    self.trainer.gamma.components[component],
                    (evidence,),
                    iteration_id=f"compatibility:{expected['fingerprint']}",
                )
                record["proposal"] = asdict(proposal)
                entries = self.expenditure_ledger.entries[entry_start:] if self.expenditure_ledger is not None else ()
                record["textgrad_receipts"] = [compatibility_attempt_assessment(entry) for entry in entries]
                record["status"] = "failed" if proposal.error else "complete"
                record["committed_prompt_updates"] = 0
        except Exception as error:
            record["status"], record["error"] = (
                "failed",
                f"{type(error).__name__}: {error}",
            )
        after = parameter_receipt()
        record["parameter_receipts"].update(after=after, unchanged=before == after)
        if before != after:
            raise ValueError("compatibility check changed numerical parameters or accepted prompts")
        record["assessment"] = compatibility_report_assessment(
            record["requests"], record.get("textgrad_receipts", ()), self.config.agent_ids
        )
        if any(
            state not in {"validated", "not_generated"}
            for row in record.get("textgrad_receipts", ())
            for state in row["checks"].values()
        ):
            record["status"] = "failed"
            if self.expenditure_ledger is not None:
                self.expenditure_ledger.halt(
                    "TextGrad compatibility receipt has failed or unavailable required evidence"
                )
        record["attempts_used"] = (
            sum(item["stage"] == "compatibility" for item in self.expenditure_ledger.entries)
            if self.expenditure_ledger is not None
            else sum(item["usage"]["model_calls"] for item in record["requests"])
        )
        record["completion_reason"] = (
            "declared_pass_finished" if record["status"] == "complete" else "stopped_on_first_failure_or_resource_limit"
        )
        record["expenditure_by_stage"] = self.expenditure_report()
        self.trainer._checkpoint()
        return copy.deepcopy(record)

    async def pilot_safety(self) -> Any:
        from .calibration import collect_pilot_observation, calibrate_safety
        from ..models.policies import RoutingRolloutIdentity

        self.require_stage_authorization("pilot")
        if self.pilot_plan is None:
            raise ValueError("a predeclared training-only pilot plan is required")
        if self.trainer.state.phase not in {"unstarted", "pilot", "pilot_complete"}:
            raise ValueError("engineering pilot must precede development")
        if self.expenditure_ledger is not None:
            self.expenditure_ledger.stage = "pilot"

        def current_halt() -> Any:
            if ledger_stop_reason(self.expenditure_ledger):
                return copy.deepcopy(ledger_stop_reason(self.expenditure_ledger))
            if self.safety_record is not None:
                return self.safety_record.operational_halt
            return None

        def save_report(halt: Any = None, *, safe_boundary: bool = True, checkpoint: bool = True) -> None:
            self.safety_record = calibrate_safety(
                self.pilot_plan, self.pilot_observations, self.config.safety, operational_halt=halt
            )
            if checkpoint:
                self.trainer._checkpoint(safe_boundary=safe_boundary)

        if current_halt() is not None:
            halt = current_halt()
            save_report(halt)
            raise WorkflowExecutionFailure(str(halt.get("reason", "pilot campaign halted")))
        if self.safety_record is None or not self.safety_record.execution_complete:
            examples = self.dataset.select("train", self.pilot_plan.ordered_example_ids)
            observations = self.pilot_observations
            for index, example in enumerate(examples):
                if index < len(observations):
                    continue
                if current_halt() is not None:
                    halt = current_halt()
                    save_report(halt)
                    raise WorkflowExecutionFailure(str(halt.get("reason", "pilot campaign halted")))
                from ..infrastructure.shutdown import raise_if_stopping
                from ..infrastructure.failure_policy import cancellation_cause

                raise_if_stopping()
                runtime = self._runtime()
                try:
                    with torch.no_grad():
                        trajectory = await runtime.run(
                            example,
                            routing_mode=RoutingMode.TRAIN,
                            routing_identity=RoutingRolloutIdentity(self.pilot_plan.seed, example.example_id, index),
                            partition="train",
                            generation_context=GenerationContext(self.pilot_plan.seed, "pilot", example.example_id),
                            fixed_horizon=self.pilot_plan.horizon,
                        )
                except asyncio.CancelledError as exc:
                    partial = getattr(runtime, "last_trajectory", None)
                    if partial is not None:
                        observations.append(collect_pilot_observation(partial))
                    halt = {
                        "reason": cancellation_cause(exc),
                        "example_id": example.example_id,
                        "status": "interrupted",
                        "unfinished_workflow": True,
                        "partial_progress_available": partial is not None,
                    }
                    from ..infrastructure.shutdown import note_cleanup_failure

                    if self.expenditure_ledger is not None:
                        try:
                            await self.expenditure_ledger.flush_durable()
                        except Exception as error:
                            note_cleanup_failure(exc, "pilot accounting flush", error)
                    try:
                        # The trainer/stage publishes one bounded unsafe
                        # checkpoint including this in-memory partial report.
                        save_report(halt, safe_boundary=False, checkpoint=False)
                    except Exception as error:
                        note_cleanup_failure(exc, "pilot interrupted checkpoint", error)
                    raise
                observations.append(collect_pilot_observation(trajectory))
                status = getattr(trajectory, "status", None)
                halt = current_halt() or getattr(trajectory, "operational_halt", None)
                if status in FATAL_WORKFLOW_STATUSES:
                    halt = halt or {
                        "reason": trajectory.error or status,
                        "status": status,
                        "example_id": example.example_id,
                    }
                save_report(halt)
                if halt is not None:
                    raise WorkflowExecutionFailure(
                        trajectory.error or str(halt.get("reason", "pilot campaign halted")),
                        budget_exhausted=status == "budget-exhausted",
                    )
        return self.safety_record

    async def final_test(self, checkpoint_path: str | Path) -> FinalTestRecord:
        """Predict from stripped test inputs, then score; never select or update a model."""

        if self.final_test_plan is None:
            raise ValueError("final-test executions must be declared before training")
        parent_profile = self._has_extension_parent_profile()
        if parent_profile:
            self._require_selected_extension()
        if self.trainer.state.phase != "complete" or (self.safety_record is None and not parent_profile) or self.termination_record is None:
            raise ValueError("final testing requires completed joint checkpoint selection and frozen calibrations")
        outgoing, incoming = self.config.capacity.resolve(self.config.n_agents)
        selected = self.termination_record.configuration_for_test(
            agent_count=self.config.n_agents,
            outgoing_capacity=outgoing,
            incoming_capacity=incoming,
            routing_labels=tuple(scheme.value for scheme in self.config.scheme_order),
        )
        if selected != self.config.termination or (not parent_profile and self.safety_record.configuration != self.config.safety):
            raise ValueError("final-test runtime does not match the frozen calibrated configuration")
        checkpoint_bytes = Path(checkpoint_path).read_bytes()
        checkpoint_sha256 = hashlib.sha256(checkpoint_bytes).hexdigest()
        self.require_stage_authorization("test", checkpoint_sha256=checkpoint_sha256)
        payload = load_checkpoint(checkpoint_path)
        if payload.get("trainer_state", {}).get("phase") != "complete":
            raise ValueError("final test requires a completed selected checkpoint file")
        saved_config = copy.deepcopy(payload["config"])
        active_config = self.config.to_dict()
        saved_config["training"].pop("max_outer_cycles", None)
        active_config["training"].pop("max_outer_cycles", None)
        if saved_config != active_config or payload["gamma"] != self.trainer.gamma.to_dict():
            raise ValueError("final-test configuration or prompts differ from the selected checkpoint")
        saved_hooks = payload.get("hook_state", {})
        if any(
            _canonical(saved_hooks.get(key)) != _canonical(self.state_dict().get(key))
            for key in (
                "condition",
                "safety_record",
                "termination_record",
                "extension_parent_acceptance",
            )
        ):
            raise ValueError("final-test protocol differs from the selected checkpoint")

        def check_parameters() -> None:
            for name, module in (
                ("alpha", self.trainer.alpha),
                ("weighting", self.trainer.weighting),
            ):
                active = module.state_dict()
                saved = payload[name]
                if set(active) != set(saved) or any(
                    not torch.equal(value.detach().cpu(), saved[key]) for key, value in active.items()
                ):
                    raise ValueError("final-test numerical parameters differ from the selected checkpoint")

        check_parameters()
        runtime = self._runtime()
        if self.expenditure_ledger is not None:
            self.expenditure_ledger.stage = "test"
        modules = (runtime.routing_policy, runtime.weighting_model)
        modes = tuple(module.training for module in modules)
        predictions = []
        stop_status = "campaign-halted" if ledger_stop_reason(self.expenditure_ledger) else None
        stop_error = None if stop_status is None else str(ledger_stop_reason(self.expenditure_ledger))
        try:
            for module in modules:
                module.eval()
            with torch.no_grad():
                for seed in self.final_test_plan.run_seeds:
                    for example in self.dataset.select("test", self.final_test_plan.ordered_example_ids):
                        if stop_status is not None:
                            predictions.append(
                                TestPrediction(
                                    example.example_id,
                                    seed,
                                    None,
                                    None if example.gold_answer is None else False,
                                    None,
                                    0,
                                    0,
                                    (),
                                    False,
                                    "unattempted-" + stop_status,
                                    None,
                                    0.0,
                                    0,
                                    0,
                                    stop_error,
                                )
                            )
                            continue
                        stripped = replace(
                            example,
                            gold_answer=None,
                            metadata={
                                "original_query_id": str(example.metadata.get("original_query_id", example.example_id)),
                            },
                        )
                        async def execute(index: int, identity: str) -> Trajectory:
                            del index, identity
                            return await self._runtime().run(
                                stripped, routing_mode=RoutingMode.EVALUATION,
                                generation_context=GenerationContext(seed, "test", example.example_id),
                                partition="test",
                            )
                        if self.config.workflow_recovery.enabled:
                            slot_id = f"test:{checkpoint_sha256}:{seed}:{example.example_id}"
                            async def persist_test() -> None:
                                if self.expenditure_ledger is not None:
                                    await self.expenditure_ledger.record_progress(
                                        "workflow-recovery", copy.deepcopy(self.recovery_slots[slot_id])
                                    )
                            try:
                                trajectory, _ = await self._recover_workflow(
                                    slot_id=slot_id, operation="test", execute=execute,
                                    allocate=lambda index: f"{slot_id}:{index}", persist=persist_test,
                                )
                            except WorkflowRecoveryStopped as stopped:
                                failed = stopped.trajectory
                                stop_status = failed.status if failed.status in FATAL_WORKFLOW_STATUSES else "campaign-halted"
                                stop_error = failed.error
                                trajectory = stopped.selected
                        else:
                            trajectory = await execute(0, "unused")
                        if trajectory.example != stripped:
                            raise ValueError("final-test prediction requires the exact stripped input")
                        status = getattr(trajectory, "status", "completed")
                        if fatal_workflow(trajectory, self.expenditure_ledger):
                            stop_status = status if status in FATAL_WORKFLOW_STATUSES else "campaign-halted"
                            stop_error = getattr(trajectory, "error", None)
                        predictions.append(
                            TestPrediction(
                                example.example_id,
                                seed,
                                trajectory.final_answer,
                                (
                                    None
                                    if example.gold_answer is None
                                    else trajectory.final_answer == example.gold_answer
                                ),
                                (
                                    sum(session.normalized_cost for session in trajectory.sessions)
                                    if trajectory.completed and trajectory.sessions and all(session.normalized_cost is not None for session in trajectory.sessions)
                                    else None
                                ),
                                trajectory.usage.model_calls,
                                trajectory.usage.total_tokens,
                                tuple(
                                    int(item["seed"])
                                    for item in trajectory.call_records
                                    if item.get("seed") is not None
                                ),
                                all(bool(getattr(agent, "supports_seed", False)) for agent in runtime.agents.values()),
                                stop_status or status,
                                _sum_available(session.usage.monetary_cost for session in trajectory.sessions),
                                trajectory.usage.paid_monetary_cost,
                                trajectory.usage.input_tokens,
                                trajectory.usage.output_tokens,
                                getattr(trajectory, "error", None),
                                copy.deepcopy(getattr(trajectory, "workflow_recovery", {})),
                            )
                        )
        finally:
            for module, mode in zip(modules, modes, strict=True):
                module.train(mode)
        if hashlib.sha256(Path(checkpoint_path).read_bytes()).hexdigest() != checkpoint_sha256:
            raise ValueError("selected checkpoint changed during final-test execution")
        check_parameters()
        return FinalTestRecord(
            self.final_test_plan,
            checkpoint_sha256,
            self.protocol_fingerprint,
            tuple(predictions),
            self.expenditure_report(),
        )

    def expenditure_report(self) -> dict[str, Any]:
        if self.expenditure_ledger is None:
            return {}
        result = {}
        for stage in ("compatibility", "pilot", "development", "test"):
            entries = tuple(item for item in self.expenditure_ledger.entries if item["stage"] == stage)
            charges = tuple(self.expenditure_ledger.effective_charge(item) for item in entries)
            result[stage] = {
                "physical_requests": len(entries),
                "actual_paid_usd": _sum_available(charges),
                "known_paid_usd": sum(charge for charge in charges if charge is not None),
                "outstanding_reserved_usd": sum(
                    item["reserved"] for item, charge in zip(entries, charges) if charge is None
                ),
            }
        return result

    async def calibrate_termination(self) -> Any:
        from .calibration import calibrate_termination

        if self.termination_plan is None:
            raise ValueError("a predeclared system-validation termination search is required")
        self.require_development(bootstrap_only=False, weighting_supplement=False)
        if not self._has_extension_parent_profile() and (self.safety_record is None or (self.authorization_required and not self.safety_record.approved)):
            raise ValueError("freeze explicitly approved training-only safeguards before termination calibration")
        if self.config.termination != self.termination_plan.bootstrap_configuration:
            raise ValueError("calibration bootstrap configuration differs from the executed provisional one")

        async def evaluate_shared(
            plan: Any,
        ) -> tuple[tuple[EvaluationRecord, ...], tuple[dict[str, Any], ...]]:
            runtime = self._runtime()
            results: list[list[ExampleEvaluation]] = [[] for _ in plan.candidates]
            shared_records = []
            key = hashlib.sha256(_canonical({
                "condition": self._recovery_condition(), "plan": asdict(plan),
            }).encode()).hexdigest()
            selected_queries = self.calibration_query_cache.setdefault(key, {})
            for example in self.dataset.select(plan.validation.partition, plan.validation.ordered_example_ids):
                cached = selected_queries.get(example.example_id)
                if cached is not None:
                    for items, projection in zip(results, cached["examples"], strict=True):
                        items.append(ExampleEvaluation(**projection))
                    shared_records.append(copy.deepcopy(cached["shared_record"]))
                    continue
                async def execute(index: int, identity: str) -> Any:
                    del index, identity
                    return await self._runtime().run_shared_prefix(
                        example, plan.candidates,
                        generation_context=plan.validation.generation_context(example.example_id), partition="sys-val",
                    )
                recovery = {}
                if self.config.workflow_recovery.enabled:
                    slot_id = f"calibration:{key}:{example.example_id}"
                    result, recovery = await self._recover_workflow(
                        slot_id=slot_id, operation="shared-prefix", execute=execute,
                        allocate=lambda index: f"{slot_id}:{index}",
                    )
                else:
                    result = await execute(0, "unused")
                if tuple(result.configurations) != tuple(plan.candidates) or len(result.prefixes) != len(
                    plan.candidates
                ):
                    raise ValueError("runtime changed the declared shared-prefix candidate order")
                replay = all(bool(getattr(agent, "supports_seed", False)) for agent in runtime.agents.values())
                for index, trajectory in enumerate(result.prefixes):
                    trajectory.workflow_recovery = copy.deepcopy(recovery)
                    results[index].append(_trajectory_evaluation(
                        trajectory, seed_replay_supported=replay,
                        allow_incomplete=self.config.workflow_recovery.enabled,
                    ))
                shared_records.append(
                    {
                        "example_id": example.example_id,
                        "workflow_recovery": recovery,
                        "call_records": copy.deepcopy(result.shared.call_records),
                        "session_costs": [session.normalized_cost for session in result.shared.sessions],
                        "candidate_stopping_sessions": [len(trajectory.sessions) for trajectory in result.prefixes],
                        "physical_usage": asdict(result.shared.usage),
                        "boundaries": [
                            {
                                "session": session.session,
                                "answer": session.weighted_answer,
                                "distribution": dict(session.candidate_scores),
                            }
                            for session in result.shared.sessions
                        ],
                    }
                )
                selected_queries[example.example_id] = {
                    "examples": [asdict(items[-1]) for items in results],
                    "shared_record": copy.deepcopy(shared_records[-1]),
                }
                # Publish every candidate's selected projection together before
                # calling the next query. A resumed query cannot consume a fresh
                # replacement allowance or splice candidate realizations.
                await self._publish_recovery(safe_boundary=True)
            records = tuple(EvaluationRecord(plan.validation, tuple(items)) for items in results)
            if any(record.score is None for record in records):
                self.evaluation_records.extend(record.to_dict() for record in records)
            return records, tuple(shared_records)

        self.termination_record = await calibrate_termination(self.termination_plan, evaluate_shared)
        self.trainer.apply_configuration(
            replace(self.config, termination=self.termination_record.selected_configuration)
        )
        baseline = self.termination_record.selected_evaluation
        if baseline.plan.fingerprint != self.plans["system"].fingerprint:
            raise ValueError("checkpoint-zero baseline must match the declared system validation plan")
        self.evaluation_cache[self._cache_key(self.plans["system"])] = baseline
        self.evaluation_records.append(baseline.to_dict())
        return self.termination_record
