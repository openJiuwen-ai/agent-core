"""Explicit extension plans preserve primary gates and original data partitions."""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import asdict, replace
from pathlib import Path

import pytest
import torch

from examples.adaptive_multi_agent_collab.config import Config, SchemeRegistration, LogicalAgentConfig
from examples.adaptive_multi_agent_collab.datasets.data import ActiveDataPlan, select_active_data
from examples.adaptive_multi_agent_collab.operations.experiment import extension_policy_manifest, prepare_experiment
from examples.adaptive_multi_agent_collab.learning.extension import EXTENSION_PROTOCOL
from examples.adaptive_multi_agent_collab.operations.preflight import execution_specification, inspect_preflight
from examples.adaptive_multi_agent_collab.tests.test_extension_adaptation import plan as example_plan
from examples.adaptive_multi_agent_collab.tests.test_preflight_data import _local_specification


def _write(path, value):
    path.write_text(json.dumps(value))


def _condition(value):
    scientific = execution_specification(value)
    scientific["config"].setdefault("training", {}).pop("max_outer_cycles", None)
    return hashlib.sha256(json.dumps(scientific, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@pytest.fixture
def registered(tmp_path):
    source, raw, _, _ = _local_specification(tmp_path)
    raw["campaign_directory"] = str(tmp_path / "parent_campaign")
    _write(source, raw)
    parent = prepare_experiment(source)
    active = parent.active_data_plan
    scheme = SchemeRegistration(label="S_check", legal_edges=((0, 1),), purpose="Check a supported material issue.",
        init_prompt="Check {query} and {answer}.", role_prompt="Assess {message}.", revision_prompt="Revise {message}.")
    cfg = replace(parent.config, scheme_registry=(scheme,))
    selected = example_plan(cfg, parent_condition=parent.specification_sha256,
        train_ids=active.train_ids[:2], component_validation_ids=active.component_validation_ids[:2],
        system_validation_ids=active.system_validation_ids[:2], calibration_attempt_limit=2)
    cfg = replace(cfg, training=replace(cfg.training, weighting_phase=selected.weighting_phase,
        routing_phase=selected.routing_phase, prompt_phase=selected.prompt_phase))
    child = copy.deepcopy(raw)
    child["experiment_profile"] = "registered_extension"
    registration = tmp_path / "registration.json"
    child["extension_registration"] = {"protocol": EXTENSION_PROTOCOL, "path": str(registration)}
    child["config"] = cfg.to_dict()
    child["active_data"] = {"plan": ActiveDataPlan(selected.train_ids, selected.component_validation_ids,
        selected.system_validation_ids, (), 17, parent.dataset.fingerprint,
        "registered_extension", "explicit_registered_extension_v1").to_dict()}
    child["termination_calibration"] = {"candidates": [asdict(item) for item in selected.calibration_candidates]}
    manifest = tmp_path / "extension.json"
    _write(manifest, child)
    selected = replace(selected, extension_condition=_condition(child))
    references = {"parent_manifest": source, "extension_manifest": manifest}
    for name in ("parent_checkpoint", "parent_evaluation", "parent_states"):
        path = tmp_path / name
        if name == "parent_checkpoint":
            torch.save({"hook_state": {}}, path)
        else:
            path.write_bytes(b"Explicit synthetic reference, not paid evidence")
        references[name] = path
    registration_value = {"protocol": EXTENSION_PROTOCOL, "plan": selected.to_dict(),
        **{name: {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()} for name,path in references.items()}}
    _write(registration, registration_value)
    return manifest, child, registration, registration_value, parent


def _rebind(manifest, child, registration, value):
    """Explicit disposable new-plan binding: never used to modify real evidence."""
    _write(manifest, child)
    value["extension_manifest"]["sha256"] = hashlib.sha256(manifest.read_bytes()).hexdigest()
    value["plan"]["extension_condition"] = _condition(child)
    _write(registration, value)


def test_registered_extension_uses_explicit_subsets_and_candidates_without_pilot(registered):
    manifest, _, _, value, parent = registered
    prepared = prepare_experiment(manifest)
    assert prepared.dataset.fingerprint == parent.dataset.fingerprint
    assert prepared.active_data_plan.profile == "registered_extension"
    assert prepared.pilot_plan is None and prepared.active_data_plan.pilot_ids == ()
    assert prepared.manifest()["pilot"] is None
    assert len(prepared.termination_plan.candidates) == 1
    assert prepared.extension_registration["plan"]["train_ids"] == tuple(value["plan"]["train_ids"])
    assert prepared.config.scheme_order[-1].value == "S_check"
    assert extension_policy_manifest(manifest) == Path(value["parent_manifest"]["path"])


@pytest.mark.parametrize("change", ["marker", "reference", "cost", "ids", "candidate", "phase", "campaign"])
def test_extension_registration_cannot_bypass_preserved_scientific_contract(registered, change):
    manifest, child, registration, value, _ = registered
    if change == "marker":
        child.pop("extension_registration")
    elif change == "reference":
        value["parent_manifest"]["sha256"] = "0" * 64
    elif change == "cost":
        child["config"]["costs"]["lambda_conversation"] = .9
    elif change == "ids":
        child["active_data"]["plan"]["train_ids"] = child["active_data"]["plan"]["component_validation_ids"]
    elif change == "candidate":
        child["termination_calibration"]["candidates"] = []
    elif change == "phase":
        child["config"]["training"]["routing_phase"]["max_updates"] += 1
    else:
        child["campaign_directory"] += "_new_budget"
    _rebind(manifest, child, registration, value)
    with pytest.raises((ValueError, TypeError)):
        prepare_experiment(manifest)


def test_primary_profile_cannot_use_extension_marker_or_extra_schemes(registered):
    manifest, child, registration, value, _ = registered
    child["experiment_profile"] = "strategyqa_initial"
    _rebind(manifest, child, registration, value)
    with pytest.raises(ValueError, match="primary-condition"):
        prepare_experiment(manifest)


def test_extension_active_selection_is_explicit_and_has_no_new_pilot(registered):
    _, _, _, _, parent = registered
    with pytest.raises(ValueError, match="automatic selection"):
        select_active_data(parent.dataset, seed=42, profile="registered_extension")
    active = parent.active_data_plan
    with pytest.raises(ValueError, match="new pilot"):
        ActiveDataPlan(active.train_ids, active.component_validation_ids, active.system_validation_ids,
            active.pilot_ids,42,parent.dataset.fingerprint,"registered_extension","explicit_registered_extension_v1")


def test_invalid_four_agent_registration_reports_issues_without_index_error(registered):
    manifest, child, registration, value, _ = registered
    config = Config.from_dict(child["config"])
    config = replace(config,n_agents=4,agent_pool=(*config.agent_pool,LogicalAgentConfig(agent_id=3,backend_key="agent-3")))
    child["config"] = config.to_dict()
    _rebind(manifest, child, registration, value)
    value["parent_manifest"]["sha256"] = "0" * 64
    _write(registration,value)
    report = inspect_preflight(manifest, operation="train")
    assert not report.ready
    assert "extension_registration" in {issue.code for issue in report.issues}


@pytest.mark.parametrize("enabled", [False, True])
def test_registered_extension_inherits_whole_workflow_recovery(registered, enabled):
    from examples.adaptive_multi_agent_collab.workflows.recovery import WorkflowRecoveryConfig

    manifest, child, registration, value, _ = registered
    parent_path = Path(value["parent_manifest"]["path"])
    parent = json.loads(parent_path.read_text())
    parent_config = replace(Config.from_dict(parent["config"]),
                            workflow_recovery=WorkflowRecoveryConfig(enabled=True))
    parent["config"] = parent_config.to_dict()
    _write(parent_path, parent)
    value["parent_manifest"]["sha256"] = hashlib.sha256(parent_path.read_bytes()).hexdigest()
    value["plan"]["parent_condition"] = _condition(parent)
    child["config"] = replace(Config.from_dict(child["config"]),
                              workflow_recovery=WorkflowRecoveryConfig(enabled=enabled)).to_dict()
    _rebind(manifest, child, registration, value)
    if enabled:
        assert prepare_experiment(manifest).config.workflow_recovery.enabled
    else:
        with pytest.raises(ValueError, match="inherited workflow_recovery"):
            prepare_experiment(manifest)


def test_extension_preparation_rejects_omitted_published_amendment_reference(registered):
    manifest, child, registration, value, _ = registered
    path = Path(value["parent_checkpoint"]["path"])
    torch.save({"hook_state": {"supplement_lineage": {"protocol": "unregistered"}}}, path)
    value["parent_checkpoint"]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    _rebind(manifest, child, registration, value)
    with pytest.raises(ValueError, match="explicit supplement reference"):
        prepare_experiment(manifest)
