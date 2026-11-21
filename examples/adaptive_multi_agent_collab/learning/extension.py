"""Explicitly budgeted incremental adaptation of appended numerical blocks.

The controller uses the ordinary fresh-trajectory trainer and paired TextGrad
procedure. Parent-mode numerical retention and enlarged-system selection are
separate records. Constructing a plan or controller authorizes no model call.
"""
from __future__ import annotations

import copy
import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

import torch

from ..config import PhaseConfig, PromptPhaseConfig, TerminationConfig
from .evaluation import EvaluationRecord, WorkflowExecutionFailure
from .training import CheckpointManager, _fingerprint, evaluating, frozen

EXTENSION_PROTOCOL = "append_blocks_restricted_fresh_trajectory_v1"


@dataclass(frozen=True)
class ExtensionPlan:
    """Every scientific bound/tolerance is supplied before any adaptation."""

    parent_condition: str
    extension_condition: str
    train_ids: tuple[str, ...]
    component_validation_ids: tuple[str, ...]
    system_validation_ids: tuple[str, ...]
    weighting_phase: PhaseConfig
    routing_phase: PhaseConfig
    spending_limit_usd: float
    physical_attempt_limit: int
    validation_attempt_limit: int
    calibration_attempt_limit: int
    calibration_candidates: tuple[TerminationConfig, ...]
    acceptance_min_improvement: float
    retained_accuracy_tolerance: float
    parent_value_tolerance: float
    min_scheme_coverage: Mapping[str, int]
    min_agent_incoming_coverage: Mapping[int, int]
    min_agent_outgoing_coverage: Mapping[int, int]
    min_free_zero_coverage: int
    prompt_components: tuple[str, ...]
    prompt_phase: PromptPhaseConfig
    escalation_scopes: tuple[Mapping[str, Any], ...]
    protocol: str = EXTENSION_PROTOCOL

    def __post_init__(self) -> None:
        if self.protocol != EXTENSION_PROTOCOL or not self.parent_condition or not self.extension_condition:
            raise ValueError("extension requires current protocol and both condition identities")
        groups = [set(getattr(self, name)) for name in ("train_ids", "component_validation_ids", "system_validation_ids")]
        for name, values in zip(("train_ids", "component_validation_ids", "system_validation_ids"), groups):
            original = tuple(getattr(self, name))
            if not values or len(original) != len(values) or not all(isinstance(item, str) and item for item in values):
                raise ValueError("extension query minibatches must be nonempty ordered unique IDs")
            object.__setattr__(self, name, original)
        if any(groups[left] & groups[right] for left in range(3) for right in range(left + 1, 3)):
            raise ValueError("extension train/component/system queries must remain disjoint")
        for name in ("spending_limit_usd", "acceptance_min_improvement", "retained_accuracy_tolerance", "parent_value_tolerance"):
            value = getattr(self, name)
            if not math.isfinite(value) or value < 0 or (name == "spending_limit_usd" and value == 0):
                raise ValueError("extension bounds and tolerances must be finite and nonnegative")
        for name in ("physical_attempt_limit", "validation_attempt_limit", "calibration_attempt_limit", "min_free_zero_coverage"):
            if not isinstance(getattr(self, name), int) or getattr(self, name) < 0:
                raise ValueError("extension attempt/coverage bounds must be nonnegative integers")
        if not self.calibration_candidates or len(set(self.calibration_candidates)) != len(self.calibration_candidates):
            raise ValueError("declare a nonempty finite unique calibration list")
        if self.calibration_attempt_limit < len(self.system_validation_ids):
            raise ValueError("calibration budget must cover its declared shared-prefix query plan")
        for name in ("min_scheme_coverage", "min_agent_incoming_coverage", "min_agent_outgoing_coverage"):
            values = dict(getattr(self, name))
            if any(not isinstance(value, int) or value < 0 for value in values.values()):
                raise ValueError("coverage requirements must be nonnegative integers")
            object.__setattr__(self, name, values)
        if bool(self.prompt_components) != (self.prompt_phase.max_iterations > 0):
            raise ValueError("optional prompt adaptation requires both explicit components and a finite budget")
        if self.routing_phase.max_attempts != self.routing_phase.max_updates:
            raise ValueError("restricted alpha retains one fresh attempt per scheduled update slot")
        for scope in self.escalation_scopes:
            required = {"name", "alpha_parameters", "weighting_parameters", "weighting_phase", "routing_phase", "spending_limit_usd", "physical_attempt_limit", "validation_attempt_limit", "calibration_attempt_limit"}
            optional = {"prompt_components", "prompt_phase"}
            if (set(scope) not in (required, required | optional) or not scope["name"]
                    or not math.isfinite(float(scope["spending_limit_usd"])) or float(scope["spending_limit_usd"]) <= 0):
                raise ValueError("escalation requires exact trainable names and predeclared finite phase/spending limits")
            if any(not isinstance(scope[key], int) or scope[key] < 1 for key in ("physical_attempt_limit", "validation_attempt_limit", "calibration_attempt_limit")):
                raise ValueError("escalation requires finite positive attempt limits")
            PhaseConfig(**scope["weighting_phase"])
            routing = PhaseConfig(**scope["routing_phase"])
            if routing.max_attempts != routing.max_updates:
                raise ValueError("escalated alpha retains one fresh attempt per scheduled update slot")
            if "prompt_components" in scope:
                prompt = PromptPhaseConfig(**scope["prompt_phase"])
                if bool(scope["prompt_components"]) != (prompt.max_iterations > 0):
                    raise ValueError("expanded prompt scope requires explicit components and a finite phase")
        if len({scope["name"] for scope in self.escalation_scopes}) != len(self.escalation_scopes):
            raise ValueError("escalation scope identities must be unique")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ExtensionPlan:
        data = copy.deepcopy(dict(value))
        for name in ("weighting_phase", "routing_phase"):
            data[name] = PhaseConfig(**data[name])
        data["prompt_phase"] = PromptPhaseConfig(**data["prompt_phase"])
        data["calibration_candidates"] = tuple(TerminationConfig(**item) for item in data["calibration_candidates"])
        for name in ("train_ids", "component_validation_ids", "system_validation_ids", "prompt_components", "escalation_scopes"):
            data[name] = tuple(data[name])
        for name in ("min_agent_incoming_coverage", "min_agent_outgoing_coverage"):
            data[name] = {int(key): item for key, item in data[name].items()}
        return cls(**data)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def fingerprint(self) -> str:
        return _fingerprint(self.to_dict())


