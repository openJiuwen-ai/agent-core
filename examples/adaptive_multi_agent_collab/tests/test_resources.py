"""Final-request limits, physical accounting and real TextGrad admission offline."""

from __future__ import annotations

import asyncio
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest

from examples.adaptive_multi_agent_collab.providers.adapter import GenerationRequest, OpenJiuwenAgent
from examples.adaptive_multi_agent_collab.config import CostConfig, ResourceConfig
from examples.adaptive_multi_agent_collab.workflows.prompts import compose_backend_input, protocol_instructions
from examples.adaptive_multi_agent_collab.workflows.repair import REPAIR_DIAGNOSTICS, render_repair, repair_variants
from examples.adaptive_multi_agent_collab.providers.resources import (
    AccountingFailure,
    BackendResourceProfile,
    BudgetExhausted,
    ExpenditureLedger,
    ResourceController,
    ResourceFailure,
    TransientFailure,
)
from examples.adaptive_multi_agent_collab.workflows.runtime import normalized_cost
from examples.adaptive_multi_agent_collab.schemas import UsageRecord


class Counter:
    """Explicit test backend tokenizer, counts the complete supplied chat blocks."""

    def count_input(self, messages, schema=None):
        assert schema is None
        return sum(len(item["content"].split()) + 2 for item in messages) + 1

    def count_output(self, text):
        return len(text.split())


def controller(*, limits=None, ledger=None):
    limits = limits or ResourceConfig(pilot_budget=100, development_budget=100, test_budget=100, currency="TEST")
    profile = BackendResourceProfile(
        "fixture",
        "fixture-revision",
        "transformers",
        20000,
        4096,
        0.001,
        0.002,
        "TEST",
        "tariff-fixture",
        reference_input_price=0.001,
        reference_output_price=0.002,
        reference_pricing_date="fixture",
    )
    ledger = ledger or ExpenditureLedger(limits)
    return ResourceController(profile, Counter(), ledger, limits=limits)


class Model:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    async def invoke(self, **kwargs):
        self.calls.append(kwargs)
        response = next(self.responses)
        if isinstance(response, BaseException):
            raise response
        return response


def response(content='{"answer":"yes","explanation":""}', **kwargs):
    return SimpleNamespace(content=content, **kwargs)


def run_call(ctl, model, **overrides):
    settings = dict(
        messages=[{"role": "system", "content": "fixed rules"}, {"role": "user", "content": "query data"}],
        kind="initial",
        deadline=time.monotonic() + 10,
        settings={"seed": 17},
    )
    settings.update(overrides)
    return asyncio.run(ctl.invoke(model, **settings))


def test_complete_composed_request_and_output_cap_reach_public_model_boundary():
    ctl = controller()
    model = Model([response()])
    agent = OpenJiuwenAgent(model, resources=ctl, instructions="agent specific", supports_seed=True)
    result = asyncio.run(agent.generate(GenerationRequest("initial", "question", 0, {"generation_seed": 18})))
    actual = model.calls[0]
    assert [item["role"] for item in actual["messages"]] == ["system", "user"]
    assert "agent specific" in actual["messages"][0]["content"]
    assert actual["messages"][0]["content"].count("Fixed protocol:") == 1
    assert actual["seed"] == 18 and actual["max_tokens"] == 256
    assert result.data == {"answer": "yes", "explanation": ""}
    entry = ctl.ledger.entries[0]
    assert entry["category"] == "initialization"
    assert entry["input_count"] == Counter().count_input(actual["messages"])
    assert entry["usage"]["accounting_methods"]["total_cost"] == "derived"
    assert entry["charged"] is not None


def test_cache_write_premium_reserved_and_native_paid_cost_kept_separate():
    ctl = controller()
    ctl.profile = replace(ctl.profile, cache_write_price=0.003)
    result = run_call(
        ctl,
        Model(
            [
                response(
                    usage_metadata={
                        "input_tokens": 20,
                        "output_tokens": 5,
                        "cache_write_tokens": 10,
                        "total_cost": 0.042,
                    }
                )
            ]
        ),
    )
    entry = ctl.ledger.entries[0]
    assert entry["reserved"] == pytest.approx(entry["reserved_input_tokens"] * 0.003 + 256 * 0.002)
    assert result.metadata["reference_cost"] == pytest.approx(0.03)
    assert entry["charged"] == pytest.approx(0.042)
    assert result.metadata["cache_write_tokens"] == 10


