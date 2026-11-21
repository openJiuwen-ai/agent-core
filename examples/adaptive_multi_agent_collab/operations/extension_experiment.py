"""Registered incremental extensions using the ordinary production runtime.

Preparation is local and unactivated. Adaptation requires a separately reviewed
extension authorization, an existing campaign lock, and the ordinary approved
development/resource gates. This module never obtains credentials or approvals.
"""
from __future__ import annotations

from ..infrastructure.storage import load_checkpoint

import copy
import asyncio
import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

import torch

from ..learning.evaluation import EvaluationRecord
from .experiment import build_experiment, build_model, prepare_experiment, prepare_extension_parent, extension_policy_manifest, _write_json_atomic
from ..learning.extension import EXTENSION_PROTOCOL, ExtensionPlan, RestrictedAdaptation, check_parent_retention, block_optimizer, named_optimizer_state, scope_configuration
from ..models.policies import expand_models, models_from_expansion_metadata
from ..workflows.prompts import PromptCollection, seed_prompt_collection
from ..learning.training import _fingerprint, evaluating


def _reference(value: Mapping[str, Any], directory: Path) -> Path:
    if set(value) != {"path", "sha256"}:
        raise ValueError("extension inputs require exact content-hashed references")
    path = Path(value["path"])
    path = path if path.is_absolute() else directory / path
    if hashlib.sha256(path.read_bytes()).hexdigest() != value["sha256"]:
        raise ValueError("extension source reference changed: " + str(path))
    return path.resolve()


def _canonical_campaign(manifest: str | Path) -> Path:
    source = extension_policy_manifest(manifest)
    value = json.loads(source.read_text())
    campaign = Path(value["campaign_directory"])
    return (campaign if campaign.is_absolute() else source.parent / campaign).resolve()


def _matching_parent_evaluation(payload: Mapping[str, Any], expected_plan: Any,
                                requested: EvaluationRecord) -> None:
    """A subset may reuse its unchanged complete selected-parent predictions."""
    selected = EvaluationRecord.from_dict(payload["best_system"]["evaluation"])
    if selected.plan.fingerprint != expected_plan.fingerprint:
        raise ValueError("parent comparison validation condition differs")
    by_id = {item.example_id:item for item in selected.examples}
    ids = requested.plan.ordered_example_ids
    if any(identifier not in by_id for identifier in ids):
        raise ValueError("matching selected-parent evidence is unavailable for requested extension queries")
    projection = EvaluationRecord(replace(selected.plan, ordered_example_ids=ids),
                                  tuple(by_id[identifier] for identifier in ids), selected.objective)
    if requested.to_dict() != projection.to_dict():
        raise ValueError("parent comparison must match its selected joint checkpoint exactly")


def _scope_usage(ledger: Any, scope: str) -> dict[str, Any]:
    entries = [item for item in ledger.entries if item.get("scope_id") == scope and item.get("status") != "not-dispatched"]
    result = {"dispatch_identities": [item["dispatch_id"] for item in entries],
              "physical_attempts_and_inflight_reservations": len(entries)}
    for field in ("input_tokens", "output_tokens", "total_cost"):
        values = [ledger.effective_usage(item).get(field) for item in entries]
        result[field] = sum(values) if all(value is not None for value in values) else None
        result["known_" + field] = sum(value for value in values if value is not None)
        result["missing_" + field + "_requests"] = sum(value is None for value in values)
    return result


