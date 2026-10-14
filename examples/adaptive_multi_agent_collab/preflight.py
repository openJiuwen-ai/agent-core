"""Read-only readiness checks. No provider, tokenizer, encoder, or model is constructed."""

from __future__ import annotations

import copy
import hashlib
import importlib.metadata
import importlib.util
import json
import math
import os
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .benchmark import RECONCILE_REFERENCE
from .config import Config, NetworkConfig


@dataclass(frozen=True, slots=True)
class PreflightIssue:
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class PreflightReport:
    operation: str
    approval_fingerprint: str
    issues: tuple[PreflightIssue, ...]
    checks: Mapping[str, Any]

    @property
    def ready(self) -> bool:
        return not self.issues

    def to_dict(self) -> dict[str, Any]:
        return {"ready": self.ready, **asdict(self)}

    def require_ready(self) -> None:
        if self.issues:
            raise ValueError("execution preflight failed: " + "; ".join(item.message for item in self.issues))


STAGE_SCOPES = {
    "pilot": "pilot_only",
    "compatibility": "bounded_synthetic_compatibility",
    "development": "development_campaign",
    "test": "selected_checkpoint_three_test_runs",
}


def stage_authorization_template(
    *,
    stage: str,
    manifest_fingerprint: str,
    condition_fingerprint: str,
    resource_profile_fingerprint: str,
    approved_budget_usd: float,
    pilot_fingerprint: str | None = None,
    checkpoint_sha256: str | None = None,
    compatibility_plan_fingerprint: str | None = None,
) -> dict[str, Any]:
    if stage not in STAGE_SCOPES:
        raise ValueError("unknown authorization stage")
    return {
        "protocol": "experiment_stage_authorization_v2",
        "decision": "pending",
        "stage": stage,
        "manifest_fingerprint": manifest_fingerprint,
        "condition_fingerprint": condition_fingerprint,
        "resource_profile_fingerprint": resource_profile_fingerprint,
        "approved_budget_usd": approved_budget_usd,
        "pilot_fingerprint": pilot_fingerprint,
        "checkpoint_sha256": checkpoint_sha256,
        "compatibility_plan_fingerprint": compatibility_plan_fingerprint,
        "scope": STAGE_SCOPES[stage],
        "reviewer": "",
        "rationale": "",
    }


def validate_stage_authorization(
    value: Mapping[str, Any],
    *,
    stage: str,
    manifest_fingerprint: str,
    condition_fingerprint: str,
    resource_profile_fingerprint: str,
    approved_budget_usd: float,
    pilot_fingerprint: str | None = None,
    checkpoint_sha256: str | None = None,
    compatibility_plan_fingerprint: str | None = None,
) -> dict[str, Any]:
    expected = stage_authorization_template(
        stage=stage,
        manifest_fingerprint=manifest_fingerprint,
        condition_fingerprint=condition_fingerprint,
        resource_profile_fingerprint=resource_profile_fingerprint,
        approved_budget_usd=approved_budget_usd,
        pilot_fingerprint=pilot_fingerprint,
        checkpoint_sha256=checkpoint_sha256,
        compatibility_plan_fingerprint=compatibility_plan_fingerprint,
    )
    if not isinstance(value, Mapping) or set(value) != set(expected):
        raise ValueError("stage authorization requires exactly the declared binding and review fields")
    if value["decision"] != "approve_stage" or any(
        not isinstance(value[key], str) or not value[key].strip() for key in ("reviewer", "rationale")
    ):
        raise ValueError("stage execution requires an explicit approval decision, reviewer, and rationale")
    for key in ("manifest_fingerprint", "condition_fingerprint", "resource_profile_fingerprint"):
        if not isinstance(expected[key], str) or not re.fullmatch(r"[0-9a-f]{64}", expected[key]):
            raise ValueError("stage authorization requires complete trusted manifest/profile fingerprints")
    required = {
        "pilot_fingerprint": stage in {"development", "test"},
        "checkpoint_sha256": stage == "test",
        "compatibility_plan_fingerprint": stage == "compatibility",
    }
    for key, mandatory in required.items():
        if mandatory and (not isinstance(expected[key], str) or not re.fullmatch(r"[0-9a-f]{64}", expected[key])):
            raise ValueError(
                "stage authorization requires its exact pilot, checkpoint, or compatibility-plan fingerprint"
            )
        if not mandatory and expected[key] is not None:
            raise ValueError("authorization includes an unrelated stage binding")
    if not _positive_money(approved_budget_usd) or not _positive_money(value["approved_budget_usd"]):
        raise ValueError("stage authorization requires its finite positive registered USD ceiling")
    if any(value[key] != expected[key] for key in set(expected) - {"decision", "reviewer", "rationale"}):
        raise ValueError(
            "stage authorization differs from the exact manifest, pilot, profile, checkpoint, or spending scope"
        )
    return copy.deepcopy(dict(value))


def load_stage_authorization(path: str | Path, **bindings: Any) -> dict[str, Any]:
    return validate_stage_authorization(json.loads(Path(path).read_text(encoding="utf-8")), **bindings)


