"""Prepare and execute an explicitly supplied local experiment specification."""

from __future__ import annotations

from ..paths import PACKAGE_ROOT
from ..infrastructure.storage import (load_checkpoint, load_ledger, file_sha256, read_bytes, read_json,
                      strict_json_equal, startup_scope, current_startup)

import asyncio
import copy
import hashlib
import json
import os
import random
import tempfile
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

import torch

from ..learning.calibration import SafetyCalibrationPlan, TerminationCalibrationPlan
from ..config import Config, TerminationConfig
from ..datasets.data import ActiveDataPlan, DatasetPartitions, load_strategyqa, partition_queries, select_active_data
from ..learning.evaluation import EvaluationPlan, Experiment, FinalTestPlan, scoring_configuration
from ..infrastructure.randomness import derive_seed


@dataclass(frozen=True, slots=True)
class PreparedExperiment:
    config: Config
    dataset: DatasetPartitions
    plans: Mapping[str, EvaluationPlan]
    pilot_plan: SafetyCalibrationPlan | None
    termination_plan: TerminationCalibrationPlan
    final_test_plan: FinalTestPlan
    backends: Mapping[str, Mapping[str, Any]]
    textgrad: Mapping[str, Any]
    specification_sha256: str
    active_data_plan: ActiveDataPlan | None = None
    benchmark_provenance: Mapping[str, Any] = field(default_factory=dict)
    authorization_manifest_fingerprint: str = ""
    resource_profile_fingerprint: str = ""
    specification_amendments: tuple[Mapping[str, Any], ...] = ()
    attestations: Mapping[str, Any] = field(default_factory=dict)
    # Operational receipt interpretation only; never a request or scientific setting.
    response_identity_evidence: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    campaign_binding: Mapping[str, Any] = field(default_factory=dict)
    extension_registration: Mapping[str, Any] = field(default_factory=dict)
    continuation_registration: Mapping[str, Any] = field(default_factory=dict)

    def manifest(self) -> dict[str, Any]:
        """Export the scientific condition without credentials or client endpoints."""

        public_backend_fields = (
            "provider",
            "model_name",
            "revision",
            "decoding",
            "supports_seed",
            "availability_verified",
            "availability_reference",
            "revision_binding",
            "gateway",
            "role_output",
            "billing_verification",
            "generation_mode",
            "omitted_parameters",
            "backend_defaults_evidence",
            "resources",
        )
        return {
            "specification_sha256": self.specification_sha256,
            "authorization_manifest_fingerprint": self.authorization_manifest_fingerprint,
            "resource_profile_fingerprint": self.resource_profile_fingerprint,
            "specification_amendments": list(self.specification_amendments),
            "attestations": copy.deepcopy(dict(self.attestations)),
            "configuration": self.config.to_dict(),
            "data": self.dataset.manifest(),
            "active_data": self.active_data_plan.to_dict() if self.active_data_plan else None,
            "validation": {key: plan.to_dict() for key, plan in self.plans.items()},
            "pilot": None if self.pilot_plan is None else asdict(self.pilot_plan),
            "termination_calibration": asdict(self.termination_plan),
            "final_test": asdict(self.final_test_plan),
            "benchmark_provenance": dict(self.benchmark_provenance),
            "backends": {
                key: {name: backend[name] for name in public_backend_fields if name in backend}
                for key, backend in self.backends.items()
            },
            "textgrad": dict(self.textgrad),
            **({"extension_registration": dict(self.extension_registration)} if self.extension_registration else {}),
        }


def _relative(path: str | Path, directory: Path) -> Path:
    value = Path(path)
    return value if value.is_absolute() else directory / value


