"""Atomic, non-copying preparation of the actual expenditure ledger restore."""
from __future__ import annotations

import copy
import json

import pytest

from examples.adaptive_multi_agent_collab.config import ResourceConfig
from examples.adaptive_multi_agent_collab.infrastructure.persistence import _json
from examples.adaptive_multi_agent_collab.providers.resources import ExpenditureLedger
from examples.adaptive_multi_agent_collab.infrastructure.storage import (
    EvidenceStore, StartupVerification, load_ledger, ledger_envelope, mutable_json,
)
from examples.adaptive_multi_agent_collab.tests.test_receipt_restore import receipt_state, pending_states


def ledger():
    return ExpenditureLedger(ResourceConfig(development_budget=80, currency="USD"))


def test_validation_never_serializes_or_deepcopies_a_full_ledger(monkeypatch):
    saved = receipt_state()
    current = ledger()
    current.load_state_dict(json.loads(_json(saved)))
    owned_entry = current.entries[0]
    monkeypatch.setattr(current, "state_dict", lambda **_: pytest.fail("full ledger snapshot"))
    monkeypatch.setattr(copy, "deepcopy", lambda *_a, **_k: pytest.fail("validation copy"))
    current.validate_restore(saved)
    assert current.entries[0] is owned_entry


def test_prepare_apply_is_single_use_and_reuses_owned_unchanged_receipts():
    saved = receipt_state()
    current = ledger()
    current.load_state_dict(saved)
    original = current.entries[0]
    plan = current.prepare_restore(json.loads(_json(saved)))
    current.apply_restore(plan)
    assert current.entries[0] is original
    with pytest.raises(ValueError, match="consumed"):
        current.apply_restore(plan)
    other = ledger()
    with pytest.raises(ValueError, match="foreign"):
        other.apply_restore(current.prepare_restore(saved))


@pytest.mark.parametrize("changed", ["source", "ledger", "prepared"])
def test_prepared_restore_rejects_in_memory_mutation_atomically(changed):
    saved = receipt_state()
    current = ledger()
    current.load_state_dict(saved)
    plan = current.prepare_restore(saved)
    if changed == "source":
        saved["entries"][0]["usage"]["input_tokens"] += 1
    elif changed == "ledger":
        current.entries[0]["usage"]["input_tokens"] += 1
    else:
        plan.fields["stage"] = "test"
    before = current.state_dict()
    with pytest.raises(ValueError, match="changed after validation"):
        current.apply_restore(plan)
    assert current.state_dict() == before
    assert not plan.used


def test_late_invalid_extension_does_not_mutate_pending_recovery_or_policy():
    older, durable = pending_states()
    current = ledger()
    current.load_state_dict(older)
    saved = copy.deepcopy(older)
    saved["entries"][0]["recovery_events"] = [{"event": "locally recorded"}]
    saved["scope_activations"] = [{"scope_id": "not-registered", "after_entries": 0}]
    before, saved_before = current.state_dict(), copy.deepcopy(saved)
    for operation in (current.prepare_restore, current.validate_restore, current.load_state_dict):
        with pytest.raises(ValueError, match="scope activation"):
            operation(saved)
        assert current.state_dict() == before
        assert saved == saved_before


def test_imported_mutable_values_do_not_alias_caller_and_newer_suffix_survives():
    saved = receipt_state()
    current = ledger()
    current.load_state_dict(saved)
    saved["entries"][0]["usage"]["input_tokens"] += 1
    assert current.entries[0]["usage"]["input_tokens"] == 120
    current.reserve(0.2, kind="role", model="fixture", metadata={"request_hash": "new"})
    before = current.state_dict()
    plan = current.prepare_restore(receipt_state())
    current.apply_restore(plan)
    assert current.state_dict() == before
    assert current._recovered_inflight == {1}


def test_readonly_receipts_are_reused_but_pending_receipts_become_owned(tmp_path):
    saved = receipt_state()
    original = ledger()
    original.load_state_dict(saved)
    original.reserve(0.2, kind="role", model="fixture", metadata={"request_hash": "pending"})
    store = EvidenceStore(tmp_path)
    raw = store.compact_ledger(original.state_dict())
    path = tmp_path / "ledger.json"
    path.write_text(json.dumps(ledger_envelope(raw)))
    with StartupVerification():
        loaded = load_ledger(path)
        current = ledger()
        current.load_state_dict(loaded)
        assert current.entries[0] is loaded["entries"][0]
        current.entries[1]["status"] = "dispatched"
        assert loaded["entries"][1]["status"] == "reserved"
        assert current.effective_usage(current.entries[0])["input_tokens"] == 120
        with pytest.raises(TypeError, match="read-only"):
            current.entries[0]["usage"]["input_tokens"] += 1
        # A private independent mutable materialization cannot alter cached data.
        mutable = mutable_json(current.entries[0])
        mutable["usage"]["input_tokens"] += 1
        assert loaded["entries"][0]["usage"]["input_tokens"] == 120


def test_restored_readonly_policy_retains_mutable_owned_append_boundaries(tmp_path):
    from examples.adaptive_multi_agent_collab.infrastructure.failure_policy import FailurePolicyConfig
    from examples.adaptive_multi_agent_collab.tests.test_failure_policy_resources import controller

    original = controller().ledger
    store = EvidenceStore(tmp_path)
    state = store.compact_ledger(original.state_dict())
    state["failure_policy"] = store.put(state["failure_policy"])
    path = tmp_path / "ledger.json"
    path.write_text(json.dumps(ledger_envelope(state)))
    with StartupVerification():
        saved = load_ledger(path)
        current = ExpenditureLedger(original.limits)
        current.load_state_dict(saved)
        policy = saved["failure_policy"]
        current.activate_failure_policy(FailurePolicyConfig(**policy["config"]),
            authorization=policy["authorization"], bindings=policy["bindings"],
            execution_guard=lambda: True)
        token = current.begin_logical_request("fixture")
        current.finish_logical_request(token)
        assert [event["event"] for event in current.failure_policy["events"]] == ["logical-start", "logical-finish"]
        assert saved["failure_policy"]["events"] == []
