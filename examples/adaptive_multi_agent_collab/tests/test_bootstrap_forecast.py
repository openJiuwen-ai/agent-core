"""The bootstrap launch reviews its own forecast without rewriting a condition."""

import copy
import hashlib
import json
from types import SimpleNamespace

import pytest
import torch

from examples.adaptive_multi_agent_collab.operations import preflight


@pytest.fixture
def forecast_case(tmp_path, monkeypatch):
    bindings = dict(stage="development", manifest_fingerprint="a" * 64,
                    condition_fingerprint="b" * 64, resource_profile_fingerprint="c" * 64,
                    approved_budget_usd=80.0, pilot_fingerprint="d" * 64,
                    checkpoint_sha256=None, compatibility_plan_fingerprint=None)
    monkeypatch.setattr(preflight, "authorization_bindings", lambda *a, **kw: (dict(bindings), None))
    state = {"phase": "pilot_complete", "bootstrap_complete": {"weighting": False, "routing": False},
             "calibration_complete": False, "outer_cycle": 0, "attempts": {"prompt": 0}}
    checkpoint = tmp_path / "checkpoint.pt"
    torch.save({"trainer_state": state}, checkpoint)
    value = {"protocol": "bootstrap_forecast_v1", "development_ceiling_usd": 80.0,
             "estimated_cost_usd": 12.0, "headroom_usd": 4.0, "basis": "synthetic workload fixture",
             "bindings": {k: bindings[k] for k in ("manifest_fingerprint", "condition_fingerprint",
                                                   "resource_profile_fingerprint", "pilot_fingerprint")}}
    path = tmp_path / "forecast.json"
    path.write_text(json.dumps(value))
    return SimpleNamespace(), checkpoint, path, value, state


def test_forecast_hash_and_pending_review_do_not_change_checkpoint(forecast_case):
    prepared, checkpoint, path, value, _ = forecast_case
    before = checkpoint.read_bytes()
    bindings, report = preflight.bootstrap_forecast_bindings(prepared, checkpoint, path)
    assert report == value
    assert bindings["bootstrap_forecast_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    approval = preflight.stage_authorization_template(**bindings)
    assert approval["decision"] == "pending"
    assert approval["scope"] == preflight.BOOTSTRAP_SCOPE
    assert checkpoint.read_bytes() == before
    with pytest.raises(ValueError, match="explicit approval"):
        preflight.validate_stage_authorization(approval, **bindings)


@pytest.mark.parametrize("change", ["condition", "budget", "headroom", "calibrated", "phase", "extension"])
def test_forecast_rejects_wrong_binding_budget_or_boundary(forecast_case, change):
    prepared, checkpoint, path, value, state = forecast_case
    if change == "condition":
        value["bindings"]["condition_fingerprint"] = "e" * 64
    elif change == "budget":
        value["development_ceiling_usd"] = 81.0
    elif change == "headroom":
        value["headroom_usd"] = 79.0
    elif change == "calibrated":
        state["calibration_complete"] = True
    elif change == "phase":
        state["phase"] = "weighting"
    else:
        prepared.extension_registration = {"registered": True}
    torch.save({"trainer_state": state}, checkpoint)
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        preflight.bootstrap_forecast_bindings(prepared, checkpoint, path)


def test_forecast_resume_binding_retains_scope_without_replaying_entry(forecast_case):
    prepared, checkpoint, path, _, state = forecast_case
    first, _ = preflight.bootstrap_forecast_bindings(prepared, checkpoint, path)
    state = copy.deepcopy(state)
    state.update(phase="calibration", bootstrap_complete={"weighting": True, "routing": True})
    torch.save({"trainer_state": state}, checkpoint)
    resumed, _ = preflight.bootstrap_forecast_bindings(prepared, checkpoint, path)
    assert resumed == first


@pytest.mark.parametrize("operation,flag,path", [("pilot", True, "forecast"), ("train", True, None),
                                                ("train", False, "forecast")])
def test_preflight_rejects_unpaired_or_wrong_stage_scope(operation, flag, path):
    with pytest.raises(ValueError, match="bootstrap-only development"):
        preflight.inspect_preflight("unused.json", operation=operation,
                                    bootstrap_only=flag, bootstrap_forecast_path=path)