def _write_json_atomic(path: Path, value: Any) -> None:
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False
        ) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def extension_registration_context(source: Path, specification: Mapping[str, Any], config: Config) -> dict[str, Any]:
    """Resolve an explicit, content-bound extension without recursive preparation.

    The external registration references this manifest's bytes. The manifest
    records only its registration path, avoiding circular content hashes.
    """
    from ..learning.extension import EXTENSION_PROTOCOL, ExtensionPlan
    from ..learning.training import _fingerprint

    marker = specification.get("extension_registration")
    if not isinstance(marker, Mapping) or set(marker) != {"protocol", "path"} or marker["protocol"] != EXTENSION_PROTOCOL:
        raise ValueError("registered extension requires its exact external registration marker")
    path = _relative(marker["path"], source.parent).resolve()
    raw = read_json(path)
    fields = {"parent_manifest", "parent_checkpoint", "parent_evaluation", "parent_states", "extension_manifest"}
    if "parent_supplement_plan" in raw:
        fields.add("parent_supplement_plan")
    if set(raw) != fields | {"protocol", "plan"} or raw.get("protocol") != EXTENSION_PROTOCOL:
        raise ValueError("extension registration requires complete parent/source lineage and explicit plan")
    references = {}
    for name in fields:
        ref = raw[name]
        if not isinstance(ref, Mapping) or set(ref) != {"path", "sha256"}:
            raise ValueError("extension registration requires exact content-hashed references")
        resolved = _relative(ref["path"], path.parent).resolve()
        if file_sha256(resolved) != ref["sha256"]:
            raise ValueError("extension registration reference changed: " + name)
        references[name] = resolved
    if references["extension_manifest"] != source.resolve() or references["parent_manifest"] == source.resolve():
        raise ValueError("extension registration does not name this distinct manifest")
    plan = ExtensionPlan.from_dict(raw["plan"])
    parent_source = references["parent_manifest"]
    parent = read_json(parent_source)
    if any(specification.get(name) for name in ("independent_campaign", "policy_transition", "revised_condition")):
        raise ValueError("registered extension cannot impersonate a successor or historical transition")
    if _relative(specification["campaign_directory"], source.parent).resolve() != _relative(parent["campaign_directory"], parent_source.parent).resolve():
        raise ValueError("extension must share the canonical parent accounting campaign")
    from .preflight import execution_specification
    parent_scientific = execution_specification(parent)
    parent_scientific["config"].setdefault("training", {}).pop("max_outer_cycles", None)
    parent_condition = hashlib.sha256(json.dumps(parent_scientific, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    pc = Config.from_dict(parent["config"])
    if "parent_supplement_plan" in references:
        prospective = prepare_extension_parent(references)
        parent_condition, pc = prospective.specification_sha256, prospective.config
    elif (load_checkpoint(references["parent_checkpoint"])
          .get("hook_state") or {}).get("supplement_lineage") is not None:
        raise ValueError("extension amended parent requires its explicit supplement reference")
    if parent_condition != plan.parent_condition:
        raise ValueError("extension plan differs from the actual parent scientific condition")
    if config.encoders.cache_directory is not None:
        config = replace(config, encoders=replace(config.encoders, cache_directory=str(_relative(config.encoders.cache_directory, source.parent).resolve())))
    if pc.encoders.cache_directory is not None:
        pc = replace(pc, encoders=replace(pc.encoders, cache_directory=str(_relative(pc.encoders.cache_directory, parent_source.parent).resolve())))
    if config.n_agents < pc.n_agents or config.agent_pool[:pc.n_agents] != pc.agent_pool:
        raise ValueError("registered extension must preserve inherited agent identities and settings")
    if config.scheme_registry[:len(pc.scheme_registry)] != pc.scheme_registry:
        raise ValueError("registered extension schemes are append-only")
    if config.n_agents == pc.n_agents and config.scheme_registry == pc.scheme_registry:
        raise ValueError("registered extension must actually append an agent or scheme")
    for name in ("network", "encoders", "costs", "selection", "reproducibility", "safety", "resources", "failure_policy", "workflow_recovery"):
        if getattr(pc, name) != getattr(config, name):
            raise ValueError("registered extension changed inherited " + name)
    inherited_training = replace(config.training, weighting_phase=pc.training.weighting_phase,
                                  routing_phase=pc.training.routing_phase, prompt_phase=pc.training.prompt_phase)
    if inherited_training != pc.training or (config.training.weighting_phase, config.training.routing_phase, config.training.prompt_phase) != (plan.weighting_phase, plan.routing_phase, plan.prompt_phase):
        raise ValueError("extension training changes must equal the explicitly registered restricted phases")
    old_out, old_in = pc.capacity.resolve(pc.n_agents)
    new_out, new_in = config.capacity.resolve(config.n_agents)
    if new_out[:pc.n_agents] != old_out or new_in[:pc.n_agents] != old_in:
        raise ValueError("extension cannot change inherited capacity coordinates")
    for key, value in parent["backends"].items():
        if specification["backends"].get(key) != value:
            raise ValueError("extension cannot change inherited backend settings: " + key)
    if specification.get("textgrad") != parent.get("textgrad"):
        raise ValueError("extension cannot change inherited TextGrad settings")
    def data_identity(value: Mapping[str, Any], directory: Path) -> dict[str, Any]:
        result = copy.deepcopy(dict(value))
        for name in ("source_files", "evaluation_id_files"):
            result[name] = [str(_relative(item, directory).resolve()) for item in result[name]]
        return result
    if data_identity(specification["dataset"], source.parent) != data_identity(parent["dataset"], parent_source.parent):
        raise ValueError("extension must preserve the complete partition source and protected evaluation IDs")
    if config.termination not in plan.calibration_candidates:
        raise ValueError("extension calibration candidates must include its inherited stopping tuple")
    if tuple(TerminationConfig(**item) for item in specification.get("termination_calibration", {}).get("candidates", ())) != plan.calibration_candidates:
        raise ValueError("extension calibration must equal its explicit registered candidate list")
    return {"protocol": EXTENSION_PROTOCOL, "registration_path": str(path),
            "registration_fingerprint": _fingerprint(raw), "plan_fingerprint": plan.fingerprint,
            "parent_condition": plan.parent_condition, "extension_condition": plan.extension_condition,
            "parent_checkpoint_sha256": raw["parent_checkpoint"]["sha256"],
            "references": {key: str(value) for key, value in references.items()}, "plan": plan.to_dict()}


def prepare_extension_parent(references: Mapping[str, Any]) -> PreparedExperiment:
    """Prepare a registered parent, with explicit published-amendment ancestry."""
    plan_path = references.get("parent_supplement_plan")
    if (plan_path is not None
            and json.loads(Path(references["parent_manifest"]).read_text()).get("experiment_profile") == "registered_extension"):
        raise ValueError("supplementary parent lineage applies only to the registered primary condition")
    prepared = prepare_experiment(references["parent_manifest"])
    payload = load_checkpoint(references["parent_checkpoint"])
    lineage = (payload.get("hook_state") or {}).get("supplement_lineage")
    if plan_path is None:
        if lineage is not None:
            raise ValueError("extension amended parent requires its explicit supplement reference")
        return prepared
    from .supplement import prepare_checkpoint_condition

    if (not isinstance(lineage, Mapping)
            or Path(lineage.get("plan_path", "")).resolve() != Path(plan_path).resolve()
            or lineage.get("plan_sha256") != hashlib.sha256(Path(plan_path).read_bytes()).hexdigest()):
        raise ValueError("extension supplementary parent reference differs from checkpoint lineage")
    return prepare_checkpoint_condition(prepared, payload, specification_path=references["parent_manifest"])


def extension_policy_manifest(source: str | Path) -> Path:
    """Resolve the independently approved campaign without importing its results."""
    current = Path(source).resolve()
    seen: set[Path] = set()
    while True:
        if current in seen:
            raise ValueError("extension policy lineage contains a cycle")
        seen.add(current)
        value = json.loads(current.read_text())
        if value.get("experiment_profile") != "registered_extension":
            return current
        context = extension_registration_context(current, value, Config.from_dict(value["config"]))
        current = Path(context["references"]["parent_manifest"])


def prepare_experiment(specification_path: str | Path) -> PreparedExperiment:
    """Read local data and freeze plans; this creates no models and performs no generation."""

    source = Path(specification_path).resolve()
    raw = read_bytes(source)
    specification = json.loads(raw)
    config = Config.from_dict(specification["config"])
    if config.encoders.cache_directory is not None:
        config = replace(
            config,
            encoders=replace(
                config.encoders,
                cache_directory=str(_relative(config.encoders.cache_directory, source.parent).resolve()),
            ),
        )
    extension = {}
    if specification.get("experiment_profile") == "registered_extension":
        extension = extension_registration_context(source, specification, config)
    elif specification.get("extension_registration") is not None:
        raise ValueError("extension registration cannot replace primary-condition validation")
    if specification.get("experiment_profile") == "strategyqa_initial":
        from .preflight import primary_condition_errors

        errors = primary_condition_errors(specification, config)
        if errors:
            raise ValueError("primary scientific condition changed: " + "; ".join(errors))
    data_spec = specification["dataset"]
    examples, provenance = load_strategyqa(
        tuple(_relative(path, source.parent) for path in data_spec["source_files"]),
        revision=data_spec["revision"],
    )
    if specification.get("experiment_profile") == "strategyqa_initial":
        from ..datasets.assets import DATA_ASSETS, _verify_data

        asset_root = _relative(specification.get("asset_directory", "assets"), source.parent)
        originals = [asset_root / str(item["relative_path"]) for item in DATA_ASSETS]
        for path, item in zip(originals, DATA_ASSETS, strict=True):
            _verify_data(path.read_bytes(), item)
        original_examples, _ = load_strategyqa((originals[1], originals[0]), revision=data_spec["revision"])
        identity = lambda values: [(item.example_id, item.query, item.gold_answer) for item in values]
        if identity(examples) != identity(original_examples):
            raise ValueError(
                "working labelled pool differs from the complete verified official train and ReConcile dev artifacts"
            )
    evaluation_sets = []
    artifacts = []
    for path in data_spec["evaluation_id_files"]:
        artifact = _relative(path, source.parent).resolve()
        artifact_bytes = artifact.read_bytes()
        ids = json.loads(artifact_bytes)
        if not isinstance(ids, list) or not ids or any(not isinstance(identifier, str) for identifier in ids):
            raise ValueError("evaluation-ID artifacts must be nonempty JSON string arrays")
        if len(set(ids)) != len(ids):
            raise ValueError("evaluation-ID artifacts must not repeat query IDs")
        evaluation_sets.append(tuple(ids))
        artifacts.append(f"{artifact}#sha256={hashlib.sha256(artifact_bytes).hexdigest()}")
    provenance = replace(
        provenance,
        evaluation_protocol=data_spec.get("evaluation_protocol", "fixed_holdout"),
        evaluation_artifacts=tuple(artifacts),
    )
    benchmark_provenance = {}
    if provenance.evaluation_protocol == "reconcile_main100":
        from ..datasets.benchmark import verify_reconcile_main100

        if len(evaluation_sets) != 1:
            raise ValueError("main-100 preparation requires one exact ordered evaluation artifact")
        benchmark_provenance = verify_reconcile_main100(
            specification.get("benchmark", {}).get("evaluation_evidence", {}),
            evaluation_sets[0],
            directory=source.parent,
            supplied_examples=examples,
        )
    elif provenance.evaluation_protocol != "fixed_holdout":
        raise ValueError("unsupported evaluation provenance; do not infer a full-set artifact from split names")
    dataset = partition_queries(
        examples,
        evaluation_id_sets=evaluation_sets,
        split_seed=data_spec["split_seed"],
        provenance=provenance,
    )
    from .preflight import execution_specification

    scientific_specification = execution_specification(specification)
    scientific_specification["config"].setdefault("training", {}).pop("max_outer_cycles", None)
    specification_hash = hashlib.sha256(
        json.dumps(scientific_specification, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if extension and extension["extension_condition"] != specification_hash:
        raise ValueError("extension plan differs from the actual enlarged scientific condition")
    active_spec = specification.get("active_data", {})
    if "plan" in active_spec:
        active_data = ActiveDataPlan.from_dict(active_spec["plan"])
        active_data.validate(dataset)
    else:
        if extension:
            raise ValueError("extension requires explicit active-data IDs before preparation")
        active_data = select_active_data(
            dataset,
            seed=active_spec.get("seed", config.seed),
            profile=active_spec.get("profile", "strategyqa_initial"),
            counts=tuple(active_spec.get("counts", (60, 20, 20, 8))),
        )
    if extension:
        plan = extension["plan"]
        if active_data.profile != "registered_extension" or (
            active_data.train_ids, active_data.component_validation_ids, active_data.system_validation_ids
        ) != tuple(tuple(plan[name]) for name in ("train_ids", "component_validation_ids", "system_validation_ids")):
            raise ValueError("extension active subsets must equal the complete registered restricted plan")
    if specification.get("experiment_profile") == "strategyqa_initial":
        registered = select_active_data(dataset, seed=42, profile="strategyqa_initial", counts=(60, 20, 20, 8))
        if active_data != registered:
            raise ValueError("primary active plan must equal the registered seed-42 60/20/20/8 selection")
    plans = {}
    supplied_batches = specification.get("validation", {})
    for component, domain, partition in (
        ("weighting", "wval", "comp-val"),
        ("routing", "alphaval", "comp-val"),
        ("prompt", "promptval", "comp-val"),
        ("system", "sysval", "sys-val"),
    ):
        identifiers = (
            active_data.system_validation_ids if component == "system" else active_data.component_validation_ids
        )
        if component in supplied_batches and tuple(supplied_batches[component]) != identifiers:
            raise ValueError("validation IDs must match the fixed shared active component/system subsets")
        dataset.select(partition, identifiers)
        plans[component] = EvaluationPlan(
            identifiers,
            domain,
            config.seed,
            partition,
            {"dataset_fingerprint": dataset.fingerprint, "specification_sha256": specification_hash},
            scoring_configuration=scoring_configuration(config),
        )
    pilot_spec = dict(specification.get("pilot", {}))
    if "ordered_example_ids" in pilot_spec and tuple(pilot_spec["ordered_example_ids"]) != active_data.pilot_ids:
        raise ValueError("pilot IDs must equal the frozen active-training pilot subset")
    pilot_spec["ordered_example_ids"] = active_data.pilot_ids
    pilot_spec.setdefault("seed", derive_seed(config.seed, "pilot"))
    pilot = None if extension else SafetyCalibrationPlan(**pilot_spec)
    if pilot is not None:
        dataset.select("train", pilot.ordered_example_ids)
    from ..learning.calibration import termination_presets

    calibration = specification.get("termination_calibration", {})
    if "quality_weight" in calibration or "normalized_cost_weight" in calibration:
        raise ValueError("termination uses the shared selection score; remove separate calibration weights")
    candidates = (tuple(TerminationConfig(**value) for value in extension["plan"]["calibration_candidates"])
                  if extension else termination_presets(config.termination))
    if (
        "candidates" in calibration
        and tuple(TerminationConfig(**value) for value in calibration["candidates"]) != candidates
    ):
        raise ValueError("initial calibration candidates must equal the registered bootstrap and five presets")
    outgoing, incoming = config.capacity.resolve(config.n_agents)
    termination = TerminationCalibrationPlan(
        plans["system"],
        candidates,
        config.termination,
        config.n_agents,
        outgoing,
        incoming,
        tuple(scheme.value for scheme in config.scheme_order),
    )
    test = FinalTestPlan(
        tuple(example.example_id for example in dataset.test),
        tuple(specification.get("final_test", {}).get("run_seeds", (config.seed,))),
    )
    backends = copy.deepcopy(specification["backends"])
    for backend in backends.values():
        profile = backend.get("resources", {})
        if profile.get("tokenizer_cache_directory") is not None:
            profile["tokenizer_cache_directory"] = str(
                _relative(profile["tokenizer_cache_directory"], source.parent).resolve()
            )
    from .preflight import approval_fingerprint, gateway_fingerprint_fields

    authorization_hash = approval_fingerprint(specification, directory=source.parent)
    resource_hash = hashlib.sha256(
        json.dumps(
            {
                "resources": asdict(config.resources),
                **({"failure_policy": asdict(config.failure_policy)} if config.failure_policy.enabled else {}),
                "backends": {
                    key: {
                        name: gateway_fingerprint_fields(backend.get(name)) if name == "gateway" else backend.get(name)
                        for name in (
                            "model_name",
                            "revision",
                            "resources",
                            "gateway",
                            "billing_verification",
                            "role_output",
                        )
                    }
                    for key, backend in backends.items()
                },
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    from .campaigns import campaign_binding

    return PreparedExperiment(
        config,
        dataset,
        plans,
        pilot,
        termination,
        test,
        backends,
        specification["textgrad"],
        specification_hash,
        active_data,
        benchmark_provenance,
        authorization_hash,
        resource_hash,
        tuple(specification.get("specification_amendments", ())),
        copy.deepcopy(specification.get("attestations", {})),
        campaign_binding=campaign_binding(source, specification),
        extension_registration=extension,
    )


def build_model(backend: Mapping[str, Any]) -> Any:
    """Construct a public openJiuwen Model; providers are invoked only by workflow execution."""

    from ..providers.gateway import OPENROUTER_BASE_URL, OpenRouterBinding, build_openrouter_model

    if backend.get("provider") != "OpenRouter" or backend.get("api_base") != OPENROUTER_BASE_URL:
        raise ValueError(
            "the primary experiment requires its approved audited OpenRouter binding; no model substitution is allowed"
        )
    binding = OpenRouterBinding.from_dict(backend["gateway"])
    binding.validate()
    if backend["model_name"] != binding.model or set(backend.get("decoding", {})) != {"temperature"}:
        raise ValueError("model identity and temperature-only decoding must match the fixed gateway binding")

    api_key = os.environ.get(backend["api_key_env"], "") if backend.get("api_key_env") else ""
    if backend.get("api_key_env") and not api_key:
        raise ValueError(f"required backend credential variable is unset: {backend['api_key_env']}")
    return build_openrouter_model(
        binding,
        api_key,
        temperature=backend["decoding"]["temperature"],
        response_identity_evidence=backend.get("response_identity_evidence"),
        role_output=backend.get("role_output"),
    )


def _execution_metadata(prepared: PreparedExperiment, proposer_metadata: Mapping[str, Any]) -> dict[str, Any]:
    from ..infrastructure.persistence import PERSISTENCE_PROTOCOL
    from ..infrastructure.failure_policy import DEADLINE_CAUSE_PROTOCOL, DISPATCH_ADMISSION_PROTOCOL
    from ..infrastructure.shutdown import SHUTDOWN_PROTOCOL

    metadata = {
        "persistence_protocol": PERSISTENCE_PROTOCOL,
        "deadline_cause_protocol": DEADLINE_CAUSE_PROTOCOL,
        "dispatch_admission_protocol": DISPATCH_ADMISSION_PROTOCOL,
        "shutdown_protocol": SHUTDOWN_PROTOCOL,
        "specification_sha256": prepared.specification_sha256,
        "textgrad": dict(proposer_metadata),
        "authorization_manifest_fingerprint": prepared.authorization_manifest_fingerprint,
        "resource_profile_fingerprint": prepared.resource_profile_fingerprint,
    }
    if prepared.continuation_registration:
        metadata["registered_supplement"] = copy.deepcopy(dict(prepared.continuation_registration))
        if "supplement_routing_source" in prepared.continuation_registration:
            metadata["supplement_routing_source"] = copy.deepcopy(prepared.continuation_registration["supplement_routing_source"])
    for amendment in prepared.specification_amendments:
        if amendment.get("protocol") == "calibration_limit_plan_v1":
            metadata["registered_calibration"] = copy.deepcopy(dict(amendment))
        elif amendment.get("protocol") == "regular_textgrad_repair_plan_v1":
            metadata["registered_regular"] = copy.deepcopy(dict(amendment))
    if prepared.extension_registration:
        metadata["registered_extension"] = copy.deepcopy(dict(prepared.extension_registration))
    if prepared.config.failure_policy.enabled:
        from ..infrastructure.failure_policy import FAILURE_POLICY_PROTOCOL
        from ..workflows.runtime import FAILURE_ELIGIBILITY_PROTOCOL

        metadata["failure_semantics"] = {
            "policy": FAILURE_POLICY_PROTOCOL,
            "eligibility": FAILURE_ELIGIBILITY_PROTOCOL,
        }
    return metadata


def planning_hooks(prepared: PreparedExperiment) -> Experiment:
    """Compute exact condition/authorization templates without loading or constructing models."""
    from types import SimpleNamespace
    from ..providers.resources import BackendResourceProfile
    from ..learning.textgrad_optimizer import TextGradConfig, textgrad_metadata

    profiles = {
        role: asdict(
            BackendResourceProfile.from_dict(prepared.backends[prepared.textgrad[f"{role}_backend"]]["resources"])
        )
        for role in ("backward", "optimizer")
    }
    metadata = textgrad_metadata(TextGradConfig(**prepared.textgrad["config"]), profiles)

    def unavailable(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("planning hooks never construct a runtime")

    hooks = Experiment(
        prepared.dataset,
        unavailable,
        plans=prepared.plans,
        prompt_train_batch_size=prepared.config.training.prompt_train_batch_size,
        pilot_plan=prepared.pilot_plan,
        termination_plan=prepared.termination_plan,
        final_test_plan=prepared.final_test_plan,
        active_data_plan=prepared.active_data_plan,
        execution_metadata=_execution_metadata(prepared, metadata),
        authorization_required=True,
    )
    hooks.bind(SimpleNamespace(config=prepared.config))
    return hooks


def build_experiment(
    prepared: PreparedExperiment,
    *,
    model_factory: Callable[[Mapping[str, Any]], Any] = build_model,
    fixed_encoders: Any = None,
    proposer: Any = None,
    resource_factory: Callable[[Mapping[str, Any], Any, Config], Any] | None = None,
) -> tuple[Any, Experiment]:
    """Connect public backends, one shared fixed cache, numerical models, and the trainer."""

    from ..providers.adapter import OpenJiuwenAgent
    from ..models.encoders import FixedBaseEncoders
    from ..models.policies import RoutingPolicy, WeightingModel
    from ..workflows.prompts import seed_prompt_collection
    from ..workflows.runtime import Runtime
    from ..providers.resources import ExpenditureLedger, build_resource_controller
    from ..learning.textgrad_optimizer import TextGradConfig, TextGradPromptProposer
    from ..learning.training import InterleavedTrainer, PromptOptimizer

    config = prepared.config
    ledger = ExpenditureLedger(config.resources, concurrency=config.safety.max_concurrent_model_calls)
    ledger.campaign_binding = copy.deepcopy(dict(prepared.campaign_binding))
    make_resources = resource_factory or build_resource_controller
    torch.manual_seed(config.seed)
    random.seed(config.seed)
    encoders = (
        fixed_encoders
        if fixed_encoders is not None
        else FixedBaseEncoders(config.n_agents, configuration=config.encoders, scheme_order=config.scheme_order, agent_ids=config.agent_ids, categories=dict(config.history_categories) or None)
    )
    alpha = RoutingPolicy(config.n_agents, encoders.history_dimension, configuration=config.network, scheme_order=config.scheme_order, legality=config.scheme_legality).to(
        dtype=encoders.dtype
    )
    weighting = WeightingModel(config.n_agents, encoders.history_dimension, configuration=config.network).to(
        dtype=encoders.dtype
    )
    models = {}
    adapters = {}
    controllers = {}

    def client_backend(key: str) -> dict[str, Any]:
        backend = copy.deepcopy(dict(prepared.backends[key]))
        if key in prepared.response_identity_evidence:
            backend["response_identity_evidence"] = copy.deepcopy(prepared.response_identity_evidence[key])
        return backend

    def controller(key: str) -> Any:
        if key not in controllers:
            backend = prepared.backends[key]
            if "resources" in backend:
                controllers[key] = make_resources(backend["resources"], ledger, config)
            elif model_factory is build_model:
                raise ValueError("production backends require declared resource profiles and local tokenizers")
            else:
                controllers[key] = None  # Explicit injected offline fixtures only.
        return controllers[key]

    for logical in config.agent_pool:
        backend = prepared.backends[logical.backend_key]
        if logical.model_name != backend["model_name"] or logical.model_revision != backend["revision"]:
            raise ValueError("backend model identity must match recorded logical-agent configuration")
        if dict(logical.decoding_configuration) != {
            str(key): str(value) for key, value in backend.get("decoding", {}).items()
        }:
            raise ValueError("backend decoding must match recorded logical-agent decoding configuration")
        if logical.backend_key not in adapters:
            resources = controller(logical.backend_key)
            models[logical.backend_key] = model_factory(client_backend(logical.backend_key))
            adapters[logical.backend_key] = OpenJiuwenAgent(
                models[logical.backend_key],
                timeout=config.safety.call_timeout_seconds,
                parse_retries=0,
                supports_seed=bool(backend.get("supports_seed", False)),
                resources=resources,
            )
    agents = {logical.agent_id: adapters[logical.backend_key] for logical in config.agent_pool}
    if proposer is None:
        textgrad_spec = prepared.textgrad
        textgrad_config = TextGradConfig(**textgrad_spec["config"])
        if textgrad_config.run_seed != config.seed:
            raise ValueError("TextGrad generation must use the recorded experiment run seed")
        engine_models = {}
        for name in ("backward_backend", "optimizer_backend"):
            key = textgrad_spec[name]
            role = name.removesuffix("_backend")
            backend = prepared.backends[key]
            if (
                getattr(textgrad_config, f"{role}_engine") != backend["model_name"]
                or getattr(textgrad_config, f"{role}_revision") != backend["revision"]
                or getattr(textgrad_config, f"{role}_supports_seed") != bool(backend.get("supports_seed", False))
            ):
                raise ValueError("TextGrad engine metadata must match the actual configured backend")
            controller(key)
            engine_models[role] = model_factory(
                {**client_backend(key), "decoding": dict(getattr(textgrad_config, f"{role}_decoding"))}
            )
        engine_resources = {
            "backward": controller(textgrad_spec["backward_backend"]),
            "optimizer": controller(textgrad_spec["optimizer_backend"]),
        }
        proposer = TextGradPromptProposer(
            engine_models["backward"],
            engine_models["optimizer"],
            config=textgrad_config,
            resources=engine_resources if any(engine_resources.values()) else {},
        )
    trainer = InterleavedTrainer(
        alpha=alpha,
        weighting=weighting,
        gamma=seed_prompt_collection(config.scheme_registry),
        alpha_optimizer=torch.optim.Adam(
            alpha.parameters(),
            lr=config.training.routing_learning_rate,
            weight_decay=config.training.weight_decay,
            betas=(config.training.adam_beta1, config.training.adam_beta2),
            eps=config.training.adam_epsilon,
            amsgrad=config.training.adam_amsgrad,
            maximize=config.training.adam_maximize,
        ),
        weighting_optimizer=torch.optim.Adam(
            weighting.parameters(),
            lr=config.training.weighting_learning_rate,
            weight_decay=config.training.weight_decay,
            betas=(config.training.adam_beta1, config.training.adam_beta2),
            eps=config.training.adam_epsilon,
            amsgrad=config.training.adam_amsgrad,
            maximize=config.training.adam_maximize,
        ),
        prompt_optimizer=PromptOptimizer(proposer),
        config=config,
        encoder_metadata=encoders.metadata(),
    )

    trainer.fixed_encoders = encoders

    def runtime_factory(active: Config, prompts: Any) -> Runtime:
        for adapter in adapters.values():
            adapter.timeout = active.safety.call_timeout_seconds
        return Runtime(active, agents, trainer.alpha, trainer.weighting, prompts=prompts, encoders=trainer.fixed_encoders)

    from openjiuwen.core.foundation.llm import Model

    production = model_factory is build_model or any(
        isinstance(model, Model)
        for model in (
            *models.values(),
            getattr(proposer, "backward_model", None),
            getattr(proposer, "optimizer_model", None),
        )
    )
    hooks = Experiment(
        prepared.dataset,
        runtime_factory,
        plans=prepared.plans,
        prompt_train_batch_size=config.training.prompt_train_batch_size,
        pilot_plan=prepared.pilot_plan,
        termination_plan=prepared.termination_plan,
        final_test_plan=prepared.final_test_plan,
        active_data_plan=prepared.active_data_plan,
        expenditure_ledger=ledger,
        execution_metadata=_execution_metadata(prepared, proposer.metadata()),
        authorization_required=production,
    )
    hooks.bind(trainer)
    ledger.on_change = trainer._request_checkpoint
    return trainer, hooks


def _read_ledger(path: Path) -> dict[str, Any] | None:
    from ..infrastructure.persistence import read_journal

    journal = read_journal(path.parent, include_audit=False)
    return journal["state"] if journal is not None else (load_ledger(path) if path.is_file() else None)


def validate_journal_resume(
    checkpoint_path: str | Path | None,
    directory: str | Path,
    *,
    allow_halted: bool = False,
    campaign_directory: str | Path | None = None,
) -> dict[str, Any] | None:
    """Reject replay after a journal/checkpoint crash window before constructing models or writing files."""
    from .campaigns import assert_campaign_open

    assert_campaign_open(directory)
    if checkpoint_path is not None:
        assert_campaign_open(Path(checkpoint_path).parent)
    journal_paths = [Path(directory) / "expenditure_ledger.json"]
    if campaign_directory is not None:
        journal_paths.append(Path(campaign_directory) / "expenditure_ledger.json")
    checkpoint_ledger = None
    if checkpoint_path is not None:
        payload = load_checkpoint(checkpoint_path)
        if payload.get("request_boundary_pending") is not False:
            raise ValueError(
                "operator reconciliation is required for an unresolved physical request boundary; automatic replay is disabled"
            )
        checkpoint_ledger = (payload.get("hook_state") or {}).get("expenditure_ledger")
        if checkpoint_ledger is not None and checkpoint_ledger.get("halted") and not allow_halted:
            raise ValueError(
                "the checkpoint records a sticky campaign halt; operator reconciliation is required before resume"
            )
        journal_paths.append(Path(checkpoint_path).parent / "expenditure_ledger.json")
    if checkpoint_path is None and (Path(directory) / "checkpoint.pt").exists():
        raise ValueError("existing campaign checkpoint requires explicit resume; automatic reset is disabled")
    if checkpoint_path is not None and not any(path.is_file() for path in journal_paths):
        raise ValueError("physical expenditure journal is missing; operator reconciliation is required before resume")
    from ..infrastructure.persistence import read_journal

    for path in dict.fromkeys(journal_paths):
        progress = read_journal(path.parent, include_audit=False)
        if progress is not None:
            boundary = progress["boundary"]
            if checkpoint_path is None or boundary is None:
                raise ValueError("unfinished workflow progress journal; automatic replay is disabled")
            if boundary["checkpoint_sha256"] != file_sha256(checkpoint_path):
                raise ValueError("checkpoint publication differs from durable workflow boundary")
            # At a certified boundary all three publications must agree. While
            # work is in flight the WAL may lead its snapshot, but that state is
            # already rejected above and can never authorize ordinary resume.
            if not path.is_file() or not strict_json_equal(load_ledger(path), progress["state"]):
                raise ValueError("ledger snapshot provenance or scope differs from durable workflow boundary")
        journal = _read_ledger(path)
        if journal is None:
            continue
        if journal.get("halted") and not allow_halted:
            raise ValueError(
                "the durable journal records a sticky campaign halt; operator reconciliation is required before resume"
            )
        if allow_halted and journal.get("halted") != (checkpoint_ledger or {}).get("halted"):
            raise ValueError("journal and checkpoint halt evidence differ; operator reconciliation is required")
        for state_key in (
            "protocol",
            "limits",
            "stage",
            "scopes",
            "active_scope",
            "accounting_reconciliations",
            "halt_releases",
            "reconciliation_audit",
            "accounting_lookup_intents",
            "scope_activations",
            "historical_accounting_exceptions",
            "halt_events",
            "limit_amendments",
            "failure_policy",
            "campaign_binding",
        ):
            if checkpoint_ledger is not None and not strict_json_equal(
                journal.get(state_key), checkpoint_ledger.get(state_key)
            ):
                raise ValueError(
                    "journal accounting provenance or scope differs from the safe checkpoint; operator reconciliation is required"
                )
        entries = journal.get("entries", [])
        saved = [] if checkpoint_ledger is None else checkpoint_ledger.get("entries", [])
        # JSON journals materialize sequence tuples as lists; Torch retains the
        # tuples in native gateway frames. Compare their exact JSON values while
        # preserving every recorded status, charge, identity and frame field.
        if not strict_json_equal(entries, saved[: len(entries)]):
            raise ValueError(
                "durable physical expenditure journal is ahead of or differs from the safe checkpoint; operator reconciliation is required and automatic replay is disabled"
            )
        if checkpoint_path is None and entries:
            raise ValueError(
                "an existing physical expenditure journal requires its safe checkpoint; budget reset is disabled"
            )
    return checkpoint_ledger


def wire_durable_journal(
    trainer: Any,
    hooks: Any,
    directory: Path,
    *,
    campaign_directory: Path | None = None,
    allow_empty_initialization: bool = False,
) -> None:
    """Bind lossless WAL accounting; publish full snapshots only with checkpoints."""
    from ..infrastructure.persistence import DurableJournal

    ledger = hooks.expenditure_ledger
    if ledger is None:
        return
    paths = [directory]
    if campaign_directory is not None and campaign_directory != directory:
        paths.append(campaign_directory)
    state = ledger.state_dict()
    journal = DurableJournal(paths, state, allow_empty_initialization=allow_empty_initialization)
    ledger.durable_journal = trainer.durable_journal = journal
    ledger._journal_entry_count = len(ledger.entries)
    ledger._journal_header_cache = {key: value for key, value in state.items() if key not in {"entries", "failure_policy"}}
    ledger._journal_policy_object = ledger.failure_policy
    ledger._journal_policy_events = len(ledger.failure_policy["events"]) if ledger.failure_policy else 0
    ledger._journal_policy_adoptions = len(ledger.failure_policy.get("historical_adoptions", [])) if ledger.failure_policy else 0
    # A mutation marks enclosing work unsafe without serializing trainable state.
    ledger.on_change = trainer._request_checkpoint
    for path in paths:
        if not (path / "expenditure_ledger.json").exists():
            _write_json_atomic(path / "expenditure_ledger.json", state)


def _write_run_inputs(directory: Path, prepared: PreparedExperiment, preflight: Any) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    prepared.dataset.write_manifest(directory / "data_manifest.json")
    _write_json_atomic(directory / "experiment_manifest.json", prepared.manifest())
    _write_json_atomic(directory / "preflight.json", preflight.to_dict())


def _campaign_directory(specification_path: str | Path, output_directory: str | Path, *, test: bool = False) -> Path:
    source = Path(specification_path).resolve()
    specification = read_json(source)
    declared = specification.get("campaign_directory")
    if not isinstance(declared, str) or not declared:
        raise ValueError(
            "the experiment requires one registered campaign_directory for its non-resetting physical ledger"
        )
    campaign = _relative(declared, source.parent).resolve()
    validate_revised_condition_lineage(specification, source.parent)
    if not campaign.is_relative_to(PACKAGE_ROOT / "runs"):
        raise ValueError("campaign_directory must stay inside this example runs directory")
    if (campaign / "active_condition.json").exists():
        raise ValueError("historical diagnostic transitions are retired; execution is unavailable")
    expected = campaign / "test" if test else campaign
    if Path(output_directory).resolve() != expected:
        raise ValueError(
            "output must equal the registered campaign directory (its test subdirectory for final testing); changing output cannot reset accounting or replay a stage"
        )
    return campaign


def validate_revised_condition_lineage(specification: Mapping[str, Any], directory: Path) -> None:
    """Validate current registration and reject retired historical execution lineages."""
    from .campaigns import assert_campaign_open, validate_campaign

    if specification.get("campaign_directory"):
        assert_campaign_open(_relative(specification["campaign_directory"], directory))
    if specification.get("policy_transition") or specification.get("revised_condition"):
        raise ValueError("historical diagnostic transitions are retired; execution is unavailable")
    if specification.get("independent_campaign"):
        validate_campaign(specification, directory)


@contextmanager
def _campaign_lock(directory: Path) -> Iterator[Callable[[], bool]]:
    """Permit only one process to spend against this registered campaign ledger."""
    from .campaigns import campaign_lock, assert_campaign_open

    with campaign_lock(directory):
        active = True
        try:
            def guard() -> bool:
                assert_campaign_open(directory)
                return active
            yield guard
        finally:
            active = False


def training_diagnostics(trainer: Any) -> dict[str, Any]:
    """Detached checkpoint diagnostics; never observable history or model input."""
    state = trainer.state
    return {
        "protocol": "numerical_loss_records_v1",
        "phase": state.phase,
        "request_boundary_pending": trainer._request_boundary_pending,
        "training_losses": copy.deepcopy(state.training_loss_records),
        "validation_history": copy.deepcopy(state.validation_history),
        "phase_records": copy.deepcopy(state.phase_records),
        "phase_progress": copy.deepcopy(state.phase_progress),
        "attempts": dict(state.attempts),
        "successful_updates": dict(state.updates),
        "weighting_diagnostics": state.weighting_diagnostics.to_dict(),
        "execution_failures": copy.deepcopy(state.execution_failures),
    }


@startup_scope
async def _execute_stage(
    specification_path: str | Path,
    output_directory: str | Path,
    *,
    stage: str,
    authorization_path: str | Path,
    resume: str | Path | None = None,
    bootstrap_only: bool = False,
    bootstrap_forecast_path: str | Path | None = None,
    supplement_plan_path: str | Path | None = None,
    supplement_forecast_path: str | Path | None = None,
    calibration_only: bool = False,
    calibration_plan_path: str | Path | None = None,
    calibration_forecast_path: str | Path | None = None,
    regular_plan_path: str | Path | None = None,
    regular_forecast_path: str | Path | None = None,
) -> dict[str, Any]:
    from .preflight import inspect_preflight, stage_authorization_template

    if stage == "development" and resume is None:
        raise ValueError("development requires the saved completed pilot checkpoint and its separate approval")
    directory = Path(output_directory).resolve()
    campaign = _campaign_directory(specification_path, directory)
    policy_enabled = prepare_experiment(specification_path).config.failure_policy.enabled
    if resume is not None and Path(resume).resolve().parent != directory:
        raise ValueError("resume checkpoint must belong to the registered campaign directory")
    validate_journal_resume(resume, directory, campaign_directory=campaign, allow_halted=policy_enabled)
    report = inspect_preflight(
        specification_path, operation=stage, authorization_path=authorization_path, checkpoint_path=resume,
        bootstrap_only=bootstrap_only, bootstrap_forecast_path=bootstrap_forecast_path,
        supplement_plan_path=supplement_plan_path, supplement_forecast_path=supplement_forecast_path,
        calibration_only=calibration_only, calibration_plan_path=calibration_plan_path,
        calibration_forecast_path=calibration_forecast_path,
        regular_plan_path=regular_plan_path, regular_forecast_path=regular_forecast_path,
    )
    report.require_ready()
    with _campaign_lock(campaign) as session_guard:
        verification = current_startup()
        validate_journal_resume(resume, directory, campaign_directory=campaign, allow_halted=policy_enabled)
        prepared = prepare_experiment(specification_path)
        if supplement_plan_path is not None:
            from .supplement import apply_prepared, load_supplement_plan
            prepared = apply_prepared(prepared, load_supplement_plan(supplement_plan_path, specification_path))
        if resume is not None:
            from .supplement import prepare_checkpoint_condition

            prepared = prepare_checkpoint_condition(prepared,
                load_checkpoint(resume), specification_path=specification_path)
        from ..providers.identity import with_registered_response_identity

        prepared = with_registered_response_identity(prepared, specification_path)
        trainer, hooks = build_experiment(prepared)
        hooks.execution_session_guard = session_guard
        if policy_enabled:
            from .policy_activation import bind_policy_dispatch

            bind_policy_dispatch(hooks.expenditure_ledger, specification_path, session_guard)
        if resume is not None:
            trainer.resume_from_checkpoint(resume, hooks=hooks)
            if policy_enabled:
                bind_policy_dispatch(hooks.expenditure_ledger, specification_path, session_guard)
        hooks.authorize_stage(read_json(authorization_path))
        trainer.hooks, trainer.checkpoint_path = hooks, directory / "checkpoint.pt"
        # DurableJournal rechecks the pinned snapshot after acquiring its writer
        # locks. No startup cache survives its first write or the first workflow.
        wire_durable_journal(trainer, hooks, directory, campaign_directory=campaign)
        if verification is not None:
            verification.finish()
        _write_run_inputs(directory, prepared, report)
        hooks.execution_session_guard = session_guard
        trainer._checkpoint()
        interrupted: asyncio.CancelledError | None = None
        stage_error: BaseException | None = None
        try:
            if stage == "compatibility":
                result = await hooks.run_compatibility(hooks.compatibility_plan())
                trainer._checkpoint()
                _write_json_atomic(directory / "compatibility_report.json", result)
                return result
            if stage == "pilot":
                state = await trainer.run_pilot(hooks, checkpoint_path=trainer.checkpoint_path)
            else:
                state = await trainer.fit(
                    hooks, checkpoint_path=trainer.checkpoint_path, bootstrap_only=bootstrap_only,
                    weighting_supplement=supplement_plan_path is not None,
                    calibration_only=calibration_only,
                )
        except asyncio.CancelledError as exc:
            from ..infrastructure.failure_policy import cancellation_cause
            from ..infrastructure.shutdown import note_cleanup_failure

            interrupted = exc
            stage_error = exc
            if trainer.state.phase != "interrupted":
                trainer.state.phase = "interrupted"
                trainer.state.execution_failures.append(
                    {"phase": stage, "error": cancellation_cause(exc), "interrupted": True}
                )
                try:
                    await trainer.checkpoint_async(safe_boundary=False)
                except Exception as error:
                    note_cleanup_failure(exc, "stage interrupted checkpoint", error)
            raise
        except BaseException as exc:
            stage_error = exc
            raise
        finally:
            from ..infrastructure.shutdown import note_cleanup_failure

            finalization_errors = list(getattr(stage_error, "cleanup_errors", ()))

            def failed_cleanup(operation: str, error: Exception) -> None:
                finalization_errors.append({"operation": operation, "type": type(error).__name__, "error": str(error)})
                if stage_error is not None:
                    note_cleanup_failure(stage_error, operation, error)

            async def publish(path: Path, value: Any) -> None:
                try:
                    if hooks.expenditure_ledger is not None:
                        await hooks.expenditure_ledger.write_artifact(path, value)
                    else:
                        _write_json_atomic(path, value)
                except Exception as error:
                    failed_cleanup(f"publish {path.name}", error)

            if hooks.expenditure_ledger is not None:
                try:
                    await hooks.expenditure_ledger.flush_durable()
                except Exception as error:
                    failed_cleanup("stage accounting flush", error)
                try:
                    paths = [directory / "expenditure_ledger.json"]
                    if campaign != directory:
                        paths.append(campaign / "expenditure_ledger.json")
                    await hooks.expenditure_ledger.publish_snapshot(paths)
                except Exception as error:
                    failed_cleanup("stage accounting snapshot", error)
            if hooks.safety_record is not None and stage == "pilot":
                await publish(directory / "pilot_record.json", hooks.safety_record.to_dict())
                if hooks.safety_record.execution_complete and interrupted is None and not finalization_errors:
                    await publish(
                        directory / "development_authorization_template.json",
                        stage_authorization_template(**hooks._authorization_bindings("development")),
                    )
            if stage == "development":
                await publish(directory / "training_diagnostics.json", training_diagnostics(trainer))
            # Healthy idle writers are closed at the boundary. Unfinished
            # physical tasks keep their writer while the CLI performs bounded
            # cleanup, preserving any late receipt without enabling dispatch.
            pending_tasks = sum(task is not asyncio.current_task() and not task.done() for task in asyncio.all_tasks())
            journal = getattr(trainer, "durable_journal", None)
            if interrupted is not None or finalization_errors:
                from ..infrastructure.failure_policy import cancellation_cause
                from ..infrastructure.shutdown import current_shutdown

                shutdown = current_shutdown()
                await publish(
                    directory / "result.json",
                    {
                        "phase": "interrupted" if interrupted is not None else "persistence-failed",
                        "stage": stage,
                        "stage_complete": False,
                        "pilot_complete": False,
                        "checkpoint": str((directory / "checkpoint.pt").resolve()),
                        "resume_safe": False,
                        "unfinished_workflow": True,
                        "reason": cancellation_cause(interrupted) if interrupted is not None else "stage persistence failed",
                        "operator_signal": None if shutdown is None else shutdown.signum,
                        "operator_signals": [] if shutdown is None else list(shutdown.signals),
                        "pending_cleanup_tasks": pending_tasks,
                        "execution_failures": copy.deepcopy(trainer.state.execution_failures),
                        "persistence_errors": finalization_errors,
                    },
                )
            if journal is not None and not pending_tasks and (stage_error is not None or finalization_errors or stage == "compatibility"):
                try:
                    await journal.aclose()
                except Exception as error:
                    failed_cleanup("stage accounting writer close", error)
            if finalization_errors and stage_error is None:
                from ..providers.resources import AccountingFailure

                raise AccountingFailure("stage publication failed; incomplete state requires review")
        result = {
            "phase": state.phase,
            "outer_cycle": state.outer_cycle,
            "checkpoint": str((directory / "checkpoint.pt").resolve()),
            "dataset_fingerprint": prepared.dataset.fingerprint,
            "specification_sha256": prepared.specification_sha256,
            "pilot_complete": state.pilot_complete,
            "stage_complete": state.phase == (
                "pilot_complete" if stage == "pilot" else "weighting" if calibration_only
                else "calibration" if (bootstrap_only or supplement_plan_path) else "complete"
            ),
            "execution_scope": ("development_regular_only" if regular_plan_path is not None else
                                "development_calibration_only" if calibration_only else
                                "development_weighting_supplement_only" if supplement_plan_path else
                                "development_bootstrap_only" if bootstrap_only else stage),
            "weighting_supplement": copy.deepcopy(state.weighting_supplement),
            "bootstrap_complete": copy.deepcopy(state.bootstrap_complete),
            "calibration_complete": state.calibration_complete,
            "next_stage_requires_authorization": ("regular_development" if calibration_only else
                "post_bootstrap_development" if (bootstrap_only or supplement_plan_path) else
                "final_testing" if regular_plan_path is not None else None),
            "execution_failures": copy.deepcopy(state.execution_failures),
            "operational_halt": (
                None if hooks.expenditure_ledger is None else copy.deepcopy(hooks.expenditure_ledger.halted)
            ),
            "development_authorization_required": stage == "pilot",
        }
        if hooks.expenditure_ledger is not None:
            await hooks.expenditure_ledger.write_artifact(directory / "result.json", result)
        else:
            _write_json_atomic(directory / "result.json", result)
        journal = getattr(trainer, "durable_journal", None)
        if journal is not None and not any(task is not asyncio.current_task() and not task.done() for task in asyncio.all_tasks()):
            await journal.aclose()
        return result


async def pilot_experiment(
    specification_path: str | Path,
    output_directory: str | Path,
    *,
    authorization_path: str | Path,
    resume: str | Path | None = None,
) -> dict[str, Any]:
    """Run only the eight registered training pilot attempts; never enter bootstrap."""
    return await _execute_stage(
        specification_path, output_directory, stage="pilot", authorization_path=authorization_path, resume=resume
    )


async def compatibility_experiment(
    specification_path: str | Path,
    output_directory: str | Path,
    *,
    authorization_path: str | Path,
    resume: str | Path | None = None,
) -> dict[str, Any]:
    return await _execute_stage(
        specification_path,
        output_directory,
        stage="compatibility",
        authorization_path=authorization_path,
        resume=resume,
    )


async def run_experiment(
    specification_path: str | Path,
    checkpoint_directory: str | Path,
    resume: str | Path | None = None,
    *,
    authorization_path: str | Path,
    bootstrap_only: bool = False,
    bootstrap_forecast_path: str | Path | None = None,
    supplement_plan_path: str | Path | None = None,
    supplement_forecast_path: str | Path | None = None,
    calibration_only: bool = False,
    calibration_plan_path: str | Path | None = None,
    calibration_forecast_path: str | Path | None = None,
    regular_plan_path: str | Path | None = None,
    regular_forecast_path: str | Path | None = None,
) -> dict[str, Any]:
    """Run the separately approved development campaign from a completed pilot checkpoint."""
    scope: dict[str, Any] = {}
    if supplement_plan_path is not None or supplement_forecast_path is not None:
        scope.update(supplement_plan_path=supplement_plan_path, supplement_forecast_path=supplement_forecast_path)
    if regular_plan_path is not None or regular_forecast_path is not None:
        scope.update(regular_plan_path=regular_plan_path, regular_forecast_path=regular_forecast_path)
    if calibration_only or calibration_plan_path is not None or calibration_forecast_path is not None:
        scope.update(calibration_only=calibration_only, calibration_plan_path=calibration_plan_path,
                     calibration_forecast_path=calibration_forecast_path)
    return await _execute_stage(
        specification_path,
        checkpoint_directory,
        stage="development",
        authorization_path=authorization_path,
        resume=resume,
        bootstrap_only=bootstrap_only,
        bootstrap_forecast_path=bootstrap_forecast_path,
        **scope,
    )


async def evaluate_experiment(
    specification_path: str | Path,
    checkpoint_path: str | Path,
    output_directory: str | Path,
    *,
    authorization_path: str | Path | None = None,
) -> dict[str, Any]:
    """Evaluate only a frozen selected checkpoint with its separate exact test authorization."""
    from .preflight import inspect_preflight

    directory = Path(output_directory)
    saved_ledger = _read_ledger(directory / "expenditure_ledger.json")
    if saved_ledger is not None and any(entry.get("stage") == "test" for entry in saved_ledger.get("entries", ())):
        raise ValueError(
            "the output directory already records physical final-test requests; operator reconciliation is required and automatic replay of completed or unresolved test requests is disabled"
        )
    campaign = _campaign_directory(specification_path, directory, test=True)
    if Path(checkpoint_path).resolve().parent != directory.resolve().parent:
        raise ValueError("selected checkpoint must belong to the registered campaign directory")
    campaign_ledger = _read_ledger(campaign / "expenditure_ledger.json")
    if campaign_ledger is not None and any(
        entry.get("stage") == "test" for entry in campaign_ledger.get("entries", ())
    ):
        raise ValueError(
            "the campaign already records physical final-test requests; operator reconciliation is required and automatic replay is disabled"
        )
    preflight = inspect_preflight(
        specification_path, operation="test", authorization_path=authorization_path, checkpoint_path=checkpoint_path
    )
    preflight.require_ready()
    with _campaign_lock(campaign) as session_guard:
        validate_journal_resume(checkpoint_path, directory, campaign_directory=campaign)
        prepared = prepare_experiment(specification_path)
        from .supplement import prepare_checkpoint_condition

        prepared = prepare_checkpoint_condition(prepared,
            load_checkpoint(checkpoint_path), specification_path=specification_path)
        from ..providers.identity import with_registered_response_identity

        prepared = with_registered_response_identity(prepared, specification_path)
        trainer, hooks = build_experiment(prepared)
        trainer.resume_from_checkpoint(checkpoint_path, hooks=hooks)
        if authorization_path is None:
            raise ValueError("separate final-test authorization is required")
        hooks.authorize_stage(read_json(authorization_path))
        _write_run_inputs(directory, prepared, preflight)
        wire_durable_journal(trainer, hooks, directory, campaign_directory=campaign)
        hooks.execution_session_guard = session_guard
        try:
            record = await hooks.final_test(checkpoint_path)
        finally:
            if hooks.expenditure_ledger is not None:
                await hooks.expenditure_ledger.publish_snapshot([
                    directory / "expenditure_ledger.json", campaign / "expenditure_ledger.json"])
        result = record.to_dict()
        _write_json_atomic(directory / "test_results.json", result)
        return result