def independent_forecast_review_template(plan: Mapping[str, Any], pilot_budget_usd: float | None) -> dict[str, Any]:
    """Separate pending review evidence; never rewrite an immutable campaign manifest."""
    if not _positive_money(pilot_budget_usd):
        raise ValueError("forecast review requires the registered finite positive pilot ceiling")
    return {
        "protocol": "independent_pilot_forecast_review_v1",
        "decision": "pending",
        "scope": "pilot_forecast_review_only_no_activation_or_generation",
        "campaign_id": plan["registration"]["campaign_id"],
        "plan_fingerprint": plan["fingerprint"],
        **{key: plan[key] for key in ("manifest_fingerprint", "condition_fingerprint", "resource_profile_fingerprint")},
        "forecast_sha256": plan["pilot_forecast"]["sha256"],
        "pilot_budget_usd": pilot_budget_usd,
        "approval_source": {"path": None, "sha256": None},
        "reviewer": "",
        "rationale": "",
    }


def validate_independent_forecast_review(specification: Mapping[str, Any], source: Path) -> dict[str, Any]:
    """Review is exact, campaign-scoped and independent of activation/stage approval."""
    from .policy_activation import _check_reference, _load_independent_plan

    marker = specification["independent_campaign"]
    plan_path = Path(marker["policy_plan_path"])
    plan, candidate, prepared = _load_independent_plan(
        plan_path if plan_path.is_absolute() else source.parent / plan_path
    )
    if candidate != dict(specification):
        raise ValueError("forecast review belongs to a different independent manifest")
    _check_reference(plan["pilot_forecast"])
    expected = independent_forecast_review_template(plan, prepared.config.resources.pilot_budget)
    review_path = Path(plan["campaign_directory"]) / "pilot_forecast_review_pending.json"
    review = json.loads(review_path.read_text(encoding="utf-8"))
    mutable = {"decision", "approval_source", "reviewer", "rationale"}
    if (
        set(review) != set(expected)
        or review.get("decision") != "approve_forecast"
        or any(review[key] != expected[key] for key in set(expected) - mutable)
    ):
        raise ValueError("independent pilot forecast review is pending or mismatched")
    if any(not isinstance(review[key], str) or not review[key].strip() for key in ("reviewer", "rationale")):
        raise ValueError("forecast review requires its actual reviewer and rationale")
    if not isinstance(review["approval_source"], Mapping) or set(review["approval_source"]) != {"path", "sha256"}:
        raise ValueError("forecast review requires its exact preserved approval source")
    _check_reference(review["approval_source"])
    return review


EVIDENCE_STATES = frozenset({"documented", "provisional", "validated", "unavailable", "failed"})
EVIDENCE_DIMENSIONS = ("route_identity", "non_thinking", "token_counter", "native_usage", "billing")


