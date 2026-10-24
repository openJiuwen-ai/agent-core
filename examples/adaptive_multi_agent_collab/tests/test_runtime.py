"""Offline integration tests for the asynchronous collaboration runtime."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

import pytest
import torch

import examples.adaptive_multi_agent_collab.workflows.runtime as runtime_module
from examples.adaptive_multi_agent_collab.providers.adapter import DeterministicFakeAgent
from examples.adaptive_multi_agent_collab.config import (
    CapacityConfig,
    CostConfig,
    Config,
    LogicalAgentConfig,
    NetworkConfig,
    SafetyConfig,
    TerminationConfig,
)
from examples.adaptive_multi_agent_collab.models.encoders import (
    FixedBaseEncoders,
    OfflineTextEncoder,
    offline_encoder_configuration,
    serialize_query,
    text_payload,
)
from examples.adaptive_multi_agent_collab.models.policies import (
    RoutingPolicy,
    RoutingRolloutIdentity,
    WeightingModel,
)
from examples.adaptive_multi_agent_collab.infrastructure.randomness import GenerationContext
from examples.adaptive_multi_agent_collab.workflows.runtime import (
    AgentMailbox,
    Runtime,
    ScriptedRoutingPolicy,
    _WorkItem,
    normalized_cost,
    run_fake_smoke,
)
from examples.adaptive_multi_agent_collab.schemas import (
    ConversationStatus,
    ConversationTag,
    ConversationTrace,
    AgentCallKind,
    AgentOutput,
    AgentProtocol,
    AgentRequest,
    JudgeOutput,
    MessageFormat,
    QueryExample,
    RoutingAction,
    RoutingMode,
    RoleOutput,
    SafeguardDiagnostics,
    SafeguardTrigger,
    Scheme,
    SessionStartState,
    StructuredMessage,
    TerminalRecord,
    TrimmedAnswerCanonicalizer,
    UsageRecord,
)


def _offline_config(**overrides: Any) -> Config:
    """Keep runtime integration tests explicit about their local text fixture."""

    return Config(
        **{
            "encoders": offline_encoder_configuration(),
            "network": NetworkConfig(
                representation_dim=16,
                feedforward_dim=32,
                answer_byte_dim=8,
                transformer_layers=1,
                transformer_heads=2,
            ),
            **overrides,
        }
    )


def _weighting(config: Config) -> WeightingModel:
    return WeightingModel(
        config.n_agents,
        FixedBaseEncoders(config.n_agents, configuration=config.encoders).history_dimension,
        configuration=config.network,
    )


def _matrix(n_agents: int, active: Mapping[tuple[int, int], Scheme] | None = None) -> dict[tuple[int, int], Scheme]:
    result = {(initiator, receiver): Scheme.ZERO for initiator in range(n_agents) for receiver in range(n_agents)}
    result.update(active or {})
    return result


def _runtime(
    n_agents: int,
    scripts: Mapping[int, Mapping[str, Any]],
    matrices: Mapping[int, dict[tuple[int, int], Scheme]],
    *,
    termination: TerminationConfig | None = None,
    safety: SafetyConfig | None = None,
    costs: CostConfig | None = None,
    objective_scores: Mapping[int, float] | None = None,
) -> tuple[Runtime, dict[int, DeterministicFakeAgent]]:
    config = _offline_config(
        n_agents=n_agents,
        capacity=CapacityConfig(outgoing=n_agents, incoming=n_agents),
        safety=safety or SafetyConfig(),
        costs=costs or CostConfig(),
        termination=termination or TerminationConfig(min_sessions=1, max_sessions=1),
    )
    agents = {
        agent_id: DeterministicFakeAgent(
            agent_id,
            scripts.get(agent_id, {}),
            default_answer=chr(ord("A") + agent_id),
        )
        for agent_id in config.agent_ids
    }
    routing = ScriptedRoutingPolicy(
        n_agents,
        dict(matrices),
        objective_scores=dict(objective_scores or {}),
    )
    torch.manual_seed(101)
    weighting = _weighting(config)
    return Runtime(config, agents, routing, weighting), agents


def _example(candidates: tuple[str, ...] = ("A", "B", "C")) -> QueryExample:
    return QueryExample("offline", "Choose a synthetic answer.", candidates, candidates[0])


def _terminal_trace(record: TerminalRecord) -> ConversationTrace:
    assert record.tag is not None
    trace = ConversationTrace(
        record.tag,
        messages=[
            StructuredMessage(
                MessageFormat.INITIATION,
                record.tag.initiator_id,
                record.tag.receiver_id,
                record.session,
                0,
                record.tag,
                answer=record.answer,
                explanation="recorded evidence",
            )
        ],
    )
    trace.finish(record)
    return trace


def _state(n_agents: int, *, session: int = 1) -> SessionStartState:
    answers = tuple(chr(ord("A") + agent) for agent in range(n_agents))
    return SessionStartState(session, _example(), (), answers)


def _action(n_agents: int, active: Mapping[tuple[int, int], Scheme]) -> RoutingAction:
    return RoutingAction(session=1, matrix=_matrix(n_agents, active), mode=RoutingMode.INFERENCE)


def _assert_attempt_accounting(output: object, runtime: Runtime, count: int) -> None:
    attempts = output.attempts  # type: ignore[attr-defined]
    usage = output.usage  # type: ignore[attr-defined]
    assert len(attempts) == count
    assert usage.model_calls == count
    assert usage.input_tokens == sum(attempt.usage.input_tokens for attempt in attempts)
    assert usage.output_tokens == sum(attempt.usage.output_tokens for attempt in attempts)
    assert usage.total_tokens == sum(attempt.usage.total_tokens for attempt in attempts)
    assert usage.monetary_cost == pytest.approx(sum(attempt.usage.monetary_cost for attempt in attempts))
    assert usage.wall_seconds == pytest.approx(sum(attempt.usage.wall_seconds for attempt in attempts))
    assert len(usage.raw_metadata["attempts"]) == count
    assert all(attempt.raw_output for attempt in attempts)
    assert all(attempt.usage.model_calls == 1 for attempt in attempts)
    assert all(
        attempt.normalized_cost == pytest.approx(normalized_cost(attempt.usage, runtime.config.costs))
        for attempt in attempts
    )
    assert sum(attempt.normalized_cost for attempt in attempts) == pytest.approx(
        normalized_cost(usage, runtime.config.costs)
    )


def _init_message(delivery: int) -> _WorkItem:
    tag = ConversationTag(1, 0, 1, Scheme.REVIEWER)
    message = StructuredMessage(
        MessageFormat.INITIATION,
        sender_id=0,
        receiver_id=1,
        session=1,
        round_index=0,
        tag=tag,
        answer="A",
    )
    return _WorkItem(delivery, message=message)


class _ProbeBackend:
    """Shared offline backend that exposes scheduling and identity behavior."""

    def __init__(
        self,
        *,
        latency: float = 0.01,
        failing_agents: frozenset[int] = frozenset(),
    ) -> None:
        self.latency = latency
        self.failing_agents = failing_agents
        self.calls: list[AgentRequest] = []
        self.active = 0
        self.max_active = 0
        self.active_by_agent: dict[int, int] = {}
        self.max_active_by_agent: dict[int, int] = {}

    async def invoke(self, request: AgentRequest):
        self.calls.append(request)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.active_by_agent[request.agent_id] = self.active_by_agent.get(request.agent_id, 0) + 1
        self.max_active_by_agent[request.agent_id] = max(
            self.max_active_by_agent.get(request.agent_id, 0),
            self.active_by_agent[request.agent_id],
        )
        try:
            await asyncio.sleep(self.latency)
            if request.agent_id in self.failing_agents:
                raise RuntimeError(f"probe failure for agent {request.agent_id}")
            usage = UsageRecord(
                input_tokens=1,
                output_tokens=1,
                model_calls=1,
                raw_metadata={"agent_id": request.agent_id, "kind": request.kind.value},
            )
            if request.kind is AgentCallKind.ROLE:
                structured = {"status": "complete", "feedback": "probe complete"}
                return RoleOutput(
                    "probe complete",
                    ConversationStatus.COMPLETE,
                    raw_content='{"status":"complete","feedback":"probe complete"}',
                    structured_output=structured,
                    usage=usage,
                    prompt_component=request.prompt_component,
                )
            if request.kind is AgentCallKind.JUDGE:
                answer = request.context["allowed_submitted_answers"][0]
                structured = {"answer": answer, "explanation": "probe adjudication"}
                return JudgeOutput(
                    answer,
                    "probe adjudication",
                    raw_content=str(structured),
                    structured_output=structured,
                    usage=usage,
                    prompt_component=request.prompt_component,
                )
            answer = chr(ord("A") + request.agent_id)
            structured = {"answer": answer, "explanation": "probe answer"}
            return AgentOutput(
                answer,
                "probe answer",
                raw_content=str(structured),
                structured_output=structured,
                usage=usage,
                prompt_component=request.prompt_component,
            )
        finally:
            self.active -= 1
            self.active_by_agent[request.agent_id] -= 1


def test_normalized_cost_uses_configured_token_call_and_monetary_terms() -> None:
    usage = UsageRecord(
        input_tokens=30,
        output_tokens=20,
        model_calls=2,
        monetary_cost=0.6,
    )
    config = CostConfig(
        normalization=100,
        input_token_weight=2,
        output_token_weight=4,
        model_call_weight=75,
        monetary_weight=250,
    )
    assert normalized_cost(usage, config) == pytest.approx((2 * 30 + 4 * 20 + 75 * 2 + 250 * 0.6) / 100)


def test_initial_answers_are_independent_and_prompt_excludes_gold_history_and_metadata() -> None:
    runtime, agents = _runtime(
        2,
        {
            0: {"initial": {"answer": "A", "explanation": "independent zero"}},
            1: {"initial": {"answer": "B", "explanation": "independent one"}},
        },
        {1: _matrix(2)},
    )
    example = QueryExample(
        "private-id",
        "Choose one.",
        ("A", "B"),
        "B",
        {"gold_reason": "DO_NOT_LEAK", "history": "DO_NOT_LEAK"},
    )
    outputs = asyncio.run(runtime._initial_answers(example))

    assert tuple(output.answer for output in outputs) == ("A", "B")
    prompts = [agents[agent].calls[0].prompt for agent in runtime.config.agent_ids]
    assert prompts[0] == prompts[1]
    assert "DO_NOT_LEAK" not in prompts[0]
    assert "private-id" not in prompts[0]
    assert "history" not in prompts[0].casefold()
    assert "independently" in agents[0].calls[0].context["protocol_instructions"]


def test_agent_mailbox_is_fifo_and_enforces_one_active_message() -> None:
    async def exercise() -> None:
        mailbox = AgentMailbox(1)
        await mailbox.deliver(_init_message(7))
        await mailbox.deliver(_init_message(3))

        first = await mailbox.begin()
        assert first is not None and first.delivery_index == 7
        assert mailbox.active
        mailbox.finish()
        second = await mailbox.begin()
        assert second is not None and second.delivery_index == 3
        mailbox.finish()
        assert mailbox.started_delivery_indices == [7, 3]
        assert not mailbox.active

        guarded = AgentMailbox(2)
        await guarded.deliver(_init_message(0))
        await guarded.deliver(_init_message(1))
        assert await guarded.begin() is not None
        with pytest.raises(RuntimeError, match="two messages concurrently"):
            await guarded.begin()
        guarded.finish()
        with pytest.raises(RuntimeError, match="while idle"):
            guarded.finish()

    asyncio.run(exercise())


@pytest.mark.parametrize(("concurrency_limit", "expected_overlap"), [(1, 1), (2, 2)])
def test_distinct_workers_overlap_only_up_to_global_semaphore_and_preserve_order(
    concurrency_limit: int,
    expected_overlap: int,
) -> None:
    n_agents = 4
    probe = _ProbeBackend(latency=0.02)
    agents = {agent: probe for agent in range(n_agents)}
    config = _offline_config(
        n_agents=n_agents,
        agent_pool=tuple(LogicalAgentConfig(agent, "shared-probe") for agent in range(n_agents)),
        capacity=CapacityConfig(n_agents, n_agents),
        safety=SafetyConfig(max_concurrent_model_calls=concurrency_limit),
        termination=TerminationConfig(min_sessions=1, max_sessions=1),
    )
    matrix = _matrix(
        n_agents,
        {(0, 2): Scheme.REVIEWER, (1, 3): Scheme.TEACHER},
    )
    runtime = Runtime(
        config,
        agents,
        ScriptedRoutingPolicy(n_agents, {1: matrix}),
        _weighting(config),
    )

    raw, traces, history, usage = asyncio.run(
        runtime._execute_session(
            _state(n_agents),
            _action(n_agents, {(0, 2): Scheme.REVIEWER, (1, 3): Scheme.TEACHER}),
            {agent: f"a{agent}" for agent in range(n_agents)},
        )
    )

    assert probe.max_active == expected_overlap
    assert all(maximum == 1 for maximum in probe.max_active_by_agent.values())
    assert {(call.agent_id, call.context["tag"]) for call in probe.calls} == {
        (2, str(ConversationTag(1, 0, 2, Scheme.REVIEWER))),
        (3, str(ConversationTag(1, 1, 3, Scheme.TEACHER))),
    }
    assert all(trace.completed for trace in traces)
    assert [record.answer for record in raw[0]] == ["A"]
    assert [record.answer for record in raw[1]] == ["B"]
    assert usage.model_calls == 2
    assert {output.attempts[0].usage.raw_metadata["agent_id"] for trace in traces for output in trace.outputs} == {2, 3}
    delivery_indices = [record.delivery_index for record in history if record.delivery_index is not None]
    assert delivery_indices == sorted(delivery_indices)
    assert delivery_indices == list(range(len(delivery_indices)))
    assert all(record.processing_index is not None for record in history if record.delivery_index is not None)
    assert all(
        mailbox.started_delivery_indices == sorted(mailbox.started_delivery_indices)
        for mailbox in runtime.last_mailboxes.values()
    )
    encoded = runtime.encoders.encode_history(history)
    assert encoded.history_indices == tuple(range(1, len(history) + 1))
    with pytest.raises(ValueError, match="consecutive monotone history indices"):
        runtime.encoders.encode_history(tuple(reversed(history)))


def test_shared_backend_failure_is_isolated_to_affected_trace() -> None:
    n_agents = 4
    probe = _ProbeBackend(latency=0.01, failing_agents=frozenset({2}))
    config = _offline_config(
        n_agents=n_agents,
        agent_pool=tuple(LogicalAgentConfig(agent, "shared-probe") for agent in range(n_agents)),
        capacity=CapacityConfig(n_agents, n_agents),
        termination=TerminationConfig(min_sessions=1, max_sessions=1),
    )
    active = {(0, 2): Scheme.REVIEWER, (1, 3): Scheme.TEACHER}
    matrix = _matrix(n_agents, active)
    runtime = Runtime(
        config,
        {agent: probe for agent in range(n_agents)},
        ScriptedRoutingPolicy(n_agents, {1: matrix}),
        _weighting(config),
    )

    runtime.config = replace(
        runtime.config,
        costs=CostConfig(normalization=1, input_token_weight=0, output_token_weight=0, model_call_weight=1),
    )
    raw, traces, _, usage = asyncio.run(
        runtime._execute_session(
            _state(n_agents),
            _action(n_agents, active),
            {agent: f"a{agent}" for agent in range(n_agents)},
        )
    )
    by_receiver = {trace.tag.receiver_id: trace for trace in traces}

    assert by_receiver[2].completion_status is ConversationStatus.SAFEGUARD_COMPLETE
    assert by_receiver[2].safeguard_trigger is SafeguardTrigger.WORKER_FAILURE
    assert by_receiver[2].error == "RuntimeError: probe failure for agent 2"
    assert by_receiver[3].completion_status is ConversationStatus.COMPLETE
    assert by_receiver[3].error is None
    assert raw[0][0].answer == "A" and raw[1][0].answer == "B"
    assert by_receiver[2].usage.model_calls == 1
    assert usage.model_calls == 2


def test_shared_backend_relation_is_validated_and_judge_batch_has_one_barrier_call() -> None:
    n_agents = 3
    probe = _ProbeBackend()
    pool = tuple(LogicalAgentConfig(agent, "shared") for agent in range(n_agents))
    config = _offline_config(
        n_agents=n_agents,
        agent_pool=pool,
        capacity=CapacityConfig(n_agents, n_agents),
        termination=TerminationConfig(min_sessions=1, max_sessions=1),
    )
    active = {(0, 2): Scheme.JUDGE, (1, 2): Scheme.JUDGE}
    matrix = _matrix(n_agents, active)
    runtime = Runtime(
        config,
        {agent: probe for agent in range(n_agents)},
        ScriptedRoutingPolicy(n_agents, {1: matrix}),
        _weighting(config),
    )

    _, traces, _, usage = asyncio.run(
        runtime._execute_session(
            _state(n_agents),
            _action(n_agents, active),
            {agent: f"a{agent}" for agent in range(n_agents)},
        )
    )

    judge_calls = [call for call in probe.calls if call.kind is AgentCallKind.JUDGE]
    assert len(judge_calls) == 1
    assert judge_calls[0].agent_id == 2
    assert len(judge_calls[0].context["tags"]) == 2
    assert len(traces) == 2 and all(trace.completed for trace in traces)
    assert traces[0].outputs[0] is traces[1].outputs[0]
    assert usage.model_calls == 1

    with pytest.raises(ValueError, match="must share backend objects"):
        Runtime(
            config,
            {agent: _ProbeBackend() for agent in range(n_agents)},
            ScriptedRoutingPolicy(n_agents, {1: matrix}),
            _weighting(config),
        )

    distinct_config = _offline_config(
        n_agents=n_agents,
        capacity=CapacityConfig(n_agents, n_agents),
    )
    with pytest.raises(ValueError, match="must use distinct backend objects"):
        Runtime(
            distinct_config,
            {agent: probe for agent in range(n_agents)},
            ScriptedRoutingPolicy(n_agents, {1: matrix}),
            _weighting(distinct_config),
        )


def test_complete_runtime_keeps_logical_agents_distinct_on_one_shared_backend() -> None:
    n_agents = 3
    probe = _ProbeBackend(latency=0.001)
    config = _offline_config(
        n_agents=n_agents,
        agent_pool=tuple(
            LogicalAgentConfig(
                agent,
                "shared-probe",
                model_name="offline-probe",
                model_revision="1",
                role_configuration=f"logical-role-{agent}",
                decoding_configuration=(("temperature", "0"),),
            )
            for agent in range(n_agents)
        ),
        capacity=CapacityConfig(n_agents, n_agents),
        termination=TerminationConfig(min_sessions=1, max_sessions=1),
    )
    active = {(0, 2): Scheme.REVIEWER, (1, 2): Scheme.TEACHER}
    runtime = Runtime(
        config,
        {agent: probe for agent in range(n_agents)},
        ScriptedRoutingPolicy(n_agents, {1: _matrix(n_agents, active)}),
        _weighting(config),
    )

    trajectory = asyncio.run(runtime.run(QueryExample("shared-full", "Choose.", ("A", "B", "C"), "A")))
    session = trajectory.sessions[0]
    initial_requests = [request for request in probe.calls if request.kind is AgentCallKind.INITIAL]
    role_requests = [request for request in probe.calls if request.kind is AgentCallKind.ROLE]

    assert {request.agent_id for request in initial_requests} == {0, 1, 2}
    assert tuple(output.answer for output in trajectory.initial_outputs) == ("A", "B", "C")
    assert {(request.agent_id, request.context["tag"]) for request in role_requests} == {
        (2, str(ConversationTag(1, 0, 2, Scheme.REVIEWER))),
        (2, str(ConversationTag(1, 1, 2, Scheme.TEACHER))),
    }
    assert len({trace.tag for trace in session.traces}) == 2
    assert all(trace.outputs[0].structured_output["status"] == "complete" for trace in session.traces)
    assert all(trace.usage.model_calls == 1 for trace in session.traces)
    assert runtime.last_mailboxes[2].started_delivery_indices == [0, 1]
    assert session.usage.model_calls == 2
    assert trajectory.usage.model_calls == 5


def test_runtime_rejects_injected_encoder_metadata_that_differs_from_config() -> None:
    config = _offline_config(encoders=offline_encoder_configuration(serialization_version="configured-query"))
    agents = {agent: DeterministicFakeAgent(agent) for agent in config.agent_ids}
    routing = ScriptedRoutingPolicy(config.n_agents, {1: _matrix(config.n_agents)})
    weighting = _weighting(config)
    incompatible = FixedBaseEncoders(config.n_agents, configuration=offline_encoder_configuration())

    with pytest.raises(ValueError, match="injected fixed encoder metadata"):
        Runtime(
            config,
            agents,
            routing,
            weighting,
            encoders=incompatible,
        )


def test_nonjudge_continue_revision_complete_flow_is_tagged_and_fifo() -> None:
    runtime, agents = _runtime(
        2,
        {
            0: {"revision": {"answer": "B", "explanation": "revised"}},
            1: {
                "role": [
                    {"status": "continue", "feedback": "reconsider"},
                    {"status": "complete", "feedback": "accepted"},
                ]
            },
        },
        {1: _matrix(2, {(0, 1): Scheme.REVIEWER})},
    )
    state = _state(2)
    action = _action(2, {(0, 1): Scheme.REVIEWER})
    raw, traces, history, usage = asyncio.run(runtime._execute_session(state, action, {0: "a0", 1: "a1"}))

    assert len(traces) == 1
    trace = traces[0]
    assert ((trace.tag.initiator_id, trace.tag.receiver_id),) == action.selected_edges
    assert trace.tag.scheme is action.matrix[action.selected_edges[0]]
    assert [message.format for message in trace.messages] == [
        MessageFormat.INITIATION,
        MessageFormat.ROLE_RESPONSE,
        MessageFormat.REVISION_RESPONSE,
        MessageFormat.ROLE_RESPONSE,
    ]
    assert [message.round_index for message in trace.messages] == [0, 1, 2, 3]
    assert all(message.tag == trace.tag for message in trace.messages)
    assert trace.completed and trace.terminal is not None and trace.terminal.answer == "B"
    assert [record.answer for record in raw[0]] == ["B"]
    assert raw[1] == ()
    assert [call.kind for call in agents[0].calls] == ["revision"]
    assert [call.kind for call in agents[1].calls] == ["role", "role"]
    assert all(agent.max_active == 1 for agent in agents.values())
    assert all(
        mailbox.started_delivery_indices == sorted(mailbox.started_delivery_indices)
        for mailbox in runtime.last_mailboxes.values()
    )
    assert [record.event for record in history] == [
        "initiation",
        "role-response",
        "revision-response",
        "role-response",
        "conversation-complete",
    ]
    assert usage.model_calls == 3


@pytest.mark.parametrize(
    ("round_limit", "expected_answer"),
    [(1, "A"), (2, "B"), (3, "B"), (4, "C")],
)
def test_continual_continue_stops_at_exact_internal_round_boundary(
    round_limit: int,
    expected_answer: str,
) -> None:
    runtime, agents = _runtime(
        2,
        {
            0: {
                "revision": [
                    {"answer": "B", "explanation": "first revision"},
                    {"answer": "C", "explanation": "second revision"},
                ]
            },
            1: {"role": [{"status": "continue", "feedback": "continue reviewing"} for _ in range(3)]},
        },
        {1: _matrix(2, {(0, 1): Scheme.REVIEWER})},
        safety=SafetyConfig(max_internal_rounds=round_limit),
    )
    raw, traces, history, usage = asyncio.run(
        runtime._execute_session(
            _state(2),
            _action(2, {(0, 1): Scheme.REVIEWER}),
            {0: "a0", 1: "a1"},
        )
    )

    trace = traces[0]
    assert [message.round_index for message in trace.messages] == list(range(round_limit + 1))
    assert max(message.round_index for message in trace.messages) == round_limit
    assert trace.completion_status is ConversationStatus.SAFEGUARD_COMPLETE
    assert trace.safeguard_reason == trace.error
    assert trace.error is not None
    assert f"max_internal_rounds={round_limit}" in trace.error
    assert f"at round {round_limit}" in trace.error
    assert trace.terminal is not None and trace.terminal.answer == expected_answer
    assert [record.answer for record in raw[0]] == [expected_answer]
    assert len(agents[1].calls) == (round_limit + 1) // 2
    assert len(agents[0].calls) == round_limit // 2
    if round_limit % 2:
        assert trace.messages[-1].format is MessageFormat.ROLE_RESPONSE
        assert trace.messages[-1].status is ConversationStatus.CONTINUE
    assert all(call.context["round"] <= round_limit for agent in agents.values() for call in agent.calls)
    assert usage.model_calls == round_limit
    assert len(trace.outputs) == round_limit
    assert len(history) == round_limit + 2
    assert history[-1].event == "conversation-complete"


def test_immediate_complete_records_current_answer_without_revision_call() -> None:
    runtime, agents = _runtime(
        2,
        {1: {"role": {"status": "complete", "feedback": "already correct"}}},
        {1: _matrix(2, {(0, 1): Scheme.TEACHER})},
        safety=SafetyConfig(max_internal_rounds=1),
    )
    raw, traces, _, usage = asyncio.run(
        runtime._execute_session(_state(2), _action(2, {(0, 1): Scheme.TEACHER}), {0: "a0", 1: "a1"})
    )

    assert [message.format for message in traces[0].messages] == [
        MessageFormat.INITIATION,
        MessageFormat.ROLE_RESPONSE,
    ]
    assert traces[0].terminal is not None and traces[0].terminal.answer == "A"
    assert traces[0].completion_status is ConversationStatus.COMPLETE
    assert traces[0].safeguard_reason is None
    assert raw[0][0].answer == "A"
    assert not agents[0].calls
    assert [call.kind for call in agents[1].calls] == ["role"]
    assert usage.model_calls == 1


def test_context_invalid_role_status_is_repaired_before_message_creation() -> None:
    runtime, agents = _runtime(
        2,
        {
            1: {
                "role": [
                    {"status": "halt", "feedback": "invalid status"},
                    {"status": "complete", "feedback": "valid repair"},
                ]
            }
        },
        {1: _matrix(2, {(0, 1): Scheme.REVIEWER})},
        safety=SafetyConfig(parse_retries=1),
    )
    raw, traces, history, usage = asyncio.run(
        runtime._execute_session(
            _state(2),
            _action(2, {(0, 1): Scheme.REVIEWER}),
            {0: "a0", 1: "a1"},
        )
    )

    trace = traces[0]
    assert [message.format for message in trace.messages] == [
        MessageFormat.INITIATION,
        MessageFormat.ROLE_RESPONSE,
    ]
    assert trace.messages[-1].status is ConversationStatus.COMPLETE
    assert trace.completion_status is ConversationStatus.COMPLETE
    assert trace.safeguard_reason is None
    assert trace.terminal is not None and trace.terminal.answer == "A"
    assert [record.answer for record in raw[0]] == ["A"]
    assert [call.kind for call in agents[1].calls] == ["role", "role"]
    assert "required fields or field types" in agents[1].calls[1].prompt
    output = trace.outputs[0]
    assert isinstance(output, RoleOutput)
    assert output.status is ConversationStatus.COMPLETE
    assert not output.validation_exhausted and output.error is None
    assert "status must be 'continue' or 'complete'" in output.parse_errors[0]
    assert usage.model_calls == 2
    assert [record.event for record in history] == ["initiation", "role-response", "conversation-complete"]
    _assert_attempt_accounting(output, runtime, 2)


def test_role_validation_exhaustion_uses_distinct_safeguard_completion() -> None:
    runtime, agents = _runtime(
        2,
        {
            1: {
                "role": [
                    {"status": "halt", "feedback": "invalid one"},
                    {"status": "halt", "feedback": "invalid two"},
                ]
            }
        },
        {1: _matrix(2, {(0, 1): Scheme.REVIEWER})},
        safety=SafetyConfig(parse_retries=1),
    )
    raw, traces, history, usage = asyncio.run(
        runtime._execute_session(
            _state(2),
            _action(2, {(0, 1): Scheme.REVIEWER}),
            {0: "a0", 1: "a1"},
        )
    )

    trace = traces[0]
    assert [message.format for message in trace.messages] == [MessageFormat.INITIATION]
    assert trace.completion_status is ConversationStatus.SAFEGUARD_COMPLETE
    assert trace.safeguard_reason == trace.error
    assert trace.error is not None and "validation exhausted after 2 attempt(s)" in trace.error
    assert trace.terminal is not None and trace.terminal.answer == "A"
    assert [record.answer for record in raw[0]] == ["A"]
    assert not agents[0].calls
    assert [call.kind for call in agents[1].calls] == ["role", "role"]
    output = trace.outputs[0]
    assert isinstance(output, RoleOutput)
    assert output.status is ConversationStatus.SAFEGUARD_COMPLETE
    assert output.validation_exhausted
    assert len(output.parse_errors) == 2
    assert usage.model_calls == 2
    assert [record.event for record in history] == ["initiation", "conversation-complete"]
    _assert_attempt_accounting(output, runtime, 2)


@pytest.mark.parametrize("repair_budget", [0, 1, 2])
def test_repair_budget_allows_exactly_initial_plus_configured_repairs(
    repair_budget: int,
) -> None:
    attempts = repair_budget + 1
    runtime, agents = _runtime(
        2,
        {1: {"role": [{"status": "halt", "feedback": f"invalid {index}"} for index in range(attempts)]}},
        {1: _matrix(2, {(0, 1): Scheme.REVIEWER})},
        safety=SafetyConfig(parse_retries=repair_budget),
    )
    _, traces, _, usage = asyncio.run(
        runtime._execute_session(
            _state(2),
            _action(2, {(0, 1): Scheme.REVIEWER}),
            {0: "a0", 1: "a1"},
        )
    )

    output = traces[0].outputs[0]
    assert len(agents[1].calls) == attempts
    assert output.validation_exhausted
    assert output.error is not None
    assert f"after {attempts} attempt(s)" in output.error
    assert usage.model_calls == attempts
    _assert_attempt_accounting(output, runtime, attempts)


def test_context_invalid_revision_answer_is_repaired_before_message_creation() -> None:
    runtime, agents = _runtime(
        2,
        {
            0: {
                "revision": [
                    {"answer": "Z", "explanation": "invalid revision"},
                    {"answer": "B", "explanation": "valid repair"},
                ]
            },
            1: {
                "role": [
                    {"status": "continue", "feedback": "revise"},
                    {"status": "complete", "feedback": "accepted"},
                ]
            },
        },
        {1: _matrix(2, {(0, 1): Scheme.REVIEWER})},
        safety=SafetyConfig(parse_retries=1),
    )
    raw, traces, history, usage = asyncio.run(
        runtime._execute_session(
            _state(2),
            _action(2, {(0, 1): Scheme.REVIEWER}),
            {0: "a0", 1: "a1"},
        )
    )

    trace = traces[0]
    assert [message.format for message in trace.messages] == [
        MessageFormat.INITIATION,
        MessageFormat.ROLE_RESPONSE,
        MessageFormat.REVISION_RESPONSE,
        MessageFormat.ROLE_RESPONSE,
    ]
    assert trace.messages[2].answer == "B"
    assert trace.completion_status is ConversationStatus.COMPLETE
    assert trace.safeguard_reason is None
    assert trace.terminal is not None and trace.terminal.answer == "B"
    assert [record.answer for record in raw[0]] == ["B"]
    assert [call.kind for call in agents[0].calls] == ["revision", "revision"]
    assert "not an admissible selection" in agents[0].calls[1].prompt
    revision_output = trace.outputs[1]
    assert isinstance(revision_output, AgentOutput)
    assert revision_output.answer == "B"
    assert not revision_output.validation_exhausted and revision_output.error is None
    assert "outside the dataset answer space" in revision_output.parse_errors[0]
    assert usage.model_calls == 4
    assert [record.event for record in history] == [
        "initiation",
        "role-response",
        "revision-response",
        "role-response",
        "conversation-complete",
    ]
    _assert_attempt_accounting(revision_output, runtime, 2)


def test_revision_validation_exhaustion_emits_no_revision_message() -> None:
    runtime, agents = _runtime(
        2,
        {
            0: {
                "revision": [
                    {"answer": "Z", "explanation": "invalid one"},
                    {"answer": "Z", "explanation": "invalid two"},
                ]
            },
            1: {"role": {"status": "continue", "feedback": "revise"}},
        },
        {1: _matrix(2, {(0, 1): Scheme.REVIEWER})},
        safety=SafetyConfig(parse_retries=1),
    )
    raw, traces, history, usage = asyncio.run(
        runtime._execute_session(
            _state(2),
            _action(2, {(0, 1): Scheme.REVIEWER}),
            {0: "a0", 1: "a1"},
        )
    )

    trace = traces[0]
    assert [message.format for message in trace.messages] == [
        MessageFormat.INITIATION,
        MessageFormat.ROLE_RESPONSE,
    ]
    assert MessageFormat.REVISION_RESPONSE not in {message.format for message in trace.messages}
    assert trace.completion_status is ConversationStatus.SAFEGUARD_COMPLETE
    assert trace.safeguard_reason == trace.error
    assert trace.error is not None and "validation exhausted after 2 attempt(s)" in trace.error
    assert trace.terminal is not None and trace.terminal.answer == "A"
    assert [record.answer for record in raw[0]] == ["A"]
    assert [call.kind for call in agents[0].calls] == ["revision", "revision"]
    assert [call.kind for call in agents[1].calls] == ["role"]
    revision_output = trace.outputs[1]
    assert isinstance(revision_output, AgentOutput)
    assert revision_output.answer == "Z"
    assert revision_output.validation_exhausted
    assert len(revision_output.parse_errors) == 2
    assert usage.model_calls == 3
    assert [record.event for record in history] == ["initiation", "role-response", "conversation-complete"]
    _assert_attempt_accounting(revision_output, runtime, 2)


def test_session_timeout_cancels_inflight_conversation_and_records_fallback() -> None:
    config = _offline_config(
        n_agents=2,
        capacity=CapacityConfig(2, 2),
        safety=SafetyConfig(session_timeout_seconds=0.01),
        termination=TerminationConfig(min_sessions=1, max_sessions=1),
    )
    agents = {
        0: DeterministicFakeAgent(0, default_answer="A"),
        1: DeterministicFakeAgent(
            1,
            {"role": {"status": "continue", "feedback": "slow"}},
            default_answer="B",
            latency=0.5,
        ),
    }
    routing = ScriptedRoutingPolicy(2, {1: _matrix(2, {(0, 1): Scheme.REVIEWER})})
    weighting = _weighting(config)
    runtime = Runtime(config, agents, routing, weighting)
    started = time.perf_counter()
    runtime.config = replace(
        runtime.config,
        costs=CostConfig(normalization=1, input_token_weight=0, output_token_weight=0, model_call_weight=1),
    )
    raw, traces, _, _ = asyncio.run(
        runtime._execute_session(_state(2), _action(2, {(0, 1): Scheme.REVIEWER}), {0: "a0", 1: "a1"})
    )

    assert time.perf_counter() - started < 0.2
    assert raw[0][0].answer == "A"
    assert traces[0].completed
    assert traces[0].error == "session ended by engineering timeout"
    assert traces[0].completion_status is ConversationStatus.SAFEGUARD_COMPLETE
    assert traces[0].safeguard_trigger is SafeguardTrigger.SESSION_TIMEOUT
    assert traces[0].safeguard_reason == traces[0].error
    assert traces[0].usage.model_calls == 1
    diagnostics = SafeguardDiagnostics.from_traces(traces)
    assert diagnostics.completed_conversations == 1
    assert diagnostics.safeguard_completions == 1
    assert diagnostics.session_timeout_completions == 1
    assert diagnostics.safeguard_rate == 1.0
    assert diagnostics.safeguard_usage.model_calls == 1


def test_per_call_timeout_is_typed_charged_and_distinct_from_model_complete() -> None:
    runtime, _ = _runtime(
        2,
        {},
        {1: _matrix(2, {(0, 1): Scheme.REVIEWER})},
        safety=SafetyConfig(
            call_timeout_seconds=0.01,
            session_timeout_seconds=1.0,
        ),
    )
    runtime.agents[1] = DeterministicFakeAgent(1, latency=0.2)

    runtime.config = replace(
        runtime.config,
        costs=CostConfig(normalization=1, input_token_weight=0, output_token_weight=0, model_call_weight=1),
    )
    raw, traces, _, usage = asyncio.run(
        runtime._execute_session(
            _state(2),
            _action(2, {(0, 1): Scheme.REVIEWER}),
            {0: "a0", 1: "a1"},
        )
    )
    trace = traces[0]

    assert raw[0][0].answer == "A"
    assert trace.completion_status is ConversationStatus.SAFEGUARD_COMPLETE
    assert trace.safeguard_trigger is SafeguardTrigger.CALL_TIMEOUT
    assert trace.safeguard_reason == trace.error
    assert trace.error is not None and "call for agent 1 exceeded" in trace.error
    assert trace.usage.model_calls == 1
    assert usage.model_calls == 1
    diagnostics = SafeguardDiagnostics.from_traces(traces)
    assert diagnostics.call_timeout_completions == 1
    assert diagnostics.safeguard_usage.model_calls == 1


def test_session_timeout_retains_latest_valid_revised_answer_and_all_attempt_usage() -> None:
    class SlowSecondRole(_ProbeBackend):
        def __init__(self) -> None:
            super().__init__(latency=0.0)
            self.role_calls = 0

        async def invoke(self, request: AgentRequest):
            if request.kind is AgentCallKind.ROLE:
                self.role_calls += 1
                if self.role_calls == 1:
                    usage = UsageRecord(model_calls=1)
                    structured = {"status": "continue", "feedback": "revise"}
                    return RoleOutput(
                        "revise",
                        ConversationStatus.CONTINUE,
                        raw_content=str(structured),
                        structured_output=structured,
                        usage=usage,
                        prompt_component=request.prompt_component,
                    )
                await asyncio.sleep(0.2)
            return await super().invoke(request)

    config = _offline_config(
        n_agents=2,
        capacity=CapacityConfig(2, 2),
        safety=SafetyConfig(
            call_timeout_seconds=1.0,
            session_timeout_seconds=0.03,
        ),
        termination=TerminationConfig(min_sessions=1, max_sessions=1),
    )
    agents: dict[int, AgentProtocol] = {
        0: DeterministicFakeAgent(
            0,
            {"revision": {"answer": "B", "explanation": "latest valid revision"}},
        ),
        1: SlowSecondRole(),
    }
    active = {(0, 1): Scheme.REVIEWER}
    runtime = Runtime(
        config,
        agents,
        ScriptedRoutingPolicy(2, {1: _matrix(2, active)}),
        _weighting(config),
    )

    runtime.config = replace(
        runtime.config,
        costs=CostConfig(normalization=1, input_token_weight=0, output_token_weight=0, model_call_weight=1),
    )
    raw, traces, _, usage = asyncio.run(
        runtime._execute_session(
            _state(2),
            _action(2, active),
            {0: "a0", 1: "a1"},
        )
    )
    trace = traces[0]

    assert raw[0][0].answer == "B"
    assert trace.terminal is not None and trace.terminal.answer == "B"
    assert trace.completion_status is ConversationStatus.SAFEGUARD_COMPLETE
    assert trace.safeguard_trigger is SafeguardTrigger.SESSION_TIMEOUT
    assert trace.safeguard_reason == "session ended by engineering timeout"
    assert trace.usage.model_calls == 3
    assert usage.model_calls == 3


def test_shared_judge_uses_one_call_returns_to_each_initiator_and_splits_cost() -> None:
    runtime, agents = _runtime(
        3,
        {2: {"judge": {"answer": "B", "explanation": "shared decision"}}},
        {1: _matrix(3, {(0, 2): Scheme.JUDGE, (1, 2): Scheme.JUDGE})},
        safety=SafetyConfig(max_internal_rounds=1),
    )
    raw, traces, history, usage = asyncio.run(
        runtime._execute_session(
            _state(3),
            _action(3, {(0, 2): Scheme.JUDGE, (1, 2): Scheme.JUDGE}),
            {0: "a0", 1: "a1", 2: "a2"},
        )
    )

    judge_calls = [call for call in agents[2].calls if call.kind == "judge"]
    assert len(judge_calls) == 1
    assert len(traces) == 2
    assert all(trace.judge_group_size == 2 for trace in traces)
    assert all(
        [message.format for message in trace.messages] == [MessageFormat.INITIATION, MessageFormat.JUDGE_RETURN]
        for trace in traces
    )
    assert all(trace.outputs[0] is traces[0].outputs[0] for trace in traces)
    assert all(
        trace.completion_status is ConversationStatus.COMPLETE and trace.safeguard_reason is None for trace in traces
    )
    shared_output = traces[0].outputs[0]
    assert isinstance(shared_output, JudgeOutput)
    assert shared_output.answer == "B" and shared_output.error is None
    assert all(trace.error is None for trace in traces)
    assert all(trace.messages[-1].answer == "B" for trace in traces)
    assert all(trace.terminal is not None and trace.terminal.answer == "B" for trace in traces)
    assert judge_calls[0].context["allowed_submitted_answers"] == ("A", "B")
    assert "allowed_submitted_answers:" not in judge_calls[0].prompt
    assert judge_calls[0].context["allowed_submitted_answers"] == ("A", "B")
    assert "exactly one canonical submitted answer" in judge_calls[0].context["protocol_instructions"]
    assert [record.answer for record in raw[0]] == ["B"]
    assert [record.answer for record in raw[1]] == ["B"]
    assert raw[2] == ()
    assert sum(trace.normalized_cost for trace in traces) == pytest.approx(
        normalized_cost(traces[0].outputs[0].usage, runtime.config.costs)
    )
    assert traces[0].normalized_cost == pytest.approx(traces[1].normalized_cost)
    assert usage.model_calls == 1
    assert sum(record.event == "judge-adjudication" for record in history) == 1
    adjudication = next(record for record in history if record.event == "judge-adjudication")
    initiations = [record for record in history if record.event == "initiation"]
    returns = [record for record in history if record.event == "judge-return"]
    assert adjudication.delivery_index is not None
    assert all(record.delivery_index is not None for record in (*initiations, *returns))
    assert max(record.delivery_index for record in initiations) < adjudication.delivery_index
    assert adjudication.delivery_index < min(record.delivery_index for record in returns)


def test_judge_schema_and_context_failures_share_one_repair_budget() -> None:
    runtime, agents = _runtime(
        3,
        {
            2: {
                "judge": [
                    {"answer": "B"},
                    {"answer": "C", "explanation": "not submitted"},
                    {"answer": "A", "explanation": "valid repair"},
                ]
            }
        },
        {1: _matrix(3, {(0, 2): Scheme.JUDGE, (1, 2): Scheme.JUDGE})},
        safety=SafetyConfig(parse_retries=2),
    )
    state = SessionStartState(1, _example(), (), ("B", "A", "C"))
    raw, traces, _, usage = asyncio.run(
        runtime._execute_session(
            state,
            _action(3, {(0, 2): Scheme.JUDGE, (1, 2): Scheme.JUDGE}),
            {0: "b", 1: "a", 2: "c"},
        )
    )

    judge_calls = [call for call in agents[2].calls if call.kind == "judge"]
    assert len(judge_calls) == 3
    assert "required fields or field types" in judge_calls[1].prompt
    assert "not an admissible selection" in judge_calls[2].prompt
    shared_output = traces[0].outputs[0]
    assert isinstance(shared_output, JudgeOutput)
    assert shared_output.answer == "A"
    assert shared_output.error is None and not shared_output.validation_exhausted
    assert len(shared_output.parse_errors) == 2
    assert "explanation is required" in shared_output.parse_errors[0]
    assert "outside the allowed set" in shared_output.parse_errors[1]
    assert shared_output.attempts[0].structured_output is None
    assert shared_output.attempts[1].structured_output == {
        "answer": "C",
        "explanation": "not submitted",
    }
    assert [record.answer for record in raw[0]] == ["A"]
    assert [record.answer for record in raw[1]] == ["A"]
    assert usage.model_calls == 3
    _assert_attempt_accounting(shared_output, runtime, 3)


@pytest.mark.parametrize(
    ("selected", "error_fragment"),
    [
        ("C", "outside the allowed set"),
        ("Z", "outside the dataset answer space"),
    ],
)
def test_invalid_judge_selection_repairs_then_uses_first_submission_on_exhaustion(
    selected: str, error_fragment: str
) -> None:
    runtime, agents = _runtime(
        3,
        {
            2: {
                "judge": [
                    {"answer": selected, "explanation": "invalid choice one"},
                    {"answer": selected, "explanation": "invalid choice two"},
                ]
            }
        },
        {1: _matrix(3, {(0, 2): Scheme.JUDGE, (1, 2): Scheme.JUDGE})},
        safety=SafetyConfig(parse_retries=1),
    )
    state = SessionStartState(1, _example(), (), ("B", "A", "C"))
    raw, traces, history, usage = asyncio.run(
        runtime._execute_session(
            state,
            _action(3, {(0, 2): Scheme.JUDGE, (1, 2): Scheme.JUDGE}),
            {0: "b", 1: "a", 2: "c"},
        )
    )

    judge_calls = [call for call in agents[2].calls if call.kind == "judge"]
    assert len(judge_calls) == 2
    assert judge_calls[0].context["allowed_submitted_answers"] == ("B", "A")
    assert judge_calls[0].context["allowed_submitted_answers"] == ("B", "A")
    shared_output = traces[0].outputs[0]
    assert isinstance(shared_output, JudgeOutput)
    assert shared_output.answer == "B"
    assert shared_output.error is not None and error_fragment in shared_output.error
    assert "adjudication-validation error" in shared_output.error
    assert "falling back to first submitted answer 'B'" in shared_output.error
    assert shared_output.validation_exhausted
    assert all(trace.outputs[0] is shared_output for trace in traces)
    assert all(trace.error == shared_output.error for trace in traces)
    assert all(trace.messages[-1].answer == "B" for trace in traces)
    assert all(trace.terminal is not None and trace.terminal.answer == "B" for trace in traces)
    assert [record.answer for record in raw[0]] == ["B"]
    assert [record.answer for record in raw[1]] == ["B"]
    assert raw[2] == ()
    assert sum(trace.normalized_cost for trace in traces) == pytest.approx(
        normalized_cost(shared_output.usage, runtime.config.costs)
    )
    assert usage.model_calls == 2
    _assert_attempt_accounting(shared_output, runtime, 2)

    _, resolved, _, ties, _, resolution_usage = asyncio.run(runtime._resolve_answers(state, raw, traces, history))
    assert resolved == ("B", "B", "C")
    assert not ties and resolution_usage.model_calls == 0


def test_duplicate_judge_submissions_are_canonicalized_and_deduplicated() -> None:
    runtime, agents = _runtime(
        3,
        {2: {"judge": {"answer": " b ", "explanation": "same answer"}}},
        {1: _matrix(3, {(0, 2): Scheme.JUDGE, (1, 2): Scheme.JUDGE})},
    )
    runtime.canonicalizer = TrimmedAnswerCanonicalizer(case_sensitive=False)
    state = SessionStartState(1, _example(), (), (" b ", "B", "C"))
    raw, traces, _, usage = asyncio.run(
        runtime._execute_session(
            state,
            _action(3, {(0, 2): Scheme.JUDGE, (1, 2): Scheme.JUDGE}),
            {0: "lowercase", 1: "uppercase", 2: "judge"},
        )
    )

    judge_calls = [call for call in agents[2].calls if call.kind == "judge"]
    assert len(judge_calls) == 1 and usage.model_calls == 1
    assert judge_calls[0].context["allowed_submitted_answers"] == ("B",)
    assert judge_calls[0].context["allowed_submitted_answers"] == ("B",)
    shared_output = traces[0].outputs[0]
    assert isinstance(shared_output, JudgeOutput)
    assert shared_output.answer == "B" and shared_output.error is None
    assert all(trace.outputs[0] is shared_output for trace in traces)
    assert all(trace.error is None for trace in traces)
    assert [record.answer for record in raw[0]] == ["B"]
    assert [record.answer for record in raw[1]] == ["B"]


def test_no_conversation_fallback_preserves_each_session_start_answer() -> None:
    runtime, agents = _runtime(3, {}, {1: _matrix(3)})
    state = _state(3)
    effective, resolved, modes, ties, history, usage = asyncio.run(
        runtime._resolve_answers(
            state,
            {agent: () for agent in runtime.config.agent_ids},
            (),
            (),
        )
    )

    assert resolved == state.answers
    assert not ties and not history and usage.model_calls == 0
    assert all(
        len(records) == 1
        and records[0].fallback
        and records[0].tag is None
        and records[0].answer == state.answers[agent]
        for agent, records in effective.items()
    )
    assert modes == {0: ("A",), 1: ("B",), 2: ("C",)}
    assert all(not agent.calls for agent in agents.values())


@pytest.mark.parametrize(
    ("script", "expected", "attempt_count"),
    [
        ({"answer": "B", "explanation": "valid tie choice"}, "B", 1),
        (
            [
                {"answer": "C", "explanation": "not modal"},
                {"answer": "B", "explanation": "repaired tie choice"},
            ],
            "B",
            2,
        ),
    ],
)
def test_modal_tie_resolution_repairs_context_invalid_answers(
    script: object, expected: str, attempt_count: int
) -> None:
    runtime, agents = _runtime(
        3,
        {0: {"tie": script}},
        {1: _matrix(3)},
    )
    state = _state(3)
    first_tag = ConversationTag(1, 0, 1, Scheme.REVIEWER)
    second_tag = ConversationTag(1, 0, 2, Scheme.TEACHER)
    raw = {
        0: (
            TerminalRecord(1, 0, "A", first_tag),
            TerminalRecord(1, 0, "B", second_tag),
        ),
        1: (),
        2: (),
    }
    traces = tuple(_terminal_trace(item) for item in raw[0])
    effective, resolved, modes, ties, history, usage = asyncio.run(runtime._resolve_answers(state, raw, traces, ()))

    assert tuple(record.answer for record in effective[0]) == ("A", "B")
    assert modes[0] == ("A", "B")
    assert resolved[0] == expected
    assert ties[0].answer == expected
    assert ties[0].tied_answers == ("A", "B")
    assert ties[0].error is None
    assert len([call for call in agents[0].calls if call.kind == "tie"]) == attempt_count
    assert sum(record.event == "tie-resolution" for record in history) == 1
    assert usage.model_calls == attempt_count
    _assert_attempt_accounting(ties[0], runtime, attempt_count)
    if attempt_count == 2:
        assert "outside the allowed set" in ties[0].parse_errors[0]


def test_tie_validation_exhaustion_uses_first_modal_answer() -> None:
    runtime, agents = _runtime(
        3,
        {
            0: {
                "tie": [
                    {"answer": "C", "explanation": "invalid one"},
                    {"answer": "C", "explanation": "invalid two"},
                ]
            }
        },
        {1: _matrix(3)},
        safety=SafetyConfig(parse_retries=1),
    )
    state = _state(3)
    first_tag = ConversationTag(1, 0, 1, Scheme.REVIEWER)
    second_tag = ConversationTag(1, 0, 2, Scheme.TEACHER)
    raw = {
        0: (
            TerminalRecord(1, 0, "A", first_tag),
            TerminalRecord(1, 0, "B", second_tag),
        ),
        1: (),
        2: (),
    }
    effective, resolved, modes, ties, history, usage = asyncio.run(
        runtime._resolve_answers(
            state,
            raw,
            tuple(_terminal_trace(item) for item in raw[0]),
            (),
        )
    )

    assert tuple(record.answer for record in effective[0]) == ("A", "B")
    assert modes[0] == ("A", "B")
    assert resolved[0] == "A"
    output = ties[0]
    assert output.answer == "A" and output.validation_exhausted
    assert output.error is not None
    assert "tie-resolution validation error" in output.error
    assert "falling back to first modal answer 'A'" in output.error
    assert len([call for call in agents[0].calls if call.kind == "tie"]) == 2
    assert sum(record.event == "tie-resolution" for record in history) == 1
    assert usage.model_calls == 2
    _assert_attempt_accounting(output, runtime, 2)


def _termination_runtime(
    opportunity: float,
) -> tuple[Runtime, SessionStartState]:
    termination = TerminationConfig(
        min_sessions=1,
        max_sessions=3,
        persistence_length=2,
        winner_support_threshold=0.8,
        winner_margin_threshold=0.5,
        distribution_change_threshold=0.05,
        routing_opportunity_threshold=0.1,
    )
    matrices = {session: _matrix(1) for session in (1, 2, 3)}
    runtime, _ = _runtime(
        1,
        {},
        matrices,
        termination=termination,
        objective_scores={2: opportunity, 3: opportunity},
    )
    example = QueryExample("termination", "Choose.", ("A", "B"), "A")
    return runtime, SessionStartState(2, example, (), ("A",))


def test_termination_conf_stab_smax_and_high_opportunity_continuation_branches() -> None:
    high_runtime, next_state = _termination_runtime(opportunity=0.5)
    confident = high_runtime._termination(
        1,
        {"A": 0.6, "B": 0.4},
        {"A": 0.9, "B": 0.1},
        ["A", "A"],
        next_state,
    )
    assert confident.confident and confident.stop and confident.reason == "Conf"
    assert not confident.stable and confident.routing_opportunity == pytest.approx(0.5)

    low_runtime, next_state = _termination_runtime(opportunity=0.0)
    stable = low_runtime._termination(
        1,
        {"A": 0.6, "B": 0.4},
        {"A": 0.6, "B": 0.4},
        ["B", "A"],
        next_state,
    )
    assert stable.stable and stable.stop and stable.reason == "Stab"
    assert not stable.confident

    continuing = high_runtime._termination(
        1,
        {"A": 0.59, "B": 0.41},
        {"A": 0.6, "B": 0.4},
        ["B", "A"],
        next_state,
    )
    assert continuing.distribution_change <= high_runtime.config.termination.distribution_change_threshold
    assert continuing.routing_opportunity > high_runtime.config.termination.routing_opportunity_threshold
    assert not continuing.confident and not continuing.stable
    assert not continuing.stop and continuing.reason == "continue"

    maximum = high_runtime._termination(
        3,
        {"A": 0.5, "B": 0.5},
        {"A": 0.5, "B": 0.5},
        ["B", "A"],
        None,
    )
    assert maximum.stop and maximum.reason == "S_max"


def test_inference_weighting_forward_runs_without_gradients() -> None:
    runtime, _ = _runtime(
        2,
        {
            0: {"initial": {"answer": "A", "explanation": "a0"}},
            1: {"initial": {"answer": "B", "explanation": "a1"}},
        },
        {1: _matrix(2)},
    )
    grad_enabled: list[bool] = []
    hook = runtime.weighting_model.register_forward_pre_hook(
        lambda _module, _inputs: grad_enabled.append(torch.is_grad_enabled())
    )
    try:
        trajectory = asyncio.run(runtime.run(_example(("A", "B")), routing_mode=RoutingMode.INFERENCE))
    finally:
        hook.remove()

    assert grad_enabled == [False, False]
    assert trajectory.realized_sessions == 1
    assert trajectory.sessions[0].weight_tensor is not None
    assert trajectory.sessions[0].weight_logit_tensor is not None
    assert not trajectory.sessions[0].weight_tensor.requires_grad
    assert not trajectory.sessions[0].weight_logit_tensor.requires_grad


def test_training_routing_identity_records_fresh_session_substreams_only() -> None:
    config = _offline_config(
        n_agents=2,
        capacity=CapacityConfig(0, 0),
        termination=TerminationConfig(min_sessions=2, max_sessions=2),
    )
    agents = {
        0: DeterministicFakeAgent(0, default_answer="A"),
        1: DeterministicFakeAgent(1, default_answer="B"),
    }
    routing = RoutingPolicy(
        config.n_agents,
        FixedBaseEncoders(config.n_agents, configuration=config.encoders).history_dimension,
        configuration=config.network,
    )
    runtime = Runtime(
        config,
        agents,
        routing,
        _weighting(config),
    )
    identity = RoutingRolloutIdentity(123, "trajectory-a", 4)

    trajectory = asyncio.run(
        runtime.run(
            _example(("A", "B")),
            routing_mode=RoutingMode.TRAIN,
            routing_identity=identity,
        )
    )

    assert trajectory.realized_sessions == 2
    assert [session.routing.routing_substream for session in trajectory.sessions] == [
        identity.for_session(1).to_dict(),
        identity.for_session(2).to_dict(),
    ]
    assert (
        trajectory.sessions[0].routing.routing_substream["seed"]
        != trajectory.sessions[1].routing.routing_substream["seed"]
    )

    inference, _ = _runtime(2, {}, {1: _matrix(2)})
    inferred = asyncio.run(
        inference.run(
            _example(("A", "B")),
            routing_mode=RoutingMode.INFERENCE,
        )
    )
    assert inferred.sessions[0].routing.routing_substream is None

    with pytest.raises(ValueError, match="does not accept a raw routing generator"):
        asyncio.run(
            inference.run(
                _example(("A", "B")),
                generator=torch.Generator(),
            )
        )
    with pytest.raises(ValueError, match="requires a recorded RoutingRolloutIdentity"):
        asyncio.run(
            runtime.run(
                _example(("A", "B")),
                routing_mode=RoutingMode.TRAIN,
            )
        )
    with pytest.raises(ValueError, match="applies only to stochastic training"):
        asyncio.run(
            inference.run(
                _example(("A", "B")),
                routing_identity=identity,
            )
        )


def test_full_fake_smoke_exercises_required_two_session_workflow() -> None:
    trajectory = asyncio.run(run_fake_smoke())

    assert trajectory.completed
    assert trajectory.realized_sessions == 2
    assert trajectory.final_answer == trajectory.sessions[-1].weighted_answer
    first, second = trajectory.sessions
    assert first.routing.mode is RoutingMode.INFERENCE
    assert sum(trace.tag.scheme is Scheme.JUDGE for trace in first.traces) == 2
    assert any(
        [message.format for message in trace.messages]
        == [
            MessageFormat.INITIATION,
            MessageFormat.ROLE_RESPONSE,
            MessageFormat.REVISION_RESPONSE,
            MessageFormat.ROLE_RESPONSE,
        ]
        for trace in first.traces
    )
    assert any(scheme is Scheme.ZERO for scheme in first.routing.matrix.values())
    assert any(record.fallback for records in second.effective_terminal_answers.values() for record in records)
    assert second.routing.matrix[(0, 0)] is Scheme.SELF
    assert sum(second.candidate_scores.values()) == pytest.approx(1.0)
    assert sum(second.agent_weights) == pytest.approx(1.0)
    assert second.termination is not None
    assert second.termination.stop and second.termination.reason == "S_max"
    assert trajectory.safeguard_diagnostics.completed_conversations == sum(
        len(session.traces) for session in trajectory.sessions
    )
    assert trajectory.safeguard_diagnostics.safeguard_completions == sum(
        session.safeguard_diagnostics.safeguard_completions for session in trajectory.sessions
    )


def test_completed_labelled_sessions_compute_credits_rewards_and_returns_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    counts = {"credits": 0, "rewards": 0, "returns": 0}
    original_credits = runtime_module.conversation_credits
    original_rewards = runtime_module.session_rewards
    original_returns = runtime_module.returns_and_future

    def counted_credits(*args: Any, **kwargs: Any):
        counts["credits"] += 1
        return original_credits(*args, **kwargs)

    def counted_rewards(*args: Any, **kwargs: Any):
        counts["rewards"] += 1
        return original_rewards(*args, **kwargs)

    def counted_returns(*args: Any, **kwargs: Any):
        counts["returns"] += 1
        return original_returns(*args, **kwargs)

    monkeypatch.setattr(runtime_module, "conversation_credits", counted_credits)
    monkeypatch.setattr(runtime_module, "session_rewards", counted_rewards)
    monkeypatch.setattr(runtime_module, "returns_and_future", counted_returns)

    trajectory = asyncio.run(run_fake_smoke())

    assert trajectory.completed and trajectory.realized_sessions == 2
    assert counts == {"credits": 2, "rewards": 2, "returns": 1}
    assert all(session.conversation_credits for session in trajectory.sessions)
    assert all(session.immediate_reward is not None for session in trajectory.sessions)
    assert all(session.return_value is not None for session in trajectory.sessions)


def test_safeguard_diagnostics_zero_denominator_is_explicit() -> None:
    diagnostics = SafeguardDiagnostics.from_traces(())

    assert diagnostics.completed_conversations == 0
    assert diagnostics.safeguard_completions == 0
    assert diagnostics.safeguard_rate == 0.0
    assert diagnostics.reason_counts == {}
    assert diagnostics.safeguard_usage == UsageRecord()


def test_runtime_caches_fixed_inputs_and_recomputes_full_transformer_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _offline_config(
        n_agents=2,
        capacity=CapacityConfig(2, 2),
        termination=TerminationConfig(min_sessions=2, max_sessions=2),
    )
    text_encoder = OfflineTextEncoder()
    encoders = FixedBaseEncoders(2, configuration=config.encoders, text_encoder=text_encoder)
    routing = RoutingPolicy(2, encoders.history_dimension, configuration=config.network)
    original_route = routing.route_inference

    def route_with_self_conversations(*args: Any, **kwargs: Any) -> RoutingAction:
        action = original_route(*args, **kwargs)
        return replace(action, matrix=_matrix(2, {(0, 0): Scheme.SELF, (1, 1): Scheme.SELF}))

    monkeypatch.setattr(routing, "route_inference", route_with_self_conversations)
    runtime = Runtime(
        config,
        {agent: DeterministicFakeAgent(agent, default_answer=chr(65 + agent)) for agent in range(2)},
        routing,
        _weighting(config),
        encoders=encoders,
    )
    query = _example(("A", "B"))
    route_lengths: list[int] = []
    weight_lengths: list[int] = []
    hooks = [
        routing.history_map.transformer.register_forward_pre_hook(
            lambda _module, inputs: route_lengths.append(inputs[0].shape[1])
        ),
        runtime.weighting_model.history_map.transformer.register_forward_pre_hook(
            lambda _module, inputs: weight_lengths.append(inputs[0].shape[1])
        ),
    ]
    try:
        trajectory = asyncio.run(runtime.run(query))
        first_history = trajectory.sessions[0].history
        final_history = trajectory.sessions[-1].history
        assert weight_lengths == [1, len(first_history) + 1, len(final_history) + 1]
        assert route_lengths == [1, len(first_history) + 1, len(first_history) + 1]
        assert text_encoder.calls == [serialize_query(query), *(text_payload(event) for event in final_history)]
        assert [event.history_index for event in final_history] == list(range(1, len(final_history) + 1))
        assert final_history[: len(first_history)] == first_history

        frozen = runtime._history_cache.snapshot().vectors.clone()
        runtime._weight(query, final_history, trajectory.sessions[-1].resolved_answers)
        assert len(text_encoder.calls) == len(final_history) + 1
        assert torch.equal(frozen, runtime._history_cache.snapshot().vectors)
        assert len(weight_lengths) == 4
        changed = (*final_history[:-1], replace(final_history[-1], content="changed after finalization"))
        with pytest.raises(ValueError, match="finalized observable history event was changed"):
            runtime._commit_history(changed)

        repeated = asyncio.run(runtime.run(query))
        assert text_encoder.calls.count(serialize_query(query)) == 1
        assert len(text_encoder.calls) == 1 + len(final_history) + len(repeated.sessions[-1].history)
    finally:
        for hook in hooks:
            hook.remove()

    assert trajectory.call_durations == tuple(record["elapsed_seconds"] for record in trajectory.call_records)
    assert len(trajectory.call_records) == trajectory.usage.model_calls
    assert all(value > 0 for value in trajectory.call_durations)
    assert trajectory.elapsed_seconds >= max(trajectory.call_durations)
    assert all(0 < result.elapsed_seconds <= trajectory.elapsed_seconds for result in trajectory.sessions)


def test_paired_workflow_seeds_include_initial_calls_and_distinct_repairs() -> None:
    context = GenerationContext(83, "promptval", "offline")
    snapshots: list[dict[str, int]] = []
    for revised in (False, True):
        runtime, agents = _runtime(
            2,
            {
                1: {
                    "role": [
                        {"status": "halt", "feedback": "invalid"},
                        {"status": "complete", "feedback": "valid"},
                    ]
                }
            },
            {1: _matrix(2, {(0, 1): Scheme.REVIEWER})},
            safety=SafetyConfig(parse_retries=1),
        )
        if revised:
            key = "role:S_V"
            runtime.prompts = runtime.prompts.replace_template(
                key, runtime.prompts.components[key].template + "\nCheck the explanation carefully."
            )
        trajectory = asyncio.run(runtime.run(_example(), generation_context=context, partition="comp-val"))
        calls = [call for agent in agents.values() for call in agent.calls]
        assert all(call.max_repair_generations == 0 for call in calls)
        initial = [call for call in calls if call.kind == "initial"]
        repairs = [call for call in calls if call.kind == "role"]
        assert len(initial) == 2 and len(repairs) == 2
        assert [call.context["repair_attempt"] for call in repairs] == [0, 1]
        assert len({call.context["generation_seed"] for call in calls}) == 4
        snapshots.append(
            {
                json.dumps(call.context["logical_call"]["logical_call"], sort_keys=True): call.context[
                    "generation_seed"
                ]
                for call in calls
            }
        )
        assert all(record["replay_limitation"] and not record["supports_seed"] for record in trajectory.call_records)
    assert snapshots[0] == snapshots[1]


def test_shared_judge_seed_identifies_batch_independently_of_submission_membership() -> None:
    seeds: list[int] = []
    submission_sets: list[list[str]] = []
    for active in ({(0, 2): Scheme.JUDGE, (1, 2): Scheme.JUDGE}, {(1, 2): Scheme.JUDGE}):
        runtime, agents = _runtime(
            3, {2: {"judge": {"answer": "B", "explanation": "compare submissions"}}}, {1: _matrix(3, active)}
        )
        asyncio.run(runtime.run(_example(), generation_context=GenerationContext(90, "alphaval", "offline")))
        call = next(call for call in agents[2].calls if call.kind == "judge")
        seeds.append(call.context["generation_seed"])
        submission_sets.append(call.context["tags"])
        assert call.context["logical_call"]["logical_call"]["conversation_id"] == "judge:2"
    assert submission_sets[0] != submission_sets[1]
    assert seeds[0] == seeds[1]


def test_prompt_evidence_preserves_full_judge_submissions_and_pre_tie_traces() -> None:
    runtime, _ = _runtime(
        3,
        {
            0: {"tie": {"answer": "B", "explanation": "supported by judge"}},
            2: {"judge": {"answer": "B", "explanation": "shared adjudication explanation"}},
        },
        {1: _matrix(3, {(0, 1): Scheme.REVIEWER, (0, 2): Scheme.JUDGE, (1, 2): Scheme.JUDGE})},
    )
    query = QueryExample("evidence", "Compare the supplied alternatives.", ("A", "B", "C"), "C")
    trajectory = asyncio.run(runtime.run(query, partition="train"))
    occurrences = trajectory.prompt_occurrences
    judge = next(item for item in occurrences if item.component == "judge:S_J")
    tie = next(item for item in occurrences if item.component == "tie")
    assert judge.trace["input_fields"]["query"] == query.query
    assert [item["initiator"] for item in judge.trace["input_fields"]["submissions"]] == [0, 1]
    assert [item["answer"] for item in judge.trace["input_fields"]["submissions"]] == ["A", "B"]
    submitted = judge.trace["input_fields"]["submitted_messages"]
    assert len(submitted) == 2 and all(item["format"] is MessageFormat.INITIATION for item in submitted)
    assert all(item["query"] == query.query and item["scheme_request"] for item in submitted)
    assert len(judge.trace["returned_traces"]) == 2
    assert judge.trace["parsed_output"]["explanation"] == "shared adjudication explanation"
    for trace in judge.trace["returned_traces"]:
        returned = trace["messages"][-1]
        assert returned["format"] is MessageFormat.JUDGE_RETURN
        assert returned["answer"] == "B" and returned["explanation"] == ""
    evidence = tie.trace["supplied_message"]["payload"]["evidence"]
    assert [item["answer"] for item in evidence["answers"]] == ["A", "B"]
    assert evidence["pairs"] and evidence["judge_decisions"]
    assert "messages" not in evidence and "outputs" not in evidence
    assert json.dumps(evidence, ensure_ascii=False, sort_keys=True) in tie.rendered_prompt
    assert tie.trace["selected_answer"] == "B" and not tie.trace["selection_used_fallback"]
    assert tie.trace["tie_resolution_cost"] == normalized_cost(
        trajectory.sessions[0].tie_outputs[0].usage, runtime.config.costs
    )
    assert all(
        item.query == query.query and item.example_id == query.id and item.split == "train" for item in occurrences
    )
    assert all(item.gold_answer == "C" and item.complete_return is not None for item in occurrences)
    assert all(
        "gold_answer" not in json.dumps(item.trace) and "conversation_credits" not in json.dumps(item.trace)
        for item in occurrences
    )


def test_prompt_evidence_distinguishes_invalid_judge_output_from_selected_fallback() -> None:
    runtime, _ = _runtime(
        3,
        {2: {"judge": {"answer": "C", "explanation": "invalid unsubmitted answer"}}},
        {1: _matrix(3, {(0, 2): Scheme.JUDGE, (1, 2): Scheme.JUDGE})},
        safety=SafetyConfig(parse_retries=0),
    )
    trajectory = asyncio.run(runtime.run(_example(), partition="train"))
    occurrence = next(item for item in trajectory.prompt_occurrences if item.component == "judge:S_J")
    assert occurrence.trace["parsed_output"]["answer"] == "C"
    assert occurrence.trace["parsed_output"]["validation_exhausted"]
    assert occurrence.trace["selected_answer"] == occurrence.outcome == "A"
    assert occurrence.trace["selection_used_fallback"]


def test_next_session_uses_explanation_for_resolved_answer_not_another_revision() -> None:
    runtime, _ = _runtime(
        3,
        {
            0: {
                "revision": [
                    {"answer": "B", "explanation": "reason for B"},
                    {"answer": "C", "explanation": "reason for C"},
                ],
                "tie": {"answer": "B", "explanation": "selected reason for B"},
            },
            1: {"role": {"status": "continue", "feedback": "revise toward B"}},
            2: {"role": {"status": "continue", "feedback": "revise toward C"}},
        },
        {
            1: _matrix(3, {(0, 1): Scheme.REVIEWER, (0, 2): Scheme.TEACHER}),
            2: _matrix(3, {(0, 1): Scheme.REVIEWER}),
        },
        termination=TerminationConfig(min_sessions=2, max_sessions=2),
    )
    trajectory = asyncio.run(runtime.run(_example(), partition="train"))
    next_initial = trajectory.sessions[1].traces[0].messages[0]
    assert next_initial.answer == "B"
    assert next_initial.explanation == "selected reason for B"
    revisions = [item for item in trajectory.prompt_occurrences if item.component.startswith("revision:")]
    assert {item.trace["parsed_output"]["answer"] for item in revisions} == {"B", "C"}


def test_direct_runtime_inference_disables_dropout_and_restores_module_modes() -> None:
    config = _offline_config(
        n_agents=2,
        network=NetworkConfig(
            representation_dim=16,
            feedforward_dim=32,
            answer_byte_dim=8,
            transformer_layers=1,
            transformer_heads=2,
            dropout=0.5,
        ),
        capacity=CapacityConfig(0, 0),
        termination=TerminationConfig(min_sessions=1, max_sessions=1),
    )
    encoders = FixedBaseEncoders(2, configuration=config.encoders)
    routing = RoutingPolicy(2, encoders.history_dimension, configuration=config.network)
    weighting = _weighting(config)
    runtime = Runtime(
        config,
        {agent: DeterministicFakeAgent(agent, default_answer=chr(65 + agent)) for agent in range(2)},
        routing,
        weighting,
    )
    modes: list[tuple[bool, bool]] = []
    hooks = [
        module.register_forward_pre_hook(
            lambda module, _inputs: modes.append((module.training, torch.is_grad_enabled()))
        )
        for module in (routing, weighting)
    ]
    try:
        first = asyncio.run(runtime.run(_example()))
        second = asyncio.run(runtime.run(_example()))
    finally:
        for hook in hooks:
            hook.remove()
    assert modes and all(mode == (False, False) for mode in modes)
    assert routing.training and weighting.training
    assert first.sessions[0].candidate_scores == second.sessions[0].candidate_scores
    runtime._route(_state(2), RoutingMode.INFERENCE, None)
    assert routing.training
    with pytest.raises(ValueError, match="identify the current query"):
        asyncio.run(runtime.run(_example(), generation_context=GenerationContext(1, "sysval", "wrong")))
    assert routing.training and weighting.training


@pytest.mark.parametrize("kind", [AgentCallKind.ROLE, AgentCallKind.JUDGE])
@pytest.mark.parametrize("interruption", ["failure", "call-timeout", "session-timeout"])
def test_interrupted_repairs_retain_prior_usage_and_failed_prompt_occurrences(
    kind: AgentCallKind,
    interruption: str,
) -> None:
    class InterruptedBackend(DeterministicFakeAgent):
        async def invoke(self, request: AgentRequest):
            if request.kind is kind and request.context.get("repair_attempt") == 1:
                if interruption == "failure":
                    raise RuntimeError("interrupted repair")
                await asyncio.sleep(1)
            return await super().invoke(request)

    active = {(0, 1): Scheme.REVIEWER} if kind is AgentCallKind.ROLE else {(0, 2): Scheme.JUDGE, (1, 2): Scheme.JUDGE}
    config = _offline_config(
        n_agents=3,
        capacity=CapacityConfig(3, 3),
        costs=CostConfig(normalization=1, input_token_weight=0, output_token_weight=0, model_call_weight=1),
        safety=SafetyConfig(
            parse_retries=1,
            call_timeout_seconds=0.01 if interruption == "call-timeout" else 1,
            session_timeout_seconds=0.01 if interruption == "session-timeout" else 2,
        ),
        termination=TerminationConfig(min_sessions=1, max_sessions=1),
    )
    scripts = {
        "role": {"status": "halt", "feedback": "invalid status"},
        "judge": {"answer": "C", "explanation": "not a submitted answer"},
    }
    runtime = Runtime(
        config,
        {agent: InterruptedBackend(agent, scripts, default_answer=chr(65 + agent)) for agent in range(3)},
        ScriptedRoutingPolicy(3, {1: _matrix(3, active)}),
        _weighting(config),
    )
    runtime.config = replace(
        runtime.config,
        costs=CostConfig(normalization=1, input_token_weight=0, output_token_weight=0, model_call_weight=1),
    )
    trajectory = asyncio.run(runtime.run(_example(), partition="train"))
    session = trajectory.sessions[0]
    assert session.usage.model_calls == 2
    assert session.normalized_cost == 2
    assert sum(trace.normalized_cost for trace in session.traces) == 2
    assert trajectory.usage.model_calls == len(trajectory.call_records) == 5
    component = "role:S_V" if kind is AgentCallKind.ROLE else "judge:S_J"
    occurrence = next(item for item in trajectory.prompt_occurrences if item.component == component)
    output = occurrence.trace["parsed_output"]
    assert len(output["attempts"]) == 2
    assert output["attempts"][0]["raw_output"] and not output["attempts"][1]["raw_output"]
    assert output["usage"]["model_calls"] == 2
    assert output["error"] and occurrence.trace["selection_used_fallback"]
    assert all(len(trace.outputs[-1].attempts) == 2 for trace in session.traces)
    if kind is AgentCallKind.JUDGE:
        assert all(trace.terminal is not None and trace.terminal.answer == "A" for trace in session.traces)
        shared_events = [event for event in session.history if event.event == "judge-adjudication"]
        assert len(shared_events) == 1
        event = shared_events[0]
        assert event.usage.model_calls == event.normalized_cost == 2
        assert event.involved_agent_ids == (0, 1, 2)
        assert event.status == "safeguard-complete" and event.parse_outcome == "invalid"
        assert event.answer == "A" and "attempts" in event.content


@pytest.mark.parametrize("interruption", ["failure", "call-timeout", "cancelled"])
def test_interrupted_tie_uses_one_modal_fallback_and_external_cancellation_propagates(
    interruption: str,
) -> None:
    class InterruptedTie(DeterministicFakeAgent):
        def __init__(self) -> None:
            super().__init__(0, {"tie": {"answer": "C", "explanation": "not modal"}}, default_answer="A")
            self.tie_calls = 0
            self.interrupted_attempt = asyncio.Event()

        async def invoke(self, request: AgentRequest):
            if request.kind is AgentCallKind.TIE:
                self.tie_calls += 1
                if self.tie_calls == 2:
                    self.interrupted_attempt.set()
                    if interruption == "failure":
                        raise RuntimeError("tie provider failed")
                    await asyncio.sleep(1)
            return await super().invoke(request)

    runtime, _ = _runtime(
        3,
        {2: {"judge": {"answer": "B", "explanation": "choose B"}}},
        {1: _matrix(3, {(0, 1): Scheme.REVIEWER, (0, 2): Scheme.JUDGE, (1, 2): Scheme.JUDGE})},
        costs=CostConfig(normalization=1, input_token_weight=0, output_token_weight=0, model_call_weight=1),
        safety=SafetyConfig(parse_retries=1, call_timeout_seconds=0.01 if interruption == "call-timeout" else 2),
    )
    backend = InterruptedTie()
    runtime.agents[0] = backend

    async def run():
        task = asyncio.create_task(runtime.run(_example(), partition="train"))
        if interruption == "cancelled":
            await backend.interrupted_attempt.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            return None
        return await task

    runtime.config = replace(
        runtime.config,
        costs=CostConfig(normalization=1, input_token_weight=0, output_token_weight=0, model_call_weight=1),
    )
    trajectory = asyncio.run(run())
    assert backend.tie_calls == 2
    if trajectory is None:
        return
    output = trajectory.sessions[0].tie_outputs[0]
    assert output.answer == "A" and not output.validation_exhausted
    assert output.error and "tie-resolution operational failure" in output.error
    assert output.usage.model_calls == 2 and len(output.attempts) == 2
    assert trajectory.sessions[0].normalized_cost == 4
    assert trajectory.usage.model_calls == len(trajectory.call_records) == 7
    event = trajectory.sessions[0].history[-1]
    assert event.event == "tie-resolution" and event.status == "safeguard-complete"
    assert event.parse_outcome == "invalid" and event.normalized_cost == 2
    occurrence = next(item for item in trajectory.prompt_occurrences if item.component == "tie")
    assert occurrence.trace["selected_answer"] == "A" and occurrence.trace["selection_used_fallback"]
    assert occurrence.trace["tie_resolution_cost"] == 2
