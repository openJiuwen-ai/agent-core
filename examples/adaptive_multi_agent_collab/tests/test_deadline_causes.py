"""Production Runtime/public Model/gateway cancellation with mocked transport."""

from __future__ import annotations

import asyncio
from dataclasses import replace

import httpx
import pytest

from examples.adaptive_multi_agent_collab.providers.adapter import OpenJiuwenAgent
from examples.adaptive_multi_agent_collab.infrastructure.failure_policy import (
    FailureKind,
    FailurePolicyConfig,
    cancel_with_cause,
    cancellation_cause,
    classify_failure,
    deadline_scope,
    logical_summary,
)
from examples.adaptive_multi_agent_collab.providers.gateway import build_openrouter_model
from examples.adaptive_multi_agent_collab.workflows.runtime import (
    Runtime,
    ScriptedRoutingPolicy,
    _ModelCallFailure,
    _ModelCallTimeout,
)
from examples.adaptive_multi_agent_collab.providers.resources import AccountingFailure
from examples.adaptive_multi_agent_collab.schemas import (
    AgentCallKind,
    AgentOutput,
    AgentRequest,
)
from examples.adaptive_multi_agent_collab.tests.test_failure_policy_resources import (
    controller,
    call,
)
from examples.adaptive_multi_agent_collab.tests.test_gateway import binding, frame, sse
from examples.adaptive_multi_agent_collab.tests.test_runtime import (
    _matrix,
    _offline_config,
    _weighting,
)


def complete_frame(*, paid=0.00002, input_tokens=10):
    usage = {
        "prompt_tokens": input_tokens,
        "completion_tokens": 5,
        "prompt_tokens_details": {"cached_tokens": 0},
        "completion_tokens_details": {"reasoning_tokens": 0},
        "is_byok": True,
    }
    if paid is not None:
        usage["cost"] = paid
    return frame(
        '{"answer":"yes","explanation":""}', finish="stop", native="stop", usage=usage
    )


def runtime_for(handler, *, timeout=0.04):
    ctl = controller(
        policy=FailurePolicyConfig(enabled=True, max_unresolved_requests=8)
    )
    model = build_openrouter_model(
        binding(),
        "offline-key",
        temperature=0.7,
        transport=httpx.MockTransport(handler),
    )
    cfg = _offline_config(
        failure_policy=FailurePolicyConfig(enabled=True, max_unresolved_requests=8)
    )
    cfg = replace(cfg, safety=replace(cfg.safety, call_timeout_seconds=timeout))
    agents = {index: OpenJiuwenAgent(model, resources=ctl) for index in cfg.agent_ids}
    runtime = Runtime(
        cfg,
        agents,
        ScriptedRoutingPolicy(cfg.n_agents, {1: _matrix(cfg.n_agents)}),
        _weighting(cfg),
    )
    return runtime, ctl, model


async def initial(runtime):
    request = AgentRequest(AgentCallKind.INITIAL, 0, "synthetic question", "initial")
    return await runtime._validated_call(
        request, AgentOutput, lambda output: (output, None)
    )


def outcomes(ctl):
    return [
        item["outcome"]
        for item in ctl.ledger.failure_policy["events"]
        if item["event"] == "logical-finish"
    ]


def test_legacy_outer_timeout_reproduction_then_runtime_five_logical_failures_pause():
    """The legacy native outer timeout records interruption before translation."""
    requests = []

    async def handle(request):
        requests.append(request.method)
        await asyncio.sleep(1)
        raise AssertionError("late response must not be consumed")

    runtime, ctl, model = runtime_for(handle)

    async def scenario():
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(0.01):
                await call(ctl, model)
        assert (
            ctl.ledger.entries[0]["usage"]["failure_kind"]
            == FailureKind.INTERRUPTED.value
        )
        assert outcomes(ctl) == ["other-failure"]
        for _ in range(5):
            with pytest.raises(_ModelCallTimeout):
                await initial(runtime)
        count = len(requests)
        with pytest.raises(_ModelCallFailure):
            await initial(runtime)
        assert len(requests) == count == 6
        assert outcomes(ctl) == ["other-failure", *(["provider-failed"] * 5)]
        assert list(
            logical_summary(ctl.ledger.failure_policy)["backend_streaks"].values()
        ) == [5]
        assert ctl.ledger.dispatch_stop_reason["operational_drain"]
        assert all(
            item["usage"]["cancellation_cause"] == "call-deadline"
            for item in ctl.ledger.entries[1:]
        )
        assert all(item["charged"] is None for item in ctl.ledger.entries)
        assert all(item["usage"]["model_calls"] == 1 for item in ctl.ledger.entries)

    asyncio.run(scenario())