def validate_extension_registration(path: str | Path) -> dict[str, Any]:
    source = Path(path).resolve()
    value = json.loads(source.read_text())
    required = {"protocol", "plan", "parent_manifest", "parent_checkpoint", "parent_evaluation", "parent_states", "extension_manifest"}
    if "parent_supplement_plan" in value:
        required.add("parent_supplement_plan")
    if set(value) != required or value["protocol"] != EXTENSION_PROTOCOL:
        raise ValueError("extension registration requires the complete versioned source lineage")
    plan = ExtensionPlan.from_dict(value["plan"])
    refs = {name: _reference(value[name], source.parent) for name in required - {"protocol", "plan"}}
    parent, extension = prepare_extension_parent(refs), prepare_experiment(refs["extension_manifest"])
    if parent.specification_sha256 != plan.parent_condition or extension.specification_sha256 != plan.extension_condition:
        raise ValueError("extension plan must bind the actual parent and enlarged conditions")
    if parent.dataset.fingerprint != extension.dataset.fingerprint:
        raise ValueError("an extension may not repartition data")
    for partition, identifiers in (("train",plan.train_ids),("comp-val",plan.component_validation_ids),("sys-val",plan.system_validation_ids)):
        extension.dataset.select(partition,identifiers)
    if (extension.active_data_plan.train_ids,extension.active_data_plan.component_validation_ids,extension.active_data_plan.system_validation_ids) != (
            plan.train_ids,plan.component_validation_ids,plan.system_validation_ids):
        raise ValueError("extension manifest subsets must match the predeclared plan")
    pc, ec = parent.config, extension.config
    # Only declared additions/schedules/termination candidates differ. Costs,
    # encoders, inherited settings and every existing agent remain unchanged.
    if ec.n_agents < pc.n_agents or ec.agent_pool[:pc.n_agents] != pc.agent_pool:
        raise ValueError("extension cannot reassign or alter an inherited agent")
    if ec.scheme_registry[:len(pc.scheme_registry)] != pc.scheme_registry:
        raise ValueError("scheme registry is append-only")
    for name in ("network","encoders","costs","selection","seed","safety","resources","failure_policy","workflow_recovery"):
        if getattr(pc,name) != getattr(ec,name):
            raise ValueError("extension changed an unrelated inherited setting: " + name)
    if ec.training.p_mix != pc.training.p_mix:
        raise ValueError("extension cannot rescale mixed targets")
    old_out,old_in = pc.capacity.resolve(pc.n_agents)
    new_out,new_in = ec.capacity.resolve(ec.n_agents)
    if new_out[:pc.n_agents] != old_out or new_in[:pc.n_agents] != old_in:
        raise ValueError("extension cannot silently enlarge inherited capacities")
    if ec.training.weighting_phase != plan.weighting_phase or ec.training.routing_phase != plan.routing_phase or ec.training.prompt_phase != plan.prompt_phase:
        raise ValueError("manifest phase budgets differ from the extension plan")
    if ec.termination not in plan.calibration_candidates:
        raise ValueError("inherited termination tuple must be a calibration candidate")
    new_labels = {item.label for item in ec.scheme_registry[len(pc.scheme_registry):]}
    permitted_prompts = {f"{kind}:{label}" for label in new_labels for kind in ("init","role","revision")}
    if set(plan.prompt_components) - permitted_prompts:
        raise ValueError("initial restricted TextGrad scope permits only new-scheme components")
    evaluation = EvaluationRecord.from_dict(json.loads(refs["parent_evaluation"].read_text()))
    if tuple(evaluation.plan.ordered_example_ids) != plan.system_validation_ids or evaluation.score is None:
        raise ValueError("complete matching parent validation evidence required")
    return {"protocol":EXTENSION_PROTOCOL,"plan_fingerprint":plan.fingerprint,"registration_fingerprint":_fingerprint(value),
            "parent_condition":plan.parent_condition,"extension_condition":plan.extension_condition,
            "references":{key:str(item) for key,item in refs.items()},"plan":plan.to_dict(),
            "no_execution_authorized":True}


