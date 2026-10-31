"""Prospective count32/USD5 policy; all models and ledgers here are disposable."""
from __future__ import annotations

import asyncio
import copy
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from examples.adaptive_multi_agent_collab.operations import calibration_launch, experiment, supplement
from examples.adaptive_multi_agent_collab.infrastructure.failure_policy import FailurePolicyConfig, fingerprint
from examples.adaptive_multi_agent_collab.providers.resources import ExpenditureLedger, PolicyStopped, UncertaintyCapacity
from examples.adaptive_multi_agent_collab.tests.test_failure_policy_resources import controller
from examples.adaptive_multi_agent_collab.tests.test_supplement_plan import registered as registered
from examples.adaptive_multi_agent_collab.tests.test_pilot2_policy import independent as independent


NEW = FailurePolicyConfig(enabled=True, max_unresolved_requests=32, max_unresolved_reserved_usd=5)


def amendment(ledger):
    old = copy.deepcopy(ledger.failure_policy)
    return {"protocol": calibration_launch.TRANSITION_PROTOCOL, "plan_fingerprint": "p" * 64,
            "authorization": {"path": "fixture", "sha256": "a" * 64},
            "parent_policy": old, "parent_policy_sha256": fingerprint(old), "receipt_count": len(ledger.entries)}


def apply(ledger, audit, **changes):
    arguments = dict(config=NEW, authorization={"approved": True, "source": "fixture calibration only"},
                     bindings={"plan_fingerprint": "p" * 64}, execution_guard=lambda: True, amendment=audit)
    arguments.update(changes)
    ledger.amend_failure_policy(**arguments)
    return arguments


def terminal(ledger, amount=.01):
    entry = ledger.reserve(amount, kind="role", model="fixture", metadata={})
    ledger.settle(entry, {"total_cost": None, "accounting_complete": False}, status="failed", elapsed=0)
    return entry


def test_exact_amendment_preserves_unknown_holds_limits_events_and_original_approval():
    ctl = controller(policy=FailurePolicyConfig(enabled=True, max_unresolved_requests=8))
    for _ in range(6):
        terminal(ctl.ledger)
    before = ctl.ledger.state_dict()
    audit = amendment(ctl.ledger)
    arguments = apply(ctl.ledger, audit)
    after = ctl.ledger.state_dict()
    for name in before:
        if name != "failure_policy":
            assert after[name] == before[name]
    assert after["failure_policy"]["condition_amendments"][0]["parent_policy"] == before["failure_policy"]
    assert after["failure_policy"]["events"] == before["failure_policy"]["events"]
    assert ctl.ledger.semaphore._value == 3
    assert ctl.ledger.uncertainty_summary()["retained_reservations"] == pytest.approx(.06)
    ctl.ledger.amend_failure_policy(**arguments)
    assert ctl.ledger.state_dict() == after
    restored = ExpenditureLedger(ctl.ledger.limits)
    restored.load_state_dict(after)
    restored.activate_failure_policy(NEW, authorization=arguments["authorization"], bindings=arguments["bindings"],
                                     execution_guard=lambda: True, condition_amendments=[audit])
    assert restored.state_dict() == after
    with pytest.raises(ValueError, match="rewrite failure-policy"):
        restored.load_state_dict(before)


@pytest.mark.parametrize("violation", ["lock", "config", "parent", "hash", "halt", "inflight"])
def test_amendment_rejects_wrong_or_unsafe_parent_without_mutation(violation):
    ledger = controller(policy=FailurePolicyConfig(enabled=True, max_unresolved_requests=8)).ledger
    audit = amendment(ledger)
    changes = {}
    if violation == "lock":
        changes["execution_guard"] = lambda: False
    elif violation == "config":
        changes["config"] = replace(NEW, max_consecutive_provider_failures=6)
    elif violation == "parent":
        audit["parent_policy"]["authorization"]["source"] = "changed"
    elif violation == "hash":
        audit["parent_policy_sha256"] = "bad"
    elif violation == "halt":
        ledger.halted = {"reason": "fixture hard halt"}
    else:
        ledger.reserve(.01, kind="role", model="fixture", metadata={})
        audit = amendment(ledger)
    before = ledger.state_dict()
    with pytest.raises((ValueError, RuntimeError)):
        apply(ledger, audit, **changes)
    assert ledger.state_dict() == before


@pytest.mark.parametrize("bound", ["count", "money"])
def test_amended_capacity_waits_for_inflight_and_persistently_pauses_for_unknown_terminal(bound):
    ledger = controller(policy=FailurePolicyConfig(enabled=True, max_unresolved_requests=8)).ledger
    apply(ledger, amendment(ledger))
    if bound == "count":
        for _ in range(31):
            terminal(ledger)
        active = ledger.reserve(.01, kind="role", model="fixture", metadata={})
    else:
        terminal(ledger, 4.99)
        active = ledger.reserve(.01, kind="role", model="fixture", metadata={})
    before = ledger.state_dict()
    with pytest.raises(UncertaintyCapacity):
        ledger.reserve(.01, kind="role", model="fixture", metadata={})
    assert ledger.state_dict() == before
    ledger.settle(active, {"total_cost": None, "accounting_complete": False}, status="failed", elapsed=0)
    with pytest.raises(PolicyStopped):
        ledger.reserve(.01, kind="role", model="fixture", metadata={})
    assert ledger.failure_policy["config"]["max_consecutive_provider_failures"] == 5


