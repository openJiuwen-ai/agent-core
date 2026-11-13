"""Real runtime/adapter/resource boundaries with local public-model fixtures."""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
from dataclasses import replace
from typing import Any

import pytest

from examples.adaptive_multi_agent_collab.providers.adapter import OpenJiuwenAgent
from examples.adaptive_multi_agent_collab.config import CostConfig
from examples.adaptive_multi_agent_collab.workflows.prompts import seed_prompt_collection
from examples.adaptive_multi_agent_collab.infrastructure.randomness import GenerationContext, LogicalCallIdentity
from examples.adaptive_multi_agent_collab.workflows.repair import REPAIR_DIAGNOSTICS, render_repair
from examples.adaptive_multi_agent_collab.workflows.runtime import Runtime, normalized_cost
from examples.adaptive_multi_agent_collab.schemas import QueryExample, Scheme
from examples.adaptive_multi_agent_collab.learning.textgrad_optimizer import TextGradPromptProposer
from examples.adaptive_multi_agent_collab.tests.test_resources import Model, controller, response
from examples.adaptive_multi_agent_collab.tests.test_runtime import _matrix, _runtime
from examples.adaptive_multi_agent_collab.tests.test_textgrad_optimizer import configuration, occurrence


def integrated(responses: dict[int, list[Any]], active: dict[tuple[int, int], Scheme] | None = None):
    count = len(responses)
    base, _ = _runtime(count, {}, {1: _matrix(count, active)})
    shared = controller()
    models = {agent: Model(values) for agent, values in responses.items()}
    agents = {
        agent: OpenJiuwenAgent(model, resources=controller(ledger=shared.ledger), timeout=120, supports_seed=True)
        for agent, model in models.items()
    }
    runtime = Runtime(base.config, agents, base.routing_policy, base.weighting_model)
    return runtime, models, shared.ledger


def qa() -> QueryExample:
    return QueryExample("request-fixture", "Can this synthetic proposition be affirmed?", ("yes", "no"), "yes")


def test_runtime_public_boundary_initial_schema_repair_seed_caps_and_fixed_protocol() -> None:
    def exercise():
        runtime, models, ledger = integrated(
            {
                0: [response("invalid JSON"), response('{"answer":" YES. ","explanation":""}')],
                1: [response('{"answer":"no","explanation":""}')],
            }
        )
        runtime.config = replace(
            runtime.config,
            agent_pool=tuple(
                replace(
                    item,
                    system_configuration=f"logical identity {item.agent_id}",
                    role_configuration="independent reasoner",
                )
                for item in runtime.config.agent_pool
            ),
        )
        context = GenerationContext(31, "comp-val", qa().id)
        trajectory = asyncio.run(runtime.run(qa(), generation_context=context))
        return trajectory, models, ledger, context

    first, models, ledger, context = exercise()
    second, repeated, _, _ = exercise()
    assert first.completed and second.completed and first.initial_answers == ("yes", "no")
    assert first.initial_usage.model_calls == 3 and first.sessions[0].usage.model_calls == 0
    assert len(models[0].calls) == 2 and len(models[1].calls) == 1
    for attempt, call in enumerate(models[0].calls):
        assert call["seed"] == context.seed_for(LogicalCallIdentity(0, 0, "initial", "initial", 0, attempt))
        assert call["seed"] == repeated[0].calls[attempt]["seed"]
        assert call["max_tokens"] == 256
        assert [message["role"] for message in call["messages"]] == ["system", "user"]
        assert call["messages"][0]["content"].count("Fixed protocol:") == 1
        assert "logical identity 0" in call["messages"][0]["content"]
    assert "invalid JSON" not in models[0].calls[1]["messages"][1]["content"]
    assert models[0].calls[1]["messages"][1]["content"] == render_repair(
        models[0].calls[0]["messages"][1]["content"], REPAIR_DIAGNOSTICS["json"]
    )
    assert first.initial_records[0].attempts[0].raw_output == "invalid JSON"
    assert models[0].calls[0]["seed"] != models[0].calls[1]["seed"]
    assert len(ledger.entries) == 3 and all(item["category"] == "initialization" for item in ledger.entries)
    assert first.usage.monetary_cost == pytest.approx(sum(item["charged"] for item in ledger.entries))
    assert len(first.initial_records[0].attempts) == 2


def test_runtime_oversized_complete_initial_input_fails_before_any_dispatch() -> None:
    runtime, models, ledger = integrated({0: [], 1: []})
    runtime.config = replace(
        runtime.config,
        agent_pool=tuple(
            replace(item, system_configuration="fixed instructions " * 1024) if item.agent_id == 0 else item
            for item in runtime.config.agent_pool
        ),
    )
    trajectory = asyncio.run(runtime.run(qa()))
    assert trajectory.status == "initialization-failed" and not trajectory.completed
    assert trajectory.initial_answers == () and trajectory.final_answer is None
    assert trajectory.usage.model_calls == 0 and trajectory.usage.monetary_cost == 0
    assert not ledger.entries and all(not model.calls for model in models.values())
    assert "input-token-limit" in trajectory.error
    assert len(trajectory.call_records) == 1