def test_successful_provider_completion_resets_deadline_failure_streak():
    modes = iter(["timeout", "complete", "timeout"])

    async def handle(request):
        assert request.method == "POST"
        if next(modes) == "timeout":
            await asyncio.sleep(1)
        return httpx.Response(200, text=sse([complete_frame()]))

    runtime, ctl, _ = runtime_for(handle)

    async def scenario():
        with pytest.raises(_ModelCallTimeout):
            await initial(runtime)
        output = await initial(runtime)
        assert output.answer == "yes"
        with pytest.raises(_ModelCallTimeout):
            await initial(runtime)
        assert outcomes(ctl) == [
            "provider-failed",
            "provider-completed",
            "provider-failed",
        ]
        assert list(
            logical_summary(ctl.ledger.failure_policy)["backend_streaks"].values()
        ) == [1]

    asyncio.run(scenario())


def test_receipt_get_deadline_does_not_reclassify_completed_generation_or_revive_answer():
    requests = []

    async def handle(request):
        requests.append(request.method)
        if request.method == "GET":
            await asyncio.sleep(1)
        return httpx.Response(200, text=sse([complete_frame(paid=None)]))

    runtime, ctl, _ = runtime_for(handle)

    async def scenario():
        with pytest.raises(_ModelCallTimeout) as captured:
            await initial(runtime)
        assert captured.value.output.structured_output is None
        assert requests == ["POST", "GET"]
        receipt = ctl.ledger.entries[0]
        assert receipt["usage"]["provider_completed"] is True
        assert receipt["usage"]["timing_phase"] == "receipt-recovery"
        assert receipt["usage"]["input_tokens"] == 10
        assert receipt["charged"] is None and receipt["reserved"] > 0
        assert outcomes(ctl) == ["provider-completed"]
        assert (
            len(
                [
                    event
                    for event in receipt["recovery_events"]
                    if event["event"] == "metadata-lookup-intent"
                ]
            )
            == 1
        )

    asyncio.run(scenario())


def test_get_transport_timeouts_inside_call_deadline_retain_valid_answer_and_unknown_bill():
    """A failed receipt lookup is not failure of the completed generation."""
    requests = []

    async def handle(request):
        requests.append(request.method)
        if request.method == "GET":
            raise httpx.ReadTimeout("synthetic receipt lookup timeout", request=request)
        return httpx.Response(200, text=sse([complete_frame(paid=None)]))

    runtime, ctl, _ = runtime_for(handle, timeout=1.0)

    async def scenario():
        output = await initial(runtime)
        assert output.answer == "yes"
        assert output.structured_output == {"answer": "yes", "explanation": ""}
        assert output.error is None
        assert requests == ["POST", "GET", "GET", "GET"]
        receipt = ctl.ledger.entries[0]
        assert receipt["status"] == "completed"
        assert receipt["usage"]["provider_completed"] is True
        assert receipt["usage"]["response_identity_verified"] is True
        assert receipt["charged"] is None and receipt["reserved"] > 0
        assert receipt["usage"]["reference_cost"] == pytest.approx(0.00002)
        assert outcomes(ctl) == ["provider-completed"]
        assert ctl.ledger.dispatch_stop_reason is None
        attempts = receipt["usage"]["metadata_lookup_attempts"]
        assert len(attempts) == 3 and all(
            item["error_type"] == "ReadTimeout" for item in attempts
        )

    asyncio.run(scenario())


def test_predispatch_capacity_expiry_has_no_post_attempt_or_provider_failure():
    calls = []
    runtime, ctl, _ = runtime_for(lambda request: calls.append(request))
    for _ in range(8):
        ctl.ledger.reserve(0.001, kind="initial", model="pending", metadata={})

    async def scenario():
        with pytest.raises((_ModelCallTimeout, _ModelCallFailure)) as captured:
            await initial(runtime)
        assert captured.value.usage.model_calls == 0
        assert len(ctl.ledger.entries) == 8 and calls == []
        assert outcomes(ctl) == ["not-dispatched"]
        assert sum(entry["reserved"] for entry in ctl.ledger.entries) == pytest.approx(
            0.008
        )

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "cause", ["operator-cancellation", "shutdown-cleanup", "session-deadline"]
)
def test_explicit_non_call_cancellation_does_not_increment_provider_streak(cause):
    started = asyncio.Event()

    async def handle(request):
        started.set()
        await asyncio.sleep(5)

    runtime, ctl, _ = runtime_for(handle, timeout=1)

    async def scenario():
        task = asyncio.create_task(initial(runtime))
        await started.wait()
        cancel_with_cause(task, cause)
        with pytest.raises(asyncio.CancelledError) as captured:
            await task
        assert cancellation_cause(captured.value) == cause
        assert outcomes(ctl) == ["other-failure"]
        assert ctl.ledger.entries[0]["usage"]["cancellation_cause"] == cause
        assert ctl.ledger.entries[0]["charged"] is None

    asyncio.run(scenario())


