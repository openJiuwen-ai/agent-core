"""A policy pause before tie dispatch records a fallback, never a generation."""

from __future__ import annotations

import asyncio
import copy
from dataclasses import replace

import httpx

from examples.adaptive_multi_agent_collab.providers.adapter import OpenJiuwenAgent
from examples.adaptive_multi_agent_collab.infrastructure.failure_policy import proposed_failure_policy
from examples.adaptive_multi_agent_collab.providers.gateway import build_openrouter_model
from examples.adaptive_multi_agent_collab.providers.resources import PolicyStopped
from examples.adaptive_multi_agent_collab.schemas import ConversationStatus, ConversationTag, Scheme, TerminalRecord
from examples.adaptive_multi_agent_collab.tests.test_failure_policy_resources import controller
from examples.adaptive_multi_agent_collab.tests.test_gateway import binding
from examples.adaptive_multi_agent_collab.tests.test_runtime import _matrix, _runtime, _state, _terminal_trace


def test_predispatch_policy_pause_uses_first_modal_answer_without_inventing_tie_output():
    policy = proposed_failure_policy()
    controls = controller(policy=policy)
    # Eight synthetic terminal unknown receipts exercise the real admission
    # policy on an isolated ledger; these are not historical campaign records.
    for _ in range(policy.max_unresolved_requests):
        receipt = controls.ledger.reserve(0.001, kind="role", model="fixture", metadata={})
        controls.ledger.settle(receipt, {"total_cost": None, "accounting_complete": False}, status="failed", elapsed=0)
    original_ledger = copy.deepcopy(controls.ledger.state_dict())
    reason = controls.ledger.dispatch_stop_reason["reason"]
    transport_requests = []

    def handle(request):
        transport_requests.append(request)
        raise AssertionError("a policy-paused tie must not reach the mocked transport")

    model = build_openrouter_model(
        binding(), "offline-fixture-key", temperature=0.7, transport=httpx.MockTransport(handle)
    )
    runtime, _ = _runtime(3, {}, {1: _matrix(3)})
    runtime.config = replace(runtime.config, failure_policy=policy)
    runtime.agents[0] = OpenJiuwenAgent(model, resources=controls)
    state = _state(3)
    records = (
        TerminalRecord(1, 0, "B", ConversationTag(1, 0, 1, Scheme.REVIEWER)),
        TerminalRecord(1, 0, "A", ConversationTag(1, 0, 2, Scheme.TEACHER)),
    )
    raw = {0: records, 1: (), 2: ()}
    traces = tuple(_terminal_trace(record) for record in records)
    effective, answers, modes, outputs, history, usage = asyncio.run(runtime._resolve_answers(state, raw, traces, ()))

    assert effective[0] == records and modes[0] == ("A", "B")
    assert answers == ("A", "B", "C")
    assert not outputs and not transport_requests
    assert not runtime._call_records and not runtime._prompt_calls
    assert usage.model_calls == usage.input_tokens == usage.output_tokens == 0
    assert usage.monetary_cost == usage.paid_monetary_cost == 0
    assert isinstance(runtime._drain_error, PolicyStopped)
    assert str(runtime._drain_error) == reason
    fallback = [item for item in runtime._fallback_records if item["kind"] == "tie-fallback"]
    assert len(fallback) == 1
    assert fallback[0]["answer"] == "A" and fallback[0]["error"] == reason
    assert fallback[0]["not_dispatched"] and fallback[0]["operational_drain"]
    assert len(history) == 1
    assert history[0].status == ConversationStatus.SAFEGUARD_COMPLETE.value
    assert history[0].parse_outcome == "not-dispatched"
    assert history[0].usage.model_calls == history[0].normalized_cost == 0
    assert history[0].explanation == "" and reason in history[0].content
    assert controls.ledger.state_dict() == original_ledger