def gateway_fingerprint_fields(gateway: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Exclude withdrawn account-policy metadata without renumbering existing campaign digests.

    The fixed historical slot below preserves serialization bytes used by the
    completed compatibility campaign. It is neither evidence nor a requirement:
    omitted, changed, or arbitrary input account-policy values are all ignored.
    Every actual request/binding field remains part of the digest.
    """
    if gateway is None:
        return None
    fields = copy.deepcopy(dict(gateway))
    fields["account_policy_evidence"] = (
        "Provisional for the user-authorized bounded synthetic compatibility pass: request disables known plugins "
        "and transforms; account dashboard enforcement has not been independently inspected. No claim of live validation."
    )
    return fields


def execution_specification(specification: Mapping[str, Any]) -> dict[str, Any]:
    """Separate observations and approval paperwork from immutable executed settings."""
    scientific = copy.deepcopy(dict(specification))
    for key in ("approvals", "attestations", "spending_plan"):
        scientific.pop(key, None)
    for backend in scientific.get("backends", {}).values():
        if "gateway" in backend:
            backend["gateway"] = gateway_fingerprint_fields(backend["gateway"])
    return scientific


def approval_fingerprint(specification: Mapping[str, Any], *, directory: Path | None = None) -> str:
    scientific = execution_specification(specification)
    if directory is not None:
        data = specification.get("dataset", {})
        paths = [*data.get("source_files", ()), *data.get("evaluation_id_files", ())]
        original = specification.get("benchmark", {}).get("evaluation_evidence", {}).get("local_source")
        if original:
            paths.append(original)
        artifacts = []
        for name in paths:
            path = Path(name)
            path = path if path.is_absolute() else directory / path
            digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
            artifacts.append({"path": str(path.resolve()), "sha256": digest})
        scientific["local_artifact_checksums"] = artifacts
    return hashlib.sha256(json.dumps(scientific, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _positive_money(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value) and value > 0


def _hub_snapshot(model: str, revision: str, cache_directory: str | None = None) -> Path:
    default = Path(os.environ.get("HF_HOME", str(Path.home() / ".cache/huggingface"))) / "hub"
    return (
        Path(cache_directory or os.environ.get("HF_HUB_CACHE", str(default)))
        / ("models--" + model.replace("/", "--"))
        / "snapshots"
        / revision
    )


def _tokenizer_present(profile: Any) -> bool:
    if profile.tokenizer_kind == "conservative":
        return bool(profile.estimator_approval and profile.estimator_evidence)
    if profile.tokenizer_kind == "transformers":
        snapshot = _hub_snapshot(profile.tokenizer_name, profile.tokenizer_revision, profile.tokenizer_cache_directory)
        return (snapshot / "tokenizer_config.json").is_file() and (snapshot / "tokenizer.json").is_file()
    from .assets import TIKTOKEN_URL, TIKTOKEN_SHA256

    try:
        installed = importlib.metadata.version("tiktoken")
    except importlib.metadata.PackageNotFoundError:
        return False
    cache = Path(
        profile.tokenizer_cache_directory or os.environ.get("TIKTOKEN_CACHE_DIR", "/nonexistent-tokenizer-cache")
    )
    vocabulary = cache / hashlib.sha1(TIKTOKEN_URL.encode()).hexdigest()
    return (
        profile.tokenizer_name == "o200k_base"
        and installed == profile.tokenizer_revision
        and vocabulary.is_file()
        and hashlib.sha256(vocabulary.read_bytes()).hexdigest() == TIKTOKEN_SHA256
        and profile.chat_tokens_per_message is not None
        and profile.chat_priming_tokens is not None
        and profile.counting_method == "estimated"
    )


PRIMARY_MODELS = ("openai/gpt-4.1-mini", "google/gemini-2.5-flash", "anthropic/claude-sonnet-4.6")
PRIMARY_INSTRUCTION = (
    "You are a careful reasoning assistant. Follow the task-specific protocol and keep explanations concise."
)
REFERENCE_RATES = tuple(
    (input_price / 1e6, output_price / 1e6) for input_price, output_price in ((0.4, 1.6), (0.3, 2.5), (3.0, 15.0))
)


def primary_condition_errors(specification: Mapping[str, Any], config: Config) -> list[str]:
    """Keep the registered Appendix C scientific condition fixed before any paid stage."""
    from .assets import MINILM_MODEL, MINILM_REVISION
    from .config import CostConfig, SafetyConfig, TerminationConfig, TrainingConfig, ResourceConfig

    errors = []
    if any(agent.prompt_collection_version != "seed_prompt_collection_v4" for agent in config.agent_pool):
        errors.append("current phase-specific seed prompt collection")
    if config.n_agents != 3 or tuple(agent.model_name for agent in config.agent_pool) != PRIMARY_MODELS:
        errors.append("fixed ordered three-model collaboration pool")
    if any(
        agent.agent_id != index
        or agent.system_configuration != PRIMARY_INSTRUCTION
        or agent.role_configuration
        or dict(agent.decoding_configuration) != {"temperature": "0.7"}
        for index, agent in enumerate(config.agent_pool)
    ):
        errors.append("fixed agent order, common instruction, and temperature 0.7")
    if (
        config.network != NetworkConfig()
        or config.encoders.numerical_precision != "float32"
        or config.encoders.max_tokens != 256
    ):
        errors.append("Appendix A numerical architecture and encoder chunk precision")
    if (config.encoders.encoder_type, config.encoders.model_name, config.encoders.model_revision) != (
        "minilm",
        MINILM_MODEL,
        MINILM_REVISION,
    ):
        errors.append("predeclared immutable MiniLM revision")
    expected_cost = CostConfig(
        normalization=0.01,
        input_token_weight=0,
        output_token_weight=0,
        model_call_weight=0,
        monetary_weight=1,
        lambda_session=0.002,
        lambda_conversation=0.002,
    )
    if config.costs != expected_cost or config.selection.lambda_select != 0.002:
        errors.append("reference-dollar-only cost, 0.01 normalization, and three 0.002 coefficients")
    if config.capacity.resolve(config.n_agents) != ((2, 2, 2), (2, 2, 2)):
        errors.append("outgoing and incoming capacity two for every agent")
    if (
        config.safety != SafetyConfig()
        or config.termination != TerminationConfig()
        or config.training != TrainingConfig()
    ):
        errors.append("registered safeguards, provisional termination, and Appendix B training schedule")
    independent = specification.get("independent_campaign", {}).get("protocol") == "independent_campaign_v1"
    # Successor pilots preserve the project compatibility ceiling but cannot
    # spend it. Its value no longer requires a predecessor's recheck condition.
    allowed_attempts = 48 if independent or specification.get("role_recheck", {}).get("protocol") == "role_contract_recheck_v1" else 24
    if config.resources != ResourceConfig(
        compatibility_budget=3,
        compatibility_max_attempts=allowed_attempts,
        pilot_budget=5,
        development_budget=80,
        test_budget=20,
        currency="USD",
    ):
        errors.append(
            "registered request caps, explicitly authorized compatibility attempt limit, and USD3/5/80/20 stage ceilings"
        )
    if (
        config.seed != 42
        or specification.get("dataset", {}).get("split_seed") != 42
        or specification.get("active_data", {}).get("seed") != 42
    ):
        errors.append("master, split, and active-subset seeds 42")
    if tuple(specification.get("final_test", {}).get("run_seeds", ())) != (42, 43, 44):
        errors.append("three final test runs with seeds 42, 43, 44")
    if specification.get("benchmark", {}).get("model_pool") != "matched_backbone_protocol":
        errors.append("matched-backbone protocol comparison scope")
    return errors


def authorization_bindings(
    prepared: Any, stage: str, checkpoint_path: str | Path | None = None
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Inspect a trusted local checkpoint without constructing models or restoring state."""
    from .experiment import planning_hooks

    hooks = planning_hooks(prepared)
    pilot_fingerprint = None
    checkpoint_hash = None
    if stage in {"development", "test"} and checkpoint_path is None:
        raise ValueError("development/test authorization requires the completed pilot checkpoint")
    if checkpoint_path is not None:
        import torch
        from .calibration import SafetyCalibrationRecord

        raw = Path(checkpoint_path).read_bytes()
        checkpoint_hash = hashlib.sha256(raw).hexdigest() if stage == "test" else None
        payload = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
        from .training import CheckpointManager

        if payload.get("schema_version") != CheckpointManager.schema_version:
            raise ValueError("checkpoint execution schema is incompatible with this revised condition")
        state = payload.get("hook_state", {})
        if payload.get("request_boundary_pending") is not False:
            raise ValueError("checkpoint has an unresolved physical request boundary")
        if state.get("condition") != hooks._condition():
            # Tuple/list normalization matches checkpoint validators.
            if json.dumps(state.get("condition"), sort_keys=True) != json.dumps(hooks._condition(), sort_keys=True):
                raise ValueError("checkpoint condition differs from the prepared manifest")
        if stage in {"development", "test"}:
            if not payload.get("trainer_state", {}).get("pilot_complete") or not state.get("safety_record"):
                raise ValueError("checkpoint does not contain the complete pilot report")
            pilot = SafetyCalibrationRecord.from_dict(state["safety_record"])
            if not pilot.execution_complete:
                raise ValueError("checkpoint pilot report is incomplete or operationally halted")
            pilot_fingerprint = pilot.fingerprint
    plan = hooks.compatibility_plan() if stage == "compatibility" else None
    return {
        "stage": stage,
        "manifest_fingerprint": prepared.authorization_manifest_fingerprint,
        "condition_fingerprint": hooks.condition_fingerprint,
        "resource_profile_fingerprint": prepared.resource_profile_fingerprint,
        "approved_budget_usd": getattr(prepared.config.resources, f"{stage}_budget"),
        "pilot_fingerprint": pilot_fingerprint,
        "checkpoint_sha256": checkpoint_hash,
        "compatibility_plan_fingerprint": None if plan is None else plan["fingerprint"],
    }, plan


def inspect_preflight(
    specification_path: str | Path,
    *,
    operation: str = "train",
    authorization_path: str | Path | None = None,
    checkpoint_path: str | Path | None = None,
) -> PreflightReport:
    """Aggregate missing prerequisites. Credentials never imply authorization."""
    stage = "development" if operation == "train" else operation
    if stage not in STAGE_SCOPES:
        raise ValueError("preflight operation must be compatibility, pilot, train, development, or test")
    source = Path(specification_path).resolve()
    specification = json.loads(source.read_text(encoding="utf-8"))
    fingerprint = approval_fingerprint(specification, directory=source.parent)
    issues: list[PreflightIssue] = []
    checks: dict[str, Any] = {
        "network_accessed": False,
        "models_constructed": False,
        "historical_reference": RECONCILE_REFERENCE,
        "comparison_scope": "matched_backbone_protocol_comparison",
        "credential_presence_is_authorization": False,
        "requested_stage": stage,
    }

    def missing(code: str, message: str) -> None:
        issues.append(PreflightIssue(code, message))

    campaign_path = Path(specification.get("campaign_directory", "runs/unconfigured"))
    campaign_path = campaign_path if campaign_path.is_absolute() else source.parent / campaign_path
    journal_path = campaign_path / "expenditure_ledger.json"
    try:
        from .experiment import validate_revised_condition_lineage

        validate_revised_condition_lineage(specification, source.parent)
    except (ValueError, TypeError, KeyError, OSError) as error:
        missing("revised_condition_lineage", str(error))
    if journal_path.is_file():
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
        if specification.get("policy_transition") or specification.get("independent_campaign"):
            try:
                from .policy_activation import bind_policy_dispatch
                from .resources import ExpenditureLedger

                candidate = Config.from_dict(specification.get("config", {}))
                preview = ExpenditureLedger(candidate.resources)
                # This detached read-only ledger cannot grant production dispatch.
                bind_policy_dispatch(preview, source, lambda: True)
                preview.assert_dispatch_allowed()
                checks["bounded_failure_policy"] = {
                    "activated": True,
                    "historical_halt_preserved": bool(journal.get("halted")),
                    "independent_campaign": bool(specification.get("independent_campaign")),
                    "pilot_approval_is_separate": True,
                }
            except (ValueError, TypeError, KeyError, OSError, RuntimeError) as error:
                missing("failure_policy_activation", str(error))
        elif journal.get("halted"):
            missing(
                "campaign_halted",
                "the durable campaign has a sticky dispatch halt; operator reconciliation is required before any further calls",
            )
    try:
        config = Config.from_dict(specification.get("config", {}))
    except (TypeError, ValueError, KeyError):
        missing("configuration", "scientific configuration is invalid")
        return PreflightReport(operation, fingerprint, tuple(issues), checks)
    if config.failure_policy.enabled and not (
        specification.get("policy_transition") or specification.get("independent_campaign")
    ):
        missing("failure_policy_activation", "enabled policy requires its separately approved condition transition")
    for message in primary_condition_errors(specification, config):
        missing(
            "primary_condition",
            "restore the " + message + "; changing it requires a separately registered experiment",
        )
    for library in ("torch", "transformers", "textgrad", "tiktoken", "huggingface_hub"):
        if importlib.util.find_spec(library) is None:
            missing("dependency", f"required library is not installed: {library}")
    from .assets import inspect_assets

    asset_directory = source.parent / specification.get("asset_directory", "assets")
    checks["assets"] = inspect_assets(asset_directory)
    if not checks["assets"]["ready"]:
        missing(
            "assets",
            "pinned data, encoder, tokenizer files and their receipt must be present and verified locally",
        )
    prepared = None
    try:
        from .experiment import prepare_experiment

        prepared = prepare_experiment(source)
        checks["dataset_fingerprint"] = prepared.dataset.fingerprint
        checks["partition_counts"] = {
            "train": len(prepared.dataset.train),
            "component_validation": len(prepared.dataset.component_validation),
            "system_validation": len(prepared.dataset.system_validation),
            "test": len(prepared.dataset.test),
        }
        checks["active_counts"] = {
            "train": len(prepared.active_data_plan.train_ids),
            "component_validation": len(prepared.active_data_plan.component_validation_ids),
            "system_validation": len(prepared.active_data_plan.system_validation_ids),
            "pilot": len(prepared.active_data_plan.pilot_ids),
        }
        checks["benchmark_verification"] = prepared.benchmark_provenance.get("verification")
        if len(prepared.dataset.test) != 100 or prepared.dataset.provenance.evaluation_protocol != "reconcile_main100":
            missing("test_provenance", "the initial condition requires the exact verified protected main-100")
    except (ImportError, OSError, TypeError, ValueError, KeyError, AttributeError) as error:
        missing(
            "data_plan",
            f"local data/provenance/active plan is incomplete or incompatible ({type(error).__name__})",
        )
    from .resources import BackendResourceProfile
    from .gateway import OpenRouterBinding, OPENROUTER_BASE_URL

    backends = prepared.backends if prepared is not None else specification.get("backends", {})
    capabilities: dict[str, Any] = {}
    for index, logical in enumerate(config.agent_pool):
        key = logical.backend_key
        backend = backends.get(key, {})
        if any(backend.get(name) for name in ("api_key", "token", "password", "cookie")):
            missing(
                "credential_storage",
                f"backend {key} must reference environment credentials instead of embedding secrets",
            )
        env = backend.get("api_key_env")
        if not isinstance(env, str) or not os.environ.get(env):
            missing("credentials", f"backend {key} credential variable is absent or unset")
        if backend.get("provider") != "OpenRouter" or backend.get("api_base") != OPENROUTER_BASE_URL:
            missing("backend_transport", f"backend {key} requires the audited public OpenRouter client")
        try:
            binding = OpenRouterBinding.from_dict(backend.get("gateway", {}))
            binding.validate()
            if binding.model != logical.model_name or binding.supports_seed != bool(
                backend.get("supports_seed", False)
            ):
                raise ValueError("binding disagrees")
            if (
                backend.get("revision") != binding.revision_binding
                or logical.model_revision != binding.revision_binding
            ):
                raise ValueError("revision binding disagrees")
            if specification.get("independent_campaign") or specification.get("role_recheck", {}).get("protocol") == "role_contract_recheck_v1":
                from .output_contract import validate_role_output_profile

                if backend.get("role_output") is None:
                    raise ValueError("role output mode must be explicitly frozen before recheck")
                validate_role_output_profile(backend["role_output"], binding)
                checks.setdefault("role_output_modes", {})[key] = backend["role_output"]["mode"]
        except (TypeError, ValueError, KeyError, OSError):
            missing(
                "gateway_binding",
                f"backend {key} needs approved exact endpoint/revision and non-thinking/seed evidence",
            )
        if (
            backend.get("decoding") != {"temperature": 0.7}
            or backend.get("generation_mode") != "standard_non_thinking"
            or set(backend.get("omitted_parameters", ()))
            != {"top_p", "top_k", "frequency_penalty", "presence_penalty", "stop"}
            or not backend.get("backend_defaults_evidence")
        ):
            missing(
                "decoding",
                f"backend {key} must record temperature 0.7, non-thinking mode, and omitted backend defaults",
            )
        evidence = specification.get("attestations", {}).get("backends", {}).get(key, {})
        checks.setdefault("evidence_states", {})[key] = {
            name: evidence.get(name, {}).get("state", "unavailable") for name in EVIDENCE_DIMENSIONS
        }
        for name in EVIDENCE_DIMENSIONS:
            item = evidence.get(name, {})
            state = item.get("state", "unavailable")
            if state not in EVIDENCE_STATES:
                missing("evidence_state", f"backend {key} has an invalid {name} evidence state")
            elif stage == "compatibility":
                if state not in {"documented", "provisional", "validated"} or not item.get("evidence"):
                    missing(
                        "compatibility_evidence",
                        f"backend {key} needs honest documented/provisional {name} evidence for bounded compatibility only",
                    )
            elif state != "validated":
                missing(
                    "live_evidence",
                    f"backend {key} {name} remains {state}; pilot and later stages require validated evidence",
                )
        verification = backend.get("billing_verification", {})
        if stage == "compatibility" and not (
            verification.get("catalog_fetched_at_utc")
            and verification.get("catalog_sha256")
            and verification.get("evidence")
        ):
            missing(
                "billing_verification",
                f"backend {key} bounded compatibility requires current documented native tariff evidence",
            )
        try:
            profile = BackendResourceProfile.from_dict(backend.get("resources", {}))
            capabilities[key] = asdict(profile)
            expected_factor, expected_tokens = (1.2, 32) if index == 0 else (2.0, 128)
            if (
                profile.require_native_accounting is not True
                or profile.reservation_safety_factor < 1.5
                or profile.tokenizer_kind != "tiktoken"
                or profile.tokenizer_name != "o200k_base"
                or profile.tokenizer_revision != "0.13.0"
                or profile.counting_method != "estimated"
                or profile.counting_safety_factor != expected_factor
                or profile.counting_safety_tokens != expected_tokens
                or profile.chat_tokens_per_message != 16
                or profile.chat_priming_tokens != 32
            ):
                missing(
                    "primary_counter_profile",
                    f"backend {key} must retain the explicitly approved provisional counter, native accounting, and independent reservation margin",
                )
            if (
                (profile.reference_input_price, profile.reference_output_price) != REFERENCE_RATES[index]
                or profile.reference_pricing_date != "2026-09-18"
                or profile.currency != "USD"
            ):
                missing("reference_tariff", f"backend {key} must retain its frozen ordinary reference USD prices")
            if profile.counting_method == "estimated":
                expected_approval = counting_profile_fingerprint(backend)
                if profile.estimator_approval != expected_approval or not evidence.get("token_counter", {}).get(
                    "evidence"
                ):
                    missing(
                        "counter_approval",
                        f"backend {key} estimated admission counter needs explicit approval of its exact provisional profile; later stages require native-usage validation",
                    )
            if not _tokenizer_present(profile):
                missing(
                    "backend_tokenizer_files",
                    f"backend {key} local request counter is not available or not explicitly approved",
                )
            if index > 0 and profile.tokenizer_name == "sentence-transformers/all-MiniLM-L6-v2":
                missing(
                    "backend_counter",
                    f"backend {key} cannot claim the feature encoder is its native request tokenizer",
                )
            for role in (
                ("initial", "revision", "judge", "tie", "textgrad_backward")
                if index == 0
                else ("initial", "revision", "judge", "tie")
            ):
                input_cap, output_cap = config.resources.caps(role)
                if (
                    not profile.min_output_tokens <= output_cap <= profile.max_output_tokens
                    or input_cap + output_cap + config.resources.context_reserve > profile.context_limit
                ):
                    missing("backend_limits", f"backend {key} cannot support the fixed {role} request envelope")
        except (OSError, TypeError, ValueError, KeyError):
            missing(
                "backend_resources",
                f"backend {key} requires complete tokenizer/estimator, native limits, billing and frozen reference tariffs",
            )
    checks["backend_resources_valid"] = sorted(capabilities)
    tg = specification.get("textgrad", {})
    try:
        from .textgrad_optimizer import TextGradConfig

        tc = TextGradConfig(**tg.get("config", {}))
        first = config.agent_pool[0].backend_key
        if tg.get("backward_backend") != first or tg.get("optimizer_backend") != first:
            raise ValueError("both engines must share agent one binding")
        for role in ("backward", "optimizer"):
            if (
                getattr(tc, f"{role}_engine") != PRIMARY_MODELS[0]
                or getattr(tc, f"{role}_revision") != backends[first].get("revision")
                or dict(getattr(tc, f"{role}_decoding")) != {"temperature": 0.0}
                or getattr(tc, f"{role}_supports_seed") != bool(backends[first].get("supports_seed", False))
            ):
                raise ValueError("engine identity or temperature differs")
        if (
            tc.run_seed != 42
            or tc.package_version != "0.1.8"
            or tc.gradient_memory != 0
            or tc.momentum != 0
            or tc.call_timeout_seconds != 120
            or tc.proposal_timeout_seconds != 600
            or importlib.metadata.version("textgrad") != tc.package_version
        ):
            raise ValueError("TextGrad protocol differs")
    except (
        ImportError,
        TypeError,
        ValueError,
        KeyError,
        AttributeError,
        importlib.metadata.PackageNotFoundError,
    ):
        missing(
            "textgrad_configuration",
            "TextGrad must use both agent-one GPT bindings/tokenizers at temperature zero with the fixed package/deadlines and no engine escalation",
        )
    for spending_stage, budget in ((stage, {"compatibility": 3, "pilot": 5, "development": 80, "test": 20}[stage]),):
        plan = specification.get("spending_plan", {}).get(spending_stage, {})
        estimate, headroom = plan.get("estimated_cost_usd"), plan.get("headroom_usd")
        reviewed = plan.get("reviewed") is True
        if specification.get("independent_campaign") and spending_stage == "pilot":
            try:
                review = validate_independent_forecast_review(specification, source)
                reviewed = True
                checks["pilot_forecast_review"] = {
                    "approved": True,
                    "forecast_sha256": review["forecast_sha256"],
                    "generation_authorized": False,
                }
            except (OSError, ValueError, TypeError, KeyError) as error:
                reviewed = False
                checks["pilot_forecast_review"] = {"approved": False, "reason": str(error)}
        if (
            not reviewed
            or not plan.get("basis")
            or not _positive_money(estimate)
            or not _positive_money(headroom)
            or estimate + headroom > budget
        ):
            missing(
                "spending_headroom",
                f"{spending_stage} needs a reviewed cost projection and explicit safety headroom within its USD {budget} ceiling",
            )
    if stage != "compatibility":
        try:
            validate_finalized_compatibility(specification, source)
        except (OSError, ValueError, TypeError, KeyError):
            missing(
                "compatibility_validation",
                "the exact completed compatibility report and independent validation evidence must be finalized before pilot or later stages",
            )
    if prepared is not None:
        try:
            bindings, plan = authorization_bindings(prepared, stage, checkpoint_path)
            checks["stage_authorization_template"] = stage_authorization_template(**bindings)
            if plan is not None:
                checks["compatibility_plan"] = plan
            if authorization_path is None:
                missing(
                    "stage_authorization",
                    f"explicit separate {stage} authorization is required; limits and credentials do not grant it",
                )
            else:
                load_stage_authorization(authorization_path, **bindings)
        except (OSError, TypeError, ValueError, KeyError, AttributeError):
            missing(
                "stage_authorization",
                "stage authorization cannot be validated against the complete exact manifest, resource profile, pilot report and selected checkpoint",
            )
    else:
        missing("stage_authorization", "stage authorization requires a complete prepared condition")
    return PreflightReport(operation, fingerprint, tuple(issues), checks)


def counting_profile_fingerprint(backend: Mapping[str, Any]) -> str:
    profile = copy.deepcopy(dict(backend.get("resources", {})))
    profile.pop("estimator_approval", None)
    profile.pop("tokenizer_cache_directory", None)
    return hashlib.sha256(
        json.dumps(
            {
                "model": backend.get("model_name"),
                "gateway": gateway_fingerprint_fields(backend.get("gateway")),
                "profile": profile,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def validate_finalized_compatibility(specification: Mapping[str, Any], source: Path) -> Mapping[str, Any]:
    """Verify evidence against the durable recorded bounded experiment, never provider claims alone."""
    from .experiment import prepare_experiment, planning_hooks

    if specification.get("independent_campaign"):
        from .campaigns import shared_compatibility_evidence

        return shared_compatibility_evidence(specification, source.parent)
    campaign = Path(specification["campaign_directory"])
    campaign = campaign if campaign.is_absolute() else source.parent / campaign
    if specification.get("revised_condition") and (campaign / "active_condition.json").is_file():
        from .transition import validate_revised_recheck_evidence

        return validate_revised_recheck_evidence(source)
    report_path = campaign / "compatibility_report.json"
    raw = report_path.read_bytes()
    report = json.loads(raw)
    summary = specification.get("attestations", {}).get("compatibility", {})
    if summary.get("state") != "validated" or summary.get("report_sha256") != hashlib.sha256(raw).hexdigest():
        raise ValueError("finalized compatibility evidence does not match its recorded report")
    expected = planning_hooks(prepare_experiment(source)).compatibility_plan()
    if (
        report.get("plan") != expected
        or report.get("status") != "complete"
        or report.get("assessment", {}).get("passed") is not True
    ):
        raise ValueError("the recorded compatibility plan did not pass all independent checks")
    if (
        set(report["assessment"].get("backends", {})) != {"0", "1", "2"}
        or report["assessment"].get("textgrad", {}).get("status") != "validated"
    ):
        raise ValueError("compatibility evidence must cover every fixed backend and all TextGrad roles")
    for key, evidence in report["assessment"]["backends"].items():
        if any(evidence.get(name, {}).get("status") != "validated" for name in EVIDENCE_DIMENSIONS):
            raise ValueError("backend compatibility evidence is incomplete")
    return report


def finalize_compatibility_evidence(specification_path: str | Path, report_path: str | Path) -> dict[str, Any]:
    """Copy recorded findings into mutable attestations without changing any executed condition."""
    import torch
    from .experiment import prepare_experiment, planning_hooks, _write_json_atomic, validate_journal_resume

    source = Path(specification_path).resolve()
    specification = json.loads(source.read_text(encoding="utf-8"))
    campaign = Path(specification["campaign_directory"])
    campaign = (campaign if campaign.is_absolute() else source.parent / campaign).resolve()
    target = Path(report_path).resolve()
    if target != campaign / "compatibility_report.json":
        raise ValueError("finalization requires the canonical campaign compatibility report")
    checkpoint = campaign / "checkpoint.pt"
    # This permits evidence recording only. The journal and checkpoint must
    # agree, and ordinary execution paths continue to reject the sticky halt.
    validate_journal_resume(checkpoint, campaign, allow_halted=True)
    payload = torch.load(checkpoint, map_location="cpu", weights_only=False)
    raw = target.read_bytes()
    report = json.loads(raw)
    recorded = payload.get("hook_state", {}).get("compatibility_records", [])
    if not any(json.dumps(item, sort_keys=True) == json.dumps(report, sort_keys=True) for item in recorded):
        raise ValueError("compatibility report differs from its durable checkpoint record")
    prepared = prepare_experiment(source)
    if report.get("plan") != planning_hooks(prepared).compatibility_plan():
        raise ValueError("compatibility report belongs to different executed settings")
    if payload.get("trainer_state", {}).get("phase") != "unstarted":
        raise ValueError("evidence finalization must precede pilot and all development")
    if report.get("status") not in {"complete", "failed"}:
        raise ValueError("compatibility execution is not at a completed boundary")
    evidence = specification.setdefault("attestations", {})
    assessment = report.get("assessment", {})
    for index, backend_evidence in assessment.get("backends", {}).items():
        key = prepared.config.agent_pool[int(index)].backend_key
        for name, item in backend_evidence.items():
            if name not in EVIDENCE_DIMENSIONS or item.get("status") not in EVIDENCE_STATES:
                raise ValueError("compatibility assessment has an unsupported evidence field or state")
            evidence.setdefault("backends", {}).setdefault(key, {})[name] = {
                "state": item["status"],
                "evidence": str(target),
                "report_sha256": hashlib.sha256(raw).hexdigest(),
                "details": copy.deepcopy(item),
            }
            if name == "billing":
                dispatch_ids = set(item.get("evidence_dispatch_ids", ()))
                evidence["backends"][key][name]["observed_billing_modes"] = sorted(
                    {
                        row["observed_billing_mode"]
                        for request in report.get("requests", ())
                        for row in request.get("assessment_receipts", ())
                        if row.get("dispatch_id") in dispatch_ids
                        and row.get("observed_billing_mode") in {"credits", "byok"}
                        and not row.get("generation_confirmed_not_started")
                    }
                )
    textgrad = assessment.get("textgrad", {"status": "unavailable"})
    evidence["textgrad"] = {"state": textgrad["status"], "details": copy.deepcopy(textgrad)}
    evidence["compatibility"] = {
        "state": ("validated" if assessment.get("passed") is True and report.get("status") == "complete" else "failed"),
        "report_sha256": hashlib.sha256(raw).hexdigest(),
        "report": str(target),
        "plan_fingerprint": report["plan"]["fingerprint"],
    }
    if execution_specification(specification) != execution_specification(
        json.loads(source.read_text(encoding="utf-8"))
    ):
        raise ValueError("evidence finalization attempted to change immutable execution settings")
    _write_json_atomic(source, specification)
    updated = prepare_experiment(source)
    if (
        updated.specification_sha256,
        updated.authorization_manifest_fingerprint,
        updated.resource_profile_fingerprint,
    ) != (
        prepared.specification_sha256,
        prepared.authorization_manifest_fingerprint,
        prepared.resource_profile_fingerprint,
    ):
        raise ValueError("evidence-only finalization changed an execution fingerprint")
    bindings, _ = authorization_bindings(updated, "pilot")
    template = stage_authorization_template(**bindings)
    _write_json_atomic(campaign / "pilot_authorization_template.json", template)
    return {
        "state": evidence["compatibility"]["state"],
        "report_sha256": evidence["compatibility"]["report_sha256"],
        "execution_fingerprints_unchanged": True,
        "pilot_authorization": "pending",
        "pilot_authorization_template": str(campaign / "pilot_authorization_template.json"),
        "resume_checkpoint": str(checkpoint),
    }