def test_external_cancellation_wins_same_turn_as_fired_deadline():
    async def scenario():
        task = asyncio.current_task()
        with pytest.raises(asyncio.CancelledError) as captured:
            async with deadline_scope("call-deadline", timeout=0):
                asyncio.get_running_loop().call_soon(
                    cancel_with_cause, task, "operator-cancellation"
                )
                await asyncio.sleep(1)
        assert cancellation_cause(captured.value) == "operator-cancellation"
        assert classify_failure(captured.value) is FailureKind.INTERRUPTED
        task.uncancel()

    asyncio.run(scenario())


def test_session_scope_is_visible_before_worker_unwind_and_not_call_failure():
    async def handle(request):
        await asyncio.sleep(1)

    runtime, ctl, _ = runtime_for(handle, timeout=1)

    async def scenario():
        runtime._session_deadline = asyncio.get_running_loop().time() + 0.03
        with pytest.raises(_ModelCallTimeout):
            await initial(runtime)
        assert (
            ctl.ledger.entries[0]["usage"]["cancellation_cause"] == "session-deadline"
        )
        assert outcomes(ctl) == ["other-failure"]

    asyncio.run(scenario())


def test_native_sdk_idle_timeout_explicit_exception_chain_is_classified():
    class Slow(httpx.AsyncByteStream):
        async def __aiter__(self):
            await asyncio.sleep(1)
            yield b"data: [DONE]\n\n"

    runtime, ctl, model = runtime_for(
        lambda request: httpx.Response(200, stream=Slow()), timeout=1
    )
    model.model_client_config.stream_first_chunk_timeout = 0.01

    async def scenario():
        with pytest.raises(_ModelCallFailure):
            await initial(runtime)
        assert (
            ctl.ledger.entries[0]["usage"]["failure_kind"] == FailureKind.TIMEOUT.value
        )
        assert outcomes(ctl) == ["provider-failed"]

    asyncio.run(scenario())


def test_known_count_violation_during_receipt_cancellation_keeps_bill_and_hard_stop():
    async def handle(request):
        if request.method == "GET":
            await asyncio.sleep(1)
        return httpx.Response(
            200, text=sse([complete_frame(paid=None, input_tokens=10000)])
        )

    runtime, ctl, _ = runtime_for(handle)

    async def scenario():
        with pytest.raises(AccountingFailure, match="input-token-limit"):
            await initial(runtime)
        assert ctl.ledger.halted
        assert ctl.ledger.entries[0]["usage"]["input_tokens"] == 10000
        assert ctl.ledger.entries[0]["charged"] is None

    asyncio.run(scenario())


def test_known_bill_on_incomplete_stream_is_retained_once_on_call_timeout():
    class Partial(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield sse([complete_frame()]).replace("data: [DONE]\n\n", "").encode()
            await asyncio.sleep(1)

    runtime, ctl, _ = runtime_for(lambda request: httpx.Response(200, stream=Partial()))

    async def scenario():
        with pytest.raises(_ModelCallTimeout):
            await initial(runtime)
        assert len(ctl.ledger.entries) == 1
        receipt = ctl.ledger.entries[0]
        assert receipt["charged"] == pytest.approx(0.00002)
        assert receipt["usage"]["input_tokens"] == 10
        assert receipt["usage"]["provider_completed"] is False
        assert outcomes(ctl) == ["provider-failed"]

    asyncio.run(scenario())


def test_known_binding_failure_survives_cancellation_during_receipt_recovery():
    invalid = complete_frame(paid=None)
    invalid["model"] = "unapproved-model"

    async def handle(request):
        if request.method == "GET":
            await asyncio.sleep(1)
        return httpx.Response(200, text=sse([invalid]))

    runtime, ctl, _ = runtime_for(handle)

    async def scenario():
        with pytest.raises(AccountingFailure, match="served model differs"):
            await initial(runtime)
        assert ctl.ledger.halted
        assert (
            ctl.ledger.entries[0]["usage"]["failure_kind"] == FailureKind.BINDING.value
        )
        assert ctl.ledger.entries[0]["charged"] is None

    asyncio.run(scenario())