def test_cache_write_without_ttl_specific_billing_keeps_paid_charge_unresolved():
    ctl = controller()
    ctl.profile = replace(ctl.profile, cache_write_price=0.003)
    result = run_call(
        ctl,
        Model([response(usage_metadata={"input_tokens": 20, "output_tokens": 5, "cache_write_tokens": 10})]),
    )
    assert result.metadata["reference_cost"] == pytest.approx(0.03)
    assert result.metadata.get("total_cost") is None
    assert ctl.ledger.entries[0]["charged"] is None


def test_oversized_fixed_system_input_rejected_before_dispatch_without_truncation():
    ctl = controller()
    model = Model([])
    agent = OpenJiuwenAgent(model, resources=ctl, instructions="instruction " * 1024)
    with pytest.raises(ResourceFailure, match="input-token-limit") as error:
        asyncio.run(agent.generate(GenerationRequest("initial", "unchanged query", 0)))
    assert model.calls == [] and ctl.ledger.entries == []
    assert error.value.usage_record == UsageRecord()


def test_worst_repair_envelope_is_admitted_before_the_first_generation():
    variants = tuple(
        compose_backend_input("", protocol_instructions("initial", {}), body) for body in repair_variants("query")
    )
    counts = [Counter().count_input(messages) for messages in variants]
    assert counts[0] < max(counts)
    limits = ResourceConfig(initial_input=max(counts) - 1, pilot_budget=100, development_budget=100, currency="TEST")
    ctl = controller(limits=limits)
    model = Model([response()])
    agent = OpenJiuwenAgent(model, resources=ctl)
    with pytest.raises(ResourceFailure, match="repair-envelope: input-token-limit") as error:
        asyncio.run(agent.generate(GenerationRequest("initial", "query", 0)))
    assert model.calls == [] and ctl.ledger.entries == []
    assert error.value.usage_record == UsageRecord()


@pytest.mark.parametrize(
    "field,value,error", [("context_limit", 100, "context-limit"), ("max_output_tokens", 100, "output-token-cap")]
)
def test_backend_supported_bounds_are_enforced(field, value, error):
    ctl = controller()
    ctl.profile = replace(ctl.profile, **{field: value})
    model = Model([])
    with pytest.raises(ResourceFailure, match=error):
        run_call(ctl, model)
    assert not model.calls


def test_only_documented_safe_transport_retry_reuses_identical_body_and_seed():
    ctl = controller()
    model = Model(
        [
            TransientFailure(
                "pre-dispatch overload", documentation="backend guarantee", generation_not_started=True, retry_after=0
            ),
            response(),
        ]
    )
    result = run_call(ctl, model)
    assert len(result.attempts) == len(model.calls) == len(ctl.ledger.entries) == 2
    assert model.calls[0]["messages"] == model.calls[1]["messages"]
    assert model.calls[0]["seed"] == model.calls[1]["seed"] == 17
    assert ctl.ledger.entries[0]["charged"] == 0
    assert ctl.ledger.entries[1]["charged"] > 0


def test_ambiguous_timeout_is_not_retried_and_keeps_maximum_charge_reserved():
    ctl = controller()
    model = Model([TimeoutError("response unknown")])
    with pytest.raises(ResourceFailure) as caught:
        run_call(ctl, model)
    assert len(model.calls) == 1
    assert caught.value.usage_record.input_tokens is None
    assert caught.value.usage_record.output_tokens is None
    assert caught.value.usage_record.monetary_cost is None
    assert ctl.ledger.entries[0]["charged"] is None
    assert ctl.ledger.entries[0]["reserved"] > 0
    with pytest.raises(AccountingFailure):
        normalized_cost(caught.value.usage_record, CostConfig())
    assert normalized_cost(
        caught.value.usage_record, CostConfig(input_token_weight=0, output_token_weight=0, model_call_weight=1)
    ) == pytest.approx(0.001)


def test_finite_monetary_budget_blocks_dispatch_and_is_not_a_score():
    ctl = controller(limits=ResourceConfig(development_budget=0.01, currency="TEST"))
    model = Model([])
    with pytest.raises(BudgetExhausted):
        run_call(ctl, model)
    assert not model.calls and not ctl.ledger.entries
    for value in (float("inf"), float("nan"), -1):
        with pytest.raises(ValueError):
            ResourceConfig(development_budget=value)


