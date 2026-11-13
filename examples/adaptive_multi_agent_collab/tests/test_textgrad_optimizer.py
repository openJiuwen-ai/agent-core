"""Real TextGrad graph tests with local openJiuwen-shaped model objects."""

from __future__ import annotations

import asyncio
import importlib.metadata
import importlib.util
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest

from examples.adaptive_multi_agent_collab.workflows.prompts import PromptOccurrence, seed_prompt_collection
from examples.adaptive_multi_agent_collab.learning.textgrad_optimizer import (
    TextGradConfig,
    TextGradPromptProposer,
    EVALUATOR_INSTRUCTION,
    OPTIMIZER_INSTRUCTION,
    _load_textgrad,
)


def configuration(**kwargs: Any) -> TextGradConfig:
    return TextGradConfig("local-backward", "revision-a", "local-optimizer", "revision-b", **kwargs)


def occurrence(**kwargs: Any) -> PromptOccurrence:
    item = PromptOccurrence(
        "role:S_T",
        "rendered teaching prompt",
        "observed model response",
        "wrong",
        "correct",
        {"subsequent_messages": ["fixed trace evidence"]},
        0.25,
        {"conversation-1": -0.2},
        query="private training query",
        example_id="training-query-1",
    )
    return replace(item, **kwargs)


class LocalTextModel:
    def __init__(self, text: str = "Clarify the reusable instruction.", *, fail: bool = False) -> None:
        self.text = text
        self.fail = fail
        self.calls: list[dict[str, Any]] = []

    async def invoke(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.fail:
            raise RuntimeError("local recorded failure")
        return SimpleNamespace(
            content=self.text,
            usage_metadata={"input_tokens": 11, "output_tokens": 4, "total_cost": 0.01},
        )


def test_textgrad_dependency_missing_is_explicit_without_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(name: str) -> str:
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "version", missing)
    with pytest.raises(ImportError, match="not installed"):
        _load_textgrad("0.1.8")


@pytest.mark.parametrize("kwargs", [{"gradient_memory": 1}, {"momentum": 1}])
def test_primary_textgrad_protocol_rejects_memory_and_momentum_variants(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="zero memory"):
        configuration(**kwargs)


def test_workflow_output_guidance_does_not_replace_textgrad_engine_protocol() -> None:
    # Workflow records can contain JSON instructions as evidence, but the
    # evaluator and optimizer themselves still use the library's text/tag APIs.
    for instruction in (EVALUATOR_INSTRUCTION, OPTIMIZER_INSTRUCTION):
        assert "60 words" not in instruction and "complete JSON object" not in instruction
    assert "textual feedback, not a replacement template" in EVALUATOR_INSTRUCTION
    assert "{new_variable_start_tag}" in OPTIMIZER_INSTRUCTION
    assert "{new_variable_end_tag}" in OPTIMIZER_INSTRUCTION


def test_textgrad_rejects_validation_or_successful_evidence_before_any_call() -> None:
    model = LocalTextModel()
    proposer = TextGradPromptProposer(model, model, config=configuration())  # type: ignore[arg-type]
    spec = seed_prompt_collection().components["role:S_T"]
    for evidence in (occurrence(split="comp-val"), occurrence(outcome="correct")):
        with pytest.raises(ValueError, match="failed training occurrences"):
            asyncio.run(proposer.propose(spec, [evidence], iteration_id="cycle-1:attempt-1"))
    assert model.calls == []


@pytest.mark.parametrize("suffix", [" {invented}", " {query.__class__}", " {query!r}", " {"])
def test_prompt_structural_validator_rejects_new_or_malformed_fields(suffix: str) -> None:
    collection = seed_prompt_collection()
    key = "role:S_T"
    with pytest.raises(ValueError):
        collection.replace_template(key, collection.components[key].template + suffix)


requires_textgrad = pytest.mark.skipif(
    importlib.util.find_spec("textgrad") is None,
    reason="real TextGrad package unavailable; no fake library substitutes for its computation graph",
)


