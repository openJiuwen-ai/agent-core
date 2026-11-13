"""Extension parent evidence is read-only validation, including warmed memos."""

import copy
import hashlib

import pytest

from examples.adaptive_multi_agent_collab.learning.evaluation import Experiment
from examples.adaptive_multi_agent_collab.infrastructure.storage import StartupVerification, StorageFailure


def test_rejected_extension_restore_does_not_populate_caller_cache(monkeypatch):
    hooks = Experiment.__new__(Experiment)
    hooks.execution_metadata = {"registered_extension": {}}
    hooks._extension_parent_source_facts = None
    monkeypatch.setattr(hooks, "_condition", lambda: {"fixed": True})
    monkeypatch.setattr(hooks, "_validate_calibration_lineage", lambda _: None)
    monkeypatch.setattr(hooks, "_validate_supplement_lineage", lambda _: None)

    def expected(candidate):
        candidate._extension_parent_source_facts = {"parent_pilot_fingerprint": "verified"}
        return {"expected": "verified"}

    monkeypatch.setattr(Experiment, "expected_extension_parent_acceptance", expected)
    with pytest.raises(ValueError, match="parent safeguard acceptance"):
        hooks.validate_state({
            "schema_version": 4, "condition": {"fixed": True},
            "extension_parent_acceptance": {"expected": "changed"},
        })
    assert hooks._extension_parent_source_facts is None


def test_cached_parent_acceptance_is_pinned_in_each_startup(tmp_path):
    parent = tmp_path / "parent.pt"
    parent.write_bytes(b"exact inherited parent")
    hooks = Experiment.__new__(Experiment)
    context = {
        "registration_fingerprint": "registration", "plan_fingerprint": "plan",
        "parent_condition": "parent", "extension_condition": "extension",
        "parent_checkpoint_sha256": hashlib.sha256(parent.read_bytes()).hexdigest(),
        "references": {"parent_checkpoint": str(parent)},
    }
    hooks.execution_metadata = {
        "registered_extension": context, "resource_profile_fingerprint": "resources",
    }
    hooks._extension_parent_source_facts = {"parent_pilot_fingerprint": "accepted"}
    with StartupVerification() as verification:
        record = hooks.expected_extension_parent_acceptance()
        assert record["parent_pilot_fingerprint"] == "accepted"
        parent.write_bytes(b"changed inherited parent")
        with pytest.raises(StorageFailure, match="changed"):
            verification.assert_fresh()
    # The runtime memo cannot authorize the changed file in a later context.
    with StartupVerification(), pytest.raises(ValueError, match="parent checkpoint changed"):
        hooks.expected_extension_parent_acceptance()
    assert hooks._extension_parent_source_facts == {"parent_pilot_fingerprint": "accepted"}


def test_successful_prepared_restore_commits_validated_parent_cache_once(monkeypatch):
    from examples.adaptive_multi_agent_collab.tests.test_data_evaluation import _build_offline_experiment

    _, hooks, models, _, proposer = _build_offline_experiment()
    hooks.execution_metadata["registered_extension"] = {"fixture": "parent"}
    state = hooks.state_dict()
    state["extension_parent_acceptance"] = {"expected": "verified"}
    lookups = []

    def expected(candidate):
        lookups.append(candidate)
        candidate._extension_parent_source_facts = {"parent_pilot_fingerprint": "verified"}
        return {"expected": "verified"}

    monkeypatch.setattr(Experiment, "expected_extension_parent_acceptance", expected)
    invalid = copy.deepcopy(state)
    invalid["sample_counters"]["weighting"] = -1
    facts = []
    with pytest.raises(ValueError, match="data-sampling state"):
        hooks.validate_state(invalid, _parent_facts=facts)
    assert facts == [] and hooks._extension_parent_source_facts is None
    with pytest.raises(ValueError, match="data-sampling state"):
        hooks.prepare_restore(invalid)
    assert hooks._extension_parent_source_facts is None

    lookups.clear()
    plan = hooks.prepare_restore(state)
    assert len(lookups) == 1 and lookups[0] is not hooks
    assert hooks._extension_parent_source_facts is None
    hooks.apply_restore(plan)
    assert hooks._extension_parent_source_facts == {"parent_pilot_fingerprint": "verified"}
    assert len(lookups) == 1
    lookups[0]._extension_parent_source_facts["parent_pilot_fingerprint"] = "modified"
    assert hooks._extension_parent_source_facts["parent_pilot_fingerprint"] == "verified"
    assert not any(model.calls for model in models) and proposer.calls == 0