def test_usage_subcategories_are_inclusive_and_do_not_double_count():
    ctl = controller()
    ctl.profile = replace(ctl.profile, cached_input_price=0.0005, reasoning_price=0.003)
    model = Model(
        [response(usage_metadata={"input_tokens": 10, "output_tokens": 8, "cache_tokens": 4, "reasoning_tokens": 3})]
    )
    result = run_call(ctl, model)
    assert result.metadata["total_cost"] == pytest.approx(6 * 0.001 + 4 * 0.0005 + 5 * 0.002 + 3 * 0.003)


def test_ledger_restore_retains_later_physical_expenditure():
    ctl = controller()
    run_call(ctl, Model([response()]))
    saved = ctl.ledger.state_dict()
    run_call(ctl, Model([response()]))
    ctl.ledger.load_state_dict(saved)
    assert len(ctl.ledger.entries) == 2
    fresh = ExpenditureLedger(ctl.limits)
    fresh.load_state_dict(ctl.ledger.state_dict())
    assert fresh.state_dict() == ctl.ledger.state_dict()


def test_global_physical_concurrency_is_shared_across_controllers():
    first = controller()
    second = controller(ledger=first.ledger)
    active = peak = 0

    class SlowModel:
        async def invoke(self, **kwargs):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.005)
            active -= 1
            return response()

    async def exercise():
        await asyncio.gather(
            *(
                ctl.invoke(
                    SlowModel(),
                    messages=[{"role": "user", "content": "q"}],
                    kind="initial",
                    deadline=time.monotonic() + 10,
                    settings={},
                )
                for ctl in [first, second] * 4
            )
        )

    asyncio.run(exercise())
    assert peak == 3 and len(first.ledger.entries) == 8


def test_real_textgrad_bounded_feedback_and_no_fit_make_no_alternative_proposal():
    from examples.adaptive_multi_agent_collab.workflows.prompts import seed_prompt_collection
    from examples.adaptive_multi_agent_collab.learning.textgrad_optimizer import TextGradPromptProposer
    from .test_textgrad_optimizer import LocalTextModel, configuration, occurrence

    ctl = controller()
    spec = seed_prompt_collection().components["role:S_T"]
    candidate = spec.template + "\nCheck the premise."
    backward = LocalTextModel()
    optimizer = LocalTextModel(f"<IMPROVED_VARIABLE>{candidate}</IMPROVED_VARIABLE>")
    proposer = TextGradPromptProposer(
        backward, optimizer, config=configuration(), resources={"backward": ctl, "optimizer": ctl}
    )
    evidence = [occurrence(example_id=f"b{index}") for index in range(4)]
    result = asyncio.run(proposer.propose(spec, evidence, iteration_id="bounded"))
    assert result.error is None and result.template == candidate
    assert len(result.metadata["selected_occurrence_indices"]) <= 2
    assert len(result.metadata["omitted_occurrence_indices"]) >= 2
    assert len(optimizer.calls) == 1
    assert all(call["max_tokens"] == 1024 for call in backward.calls + optimizer.calls)

    small = controller(limits=replace(ctl.limits, textgrad_input=10))
    other = TextGradPromptProposer(
        backward, optimizer, config=configuration(), resources={"backward": small, "optimizer": small}
    )
    before = len(backward.calls) + len(optimizer.calls)
    rejected = asyncio.run(other.propose(spec, evidence, iteration_id="does-not-fit"))
    assert "resource-failed" in rejected.error and rejected.template is None
    assert rejected.attempts == ()
    assert len(backward.calls) + len(optimizer.calls) == before


def test_reservations_are_durable_before_dispatch_and_completed_ledger_can_extend_pending_prefix():
    ctl = controller()
    snapshots = []
    ctl.ledger.on_change = lambda: snapshots.append(ctl.ledger.state_dict())

    class CheckedModel:
        async def invoke(self, **kwargs):
            assert snapshots[0]["entries"][0]["status"] == "reserved"
            assert snapshots[-1]["entries"][0]["status"] == "dispatched"
            return response()

    run_call(ctl, CheckedModel())
    assert snapshots[-1]["entries"][0]["status"] == "completed"
    restarted = ExpenditureLedger(ctl.limits)
    restarted.load_state_dict(snapshots[0])
    run_call(ctl, Model([response()]))
    restarted.load_state_dict(ctl.ledger.state_dict())
    assert len(restarted.entries) == 2
    assert restarted.entries[0]["charged"] is not None
    restarted.load_state_dict(snapshots[0])
    assert len(restarted.entries) == 2 and restarted.entries[0]["charged"] is not None


