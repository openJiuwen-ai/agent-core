"""Bounded candidate policy with disposable ledgers and mocked public transports."""

from __future__ import annotations

import asyncio
import copy
import json
import time
from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest

from examples.adaptive_multi_agent_collab.providers.adapter import GenerationRequest, OpenJiuwenAgent
from examples.adaptive_multi_agent_collab.config import Config, ResourceConfig
from examples.adaptive_multi_agent_collab.infrastructure.failure_policy import (
    FailureKind,
    FailurePolicyConfig,
    ProviderFailure,
    classify_failure,
    logical_summary,
)
from examples.adaptive_multi_agent_collab.providers.gateway import build_openrouter_model
from examples.adaptive_multi_agent_collab.providers.resources import (
    AccountingFailure,
    BackendResourceProfile,
    ExpenditureLedger,
    PolicyStopped,
    ResourceController,
    ResourceFailure,
    TransientFailure,
    UncertaintyCapacity,
)
from examples.adaptive_multi_agent_collab.tests.test_gateway import binding, frame, sse


class Counter:
    def count_input(self, messages, schema=None):
        del messages, schema
        return 100

    def count_output(self, text):
        return len(text.split())


class Model:
    def __init__(self, values):
        self.values = iter(values)
        self.calls = []

    async def invoke(self, **kwargs):
        self.calls.append(kwargs)
        result = next(self.values)
        if isinstance(result, BaseException):
            raise result
        return result


def controller(*, policy=None, activate=True):
    policy = policy or FailurePolicyConfig(enabled=True)
    limits = ResourceConfig(
        compatibility_budget=3, pilot_budget=5, development_budget=80, test_budget=20, currency="USD"
    )
    ledger = ExpenditureLedger(limits)
    ledger.stage = "pilot"
    if activate:
        ledger.activate_failure_policy(
            policy,
            authorization={"approved": True, "source": "offline fixture"},
            bindings={"condition": "fixture"},
            execution_guard=lambda: True,
        )
    profile = BackendResourceProfile(
        "fixture",
        "fixture",
        "transformers",
        20000,
        4096,
        0.000001,
        0.000002,
        "USD",
        "fixture",
        reference_input_price=0.000001,
        reference_output_price=0.000002,
        reference_pricing_date="fixture",
        billing_mode="byok",
        require_native_accounting=True,
    )
    return ResourceController(profile, Counter(), ledger, limits=limits, failure_policy=policy)


def completed(*, paid=0.00002, input_tokens=10, content='{"answer":"yes","explanation":""}', **changes):
    usage = dict(
        input_tokens=input_tokens,
        output_tokens=5,
        cache_tokens=0,
        reasoning_tokens=0,
        total_cost=paid,
        is_byok=True,
        response_identity_verified=True,
    )
    usage.update(changes)
    return SimpleNamespace(content=content, usage_metadata=usage, finish_reason="stop", parser_content=None)


def provider_failure(*, paid=None):
    error = ProviderFailure("synthetic provider unavailable", FailureKind.PROVIDER)
    if paid is not None:
        error.usage = completed(paid=paid, input_tokens=0, output_tokens=0)
    return error


async def call(ctl, model, **overrides):
    settings = dict(
        messages=[{"role": "user", "content": "synthetic"}], kind="initial", deadline=time.monotonic() + 2, settings={}
    )
    settings.update(overrides)
    return await ctl.invoke(model, **settings)


def test_default_config_and_ledger_shape_stay_disabled_and_activation_is_required():
    assert "failure_policy" not in Config().to_dict()
    cfg = replace(Config(), failure_policy=FailurePolicyConfig(enabled=True))
    assert Config.from_dict(cfg.to_dict()) == cfg
    ctl = controller(activate=False)
    assert "failure_policy" not in ctl.ledger.state_dict()
    with pytest.raises(AccountingFailure, match="explicitly activated"):
        asyncio.run(call(ctl, Model([completed()])))


def test_complete_output_without_bill_retained_independent_from_reference_cost():
    ctl = controller()
    result = asyncio.run(call(ctl, Model([completed(paid=None)])))
    assert result.response.content.startswith('{"answer"')
    assert result.metadata["accounting_complete"] is False
    assert result.metadata["reference_cost"] == pytest.approx(0.00002)
    assert result.metadata["missing_usage"] == ["total_cost"]
    assert ctl.ledger.entries[0]["charged"] is None
    assert ctl.ledger.halted is None and ctl.ledger.dispatch_stop_reason is None
    assert ctl.ledger.uncertainty_summary()["terminal_unresolved_ids"] == [0]


