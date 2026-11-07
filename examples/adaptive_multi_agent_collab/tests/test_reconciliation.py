"""Metadata-only settlement and recheck reservations, with no network or secrets."""

from __future__ import annotations

import asyncio
import copy
import fcntl
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from examples.adaptive_multi_agent_collab.config import ResourceConfig
from examples.adaptive_multi_agent_collab.providers.gateway import lookup_generation_metadata
from examples.adaptive_multi_agent_collab.operations.reconciliation import (
    ACCOUNTING_HALT,
    billing_mode_evidence,
    reconcile_campaign,
    settle_cached_receipts,
    validate_generation_receipt,
    validate_reconciliation_receipt,
)
from examples.adaptive_multi_agent_collab.providers.resources import BudgetExhausted, ExpenditureLedger


def historical(tmp_path):
    campaign = tmp_path / "campaign"
    campaign.mkdir()
    ledger = ExpenditureLedger(ResourceConfig(compatibility_budget=3, pilot_budget=5, currency="USD"))
    ledger.stage = "pilot"
    binding = {
        "model": "openai/gpt-4.1-mini",
        "provider_endpoint": "openai",
        "revision_binding": "approved alias",
        "binding_evidence": "fixture evidence",
        "approved": True,
        "limited_reproducibility_approved": True,
        "served_provider": "OpenAI",
    }
    for index in range(3):
        ledger.entries.append(
            {
                "dispatch_id": index,
                "stage": "pilot",
                "kind": "role",
                "model": binding["model"],
                "category": "collaboration",
                "reserved": 0.1,
                "charged": None,
                "status": "failed",
                "input_count": 100,
                "input_count_method": "estimated",
                "reserved_input_tokens": 200,
                "output_cap": 256,
                "request_sha256": "unchanged request",
                "effective_input": [{"content": "original"}],
                "billing_tariff": {"reference_input_price": 0.0001, "reference_output_price": 0.0002},
                "usage": {"generation_id": f"gen-fixture-{index}", "binding": binding, "error": "original failure"},
            }
        )
    ledger.halted = {"reason": ACCOUNTING_HALT, "stage": "pilot", "after_entries": 3}
    state = ledger.state_dict()
    (campaign / "expenditure_ledger.json").write_text(json.dumps(state, indent=3), encoding="utf-8")
    return campaign, state


def native(generation_id):
    return {
        "http_status": 200,
        "data": {
            "id": generation_id,
            "model": "openai/gpt-4.1-mini",
            "provider_name": "OpenAI",
            "native_tokens_prompt": 30,
            "native_tokens_completion": 12,
            "native_tokens_cached": 0,
            "native_tokens_reasoning": 0,
            "tokens_prompt": 99999,
            "tokens_completion": 99999,
            "total_cost": 0.01,
            "upstream_inference_cost": 0.99,
            "is_byok": False,
            "finish_reason": "length",
            "native_finish_reason": "max_tokens",
        },
    }


def invoke(campaign, audit, lookup):
    source_path = campaign.parent / "fixture-authority.txt"
    source_path.write_text("Explicit fixture authorization for offline tests only.")
    source_ledger = (
        (audit / "source_ledger.json")
        if (audit / "source_ledger.json").exists()
        else campaign / "expenditure_ledger.json"
    )
    authorization = {
        "protocol": "historical_accounting_lookup_authorization_v1",
        "decision": "approve_bounded_accounting_lookup_and_evidence_backed_settlement",
        "campaign_directory": str(campaign.resolve()),
        "original_ledger_sha256": hashlib.sha256(source_ledger.read_bytes()).hexdigest(),
        "maximum_additional_http_attempts": 9,
        "maximum_additional_http_attempts_per_generation": 3,
        "stop_on_credential_rejection": True,
        "new_generation_authorized_by_this_artifact": False,
        "pilot_authorized": False,
        "development_authorized": False,
        "dispatch_generation_ids": {str(index): f"gen-fixture-{index}" for index in range(3)},
        "source_transcription_path": str(source_path),
        "source_transcription_sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
    }
    return asyncio.run(
        reconcile_campaign(
            campaign,
            audit,
            lookup=lookup,
            authorization=authorization,
            dispatch_ids=(0, 1, 2),
            disposition="settled native debit; retain failure",
        )
    )


