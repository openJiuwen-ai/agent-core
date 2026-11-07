"""Pinned TextGrad graph with only local injected model/resource boundaries."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from examples.adaptive_multi_agent_collab.learning.textgrad_optimizer import (
    TEXTGRAD_PROTOCOL_ID, TEXTGRAD_REPAIR_PROTOCOL_ID, TEXTGRAD_REPAIR_DIAGNOSTICS,
    TextGradPromptProposer, _load_textgrad, textgrad_metadata, textgrad_repair_messages,
)
from examples.adaptive_multi_agent_collab.providers.resources import (
    AccountingFailure, BudgetExhausted, PolicyStopped,
)
from examples.adaptive_multi_agent_collab.workflows.prompts import seed_prompt_collection
from .test_textgrad_optimizer import configuration, occurrence, requires_textgrad
from .test_resources import controller

pytestmark = requires_textgrad


class Scripted:
    def __init__(self, outputs):
        self.outputs = iter(outputs)
        self.calls = []

    async def invoke(self, **kwargs):
        self.calls.append(kwargs)
        value = next(self.outputs)
        if isinstance(value, BaseException):
            raise value
        if isinstance(value, tuple):
            content, metadata = value
        else:
            content, metadata = value, {}
        return SimpleNamespace(content=content, usage_metadata={
            "input_tokens": 20, "output_tokens": 10, "total_cost": 0.01,
            "reference_cost": 0.02, "finish_reason": "stop", **metadata,
        })


def spec_and_output():
    spec = seed_prompt_collection().components["role:S_T"]
    return spec, f"<IMPROVED_VARIABLE>{spec.template}\nExplain a supported correction.</IMPROVED_VARIABLE>"


def run(backward, optimizer, **kwargs):
    spec, _ = spec_and_output()
    proposer = TextGradPromptProposer(backward, optimizer, config=configuration(
        output_repair_limit=1, backward_supports_seed=True, optimizer_supports_seed=True, **kwargs,
    ))
    return asyncio.run(proposer.propose(spec, [occurrence()], iteration_id="repair-slot-1"))


@pytest.mark.parametrize("stage,invalid", [
    ("evaluation", ""), ("evaluation", ("partial", {"finish_reason": "length"})),
    ("backward", ""), ("backward", ("partial", {"native_finish_reason": "max_tokens"})),
    ("proposal", ""), ("proposal", ("partial", {"finish_reason": "max_output_tokens"})),
    ("proposal", "missing delimiters"),
    ("proposal", "<IMPROVED_VARIABLE>missing scaffold</IMPROVED_VARIABLE>"),
])
def test_repair_only_invalid_generation_preserves_graph_and_one_step(monkeypatch, stage, invalid):
    tg, _ = _load_textgrad("0.1.8")
    calls = []
    original_step = tg.TextualGradientDescent.step
    def step(self):
        calls.append(self)
        return original_step(self)
    monkeypatch.setattr(tg.TextualGradientDescent, "step", step)
    spec, output = spec_and_output()
    backward_values = ["evaluation feedback", "backward feedback"]
    proposal_values = [output]
    if stage == "proposal":
        proposal_values.insert(0, invalid)
    else:
        backward_values.insert(0 if stage == "evaluation" else 1, invalid)
    backward, optimizer = Scripted(backward_values), Scripted(proposal_values)
    result = run(backward, optimizer)
    assert result.template == spec.template + "\nExplain a supported correction."
    assert result.error is None and len(calls) == 1
    assert len(result.attempts) == 4 and result.usage.model_calls == 4
    assert result.usage.paid_monetary_cost == pytest.approx(0.04)
    assert result.usage.monetary_cost == pytest.approx(0.08)
    repaired = [item for item in result.attempts if item.usage.raw_metadata["output_attempt"] == 1]
    assert len(repaired) == 1 and repaired[0].stage == stage
    pair = [item for item in result.attempts if item.stage == stage]
    assert pair[0].generation_seed == pair[1].generation_seed
    before, after = [item.usage.raw_metadata["effective_messages"] for item in pair]
    assert after[:-1] == before
    reason = pair[1].usage.raw_metadata["repair_reason"]
    assert after[-1]["content"] == TEXTGRAD_REPAIR_DIAGNOSTICS[stage][reason]
    assert "complete JSON" not in after[-1]["content"]
    assert result.attempts[-1].usage.raw_metadata["logical_call_id"].endswith(":proposal:3")


def test_exhausted_proposer_is_rejected_before_library_mutation_and_does_not_repeat_graph(monkeypatch):
    tg, _ = _load_textgrad("0.1.8")
    calls = []
    step = tg.TextualGradientDescent.step
    def tracked(self):
        calls.append(self)
        return step(self)
    monkeypatch.setattr(tg.TextualGradientDescent, "step", tracked)
    spec, _ = spec_and_output()
    backward, optimizer = Scripted(["eval", "critic"]), Scripted([
        "missing tags", "<IMPROVED_VARIABLE>still missing scaffold</IMPROVED_VARIABLE>",
    ])
    result = run(backward, optimizer)
    assert result.template is None and "exhausted: structure" in result.error
    assert len(calls) == 1 and len(backward.calls) == 2 and len(optimizer.calls) == 2
    assert calls[0].parameters[0].value == spec.template
    assert len(result.attempts) == 4


@pytest.mark.parametrize("stage", ["evaluation", "backward"])
def test_exhausted_critic_never_runs_proposal(stage):
    _, valid = spec_and_output()
    backward = Scripted((["evaluation"] if stage == "backward" else []) + ["", ""])
    optimizer = Scripted([valid])
    result = run(backward, optimizer)
    assert result.template is None and "exhausted: empty" in result.error
    assert not optimizer.calls


def test_unchanged_valid_template_and_missing_billing_do_not_repair():
    spec, _ = spec_and_output()
    backward = Scripted([("weak feedback", {"total_cost": None}), "weak critique"])
    optimizer = Scripted([f"<IMPROVED_VARIABLE>{spec.template}</IMPROVED_VARIABLE>"])
    result = run(backward, optimizer)
    assert result.template == spec.template and len(result.attempts) == 3
    assert result.usage.paid_monetary_cost is None


@pytest.mark.parametrize("failure,disposition", [
    (AccountingFailure("known accounting corruption"), "accounting-stop"),
    (BudgetExhausted("spent budget"), "budget-exhausted"),
    (PolicyStopped("backend streak pause"), "policy-stop"),
    (asyncio.CancelledError("operator stop"), "cancelled"),
])
def test_hard_stop_and_cancellation_propagate_with_audit_and_no_repair(failure, disposition):
    _, output = spec_and_output()
    backward, optimizer = Scripted(["eval", "critic"]), Scripted([failure, output])
    with pytest.raises(type(failure)) as caught:
        run(backward, optimizer)
    audit = caught.value.prompt_proposal
    assert audit.metadata["failure_disposition"] == disposition
    assert len(audit.attempts) == 3 and len(optimizer.calls) == 1
    assert audit.template is None


def test_provider_failure_never_receives_output_repair():
    _, output = spec_and_output()
    backward, optimizer = Scripted([RuntimeError("provider unavailable"), "eval"]), Scripted([output])
    result = run(backward, optimizer)
    assert "provider unavailable" in result.error and len(backward.calls) == 1 and not optimizer.calls


def test_original_logical_deadline_covers_repair():
    class SlowInvalid(Scripted):
        async def invoke(self, **kwargs):
            self.calls.append(kwargs)
            await asyncio.sleep(0.04)
            return SimpleNamespace(content="", usage_metadata={})
    _, output = spec_and_output()
    backward, optimizer = SlowInvalid([]), Scripted([output])
    result = run(backward, optimizer, call_timeout_seconds=0.06)
    assert result.template is None and "TimeoutError" in result.error
    assert len(backward.calls) == 2 and not optimizer.calls
    assert result.attempts[1].usage.raw_metadata["output_attempt"] == 1


def test_config_versioning_does_not_relabel_historical_metadata():
    old = textgrad_metadata(configuration(), {})
    new = textgrad_metadata(configuration(output_repair_limit=1), {})
    assert old["protocol"] == TEXTGRAD_PROTOCOL_ID and "output_repair_limit" not in old
    assert new["protocol"] == TEXTGRAD_REPAIR_PROTOCOL_ID and new["output_repair_limit"] == 1
    assert new["output_repair_diagnostics"] == TEXTGRAD_REPAIR_DIAGNOSTICS
    for value in (True, -1, 2):
        with pytest.raises(ValueError, match="bounded to one"):
            configuration(output_repair_limit=value)


def test_repair_uses_original_messages_without_rejected_output_or_accumulation():
    original = [{"role": "system", "content": "preserved system"}, {"role": "user", "content": "full evidence"}]
    for stage, reasons in TEXTGRAD_REPAIR_DIAGNOSTICS.items():
        for reason in reasons:
            actual = textgrad_repair_messages(original, stage, reason)
            assert actual[:-1] == original and len(actual) == 3
            actual[0]["content"] = "not aliased"
            assert original[0]["content"] == "preserved system"


def test_actual_resource_path_accounts_both_generations_and_keeps_logical_identity():
    spec, output = spec_and_output()
    backward = Scripted(["", "eval", "critic"])
    optimizer = Scripted([output])
    ctl = controller()
    proposer = TextGradPromptProposer(backward, optimizer, config=configuration(output_repair_limit=1),
                                    resources={"backward": ctl, "optimizer": ctl})
    result = asyncio.run(proposer.propose(spec, [occurrence()], iteration_id="accounted-output-repair"))
    assert result.error is None and len(ctl.ledger.entries) == 4
    assert all(entry["category"] == "textgrad" for entry in ctl.ledger.entries)
    assert len({entry["dispatch_id"] for entry in ctl.ledger.entries}) == 4
    assert result.usage.paid_monetary_cost == pytest.approx(sum(entry["charged"] for entry in ctl.ledger.entries))
    assert [item["physical_dispatch_id"] for attempt in result.attempts
            for item in attempt.usage.raw_metadata["physical_attempts"]] == [0, 1, 2, 3]
    first, repaired = ctl.ledger.entries[:2]
    assert repaired["effective_input"][:-1] == first["effective_input"]


def test_planning_counts_repair_envelope_before_any_model_call():
    class DiagnosticCounter:
        def count_input(self, messages, schema=None):
            return 9000 if any(message["content"] in {value for values in TEXTGRAD_REPAIR_DIAGNOSTICS.values()
                                                      for value in values.values()} for message in messages) else 100
        def count_output(self, text):
            return 10
    spec, output = spec_and_output()
    ctl = controller()
    ctl.counter = DiagnosticCounter()
    backward, optimizer = Scripted(["eval", "critic"]), Scripted([output])
    proposer = TextGradPromptProposer(backward, optimizer, config=configuration(output_repair_limit=1),
                                    resources={"backward": ctl, "optimizer": ctl})
    result = asyncio.run(proposer.propose(spec, [occurrence()], iteration_id="envelope-refusal"))
    assert result.template is None and "no complete failure record fits" in result.error
    assert not backward.calls and not optimizer.calls and not ctl.ledger.entries


def test_receipt_identity_survives_restore_and_prevents_repair_allowance_replay():
    from examples.adaptive_multi_agent_collab.providers.resources import ExpenditureLedger, ResourceController
    spec, output = spec_and_output()
    ctl = controller()
    first = TextGradPromptProposer(Scripted(["", "eval", "critic"]), Scripted([output]),
        config=configuration(output_repair_limit=1), resources={"backward": ctl, "optimizer": ctl})
    result = asyncio.run(first.propose(spec, [occurrence()], iteration_id="durable-slot"))
    assert result.error is None
    saved = ctl.ledger.state_dict()
    restored = ExpenditureLedger(ctl.limits)
    restored.load_state_dict(saved)
    restored_ctl = ResourceController(ctl.profile, ctl.counter, restored, limits=ctl.limits)
    backward, optimizer = Scripted(["eval", "critic"]), Scripted([output])
    resumed = TextGradPromptProposer(backward, optimizer, config=configuration(output_repair_limit=1),
        resources={"backward": restored_ctl, "optimizer": restored_ctl})
    with pytest.raises(AccountingFailure, match="replay is forbidden"):
        asyncio.run(resumed.propose(spec, [occurrence()], iteration_id="durable-slot"))
    assert len(restored.entries) == 4 and not backward.calls and not optimizer.calls
    different = asyncio.run(resumed.propose(spec, [occurrence()], iteration_id="next-legitimate-slot"))
    assert different.error is None and len(restored.entries) == 7


def test_repair_does_not_multiply_active_policy_logical_completions():
    from .test_failure_policy_resources import controller as active_controller
    ctl = active_controller()
    spec, output = spec_and_output()
    paid = {"total_cost": 0.00004, "reasoning_tokens": 0, "cache_tokens": 0, "is_byok": True,
            "response_identity_verified": True}
    backward = Scripted([(text, paid) for text in ("", "eval", "critic")])
    optimizer = Scripted([(output, paid)])
    proposer = TextGradPromptProposer(backward, optimizer, config=configuration(output_repair_limit=1),
        resources={"backward": ctl, "optimizer": ctl})
    result = asyncio.run(proposer.propose(spec, [occurrence()], iteration_id="policy-logical"))
    assert result.error is None
    events = ctl.ledger.failure_policy["events"]
    assert len([item for item in events if item["event"] == "logical-start"]) == 3
    assert len([item for item in events if item["event"] == "logical-finish"]) == 3
    assert ctl.ledger.entries[0]["provider_logical_request_id"] == ctl.ledger.entries[1]["provider_logical_request_id"]


def test_cancellation_audit_waits_for_resource_coroutine_finalization():
    class CleanupModel(Scripted):
        finished = False
        async def invoke(self, **kwargs):
            self.calls.append(kwargs)
            try:
                await asyncio.sleep(30)
            finally:
                await asyncio.sleep(0.02)
                self.finished = True
    async def exercise():
        spec, output = spec_and_output()
        model = CleanupModel([])
        proposer = TextGradPromptProposer(model, Scripted([output]), config=configuration(output_repair_limit=1))
        task = asyncio.create_task(proposer.propose(spec, [occurrence()], iteration_id="cancelled-graph"))
        while not model.calls:
            await asyncio.sleep(0.001)
        task.cancel()
        with pytest.raises(asyncio.CancelledError) as caught:
            await task
        assert model.finished
        assert len(caught.value.prompt_proposal.attempts) == 1
        assert caught.value.prompt_proposal.attempts[0].error is not None
    asyncio.run(exercise())


def test_real_public_model_gateway_serializes_repair_and_accounts_each_dispatch():
    import json
    import httpx
    from examples.adaptive_multi_agent_collab.providers.gateway import build_openrouter_model, COLLABORATION_MODELS
    from examples.adaptive_multi_agent_collab.learning.textgrad_optimizer import TextGradConfig
    from .test_gateway import binding, frame, sse
    spec, proposal = spec_and_output()
    bodies = []
    outputs = iter(["", "specific evaluator feedback", "specific backward feedback", proposal])
    def transport(request):
        body = json.loads(request.content)
        bodies.append(body)
        return httpx.Response(200, text=sse([
            frame(next(outputs), finish="stop", native="stop", model=body["model"]),
            {"choices": [], "usage": {"prompt_tokens": 40, "completion_tokens": 10, "cost": 0.000009,
                "is_byok": False, "prompt_tokens_details": {"cached_tokens": 0},
                "completion_tokens_details": {"reasoning_tokens": 0}}},
        ]))
    model = build_openrouter_model(binding(), "offline-fixture", temperature=0,
                                   transport=httpx.MockTransport(transport))
    ctl = controller()
    config = TextGradConfig(COLLABORATION_MODELS[0], "fixture", COLLABORATION_MODELS[0], "fixture",
                           output_repair_limit=1, backward_decoding=(("temperature", 0),),
                           optimizer_decoding=(("temperature", 0),), backward_supports_seed=True,
                           optimizer_supports_seed=True)
    proposer = TextGradPromptProposer(model, model, config=config, resources={"backward": ctl, "optimizer": ctl})
    result = asyncio.run(proposer.propose(spec, [occurrence()], iteration_id="public-gateway-repair"))
    assert result.error is None and len(bodies) == len(ctl.ledger.entries) == 4
    assert bodies[1]["messages"][:-1] == bodies[0]["messages"]
    assert bodies[1]["seed"] == bodies[0]["seed"]
    assert all(body["model"] == COLLABORATION_MODELS[0] and body["temperature"] == 0 for body in bodies)
    assert all("response_format" not in body and "reasoning" not in body and "thinking" not in body for body in bodies)
    assert result.usage.model_calls == 4 and result.usage.input_tokens == 160 and result.usage.output_tokens == 40
    assert result.usage.paid_monetary_cost == pytest.approx(4 * 0.000009)
    assert all(entry["category"] == "textgrad" for entry in ctl.ledger.entries)


def test_output_repair_does_not_replenish_logical_safe_transport_retries():
    from examples.adaptive_multi_agent_collab.providers.resources import TransientFailure
    def safe_retry():
        return TransientFailure("confirmed not generated", documentation="offline fixture contract",
                                generation_not_started=True, retry_after=0)
    spec, output = spec_and_output()
    ctl = controller()
    backward = Scripted([safe_retry(), "", safe_retry(), "must not be reached"])
    optimizer = Scripted([output])
    proposer = TextGradPromptProposer(backward, optimizer, config=configuration(output_repair_limit=1),
                                    resources={"backward": ctl, "optimizer": ctl})
    result = asyncio.run(proposer.propose(spec, [occurrence()], iteration_id="shared-transport-allowance"))
    assert result.template is None and "confirmed not generated" in result.error
    assert len(backward.calls) == len(ctl.ledger.entries) == 3 and not optimizer.calls
    assert len(result.attempts) == 2 and result.usage.model_calls == 3
    assert ctl.limits.transport_retries == ctl.ledger.limits.transport_retries == 1
    assert backward.calls[0]["messages"] == backward.calls[1]["messages"]
    assert backward.calls[2]["messages"][:-1] == backward.calls[1]["messages"]