def test_truncated_valid_json_requires_repair_and_records_both_costs():
    ctl = controller()
    model = Model([response(finish_reason="length"), response()])
    agent = OpenJiuwenAgent(model, resources=ctl)
    result = asyncio.run(agent.generate(GenerationRequest("initial", "query", 0)))
    assert result.data == {"answer": "yes", "explanation": ""}
    assert len(model.calls) == len(ctl.ledger.entries) == 2
    assert "truncated" in result.parse_errors[0]
    assert all(entry["charged"] is not None for entry in ctl.ledger.entries)


def test_accounting_error_after_dispatch_keeps_known_usage_and_reserved_entry():
    ctl = controller()
    ctl.profile = replace(ctl.profile, cached_input_price=0.0005)
    model = Model([response(usage_metadata={"input_tokens": 10, "output_tokens": 3, "cache_tokens": 11})])
    with pytest.raises(AccountingFailure) as caught:
        run_call(ctl, model)
    assert caught.value.usage_record.model_calls == 1
    assert ctl.ledger.entries[0]["charged"] is None
    assert ctl.ledger.entries[0]["status"] == "accounting-failed"


def test_late_backend_reply_does_not_change_timed_out_record():
    ctl = controller()

    class LateModel:
        async def invoke(self, **kwargs):
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                await asyncio.sleep(0.06)
                return response()

    async def exercise():
        with pytest.raises(ResourceFailure):
            await ctl.invoke(
                LateModel(),
                messages=[{"role": "user", "content": "q"}],
                kind="initial",
                deadline=time.monotonic() + 0.005,
                settings={},
            )
        assert ctl.ledger.entries[0]["status"] == "failed"
        before = ctl.ledger.state_dict()
        await asyncio.sleep(0.03)
        assert ctl.ledger.state_dict() == before

    asyncio.run(exercise())


def test_reference_cost_ignores_discounts_paid_charge_and_billing_fees():
    ctl = controller()
    ctl.profile = replace(
        ctl.profile,
        reference_input_price=0.4 / 1e6,
        reference_output_price=1.6 / 1e6,
        reference_pricing_date="2026-09-18",
        cached_input_price=0.0005,
        request_fee=0.0001,
    )
    first = run_call(
        ctl,
        Model(
            [
                response(
                    usage_metadata={
                        "input_tokens": 100,
                        "output_tokens": 20,
                        "cache_tokens": 80,
                        "total_cost": 0.00001,
                    }
                )
            ]
        ),
    )
    second = run_call(
        ctl,
        Model(
            [
                response(
                    usage_metadata={
                        "input_tokens": 100,
                        "output_tokens": 20,
                        "cache_tokens": 0,
                        "total_cost": 0.00005,
                    }
                )
            ]
        ),
    )
    expected = (100 * 0.4 + 20 * 1.6) / 1e6
    assert first.metadata["reference_cost"] == second.metadata["reference_cost"] == pytest.approx(expected)
    assert [item["charged"] for item in ctl.ledger.entries] == [0.00001, 0.00005]
    from examples.adaptive_multi_agent_collab.providers.adapter import _attempt_usage

    usage = _attempt_usage(dict(first.metadata), 0)
    assert usage.monetary_cost == pytest.approx(expected)
    assert usage.paid_monetary_cost == 0.00001
    assert normalized_cost(
        usage,
        CostConfig(
            model_call_weight=0,
            input_token_weight=0,
            output_token_weight=0,
            monetary_weight=1,
            normalization=0.01,
        ),
    ) == pytest.approx(expected / 0.01)


def test_unknown_paid_charge_stays_reserved_when_reference_cost_is_available():
    ctl = controller()
    ctl.profile = replace(ctl.profile, cached_input_price=0.0005)
    result = run_call(ctl, Model([response(usage_metadata={"input_tokens": 10, "output_tokens": 2})]))
    assert result.metadata["reference_cost"] == pytest.approx(0.014)
    assert result.metadata.get("total_cost") is None
    assert ctl.ledger.entries[0]["charged"] is None
    assert ctl.ledger.entries[0]["reserved"] > 0


def test_estimated_tokens_do_not_masquerade_as_measured_paid_charge():
    ctl = controller()
    ctl.profile = replace(ctl.profile, counting_method="estimated")
    result = run_call(ctl, Model([response()]))
    assert result.metadata["reference_cost"] > 0
    assert result.metadata["accounting_methods"]["reference_cost"] == "estimated"
    assert result.metadata.get("total_cost") is None and result.metadata["estimated_paid_cost"] > 0
    assert ctl.ledger.entries[0]["reserved_input_tokens"] == ctl.limits.initial_input
    assert ctl.ledger.entries[0]["charged"] is None