def test_wrong_adapter_policy_rejected_before_logical_registration_or_mutation():
    ctl = controller()
    before = ctl.ledger.state_dict()
    ctl.expected_failure_policy = FailurePolicyConfig(enabled=True, max_unresolved_requests=4)
    agent = OpenJiuwenAgent(Model([completed()]), resources=ctl)
    with pytest.raises(AccountingFailure, match="configured failure policy"):
        asyncio.run(agent.generate(GenerationRequest("initial", "synthetic", 0)))
    assert ctl.ledger.state_dict() == before


def test_policy_stopped_queued_request_is_proven_not_dispatched_and_known_zero():
    ctl = controller()

    async def scenario():
        await ctl.ledger.semaphore.acquire()
        await ctl.ledger.semaphore.acquire()
        await ctl.ledger.semaphore.acquire()
        model = Model([completed()])
        task = asyncio.create_task(call(ctl, model))
        while not ctl.ledger.entries:
            await asyncio.sleep(0)
        ctl.ledger._policy_drain("offline limit decision")
        ctl.ledger.semaphore.release()
        with pytest.raises(PolicyStopped) as caught:
            await task
        assert not model.calls
        receipt = ctl.ledger.entries[0]
        assert receipt["status"] == "not-dispatched" and receipt["charged"] == 0
        assert caught.value.usage_record.paid_monetary_cost == 0
        assert caught.value.usage_record.monetary_cost == 0

    asyncio.run(scenario())


def test_known_bill_missing_native_counts_has_no_hold_and_remains_unknown():
    ctl = controller()
    result = asyncio.run(call(ctl, Model([completed(input_tokens=None)])))
    assert result.metadata["input_tokens"] is None
    assert result.metadata["reference_cost"] is None
    summary = ctl.ledger.uncertainty_summary()
    assert summary["terminal_unresolved_ids"] == [0] and summary["retained_reservations"] == 0
    assert ctl.ledger.entries[0]["charged"] == 0.00002


def test_partial_stream_known_core_usage_missing_native_categories_counts_as_unresolved():
    ctl = controller()
    error = ProviderFailure("partial synthetic stream", FailureKind.TRANSPORT)
    error.usage = completed(reasoning_tokens=None, is_byok=None)
    with pytest.raises(ResourceFailure):
        asyncio.run(call(ctl, Model([error])))
    receipt = ctl.ledger.entries[0]
    assert receipt["charged"] == 0.00002
    assert receipt["usage"]["accounting_complete"] is False
    assert receipt["usage"]["missing_usage"] == ["reasoning_tokens", "is_byok"]
    assert ctl.ledger.uncertainty_summary()["terminal_unresolved_ids"] == [0]
    assert ctl.ledger.uncertainty_summary()["retained_reservations"] == 0


@pytest.mark.parametrize(
    "error,kind",
    [
        (httpx.ReadTimeout("fixture"), FailureKind.TIMEOUT),
        (httpx.ConnectError("fixture"), FailureKind.TRANSPORT),
        (ValueError("fixture"), FailureKind.INTEGRATION),
        (ProviderFailure("fixture", FailureKind.REQUEST), FailureKind.REQUEST),
    ],
)
def test_explicit_classification_is_not_a_retry_heuristic(error, kind):
    assert classify_failure(error) == kind


def test_five_unknown_terminal_requests_drain_and_do_not_authorize_sixth():
    ctl = controller()
    model = Model([provider_failure() for _ in range(6)])
    for index in range(5):
        with pytest.raises(ResourceFailure):
            asyncio.run(call(ctl, model))
        assert len(ctl.ledger.entries) == index + 1
    assert ctl.ledger.dispatch_stop_reason["operational_drain"] is True
    assert ctl.ledger.halted is None
    with pytest.raises(PolicyStopped):
        asyncio.run(call(ctl, model))
    assert len(model.calls) == 5
    assert ctl.ledger.uncertainty_summary()["terminal_unresolved_ids"] == list(range(5))


def test_provider_breaker_counts_logical_terminal_failures_not_safe_retry_attempts():
    ctl = controller()
    retry = TransientFailure(
        "known not started", documentation="offline fixture", generation_not_started=True, retry_after=0
    )
    model = Model([retry, provider_failure(paid=0), *[provider_failure(paid=0) for _ in range(4)]])
    for _ in range(5):
        with pytest.raises(ResourceFailure):
            asyncio.run(call(ctl, model))
    summary = logical_summary(ctl.ledger.failure_policy)
    assert summary["backend_streaks"] == {"fixture": 5}
    assert len(model.calls) == 6 and len(ctl.ledger.entries) == 6
    assert ctl.ledger.uncertainty_summary()["terminal_unresolved_ids"] == []
    assert ctl.ledger.dispatch_stop_reason["operational_drain"]