def read_ledger(campaign):
    state = json.loads((campaign / "expenditure_ledger.json").read_text())
    ledger = ExpenditureLedger(ResourceConfig(**state["limits"]))
    ledger.load_state_dict(state)
    return ledger


def test_complete_settlement_preserves_receipts_and_restoring_old_checkpoint_keeps_charge(tmp_path):
    campaign, before = historical(tmp_path)
    original_bytes = (campaign / "expenditure_ledger.json").read_bytes()
    calls = []

    async def lookup(generation_id):
        journal = json.loads((tmp_path / "audit" / "lookup_journal.json").read_text())
        assert journal["attempts"][-1]["status"] == "intent_reserved"
        calls.append(generation_id)
        return native(generation_id)

    receipt = invoke(campaign, tmp_path / "audit", lookup)
    ledger = read_ledger(campaign)
    assert len(calls) == 3 and receipt["fully_resolved"]
    assert (tmp_path / "audit" / "source_ledger.json").read_bytes() == original_bytes
    assert ledger.entries == before["entries"] and ledger.halted is None
    assert ledger.stage_summary("pilot")["paid"] == pytest.approx(0.03)
    assert ledger.stage_summary("pilot")["unresolved"] == 0
    usage = ledger.accounting_reconciliations[0]["usage"]
    assert usage["input_tokens"] == 30 and usage["normalized_input_tokens"] == 99999
    assert usage["total_cost"] == 0.01 and usage["native_finish_reason"] == "max_tokens"
    ledger.load_state_dict(before)
    assert ledger.halted is None and ledger.stage_summary("pilot")["paid"] == pytest.approx(0.03)
    ledger.register_scope("recheck", bindings={"plan": "frozen"})
    ledger.activate_scope("recheck")
    ledger.stage = "compatibility"
    ledger.reserve(0.02, kind="initial", model="fixture", metadata={})
    validate_reconciliation_receipt(ledger.state_dict(), receipt)
    corrupted = ledger.state_dict()
    corrupted["entries"][0]["usage"]["error"] = "rewritten"
    with pytest.raises(ValueError, match="prefix"):
        validate_reconciliation_receipt(corrupted, receipt)
    repeated = invoke(campaign, tmp_path / "audit", lookup)
    assert repeated == receipt and len(calls) == 3


def test_partial_settlement_keeps_holds_and_halt_and_never_resets_lookup_limit(tmp_path):
    campaign, before = historical(tmp_path)
    calls = []

    async def lookup(generation_id):
        calls.append(generation_id)
        return native(generation_id) if generation_id.endswith("-0") else {"http_status": 404}

    receipt = invoke(campaign, tmp_path / "audit", lookup)
    assert not receipt["fully_resolved"] and receipt["additional_lookup_attempts"] == 7
    ledger = read_ledger(campaign)
    assert ledger.entries == before["entries"] and ledger.halted == before["halted"]
    assert ledger.stage_summary("pilot")["occupied"] == pytest.approx(0.21)
    invoke(campaign, tmp_path / "audit", lookup)
    assert len(calls) == 7
    with pytest.raises(ValueError, match="cannot reset"):
        invoke(campaign, tmp_path / "other-audit", lookup)
    (tmp_path / "audit" / "lookup_journal.json").unlink()
    with pytest.raises(ValueError, match="evidence is missing"):
        invoke(campaign, tmp_path / "audit", lookup)


def test_credential_rejection_stops_all_followups_and_survives_restart(tmp_path):
    campaign, _ = historical(tmp_path)
    calls = []

    async def rejected(generation_id):
        calls.append(generation_id)
        return {"http_status": 401}

    receipt = invoke(campaign, tmp_path / "audit", rejected)
    assert receipt["credential_rejected"] and len(calls) == 1
    invoke(campaign, tmp_path / "audit", rejected)
    assert len(calls) == 1 and read_ledger(campaign).halted is not None


def test_interrupted_lookup_intent_consumes_attempt_across_restart(tmp_path):
    campaign, _ = historical(tmp_path)

    async def interrupted(generation_id):
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        invoke(campaign, tmp_path / "audit", interrupted)
    # Even rolling back the mutable lookup journal cannot erase the canonical
    # pre-GET reservation, including an intent that has no response receipt.
    journal_path = tmp_path / "audit" / "lookup_journal.json"
    rolled_back = json.loads(journal_path.read_text())
    rolled_back["attempts"] = []
    journal_path.write_text(json.dumps(rolled_back))
    calls = []

    async def unavailable(generation_id):
        calls.append(generation_id)
        return {"http_status": 404}

    receipt = invoke(campaign, tmp_path / "audit", unavailable)
    assert receipt["additional_lookup_attempts"] == 9 and len(calls) == 8
    assert calls.count("gen-fixture-0") == 2
    invoke(campaign, tmp_path / "audit", unavailable)
    assert len(calls) == 8