def test_concurrent_reservations_are_atomic_and_stage_limits_do_not_roll_over():
    from concurrent.futures import ThreadPoolExecutor

    limits = ResourceConfig(pilot_budget=5, development_budget=80, test_budget=20, currency="USD")
    ledger = ExpenditureLedger(limits)
    ledger.stage = "pilot"

    def reserve(_):
        try:
            return ledger.reserve(1, kind="initial", model="fixture", metadata={})["dispatch_id"]
        except BudgetExhausted:
            return None

    with ThreadPoolExecutor(max_workers=12) as pool:
        accepted = [item for item in pool.map(reserve, range(20)) if item is not None]
    assert sorted(accepted) == list(range(5))
    saved = ledger.state_dict()
    restored = ExpenditureLedger(limits)
    restored.load_state_dict(saved)
    with pytest.raises(BudgetExhausted):
        restored.reserve(0.000001, kind="initial", model="fixture", metadata={})
    restored.stage = "test"
    restored.reserve(20, kind="initial", model="fixture", metadata={})
    with pytest.raises(BudgetExhausted):
        restored.reserve(0.000001, kind="initial", model="fixture", metadata={})
    restored.load_state_dict(saved)
    assert len(restored.entries) == 6
    assert restored.limits.development_budget == 80


def test_conservative_counter_requires_approval_and_records_its_limitations():
    from examples.adaptive_multi_agent_collab.providers.resources import LocalTokenCounter

    with pytest.raises(ValueError, match="approval"):
        replace(controller().profile, tokenizer_kind="conservative", counting_method="estimated")
    profile = replace(
        controller().profile,
        tokenizer_kind="conservative",
        tokenizer_name="approved-native-envelope",
        tokenizer_revision="fixture-method",
        counting_method="estimated",
        conservative_tokens_per_byte=1,
        chat_tokens_per_message=8,
        chat_priming_tokens=8,
        estimator_approval="fixture-approval",
        estimator_evidence="fixture-native-count-comparison",
    )
    counter = LocalTokenCounter(profile)
    assert counter.count_output("é") == 2
    assert counter.count_input([{"role": "user", "content": "é"}]) == 22


def test_provisional_counter_counts_all_fields_and_explicit_uncertainty():
    from examples.adaptive_multi_agent_collab.providers.resources import LocalTokenCounter

    profile = replace(
        controller().profile,
        tokenizer_kind="conservative",
        counting_method="estimated",
        conservative_tokens_per_byte=1,
        chat_tokens_per_message=8,
        chat_priming_tokens=8,
        estimator_approval="explicit provisional user authorization",
        estimator_evidence=None,
        counting_safety_factor=1.5,
        counting_safety_tokens=32,
    )
    counter = LocalTokenCounter(profile)
    messages = [{"role": "system", "content": "fixed"}, {"role": "user", "content": '修复 {"a": "é"}'}]
    raw = 8 + sum(8 + sum(len(value.encode("utf-8")) for value in item.values()) for item in messages)
    import math

    assert counter.count_input(messages) == math.ceil(raw * 1.5) + 32
    with pytest.raises(ValueError, match="native schema"):
        counter.count_input(messages, schema={"type": "object"})


def test_compatibility_budget_and_attempt_limit_are_independent_durable_and_atomic():
    from concurrent.futures import ThreadPoolExecutor

    limits = ResourceConfig(compatibility_budget=3, pilot_budget=5, currency="USD")
    ledger = ExpenditureLedger(limits)
    ledger.stage = "compatibility"

    def reserve(_):
        try:
            return ledger.reserve(0.01, kind="initial", model="fixture", metadata={})
        except BudgetExhausted:
            return None

    with ThreadPoolExecutor(max_workers=12) as pool:
        accepted = [item for item in pool.map(reserve, range(36)) if item is not None]
    assert len(accepted) == 24
    assert ledger.stage_summary("compatibility")["unresolved"] == pytest.approx(0.24)
    assert ledger.stage_summary("pilot")["occupied"] == 0
    restored = ExpenditureLedger(limits)
    restored.load_state_dict(ledger.state_dict())
    with pytest.raises(BudgetExhausted, match="attempt"):
        restored.reserve(0, kind="role", model="fixture", metadata={})
    restored.stage = "pilot"
    restored.reserve(5, kind="initial", model="fixture", metadata={})
    assert restored.stage_summary("pilot")["occupied"] == 5
    assert restored.stage_summary("compatibility")["occupied"] == pytest.approx(0.24)
    with pytest.raises(ValueError):
        ResourceConfig(compatibility_budget=3.01)
    with pytest.raises(ValueError):
        ResourceConfig(compatibility_max_attempts=49)