def build_extension_registration(path: str | Path, *, model_factory: Any = build_model,
                                 fixed_encoders: Any = None, proposer: Any = None,
                                 resource_factory: Any = None) -> tuple[RestrictedAdaptation, Any, Any]:
    """Construct and transfer registered blocks; no workflow is executed here."""
    registration = validate_extension_registration(path)
    refs = registration["references"]
    plan = ExtensionPlan.from_dict(registration["plan"])
    parent_prepared = prepare_extension_parent(refs)
    prepared = prepare_experiment(refs["extension_manifest"])
    parent, parent_hooks = build_experiment(parent_prepared, model_factory=model_factory,
        fixed_encoders=fixed_encoders, proposer=proposer, resource_factory=resource_factory)
    from .policy_activation import _inert_model
    if model_factory is _inert_model:
        parent_hooks.authorization_required = True
    parent_payload = load_checkpoint(refs["parent_checkpoint"])
    if parent_payload.get("extension_architecture") is not None:
        saved_extension = parent_payload["extension"]
        registered_plan = ExtensionPlan.from_dict(parent_prepared.extension_registration["plan"])
        parent.config = parent.declared_config = scope_configuration(parent.config, saved_extension, registered_plan)
        parent_hooks.termination_plan = replace(parent_hooks.termination_plan,
            bootstrap_configuration=parent.config.termination)
        rebuilt = models_from_expansion_metadata(parent_payload["extension_architecture"],
            configuration=parent.config.network,encoder_configuration=parent.config.encoders,
            text_encoder=parent.fixed_encoders.text_encoder)
        parent.alpha,parent.weighting,parent.fixed_encoders = rebuilt.alpha,rebuilt.weighting,rebuilt.encoders
        scopes = parent_payload.get("extension",{}).get("permitted",{})
        for key,model in (("alpha",parent.alpha),("weighting",parent.weighting)):
            saved_optimizer=parent_payload["alpha_optimizer" if key=="alpha" else "weighting_optimizer"]
            group=saved_optimizer["param_groups"][0]
            settings={field:group[field] for field in ("lr","betas","eps","weight_decay","amsgrad","maximize")}
            optimizer=block_optimizer(model,scopes[key],settings)
            setattr(parent,"alpha_optimizer" if key=="alpha" else "weighting_optimizer",optimizer)
    # Ordinary loading rejects historical logits/checkpoints and unsafe boundaries.
    parent.resume_from_checkpoint(refs["parent_checkpoint"], hooks=parent_hooks)
    parent_payload = load_checkpoint(refs["parent_checkpoint"])
    if parent_payload["trainer_state"]["phase"] != "complete" or parent_payload.get("best_system") is None:
        raise ValueError("extension requires a selected complete parent, not an engineering pilot")
    parent_evaluation = EvaluationRecord.from_dict(json.loads(Path(refs["parent_evaluation"]).read_text()))
    _matching_parent_evaluation(parent_payload,parent_hooks.plans["system"],parent_evaluation)
    if prepared.config.termination != parent.config.termination or parent.config.termination not in plan.calibration_candidates:
        raise ValueError("extension must begin with the selected parent's stopping tuple and include it in calibration")
    if parent_payload.get("extension"):
        for key,model in (("alpha",parent.alpha),("weighting",parent.weighting)):
            attribute="alpha_optimizer" if key=="alpha" else "weighting_optimizer"
            current=getattr(parent,attribute)
            bank=copy.deepcopy(parent_payload["extension"]["optimizer_bank"][key])
            bank["state"].update(named_optimizer_state(model,current)["state"])
            settings={field:current.param_groups[0][field] for field in ("lr","betas","eps","weight_decay","amsgrad","maximize")}
            setattr(parent,attribute,block_optimizer(model,tuple(dict(model.named_parameters())),settings,inherited=bank))
    expansion = expand_models(parent.alpha,parent.weighting,parent.fixed_encoders,
        agent_ids=tuple(agent.agent_id for agent in prepared.config.agent_pool),
        scheme_order=prepared.config.scheme_order,legality=prepared.config.scheme_legality,
        categories=dict(prepared.config.history_categories) or None)
    trainer,hooks = build_experiment(prepared,model_factory=model_factory,fixed_encoders=expansion.encoders,
        proposer=proposer,resource_factory=resource_factory)
    if model_factory is _inert_model:
        hooks.authorization_required = True
    trainer.alpha,trainer.weighting,trainer.fixed_encoders = expansion.alpha,expansion.weighting,expansion.encoders
    prompts = seed_prompt_collection(prepared.config.scheme_registry)
    trainer.gamma = PromptCollection({**prompts.components,**parent.gamma.components})
    trainer.state.workflow_attempt_counter = parent.state.workflow_attempt_counter
    # Retain established query sampling cursors across adaptation; never attach
    # parent trajectories, scores, calibration evidence, or execution approvals.
    hooks.sample_counters = copy.deepcopy(parent_hooks.sample_counters)
    parent_safety = parent_hooks.safety_record
    hooks.termination_plan = replace(hooks.termination_plan,candidates=plan.calibration_candidates,
                                    bootstrap_configuration=prepared.config.termination,
                                    routing_labels=tuple(label.value for label in prepared.config.scheme_order))
    inherited_acceptance = getattr(parent_hooks,"extension_parent_acceptance",None)
    if parent_safety is not None and parent_safety.approved:
        profile_fingerprint=parent_safety.fingerprint
    elif inherited_acceptance is not None:
        profile_fingerprint=inherited_acceptance["parent_pilot_fingerprint"]
    else:
        raise ValueError("accepted parent safeguard profile must be separately approved for extension use")
    hooks.extension_parent_acceptance = hooks.expected_extension_parent_acceptance()
    if hooks.extension_parent_acceptance["parent_pilot_fingerprint"] != profile_fingerprint:
        raise ValueError("parent resource profile lineage differs")
    # The extension shares authoritative expenditure, never a filtered/fresh budget.
    hooks.expenditure_ledger.load_state_dict(parent_hooks.expenditure_ledger.state_dict())
    adaptation = RestrictedAdaptation(trainer,expansion,plan,parent_alpha=parent.alpha,parent_weighting=parent.weighting,
        parent_alpha_optimizer=parent.alpha_optimizer,parent_weighting_optimizer=parent.weighting_optimizer)
    adaptation.validate_coverage_registry(parent_agents=parent.alpha.agent_ids,agents=trainer.alpha.agent_ids,
        parent_schemes=tuple(item.value for item in parent.config.scheme_order),
        schemes=tuple(item.value for item in prepared.config.scheme_order))
    return adaptation,hooks,registration