def test_crash_after_receipt_before_settlement_reuses_evidence_without_another_get(tmp_path, monkeypatch):
    campaign, _ = historical(tmp_path)
    calls = []

    async def lookup(generation_id):
        calls.append(generation_id)
        return native(generation_id)

    original = ExpenditureLedger.record_reconciliation

    def interrupted(self, record):
        raise RuntimeError("fixture crash before ledger settlement")

    monkeypatch.setattr(ExpenditureLedger, "record_reconciliation", interrupted)
    with pytest.raises(RuntimeError, match="fixture crash"):
        invoke(campaign, tmp_path / "audit", lookup)
    monkeypatch.setattr(ExpenditureLedger, "record_reconciliation", original)
    receipt = invoke(campaign, tmp_path / "audit", lookup)
    assert receipt["fully_resolved"] and calls.count("gen-fixture-0") == 1 and len(calls) == 3


def test_invalid_native_receipt_records_sanitized_validation_reason(tmp_path):
    campaign, _ = historical(tmp_path)

    async def lookup(generation_id):
        result = native(generation_id)
        result["data"].pop("native_tokens_prompt")
        return result

    receipt = invoke(campaign, tmp_path / "audit", lookup)
    assert not receipt["fully_resolved"] and receipt["additional_lookup_attempts"] == 9
    journal = json.loads((tmp_path / "audit" / "lookup_journal.json").read_text())
    assert all(
        item["validation_error"] == "native accounting metadata is missing or invalid: input_tokens"
        for item in journal["attempts"]
    )


def test_cached_canonical_identity_settlement_keeps_nine_intents_and_unresolved_halt(tmp_path):
    campaign, original = historical(tmp_path)
    calls = []
    canonical = "openai/gpt-4.1-mini-2025-04-14"

    async def lookup(generation_id):
        calls.append(generation_id)
        if generation_id.endswith("-0"):
            return {"http_status": 404}
        result = native(generation_id)
        result["data"]["model"] = canonical
        return result

    audit = tmp_path / "audit"
    first = invoke(campaign, audit, lookup)
    assert len(calls) == first["additional_lookup_attempts"] == 9 and not first["fully_resolved"]
    original_lookup = (audit / "lookup-004.json").read_bytes()
    original_journal = (audit / "lookup_journal.json").read_bytes()
    original_source = (audit / "source_ledger.json").read_bytes()
    original_ledger = (campaign / "expenditure_ledger.json").read_bytes()
    original_unresolved = (audit / "unresolved_receipt.json").read_bytes()
    intents = read_ledger(campaign).state_dict()["accounting_lookup_intents"]
    catalog = tmp_path / "models.json"
    catalog.write_text(json.dumps({"data": [{"id": "openai/gpt-4.1-mini", "canonical_slug": canonical}]}))
    endpoints = tmp_path / "endpoints.json"
    endpoints.write_text(
        json.dumps(
            {
                "data": {
                    "id": "openai/gpt-4.1-mini",
                    "endpoints": [{"model_id": "openai/gpt-4.1-mini", "provider_name": "OpenAI", "tag": "openai"}],
                }
            }
        )
    )
    settled = asyncio.run(
        settle_cached_receipts(
            campaign, audit, model_catalog_path=catalog, endpoint_catalog_paths={"openai/gpt-4.1-mini": endpoints}
        )
    )
    ledger = read_ledger(campaign)
    assert len(calls) == settled["additional_lookup_attempts"] == 9
    assert not settled["fully_resolved"] and settled["unresolved_dispatch_ids"] == [0]
    assert ledger.halted == original["halted"] and ledger.entries == original["entries"]
    assert ledger.stage_summary("pilot")["paid"] == pytest.approx(0.02)
    assert ledger.stage_summary("pilot")["unresolved"] == pytest.approx(0.1)
    assert ledger.state_dict()["accounting_lookup_intents"] == intents
    assert (audit / "lookup-004.json").read_bytes() == original_lookup
    assert (audit / "lookup_journal.json").read_bytes() == original_journal
    assert (audit / "source_ledger.json").read_bytes() == original_source
    assert (
        json.loads((audit / "settlement-1.json").read_text())["model_identity_evidence"]["metadata_model"] == canonical
    )
    assert list(audit.glob("unresolved_receipt-*.json"))
    adjudication_directory = next(audit.glob("cached-adjudication-*"))
    assert (adjudication_directory / "before_ledger.json").read_bytes() == original_ledger
    assert (adjudication_directory / "before_unresolved_receipt.json").read_bytes() == original_unresolved
    # Catalog evidence stays part of validation rather than becoming a wildcard.
    catalog.write_text(json.dumps({"data": [{"id": "openai/gpt-4.1-mini", "canonical_slug": "another-snapshot"}]}))
    with pytest.raises(ValueError, match="catalog changed"):
        read_ledger(campaign)