def test_corrupt_amendment_or_original_approval_rejected_on_restore():
    ledger = controller(policy=FailurePolicyConfig(enabled=True, max_unresolved_requests=8)).ledger
    apply(ledger, amendment(ledger))
    for change in ("authorization", "receipt_count", "plan_fingerprint"):
        state = ledger.state_dict()
        item = state["failure_policy"]["condition_amendments"][0]
        if change == "authorization":
            item["parent_policy"]["authorization"]["source"] = "corrupted"
        else:
            item[change] = -1
        with pytest.raises(ValueError):
            ExpenditureLedger(ledger.limits).load_state_dict(state)


def test_prepared_change_affects_only_two_policy_coordinates_and_new_bindings(registered, monkeypatch):
    original = replace(registered.prepared,
        config=replace(registered.prepared.config, failure_policy=FailurePolicyConfig(enabled=True, max_unresolved_requests=8)))
    parent = supplement.apply_prepared(original, {**registered.plan, "parent": {**registered.plan["parent"]}})
    # Fixture planning is deliberately pure; no encoder, provider or campaign mutation.
    monkeypatch.setattr(experiment, "planning_hooks", lambda value: SimpleNamespace(condition_fingerprint="d" * 64))
    plan = {"protocol": calibration_launch.PLAN_PROTOCOL, "amendment": calibration_launch.amendment(),
            "parent": {"condition_fingerprint": "d" * 64, "manifest_fingerprint": parent.authorization_manifest_fingerprint,
                       "resource_profile_fingerprint": parent.resource_profile_fingerprint, "specification_sha256": parent.specification_sha256},
            "references": {"formal_specification": {"sha256": "s" * 64}}}
    before = parent.config.to_dict()
    updated = calibration_launch.apply_prepared(parent, plan)
    expected = copy.deepcopy(before)
    expected["failure_policy"].update(calibration_launch.amendment())
    assert updated.config.to_dict() == expected and parent.config.to_dict() == before
    assert updated.config.resources == parent.config.resources
    assert updated.continuation_registration == parent.continuation_registration
    assert updated.campaign_binding == parent.campaign_binding
    assert updated.resource_profile_fingerprint != parent.resource_profile_fingerprint
    assert updated.authorization_manifest_fingerprint != parent.authorization_manifest_fingerprint
    assert updated.termination_plan.candidates == parent.termination_plan.candidates
    for name, value in parent.plans.items():
        assert updated.plans[name].ordered_example_ids == value.ordered_example_ids
        assert updated.plans[name].run_seed == value.run_seed
        assert updated.plans[name].fingerprint != value.fingerprint


def test_preservation_records_the_new_transaction_without_relabelling_old_sources(tmp_path):
    (tmp_path / "checkpoint.pt").write_bytes(b"synthetic checkpoint")
    (tmp_path / "accounting_progress.jsonl").write_bytes(b"synthetic immutable WAL prefix\n")
    destination = tmp_path / "before"
    result = supplement._preserve_publications(tmp_path, destination, protocol=calibration_launch.TRANSITION_PROTOCOL)
    assert result["protocol"] == calibration_launch.TRANSITION_PROTOCOL
    assert (destination / "checkpoint.pt").read_bytes() == (tmp_path / "checkpoint.pt").read_bytes()
    assert supplement._preserve_publications(tmp_path, destination, protocol=calibration_launch.TRANSITION_PROTOCOL) == result
    assert (tmp_path / "accounting_progress.jsonl").read_bytes() == b"synthetic immutable WAL prefix\n"


@pytest.mark.parametrize("tamper", ["missing_activation", "changed_approval"])
def test_amended_policy_retains_exact_original_activation_evidence(independent, tamper):
    from examples.adaptive_multi_agent_collab.operations import policy_activation as policy
    from examples.adaptive_multi_agent_collab.tests.test_pilot2_policy import _plan, _approve, _activate

    _plan(independent)
    _approve(independent)
    receipt = _activate(independent)
    ledger_path = independent.anchor / "expenditure_ledger.json"
    ledger = ExpenditureLedger(independent.config.resources)
    from examples.adaptive_multi_agent_collab.infrastructure.storage import load_ledger
    ledger.load_state_dict(load_ledger(ledger_path))
    from examples.adaptive_multi_agent_collab.infrastructure.persistence import DurableJournal
    journal = DurableJournal([independent.anchor], ledger.state_dict())
    ledger.durable_journal = journal
    apply(ledger, amendment(ledger))
    journal.publish(ledger.state_dict(), independent.anchor / "checkpoint.pt", safe=True)
    asyncio.run(journal.aclose())
    assert policy.validate_policy_condition(independent.specification, independent.anchor) == receipt
    if tamper == "missing_activation":
        (independent.anchor / "policy_activation.json").unlink()
    else:
        approval = Path(receipt["authorization"]["path"])
        contents = json.loads(approval.read_text())
        contents["rationale"] = "changed after prospective amendment"
        approval.write_text(json.dumps(contents))
    with pytest.raises(ValueError):
        policy.validate_policy_condition(independent.specification, independent.anchor)