def named_optimizer_state(module: torch.nn.Module, optimizer: torch.optim.Optimizer) -> dict[str, Any]:
    """Semantic names preserve inherited Adam moments and counters exactly."""
    names = {id(parameter): name for name, parameter in module.named_parameters()}
    groups = []
    for group in optimizer.param_groups:
        if any(id(parameter) not in names for parameter in group["params"]):
            raise ValueError("optimizer owns a parameter outside its declared model")
        groups.append({**{key: copy.deepcopy(value) for key, value in group.items() if key != "params"},
                       "params": [names[id(parameter)] for parameter in group["params"]]})
    return {"groups": groups, "state": {names[id(parameter)]: copy.deepcopy(value)
                                        for parameter, value in optimizer.state.items()}}


def _tensor_equal(left: Any, right: Any) -> bool:
    if isinstance(left, torch.Tensor):
        return isinstance(right, torch.Tensor) and torch.equal(left, right)
    if isinstance(left, Mapping):
        return isinstance(right, Mapping) and set(left) == set(right) and all(_tensor_equal(left[k], right[k]) for k in left)
    if isinstance(left, (tuple, list)):
        return isinstance(right, type(left)) and len(left) == len(right) and all(_tensor_equal(a, b) for a, b in zip(left, right))
    return left == right


def block_optimizer(module: torch.nn.Module, names: Sequence[str], config: Any,
                    *, inherited: Mapping[str, Any] | None = None) -> torch.optim.Adam:
    selected = tuple(names)
    parameters = dict(module.named_parameters())
    if not selected or len(selected) != len(set(selected)) or set(selected) - parameters.keys():
        raise ValueError("restricted scope requires unique existing parameter-block names")
    for name, parameter in parameters.items():
        parameter.requires_grad_(name in selected)
        parameter.grad = None
    optimizer = torch.optim.Adam([parameters[name] for name in selected], lr=config["lr"],
                                 betas=tuple(config["betas"]), eps=config["eps"],
                                 weight_decay=config["weight_decay"], amsgrad=config["amsgrad"],
                                 maximize=config["maximize"])
    for name in selected:
        if inherited is not None and name in inherited["state"]:
            optimizer.state[parameters[name]] = copy.deepcopy(inherited["state"][name])
    return optimizer