def retention_for_registration(adaptation: RestrictedAdaptation, registration: Mapping[str,Any]) -> dict[str,Any]:
    states = load_checkpoint(registration["references"]["parent_states"])
    if not isinstance(states,list) or not states:
        raise ValueError("saved parent-mode states must be a nonempty list")
    before,after = [],[]
    parent_alpha,parent_weighting = adaptation.parent_models
    encoders = adaptation.expansion.encoders
    parent_ids = tuple(parent_alpha.agent_ids)
    with evaluating(parent_alpha,parent_weighting,adaptation.trainer.alpha,adaptation.trainer.weighting),torch.no_grad():
        for state in states:
            if set(state) != {"query","history","answers","answer_views","parent_query","parent_history"}:
                raise ValueError("saved parent state has unsupported or missing fields")
            history = encoders.encode_history(state["history"])
            query = encoders.encode_query(state["query"])
            before.append((parent_alpha(state["parent_query"],state["parent_history"],state["answers"],answer_views=state["answer_views"]).values,
                           parent_weighting(state["parent_query"],state["parent_history"]).logits))
            after.append((adaptation.trainer.alpha(query,history,state["answers"],answer_views=state["answer_views"],
                                                  active_agent_ids=parent_ids,active_scheme_order=parent_alpha.scheme_order).values,
                          adaptation.trainer.weighting(query,history,active_agent_ids=parent_ids).logits))
    return check_parent_retention(before,after,tolerance=adaptation.plan.parent_value_tolerance)


def extension_authorization_template(registration: Mapping[str,Any]) -> dict[str,Any]:
    plan = ExtensionPlan.from_dict(registration["plan"])
    return {"protocol":"restricted_extension_authorization_v1","decision":"pending",
            "registration_fingerprint":registration["registration_fingerprint"],"plan_fingerprint":plan.fingerprint,
            "parent_condition":plan.parent_condition,"extension_condition":plan.extension_condition,
            "maximum_physical_attempts":plan.physical_attempt_limit,"maximum_spending_usd":plan.spending_limit_usd,
            "scope":"registered_restricted_extension_only","reviewer":"","approval_source":""}