def test_invalid_schema_is_successful_provider_completion_and_one_logical_request():
    ctl = controller()
    for _ in range(3):
        with pytest.raises(ResourceFailure):
            asyncio.run(call(ctl, Model([provider_failure(paid=0)])))
    agent = OpenJiuwenAgent(Model([completed(content='{"wrong":1}'), completed(content='{"wrong":2}')]), resources=ctl)
    result = asyncio.run(agent.generate(GenerationRequest("initial", "synthetic", 0)))
    assert result.data is None and result.validation_exhausted
    assert logical_summary(ctl.ledger.failure_policy)["backend_streaks"] == {"fixture": 0}
    starts = [item for item in ctl.ledger.failure_policy["events"] if item["event"] == "logical-start"]
    assert len(starts) == 4 and len(ctl.ledger.entries) == 5


def test_backend_streak_follows_registration_order_despite_completion_order():
    ctl = controller()
    ledger = ctl.ledger
    first, second = [ledger.begin_logical_request("same") for _ in range(2)]
    for token, completed_flag in ((first, False), (second, True)):
        entry = ledger.reserve(0.01, kind="role", model="same", metadata={"provider_logical_request_id": token})
        ledger.settle(
            entry,
            {
                "total_cost": 0.001,
                "provider_completed": completed_flag,
                "failure_kind": FailureKind.PROVIDER.value if not completed_flag else None,
            },
            status="completed",
            elapsed=0,
        )
    ledger.finish_logical_request(second)
    assert logical_summary(ledger.failure_policy)["backend_streaks"] == {"same": 0}
    ledger.finish_logical_request(first)
    assert logical_summary(ledger.failure_policy)["backend_streaks"] == {"same": 0}


def test_atomic_uncertainty_slots_and_dollars_include_inflight_and_terminal_holds():
    ledger = controller().ledger
    entries = [ledger.reserve(0.1, kind="role", model="fixture", metadata={}) for _ in range(5)]
    with pytest.raises(UncertaintyCapacity):
        ledger.reserve(0.01, kind="role", model="fixture", metadata={})
    ledger.settle(entries[0], {"total_cost": 0.001, "accounting_complete": True}, status="completed", elapsed=0)
    entry = ledger.reserve(0.1, kind="role", model="fixture", metadata={})
    for pending in [*entries[1:], entry]:
        ledger.settle(pending, {"total_cost": None, "accounting_complete": False}, status="failed", elapsed=0)
    assert ledger.uncertainty_summary()["retained_reservations"] == pytest.approx(0.5)
    assert ledger.dispatch_stop_reason["operational_drain"]


@pytest.mark.parametrize(
    "field,value",
    [("input_tokens", 20000), ("output_tokens", 1000), ("total_cost", 1), ("response_identity_verified", False)],
)
def test_hard_violations_override_any_policy_tolerance(field, value):
    ctl = controller()
    changes = {field: value}
    if field == "total_cost":
        changes = {"paid": value}
    with pytest.raises(AccountingFailure):
        asyncio.run(call(ctl, Model([completed(**changes)])))
    assert ctl.ledger.dispatch_stop_reason["hard_failure"]


def test_restore_requires_lock_rebinding_and_preserves_drain_and_journal():
    ctl = controller()
    result = asyncio.run(call(ctl, Model([completed(paid=None)])))
    assert result.metadata["accounting_complete"] is False
    state = ctl.ledger.state_dict()
    restored = ExpenditureLedger(ctl.limits)
    restored.load_state_dict(state)
    with pytest.raises(AccountingFailure, match="activation"):
        restored.assert_dispatch_allowed()
    restored.activate_failure_policy(
        FailurePolicyConfig(enabled=True),
        authorization=state["failure_policy"]["authorization"],
        bindings=state["failure_policy"]["bindings"],
        execution_guard=lambda: True,
    )
    restored.assert_dispatch_allowed()
    assert restored.state_dict() == state
    bad = copy.deepcopy(state)
    bad["failure_policy"]["events"][0]["backend"] = "changed"
    with pytest.raises(ValueError, match="rewrite failure-policy"):
        restored.load_state_dict(bad)
    assert restored.state_dict() == state