def scope_configuration(config: Any, state: Mapping[str, Any], plan: ExtensionPlan) -> Any:
    """Reconstruct only an exactly approved predeclared scope for strict loading."""
    if state.get("plan_fingerprint") != plan.fingerprint:
        raise ValueError("scope checkpoint differs from its registered extension plan")
    name = state.get("active_scope")
    if name is None:
        return config
    scope = next((item for item in plan.escalation_scopes if item["name"] == name), None)
    approval = state.get("scope_authorization", {})
    if (scope is None or approval.get("decision") != "approve" or not approval.get("approval_source")
            or approval.get("scope_name") != name or approval.get("scope_fingerprint") != _fingerprint(scope)
            or approval.get("plan_fingerprint") != plan.fingerprint):
        raise ValueError("scope checkpoint lacks its exact registered approval")
    termination = TerminationConfig(**state["scope_entry_termination"])
    if termination not in plan.calibration_candidates:
        raise ValueError("scope-entry stopping tuple is outside the declared candidates")
    return replace(config, termination=termination, training=replace(config.training,
        weighting_phase=PhaseConfig(**scope["weighting_phase"]), routing_phase=PhaseConfig(**scope["routing_phase"]),
        prompt_phase=PromptPhaseConfig(**scope["prompt_phase"]) if "prompt_phase" in scope else plan.prompt_phase))


@dataclass
class ExtensionProgress:
    phase: str = "parent-retention"
    validation_attempts: int = 0
    calibration_attempts: int = 0
    unique_training_ids: list[str] = field(default_factory=list)
    schemes: dict[str, int] = field(default_factory=dict)
    incoming: dict[int, int] = field(default_factory=dict)
    outgoing: dict[int, int] = field(default_factory=dict)
    free_zero: int = 0
    baseline: dict[str, Any] | None = None
    final: dict[str, Any] | None = None
    parent: dict[str, Any] | None = None
    retention: dict[str, Any] | None = None
    calibration: dict[str, Any] | None = None
    accepted: bool | None = None
    reason: str | None = None


class _ScopedHooks:
    """Use real hooks, with partition, budget and coverage checks at their boundary."""
    def __init__(self, owner: RestrictedAdaptation, hooks: Any):
        self.owner, self.inner = owner, hooks

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    async def trajectory(self, component: str, split: str, *, routing_identity: Any) -> Any:
        self.owner.check_spending()
        result = await self.inner.trajectory(component, split, routing_identity=routing_identity)
        identifier = result.example.example_id
        if split != "train" or identifier not in self.owner.plan.train_ids:
            raise ValueError("extension attempted a query outside its declared training plan")
        progress = self.owner.progress
        if identifier not in progress.unique_training_ids:
            progress.unique_training_ids.append(identifier)
        for session in result.sessions:
            for decision in session.routing.decisions:
                label = getattr(decision.scheme, "value", decision.scheme)
                if label == "0":
                    progress.free_zero += int(decision.free)
                else:
                    progress.schemes[label] = progress.schemes.get(label, 0) + 1
                    sender, receiver = decision.edge
                    progress.outgoing[sender] = progress.outgoing.get(sender, 0) + 1
                    progress.incoming[receiver] = progress.incoming.get(receiver, 0) + 1
        self.owner.check_spending()
        return result

    def _admit_validation(self, count: int) -> None:
        progress = self.owner.progress
        if progress.validation_attempts + count > self.owner.limit("validation_attempt_limit"):
            raise WorkflowExecutionFailure("extension validation attempt budget exhausted", budget_exhausted=True)
        self.owner.check_spending()
        progress.validation_attempts += count

    async def validate(self, component: str) -> EvaluationRecord:
        self._admit_validation(len(self.owner.plan.component_validation_ids))
        result = await self.inner.validate(component)
        if tuple(result.plan.ordered_example_ids) != self.owner.plan.component_validation_ids:
            raise ValueError("extension validation plan changed")
        return result

    async def evaluate_prompts(self, collection: Any, batch: Any) -> EvaluationRecord:
        self._admit_validation(len(self.owner.plan.component_validation_ids))
        return await self.inner.evaluate_prompts(collection, batch)

    async def system_validation(self) -> EvaluationRecord:
        self._admit_validation(len(self.owner.plan.system_validation_ids))
        result = await self.inner.system_validation()
        if tuple(result.plan.ordered_example_ids) != self.owner.plan.system_validation_ids:
            raise ValueError("extension system validation plan changed")
        return result