def prepare_extension(path: str | Path, output: str | Path, **factory_kwargs: Any) -> dict[str,Any]:
    """Publish a local pending extension checkpoint without activation/generation."""
    from .experiment import _campaign_lock, validate_journal_resume
    from ..infrastructure.persistence import DurableJournal
    from .policy_activation import _inert_model

    factory_kwargs.setdefault("model_factory", _inert_model)
    directory = Path(output).resolve()
    registration = validate_extension_registration(path)
    campaign = _canonical_campaign(registration["references"]["extension_manifest"])
    expected = campaign / "extensions" / registration["plan_fingerprint"]
    if directory != expected:
        raise ValueError("extension states must stay under their canonical parent campaign/extension plan")
    with _campaign_lock(campaign):
        if directory.exists() and any(directory.iterdir()):
            raise ValueError("extension output already exists; initialization cannot reset it")
        validate_journal_resume(registration["references"]["parent_checkpoint"],campaign)
        adaptation,hooks,registration = build_extension_registration(path,**factory_kwargs)
        retention = retention_for_registration(adaptation,registration)
        if not retention["passed"]:
            raise ValueError("expanded parent-mode numerical retention failed")
        adaptation.initial_retention = copy.deepcopy(retention)
        directory.mkdir(parents=True,exist_ok=True)
        trainer = adaptation.trainer
        trainer.hooks,trainer.checkpoint_path = hooks,directory/'checkpoint.pt'
        ledger = hooks.expenditure_ledger
        journal = DurableJournal([campaign],ledger.state_dict())
        trainer.durable_journal = ledger.durable_journal = journal
        ledger._journal_entry_count = len(ledger.entries)
        try:
            trainer._checkpoint()
            _write_json_atomic(directory/'registration.json',registration)
            _write_json_atomic(directory/'parent_retention.json',retention)
            _write_json_atomic(directory/'authorization_pending.json',extension_authorization_template(registration))
        finally:
            asyncio.run(journal.aclose())
    return {"prepared":True,"activated":False,"generation_authorized":False,"directory":str(directory),
            "canonical_campaign":str(campaign),"plan_fingerprint":adaptation.plan.fingerprint,
            "parent_retention":retention}



async def adapt_extension(path: str | Path, output: str | Path, *, authorization: str | Path,
                          development_authorization: str | Path, resume: str | Path,
                          escalation_scope: str | None = None, escalation_authorization: str | Path | None = None,
                          **factory_kwargs: Any) -> dict[str,Any]:
    """Run only the registered restricted phases through the production hooks."""
    from .experiment import _campaign_lock, validate_journal_resume
    from .preflight import inspect_preflight
    from .policy_activation import bind_policy_dispatch
    from ..infrastructure.persistence import DurableJournal

    directory = Path(output).resolve()
    registration = validate_extension_registration(path)
    approval = json.loads(Path(authorization).read_text())
    expected = extension_authorization_template(registration)
    if (set(approval) != set(expected) or approval['decision'] != 'approve'
            or not approval['reviewer'] or not approval['approval_source']
            or any(approval[key] != value for key,value in expected.items() if key not in {'decision','reviewer','approval_source'})):
        raise ValueError("a separately approved exact extension registration is required")
    if Path(resume).resolve().parent != directory:
        raise ValueError("extension resume must use its prepared directory")
    manifest = registration['references']['extension_manifest']
    scope_approval = (None if escalation_authorization is None
                      else json.loads(Path(escalation_authorization).read_text()))
    report = inspect_preflight(manifest,operation='development',authorization_path=development_authorization,
        checkpoint_path=resume,extension_scope=escalation_scope,extension_scope_authorization=scope_approval)
    report.require_ready()
    campaign = _canonical_campaign(manifest)
    if directory != campaign / "extensions" / registration["plan_fingerprint"]:
        raise ValueError("extension cannot create a separate spending/admission identity")
    with _campaign_lock(campaign) as guard:
        validate_journal_resume(resume,campaign)
        adaptation,hooks,registration = build_extension_registration(path,**factory_kwargs)
        trainer = adaptation.trainer
        candidate = load_checkpoint(resume)
        adaptation.configure_checkpoint_scope(candidate.get("extension",{}))
        adaptation.configure_hooks(hooks)
        trainer.resume_from_checkpoint(resume,hooks=hooks)
        if escalation_scope is not None:
            if escalation_authorization is None:
                raise ValueError("escalation requires its separate scope authorization")
            adaptation.select_escalation(escalation_scope,scope_approval)
        elif escalation_authorization is not None:
            raise ValueError("an escalation approval must name its predeclared scope")
        hooks.execution_session_guard = guard
        if trainer.config.failure_policy.enabled:
            bind_policy_dispatch(hooks.expenditure_ledger,extension_policy_manifest(manifest),guard)
        hooks.authorize_stage(json.loads(Path(development_authorization).read_text()))
        ledger = hooks.expenditure_ledger
        journal = DurableJournal([campaign],ledger.state_dict())
        ledger.durable_journal = trainer.durable_journal = journal
        ledger._journal_entry_count = len(ledger.entries)
        ledger.stage = 'development'
        scope = 'extension:'+adaptation.plan.fingerprint+(':'+adaptation.active_scope if adaptation.active_scope else '')
        ledger.register_scope(scope,stage='development',max_attempts=adaptation.limit("physical_attempt_limit"),
            budget=adaptation.limit("spending_limit_usd"),bindings={'protocol':EXTENSION_PROTOCOL,
                'condition_fingerprint':adaptation.plan.extension_condition,'plan_fingerprint':adaptation.plan.fingerprint,
                'authorization_fingerprint':_fingerprint(adaptation.scope_authorization or approval)})
        ledger.activate_scope(scope)
        trainer.checkpoint_path = Path(resume)
        trainer._checkpoint()
        parent = EvaluationRecord.from_dict(json.loads(Path(registration['references']['parent_evaluation']).read_text()))
        try:
            result = await adaptation.run(hooks,parent_result=parent,parent_retention=retention_for_registration(adaptation,registration))
            result['parent_retention_after'] = retention_for_registration(adaptation,registration)
            result['expenditure'] = ledger.scope_summary(scope)
            result['physical_usage'] = _scope_usage(ledger,scope)
            trainer._checkpoint()
            await ledger.write_artifact(directory/'extension_report.json',result)
            return result
        finally:
            await ledger.flush_durable()
            await ledger.publish_snapshot([campaign / 'expenditure_ledger.json'])
            await ledger.durable_journal.aclose()


