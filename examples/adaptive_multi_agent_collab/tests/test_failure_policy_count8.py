"""Count-eight candidate admission; every ledger and model is an offline fixture."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import time

import pytest

from examples.adaptive_multi_agent_collab.providers import resources
from examples.adaptive_multi_agent_collab.config import Config
from examples.adaptive_multi_agent_collab.infrastructure.failure_policy import (
    FailurePolicyConfig,
    logical_summary,
    proposed_failure_policy,
)
from examples.adaptive_multi_agent_collab.providers.resources import PolicyStopped, ResourceFailure, UncertaintyCapacity
from examples.adaptive_multi_agent_collab.tests.test_failure_policy_resources import Model, call, completed, controller


def candidate():
    ctl = controller(policy=FailurePolicyConfig(enabled=True, max_unresolved_requests=8))
    assert ctl.ledger.semaphore._value == 3
    assert ctl.ledger.failure_policy["config"]["max_unresolved_reserved_usd"] == 0.5
    assert ctl.ledger.failure_policy["config"]["max_consecutive_provider_failures"] == 5
    return ctl


def test_proposed_factory_selects_eight_without_changing_legacy_disabled_fingerprint():
    before = Config().to_dict()
    fingerprint = hashlib.sha256(json.dumps(before, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    proposal = proposed_failure_policy()
    assert proposal == FailurePolicyConfig(
        enabled=True,
        max_unresolved_requests=8,
        max_unresolved_reserved_usd=0.50,
        max_consecutive_provider_failures=5,
    )
    assert FailurePolicyConfig().enabled is False
    assert FailurePolicyConfig().max_unresolved_requests == 5
    assert "failure_policy" not in before
    after = Config().to_dict()
    assert after == before and "failure_policy" not in after
    assert hashlib.sha256(json.dumps(after, sort_keys=True, separators=(",", ":")).encode()).hexdigest() == fingerprint


def occupied(ledger, *, terminal=7, count=8, reservation=0.01):
    entries = [ledger.reserve(reservation, kind="role", model="fixture", metadata={}) for _ in range(count)]
    for entry in entries[:terminal]:
        ledger.settle(entry, {"total_cost": None, "accounting_complete": False}, status="failed", elapsed=0)
    return entries


def test_eight_total_slots_admit_seven_terminal_and_one_inflight_then_refuse_ninth():
    ledger = candidate().ledger
    entries = occupied(ledger)
    summary = ledger.uncertainty_summary()
    assert summary["reserved_slots"] == 8
    assert summary["terminal_unresolved_ids"] == list(range(7))
    assert summary["inflight_ids"] == [7]
    before = ledger.state_dict()
    with pytest.raises(UncertaintyCapacity):
        ledger.reserve(0.01, kind="role", model="fixture", metadata={})
    assert ledger.state_dict() == before
    ledger.settle(entries[7], {"total_cost": None, "accounting_complete": False}, status="failed", elapsed=0)
    assert ledger.uncertainty_summary()["terminal_unresolved_ids"] == list(range(8))
    assert ledger.dispatch_stop_reason["operational_drain"] is True
    assert ledger.uncertainty_summary()["retained_reservations"] == pytest.approx(0.08)
    with pytest.raises(PolicyStopped):
        ledger.reserve(0.001, kind="role", model="fixture", metadata={})
    assert len(ledger.entries) == 8


@pytest.mark.parametrize("bound", ["slots", "money"])
def test_temporary_wait_adds_no_attempt_hold_or_failure_streak_and_uses_remaining_deadline(monkeypatch, bound):
    ctl = candidate()
    if bound == "slots":
        entries = occupied(ctl.ledger)
        before_hold, terminal_hold = 0.08, 0.07
    else:
        terminal = ctl.ledger.reserve(0.49, kind="role", model="fixture", metadata={})
        ctl.ledger.settle(terminal, {"total_cost": None, "accounting_complete": False}, status="failed", elapsed=0)
        entries = [terminal, ctl.ledger.reserve(0.01, kind="role", model="fixture", metadata={})]
        before_hold, terminal_hold = 0.50, 0.49
    preserved_terminal = copy.deepcopy(entries[:-1])
    count_before = len(entries)
    model = Model([completed()])
    original_sleep = asyncio.sleep

    async def scenario():
        waiting = asyncio.Event()

        async def observed_sleep(delay):
            assert delay == 0.01
            waiting.set()
            await original_sleep(delay)

        monkeypatch.setattr(resources.asyncio, "sleep", observed_sleep)
        deadline = time.monotonic() + 0.5
        started = time.monotonic()
        pending = asyncio.create_task(call(ctl, model, deadline=deadline))
        await asyncio.wait_for(waiting.wait(), timeout=0.2)
        assert not model.calls and len(ctl.ledger.entries) == count_before
        assert ctl.ledger.uncertainty_summary()["retained_reservations"] == pytest.approx(before_hold)
        assert logical_summary(ctl.ledger.failure_policy)["backend_streaks"] == {"fixture": 0}
        assert ctl.ledger.dispatch_stop_reason is None
        await original_sleep(0.02)
        ctl.ledger.settle(
            entries[-1], {"total_cost": 0.001, "accounting_complete": True}, status="completed", elapsed=0
        )
        result = await pending
        assert result.response.content and len(model.calls) == 1
        assert len(ctl.ledger.entries) == count_before + 1
        assert 0 < model.calls[0]["timeout"] < deadline - started - 0.01
        assert time.monotonic() < deadline
        assert entries[:-1] == preserved_terminal
        assert ctl.ledger.uncertainty_summary()["retained_reservations"] == pytest.approx(terminal_hold)
        assert ctl.ledger.dispatch_stop_reason is None
        assert logical_summary(ctl.ledger.failure_policy)["backend_streaks"] == {"fixture": 0}

    asyncio.run(scenario())


def test_wait_timeout_retains_all_holds_and_never_counts_as_provider_failure():
    ctl = candidate()
    for _ in range(2):
        token = ctl.ledger.begin_logical_request("fixture")
        entry = ctl.ledger.reserve(0.01, kind="role", model="fixture", metadata={"provider_logical_request_id": token})
        ctl.ledger.settle(
            entry,
            {
                "total_cost": 0,
                "accounting_complete": True,
                "provider_completed": False,
                "failure_kind": "provider-unavailable",
                "model_calls": 1,
            },
            status="failed",
            elapsed=0,
        )
        ctl.ledger.finish_logical_request(token)
    occupied(ctl.ledger)
    entries_before = copy.deepcopy(ctl.ledger.entries)
    model = Model([completed()])
    started = time.monotonic()
    deadline = started + 0.035
    with pytest.raises(ResourceFailure, match="deadline") as caught:
        asyncio.run(call(ctl, model, deadline=deadline))
    assert not model.calls
    assert ctl.ledger.entries == entries_before
    assert ctl.ledger.uncertainty_summary()["retained_reservations"] == pytest.approx(0.08)
    assert caught.value.usage_record.model_calls == 0
    assert logical_summary(ctl.ledger.failure_policy)["backend_streaks"] == {"fixture": 2}
    assert ctl.ledger.dispatch_stop_reason is None
    assert time.monotonic() - started < 0.2
    finishes = [event for event in ctl.ledger.failure_policy["events"] if event["event"] == "logical-finish"]
    assert [event["outcome"] for event in finishes] == ["provider-failed", "provider-failed", "not-dispatched"]


def test_completed_missing_usage_known_charge_consumes_slot_without_duplicate_hold():
    ledger = candidate().ledger
    entries = [ledger.reserve(0.01, kind="role", model="fixture", metadata={}) for _ in range(8)]
    for entry in entries:
        ledger.settle(entry, {"total_cost": 0.001, "accounting_complete": False}, status="completed", elapsed=0)
    assert ledger.uncertainty_summary()["terminal_unresolved_ids"] == list(range(8))
    assert ledger.uncertainty_summary()["retained_reservations"] == 0
    assert ledger.stage_summary("pilot")["paid"] == pytest.approx(0.008)
    assert ledger.dispatch_stop_reason["operational_drain"] is True


def test_permanent_monetary_saturation_stops_without_waiting_for_irrelevant_inflight():
    ledger = candidate().ledger
    terminal = ledger.reserve(0.49, kind="role", model="fixture", metadata={})
    ledger.settle(terminal, {"total_cost": None, "accounting_complete": False}, status="failed", elapsed=0)
    ledger.reserve(0.001, kind="role", model="fixture", metadata={})
    # Even an authoritative zero bill for the in-flight request cannot admit
    # another reservation of 0.02 alongside the retained terminal 0.49 hold.
    with pytest.raises(PolicyStopped):
        ledger.reserve(0.02, kind="role", model="fixture", metadata={})
    assert len(ledger.entries) == 2
    assert ledger.uncertainty_summary()["retained_reservations"] == pytest.approx(0.491)
    assert ledger.dispatch_stop_reason["operational_drain"] is True


def test_capacity_clearing_after_original_deadline_cannot_dispatch_or_reserve(monkeypatch):
    ctl = candidate()
    entries = occupied(ctl.ledger)
    model = Model([completed()])
    original_sleep = asyncio.sleep

    async def after_deadline(delay):
        assert delay == 0.01
        await original_sleep(0.04)
        ctl.ledger.settle(entries[7], {"total_cost": 0.001, "accounting_complete": True}, status="completed", elapsed=0)

    monkeypatch.setattr(resources.asyncio, "sleep", after_deadline)
    with pytest.raises(ResourceFailure, match="deadline"):
        asyncio.run(call(ctl, model, deadline=time.monotonic() + 0.025))
    assert not model.calls
    assert len(ctl.ledger.entries) == 8
    assert ctl.ledger.uncertainty_summary()["retained_reservations"] == pytest.approx(0.07)
    assert logical_summary(ctl.ledger.failure_policy)["backend_streaks"] == {"fixture": 0}