def test_compatibility_charge_ceiling_accounts_for_spent_and_unresolved():
    ledger = ExpenditureLedger(ResourceConfig(compatibility_budget=3, pilot_budget=5, currency="USD"))
    ledger.stage = "compatibility"
    first = ledger.reserve(2, kind="initial", model="fixture", metadata={})
    ledger.settle(first, {"total_cost": 1.25}, status="completed", elapsed=1)
    ledger.reserve(1.75, kind="role", model="fixture", metadata={})
    with pytest.raises(BudgetExhausted, match="monetary"):
        ledger.reserve(0.000001, kind="role", model="fixture", metadata={})
    assert ledger.stage_summary("compatibility")["occupied"] == 3
    assert ledger.stage_summary("pilot")["occupied"] == 0


def test_accounting_halt_survives_rollback_resume_and_stage_switch():
    ledger = ExpenditureLedger(ResourceConfig(compatibility_budget=3, pilot_budget=5, currency="USD"))
    ledger.stage = "compatibility"
    before = ledger.state_dict()
    entry = ledger.reserve(0.1, kind="initial", model="fixture", metadata={})
    ledger.settle(entry, {}, status="failed", elapsed=1)
    ledger.load_state_dict(before)
    assert ledger.halted is not None and len(ledger.entries) == 1
    resumed = ExpenditureLedger(ledger.limits)
    resumed.load_state_dict(ledger.state_dict())
    resumed.stage = "pilot"
    with pytest.raises(AccountingFailure, match="halted"):
        resumed.reserve(0.01, kind="initial", model="fixture", metadata={})
    assert resumed.stage_summary("pilot")["occupied"] == 0


def test_native_counter_bound_violation_stops_subsequent_dispatch():
    ctl = controller()
    ctl.profile = replace(ctl.profile, counting_method="estimated", require_native_accounting=True)
    model = Model([response(usage_metadata={"input_tokens": 100, "output_tokens": 2, "total_cost": 0.1})])
    with pytest.raises(AccountingFailure, match="conservative estimate"):
        run_call(ctl, model)
    assert ctl.ledger.entries[0]["charged"] == 0.1
    with pytest.raises(AccountingFailure, match="halted"):
        run_call(ctl, model)
    assert len(model.calls) == 1


def test_native_production_usage_never_falls_back_to_local_estimates():
    ctl = controller()
    ctl.profile = replace(ctl.profile, require_native_accounting=True)
    with pytest.raises(AccountingFailure, match="native usage"):
        run_call(ctl, Model([response(usage_metadata={"total_cost": 0.01})]))
    record = ctl.ledger.entries[0]
    assert record["charged"] == 0.01
    assert record["usage"].get("input_tokens") is None
    assert record["usage"].get("output_tokens") is None
    assert ctl.ledger.halted is not None


def test_reservation_uncertainty_is_not_an_actual_billing_fee():
    ctl = controller()
    ctl.profile = replace(ctl.profile, reservation_safety_factor=1.5)
    result = run_call(ctl, Model([response(usage_metadata={"input_tokens": 9, "output_tokens": 2})]))
    entry = ctl.ledger.entries[0]
    assert entry["reserved"] == pytest.approx((entry["reserved_input_tokens"] * 0.001 + 256 * 0.002) * 1.5)
    assert result.metadata["total_cost"] == pytest.approx(0.013)


def test_queued_dispatch_cannot_overtake_fatal_receipt_accounting():
    ctl = controller()
    ctl.profile = replace(ctl.profile, require_native_accounting=True)
    ctl.ledger.semaphore = asyncio.Semaphore(1)
    calls = []

    class MissingNativeUsage:
        async def invoke(self, **kwargs):
            calls.append(kwargs)
            await asyncio.sleep(0)
            return response(usage_metadata={"total_cost": 0.01})

    async def exercise():
        return await asyncio.gather(
            *(
                ctl.invoke(
                    MissingNativeUsage(),
                    messages=[{"role": "user", "content": "synthetic"}],
                    kind="initial",
                    deadline=time.monotonic() + 5,
                    settings={},
                )
                for _ in range(3)
            ),
            return_exceptions=True,
        )

    results = asyncio.run(exercise())
    assert all(isinstance(item, AccountingFailure) for item in results)
    assert len(calls) == 1
    assert [entry["charged"] for entry in ctl.ledger.entries] == [0.01, 0.0, 0.0]
    assert ctl.ledger.halted is not None