def test_canonical_mapping_requires_structured_fields_and_the_exact_direct_route(tmp_path):
    campaign, before = historical(tmp_path)
    catalog = tmp_path / "models.json"
    catalog.write_text(json.dumps({"data": [{"id": "openai/gpt-4.1-mini", "name": "OpenAI | canonical-only-in-name"}]}))
    payload = native("gen-fixture-0")["data"]
    payload["model"] = "canonical-only-in-name"
    evidence = {
        "protocol": "documented_accounting_model_identity_1",
        "requested_model": "openai/gpt-4.1-mini",
        "metadata_model": payload["model"],
        "provider_endpoint": "openai",
        "served_provider": "OpenAI",
    }
    endpoint = tmp_path / "endpoint.json"
    endpoint.write_text(
        json.dumps(
            {
                "data": {
                    "id": "openai/gpt-4.1-mini",
                    "endpoints": [
                        {"model_id": "openai/gpt-4.1-mini", "provider_name": "OpenAI", "tag": "openai/other"}
                    ],
                }
            }
        )
    )
    for key, path in (("model_catalog", catalog), ("endpoint_catalog", endpoint)):
        evidence[key] = {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    with pytest.raises(ValueError, match="explicitly map"):
        validate_generation_receipt(before["entries"][0], payload, model_identity_evidence=evidence)
    catalog.write_text(json.dumps({"data": [{"id": "openai/gpt-4.1-mini", "canonical_slug": payload["model"]}]}))
    evidence["model_catalog"]["sha256"] = hashlib.sha256(catalog.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="direct provider"):
        validate_generation_receipt(before["entries"][0], payload, model_identity_evidence=evidence)


@pytest.mark.parametrize(
    "key,value",
    [
        ("id", "gen-other"),
        ("model", "other-model"),
        ("provider_name", "other-provider"),
        ("native_tokens_prompt", 101),
        ("native_tokens_completion", None),
        ("native_tokens_reasoning", 1),
        ("native_tokens_cached", 31),
        ("total_cost", True),
        ("total_cost", -0.01),
        ("total_cost", 0.11),
        ("is_byok", None),
        ("is_byok", True),
    ],
)
def test_conflicting_or_missing_native_evidence_cannot_settle(tmp_path, key, value):
    _, before = historical(tmp_path)
    payload = native("gen-fixture-0")["data"]
    payload[key] = value
    with pytest.raises(ValueError):
        validate_generation_receipt(before["entries"][0], payload)


def test_campaign_process_lock_prevents_concurrent_reconciliation(tmp_path):
    campaign, _ = historical(tmp_path)

    async def forbidden(generation_id):
        raise AssertionError("lookup while locked")

    with (campaign / ".campaign.lock").open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ValueError, match="canonical campaign lock"):
            invoke(campaign, tmp_path / "audit", forbidden)


def test_recovered_billing_mode_is_bound_to_observed_same_binding_receipts(tmp_path):
    _, before = historical(tmp_path)
    witness = before["entries"][0]
    witness.update(status="completed", charged=0.01)
    witness["usage"].update(is_byok=False, response_identity_verified=True)
    evidence = billing_mode_evidence(before["entries"], 1)
    assert evidence["dispatch_id"] == 0 and evidence["is_byok"] is False
    payload = native("gen-fixture-1")["data"]
    payload["is_byok"] = True
    with pytest.raises(ValueError, match="preserved same-binding"):
        validate_generation_receipt(before["entries"][1], payload, evidence)