def test_runtime_dispatched_ambiguous_failure_invalidates_required_usage_and_keeps_reservation() -> None:
    runtime, models, ledger = integrated(
        {0: [response()], 1: [response(), TimeoutError("unknown provider completion")]}, {(0, 1): Scheme.REVIEWER}
    )
    trajectory = asyncio.run(runtime.run(qa()))
    assert not trajectory.completed and trajectory.status == "accounting-failed"
    assert trajectory.final_answer is None and not trajectory.accounting_complete
    assert trajectory.usage.input_tokens is None and trajectory.usage.output_tokens is None
    assert trajectory.usage.monetary_cost is None and trajectory.usage.model_calls == 3
    assert len(models[1].calls) == 2 and len(ledger.entries) == 3
    assert ledger.entries[-1]["charged"] is None and ledger.entries[-1]["reserved"] > 0
    assert trajectory.failed_session_traces[0].terminal is None
    assert trajectory.failed_session_traces[0].messages[0].answer == "yes"
    assert trajectory.call_records[-1]["usage"]["input_tokens"] is None
    assert trajectory.call_records[-1]["physical_dispatch_ids"] == [2]


def test_shared_judge_and_modal_tie_charge_each_physical_generation_once() -> None:
    initial = lambda answer: response(json.dumps({"answer": answer, "explanation": f"evidence {answer}"}))
    runtime, models, ledger = integrated(
        {
            0: [initial("A"), response('{"feedback":"supported","status":"complete"}'), initial("B")],
            1: [initial("B")],
            2: [initial("C"), initial("B")],
        },
        {(0, 0): Scheme.SELF, (0, 2): Scheme.JUDGE, (1, 2): Scheme.JUDGE},
    )
    runtime.config = replace(
        runtime.config,
        costs=CostConfig(normalization=1, input_token_weight=0, output_token_weight=0, model_call_weight=1),
    )
    example = QueryExample("judged", "Choose supported evidence.", ("A", "B", "C"), "B")
    trajectory = asyncio.run(runtime.run(example, generation_context=GenerationContext(29, "sys-val", example.id)))
    assert trajectory.completed and trajectory.usage.model_calls == len(ledger.entries) == 6
    session = trajectory.sessions[0]
    assert session.usage.model_calls == 3 and session.normalized_cost == 3
    judged = [trace for trace in session.traces if trace.tag.scheme is Scheme.JUDGE]
    assert len(judged) == 2 and sum(trace.normalized_cost for trace in judged) == 1
    assert session.tie_outputs[0].answer == "B"
    assert normalized_cost(session.tie_outputs[0].usage, runtime.config.costs) == 1
    assert len(models[2].calls) == 2 and len(models[0].calls) == 3
    assert trajectory.usage.monetary_cost == pytest.approx(sum(item["charged"] for item in ledger.entries))
    assert sum(item["category"] == "collaboration" for item in ledger.entries) == 3


class SeededTextModel:
    def __init__(self, candidate: str | None = None) -> None:
        self.candidate = candidate
        self.calls: list[dict[str, Any]] = []

    async def invoke(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        stable = json.dumps({"messages": kwargs["messages"], "seed": kwargs["seed"]}, sort_keys=True)
        digest = hashlib.sha256(stable.encode()).hexdigest()
        content = (
            f"<IMPROVED_VARIABLE>{self.candidate}</IMPROVED_VARIABLE>"
            if self.candidate is not None
            else f"Address the reusable premise; feedback identity {digest}."
        )
        return response(content)


def test_different_textgrad_tokenizers_require_recorded_expansion_before_any_call() -> None:
    backward, optimizer = SeededTextModel(), SeededTextModel()
    first = controller()
    second = controller(ledger=first.ledger)
    second.profile = replace(second.profile, tokenizer_name="another-fixture-tokenizer")
    with pytest.raises(ValueError, match="cross_tokenizer_expansion"):
        TextGradPromptProposer(
            backward, optimizer, config=configuration(), resources={"backward": first, "optimizer": second}
        )
    allowed = TextGradPromptProposer(
        backward,
        optimizer,
        config=configuration(cross_tokenizer_expansion=2),
        resources={"backward": first, "optimizer": second},
    )
    assert allowed.metadata()["cross_tokenizer_expansion"] == 2
    assert not backward.calls and not optimizer.calls and not first.ledger.entries


@pytest.mark.skipif(importlib.util.find_spec("textgrad") is None, reason="actual pinned TextGrad package unavailable")
def test_two_real_textgrad_graphs_replay_identical_seeded_engine_inputs() -> None:
    spec = seed_prompt_collection().components["role:S_T"]
    candidate = spec.template + "\nExplain the missing premise."
    evidence = [occurrence(example_id=f"query-{index}", generation_id=f"generation-{index}") for index in range(3)]

    async def exercise():
        backward, optimizer = SeededTextModel(), SeededTextModel(candidate)
        ctl = controller()
        proposer = TextGradPromptProposer(
            backward,
            optimizer,
            config=configuration(backward_supports_seed=True, optimizer_supports_seed=True),
            resources={"backward": ctl, "optimizer": ctl},
        )
        proposal = await proposer.propose(spec, evidence, iteration_id="phase-1:iteration-2")
        return proposal, backward.calls, optimizer.calls, ctl.ledger.entries

    first, first_backward, first_optimizer, first_ledger = asyncio.run(exercise())
    second, second_backward, second_optimizer, second_ledger = asyncio.run(exercise())
    assert first.error is None and second.error is None and first.template == second.template == candidate
    assert first.metadata["selected_occurrence_indices"] == second.metadata["selected_occurrence_indices"]
    assert len(first.metadata["selected_occurrence_indices"]) == 2
    assert len(first_optimizer) == len(second_optimizer) == 1
    for left, right in zip(first_backward + first_optimizer, second_backward + second_optimizer, strict=True):
        assert left["messages"] == right["messages"] and left["seed"] == right["seed"]
        assert left["max_tokens"] == right["max_tokens"] == 1024
    assert first.textual_gradient.critique == second.textual_gradient.critique
    assert len(first_ledger) == len(second_ledger) == first.usage.model_calls