def test_invalid_native_subcategory_keeps_independently_reported_paid_debit():
    ctl = controller()
    model = Model(
        [response(usage_metadata={"input_tokens": 9, "output_tokens": 2, "cache_tokens": 10, "total_cost": 0.01})]
    )
    with pytest.raises(AccountingFailure, match="subcategory"):
        run_call(ctl, model)
    assert ctl.ledger.entries[0]["charged"] == 0.01
    assert ctl.ledger.entries[0]["usage"]["cache_tokens"] == 10
    assert ctl.ledger.halted is not None


def test_deadline_cancellation_retains_observed_native_bill_without_accepting_answer():
    ctl = controller()

    class Partial:
        async def invoke(self, **kwargs):
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError as error:
                setattr(error, "usage", {"input_tokens": 9, "output_tokens": 2, "total_cost": 0.01})
                raise

    with pytest.raises(ResourceFailure, match="deadline") as error:
        run_call(ctl, Partial(), deadline=time.monotonic() + 0.01)
    assert error.value.usage_record.paid_monetary_cost == 0.01
    assert ctl.ledger.entries[0]["charged"] == 0.01
    assert ctl.ledger.entries[0]["status"] == "failed"


def test_repair_envelope_uses_full_compositions_including_empty_not_additive_token_estimates():
    class NonAdditiveCounter(Counter):
        def __init__(self):
            self.inputs = []

        def count_input(self, messages, schema=None):
            self.inputs.append(messages)
            body = messages[-1]["content"]
            # The base itself can be the most expensive tokenization. Neither
            # string length nor independently counted suffix tokens suffice.
            return 90 if "Repair diagnostic" not in body else (75 if "truncated" in body else 80)

    ctl = controller()
    counter = NonAdditiveCounter()
    ctl.counter = counter
    protocol = protocol_instructions("initial", {"admissible_answers": ("yes", "no")})
    variants = tuple(compose_backend_input("fixed agent", protocol, body) for body in repair_variants("complete query"))
    assert ctl.preflight_envelope(variants, "initial") == (90, 256)
    assert counter.inputs == list(variants) and len(counter.inputs) == 5
    assert all(messages[0]["content"].startswith("Agent instructions:\nfixed agent") for messages in counter.inputs)
    assert all(messages[0]["content"].count("Fixed protocol:") == 1 for messages in counter.inputs)
    assert not ctl.ledger.entries


def test_repair_envelope_checks_backend_context_with_output_cap_and_reserve():
    ctl = controller()
    variants = tuple(
        compose_backend_input("", protocol_instructions("initial", {}), body) for body in repair_variants("query")
    )
    counts = [ctl.counter.count_input(messages) for messages in variants]
    ctl.profile = replace(ctl.profile, context_limit=max(counts) + 256 + 64 - 1)
    with pytest.raises(ResourceFailure, match="repair-envelope: backend-context-limit"):
        ctl.preflight_envelope(variants, "initial")
    assert not ctl.ledger.entries


def test_runtime_zero_adapter_repair_override_still_requires_logical_repair_envelope():
    variants = tuple(
        compose_backend_input("", protocol_instructions("initial", {}), body) for body in repair_variants("query")
    )
    cap = max(Counter().count_input(messages) for messages in variants) - 1
    ctl = controller(limits=ResourceConfig(initial_input=cap, development_budget=100, currency="TEST"))
    model = Model([response()])
    agent = OpenJiuwenAgent(model, resources=ctl)
    request = GenerationRequest(
        "initial", "query", 0, {"repair_enabled": True, "repair_base_prompt": "query"}, max_repair_generations=0
    )
    with pytest.raises(ResourceFailure, match="repair-envelope"):
        asyncio.run(agent.generate(request))
    assert not model.calls and not ctl.ledger.entries


