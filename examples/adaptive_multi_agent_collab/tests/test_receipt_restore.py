"""Strict serialized receipt equality and monotonic checkpoint restoration."""
from __future__ import annotations

import copy
import json

import pytest

from examples.adaptive_multi_agent_collab.config import ResourceConfig
from examples.adaptive_multi_agent_collab.infrastructure.persistence import _json
from examples.adaptive_multi_agent_collab.providers.resources import ExpenditureLedger


def ledger():
    return ExpenditureLedger(ResourceConfig(development_budget=80, currency="USD"))


def receipt_state():
    value = ledger()
    entry = value.reserve(0.25, kind="role", model="fixture", metadata={"request_hash": "request-0"})
    value.settle(
        entry,
        {
            "total_cost": 0.0123,
            "input_tokens": 120,
            "output_tokens": 8,
            "generation_id": "generation-0",
            "raw_gateway_frames": (
                {"id": "generation-0", "choices": ({"text": "first"},)},
                {"id": "generation-0", "choices": (), "usage": {"cost": 0.0123}},
            ),
        },
        status="completed",
        elapsed=0.4,
    )
    return value.state_dict()


def test_json_equivalent_receipts_restore_without_mutating_or_duplicating_either_input():
    checkpoint = receipt_state()
    durable = json.loads(_json(checkpoint))
    value = ledger()
    value.load_state_dict(durable)
    saved_before, current_before = copy.deepcopy(checkpoint), copy.deepcopy(value.state_dict())
    value.on_change = lambda: pytest.fail("validation/restore must not persist")
    value.validate_restore(checkpoint)
    assert value.state_dict() == current_before
    for _ in range(2):
        value.load_state_dict(checkpoint)
        assert value.state_dict() == current_before
    assert checkpoint == saved_before
    assert isinstance(checkpoint["entries"][0]["usage"]["raw_gateway_frames"], tuple)
    assert isinstance(value.entries[0]["usage"]["raw_gateway_frames"], list)
    assert len(value.entries) == 1


@pytest.mark.parametrize("difference", ["frame-content", "frame-order", "frame-removed", "identity", "usage", "charge", "hold"])
def test_real_receipt_differences_are_rejected_atomically(difference):
    checkpoint = receipt_state()
    value = ledger()
    value.load_state_dict(json.loads(_json(checkpoint)))
    before = copy.deepcopy(value.state_dict())
    entry = checkpoint["entries"][0]
    if difference == "frame-content":
        entry["usage"]["raw_gateway_frames"][0]["choices"][0]["text"] = "changed"
    elif difference == "frame-order":
        entry["usage"]["raw_gateway_frames"] = entry["usage"]["raw_gateway_frames"][::-1]
    elif difference == "frame-removed":
        entry["usage"]["raw_gateway_frames"] = entry["usage"]["raw_gateway_frames"][:1]
    elif difference == "identity":
        entry["request_hash"] = "different-request"
    elif difference == "usage":
        entry["usage"]["input_tokens"] += 1
    elif difference == "charge":
        entry["charged"] += 1e-12
    else:
        entry["reserved"] += 1e-12
    for operation in (value.validate_restore, value.load_state_dict):
        with pytest.raises(ValueError, match="rewrite"):
            operation(checkpoint)
        assert value.state_dict() == before


@pytest.mark.parametrize("unsupported", [object(), float("nan"), float("inf")])
def test_unsupported_or_nonfinite_accounting_is_not_stringified(unsupported):
    checkpoint = receipt_state()
    checkpoint["entries"][0]["usage"]["raw_gateway_frames"][0]["bad"] = unsupported
    for current in (ledger(), ledger()):
        with pytest.raises((ValueError, TypeError)):
            current.load_state_dict(checkpoint)
        assert current.entries == []


def pending_states():
    value = ledger()
    entry = value.reserve(0.25, kind="role", model="fixture", metadata={"request_hash": "request-0"})
    older = value.state_dict()
    entry.update(status="dispatched", charged=0.013, usage={"input_tokens": 120, "total_cost": 0.013})
    entry["recovery_events"] = [{"event_id": 0, "event": "lookup-intent", "attempt": 1}]
    value.reserve(0.2, kind="revision", model="fixture", metadata={"request_hash": "request-1"})
    value.entries[1]["status"] = "dispatched"
    return older, value.state_dict()


def test_older_checkpoint_preserves_newer_pending_charge_usage_holds_and_dispatch_markers():
    older, durable = pending_states()
    value = ledger()
    value.load_state_dict(durable)
    before = copy.deepcopy(value.state_dict())
    for operation in (value.validate_restore, value.load_state_dict, value.load_state_dict):
        operation(older)
        assert value.state_dict() == before
        assert value._recovered_inflight == {0, 1}
    assert value.entries[0]["charged"] == 0.013
    assert value.entries[0]["usage"]["input_tokens"] == 120
    assert value.entries[1]["charged"] is None and value.entries[1]["reserved"] == 0.2
    assert all(item["status"] == "dispatched" for item in value.entries)


@pytest.mark.parametrize("field", ["charge", "usage", "recovery"])
def test_conflicting_known_pending_receipt_is_rejected(field):
    older, durable = pending_states()
    saved = copy.deepcopy(durable)
    saved["entries"] = saved["entries"][:1]
    if field == "charge":
        saved["entries"][0]["charged"] += 1e-12
    elif field == "usage":
        saved["entries"][0]["usage"]["input_tokens"] += 1
    else:
        saved["entries"][0]["recovery_events"][0]["attempt"] += 1
    value = ledger()
    value.load_state_dict(durable)
    for operation in (value.validate_restore, value.load_state_dict):
        with pytest.raises(ValueError, match="rewrite"):
            operation(saved)
        assert value.state_dict() == durable


def test_pending_receipt_ahead_of_durable_dispatch_is_rejected():
    older, newer = pending_states()
    newer["entries"] = newer["entries"][:1]
    value = ledger()
    value.load_state_dict(older)
    with pytest.raises(ValueError, match="ahead of durable"):
        value.load_state_dict(newer)
    assert value.state_dict() == older


def test_later_finalized_receipts_and_suffix_survive_older_pending_checkpoint():
    older = ledger()
    entry = older.reserve(0.25, kind="role", model="fixture", metadata={"request_hash": "request-0"})
    pending = older.state_dict()
    older.settle(entry, {"total_cost": 0.03, "input_tokens": 100, "output_tokens": 10}, status="completed", elapsed=1)
    later = older.reserve(0.25, kind="revision", model="fixture", metadata={"request_hash": "request-1"})
    older.settle(later, {"total_cost": 0.01}, status="completed", elapsed=1)
    final = older.state_dict()
    current = ledger()
    current.load_state_dict(pending)
    current.load_state_dict(final)
    assert current.state_dict() == final
    current.validate_restore(pending)
    current.load_state_dict(pending)
    assert current.state_dict() == final