@requires_textgrad
def test_real_textgrad_graph_uses_one_variable_fixed_fields_and_one_library_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tg, _ = _load_textgrad("0.1.8")
    variables: list[Any] = []
    real_variable = tg.Variable
    real_sum = tg.sum
    aggregates: list[Any] = []

    def tracked_variable(*args: Any, **kwargs: Any) -> Any:
        variable = real_variable(*args, **kwargs)
        variables.append(variable)
        return variable

    def tracked_sum(losses: Any) -> Any:
        value = real_sum(losses)
        aggregates.append(value)
        return value

    monkeypatch.setattr(tg, "Variable", tracked_variable)
    monkeypatch.setattr(tg, "sum", tracked_sum)
    collection = seed_prompt_collection()
    spec = collection.components["role:S_T"]
    candidate = spec.template + "\nExplain the missing concept explicitly."
    backward = LocalTextModel()
    optimizer = LocalTextModel(f"<IMPROVED_VARIABLE>{candidate}</IMPROVED_VARIABLE>")
    proposer = TextGradPromptProposer(  # type: ignore[arg-type]
        backward,
        optimizer,
        config=configuration(backward_supports_seed=True, optimizer_supports_seed=True),
    )
    result = asyncio.run(
        proposer.propose(
            spec, [occurrence(), occurrence(example_id="training-query-2")], iteration_id="cycle-1:attempt-1"
        )
    )
    assert result.error is None
    assert result.template == candidate
    assert collection.components[spec.key].template == spec.template
    assert len(optimizer.calls) == 1
    assert len(aggregates) == 1
    trainable = [variable for variable in variables if variable.requires_grad]
    assert len(trainable) == 1
    assert trainable[0].role_description == spec.purpose
    assert trainable[0].gradients == set()
    assert result.textual_gradient is not None and result.textual_gradient.critique
    assert sum(item.stage == "evaluation" for item in result.attempts) == 2
    assert sum(item.stage == "proposal" for item in result.attempts) == 1
    assert all("seed" in call and call["timeout"] == 120 for call in backward.calls + optimizer.calls)
    first_evaluation = backward.calls[0]["messages"][-1]["content"]
    for field in (
        "training query",
        "rendered prompt",
        "generated response",
        "downstream trace",
        "observed outcome",
        "known correct answer",
        "complete return",
        "conversation credits",
        "intended behavior",
        "structural constraints",
    ):
        assert field in first_evaluation
    assert result.usage.model_calls == len(backward.calls) + 1
    assert result.usage.total_tokens == 15 * result.usage.model_calls
    assert result.metadata["gradient_memory"] == 0
    assert result.metadata["momentum"] == 0


@requires_textgrad
def test_real_textgrad_generation_failure_returns_attempt_audit_without_proposal() -> None:
    backward = LocalTextModel(fail=True)
    optimizer = LocalTextModel()
    proposer = TextGradPromptProposer(backward, optimizer, config=configuration())  # type: ignore[arg-type]
    spec = seed_prompt_collection().components["role:S_T"]
    result = asyncio.run(proposer.propose(spec, [occurrence()], iteration_id="cycle-1:attempt-1"))
    assert result.template is None
    assert "local recorded failure" in (result.error or "")
    assert len(result.attempts) == result.usage.model_calls == 1
    assert result.attempts[0].error
    assert optimizer.calls == []


@requires_textgrad
def test_real_textgrad_overall_timeout_cancels_inflight_engine_and_preserves_usage() -> None:
    class SlowModel(LocalTextModel):
        cancelled = False

        async def invoke(self, **kwargs: Any) -> Any:
            self.calls.append(kwargs)
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                self.cancelled = True
                raise

    backward = SlowModel()
    optimizer = LocalTextModel()
    proposer = TextGradPromptProposer(  # type: ignore[arg-type]
        backward, optimizer, config=configuration(proposal_timeout_seconds=0.05)
    )
    result = asyncio.run(
        proposer.propose(
            seed_prompt_collection().components["role:S_T"], [occurrence()], iteration_id="cycle-1:attempt-1"
        )
    )
    assert result.template is None
    assert "TimeoutError" in (result.error or "")
    assert backward.cancelled
    assert result.usage.model_calls == 1
    assert result.attempts[0].error
    assert optimizer.calls == []


