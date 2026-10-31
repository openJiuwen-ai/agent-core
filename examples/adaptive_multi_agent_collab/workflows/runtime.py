"""Asynchronous tagged-conversation workflow runtime."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from typing import Any, cast

import torch

from ..config import Config, CostConfig, TerminationConfig
from ..models.encoders import FixedBaseEncoders, HistoryEncoding, answer_views
from ..models.policies import (
    RoutingPolicy,
    RoutingRolloutIdentity,
    WeightingModel,
    routing_generator,
)
from .prompts import (
    PromptCollection,
    PromptOccurrence,
    seed_prompt_collection,
    protocol_instructions,
    compose_backend_input,
    INITIAL_TEMPLATE_ID,
    initiation_request,
)
from .repair import render_repair, select_repair_diagnostic
from .tie_view import event_reference, tie_view
from ..providers.resources import ResourceFailure, AccountingFailure, BudgetExhausted, PolicyStopped
from ..infrastructure.failure_policy import cancellation_cause, cancel_with_cause, deadline_scope, tag_cancellation
from ..infrastructure.shutdown import bounded_cleanup, current_shutdown, raise_if_stopping
from ..infrastructure.randomness import GenerationContext, LogicalCallIdentity
from ..schemas import (
    AgentCallKind,
    AgentOutput,
    AgentProtocol,
    AgentRequest,
    AnswerCanonicalizer,
    ConversationStatus,
    ConversationTag,
    ConversationTrace,
    ExplanationReference,
    HistoryRecord,
    JudgeOutput,
    MessageFormat,
    ParsedOutput,
    QueryExample,
    RoleOutput,
    RoutingAction,
    RoutingMode,
    SafeguardDiagnostics,
    SafeguardTrigger,
    Scheme,
    SessionResult,
    SessionStartState,
    SharedPrefixResult,
    StrategyQAAnswerCanonicalizer,
    StructuredMessage,
    TerminalRecord,
    TerminationMetrics,
    TieOutput,
    Trajectory,
    TrimmedAnswerCanonicalizer,
    UsageRecord,
    ValidationAttempt,
)
from ..learning.training import (
    conversation_credits,
    modal_answers,
    returns_and_future,
    session_rewards,
)

FAILURE_ELIGIBILITY_PROTOCOL = "separate_output_accounting_objective_eligibility_v1"


def _available_sum(values: Sequence[float | None]) -> float | None:
    return None if any(value is None for value in values) else sum(value for value in values if value is not None)


def normalized_cost(usage: UsageRecord, config: CostConfig) -> float:
    from ..providers.resources import AccountingFailure

    total = 0.0
    for name, coefficient in (
        ("input_tokens", config.input_token_weight),
        ("output_tokens", config.output_token_weight),
        ("model_calls", config.model_call_weight),
        ("monetary_cost", config.monetary_weight),
    ):
        if coefficient:
            value = getattr(usage, name)
            if value is None:
                raise AccountingFailure(f"required usage coordinate {name} is unavailable", usage)
            total += coefficient * value
    return total / config.normalization


def total_variation(previous: dict[str, float], current: dict[str, float]) -> float:
    return 0.5 * sum(abs(current.get(key, 0.0) - previous.get(key, 0.0)) for key in previous.keys() | current.keys())


def deterministic_winner(scores: dict[str, float], order: tuple[str, ...]) -> str:
    if not scores:
        raise ValueError("weighted vote has no realized candidate")
    canonical = tuple(dict.fromkeys([*order, *scores]))
    return min(canonical, key=lambda answer: (-scores.get(answer, float("-inf")), canonical.index(answer)))


class ScriptedRoutingPolicy(torch.nn.Module):
    """Deterministic offline routing fixture keyed by session number.

    This is deliberately not a trainable alpha substitute.  It exists so the
    smoke test can exercise an exact, reproducible multi-session control flow
    without relying on random neural-network initialization.
    """

    def __init__(
        self,
        n_agents: int,
        matrices: dict[int, dict[tuple[int, int], Scheme]],
        *,
        objective_scores: dict[int, float] | None = None,
    ) -> None:
        super().__init__()
        self.n_agents = n_agents
        self._device_anchor = torch.nn.Parameter(torch.zeros(()), requires_grad=False)
        self.matrices = {session: dict(matrix) for session, matrix in matrices.items()}
        self.objective_scores = dict(objective_scores or {})
        expected = {(j, i) for j in range(n_agents) for i in range(n_agents)}
        if not self.matrices or any(set(matrix) != expected for matrix in self.matrices.values()):
            raise ValueError("every scripted session must contain the complete ordered edge set")

    def _action(self, session: int, mode: RoutingMode) -> RoutingAction:
        if session not in self.matrices:
            raise KeyError(f"no scripted routing matrix for session {session}")
        return RoutingAction(
            session=session,
            matrix=self.matrices[session],
            mode=mode,
            objective_score=self.objective_scores.get(session, 0.0),
        )

    def route_inference(
        self,
        base_query: torch.Tensor,
        base_history: HistoryEncoding,
        answers: tuple[str, ...],
        outgoing_capacity: tuple[int, ...],
        incoming_capacity: tuple[int, ...],
        *,
        session: int,
        answer_views: tuple[str, ...] | None = None,
    ) -> RoutingAction:
        del base_query, base_history, answers, outgoing_capacity, incoming_capacity
        return self._action(session, RoutingMode.INFERENCE)

    def route_training(
        self,
        base_query: torch.Tensor,
        base_history: HistoryEncoding,
        answers: tuple[str, ...],
        outgoing_capacity: tuple[int, ...],
        incoming_capacity: tuple[int, ...],
        *,
        session: int,
        generator: torch.Generator | None = None,
        answer_views: tuple[str, ...] | None = None,
    ) -> RoutingAction:
        del base_query, base_history, answers, outgoing_capacity, incoming_capacity, generator
        raise ValueError(
            f"scripted routing session {session} is an inference-only offline fixture; "
            "use RoutingPolicy for on-policy training"
        )


class AgentMailbox:
    """One FIFO mailbox and one-active-processing guard for an agent."""

    def __init__(self, agent_id: int) -> None:
        self.agent_id = agent_id
        self.queue: asyncio.Queue[_WorkItem | None] = asyncio.Queue()
        self.active = False
        self.started_delivery_indices: list[int] = []

    async def deliver(self, item: "_WorkItem") -> None:
        await self.queue.put(item)

    async def begin(self) -> "_WorkItem | None":
        item = await self.queue.get()
        if item is None:
            return None
        if self.active:
            raise RuntimeError(f"agent {self.agent_id} began two messages concurrently")
        self.active = True
        self.started_delivery_indices.append(item.delivery_index)
        return item

    def finish(self) -> None:
        if not self.active:
            raise RuntimeError(f"agent {self.agent_id} finished while idle")
        self.active = False
        self.queue.task_done()


@dataclass(slots=True)
class _JudgeBatch:
    judge_id: int
    messages: tuple[StructuredMessage, ...]


@dataclass(slots=True)
class _WorkItem:
    delivery_index: int
    message: StructuredMessage | None = None
    judge_batch: _JudgeBatch | None = None

    def __post_init__(self) -> None:
        if (self.message is None) == (self.judge_batch is None):
            raise ValueError("work item must contain exactly one message or judge batch")


class _ModelCallTimeout(TimeoutError):
    """One configured model-call deadline expired before a response arrived."""

    def __init__(self, request: AgentRequest, usage: UsageRecord) -> None:
        self.request = request
        self.usage = usage
        self.output: ParsedOutput | None = None
        super().__init__(
            f"{request.kind.value} call for agent {request.agent_id} exceeded " "SafetyConfig.call_timeout_seconds"
        )


class _ModelCallFailure(RuntimeError):
    """A backend call failed after consuming one observable call attempt."""

    def __init__(
        self,
        request: AgentRequest,
        usage: UsageRecord,
        error: Exception,
    ) -> None:
        self.request = request
        self.usage = usage
        self.output: ParsedOutput | None = None
        self.original_error = error
        super().__init__(f"{type(error).__name__}: {error}")


class Runtime:
    """Exact specification orchestration with asynchronous per-agent workers."""

    def __init__(
        self,
        config: Config,
        agents: Mapping[int, AgentProtocol],
        routing_policy: RoutingPolicy | ScriptedRoutingPolicy,
        weighting_model: WeightingModel,
        *,
        prompts: PromptCollection | None = None,
        encoders: FixedBaseEncoders | None = None,
        canonicalizer: AnswerCanonicalizer | None = None,
    ) -> None:
        if set(agents) != set(config.agent_ids):
            raise ValueError("agents must contain exactly one backend for each configured agent ID")
        for left in config.agent_pool:
            for right in config.agent_pool:
                same_key = left.backend_key == right.backend_key
                same_object = agents[left.agent_id] is agents[right.agent_id]
                if same_key != same_object:
                    relation = "share" if same_key else "use distinct"
                    raise ValueError(
                        f"logical agents {left.agent_id} and {right.agent_id} must {relation} "
                        "backend objects according to Config.agent_pool backend_key"
                    )
        if routing_policy.n_agents != config.n_agents or weighting_model.n_agents != config.n_agents:
            raise ValueError("alpha/W agent counts must match runtime configuration")
        self.config = config
        self.agents = dict(agents)
        self.routing_policy = routing_policy
        self.weighting_model = weighting_model
        self.prompts = prompts or seed_prompt_collection(config.scheme_registry)
        configured_encoders = FixedBaseEncoders(
            config.n_agents,
            configuration=config.encoders,
            scheme_order=config.scheme_order,
            agent_ids=config.agent_ids,
            categories=dict(config.history_categories) or None,
        )
        if encoders is not None and encoders.metadata() != configured_encoders.metadata():
            raise ValueError("injected fixed encoder metadata does not match Config.encoders and network dimensions")
        self.encoders = encoders or configured_encoders
        self.canonicalizer = canonicalizer or TrimmedAnswerCanonicalizer()
        self._call_semaphore = asyncio.Semaphore(config.safety.max_concurrent_model_calls)
        self._cancelled_call_usage: dict[ConversationTag, UsageRecord] = {}
        self.last_mailboxes: dict[int, AgentMailbox] = {}
        self._history_cache = self.encoders.new_history_cache()
        self._finalized_history: list[HistoryRecord] = []
        self._generation_context: GenerationContext | None = None
        self._call_records: list[dict[str, Any]] = []
        self._prompt_calls: list[tuple[AgentRequest, ParsedOutput | None]] = []
        self._interrupted_outputs: dict[ConversationTag, ParsedOutput] = {}
        self._session_deadline: float | None = None
        self._initial_records: dict[int, AgentOutput] = {}
        self._explanation_sources: dict[int, ExplanationReference | None] = {}
        self._boundary_counts: dict[int, tuple[int, int, float]] = {}
        self._candidate_metrics: dict[int, list[TerminationMetrics]] = {}
        self._active_traces: dict[ConversationTag, ConversationTrace] = {}
        self._fallback_records: list[dict[str, Any]] = []
        self._fatal_error: ResourceFailure | None = None
        self._drain_error: PolicyStopped | None = None
        self._next_call_id = 0
        self._partial_history: tuple[HistoryRecord, ...] = ()
        self.progress_observer: Callable[[str, Trajectory], Awaitable[None]] | None = None
        self.event_observer: Callable[[str, Mapping[str, Any]], Awaitable[None]] | None = None
        self.last_trajectory: Trajectory | None = None

    async def _publish_progress(self, kind: str, trajectory: Trajectory) -> None:
        """Publish meaningful workflow boundaries, never individual stream frames."""
        trajectory.call_records = tuple(self._call_records)
        trajectory.call_durations = tuple(float(item["elapsed_seconds"]) for item in self._call_records)
        trajectory.fallback_records = tuple(self._fallback_records)
        trajectory.execution_counters = self._execution_counters(trajectory)
        if self.progress_observer is not None:
            await self.progress_observer(kind, trajectory)

    async def _record_progress_event(self, kind: str, payload: Mapping[str, Any]) -> None:
        if self.event_observer is not None:
            await self.event_observer(kind, payload)

    def _commit_history(self, records: tuple[HistoryRecord, ...]) -> tuple[HistoryRecord, ...]:
        """Assign final indices and encode only newly finalized observable events."""

        committed: list[HistoryRecord] = []
        for index, record in enumerate(records, start=1):
            finalized = replace(record, history_index=index)
            if index > len(self._finalized_history):
                self._history_cache.append(finalized)
                self._finalized_history.append(finalized)
            elif self._finalized_history[index - 1] != finalized:
                raise ValueError("a finalized observable history event was changed")
            committed.append(finalized)
        return tuple(committed)

    async def _call(self, request: AgentRequest) -> ParsedOutput:
        if self._fatal_error is not None:
            raise self._fatal_error
        message = request.message
        session = message.session if message is not None else int(request.context.get("session", 0))
        if request.kind is AgentCallKind.JUDGE:
            # The batch is one logical generation even if its submission set changes.
            conversation = f"judge:{request.agent_id}"
        else:
            conversation = str(message.tag) if message is not None and message.tag else request.kind.value
        logical = LogicalCallIdentity(
            request.agent_id,
            session,
            conversation,
            request.kind.value,
            int(request.context.get("round", message.round_index if message is not None else 0)),
            int(request.context.get("repair_attempt", 0)),
        )
        context = dict(request.context)
        if "runtime_call_id" not in context:
            context["runtime_call_id"] = self._next_call_id
            self._next_call_id += 1
        identity = self.config.agent_pool[request.agent_id]
        context["agent_instructions"] = "\n".join(
            value for value in (identity.system_configuration, identity.role_configuration) if value
        )
        if self._generation_context is not None:
            context["generation_seed"] = self._generation_context.seed_for(logical)
            context["logical_call"] = self._generation_context.descriptor(logical)
        context["protocol_instructions"] = protocol_instructions(request.prompt_component, context)
        context["component_identity"] = request.prompt_component
        context["effective_input"] = compose_backend_input(
            context["agent_instructions"], context["protocol_instructions"], request.prompt
        )
        request = replace(request, context=context, max_repair_generations=0)
        started = time.perf_counter()
        now = asyncio.get_running_loop().time()
        deadline = float(context.get("logical_deadline_monotonic", now + self.config.safety.call_timeout_seconds))
        if self._session_deadline is not None:
            deadline = min(deadline, self._session_deadline)
        timeout = max(0.0, deadline - now)
        dispatched = False
        record: dict[str, Any] = {
            "request_id": context.get("runtime_call_id", self._next_call_id),
            "requested": True,
            "logical_call": asdict(logical),
            "prompt": request.prompt,
            "component": request.prompt_component,
            "effective_input": context["effective_input"],
            "seed": context.get("generation_seed"),
            "supports_seed": bool(getattr(self.agents[request.agent_id], "supports_seed", False)),
            "replay_limitation": (
                None
                if getattr(self.agents[request.agent_id], "supports_seed", False)
                else "backend does not declare replayable seeded generation"
            ),
        }
        try:
            if timeout <= 0:
                raise _ModelCallTimeout(request, UsageRecord(raw_metadata={"not_dispatched": True}))
            deadline_kind = (
                "session-deadline"
                if self._session_deadline is not None and deadline == self._session_deadline
                else "call-deadline"
            )
            async with deadline_scope(deadline_kind, deadline=deadline):
                async with self._call_semaphore:
                    dispatched = True
                    output = await self.agents[request.agent_id].invoke(request)
                    record["usage"] = asdict(output.usage)
                    record["raw_output"] = output.raw_content
                    record["structured_output"] = output.structured_output
                    record["effective_input"] = (
                        output.usage.raw_metadata.get("effective_input") or context["effective_input"]
                    )
                    return output
        except asyncio.CancelledError as exc:
            record["cancellation_cause"] = tag_cancellation(exc)
            record["error"] = "cancelled"
            elapsed = time.perf_counter() - started
            if request.message is not None and request.message.tag is not None:
                tag = request.message.tag
                cancelled = UsageRecord(
                    input_tokens=None if dispatched else 0,
                    output_tokens=None if dispatched else 0,
                    monetary_cost=None if dispatched else 0.0,
                    paid_monetary_cost=None if dispatched else 0.0,
                    model_calls=int(dispatched),
                    wall_seconds=elapsed,
                    raw_metadata={
                        "cancelled": True,
                        "call_kind": request.kind.value,
                        "agent_id": request.agent_id,
                    },
                )
                cancelled = getattr(exc, "usage_record", None) or cancelled
                previous = self._cancelled_call_usage.get(tag, UsageRecord())
                self._cancelled_call_usage[tag] = previous + cancelled
            if request.message is None or request.message.tag is None:
                cancelled = UsageRecord(
                    input_tokens=None if dispatched else 0,
                    output_tokens=None if dispatched else 0,
                    monetary_cost=None if dispatched else 0.0,
                    paid_monetary_cost=None if dispatched else 0.0,
                    model_calls=int(dispatched),
                    wall_seconds=elapsed,
                )
            cancelled = getattr(exc, "usage_record", None) or cancelled
            record["usage"] = asdict(cancelled)
            raise
        except ResourceFailure as exc:
            usage = exc.usage_record or UsageRecord(raw_metadata={"not_dispatched": True})
            record["usage"] = asdict(usage)
            record["error"] = str(exc)
            if isinstance(exc, PolicyStopped):
                self._drain_error = exc
                raise _ModelCallFailure(request, usage, exc) from exc
            if isinstance(exc, (BudgetExhausted, AccountingFailure)):
                self._fatal_error = exc
                raise
            raise _ModelCallFailure(request, usage, exc) from exc
        except _ModelCallTimeout:
            record["usage"] = asdict(UsageRecord(raw_metadata={"not_dispatched": True}))
            raise
        except TimeoutError as exc:
            record["error"] = "timeout"
            record["cancellation_cause"] = getattr(exc, "cancellation_cause", "transport-timeout")
            elapsed = time.perf_counter() - started
            usage = UsageRecord(
                input_tokens=None if dispatched else 0,
                output_tokens=None if dispatched else 0,
                monetary_cost=None if dispatched else 0.0,
                paid_monetary_cost=None if dispatched else 0.0,
                model_calls=int(dispatched),
                wall_seconds=elapsed,
                raw_metadata={
                    "timeout": True,
                    "call_kind": request.kind.value,
                    "agent_id": request.agent_id,
                    "timeout_seconds": timeout,
                },
            )
            usage = getattr(exc.__cause__, "usage_record", None) or usage
            record["usage"] = asdict(usage)
            raise _ModelCallTimeout(request, usage) from exc
        except Exception as exc:
            record["error"] = f"{type(exc).__name__}: {exc}"
            elapsed = time.perf_counter() - started
            usage = UsageRecord(
                input_tokens=None if dispatched else 0,
                output_tokens=None if dispatched else 0,
                monetary_cost=None if dispatched else 0.0,
                paid_monetary_cost=None if dispatched else 0.0,
                model_calls=int(dispatched),
                wall_seconds=elapsed,
                raw_metadata={
                    "failed": True,
                    "call_kind": request.kind.value,
                    "agent_id": request.agent_id,
                    "error_type": type(exc).__name__,
                },
            )
            record["usage"] = asdict(usage)
            if not self.config.failure_policy.enabled:
                # The disabled policy must not silently gain tolerance through
                # an adapter error that has no resource receipt. Preserve the
                # original failure and unknown usage before the strict stop.
                try:
                    normalized_cost(usage, self.config.costs)
                except AccountingFailure as accounting_error:
                    self._fatal_error = accounting_error
                    raise accounting_error from exc
            raise _ModelCallFailure(request, usage, exc) from exc
        finally:
            record["elapsed_seconds"] = time.perf_counter() - started
            usage_fields = record.get("usage", {})
            observed_usage = UsageRecord(**usage_fields) if usage_fields else None
            physical: dict[int, Mapping[str, Any]] = {}

            def collect(value: Any) -> None:
                if isinstance(value, Mapping):
                    if isinstance(value.get("physical_dispatch_id"), int):
                        physical.setdefault(value["physical_dispatch_id"], value)
                    for child in value.values():
                        collect(child)
                elif isinstance(value, (tuple, list)):
                    for child in value:
                        collect(child)

            collect(usage_fields.get("raw_metadata", {}))
            calls = usage_fields.get("model_calls")
            rejection = any(
                term in record.get("error", "")
                for term in (
                    "input-token-limit",
                    "context-limit",
                    "output-token-cap",
                    "budget",
                    "campaign dispatch halted",
                )
            )
            dispatched_ids = [index for index, item in sorted(physical.items()) if item.get("model_calls", 1) > 0]
            record.update(
                admission_status=("admitted" if physical or calls else "rejected" if rejection else "unavailable"),
                physical_dispatch_ids=dispatched_ids,
                physical_dispatch_count=calls,
                valid_output=(record["structured_output"] is not None if "structured_output" in record else None),
                repair_attempt=logical.repair_attempt,
                truncated_dispatch_ids=[
                    index
                    for index, item in sorted(physical.items())
                    if item.get("finish_reason") in {"length", "max_tokens", "max_output_tokens"}
                ],
                transport_retry_dispatch_ids=[
                    index for index, item in sorted(physical.items()) if item.get("transport_attempt", 0) > 0
                ],
                accounting_complete=bool(observed_usage is not None and observed_usage.accounting_complete),
                usage_missing=list(observed_usage.missing_coordinates) if observed_usage is not None else ["usage"],
            )
            self._call_records.append(record)

    def _execution_counters(self, trajectory: Trajectory) -> dict[str, Any]:
        """Report measured requests and dispatches, including failed-session calls."""

        by_kind: dict[str, dict[str, Any]] = {}
        for kind in AgentCallKind:
            records = [item for item in self._call_records if item["logical_call"]["call_type"] == kind.value]
            identifiers = {index for item in records for index in item["physical_dispatch_ids"]}
            by_kind[kind.value] = {
                "requested": len(records),
                "admitted": sum(item["admission_status"] == "admitted" for item in records),
                "rejected": sum(item["admission_status"] == "rejected" for item in records),
                "admission_unavailable": sum(item["admission_status"] == "unavailable" for item in records),
                "dispatched": (
                    sum(item["physical_dispatch_count"] for item in records)
                    if all(item.get("physical_dispatch_count") is not None for item in records)
                    else None
                ),
                "physical_dispatch_ids": sorted(identifiers),
                "valid": sum(item["valid_output"] is True for item in records),
                "validity_unavailable": sum(item["valid_output"] is None for item in records),
            }
        all_traces = [trace for session in trajectory.sessions for trace in session.traces]
        completed_tags = {trace.tag for trace in all_traces}
        all_traces.extend(trace for trace in trajectory.failed_session_traces if trace.tag not in completed_tags)
        fallbacks: dict[str, int] = {}
        for item in self._fallback_records:
            fallbacks[item["kind"]] = fallbacks.get(item["kind"], 0) + 1
        return {
            "protocol": "explicit_execution_outcomes_v1",
            "call_types": by_kind,
            "scheme_conversations": {
                scheme.value: sum(trace.tag.scheme == scheme for trace in all_traces)
                for scheme in self.config.scheme_order
                if scheme is not Scheme.ZERO
            },
            "repair_requests": sum(item["repair_attempt"] > 0 for item in self._call_records),
            "repair_dispatches": sum(
                item.get("physical_dispatch_count") or 0 for item in self._call_records if item["repair_attempt"] > 0
            ),
            "transport_retry_dispatch_ids": sorted(
                {index for item in self._call_records for index in item["transport_retry_dispatch_ids"]}
            ),
            "truncated_dispatch_ids": sorted(
                {index for item in self._call_records for index in item["truncated_dispatch_ids"]}
            ),
            "safeguards": {
                trigger.value: sum(trace.safeguard_trigger is trigger for trace in all_traces)
                for trigger in SafeguardTrigger
            },
            "fallbacks": fallbacks,
        }

    @staticmethod
    def _aggregate_attempt_usage(attempts: tuple[ValidationAttempt, ...]) -> UsageRecord:
        total = sum((attempt.usage for attempt in attempts), UsageRecord())
        return replace(
            total,
            raw_metadata={
                "attempts": [
                    {
                        "index": attempt.index,
                        "raw_output": attempt.raw_output,
                        "structured_output": attempt.structured_output,
                        "validation_errors": attempt.validation_errors,
                        "usage": attempt.usage.raw_metadata,
                        "normalized_cost": attempt.normalized_cost,
                    }
                    for attempt in attempts
                ]
            },
        )

    async def _validated_call(
        self,
        request: AgentRequest,
        expected_type: type,
        context_validator: Callable[[ParsedOutput], tuple[ParsedOutput | None, str | None]],
    ) -> ParsedOutput:
        """One logical provider outcome, including every allowed schema repair."""

        agent = self.agents[request.agent_id]
        begin = getattr(agent, "begin_logical_request", None)
        finish = getattr(agent, "finish_logical_request", None)
        try:
            token = begin(request) if begin is not None else None
        except PolicyStopped as exc:
            self._drain_error = exc
            raise _ModelCallFailure(request, UsageRecord(raw_metadata={"not_dispatched": True}), exc) from exc
        if token is not None:
            request = replace(request, context={**request.context, "provider_logical_request_id": token})
        first_call_id = self._next_call_id
        result: ParsedOutput | None = None
        failure: BaseException | None = None
        try:
            result = await self._validated_attempts(request, expected_type, context_validator)
            return result
        except BaseException as error:
            failure = error
            result = getattr(error, "output", None)
            raise
        finally:
            if token is not None and finish is not None:
                finish(token)
                flush = getattr(agent, "flush_accounting", None)
                if flush is not None:
                    await flush()
            # Compact parsed progress refers to physical receipts; it neither
            # repeats complete requests/raw streams nor marks a session safe.
            message = request.message
            status = getattr(result, "status", None)
            await self._record_progress_event("logical-call-result", {
                "example_id": self._generation_context.example_id if self._generation_context else None,
                "provider_logical_request_id": token,
                "runtime_call_id_start": first_call_id,
                "runtime_call_id_end": self._next_call_id,
                "kind": request.kind.value,
                "agent_id": request.agent_id,
                "session": message.session if message is not None else request.context.get("session", 0),
                "tag": str(message.tag) if message is not None and message.tag is not None else None,
                "round": message.round_index if message is not None else request.context.get("round", 0),
                "answer": getattr(result, "answer", None),
                "explanation": getattr(result, "explanation", None),
                "feedback": getattr(result, "feedback", None),
                "status": getattr(status, "value", status),
                "structured_output_available": getattr(result, "structured_output", None) is not None,
                "validation_exhausted": getattr(result, "validation_exhausted", False),
                "error": str(failure) if failure is not None else getattr(result, "error", None),
                "cancelled": isinstance(failure, asyncio.CancelledError),
            })

    async def _validated_attempts(
        self,
        request: AgentRequest,
        expected_type: type,
        context_validator: Callable[[ParsedOutput], tuple[ParsedOutput | None, str | None]],
    ) -> ParsedOutput:
        """Apply one shared schema/context repair budget and retain every attempt."""

        context = dict(request.context)
        context["repair_base_prompt"] = request.prompt
        context["repair_enabled"] = self.config.safety.parse_retries > 0
        context["logical_deadline_monotonic"] = (
            asyncio.get_running_loop().time() + self.config.safety.call_timeout_seconds
        )
        if self._session_deadline is not None:
            context["session_deadline_monotonic"] = self._session_deadline
            context["logical_deadline_monotonic"] = min(context["logical_deadline_monotonic"], self._session_deadline)
        identity = self.config.agent_pool[request.agent_id]
        context["agent_instructions"] = "\n".join(
            value for value in (identity.system_configuration, identity.role_configuration) if value
        )
        if request.message is not None:
            message = request.message
            context["component_fields"] = {
                "query": message.query,
                "answer": message.answer,
                "explanation": message.explanation,
                "feedback": message.feedback,
                "message_text": message.text,
                "tag": str(message.tag) if message.tag else None,
                "session": message.session,
                "round": message.round_index,
                "sender": message.sender_id,
                "receiver": message.receiver_id,
            }
        context["protocol_instructions"] = protocol_instructions(request.prompt_component, context)
        context["effective_input"] = compose_backend_input(
            context["agent_instructions"], context["protocol_instructions"], request.prompt
        )
        request = replace(request, context=context)
        attempts: list[ValidationAttempt] = []
        validation_errors: list[str] = []
        last_output: ParsedOutput | None = None
        last_error = "invalid structured output"
        prompt = request.prompt
        for generation in range(self.config.safety.parse_retries + 1):
            call_id = self._next_call_id
            self._next_call_id += 1
            attempt_request = replace(
                request,
                prompt=prompt,
                max_repair_generations=0,
                context={**request.context, "repair_attempt": generation, "runtime_call_id": call_id},
            )
            try:
                output = await self._call(attempt_request)
            except (
                _ModelCallTimeout,
                _ModelCallFailure,
                asyncio.CancelledError,
                BudgetExhausted,
                AccountingFailure,
            ) as exc:
                # Completed invalid generations remain chargeable if a later
                # repair is interrupted, and the component was still used.
                from ..providers.adapter import GenerationResult, parsed_output

                tag = request.message.tag if request.message is not None else None
                current_usage = (
                    self._cancelled_call_usage.get(tag, UsageRecord(model_calls=1))
                    if isinstance(exc, asyncio.CancelledError)
                    else (
                        exc.usage_record or UsageRecord(raw_metadata={"not_dispatched": True})
                        if isinstance(exc, ResourceFailure)
                        else exc.usage
                    )
                )
                error = "generation cancelled" if isinstance(exc, asyncio.CancelledError) else str(exc)
                interrupted_attempts = (
                    *attempts,
                    ValidationAttempt(
                        len(attempts) + 1,
                        "",
                        None,
                        (error,),
                        current_usage,
                        normalized_cost=self._attempt_cost(current_usage),
                    ),
                )
                total_usage = self._aggregate_attempt_usage(interrupted_attempts)
                total_usage = replace(
                    total_usage, raw_metadata={**total_usage.raw_metadata, "provider_only_failure": True}
                )
                failed = parsed_output(
                    request,
                    GenerationResult(
                        "",
                        None,
                        None,
                        total_usage.wall_seconds,
                        attempts=len(interrupted_attempts),
                        error=error,
                        parse_errors=[*validation_errors, error],
                        attempt_records=list(interrupted_attempts),
                    ),
                )
                failed = replace(failed, usage=total_usage)
                self._prompt_calls.append((request, failed))
                if tag is not None:
                    self._interrupted_outputs[tag] = failed
                if isinstance(exc, asyncio.CancelledError):
                    if tag is not None:
                        self._cancelled_call_usage[tag] = total_usage
                elif isinstance(exc, ResourceFailure):
                    exc.usage_record = total_usage
                else:
                    exc.usage = total_usage
                    exc.output = failed
                raise
            if not isinstance(output, expected_type):
                raise TypeError(f"{request.kind.value} generation must return {expected_type.__name__}")
            output = cast(ParsedOutput, output)
            last_output = output
            records = output.attempts or (
                ValidationAttempt(
                    1,
                    output.raw_content,
                    output.structured_output,
                    tuple(output.parse_errors or ((output.error,) if output.error else ())),
                    output.usage,
                ),
            )
            normalized_records = [
                replace(
                    record,
                    index=len(attempts) + offset,
                    normalized_cost=self._attempt_cost(record.usage),
                )
                for offset, record in enumerate(records, start=1)
            ]

            validated: ParsedOutput | None = None
            context_error: str | None = None
            if output.structured_output is None:
                last_error = output.error or (
                    output.parse_errors[-1] if output.parse_errors else "structured-output schema validation failed"
                )
            else:
                validated, context_error = context_validator(output)
                if context_error is not None:
                    last_error = context_error
                    final_record = normalized_records[-1]
                    normalized_records[-1] = replace(
                        final_record,
                        validation_errors=(*final_record.validation_errors, context_error),
                    )

            attempts.extend(normalized_records)
            validation_errors.extend(error for record in normalized_records for error in record.validation_errors)
            call_record = next(item for item in reversed(self._call_records) if item.get("request_id") == call_id)
            call_record["valid_output"] = validated is not None
            call_record["validation_error"] = None if validated is not None else last_error
            if validated is not None:
                result = replace(
                    validated,
                    usage=self._aggregate_attempt_usage(tuple(attempts)),
                    error=None,
                    parse_errors=tuple(validation_errors),
                    attempts=tuple(attempts),
                    validation_exhausted=False,
                )
                self._prompt_calls.append((request, result))
                return result

            provider_failure = output.structured_output is None and output.error is not None and not output.parse_errors
            if provider_failure:
                last_output = replace(
                    output,
                    usage=replace(
                        output.usage, raw_metadata={**output.usage.raw_metadata, "provider_only_failure": True}
                    ),
                )
                break
            prompt = render_repair(
                request.prompt,
                select_repair_diagnostic(
                    last_error,
                    context_invalid=context_error is not None and request.kind is not AgentCallKind.ROLE,
                    kind=request.kind.value,
                ),
            )

        if last_output is None:  # pragma: no cover - loop always executes once
            raise RuntimeError("structured-output validation made no generation attempt")
        error = f"{request.kind.value} validation exhausted after {len(attempts)} " f"attempt(s): {last_error}"
        result = replace(
            last_output,
            usage=replace(
                self._aggregate_attempt_usage(tuple(attempts)),
                raw_metadata={
                    **self._aggregate_attempt_usage(tuple(attempts)).raw_metadata,
                    "provider_only_failure": bool(last_output.usage.raw_metadata.get("provider_only_failure")),
                },
            ),
            error=error,
            parse_errors=tuple(validation_errors),
            attempts=tuple(attempts),
            validation_exhausted=True,
        )
        self._prompt_calls.append((request, result))
        return result

    def _attempt_cost(self, usage: UsageRecord) -> float | None:
        try:
            return normalized_cost(usage, self.config.costs)
        except AccountingFailure:
            return None

    @staticmethod
    def _validate_role_output(output: ParsedOutput) -> tuple[ParsedOutput | None, str | None]:
        if not isinstance(output, RoleOutput):
            return None, "role output has the wrong structured type"
        if output.status not in {ConversationStatus.CONTINUE, ConversationStatus.COMPLETE}:
            return None, "role status must be 'continue' or 'complete'"
        if not output.feedback.strip():
            return None, "role feedback is required"
        return output, None

    def _answer_context_validator(
        self,
        example: QueryExample,
        allowed_answers: tuple[str, ...],
        label: str,
    ) -> Callable[[ParsedOutput], tuple[ParsedOutput | None, str | None]]:
        def validate(output: ParsedOutput) -> tuple[ParsedOutput | None, str | None]:
            if not isinstance(output, (AgentOutput, JudgeOutput, TieOutput)):
                return None, f"{label} output has the wrong structured type"
            try:
                answer = self._canonical(output.answer, example)
            except ValueError as exc:
                return None, (
                    f"{label} answer is context-invalid: {exc}; allowed canonical " f"answers are {allowed_answers!r}"
                )
            if (allowed_answers or not example.open_answer) and answer not in allowed_answers:
                return None, (
                    f"{label} selected canonical answer {answer!r} outside the allowed " f"set {allowed_answers!r}"
                )
            return replace(output, answer=answer), None

        return validate

    def _canonical(self, answer: str, example: QueryExample) -> str:
        canonicalizer = (
            StrategyQAAnswerCanonicalizer() if set(example.candidates) == {"yes", "no"} else self.canonicalizer
        )
        value = canonicalizer.canonicalize(answer, example)
        if example.open_answer and value:
            return value
        for candidate in example.candidates:
            if canonicalizer.equal(value, candidate, example):
                return candidate
        raise ValueError(f"answer {answer!r} is outside the dataset answer space")

    @staticmethod
    def _initial_prompt(example: QueryExample) -> str:
        # Specification Step 1: no peer answer or collaboration history appears here.
        options = ""
        if example.option_texts:
            options += f"Option text: {json.dumps(example.option_texts, ensure_ascii=False)}\n"
        return f"Initial template: {INITIAL_TEMPLATE_ID}\nQuery: {example.query}\n{options}"

    async def _initial_answers(self, example: QueryExample) -> tuple[AgentOutput, ...]:
        # Sequential dispatch ensures a terminal invalid initialization never
        # schedules another agent; each agent owns its independent repair budget.
        for agent_id in self.config.agent_ids:
            request = AgentRequest(
                AgentCallKind.INITIAL,
                agent_id,
                self._initial_prompt(example),
                "initial",
                context={"example_id": example.id, "admissible_answers": example.candidates},
            )
            try:
                output = await self._validated_call(
                    request, AgentOutput, self._answer_context_validator(example, example.candidates, "initial")
                )
            except (_ModelCallTimeout, _ModelCallFailure) as exc:
                if isinstance(exc.output, AgentOutput):
                    self._initial_records[agent_id] = exc.output
                raise
            assert isinstance(output, AgentOutput)
            self._initial_records[agent_id] = output
            if output.error is not None or output.validation_exhausted:
                raise ResourceFailure(
                    f"initialization-failed: agent {agent_id}: {output.error}", usage_record=output.usage
                )
        return tuple(self._initial_records[agent] for agent in self.config.agent_ids)

    def _route(
        self,
        state: SessionStartState,
        mode: RoutingMode,
        generator: torch.Generator | None,
    ) -> RoutingAction:
        device = next(self.routing_policy.parameters()).device
        query = self.encoders.encode_query(state.example, device=device)
        self._commit_history(state.history)
        history = self._history_cache.snapshot(device=device)
        outgoing, incoming = self.config.capacity.resolve(self.config.n_agents)
        if mode is RoutingMode.TRAIN:
            return self.routing_policy.route_training(
                query,
                history,
                state.answers,
                outgoing,
                incoming,
                session=state.session,
                generator=generator,
                answer_views=answer_views(state.example, state.answers),
            )
        was_training = self.routing_policy.training
        self.routing_policy.eval()
        try:
            with torch.no_grad():
                return self.routing_policy.route_inference(
                    query,
                    history,
                    state.answers,
                    outgoing,
                    incoming,
                    session=state.session,
                    answer_views=answer_views(state.example, state.answers),
                )
        finally:
            self.routing_policy.train(was_training)

    def _weight(
        self,
        example: QueryExample,
        history: tuple[HistoryRecord, ...],
        answers: tuple[str, ...],
    ):
        device = next(self.weighting_model.parameters()).device
        query = self.encoders.encode_query(example, device=device)
        self._commit_history(history)
        encoded_history = self._history_cache.snapshot(device=device)
        output = self.weighting_model(query, encoded_history)
        vote = self.weighting_model.vote(output.weights, answers, candidate_order=example.candidates)
        scores = {
            str(candidate): float(vote.scores[index].detach().cpu()) for index, candidate in enumerate(vote.candidates)
        }
        return output.logits, output.weights, scores, str(vote.prediction)

    def _init_message(
        self,
        state: SessionStartState,
        tag: ConversationTag,
        explanations: dict[int, str],
    ) -> StructuredMessage:
        key = f"init:{tag.scheme.value}"
        request = initiation_request(tag.scheme.value, self.config.scheme_registry)
        prompt = self.prompts.render(
            key,
            tag=str(tag),
            scheme=tag.scheme.value,
            initiator=tag.initiator_id,
            receiver=tag.receiver_id,
            query=state.example.query,
            answer=state.answers[tag.initiator_id],
            explanation=explanations.get(tag.initiator_id, ""),
            request=request,
        )
        return StructuredMessage(
            format=MessageFormat.INITIATION,
            sender_id=tag.initiator_id,
            receiver_id=tag.receiver_id,
            session=tag.session,
            round_index=0,
            tag=tag,
            query=state.example.query,
            answer=state.answers[tag.initiator_id],
            explanation=explanations.get(tag.initiator_id, ""),
            scheme_request=request,
            prompt_component=key,
            text=prompt,
            explanation_source=self._explanation_sources.get(tag.initiator_id),
        )

    async def _execute_session(
        self,
        state: SessionStartState,
        routing: RoutingAction,
        explanations: dict[int, str],
    ) -> tuple[
        dict[int, tuple[TerminalRecord, ...]],
        tuple[ConversationTrace, ...],
        tuple[HistoryRecord, ...],
        UsageRecord,
    ]:
        from ..models.policies import legal_schemes
        for edge, label in routing.matrix.items():
            if label not in legal_schemes(*edge, self.config.scheme_order, self.config.scheme_legality):
                raise ValueError("routing selected an unregistered or illegal scheme")
        traces: dict[ConversationTag, ConversationTrace] = {}
        self._active_traces = traces
        raw: dict[int, list[TerminalRecord]] = {agent: [] for agent in self.config.agent_ids}
        for initiator, receiver in routing.selected_edges:
            tag = ConversationTag(state.session, initiator, receiver, routing.matrix[(initiator, receiver)])
            message = self._init_message(state, tag, explanations)
            traces[tag] = ConversationTrace(tag=tag, messages=[message])
        if not traces:
            return {agent: () for agent in self.config.agent_ids}, (), state.history, UsageRecord()

        mailboxes = {agent: AgentMailbox(agent) for agent in self.config.agent_ids}
        self.last_mailboxes = mailboxes
        completed = 0
        self._cancelled_call_usage = {}
        self._interrupted_outputs = {}
        completed_event = asyncio.Event()
        stopping = False
        fatal_errors: list[ResourceFailure] = []
        delivery_index = 0
        processing_index = 0
        delivered_at: dict[int, int] = {}
        processed_at: dict[int, int] = {}
        shared_failed_usage = UsageRecord()
        shared_judge_outputs: list[tuple[int, JudgeOutput, tuple[ConversationTag, ...], int]] = []
        persisted_messages: dict[ConversationTag, int] = {tag: 0 for tag in traces}
        persisted_terminals: set[ConversationTag] = set()
        persisted_fallbacks = len(self._fallback_records)
        progress_lock = asyncio.Lock()

        async def persist_session_progress() -> None:
            """Append only new messages/terminals, including ordinary fallbacks."""
            nonlocal persisted_fallbacks
            if self.event_observer is None:
                return
            async with progress_lock:
                messages: list[dict[str, Any]] = []
                terminals: list[dict[str, Any]] = []
                for tag, trace in traces.items():
                    messages.extend(asdict(message) for message in trace.messages[persisted_messages[tag]:])
                    persisted_messages[tag] = len(trace.messages)
                    if trace.completed and tag not in persisted_terminals:
                        terminals.append({
                            "terminal": asdict(trace.terminal),
                            "status": trace.completion_status.value if trace.completion_status else None,
                            "error": trace.error,
                            "safeguard_reason": trace.safeguard_reason,
                            "safeguard_trigger": trace.safeguard_trigger.value if trace.safeguard_trigger else None,
                        })
                        persisted_terminals.add(tag)
                fallbacks = self._fallback_records[persisted_fallbacks:]
                persisted_fallbacks = len(self._fallback_records)
                if messages or terminals or fallbacks:
                    await self._record_progress_event("conversation-progress", {
                        "example_id": state.example.example_id,
                        "session": state.session,
                        "messages_append": messages,
                        "terminals_append": terminals,
                        "fallbacks_append": fallbacks,
                        "session_complete": False,
                    })

        async def deliver(
            agent: int,
            *,
            message: StructuredMessage | None = None,
            batch: _JudgeBatch | None = None,
        ) -> None:
            nonlocal delivery_index
            raise_if_stopping()
            if stopping:
                raise asyncio.CancelledError("session cleanup")
            if self._fatal_error is not None:
                raise self._fatal_error
            item_delivery_index = delivery_index
            delivered_messages = (message,) if message is not None else batch.messages
            for delivered_message in delivered_messages:
                assert delivered_message is not None
                delivered_at[id(delivered_message)] = delivery_index
                delivery_index += 1
            item = _WorkItem(item_delivery_index, message=message, judge_batch=batch)
            await mailboxes[agent].deliver(item)

        def latest_valid_initiator_answer(tag: ConversationTag) -> str:
            for message in reversed(traces[tag].messages):
                if message.answer:
                    return message.answer
            return state.answers[tag.initiator_id]

        def finish(
            tag: ConversationTag,
            answer: str,
            error: str | None = None,
            *,
            status: ConversationStatus = ConversationStatus.COMPLETE,
            safeguard_trigger: SafeguardTrigger | None = None,
        ) -> None:
            nonlocal completed
            trace = traces[tag]
            if trace.completed:
                return
            terminal = TerminalRecord(tag.session, tag.initiator_id, answer, tag)
            trace.error = error
            trace.finish(
                terminal,
                status=status,
                safeguard_reason=(error if status is ConversationStatus.SAFEGUARD_COMPLETE else None),
                safeguard_trigger=safeguard_trigger,
            )
            if status is ConversationStatus.SAFEGUARD_COMPLETE:
                self._fallback_records.append(
                    {
                        "kind": "conversation-safeguard",
                        "session": tag.session,
                        "agent_id": tag.initiator_id,
                        "tag": str(tag),
                        "answer": answer,
                        "reason": error,
                        "trigger": safeguard_trigger.value if safeguard_trigger else None,
                    }
                )
            raw[tag.initiator_id].append(terminal)
            completed += 1
            if completed == len(traces):
                completed_event.set()

        def finish_at_round_limit(
            tag: ConversationTag,
            answer: str,
            round_index: int,
        ) -> None:
            limit = self.config.safety.max_internal_rounds
            finish(
                tag,
                answer,
                ("conversation reached internal-round limit " f"max_internal_rounds={limit} at round {round_index}"),
                status=ConversationStatus.SAFEGUARD_COMPLETE,
                safeguard_trigger=SafeguardTrigger.ROUND_LIMIT,
            )

        async def role_reply(message: StructuredMessage) -> None:
            tag = message.tag
            assert tag is not None
            trace = traces[tag]
            next_round = message.round_index + 1
            if next_round > self.config.safety.max_internal_rounds:
                finish_at_round_limit(
                    tag,
                    message.answer or state.answers[tag.initiator_id],
                    message.round_index,
                )
                return
            key = f"role:{tag.scheme.value}"
            prompt = self.prompts.render(
                key,
                message_text=message.text,
            )
            output = await self._validated_call(
                AgentRequest(
                    AgentCallKind.ROLE,
                    tag.receiver_id,
                    prompt,
                    key,
                    message,
                    {
                        "tag": str(tag),
                        "round": next_round,
                        "allowed_statuses": ("continue", "complete"),
                        "admissible_answers": state.example.candidates,
                    },
                ),
                RoleOutput,
                self._validate_role_output,
            )
            assert isinstance(output, RoleOutput)
            if output.validation_exhausted:
                trace.record_output(output)
                finish(
                    tag,
                    message.answer or state.answers[tag.initiator_id],
                    output.error,
                    status=ConversationStatus.SAFEGUARD_COMPLETE,
                    safeguard_trigger=SafeguardTrigger.VALIDATION_EXHAUSTED,
                )
                return
            status = output.status
            response = StructuredMessage(
                MessageFormat.ROLE_RESPONSE,
                tag.receiver_id,
                tag.initiator_id,
                tag.session,
                next_round,
                tag=tag,
                query=state.example.query,
                answer=message.answer,
                explanation=message.explanation,
                feedback=output.feedback,
                status=status,
                prompt_component=key,
                explanation_source=message.explanation_source,
            )
            trace.append(response, output)
            repeated = trace.feedback_state.observe(message.answer or state.answers[tag.initiator_id], output.feedback)
            if status is ConversationStatus.CONTINUE and repeated >= 2:
                finish(
                    tag,
                    message.answer or state.answers[tag.initiator_id],
                    "repeated-feedback",
                    status=ConversationStatus.SAFEGUARD_COMPLETE,
                    safeguard_trigger=SafeguardTrigger.REPEATED_FEEDBACK,
                )
                return
            if status is ConversationStatus.CONTINUE and next_round == self.config.safety.max_internal_rounds:
                finish_at_round_limit(
                    tag,
                    response.answer or state.answers[tag.initiator_id],
                    next_round,
                )
                return
            await deliver(tag.initiator_id, message=response)

        async def revision(message: StructuredMessage) -> None:
            tag = message.tag
            assert tag is not None
            trace = traces[tag]
            if message.status is ConversationStatus.COMPLETE:
                finish(tag, message.answer or state.answers[tag.initiator_id], trace.error)
                return
            next_round = message.round_index + 1
            if next_round > self.config.safety.max_internal_rounds:
                finish_at_round_limit(
                    tag,
                    message.answer or state.answers[tag.initiator_id],
                    message.round_index,
                )
                return
            key = f"revision:{tag.scheme.value}"
            prompt = self.prompts.render(
                key,
                message_text=message.text,
            )
            output = await self._validated_call(
                AgentRequest(
                    AgentCallKind.REVISION,
                    tag.initiator_id,
                    prompt,
                    key,
                    message,
                    {
                        "tag": str(tag),
                        "round": next_round,
                        "admissible_answers": state.example.candidates,
                    },
                ),
                AgentOutput,
                self._answer_context_validator(
                    state.example,
                    state.example.candidates,
                    "revision",
                ),
            )
            assert isinstance(output, AgentOutput)
            if output.validation_exhausted:
                trace.record_output(output)
                finish(
                    tag,
                    message.answer or state.answers[tag.initiator_id],
                    output.error,
                    status=ConversationStatus.SAFEGUARD_COMPLETE,
                    safeguard_trigger=SafeguardTrigger.VALIDATION_EXHAUSTED,
                )
                return
            answer = output.answer
            response = StructuredMessage(
                MessageFormat.REVISION_RESPONSE,
                tag.initiator_id,
                tag.receiver_id,
                tag.session,
                next_round,
                tag=tag,
                query=state.example.query,
                answer=answer,
                explanation=output.explanation,
                feedback=message.feedback,
                prompt_component=key,
                explanation_source=ExplanationReference(
                    "revision", tag.initiator_id, tag.session, str(tag), next_round
                ),
            )
            trace.append(response, output)
            if tag.scheme not in {Scheme.ZERO, Scheme.JUDGE}:
                prior = message.answer or state.answers[tag.initiator_id]
                trace.unchanged_revisions = trace.unchanged_revisions + 1 if answer == prior else 0
                if trace.unchanged_revisions >= self.config.safety.answer_stability_patience:
                    finish(
                        tag, answer, "answer-stability",
                        status=ConversationStatus.SAFEGUARD_COMPLETE,
                        safeguard_trigger=SafeguardTrigger.ANSWER_STABILITY,
                    )
                    return
            if next_round == self.config.safety.max_internal_rounds:
                finish_at_round_limit(tag, response.answer or answer, next_round)
                return
            await deliver(tag.receiver_id, message=response)

        async def judge(batch: _JudgeBatch) -> None:
            nonlocal delivery_index
            messages = batch.messages
            tags = tuple(message.tag for message in messages if message.tag is not None)
            canonical_submissions = tuple(self._canonical(message.answer or "", state.example) for message in messages)
            allowed_submitted_answers = tuple(dict.fromkeys(canonical_submissions))
            if not allowed_submitted_answers:
                raise ValueError("judge batch has no canonical submitted answers")
            submission_records = [
                {
                    "initiator": message.sender_id,
                    "answer": canonical_answer,
                    "explanation": message.explanation,
                    "tag": str(message.tag),
                    "text": message.text,
                    "metadata": {
                        "session": message.session,
                        "round": message.round_index,
                        "scheme": message.tag.scheme.value if message.tag else None,
                    },
                }
                for message, canonical_answer in zip(messages, canonical_submissions, strict=True)
            ]
            submissions = json.dumps([message.text for message in messages], ensure_ascii=False)
            key = "judge:S_J"
            prompt = self.prompts.render(
                key,
                submissions=submissions,
            )
            output = await self._validated_call(
                AgentRequest(
                    AgentCallKind.JUDGE,
                    batch.judge_id,
                    prompt,
                    key,
                    messages[0],
                    {
                        "query": state.example.query,
                        "submissions": submission_records,
                        "submitted_messages": [asdict(message) for message in messages],
                        "tags": [str(tag) for tag in tags],
                        "allowed_submitted_answers": allowed_submitted_answers,
                    },
                ),
                JudgeOutput,
                self._answer_context_validator(
                    state.example,
                    allowed_submitted_answers,
                    "judge",
                ),
            )
            assert isinstance(output, JudgeOutput)
            if output.validation_exhausted:
                selected = allowed_submitted_answers[0]
                output = replace(
                    output,
                    answer=selected,
                    error=(
                        "adjudication-validation error: "
                        f"{output.error}; falling back to first submitted answer "
                        f"{selected!r}"
                    ),
                )
                self._fallback_records.append(
                    {
                        "kind": "judge-fallback",
                        "session": state.session,
                        "agent_id": batch.judge_id,
                        "tags": [str(tag) for tag in tags],
                        "answer": selected,
                        "reason": "validation-exhausted",
                        "source_event": event_reference(messages[0]),
                    }
                )
            else:
                selected = output.answer
            adjudication_event_index = delivery_index
            delivery_index += 1
            shared_judge_outputs.append((batch.judge_id, output, tags, adjudication_event_index))
            total = self._attempt_cost(output.usage)
            for message, tag in zip(messages, tags, strict=True):
                trace = traces[tag]
                trace.outputs.append(output)
                if output.error is not None:
                    trace.error = output.error
                trace.judge_group_size = len(tags)
                trace.judge_event_ref = f"s{state.session}:judge:{batch.judge_id}"
                trace.normalized_cost = None if total is None else total / len(tags)
                returned = StructuredMessage(
                    MessageFormat.JUDGE_RETURN,
                    batch.judge_id,
                    tag.initiator_id,
                    tag.session,
                    1,
                    tag=tag,
                    query=state.example.query,
                    answer=selected,
                    status=ConversationStatus.COMPLETE,
                    prompt_component=key,
                )
                trace.messages.append(returned)
                await deliver(tag.initiator_id, message=returned)

        async def process(item: _WorkItem) -> None:
            if item.judge_batch is not None:
                await judge(item.judge_batch)
                return
            message = item.message
            assert message is not None
            if message.format in {MessageFormat.INITIATION, MessageFormat.REVISION_RESPONSE}:
                await role_reply(message)
            elif message.format is MessageFormat.ROLE_RESPONSE:
                await revision(message)
            elif message.format is MessageFormat.JUDGE_RETURN:
                assert message.tag is not None
                finish(
                    message.tag,
                    message.answer or state.answers[message.tag.initiator_id],
                    traces[message.tag].error,
                )
            else:
                raise RuntimeError(f"unexpected queued message format: {message.format}")

        async def worker(mailbox: AgentMailbox) -> None:
            nonlocal processing_index, shared_failed_usage
            while True:
                raise_if_stopping()
                if stopping:
                    return
                item = await mailbox.begin()
                if item is None:
                    mailbox.queue.task_done()
                    return
                processed_messages = (item.message,) if item.message is not None else item.judge_batch.messages
                for processed_message in processed_messages:
                    assert processed_message is not None
                    processed_at[id(processed_message)] = processing_index
                    processing_index += 1
                try:
                    if fatal_errors or self._fatal_error is not None:
                        return
                    await process(item)
                except (BudgetExhausted, AccountingFailure) as exc:
                    self._fatal_error = exc
                    fatal_errors.append(exc)
                    failed_usage = exc.usage_record or UsageRecord()
                    if item.message is not None and item.message.tag is not None:
                        traces[item.message.tag].usage = traces[item.message.tag].usage + failed_usage
                    elif item.judge_batch is not None:
                        shared_failed_usage = shared_failed_usage + failed_usage
                        first_tag = item.judge_batch.messages[0].tag
                        if first_tag is not None:
                            traces[first_tag].usage = traces[first_tag].usage + failed_usage
                    completed_event.set()
                    return
                except (_ModelCallTimeout, _ModelCallFailure) as exc:
                    affected = (item.message,) if item.message is not None else item.judge_batch.messages
                    is_shared_judge = item.judge_batch is not None
                    if is_shared_judge:
                        shared_failed_usage = shared_failed_usage + exc.usage
                        self._fallback_records.append(
                            {
                                "kind": "judge-fallback",
                                "session": state.session,
                                "agent_id": item.judge_batch.judge_id,
                                "tags": [str(message.tag) for message in affected],
                                "answer": affected[0].answer,
                                "reason": type(exc).__name__,
                                "source_event": event_reference(affected[0]),
                            }
                        )
                    for position, message in enumerate(affected):
                        assert message is not None and message.tag is not None
                        tag = message.tag
                        trace = traces[tag]
                        if not is_shared_judge or position == 0:
                            trace.usage = trace.usage + exc.usage
                        if is_shared_judge:
                            trace.judge_group_size = len(affected)
                            cost = self._attempt_cost(exc.usage)
                            trace.normalized_cost = None if cost is None else cost / len(affected)
                        finish(
                            tag,
                            (
                                affected[0].answer or state.answers[affected[0].sender_id]
                                if is_shared_judge
                                else latest_valid_initiator_answer(tag)
                            ),
                            str(exc),
                            status=ConversationStatus.SAFEGUARD_COMPLETE,
                            safeguard_trigger=(
                                SafeguardTrigger.CALL_TIMEOUT
                                if isinstance(exc, _ModelCallTimeout)
                                else SafeguardTrigger.WORKER_FAILURE
                            ),
                        )
                except Exception as exc:
                    if item.message is not None and item.message.tag is not None:
                        tag = item.message.tag
                        finish(
                            tag,
                            latest_valid_initiator_answer(tag),
                            f"{type(exc).__name__}: {exc}",
                            status=ConversationStatus.SAFEGUARD_COMPLETE,
                            safeguard_trigger=SafeguardTrigger.WORKER_FAILURE,
                        )
                    elif item.judge_batch is not None:
                        selected = (
                            item.judge_batch.messages[0].answer or state.answers[item.judge_batch.messages[0].sender_id]
                        )
                        self._fallback_records.append(
                            {
                                "kind": "judge-fallback",
                                "session": state.session,
                                "agent_id": item.judge_batch.judge_id,
                                "answer": selected,
                                "tags": [str(message.tag) for message in item.judge_batch.messages],
                                "source_event": event_reference(item.judge_batch.messages[0]),
                                "reason": type(exc).__name__,
                            }
                        )
                        for message in item.judge_batch.messages:
                            assert message.tag is not None
                            finish(
                                message.tag,
                                selected,
                                f"{type(exc).__name__}: {exc}",
                                status=ConversationStatus.SAFEGUARD_COMPLETE,
                                safeguard_trigger=SafeguardTrigger.WORKER_FAILURE,
                            )
                finally:
                    mailbox.finish()
                    try:
                        await persist_session_progress()
                    except AccountingFailure as exc:
                        self._fatal_error = exc
                        fatal_errors.append(exc)
                        completed_event.set()

        judge_groups: dict[int, list[StructuredMessage]] = {}
        for trace in traces.values():
            message = trace.messages[0]
            if trace.tag.scheme is Scheme.JUDGE:
                judge_groups.setdefault(trace.tag.receiver_id, []).append(message)
            else:
                await deliver(trace.tag.receiver_id, message=message)
        for judge_id in sorted(judge_groups):
            await deliver(judge_id, batch=_JudgeBatch(judge_id, tuple(judge_groups[judge_id])))

        workers = [asyncio.create_task(worker(mailboxes[agent])) for agent in self.config.agent_ids]
        timed_out = False
        cancelled: asyncio.CancelledError | None = None
        try:
            remaining = (
                self._session_deadline - asyncio.get_running_loop().time()
                if self._session_deadline is not None
                else self.config.safety.session_timeout_seconds
            )
            async with deadline_scope("session-deadline", timeout=max(0.0, remaining)):
                await completed_event.wait()
                if fatal_errors:
                    timed_out = True
                    stopping = True
                    for task in workers:
                        cancel_with_cause(task, "shutdown-cleanup")
        except TimeoutError:
            timed_out = True
            stopping = True
            judge_fallbacks = {judge_id: output.answer for judge_id, output, _, _ in shared_judge_outputs}
            for trace in traces.values():
                if trace.tag.scheme is Scheme.JUDGE:
                    judge_fallbacks.setdefault(
                        trace.tag.receiver_id,
                        trace.messages[0].answer or state.answers[trace.tag.initiator_id],
                    )
            for judge_id, answer in judge_fallbacks.items():
                pending = [
                    trace
                    for trace in traces.values()
                    if trace.tag.scheme is Scheme.JUDGE and trace.tag.receiver_id == judge_id and not trace.completed
                ]
                if pending:
                    source = next((trace.messages[0] for trace in pending if trace.messages[0].answer == answer), None)
                    self._fallback_records.append(
                        {
                            "kind": "judge-fallback",
                            "session": state.session,
                            "agent_id": judge_id,
                            "answer": answer,
                            "tags": [str(trace.tag) for trace in pending],
                            "source_event": event_reference(source) if source is not None else None,
                            "reason": "session-timeout",
                        }
                    )
            for tag, trace in traces.items():
                if not trace.completed:
                    finish(
                        tag,
                        (
                            judge_fallbacks[tag.receiver_id]
                            if tag.scheme is Scheme.JUDGE
                            else latest_valid_initiator_answer(tag)
                        ),
                        "session ended by engineering timeout",
                        status=ConversationStatus.SAFEGUARD_COMPLETE,
                        safeguard_trigger=SafeguardTrigger.SESSION_TIMEOUT,
                    )
            for task in workers:
                cancel_with_cause(task, "session-deadline")
        except asyncio.CancelledError as exc:
            cancelled = exc
            stopping = True
            for task in workers:
                cancel_with_cause(task, cancellation_cause(exc))
        finally:
            if not timed_out and cancelled is None:
                for mailbox in mailboxes.values():
                    await mailbox.queue.put(None)
            _, cleanup_pending = await bounded_cleanup(workers)
            if cleanup_pending:
                stopping = True
                controller = current_shutdown()
                if controller is not None:
                    controller.request("session cleanup deadline; physical operations remain unconfirmed")
                if cancelled is None:
                    cancelled = asyncio.CancelledError("shutdown-cleanup-incomplete")
                    setattr(cancelled, "cancellation_cause", "shutdown-cleanup")
                    setattr(cancelled, "timing_phase", "worker-cleanup")
        for tag, cancelled_usage in self._cancelled_call_usage.items():
            if tag in traces:
                traces[tag].usage = traces[tag].usage + cancelled_usage
                if tag.scheme is Scheme.JUDGE:
                    shared_failed_usage = shared_failed_usage + cancelled_usage
                    affected = [
                        trace
                        for trace in traces.values()
                        if trace.tag.scheme is Scheme.JUDGE and trace.tag.receiver_id == tag.receiver_id
                    ]
                    for trace in affected:
                        trace.judge_group_size = len(affected)
                        if not fatal_errors:
                            cost = self._attempt_cost(cancelled_usage)
                            trace.normalized_cost = _available_sum(
                                (trace.normalized_cost, None if cost is None else cost / len(affected))
                            )
        self._cancelled_call_usage = {}
        interrupted_judges: list[tuple[int, ParsedOutput, tuple[ConversationTag, ...]]] = []
        for tag, output in self._interrupted_outputs.items():
            affected_tags: list[ConversationTag] = []
            for trace in traces.values():
                if trace.tag == tag or (
                    tag.scheme is Scheme.JUDGE
                    and trace.tag.scheme is Scheme.JUDGE
                    and trace.tag.receiver_id == tag.receiver_id
                ):
                    # Usage was already charged by the exception/cancellation
                    # path; attach the evidence without charging it twice.
                    trace.outputs.append(output)
                    affected_tags.append(trace.tag)
            if tag.scheme is Scheme.JUDGE:
                interrupted_judges.append((tag.receiver_id, output, tuple(affected_tags)))
        self._interrupted_outputs = {}

        if fatal_errors or cancelled is not None:
            # Preserve generated/received events and interrupted outputs before
            # propagating a fatal condition; never synthesize terminal answers.
            partial = [
                HistoryRecord(
                    session=state.session,
                    event=message.format.value,
                    actor_id=message.sender_id,
                    tag=message.tag,
                    round_index=message.round_index,
                    content=message.text,
                    prompt_component=message.prompt_component,
                    delivery_index=delivered_at.get(id(message)),
                    processing_index=processed_at.get(id(message)),
                    recipient_id=message.receiver_id,
                    status=message.status.value if message.status else None,
                    parse_outcome="valid",
                    answer=message.answer or "",
                    explanation=message.explanation,
                    feedback=message.feedback,
                )
                for trace in traces.values()
                for message in trace.messages
            ]
            partial.extend(
                HistoryRecord(
                    session=state.session,
                    event="conversation-complete",
                    actor_id=trace.tag.initiator_id,
                    tag=trace.tag,
                    round_index=max((message.round_index for message in trace.messages), default=0),
                    answer=trace.terminal.answer if trace.terminal else "",
                    status=trace.completion_status.value if trace.completion_status else None,
                    parse_outcome="invalid" if trace.error else "valid",
                    content=json.dumps({"error": trace.error, "safeguard_reason": trace.safeguard_reason}),
                    usage=trace.usage if trace.tag.scheme is not Scheme.JUDGE else UsageRecord(),
                    normalized_cost=self._attempt_cost(trace.usage) if trace.tag.scheme is not Scheme.JUDGE else 0.0,
                )
                for trace in traces.values()
                if trace.completed
            )
            partial.extend(
                HistoryRecord(
                    session=state.session,
                    event="judge-adjudication",
                    actor_id=judge_id,
                    content=output.explanation,
                    explanation=output.explanation,
                    answer=output.answer,
                    delivery_index=event_index,
                    involved_agent_ids=tuple(sorted({judge_id, *(tag.initiator_id for tag in tags)})),
                    status="complete",
                    parse_outcome="invalid" if output.error else "valid",
                    usage=output.usage,
                    normalized_cost=self._attempt_cost(output.usage),
                )
                for judge_id, output, tags, event_index in shared_judge_outputs
            )
            partial.extend(
                HistoryRecord(
                    session=state.session,
                    event="judge-adjudication",
                    actor_id=judge_id,
                    involved_agent_ids=tuple(sorted({judge_id, *(tag.initiator_id for tag in tags)})),
                    content=output.error or "interrupted adjudication",
                    status="interrupted",
                    parse_outcome="invalid",
                    usage=output.usage,
                    normalized_cost=self._attempt_cost(output.usage),
                )
                for judge_id, output, tags in interrupted_judges
            )
            partial.sort(
                key=lambda record: (
                    record.delivery_index is None,
                    record.delivery_index if record.delivery_index is not None else delivery_index,
                    record.processing_index if record.processing_index is not None else processing_index,
                )
            )
            self._partial_history = (*state.history, *partial)
            if fatal_errors:
                raise fatal_errors[0]
            assert cancelled is not None
            raise cancelled

        history = list(state.history)
        current_history: list[HistoryRecord] = []
        usage = UsageRecord()
        counted_shared: set[int] = set()
        for trace in traces.values():
            if trace.tag.scheme is not Scheme.JUDGE:
                trace.normalized_cost = self._attempt_cost(trace.usage)
                usage = usage + trace.usage
            for message in trace.messages:
                current_history.append(
                    HistoryRecord(
                        session=state.session,
                        event=message.format.value,
                        actor_id=message.sender_id,
                        tag=message.tag,
                        round_index=message.round_index,
                        content=message.text,
                        prompt_component=message.prompt_component,
                        delivery_index=delivered_at.get(id(message)),
                        processing_index=processed_at.get(id(message)),
                        recipient_id=message.receiver_id,
                        status=message.status.value if message.status else None,
                        answer=message.answer or "",
                        explanation=message.explanation,
                        feedback=message.feedback,
                        answer_changed=(
                            message.answer is not None and message.answer != state.answers[trace.tag.initiator_id]
                        ),
                        parse_outcome="valid",
                    )
                )
            current_history.append(
                HistoryRecord(
                    session=state.session,
                    event="conversation-complete",
                    actor_id=trace.tag.initiator_id,
                    tag=trace.tag,
                    round_index=max(message.round_index for message in trace.messages),
                    answer=trace.terminal.answer if trace.terminal else "",
                    status=trace.completion_status.value if trace.completion_status else None,
                    parse_outcome="invalid" if trace.error else "valid",
                    content=json.dumps(
                        {
                            "error": trace.error,
                            "safeguard_reason": trace.safeguard_reason,
                            "attempts": [asdict(attempt) for output in trace.outputs for attempt in output.attempts],
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    usage=trace.usage if trace.tag.scheme is not Scheme.JUDGE else UsageRecord(),
                    normalized_cost=trace.normalized_cost if trace.tag.scheme is not Scheme.JUDGE else 0.0,
                )
            )
        for judge_id, output, tags, adjudication_event_index in shared_judge_outputs:
            if id(output) not in counted_shared:
                counted_shared.add(id(output))
                usage = usage + output.usage
                current_history.append(
                    HistoryRecord(
                        session=state.session,
                        event="judge-adjudication",
                        actor_id=judge_id,
                        content=output.explanation,
                        prompt_component=output.prompt_component,
                        delivery_index=adjudication_event_index,
                        involved_agent_ids=tuple(sorted({judge_id, *(tag.initiator_id for tag in tags)})),
                        answer=output.answer,
                        explanation=output.explanation,
                        status="complete",
                        parse_outcome="invalid" if output.error else "valid",
                        usage=output.usage,
                        normalized_cost=self._attempt_cost(output.usage),
                    )
                )
        for judge_id, output, tags in interrupted_judges:
            terminal = traces[tags[0]].terminal
            current_history.append(
                HistoryRecord(
                    session=state.session,
                    event="judge-adjudication",
                    actor_id=judge_id,
                    prompt_component=output.prompt_component,
                    involved_agent_ids=tuple(sorted({judge_id, *(tag.initiator_id for tag in tags)})),
                    answer=terminal.answer if terminal is not None else "",
                    content=json.dumps(
                        {
                            "error": output.error,
                            "attempts": [asdict(attempt) for attempt in output.attempts],
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    status=ConversationStatus.SAFEGUARD_COMPLETE.value,
                    parse_outcome="invalid",
                    usage=output.usage,
                    normalized_cost=self._attempt_cost(output.usage),
                )
            )
        usage = usage + shared_failed_usage
        current_history.sort(
            key=lambda record: (
                record.delivery_index is None,
                record.delivery_index if record.delivery_index is not None else delivery_index,
                record.processing_index if record.processing_index is not None else processing_index,
            )
        )
        history.extend(current_history)
        return (
            {agent: tuple(raw[agent]) for agent in self.config.agent_ids},
            tuple(traces.values()),
            self._commit_history(tuple(history)),
            usage,
        )

    @staticmethod
    def _recorded_trace(trace: ConversationTrace) -> dict[str, Any]:
        """Complete recorded evidence for component-local training, never tie input."""

        return {
            "tag": str(trace.tag),
            "messages": [asdict(message) for message in trace.messages],
            "outputs": [asdict(output) for output in trace.outputs],
            "terminal_answer": trace.terminal.answer if trace.terminal else None,
            "status": trace.completion_status.value if trace.completion_status else None,
            "error": trace.error,
            "usage": asdict(trace.usage),
            "normalized_cost": trace.normalized_cost,
        }

    def _resolved_explanations(
        self,
        state: SessionStartState,
        resolved: tuple[str, ...],
        traces: tuple[ConversationTrace, ...],
        tie_outputs: dict[int, TieOutput],
        previous: dict[int, str],
    ) -> dict[int, str]:
        """Select an answer-associated explanation in the specified deterministic order."""

        result: dict[int, str] = {}
        sources: dict[int, ExplanationReference | None] = {}
        ordered = sorted(
            traces, key=lambda trace: (trace.tag.initiator_id, trace.tag.receiver_id, trace.tag.scheme.value)
        )
        for agent, answer in enumerate(resolved):
            tie = tie_outputs.get(agent)
            if tie is not None and tie.error is None and tie.answer == answer and tie.explanation:
                result[agent] = tie.explanation
                sources[agent] = ExplanationReference("tie", agent, state.session)
                continue
            selected = next(
                (
                    message
                    for trace in ordered
                    if trace.tag.initiator_id == agent
                    and trace.completed
                    and trace.terminal is not None
                    and trace.terminal.answer == answer
                    for message in sorted(trace.messages, key=lambda value: value.round_index, reverse=True)
                    if message.format in {MessageFormat.INITIATION, MessageFormat.REVISION_RESPONSE}
                    and message.sender_id == agent
                    and message.answer == answer
                    and message.explanation
                ),
                None,
            )
            if selected is not None:
                result[agent] = selected.explanation
                sources[agent] = selected.explanation_source or ExplanationReference(
                    selected.format.value, agent, selected.session, str(selected.tag), selected.round_index
                )
            elif answer == state.answers[agent]:
                result[agent] = previous.get(agent, "")
                sources[agent] = self._explanation_sources.get(agent)
            else:
                result[agent] = ""
                sources[agent] = None
        self._explanation_sources = sources
        return result

    async def _resolve_answers(
        self,
        state: SessionStartState,
        raw: dict[int, tuple[TerminalRecord, ...]],
        traces: tuple[ConversationTrace, ...],
        history: tuple[HistoryRecord, ...],
    ) -> tuple[
        dict[int, tuple[TerminalRecord, ...]],
        tuple[str, ...],
        dict[int, tuple[str, ...]],
        dict[int, TieOutput],
        tuple[HistoryRecord, ...],
        UsageRecord,
    ]:
        effective: dict[int, tuple[TerminalRecord, ...]] = {}
        modes: dict[int, tuple[str, ...]] = {}
        resolved = list(state.answers)
        tie_outputs: dict[int, TieOutput] = {}
        extra_history = list(history)
        usage = UsageRecord()
        trace_by_tag = {trace.tag: trace for trace in traces}
        for agent in self.config.agent_ids:
            records = raw[agent]
            if records:
                effective[agent] = records
            else:
                effective[agent] = (TerminalRecord(state.session, agent, state.answers[agent], None, True),)
                self._fallback_records.append(
                    {
                        "kind": "passive-carry",
                        "session": state.session,
                        "agent_id": agent,
                        "answer": state.answers[agent],
                        "reason": "no-terminal-contribution",
                    }
                )
            answers = [record.answer for record in effective[agent]]
            tied = modal_answers(answers, state.example.candidates)
            modes[agent] = tied
            if len(tied) == 1:
                resolved[agent] = tied[0]
                continue
            evidence = tie_view(tied, records, trace_by_tag)
            key = "tie"
            prompt = self.prompts.render(
                key,
                agent=agent,
                query=state.example.query,
                modal_answers=json.dumps(tied, ensure_ascii=False),
                evidence=json.dumps(evidence, ensure_ascii=False, sort_keys=True),
            )
            tie_message = StructuredMessage(
                MessageFormat.TIE_RESOLUTION,
                agent,
                agent,
                state.session,
                0,
                query=state.example.query,
                answer=state.answers[agent],
                prompt_component=key,
                payload={"modal_answers": tied, "evidence": evidence},
            )
            operational_failure = False
            try:
                output = await self._validated_call(
                    AgentRequest(
                        AgentCallKind.TIE,
                        agent,
                        prompt,
                        key,
                        tie_message,
                        {"tied_answers": tied, "session": state.session},
                    ),
                    TieOutput,
                    self._answer_context_validator(
                        state.example,
                        tied,
                        "tie",
                    ),
                )
            except (_ModelCallTimeout, _ModelCallFailure) as exc:
                if (
                    isinstance(exc, _ModelCallFailure)
                    and exc.output is None
                    and isinstance(exc.original_error, PolicyStopped)
                ):
                    # Admission paused before a generation/validation attempt
                    # existed. Record the deterministic decision without
                    # manufacturing a TieOutput or a model-call observation.
                    selected = tied[0]
                    reason = str(exc.original_error)
                    resolved[agent] = selected
                    usage = usage + exc.usage
                    self._fallback_records.append(
                        {
                            "kind": "tie-fallback",
                            "session": state.session,
                            "agent_id": agent,
                            "answer": selected,
                            "reason": "operational pause",
                            "error": reason,
                            "admission_failure": True,
                            "not_dispatched": True,
                            "operational_drain": True,
                        }
                    )
                    extra_history.append(
                        HistoryRecord(
                            state.session,
                            "tie-resolution",
                            agent,
                            None,
                            None,
                            f"tie-resolution operational pause: {reason}; first modal answer {selected!r}",
                            key,
                            recipient_id=agent,
                            answer=selected,
                            explanation="",
                            status=ConversationStatus.SAFEGUARD_COMPLETE.value,
                            parse_outcome="not-dispatched",
                            usage=exc.usage,
                            normalized_cost=self._attempt_cost(exc.usage),
                            answer_changed=selected != state.answers[agent],
                        )
                    )
                    continue
                assert isinstance(exc.output, TieOutput)
                output = exc.output
                operational_failure = True
            assert isinstance(output, TieOutput)
            if output.validation_exhausted or operational_failure:
                selected = tied[0]
                failure_kind = "operational failure" if operational_failure else "validation error"
                output = replace(
                    output,
                    answer=selected,
                    error=(
                        f"tie-resolution {failure_kind}: "
                        f"{output.error}; falling back to first modal answer {selected!r}"
                    ),
                )
                self._fallback_records.append(
                    {
                        "kind": "tie-fallback",
                        "session": state.session,
                        "agent_id": agent,
                        "answer": selected,
                        "reason": failure_kind,
                        "admission_failure": bool(output.error and "input-token-limit" in output.error),
                    }
                )
            else:
                selected = output.answer
            output = replace(output, answer=selected, tied_answers=tied)
            tie_outputs[agent] = output
            usage = usage + output.usage
            resolved[agent] = selected
            extra_history.append(
                HistoryRecord(
                    state.session,
                    "tie-resolution",
                    agent,
                    None,
                    None,
                    output.explanation,
                    key,
                    recipient_id=agent,
                    answer=selected,
                    explanation=output.explanation,
                    status=ConversationStatus.SAFEGUARD_COMPLETE.value if operational_failure else "complete",
                    parse_outcome="invalid" if output.error else "valid",
                    usage=output.usage,
                    normalized_cost=self._attempt_cost(output.usage),
                    answer_changed=selected != state.answers[agent],
                )
            )
        return effective, tuple(resolved), modes, tie_outputs, self._commit_history(tuple(extra_history)), usage

    def _termination(
        self,
        session: int,
        previous_scores: dict[str, float],
        current_scores: dict[str, float],
        winner_history: list[str],
        next_state: SessionStartState | None,
        *,
        configuration: TerminationConfig | None = None,
        opportunity: float | None = None,
    ) -> TerminationMetrics:
        cfg = configuration or self.config.termination
        winner = deterministic_winner(
            current_scores,
            next_state.example.candidates if next_state else tuple(current_scores),
        )
        support = current_scores[winner]
        runner = max((score for answer, score in current_scores.items() if answer != winner), default=0.0)
        margin = support - runner
        change = total_variation(previous_scores, current_scores)
        persisted = (
            len(winner_history) >= cfg.persistence_length and len(set(winner_history[-cfg.persistence_length :])) == 1
        )
        if opportunity is None:
            if session < cfg.max_sessions and next_state is not None:
                with torch.no_grad():
                    opportunity = self._route(next_state, RoutingMode.INFERENCE, None).objective_score
            else:
                opportunity = 0.0
        confident = support >= cfg.winner_support_threshold and margin >= cfg.winner_margin_threshold and persisted
        stable = change <= cfg.distribution_change_threshold and opportunity <= cfg.routing_opportunity_threshold
        eligible = cfg.min_sessions <= session < cfg.max_sessions
        stop = session == cfg.max_sessions or (eligible and (confident or stable))
        reason = (
            "S_max"
            if session == cfg.max_sessions
            else "Conf" if eligible and confident else "Stab" if eligible and stable else "continue"
        )
        return TerminationMetrics(
            session,
            winner,
            support,
            runner,
            margin,
            change,
            persisted,
            opportunity,
            confident,
            stable,
            stop,
            reason,
        )

    async def run(
        self,
        example: QueryExample,
        *,
        routing_mode: RoutingMode = RoutingMode.INFERENCE,
        generator: torch.Generator | None = None,
        routing_identity: RoutingRolloutIdentity | None = None,
        generation_context: GenerationContext | None = None,
        partition: str = "inference",
        fixed_horizon: int | None = None,
        _termination_candidates: tuple[TerminationConfig, ...] = (),
    ) -> Trajectory:
        """Run one complete adaptive trajectory; alpha never updates inside it."""

        if fixed_horizon is not None and fixed_horizon < 1:
            raise ValueError("fixed horizon must be positive")
        if fixed_horizon is not None and _termination_candidates:
            raise ValueError("fixed pilot horizon and candidate stopping rules are mutually exclusive")
        previous_modes = self.routing_policy.training, self.weighting_model.training
        started = time.perf_counter()
        trajectory = Trajectory(example, (), (), routing_mixture=self.config.training.p_mix,
                                routing_cost_coefficient=self.config.costs.lambda_conversation)
        self.last_trajectory = trajectory
        interruption: asyncio.CancelledError | None = None
        self._drain_error = None
        candidates = _termination_candidates or (self.config.termination,)
        maximum = fixed_horizon or max(candidate.max_sessions for candidate in candidates)
        stopped: dict[int, int] = {}
        if routing_mode is not RoutingMode.TRAIN:
            self.routing_policy.eval()
            self.weighting_model.eval()
        try:
            if generator is not None:
                raise ValueError(
                    "Runtime.run does not accept a raw routing generator; training "
                    "requires a recorded RoutingRolloutIdentity"
                )
            if routing_mode is RoutingMode.TRAIN and routing_identity is None:
                raise ValueError("training routing requires a recorded RoutingRolloutIdentity")
            if routing_mode is not RoutingMode.TRAIN and routing_identity is not None:
                raise ValueError("routing_identity applies only to stochastic training routing")

            started = time.perf_counter()
            if generation_context is not None and generation_context.example_id != example.id:
                raise ValueError("generation context must identify the current query")
            self._generation_context = generation_context
            self._call_records = []
            self._prompt_calls = []
            self._initial_records = {}
            self._active_traces = {}
            self._fallback_records = []
            self._fatal_error = None
            self._next_call_id = 0
            self._partial_history = ()
            self._session_deadline = None
            self._boundary_counts = {}
            self._candidate_metrics = {index: [] for index in range(len(candidates))}
            self._history_cache = self.encoders.new_history_cache()
            self._finalized_history = []
            # Only the fixed base is cached; every learned forward below stays current.
            self.encoders.encode_query(example)
            await self._publish_progress("workflow-started", trajectory)
            initial = await self._initial_answers(example)
            answers = tuple(output.answer for output in initial)
            explanations = {agent: output.explanation for agent, output in enumerate(initial)}
            self._explanation_sources = {
                agent: ExplanationReference("initial", agent) for agent in self.config.agent_ids
            }
            initial_usage = sum((output.usage for output in initial), UsageRecord())
            with torch.no_grad():
                _, initial_weights, initial_scores, initial_winner = self._weight(example, (), answers)
            trajectory = Trajectory(
                example=example,
                initial_outputs=initial,
                initial_answers=answers,
                initial_weights=tuple(float(value) for value in initial_weights.cpu()),
                initial_candidate_scores=initial_scores,
                usage=initial_usage,
                initial_usage=initial_usage,
                initial_records=dict(self._initial_records),
                routing_mixture=self.config.training.p_mix,
                routing_cost_coefficient=self.config.costs.lambda_conversation,
            )
            self.last_trajectory = trajectory
            await self._publish_progress("initialization-complete", trajectory)
            history: tuple[HistoryRecord, ...] = ()
            previous_scores = initial_scores
            winner_history = [initial_winner]
            support_values: list[float] = []
            if example.gold_answer is not None:
                initial_supports = [float(answer == example.gold_answer) for answer in answers]
                support_values.append(
                    sum(
                        weight * value
                        for weight, value in zip(trajectory.initial_weights, initial_supports, strict=True)
                    )
                )

            for session in range(1, maximum + 1):
                session_started = time.perf_counter()
                self._session_deadline = asyncio.get_running_loop().time() + self.config.safety.session_timeout_seconds
                state = SessionStartState(session, example, history, answers)
                session_generator = generator
                routing_substream = None
                if routing_mode is RoutingMode.TRAIN and routing_identity is not None:
                    substream = routing_identity.for_session(session)
                    _, session_generator = routing_generator(
                        routing_identity,
                        session,
                        device=next(self.routing_policy.parameters()).device.type,
                    )
                    routing_substream = substream.to_dict()
                routing = self._route(state, routing_mode, session_generator)
                if routing_substream is not None:
                    routing = replace(routing, routing_substream=routing_substream)
                raw, traces, history_after_conversations, session_usage = await self._execute_session(
                    state, routing, explanations
                )
                effective, resolved, modes, ties, history, tie_usage = await self._resolve_answers(
                    state, raw, traces, history_after_conversations
                )
                explanations = self._resolved_explanations(state, resolved, traces, ties, explanations)
                session_usage = session_usage + tie_usage
                if routing_mode is RoutingMode.TRAIN:
                    weight_logits, weights, scores, winner = self._weight(example, history, resolved)
                else:
                    with torch.no_grad():
                        weight_logits, weights, scores, winner = self._weight(example, history, resolved)
                winner_history.append(winner)
                next_state = SessionStartState(session + 1, example, history, resolved) if session < maximum else None
                opportunity = None
                if fixed_horizon is None and next_state is not None:
                    opportunity = self._route(next_state, RoutingMode.INFERENCE, None).objective_score
                metrics: list[TerminationMetrics] = []
                for index, candidate in enumerate(candidates):
                    if index in stopped:
                        continue
                    metric = self._termination(
                        session,
                        previous_scores,
                        scores,
                        winner_history,
                        next_state,
                        configuration=candidate,
                        opportunity=opportunity or 0.0,
                    )
                    if fixed_horizon is not None:
                        metric = replace(
                            metric,
                            stop=session == maximum,
                            reason="pilot-horizon" if session == maximum else "continue",
                        )
                    self._candidate_metrics[index].append(metric)
                    metrics.append(metric)
                    if metric.stop:
                        stopped[index] = session
                termination = metrics[0]
                termination = replace(termination, stop=len(stopped) == len(candidates))
                cost = _available_sum([*(trace.normalized_cost for trace in traces), self._attempt_cost(tie_usage)])
                result = SessionResult(
                    session=session,
                    start_state=state,
                    routing=routing,
                    traces=traces,
                    raw_terminal_answers=raw,
                    effective_terminal_answers=effective,
                    resolved_answers=resolved,
                    agent_weights=tuple(float(value) for value in weights.detach().cpu()),
                    candidate_scores=scores,
                    weighted_answer=winner,
                    weight_tensor=weights,
                    weight_logit_tensor=weight_logits,
                    history=history,
                    modal_answers=modes,
                    tie_outputs=ties,
                    usage=session_usage,
                    normalized_cost=cost,
                    termination=termination,
                    safeguard_diagnostics=SafeguardDiagnostics.from_traces(traces),
                    elapsed_seconds=time.perf_counter() - session_started,
                    resolved_explanations=tuple(explanations[agent] for agent in self.config.agent_ids),
                    explanation_sources=dict(self._explanation_sources),
                )
                if example.gold_answer is not None:
                    costs = {trace.tag: trace.normalized_cost for trace in traces}
                    credit_available = not self.config.costs.lambda_conversation or all(
                        value is not None for value in costs.values()
                    )
                    credits, supports, value = conversation_credits(
                        raw,
                        {agent: state.answers[agent] for agent in self.config.agent_ids},
                        weights,
                        example.gold_answer,
                        {tag: value for tag, value in costs.items() if value is not None},
                        lambda_conversation=self.config.costs.lambda_conversation if credit_available else 0.0,
                        answer_order=example.candidates,
                    )
                    support_values.append(value)
                    reward = (
                        None
                        if cost is None and self.config.costs.lambda_session
                        else session_rewards(
                            support_values[-2:], [cost if cost is not None else 0.0], self.config.costs.lambda_session
                        )[0]
                    )
                    result = replace(
                        result, conversation_credits=credits if credit_available else {}, immediate_reward=reward,
                        routing_local_labels={trace.tag: float(trace.terminal.answer == example.gold_answer)
                                              for trace in traces if trace.terminal is not None},
                        routing_zero_labels={agent: float(state.answers[agent] == example.gold_answer)
                                             for agent in self.config.agent_ids},
                        conversation_costs=costs,
                    )
                trajectory.append_session(result)
                await self._publish_progress("completed-session", trajectory)
                self._boundary_counts[session] = (
                    len(self._call_records),
                    len(self._prompt_calls),
                    time.perf_counter() - started,
                )
                answers = resolved
                previous_scores = scores
                stop_reasons = [getattr(agent, "dispatch_stop_reason", None) for agent in self.agents.values()]
                drain = next((reason for reason in stop_reasons if reason and reason.get("operational_drain")), None)
                if self._drain_error is not None or drain is not None:
                    raise self._drain_error or PolicyStopped(drain["reason"])
                if termination.stop:
                    break

            if example.gold_answer is not None:
                rewards = [session.immediate_reward for session in trajectory.sessions]
                returns, future = returns_and_future(rewards)
                trajectory.sessions = [
                    replace(session, return_value=returns[index], future_return=future[index])
                    for index, session in enumerate(trajectory.sessions)
                ]
            trajectory.final_answer = trajectory.sessions[-1].weighted_answer
            trajectory.completed = True
            trajectory.status = "completed"
            trajectory.call_records = tuple(self._call_records)
            trajectory.fallback_records = tuple(self._fallback_records)
            trajectory.call_durations = tuple(float(record["elapsed_seconds"]) for record in self._call_records)
            trajectory.elapsed_seconds = time.perf_counter() - started
            if example.gold_answer is not None:
                occurrences: list[PromptOccurrence] = []
                sessions_by_id = {result.session: result for result in trajectory.sessions}
                traces_by_tag = {str(trace.tag): trace for result in trajectory.sessions for trace in result.traces}
                for request, output in self._prompt_calls:
                    if request.prompt_component == "initial":
                        continue
                    session_id = (
                        request.message.session if request.message is not None else int(request.context["session"])
                    )
                    result = sessions_by_id[session_id]
                    tag_key = (
                        str(request.message.tag)
                        if request.message is not None and request.message.tag
                        else request.context.get("tag")
                    )
                    trace = traces_by_tag.get(tag_key)
                    if request.kind is AgentCallKind.TIE:
                        outcome = result.resolved_answers[request.agent_id]
                    elif request.kind is AgentCallKind.JUDGE and isinstance(output, JudgeOutput):
                        # The actual accepted/fallback shared answer is the tagged terminal.
                        outcome = trace.terminal.answer if trace and trace.terminal else output.answer
                    elif trace is not None and trace.terminal is not None:
                        outcome = trace.terminal.answer
                    else:
                        continue
                    call_type = request.kind.value
                    conversation = (
                        f"judge:{request.agent_id}"
                        if request.kind is AgentCallKind.JUDGE
                        else str(request.message.tag) if request.message and request.message.tag else call_type
                    )
                    call_records = [
                        item
                        for item in self._call_records
                        if item.get("usage", {}).get("model_calls", 0) > 0
                        and item["logical_call"]["agent_id"] == request.agent_id
                        and item["logical_call"]["session_id"] == session_id
                        and item["logical_call"]["conversation_id"] == conversation
                        and item["logical_call"]["call_type"] == call_type
                        and item["logical_call"]["round_index"]
                        == int(request.context.get("round", request.message.round_index if request.message else 0))
                    ]
                    generation_id = json.dumps(call_records[0]["logical_call"], sort_keys=True) if call_records else ""
                    effective_input = tuple(call_records[0]["effective_input"]) if call_records else ()
                    record = {
                        "session": session_id,
                        "generation_id": generation_id,
                        "generation_records": call_records,
                        "effective_input": effective_input,
                        "input_fields": dict(request.context),
                        "supplied_message": asdict(request.message) if request.message is not None else None,
                        "parsed_output": asdict(output) if output is not None else None,
                        "selected_answer": outcome,
                        "selection_used_fallback": bool(output is not None and output.error is not None),
                        "downstream_trace": self._recorded_trace(trace) if trace is not None else None,
                        "provider_only_failure": bool(output.usage.raw_metadata.get("provider_only_failure")),
                    }
                    if request.kind is AgentCallKind.JUDGE:
                        record["returned_traces"] = [
                            self._recorded_trace(traces_by_tag[tag]) for tag in request.context.get("tags", ())
                        ]
                    elif request.kind is AgentCallKind.TIE:
                        record["tie_resolution_cost"] = self._attempt_cost(result.tie_outputs[request.agent_id].usage)
                    occurrences.append(
                        PromptOccurrence(
                            request.prompt_component,
                            request.prompt,
                            output.raw_content if output is not None else "",
                            outcome,
                            example.gold_answer,
                            record,
                            trajectory.sessions[0].return_value,
                            {str(tag): credit for tag, credit in result.conversation_credits.items()},
                            query=example.query,
                            example_id=example.id,
                            split=partition,
                            generation_id=generation_id,
                            effective_input=effective_input,
                        )
                    )
                    consumed = [request.message] if request.message is not None else []
                    if request.kind is AgentCallKind.JUDGE:
                        consumed = [traces_by_tag[tag].messages[0] for tag in request.context.get("tags", ())]
                    for message in consumed:
                        if message.format is not MessageFormat.INITIATION or not generation_id:
                            continue
                        init_trace = traces_by_tag[str(message.tag)]
                        init_outcome = init_trace.terminal.answer if init_trace.terminal else outcome
                        init_record = {
                            **record,
                            "input_fields": asdict(message),
                            "consumer_component": request.prompt_component,
                            "downstream_trace": self._recorded_trace(init_trace),
                            "selected_answer": init_outcome,
                            "shared_generation_id": generation_id,
                        }
                        occurrences.append(
                            PromptOccurrence(
                                message.prompt_component or "",
                                message.text,
                                output.raw_content if output is not None else "",
                                init_outcome,
                                example.gold_answer,
                                init_record,
                                trajectory.sessions[0].return_value,
                                {str(tag): credit for tag, credit in result.conversation_credits.items()},
                                query=example.query,
                                example_id=example.id,
                                split=partition,
                                generation_id=generation_id,
                                effective_input=effective_input,
                            )
                        )
                trajectory.prompt_occurrences = tuple(occurrences)
            return trajectory
        except (ResourceFailure, _ModelCallTimeout, _ModelCallFailure, asyncio.CancelledError) as exc:
            if isinstance(exc, asyncio.CancelledError):
                interruption = exc
            trajectory.completed = False
            trajectory.final_answer = None
            trajectory.error = str(exc) or cancellation_cause(exc)
            trajectory.accounting_complete = not isinstance(exc, AccountingFailure)
            trajectory.status = (
                "interrupted"
                if isinstance(exc, asyncio.CancelledError)
                else
                "accounting-failed"
                if isinstance(exc, AccountingFailure)
                else (
                    "policy-stopped"
                    if isinstance(exc, PolicyStopped) or self._drain_error is not None
                    else (
                        "budget-exhausted"
                        if isinstance(exc, BudgetExhausted)
                        else "initialization-failed" if not trajectory.initial_answers else "execution-failed"
                    )
                )
            )
            trajectory.failed_session_traces = tuple(self._active_traces.values())
            trajectory.failed_history = self._partial_history or tuple(self._finalized_history)
            trajectory.fallback_records = tuple(self._fallback_records)
            trajectory.operational_halt = (
                {
                    "type": "CancelledError",
                    "reason": cancellation_cause(exc),
                    "error": trajectory.error,
                    "fatal": False,
                    "operational_drain": True,
                    "unfinished_workflow": True,
                }
                if isinstance(exc, asyncio.CancelledError)
                else
                {
                    "type": type(exc).__name__,
                    "error": str(exc),
                    "fatal": not isinstance(exc, PolicyStopped),
                    "operational_drain": isinstance(exc, PolicyStopped),
                }
                if isinstance(exc, (BudgetExhausted, AccountingFailure))
                else (
                    {
                        "type": "PolicyStopped",
                        "error": str(self._drain_error),
                        "fatal": False,
                        "operational_drain": True,
                    }
                    if self._drain_error is not None
                    else None
                )
            )
            trajectory.initial_records = dict(self._initial_records)
            trajectory.initial_outputs = tuple(self._initial_records.values())
            trajectory.initial_usage = sum((output.usage for output in self._initial_records.values()), UsageRecord())
            trajectory.call_records = tuple(self._call_records)
            trajectory.call_durations = tuple(float(record["elapsed_seconds"]) for record in self._call_records)
            trajectory.usage = sum(
                (UsageRecord(**record["usage"]) for record in self._call_records if "usage" in record), UsageRecord()
            )
            trajectory.elapsed_seconds = time.perf_counter() - started
            trajectory.initialization_diagnostics = tuple(
                record for record in self._call_records if record["logical_call"]["call_type"] == "initial"
            )
            trajectory.sessions = [
                replace(result, return_value=None, future_return=None) for result in trajectory.sessions
            ]
            if isinstance(exc, asyncio.CancelledError):
                raise
            return trajectory
        finally:
            self._session_deadline = None
            trajectory.accounting_complete = trajectory.usage.accounting_complete
            trajectory.fallback_records = tuple(self._fallback_records)
            trajectory.execution_counters = self._execution_counters(trajectory)
            self.last_trajectory = trajectory
            self.routing_policy.train(previous_modes[0])
            self.weighting_model.train(previous_modes[1])
            if trajectory.status == "interrupted":
                try:
                    await self._publish_progress("workflow-interrupted", trajectory)
                except Exception as error:
                    if interruption is None:
                        raise
                    from ..infrastructure.shutdown import note_cleanup_failure

                    note_cleanup_failure(interruption, "interrupted workflow progress", error)

    async def run_shared_prefix(
        self,
        example: QueryExample,
        candidates: Sequence[TerminationConfig],
        *,
        generation_context: GenerationContext,
        partition: str = "sys-val",
    ) -> SharedPrefixResult:
        """Evaluate all stopping rules on one physical trajectory per query."""

        configurations = tuple(candidates)
        if not configurations or len(configurations) > 6:
            raise ValueError("shared-prefix calibration requires one to six ordered configurations")
        shared = await self.run(
            example,
            routing_mode=RoutingMode.EVALUATION,
            generation_context=generation_context,
            partition=partition,
            _termination_candidates=configurations,
        )
        prefixes: list[Trajectory] = []
        for index, configuration in enumerate(configurations):
            metrics = self._candidate_metrics.get(index, [])
            stop = next((metric.session for metric in metrics if metric.stop), None)
            reached = stop is not None and stop <= len(shared.sessions)
            count = stop if reached else len(shared.sessions)
            sessions = [
                replace(result, termination=metrics[position])
                for position, result in enumerate(shared.sessions[:count])
            ]
            if reached and example.gold_answer is not None:
                returns, future = returns_and_future([result.immediate_reward for result in sessions])
                sessions = [
                    replace(result, return_value=returns[pos], future_return=future[pos])
                    for pos, result in enumerate(sessions)
                ]
            call_count, _, elapsed = self._boundary_counts.get(
                count, (len(shared.call_records), 0, shared.elapsed_seconds)
            )
            records = shared.call_records[:call_count]
            usage = shared.initial_usage + sum((result.usage for result in sessions), UsageRecord())
            occurrences = tuple(
                occurrence
                for occurrence in shared.prompt_occurrences
                if int(occurrence.trace.get("session", 0)) <= count
            )
            # Returns and downstream evidence belong to this candidate's prefix.
            if reached and sessions and sessions[0].return_value is not None:
                occurrences = tuple(
                    replace(occurrence, complete_return=float(sessions[0].return_value)) for occurrence in occurrences
                )
            prefix = replace(
                shared,
                sessions=sessions,
                final_answer=sessions[-1].weighted_answer if reached else None,
                completed=reached,
                status="completed" if reached else shared.status,
                error=None if reached else shared.error,
                failed_session_traces=() if reached else shared.failed_session_traces,
                failed_history=() if reached else shared.failed_history,
                usage=usage,
                call_records=records,
                call_durations=tuple(float(record["elapsed_seconds"]) for record in records),
                elapsed_seconds=elapsed,
                prompt_occurrences=occurrences,
            )
            prefixes.append(prefix)
        return SharedPrefixResult(shared, tuple(prefixes), configurations)


async def run_fake_smoke() -> Trajectory:
    """Small deterministic offline execution used by ``python -m ...cli smoke``."""

    from ..providers.adapter import DeterministicFakeAgent
    from ..models.encoders import offline_encoder_configuration

    config = Config(
        encoders=offline_encoder_configuration(),
        termination=TerminationConfig(
            min_sessions=2,
            max_sessions=2,
            persistence_length=2,
            winner_support_threshold=0.99,
            winner_margin_threshold=0.99,
            distribution_change_threshold=0.0,
            routing_opportunity_threshold=0.0,
        ),
    )
    agents = {
        0: DeterministicFakeAgent(
            0,
            {
                "initial": {"answer": "A", "explanation": "a0"},
                "role": [
                    {"status": "continue", "feedback": "Compare the missing evidence."},
                    {"status": "complete", "feedback": "The revision is now supported."},
                    {"status": "complete", "feedback": "Self-check complete."},
                ],
            },
        ),
        1: DeterministicFakeAgent(1, {"initial": {"answer": "B", "explanation": "a1"}}),
        2: DeterministicFakeAgent(
            2,
            {
                "initial": {"answer": "C", "explanation": "a2"},
                "judge": {"answer": "B", "explanation": "B best fits both submissions."},
                "revision": {"answer": "A", "explanation": "Revised after reviewer evidence."},
            },
        ),
    }
    zero = {(initiator, receiver): Scheme.ZERO for initiator in config.agent_ids for receiver in config.agent_ids}
    session_one = {
        **zero,
        (0, 2): Scheme.JUDGE,
        (1, 2): Scheme.JUDGE,
        (2, 0): Scheme.REVIEWER,
    }
    session_two = {**zero, (0, 0): Scheme.SELF}
    routing = ScriptedRoutingPolicy(
        config.n_agents,
        {1: session_one, 2: session_two},
        objective_scores={1: 3.0, 2: 1.0},
    )
    weighting = WeightingModel(
        config.n_agents, FixedBaseEncoders(config.n_agents).history_dimension, configuration=config.network
    )
    runtime = Runtime(config, agents, routing, weighting)
    example = QueryExample("smoke", "Select the best synthetic answer.", ("A", "B", "C"), "A")
    return await runtime.run(example, routing_mode=RoutingMode.INFERENCE)