def test_crash_window_stops_new_dispatch_until_explicit_terminal_disposition():
    ctl = controller()
    token = ctl.ledger.begin_logical_request("fixture")
    entry = ctl.ledger.reserve(0.01, kind="role", model="fixture", metadata={"provider_logical_request_id": token})
    entry["status"] = "dispatched"
    state = ctl.ledger.state_dict()
    restored = ExpenditureLedger(ctl.limits)
    restored.load_state_dict(state)
    restored.activate_failure_policy(
        FailurePolicyConfig(enabled=True),
        authorization=state["failure_policy"]["authorization"],
        bindings=state["failure_policy"]["bindings"],
        execution_guard=lambda: True,
    )
    with pytest.raises(PolicyStopped, match="recovery"):
        restored.assert_dispatch_allowed()
    restored.record_policy_recovery(
        0,
        disposition="terminal-unresolved",
        evidence={"source": "offline ambiguous failure review"},
        execution_guard=lambda: True,
    )
    restored.finish_logical_request(token)
    restored.assert_dispatch_allowed()
    assert restored.entries[0] == entry and restored.entries[0]["charged"] is None
    assert restored.uncertainty_summary()["terminal_unresolved_ids"] == [0]


def test_real_gateway_missing_bill_has_bounded_durable_metadata_intents():
    ctl = controller()
    calls = []
    raw = frame(
        '{"answer":"yes","explanation":""}',
        finish="stop",
        native="stop",
        usage={
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "prompt_tokens_details": {"cached_tokens": 0},
            "completion_tokens_details": {"reasoning_tokens": 0},
            "is_byok": True,
        },
    )

    def handle(request):
        calls.append(request)
        return httpx.Response(404) if request.method == "GET" else httpx.Response(200, text=sse([raw]))

    model = build_openrouter_model(binding(), "offline-key", temperature=0.7, transport=httpx.MockTransport(handle))
    result = asyncio.run(call(ctl, model))
    assert result.metadata["accounting_complete"] is False
    assert [item.method for item in calls] == ["POST", "GET", "GET", "GET"]
    events = ctl.ledger.entries[0]["recovery_events"]
    assert len([item for item in events if item["event"] == "metadata-lookup-intent"]) == 3
    assert not ctl.ledger.observe_accounting(
        ctl.ledger.entries[0], "metadata-lookup-intent", {"generation_id": "generation-fixture"}
    )
    assert "accounting_observer" not in json.loads(calls[0].content)


@pytest.mark.parametrize(
    "status,hard", [(502, False), (429, False), (401, True), (403, True), (402, True), (400, False)]
)
def test_real_gateway_classifies_http_errors_without_regeneration(status, hard):
    ctl = controller()
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(status, json={"error": {"code": status}}, headers={"x-generation-id": "fixture-http"})

    model = build_openrouter_model(binding(), "offline-key", temperature=0.7, transport=httpx.MockTransport(handle))
    expected = AccountingFailure if hard else ResourceFailure
    with pytest.raises(expected):
        asyncio.run(call(ctl, model))
    assert sum(item.method == "POST" for item in calls) == 1
    assert bool(ctl.ledger.halted) is hard
    if status in (401, 403):
        assert len(calls) == 1
    assert ctl.ledger.entries[0]["charged"] is None


def test_terminal_drain_does_not_cancel_other_dispatched_calls():
    ctl = controller(policy=FailurePolicyConfig(enabled=True, max_consecutive_provider_failures=1))

    async def scenario():
        both = asyncio.Event()
        release = asyncio.Event()
        dispatched = []

        class Concurrent:
            async def invoke(self, **kwargs):
                number = len(dispatched)
                dispatched.append(number)
                if len(dispatched) == 2:
                    both.set()
                await both.wait()
                if number == 0:
                    raise provider_failure(paid=0)
                await release.wait()
                return completed()

        model = Concurrent()
        first = asyncio.create_task(call(ctl, model))
        second = asyncio.create_task(call(ctl, model))
        with pytest.raises(ResourceFailure):
            await first
        assert ctl.ledger.dispatch_stop_reason["operational_drain"]
        release.set()
        result = await second
        assert result.response.content and len(dispatched) == 2

    asyncio.run(scenario())


@pytest.mark.parametrize("case", ["metadata", "error-binding"])
def test_corrupt_metadata_or_binding_precedes_soft_provider_error(case):
    ctl = controller()
    calls = []
    raw = frame("partial", finish="stop", native="stop")
    if case == "error-binding":
        raw["model"] = "unapproved"
        raw["error"] = {"code": 502}

    def handle(request):
        calls.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"data": []})
        return httpx.Response(200, text=sse([raw]))

    model = build_openrouter_model(binding(), "offline-key", temperature=0.7, transport=httpx.MockTransport(handle))
    with pytest.raises(AccountingFailure):
        asyncio.run(call(ctl, model))
    assert ctl.ledger.dispatch_stop_reason["hard_failure"]
    assert sum(item.method == "POST" for item in calls) == 1