class RestrictedAdaptation:
    """Bounded W→alpha→optional prompts→validation→recalibration, without replay."""

    def __init__(self, trainer: Any, expansion: Any, plan: ExtensionPlan, *,
                 parent_alpha: torch.nn.Module, parent_weighting: torch.nn.Module,
                 parent_alpha_optimizer: torch.optim.Optimizer,
                 parent_weighting_optimizer: torch.optim.Optimizer):
        self.trainer, self.expansion, self.plan = trainer, expansion, plan
        if trainer.alpha is not expansion.alpha or trainer.weighting is not expansion.weighting:
            raise ValueError("extension trainer must own the declared expanded models")
        self.parent_models = (parent_alpha, parent_weighting)
        self.parent_optimizers = (parent_alpha_optimizer, parent_weighting_optimizer)
        self.inherited = {"alpha": named_optimizer_state(parent_alpha, parent_alpha_optimizer),
                          "weighting": named_optimizer_state(parent_weighting, parent_weighting_optimizer)}
        self.optimizer_bank = copy.deepcopy(self.inherited)
        self.parent_values = {"alpha": copy.deepcopy(parent_alpha.state_dict()),
                              "weighting": copy.deepcopy(parent_weighting.state_dict())}
        self.permitted = {"alpha": tuple(expansion.alpha_parameters), "weighting": tuple(expansion.weighting_parameters)}
        training = trainer.config.training
        self.optimizer_config = {"alpha": {"lr": training.routing_learning_rate},
                                 "weighting": {"lr": training.weighting_learning_rate}}
        for settings in self.optimizer_config.values():
            settings.update(betas=(training.adam_beta1, training.adam_beta2), eps=training.adam_epsilon,
                            weight_decay=training.weight_decay, amsgrad=training.adam_amsgrad,
                            maximize=training.adam_maximize)
        trainer.alpha_optimizer = block_optimizer(trainer.alpha, self.permitted["alpha"], self.optimizer_config["alpha"])
        trainer.weighting_optimizer = block_optimizer(trainer.weighting, self.permitted["weighting"], self.optimizer_config["weighting"])
        trainer.prompt_components = plan.prompt_components
        trainer.config = trainer.declared_config = replace(trainer.config, training=replace(
            training, weighting_phase=plan.weighting_phase, routing_phase=plan.routing_phase,
            prompt_phase=plan.prompt_phase))
        if trainer.config.termination not in plan.calibration_candidates:
            raise ValueError("extension calibration must contain the inherited termination tuple")
        self.progress = ExtensionProgress()
        trainer.extension_controller = self
        self.hooks: Any = None
        self._spending_start: float | None = None
        self.active_scope = None
        self.scope_entry_termination = trainer.config.termination
        self.scope_authorization = None
        self.scope_history = []
        self.initial_retention = None
        self._frozen_values = {key: {name: value.detach().clone() for name,value in model.named_parameters() if name not in self.permitted[key]}
                               for key,model in (("alpha",trainer.alpha),("weighting",trainer.weighting))}
        self.assert_frozen()

    def assert_frozen(self) -> None:
        for component, model, parent, optimizer in zip(
            ("alpha", "weighting"), (self.trainer.alpha, self.trainer.weighting),
            self.parent_models, self.parent_optimizers, strict=True
        ):
            current = model.state_dict()
            for name, tensor in self._frozen_values[component].items():
                if name not in self.permitted[component] and (name not in current or not torch.equal(tensor, current[name])):
                    raise ValueError("inherited parameter changed outside extension scope: " + name)
            if not _tensor_equal(named_optimizer_state(parent, optimizer), self.inherited[component]):
                raise ValueError("frozen inherited optimizer moments/counters changed")

    def select_escalation(self, name: str, authorization: Mapping[str, Any]) -> None:
        """Explicitly select one predeclared broader scope; never auto-unfreeze."""
        scope = next((item for item in self.plan.escalation_scopes if item["name"] == name), None)
        if scope is None:
            raise ValueError("undeclared escalation scope")
        expected = {"decision":"approve", "plan_fingerprint":self.plan.fingerprint,
                    "scope_fingerprint":_fingerprint(scope), "scope_name":name}
        if (any(authorization.get(key) != value for key,value in expected.items())
                or not authorization.get("approval_source")):
            raise ValueError("escalation requires separate exact declared-scope approval")
        if self.progress.phase != "complete" or self.active_scope == name:
            raise ValueError("escalation is permitted once, at a completed adaptation boundary")
        if any(item["scope"] == name for item in self.scope_history):
            raise ValueError("a completed escalation cannot be reset/replayed")
        models = {"alpha":self.trainer.alpha,"weighting":self.trainer.weighting}
        optimizers = {"alpha":self.trainer.alpha_optimizer,"weighting":self.trainer.weighting_optimizer}
        banks = {}
        for key,model in models.items():
            names = tuple(scope[key + "_parameters"])
            if not names or set(names) - dict(model.named_parameters()).keys():
                raise ValueError("escalation names must identify actual complete parameter blocks")
            banks[key] = copy.deepcopy(self.optimizer_bank[key])
            banks[key]["state"].update(named_optimizer_state(model,optimizers[key])["state"])
        prompt_components = tuple(scope.get("prompt_components", self.plan.prompt_components))
        if set(prompt_components) - set(self.trainer.gamma.components):
            raise ValueError("expanded prompt scope contains unregistered components")
        self.optimizer_bank = banks
        self.scope_history.append({"scope":self.active_scope,"progress":asdict(self.progress)})
        for key,model in models.items():
            self.permitted[key] = tuple(scope[key + "_parameters"])
            optimizer = block_optimizer(model,self.permitted[key],self.optimizer_config[key],inherited=banks[key])
            setattr(self.trainer,"alpha_optimizer" if key == "alpha" else "weighting_optimizer",optimizer)
        self._frozen_values = {key:{n:p.detach().clone() for n,p in m.named_parameters() if n not in self.permitted[key]}
                               for key,m in models.items()}
        self.active_scope, self.scope_authorization = name, copy.deepcopy(dict(authorization))
        self.scope_entry_termination = self.trainer.config.termination
        self.progress = ExtensionProgress()
        self.trainer.state.phase_progress = {}
        self.trainer.state.calibration_complete = False
        self.trainer.state.best_system_score = None
        self.trainer.best_system = None
        self.trainer.config = self.trainer.declared_config = replace(self.trainer.config,training=replace(
            self.trainer.config.training,weighting_phase=PhaseConfig(**scope["weighting_phase"]),
            routing_phase=PhaseConfig(**scope["routing_phase"]),
            prompt_phase=PromptPhaseConfig(**scope["prompt_phase"]) if "prompt_phase" in scope else self.plan.prompt_phase))
        self.trainer.prompt_components = prompt_components
        self._spending_start = None
        if self.hooks is not None:
            self.configure_hooks(self.hooks, clear_calibration=True)
        self.assert_frozen()

    def configure_checkpoint_scope(self, value: Mapping[str, Any]) -> None:
        """Rebuild a declared optimizer scope on a fresh controller before load."""
        name = value.get("active_scope")
        if name is None:
            return
        if self.progress.phase != "parent-retention" or self.active_scope is not None:
            raise ValueError("scope reconstruction requires a fresh isolated controller")
        if value.get("plan_fingerprint") != self.plan.fingerprint:
            raise ValueError("cannot reconstruct a scope from another extension plan")
        entry = TerminationConfig(**value["scope_entry_termination"])
        if entry not in self.plan.calibration_candidates:
            raise ValueError("escalation stopping tuple is outside the registered calibration list")
        self.trainer.config = replace(self.trainer.config, termination=entry)
        self.progress.phase = "complete"
        self.select_escalation(name,value.get("scope_authorization",{}))

    def configure_hooks(self, hooks: Any, *, clear_calibration: bool = False) -> None:
        """Keep each declared adaptation scope's calibration boundary explicit."""
        self.hooks = hooks
        if getattr(hooks, "termination_plan", None) is not None:
            hooks.termination_plan = replace(hooks.termination_plan, bootstrap_configuration=self.scope_entry_termination)
        if clear_calibration and hasattr(hooks, "termination_record"):
            hooks.termination_record = None

    def limit(self, name: str) -> Any:
        if self.active_scope is not None:
            scope = next(item for item in self.plan.escalation_scopes if item["name"] == self.active_scope)
            if name in scope:
                return scope[name]
        return getattr(self.plan,name)

    def check_spending(self) -> None:
        ledger = getattr(self.hooks, "expenditure_ledger", None)
        if ledger is None:
            if getattr(self.hooks, "authorization_required", True):
                raise ValueError("extension execution requires durable resource accounting")
            return
        spent = float(ledger.stage_summary("development")["occupied"])
        if self._spending_start is None:
            self._spending_start = spent
        if spent - self._spending_start >= self.limit("spending_limit_usd"):
            raise WorkflowExecutionFailure("extension spending budget exhausted", budget_exhausted=True)

    def coverage_complete(self) -> bool:
        checks = ((self.plan.min_scheme_coverage, self.progress.schemes),
                  (self.plan.min_agent_incoming_coverage, self.progress.incoming),
                  (self.plan.min_agent_outgoing_coverage, self.progress.outgoing))
        return all(actual.get(key, 0) >= required for expected, actual in checks for key, required in expected.items()) and self.progress.free_zero >= self.plan.min_free_zero_coverage

    def validate_coverage_registry(self, *, parent_agents: Sequence[int], agents: Sequence[int],
                                   parent_schemes: Sequence[str], schemes: Sequence[str]) -> None:
        """Every addition needs an explicit threshold, including an explicit zero."""
        new_agents = set(agents) - set(parent_agents)
        new_schemes = set(schemes) - set(parent_schemes)
        requirements = ((self.plan.min_scheme_coverage, new_schemes, set(schemes)),
                        (self.plan.min_agent_incoming_coverage, new_agents, set(agents)),
                        (self.plan.min_agent_outgoing_coverage, new_agents, set(agents)))
        if any(not added <= set(required) or set(required) - allowed for required, added, allowed in requirements):
            raise ValueError("coverage thresholds must explicitly name every new scheme and both directions of every new agent")

    async def run(self, hooks: Any, *, parent_result: EvaluationRecord,
                  parent_retention: Mapping[str, Any]) -> dict[str, Any]:
        if self.progress.phase == "complete":
            return self.report()
        self.configure_hooks(hooks)
        hooks.bind(self.trainer)
        if getattr(hooks, "active_data_plan", None) is not None:
            active = hooks.active_data_plan
            if (tuple(active.train_ids), tuple(active.component_validation_ids), tuple(active.system_validation_ids)) != (
                    self.plan.train_ids, self.plan.component_validation_ids, self.plan.system_validation_ids):
                raise ValueError("configure the exact registered extension subsets before execution")
        if tuple(parent_result.plan.ordered_example_ids) != self.plan.system_validation_ids or parent_result.score is None:
            raise ValueError("matching complete parent system results are required")
        retention = self.initial_retention or parent_retention
        if not retention.get("passed") or retention.get("tolerance") != self.plan.parent_value_tolerance:
            raise ValueError("parent-mode Q/logit retention must pass independently before enlarged validation")
        self.initial_retention = copy.deepcopy(dict(retention))
        if set(self.trainer.prompt_components) - set(self.trainer.gamma.components):
            raise ValueError("extension prompt scope contains unregistered components")
        wrapped = _ScopedHooks(self, hooks)
        self.trainer.hooks = wrapped
        self.progress.parent = parent_result.to_dict()
        self.progress.retention = copy.deepcopy(dict(retention))
        self.check_spending()
        if self.progress.phase == "parent-retention":
            with evaluating(self.trainer.alpha, self.trainer.weighting), torch.no_grad():
                baseline = await wrapped.system_validation()
            self.progress.baseline = baseline.to_dict()
            self.progress.phase = "weighting"
            self.trainer.state.phase_progress = {}
            self.trainer._checkpoint()
        for component in ("weighting", "routing"):
            if self.progress.phase != component:
                continue
            self.trainer.state.phase = component
            await self.trainer._numerical_phase(component)
            self.assert_frozen()
            self.progress.phase = "routing" if component == "weighting" else "prompt"
            self.trainer.state.phase_progress = {}
            self.trainer._checkpoint()
        if self.progress.phase == "prompt":
            if self.trainer.prompt_components:
                self.trainer.state.phase = "prompt"
                await self.trainer._prompt_phase()
            self.progress.phase = "validation"
            self.trainer.state.phase_progress = {}
            self.trainer._checkpoint()
        if self.progress.phase == "validation":
            with evaluating(self.trainer.alpha, self.trainer.weighting), torch.no_grad():
                result = await wrapped.system_validation()
            self.progress.final = result.to_dict()
            if result.score is None or not self.coverage_complete():
                self.progress.accepted = False
                self.progress.reason = "incomplete_evaluation_or_coverage"
                self.progress.phase = "complete"
                self.trainer.state.phase = "failed"
                self.trainer._checkpoint()
                return self.report()
            parent_accuracy = parent_result.accuracy
            accuracy = result.accuracy
            accepted = result.score >= parent_result.score + self.plan.acceptance_min_improvement and accuracy >= parent_accuracy - self.plan.retained_accuracy_tolerance
            if not accepted:
                self.progress.accepted, self.progress.reason, self.progress.phase = False, "validation_tolerance_not_met", "complete"
                self.trainer.state.phase = "failed"
                self.trainer._checkpoint()
                return self.report()
            self.progress.phase = "calibration"
        if self.progress.phase == "calibration":
            required = len(self.plan.system_validation_ids)
            if self.progress.calibration_attempts + required > self.limit("calibration_attempt_limit"):
                raise WorkflowExecutionFailure("extension calibration budget exhausted", budget_exhausted=True)
            self.progress.calibration_attempts += required
            self.check_spending()
            with evaluating(self.trainer.alpha, self.trainer.weighting), frozen(self.trainer.alpha, self.trainer.weighting), torch.no_grad():
                record = await hooks.calibrate_termination()
            self.progress.calibration = record.to_dict() if hasattr(record, "to_dict") else asdict(record)
            selected = record.selected_evaluation
            if selected.score is None or selected.score < parent_result.score + self.plan.acceptance_min_improvement or selected.accuracy < parent_result.accuracy - self.plan.retained_accuracy_tolerance:
                self.progress.accepted, self.progress.reason, self.progress.phase = False, "calibrated_selection_tolerance_not_met", "complete"
                self.trainer.state.phase = "failed"
            else:
                self.progress.accepted, self.progress.reason, self.progress.phase = True, "validated_and_recalibrated", "complete"
                self.trainer.state.calibration_complete = True
                self.trainer.state.phase = "complete"
                self.trainer.state.system_validation_records.append(selected.to_dict())
                self.trainer.state.best_system_score = selected.score
                self.trainer.state.eligible_checkpoints.append(self.trainer.state.outer_cycle)
                self.trainer.best_system = self.trainer._joint_state(selected)
            self.trainer._checkpoint()
        self.assert_frozen()
        return self.report()

    def state_dict(self) -> dict[str, Any]:
        bank = copy.deepcopy(self.optimizer_bank)
        for key, model, optimizer in (("alpha", self.trainer.alpha, self.trainer.alpha_optimizer),
                                      ("weighting", self.trainer.weighting, self.trainer.weighting_optimizer)):
            bank[key]["state"].update(named_optimizer_state(model, optimizer)["state"])
        return {"protocol": EXTENSION_PROTOCOL, "plan": self.plan.to_dict(), "plan_fingerprint": self.plan.fingerprint,
                "architecture": copy.deepcopy(self.expansion.metadata), "permitted": self.permitted,
                "inherited_optimizers": copy.deepcopy(self.inherited), "progress": asdict(self.progress),
                "spending_start": self._spending_start, "active_scope":self.active_scope,
                "scope_entry_termination":asdict(self.scope_entry_termination),
                "scope_authorization":copy.deepcopy(self.scope_authorization),"scope_history":copy.deepcopy(self.scope_history),
                "optimizer_bank":bank,
                "initial_retention":copy.deepcopy(self.initial_retention),
                "frozen_values":copy.deepcopy(self._frozen_values)}

    def validate_state(self, value: Mapping[str, Any]) -> None:
        if value.get("protocol") != EXTENSION_PROTOCOL or value.get("plan_fingerprint") != self.plan.fingerprint:
            raise ValueError("extension checkpoint belongs to a different scientific plan")
        if value.get("active_scope") != self.active_scope or value.get("scope_authorization") != self.scope_authorization:
            raise ValueError("extension checkpoint requires its original declared escalation scope")
        if value.get("scope_entry_termination") != asdict(self.scope_entry_termination):
            raise ValueError("extension checkpoint requires its recorded scope-entry stopping tuple")
        if value["architecture"] != self.expansion.metadata or any(tuple(value["permitted"][key]) != names for key, names in self.permitted.items()):
            raise ValueError("extension checkpoint architecture/trainable blocks differ")
        if not _tensor_equal(value["inherited_optimizers"], self.inherited):
            raise ValueError("frozen inherited optimizer evidence changed")
        progress = ExtensionProgress(**copy.deepcopy(value["progress"]))
        if progress.phase not in {"parent-retention", "weighting", "routing", "prompt", "validation", "calibration", "complete"}:
            raise ValueError("invalid extension phase")
        retention = value.get("initial_retention")
        if (retention is not None and (retention.get("passed") is not True
                                      or retention.get("tolerance") != self.plan.parent_value_tolerance)):
            raise ValueError("extension checkpoint changed its initial parent-retention evidence")
        if progress.phase != "parent-retention" and retention is None:
            raise ValueError("executed extension checkpoint lacks initial parent-retention evidence")

    def load_state_dict(self, value: Mapping[str, Any]) -> None:
        self.validate_state(value)
        self.progress = ExtensionProgress(**copy.deepcopy(value["progress"]))
        self._spending_start = value["spending_start"]
        self.scope_history = copy.deepcopy(value.get("scope_history",[]))
        self.optimizer_bank = copy.deepcopy(value["optimizer_bank"])
        self.initial_retention = copy.deepcopy(value["initial_retention"])
        self._frozen_values = copy.deepcopy(value["frozen_values"])

    def save(self, path: str | Path) -> None:
        payload = self.trainer.checkpoint_payload()
        payload["extension"] = self.state_dict()
        CheckpointManager.write_payload(path, payload)

    def report(self) -> dict[str, Any]:
        counts = {}
        for key, model in (("alpha", self.trainer.alpha), ("weighting", self.trainer.weighting)):
            counts[key] = {"total": sum(p.numel() for p in model.parameters()),
                           "trainable": sum(p.numel() for p in model.parameters() if p.requires_grad)}
        return {**asdict(self.progress), "protocol": EXTENSION_PROTOCOL, "plan_fingerprint": self.plan.fingerprint,
                "parameter_counts": counts, "attempts": dict(self.trainer.state.attempts),
                "updates": dict(self.trainer.state.updates), "coverage_complete": self.coverage_complete(),
                "parent_mode_is_separate": True, "no_retention_guarantee_from_freezing": True}


def check_parent_retention(parent: Sequence[tuple[torch.Tensor, torch.Tensor]],
                           extended: Sequence[tuple[torch.Tensor, torch.Tensor]], *, tolerance: float) -> dict[str, Any]:
    """Compare raw Q and W logits on saved parent inputs, without workflow calls."""
    if not parent or len(parent) != len(extended) or tolerance < 0 or not math.isfinite(tolerance):
        raise ValueError("retention needs matching nonempty saved parent states and a declared tolerance")
    errors = []
    for expected, actual in zip(parent, extended, strict=True):
        for before, after in zip(expected, actual, strict=True):
            if before.shape != after.shape or not bool(torch.isfinite(before).all()) or not bool(torch.isfinite(after).all()):
                raise ValueError("parent-mode output shapes/values differ")
            errors.append(float((before.detach() - after.detach()).abs().max()))
    return {"passed": max(errors) <= tolerance, "tolerance": tolerance,
            "states": len(parent), "maximum_absolute_error": max(errors), "mode": "parent_only"}