@requires_textgrad
def test_gpt_mini_all_textgrad_roles_use_real_gateway_zero_temperature_and_reference_rate():
    import json
    import httpx
    from examples.adaptive_multi_agent_collab.providers.gateway import COLLABORATION_MODELS, build_openrouter_model
    from examples.adaptive_multi_agent_collab.providers.adapter import GenerationRequest, OpenJiuwenAgent
    from examples.adaptive_multi_agent_collab.providers.resources import ResourceController
    from .test_gateway import binding, frame, sse
    from .test_resources import Counter, controller

    collection = seed_prompt_collection()
    spec = collection.components["role:S_T"]
    candidate = spec.template + "\nState the relevant premise."
    sent = []
    tg_requests = []

    def transport(request):
        body = json.loads(request.content)
        sent.append(body)
        if body["temperature"] == 0:
            tg_requests.append(body)
            content = (
                f"<IMPROVED_VARIABLE>{candidate}</IMPROVED_VARIABLE>"
                if len(tg_requests) == 3
                else "Clarify the premise."
            )
        else:
            content = '{"answer":"yes","explanation":""}'
        return httpx.Response(
            200,
            text=sse(
                [
                    frame(content, finish="stop", native="stop", model=body["model"]),
                    {
                        "choices": [],
                        "usage": {
                            "prompt_tokens": 40,
                            "completion_tokens": 10,
                            "cost": 0.000009,
                            "is_byok": False,
                            "prompt_tokens_details": {"cached_tokens": 0},
                            "completion_tokens_details": {"reasoning_tokens": 0},
                        },
                    },
                ]
            ),
        )

    ctl = controller()
    mini_profile = replace(
        ctl.profile,
        reference_input_price=0.4 / 1e6,
        reference_output_price=1.6 / 1e6,
        reference_pricing_date="2026-09-18",
    )
    mini = ResourceController(mini_profile, Counter(), ctl.ledger, limits=ctl.limits)
    sonnet = ResourceController(
        replace(mini_profile, reference_input_price=3 / 1e6, reference_output_price=15 / 1e6),
        Counter(),
        ctl.ledger,
        limits=ctl.limits,
    )
    engine = build_openrouter_model(
        binding(), "offline-fixture", temperature=0, transport=httpx.MockTransport(transport)
    )
    agent1 = build_openrouter_model(
        binding(), "offline-fixture", temperature=0.7, transport=httpx.MockTransport(transport)
    )
    agent3 = build_openrouter_model(
        binding(COLLABORATION_MODELS[2]), "offline-fixture", temperature=0.7, transport=httpx.MockTransport(transport)
    )
    config = TextGradConfig(
        COLLABORATION_MODELS[0],
        "fixture-binding",
        COLLABORATION_MODELS[0],
        "fixture-binding",
        backward_decoding=(("temperature", 0),),
        optimizer_decoding=(("temperature", 0),),
        backward_supports_seed=True,
        optimizer_supports_seed=True,
    )
    proposer = TextGradPromptProposer(engine, engine, config=config, resources={"backward": mini, "optimizer": mini})

    async def exercise():
        first = await OpenJiuwenAgent(agent1, resources=mini).generate(GenerationRequest("initial", "synthetic", 0))
        proposal = await proposer.propose(spec, [occurrence()], iteration_id="gpt-mini-contract")
        third = await OpenJiuwenAgent(agent3, resources=sonnet).generate(GenerationRequest("initial", "synthetic", 2))
        return first, proposal, third

    first, proposal, third = asyncio.run(exercise())
    assert first.data and third.data and proposal.error is None and proposal.template == candidate
    assert len(tg_requests) == 3
    assert [attempt.stage for attempt in proposal.attempts] == ["evaluation", "backward", "proposal"]
    assert all(body["model"] == COLLABORATION_MODELS[0] and body["temperature"] == 0 for body in tg_requests)
    assert all("reasoning" not in body and "thinking" not in body for body in tg_requests)
    assert sent[0]["temperature"] == 0.7 and sent[-1]["temperature"] == 0.7
    assert sent[-1]["model"] == COLLABORATION_MODELS[2] and "seed" not in sent[-1]
    assert proposer.resources["backward"].counter is proposer.resources["optimizer"].counter
    assert all(item.usage.monetary_cost == pytest.approx((40 * 0.4 + 10 * 1.6) / 1e6) for item in proposal.attempts)
    assert all(item.usage.paid_monetary_cost == 0.000009 for item in proposal.attempts)
    assert ctl.ledger.entries[-1]["usage"]["reference_cost"] == pytest.approx((40 * 3 + 10 * 15) / 1e6)
    assert collection.components[spec.key].template == spec.template
    assert proposer.metadata()["resource_profiles"]["optimizer"]["reference_input_price"] == 0.4 / 1e6


@requires_textgrad
def test_disposable_compatibility_graph_cannot_accept_validation_evidence_or_commit_prompt():
    model = LocalTextModel()
    collection = seed_prompt_collection()
    spec = collection.components["role:S_T"]
    optimizer = LocalTextModel(f"<IMPROVED_VARIABLE>{spec.template}\nClarify.</IMPROVED_VARIABLE>")
    proposer = TextGradPromptProposer(model, optimizer, config=configuration())
    with pytest.raises(ValueError, match="synthetic"):
        asyncio.run(proposer.propose_compatibility(spec, [occurrence(split="comp-val")], iteration_id="compat"))
    result = asyncio.run(proposer.propose_compatibility(spec, [occurrence(split="synthetic")], iteration_id="compat"))
    assert result.error is None and len(result.attempts) == 3
    assert collection.components[spec.key].template == spec.template
    with pytest.raises(ValueError, match="training"):
        asyncio.run(proposer.propose(spec, [occurrence(split="synthetic")], iteration_id="training"))