def test_actual_repair_is_rechecked_and_prior_physical_usage_remains_chargeable():
    ctl = controller()

    class ChangingCounter(Counter):
        def count_input(self, messages, schema=None):
            count = super().count_input(messages, schema)
            return ctl.limits.initial_input + 1 if ctl.ledger.entries else count

    ctl.counter = ChangingCounter()
    model = Model(
        [response("INVALID_RAW_MARKER", usage_metadata={"input_tokens": 9, "output_tokens": 2, "total_cost": 0.01})]
    )
    agent = OpenJiuwenAgent(model, resources=ctl)
    with pytest.raises(ResourceFailure, match="input-token-limit") as caught:
        asyncio.run(agent.generate(GenerationRequest("initial", "query", 0)))
    assert len(model.calls) == len(ctl.ledger.entries) == 1
    assert caught.value.usage_record.model_calls == 1
    assert caught.value.usage_record.paid_monetary_cost == pytest.approx(0.01)
    assert ctl.ledger.entries[0]["charged"] == pytest.approx(0.01)


def test_bounded_repair_keeps_input_independent_of_failed_output_size_and_records_admission():
    ctl = controller()
    model = Model(
        [
            response("RAW_MARKER " * 1000, usage_metadata={"input_tokens": 9, "output_tokens": 20, "total_cost": 0.01}),
            response(usage_metadata={"input_tokens": 12, "output_tokens": 10, "total_cost": 0.02}),
        ]
    )
    result = asyncio.run(OpenJiuwenAgent(model, resources=ctl).generate(GenerationRequest("initial", "query", 0)))
    assert result.data["answer"] == "yes" and len(model.calls) == 2
    assert model.calls[1]["messages"][-1]["content"] == render_repair("query", REPAIR_DIAGNOSTICS["json"])
    assert "RAW_MARKER" not in str(model.calls)
    assert result.attempt_records[0].raw_output.count("RAW_MARKER") == 1000
    assert result.usage_metadata["admission"]["maximum_input_tokens"] >= max(
        entry["input_count"] for entry in ctl.ledger.entries
    )
    assert sum(entry["charged"] for entry in ctl.ledger.entries) == pytest.approx(0.03)


def test_standalone_context_cannot_disable_headroom_while_adapter_repairs_remain_enabled():
    variants = tuple(
        compose_backend_input("", protocol_instructions("initial", {}), body) for body in repair_variants("query")
    )
    cap = max(Counter().count_input(messages) for messages in variants) - 1
    ctl = controller(limits=ResourceConfig(initial_input=cap, development_budget=100, currency="TEST"))
    model = Model([response()])
    request = GenerationRequest("initial", "query", 0, {"repair_enabled": False})
    with pytest.raises(ResourceFailure, match="repair-envelope"):
        asyncio.run(OpenJiuwenAgent(model, resources=ctl, parse_retries=1).generate(request))
    assert not model.calls and not ctl.ledger.entries


@pytest.mark.parametrize("coordinate,value", [("input_tokens", 1025), ("output_tokens", 257)])
def test_observed_provider_bound_breach_is_fatal_after_retaining_known_charge(coordinate, value):
    ctl = controller()
    usage = {"input_tokens": 9, "output_tokens": 2, "total_cost": 0.01, coordinate: value}
    model = Model([response(usage_metadata=usage)])
    with pytest.raises(AccountingFailure, match="provider .*count exceeded"):
        run_call(ctl, model)
    assert ctl.ledger.halted is not None
    assert ctl.ledger.entries[0]["charged"] == pytest.approx(0.01)
    with pytest.raises(AccountingFailure, match="halted"):
        run_call(ctl, model)
    assert len(model.calls) == len(ctl.ledger.entries) == 1


def test_strict_provider_failure_with_known_paid_usage_propagates_the_fatal_ledger_halt():
    ctl = controller()
    ctl.profile = replace(ctl.profile, require_native_accounting=True)
    failure = RuntimeError("recorded gateway identity failure")
    setattr(failure, "usage", response(usage_metadata={"input_tokens": 9, "output_tokens": 2, "total_cost": 0.01}))
    model = Model([failure])
    with pytest.raises(AccountingFailure, match="gateway identity failure") as caught:
        run_call(ctl, model)
    assert caught.value.__cause__ is failure
    assert caught.value.usage_record.paid_monetary_cost == pytest.approx(0.01)
    assert ctl.ledger.entries[0]["charged"] == pytest.approx(0.01) and ctl.ledger.halted is not None
    assert ctl.ledger.entries[0]["usage"]["transport_attempt"] == 0
    assert caught.value.usage_record.raw_metadata["physical_attempts"][0]["physical_dispatch_id"] == 0