async def evaluate_extension(registration: str | Path, checkpoint: str | Path, output: str | Path,
                             *, authorization: str | Path, **factory_kwargs: Any) -> dict[str, Any]:
    """Test only an exactly selected extension, under its separate test approval."""
    from .experiment import _campaign_lock, validate_journal_resume
    from .preflight import inspect_preflight
    from .policy_activation import bind_policy_dispatch
    from ..infrastructure.persistence import DurableJournal

    source = validate_extension_registration(registration)
    manifest = source["references"]["extension_manifest"]
    campaign = _canonical_campaign(manifest)
    directory, selected = Path(output).resolve(), Path(checkpoint).resolve()
    extension_directory = campaign / "extensions" / source["plan_fingerprint"]
    if selected.parent != extension_directory or directory != extension_directory / "final_test":
        raise ValueError("extension testing must retain its registered checkpoint and canonical accounting identity")
    preflight = inspect_preflight(manifest, operation="test", authorization_path=authorization, checkpoint_path=selected)
    preflight.require_ready()
    with _campaign_lock(campaign) as guard:
        validate_journal_resume(selected, campaign)
        adaptation, hooks, _ = build_extension_registration(registration, **factory_kwargs)
        candidate = load_checkpoint(selected)
        adaptation.configure_checkpoint_scope(candidate.get("extension", {}))
        adaptation.configure_hooks(hooks)
        trainer = adaptation.trainer
        trainer.resume_from_checkpoint(selected, hooks=hooks)
        ledger = hooks.expenditure_ledger
        if any(item.get("stage") == "test" for item in ledger.entries):
            raise ValueError("campaign already records final-test work; automatic replay is disabled")
        hooks.authorize_stage(json.loads(Path(authorization).read_text()))
        hooks.execution_session_guard = guard
        if trainer.config.failure_policy.enabled:
            bind_policy_dispatch(ledger, extension_policy_manifest(manifest), guard)
        directory.mkdir(parents=True, exist_ok=False)
        journal = DurableJournal([campaign], ledger.state_dict())
        trainer.durable_journal = ledger.durable_journal = journal
        ledger._journal_entry_count = len(ledger.entries)
        ledger.activate_scope(None)
        # The selected checkpoint and its test-approval hash remain immutable.
        trainer.checkpoint_path = directory / "execution_checkpoint.pt"
        trainer._checkpoint()
        try:
            record = await hooks.final_test(selected)
            result = record.to_dict()
            trainer._checkpoint()
            await ledger.write_artifact(directory / "test_results.json", result)
            return result
        finally:
            await ledger.flush_durable()
            await ledger.publish_snapshot([campaign / "expenditure_ledger.json"])
            await journal.aclose()