def test_scope_limits_are_monotonic_concurrent_and_cumulative():
    limits = ResourceConfig(compatibility_budget=3, currency="USD")
    ledger = ExpenditureLedger(limits)
    ledger.stage = "compatibility"
    for _ in range(18):
        entry = ledger.reserve(0.01, kind="initial", model="fixture", metadata={})
        ledger.settle(entry, {"total_cost": 0.001}, status="completed", elapsed=0)
    ledger.register_scope("recheck", bindings={"plan": "frozen"})
    ledger.activate_scope("recheck")
    persisted = []
    ledger.on_change = lambda: persisted.append(ledger.state_dict())

    def reserve(_):
        try:
            return ledger.reserve(0.04, kind="initial", model="fixture", metadata={})
        except BudgetExhausted:
            return None

    with ThreadPoolExecutor(max_workers=12) as executor:
        results = list(executor.map(reserve, range(20)))
    assert sum(value is not None for value in results) == 6
    assert ledger.scope_summary("recheck")["occupied"] == pytest.approx(0.24)
    assert ledger.stage_summary("compatibility")["attempts"] == 24
    assert len(persisted) == 6 and persisted[-1]["entries"][-1]["scope_id"] == "recheck"
    fresh = ExpenditureLedger(limits)
    fresh.load_state_dict(ledger.state_dict())
    fresh.activate_scope(None)
    with pytest.raises(BudgetExhausted, match="ceiling"):
        fresh.reserve(0.001, kind="initial", model="fixture", metadata={})
    with pytest.raises(ValueError, match="cannot change"):
        fresh.register_scope("recheck", budget=0.24)


def test_scope_money_is_enforced_before_six_attempts():
    ledger = ExpenditureLedger(ResourceConfig(compatibility_budget=3, currency="USD"))
    ledger.stage = "compatibility"
    ledger.register_scope("pass")
    ledger.activate_scope("pass")
    ledger.reserve(0.2, kind="initial", model="fixture", metadata={})
    with pytest.raises(BudgetExhausted, match="scope"):
        ledger.reserve(0.06, kind="initial", model="fixture", metadata={})
    assert len(ledger.entries) == 1


def test_failed_restore_is_atomic_and_old_checkpoint_cannot_disable_scope():
    ledger = ExpenditureLedger(ResourceConfig(compatibility_budget=3, currency="USD"))
    ledger.stage = "compatibility"
    ledger.register_scope("pass")
    old = ledger.state_dict()
    ledger.activate_scope("pass")
    ledger.load_state_dict(old)
    assert ledger.active_scope == "pass"
    before = ledger.state_dict()
    malformed = copy.deepcopy(before)
    malformed["stage"] = "pilot"
    malformed["scopes"]["pass"]["budget"] = 999
    with pytest.raises(ValueError):
        ledger.load_state_dict(malformed)
    assert ledger.state_dict() == before


def test_reconciliation_release_does_not_clear_a_new_later_halt(tmp_path):
    campaign, _ = historical(tmp_path)

    async def lookup(generation_id):
        return native(generation_id)

    invoke(campaign, tmp_path / "audit", lookup)
    ledger = read_ledger(campaign)
    old = ledger.state_dict()
    ledger.halt("new unrelated accounting failure")
    halted = copy.deepcopy(ledger.halted)
    ledger.load_state_dict(old)
    assert ledger.halted == halted


def test_documented_metadata_helper_uses_one_get_no_redirect_or_error_secret():
    calls = []

    def handle(request):
        calls.append(request)
        assert request.method == "GET" and request.url.params["id"] == "gen-fixture-0"
        assert request.extensions["timeout"]["read"] == 2
        return httpx.Response(302, headers={"location": "https://not-followed.invalid"})

    result = asyncio.run(
        lookup_generation_metadata("gen-fixture-0", "fixture-not-a-secret", transport=httpx.MockTransport(handle))
    )
    assert result == {"http_status": 302} and len(calls) == 1

    def failure(request):
        raise httpx.ConnectError("secret must never be copied", request=request)

    result = asyncio.run(
        lookup_generation_metadata("gen-fixture-0", "fixture-not-a-secret", transport=httpx.MockTransport(failure))
    )
    assert result == {"error_type": "ConnectError"}
