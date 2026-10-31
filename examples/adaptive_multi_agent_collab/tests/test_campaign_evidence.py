"""Read-only compatibility reuse validates preserved bytes and actual request scope."""

from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest

from examples.adaptive_multi_agent_collab.operations import campaigns
from examples.adaptive_multi_agent_collab.operations.preflight import gateway_fingerprint_fields


@pytest.fixture
def evidence(tmp_path):
    receipts = {
        "initial_compatibility": {"status": "complete", "assessment": {"passed": True}},
        "role_contract": {
            "stage_complete": True,
            "probes": [{"id": index, "valid_output": True, "native_evidence_valid": True} for index in range(6)],
        },
        "role_plan": {"synthetic": True, "scope": "six independent captured-state probes"},
        "earlier_recheck": {"synthetic": True, "scope": "prior partial compatibility only"},
        "earlier_plan": {"synthetic": True},
    }
    paths = {}
    for name, value in receipts.items():
        path = tmp_path / (name + ".json")
        path.write_text(json.dumps(value))
        paths[name] = path
    backend = {
        "model_name": "synthetic/model",
        "revision": "synthetic-service-alias",
        "decoding": {"temperature": 0.7},
        "supports_seed": True,
        "gateway": {"provider": "synthetic-provider", "allow_fallbacks": False},
        "resources": {"counting_method": "synthetic-approved-counter", "input_price": 0.000001},
        "role_output": {"mode": "native_schema", "version": "synthetic-role-schema"},
    }
    effective = copy.deepcopy(backend)
    effective["gateway"] = gateway_fingerprint_fields(backend["gateway"])
    value = {
        "protocol": "shared_compatibility_evidence_v1",
        "artifacts": {
            name: {
                "path": str(path),
                "sha256": campaigns._sha(path),
                "original_path": str(tmp_path / "unavailable-original-campaign" / path.name),
            }
            for name, path in paths.items()
        },
        "request_configuration_fingerprint": campaigns._digest({"agent": effective}),
        "response_identity_evidence": {"agent": {"synthetic": True}},
        "source_conditions": ["historical-synthetic-condition"],
        "scope": "backend and independent captured-state probe evidence only",
        "failure_policy_live_validated": False,
    }
    index = tmp_path / "index.json"
    index.write_text(json.dumps(value))
    specification = {
        "independent_campaign": {
            "shared_evidence_path": str(index),
            "shared_evidence_sha256": campaigns._sha(index),
        },
        "backends": {"agent": backend},
    }
    return SimpleNamespace(
        root=tmp_path, paths=paths, index=index, value=value, specification=specification, receipts=receipts
    )


def _load(item):
    return campaigns.shared_compatibility_evidence(item.specification, item.root)


def _update_receipt_with_explicit_hash(item, name):
    """Make a separate internally hashed fixture so scope, not hash, is tested."""
    path = item.paths[name]
    path.write_text(json.dumps(item.receipts[name]))
    item.value["artifacts"][name]["sha256"] = campaigns._sha(path)
    item.index.write_text(json.dumps(item.value))
    item.specification["independent_campaign"]["shared_evidence_sha256"] = campaigns._sha(item.index)


def test_shared_evidence_reuses_independent_receipts_without_old_ledger_or_halt(evidence):
    before = {path: path.read_bytes() for path in (evidence.index, *evidence.paths.values())}
    result = _load(evidence)
    assert result["historical_compatibility_report"] == evidence.receipts["initial_compatibility"]
    assert result["source_conditions"] == evidence.value["source_conditions"]
    assert result["scope"] == evidence.value["scope"]
    assert result["pilot_authorized"] is False and result["failure_policy_live_validated"] is False
    assert not (evidence.root / "unavailable-original-campaign").exists()
    assert not (evidence.root / "expenditure_ledger.json").exists()
    assert all(path.read_bytes() == data for path, data in before.items())
    result["artifacts"].clear()
    assert _load(evidence)["artifacts"] == evidence.value["artifacts"]


def test_shared_index_changed_bytes_are_rejected(evidence):
    evidence.index.write_text(evidence.index.read_text() + "\n")
    with pytest.raises(ValueError, match="index differs"):
        _load(evidence)


@pytest.mark.parametrize(
    "name", ("initial_compatibility", "role_contract", "role_plan", "earlier_recheck", "earlier_plan")
)
def test_shared_receipt_or_plan_changed_bytes_are_rejected(evidence, name):
    evidence.paths[name].write_text(evidence.paths[name].read_text() + "\n")
    with pytest.raises(ValueError, match="artifact changed"):
        _load(evidence)


@pytest.mark.parametrize(
    "field", ("model_name", "revision", "decoding", "supports_seed", "gateway", "resources", "role_output")
)
def test_changed_effective_request_configuration_invalidates_reused_evidence(evidence, field):
    backend = evidence.specification["backends"]["agent"]
    replacements = {
        "model_name": "synthetic/other-model",
        "revision": "other-revision",
        "decoding": {"temperature": 0},
        "supports_seed": False,
        "gateway": {"provider": "other-provider", "allow_fallbacks": False},
        "resources": {"counting_method": "different-counter", "input_price": 0.000001},
        "role_output": {"mode": "prompt_plus_validator", "version": "synthetic-role-schema"},
    }
    backend[field] = replacements[field]
    with pytest.raises(ValueError, match="request behavior differs"):
        _load(evidence)


@pytest.mark.parametrize(
    "missing",
    ("historical_complete", "historical_passed", "roles_complete", "six_roles", "valid_output", "native_evidence"),
)
def test_internally_hashed_evidence_cannot_exceed_observed_success_scope(evidence, missing):
    name = "initial_compatibility" if missing.startswith("historical") else "role_contract"
    receipt = evidence.receipts[name]
    if missing == "historical_complete":
        receipt["status"] = "incomplete"
    elif missing == "historical_passed":
        receipt["assessment"]["passed"] = False
    elif missing == "roles_complete":
        receipt["stage_complete"] = False
    elif missing == "six_roles":
        receipt["probes"].pop()
    elif missing == "valid_output":
        receipt["probes"][0]["valid_output"] = False
    else:
        receipt["probes"][0]["native_evidence_valid"] = False
    _update_receipt_with_explicit_hash(evidence, name)
    with pytest.raises(ValueError, match="do not establish their declared scope"):
        _load(evidence)
