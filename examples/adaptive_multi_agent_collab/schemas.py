"""Typed state, message, routing, trace, and training records."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
import json
import math
from typing import Any, Protocol, TypeAlias, runtime_checkable

from torch import Tensor


AgentId: TypeAlias = int
Answer: TypeAlias = str
Edge: TypeAlias = tuple[AgentId, AgentId]
FEEDBACK_GUARD_ID = "answer_whitespace_seen_feedback_v1"


@dataclass(frozen=True, slots=True)
class AgentIdentity:
    agent_id: AgentId
    name: str
    model_key: str | None = None

    def __post_init__(self) -> None:
        if self.agent_id < 0:
            raise ValueError("agent_id must be non-negative")
        if not self.name.strip():
            raise ValueError("agent name must not be empty")


@dataclass(frozen=True, slots=True)
class QueryExample:
    """One query and its dataset-defined answer space."""

    example_id: str
    query: str
    candidates: tuple[Answer, ...]
    gold_answer: Answer | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    option_texts: tuple[str, ...] = ()
    open_answer: bool = False

    def __post_init__(self) -> None:
        candidates = tuple(str(value).strip() for value in self.candidates)
        if not self.example_id.strip() or not self.query.strip():
            raise ValueError("example_id and query must not be empty")
        if (
            (not candidates and not self.open_answer)
            or any(not value for value in candidates)
            or len(set(candidates)) != len(candidates)
        ):
            raise ValueError("candidates must be a non-empty sequence of unique non-empty answers")
        if self.open_answer and (candidates or self.option_texts):
            raise ValueError("open-answer tasks supply canonical strings rather than finite options")
        if self.option_texts and len(self.option_texts) != len(candidates):
            raise ValueError("option_texts must follow the dataset candidate order")
        gold = None if self.gold_answer is None else str(self.gold_answer).strip()
        if gold is not None and (not gold or (not self.open_answer and gold not in candidates)):
            raise ValueError("gold_answer must belong to candidates")
        object.__setattr__(self, "candidates", candidates)
        object.__setattr__(self, "gold_answer", gold)
        object.__setattr__(self, "metadata", dict(self.metadata))
        object.__setattr__(self, "option_texts", tuple(self.option_texts))

    @property
    def id(self) -> str:
        return self.example_id

    @property
    def answer_space(self) -> tuple[Answer, ...]:
        return self.candidates


@runtime_checkable
class QueryDataset(Protocol):
    """Minimal dataset boundary; concrete datasets may be lazy or in-memory."""

    def __len__(self) -> int: ...

    def __getitem__(self, index: int) -> QueryExample: ...


@runtime_checkable
class AnswerCanonicalizer(Protocol):
    def canonicalize(self, answer: str, example: QueryExample) -> str: ...

    # converts a raw ans into a standard representation

    def equal(self, left: str, right: str, example: QueryExample) -> bool: ...

    # decides whether two answers should count as equivalent.


@dataclass(frozen=True, slots=True)
class TrimmedAnswerCanonicalizer:
    """Default exact comparison after configurable outer trimming."""

    case_sensitive: bool = True

    def canonicalize(self, answer: str, example: QueryExample) -> str:
        del example
        if not isinstance(answer, str):
            raise ValueError("generated answer must be a string")
        value = answer.strip()
        return value if self.case_sensitive else value.casefold()

    def equal(self, left: str, right: str, example: QueryExample) -> bool:
        return self.canonicalize(left, example) == self.canonicalize(right, example)


@dataclass(frozen=True, slots=True)
class StrategyQAAnswerCanonicalizer:
    """Canonicalize only an explicit yes/no string, never search prose."""

    def canonicalize(self, answer: str, example: QueryExample) -> str:
        del example
        if not isinstance(answer, str):
            raise ValueError("StrategyQA answer must be an explicit string")
        value = answer.strip().casefold()
        if value.endswith("."):
            value = value[:-1].strip()
        if value not in {"yes", "no"}:
            raise ValueError("StrategyQA answer must canonicalize exactly to yes or no")
        return value

    def equal(self, left: str, right: str, example: QueryExample) -> bool:
        return self.canonicalize(left, example) == self.canonicalize(right, example)


@dataclass(frozen=True, slots=True)
class ExplanationReference:
    kind: str
    agent_id: int
    session: int = 0
    tag: str | None = None
    round_index: int = 0


class Scheme(str, Enum):
    ZERO = "0"
    SELF = "S_self"
    DEBATE = "S_D"
    JUDGE = "S_J"
    TEACHER = "S_T"
    REVIEWER = "S_V"

    @property
    def is_active(self) -> bool:
        return self is not Scheme.ZERO


SCHEME_ORDER: tuple[Scheme, ...] = tuple(Scheme)
CROSS_AGENT_SCHEMES: tuple[Scheme, ...] = (
    Scheme.ZERO,
    Scheme.DEBATE,
    Scheme.JUDGE,
    Scheme.TEACHER,
    Scheme.REVIEWER,
)
SELF_SCHEMES: tuple[Scheme, ...] = (Scheme.ZERO, Scheme.SELF)


class RoutingMode(str, Enum):
    TRAIN = "train"
    EVALUATION = "evaluation"
    INFERENCE = "inference"


class ConversationStatus(str, Enum):
    CONTINUE = "continue"
    COMPLETE = "complete"
    SAFEGUARD_COMPLETE = "safeguard-complete"


class SafeguardTrigger(str, Enum):
    """Operational termination causes, kept separate from model statuses."""

    ROUND_LIMIT = "round-limit"
    CALL_TIMEOUT = "call-timeout"
    SESSION_TIMEOUT = "session-timeout"
    VALIDATION_EXHAUSTED = "validation-exhausted"
    WORKER_FAILURE = "worker-failure"
    REPEATED_FEEDBACK = "repeated-feedback"


class MessageFormat(str, Enum):
    INITIATION = "initiation"
    ROLE_RESPONSE = "role-response"
    REVISION_RESPONSE = "revision-response"
    JUDGE_RETURN = "judge-return"
    TIE_RESOLUTION = "tie-resolution"


class AgentCallKind(str, Enum):
    INITIAL = "initial"
    ROLE = "role"
    REVISION = "revision"
    JUDGE = "judge"
    TIE = "tie"


@dataclass(frozen=True, slots=True)
class ConversationTag:
    """The tag ``g_ji^(s)=(j,i,z_ji^(s))`` from Step 6."""

    session: int
    initiator_id: AgentId
    receiver_id: AgentId
    scheme: Scheme

    def __post_init__(self) -> None:
        if self.session < 1 or min(self.initiator_id, self.receiver_id) < 0:
            raise ValueError("conversation tags need session >= 1 and non-negative agent IDs")
        if not self.scheme.is_active:
            raise ValueError("zero routing labels do not create conversation tags")
        is_self = self.initiator_id == self.receiver_id
        if is_self != (self.scheme is Scheme.SELF):
            raise ValueError("S_self is legal exactly on self-edges")

    @property
    def session_index(self) -> int:
        return self.session

    @property
    def edge(self) -> Edge:
        return self.initiator_id, self.receiver_id

    def __str__(self) -> str:
        return f"s{self.session}:{self.initiator_id}->{self.receiver_id}:{self.scheme.value}"


@dataclass(frozen=True, slots=True)
class StructuredMessage:
    """Self-contained FIFO message for every Step 6--9 message format."""

    format: MessageFormat
    sender_id: AgentId
    receiver_id: AgentId
    session: int
    round_index: int
    tag: ConversationTag | None = None
    query: str = ""
    answer: Answer | None = None
    explanation: str = ""
    feedback: str = ""
    status: ConversationStatus | None = None
    scheme_request: str = ""
    prompt_component: str | None = None
    payload: Mapping[str, Any] = field(default_factory=dict)
    text: str = ""
    explanation_source: ExplanationReference | None = None

    def __post_init__(self) -> None:
        if min(self.sender_id, self.receiver_id, self.round_index) < 0 or self.session < 1:
            raise ValueError("message IDs/round must be non-negative and session must be >= 1")
        if self.format is MessageFormat.TIE_RESOLUTION:
            if self.tag is not None or self.sender_id != self.receiver_id:
                raise ValueError("tie-resolution messages are untagged self-directed aggregation messages")
        elif self.tag is None or self.tag.session != self.session:
            raise ValueError("conversation messages require a same-session tag")
        if self.format is MessageFormat.INITIATION and self.round_index != 0:
            raise ValueError("initiation messages use internal round zero")
        if self.format is MessageFormat.ROLE_RESPONSE:
            if self.round_index % 2 != 1 or self.status not in {
                ConversationStatus.CONTINUE,
                ConversationStatus.COMPLETE,
            }:
                raise ValueError("role responses use odd rounds and require continue/complete status")
        elif self.status is not None and self.format is not MessageFormat.JUDGE_RETURN:
            raise ValueError("only role responses and judge returns carry status")
        if self.format is MessageFormat.REVISION_RESPONSE and (self.round_index < 2 or self.round_index % 2):
            raise ValueError("revision responses use positive even rounds")
        if self.format is MessageFormat.JUDGE_RETURN and self.status is not ConversationStatus.COMPLETE:
            raise ValueError("judge returns must carry complete status")
        object.__setattr__(self, "payload", dict(self.payload))
        if not self.text:
            from .prompts import MESSAGE_SERIALIZATION_ID

            # Fixed receiver-visible serialization deliberately excludes generation
            # prompts, audit payloads and predecessor messages.
            visible = {
                "serialization": MESSAGE_SERIALIZATION_ID,
                "format": self.format.value,
                "sender": self.sender_id,
                "receiver": self.receiver_id,
                "session": self.session,
                "round": self.round_index,
                "tag": str(self.tag) if self.tag else None,
                "query": self.query,
                "answer": self.answer,
                "explanation": self.explanation,
                "feedback": self.feedback,
                "status": self.status.value if self.status else None,
                "scheme_request": self.scheme_request,
            }
            object.__setattr__(self, "text", json.dumps(visible, ensure_ascii=False, sort_keys=True))

    @property
    def session_index(self) -> int:
        return self.session


@dataclass(frozen=True, slots=True, order=True)
class QueuedMessage:
    """Stable delivery index makes FIFO order explicit in traces and tests."""

    delivery_index: int
    message: StructuredMessage = field(compare=False)

    def __post_init__(self) -> None:
        if self.delivery_index < 0:
            raise ValueError("delivery_index must be non-negative")


@dataclass(frozen=True, slots=True)
class UsageRecord:
    """Logical usage with frozen reference USD separate from actual paid USD.

    ``monetary_cost`` is the Step 11 reference coordinate. Zero defaults describe
    an empty aggregate; adapters explicitly supply None for unknown attempts.
    """

    input_tokens: int | None = 0
    output_tokens: int | None = 0
    model_calls: int = 0
    monetary_cost: float | None = 0.0
    wall_seconds: float = 0.0
    model_name: str | None = None
    raw_metadata: Mapping[str, Any] = field(default_factory=dict)
    paid_monetary_cost: float | None = 0.0

    def __post_init__(self) -> None:
        for value in (
            self.input_tokens,
            self.output_tokens,
            self.model_calls,
            self.monetary_cost,
            self.paid_monetary_cost,
            self.wall_seconds,
        ):
            if value is not None and (not math.isfinite(value) or value < 0):
                raise ValueError("available usage coordinates must be finite and non-negative")
        object.__setattr__(self, "raw_metadata", dict(self.raw_metadata))

    @property
    def total_tokens(self) -> int | None:
        if self.input_tokens is None or self.output_tokens is None:
            return None
        return self.input_tokens + self.output_tokens

    @property
    def missing_coordinates(self) -> tuple[str, ...]:
        """Receipt availability as observed, never filled from a reservation."""

        missing = [
            name
            for name in ("input_tokens", "output_tokens", "monetary_cost", "paid_monetary_cost")
            if getattr(self, name) is None
        ]

        def collect(value: Any) -> None:
            if isinstance(value, Mapping):
                fields = value.get("missing_usage", ())
                if isinstance(fields, (tuple, list)):
                    missing.extend(name for name in fields if isinstance(name, str))
                if value.get("accounting_complete") is False and not fields:
                    missing.append("accounting_details")
                for key in ("attempts", "physical_attempts", "last", "usage", "raw_metadata"):
                    collect(value.get(key))
            elif isinstance(value, (tuple, list)):
                for child in value:
                    collect(child)

        collect(self.raw_metadata)
        return tuple(dict.fromkeys(missing))

    @property
    def accounting_complete(self) -> bool:
        return not self.missing_coordinates

    def __add__(self, other: UsageRecord) -> UsageRecord:
        if not isinstance(other, UsageRecord):
            return NotImplemented
        names = {value for value in (self.model_name, other.model_name) if value}
        return UsageRecord(
            input_tokens=(
                None
                if self.input_tokens is None or other.input_tokens is None
                else self.input_tokens + other.input_tokens
            ),
            output_tokens=(
                None
                if self.output_tokens is None or other.output_tokens is None
                else self.output_tokens + other.output_tokens
            ),
            model_calls=self.model_calls + other.model_calls,
            monetary_cost=(
                None
                if self.monetary_cost is None or other.monetary_cost is None
                else self.monetary_cost + other.monetary_cost
            ),
            paid_monetary_cost=(
                None
                if self.paid_monetary_cost is None or other.paid_monetary_cost is None
                else self.paid_monetary_cost + other.paid_monetary_cost
            ),
            wall_seconds=self.wall_seconds + other.wall_seconds,
            model_name=next(iter(names)) if len(names) == 1 else None,
            raw_metadata=(
                {
                    "accounting_complete": False,
                    "missing_usage": tuple(dict.fromkeys((*self.missing_coordinates, *other.missing_coordinates))),
                }
                if not self.accounting_complete or not other.accounting_complete
                else {}
            ),
        )


@dataclass(frozen=True, slots=True)
class ValidationAttempt:
    """One raw structured-output generation and its complete validation record."""

    index: int
    raw_output: str
    structured_output: Mapping[str, Any] | None
    validation_errors: tuple[str, ...]
    usage: UsageRecord
    normalized_cost: float | None = 0.0

    def __post_init__(self) -> None:
        if self.index < 1:
            raise ValueError("validation-attempt index must be positive")
        if self.normalized_cost is not None and self.normalized_cost < 0:
            raise ValueError("validation-attempt normalized cost must be non-negative")
        if self.structured_output is not None:
            object.__setattr__(self, "structured_output", dict(self.structured_output))


@dataclass(frozen=True, slots=True)
class AgentRequest:
    kind: AgentCallKind
    agent_id: AgentId
    prompt: str
    prompt_component: str
    message: StructuredMessage | None = None
    context: Mapping[str, Any] = field(default_factory=dict)
    max_repair_generations: int | None = None

    def __post_init__(self) -> None:
        if self.agent_id < 0 or not self.prompt:
            raise ValueError("agent request requires a non-negative ID and non-empty prompt")
        if self.max_repair_generations is not None and self.max_repair_generations < 0:
            raise ValueError("max_repair_generations must be non-negative")
        object.__setattr__(self, "context", dict(self.context))


@dataclass(frozen=True, slots=True)
class AgentOutput:
    answer: Answer
    explanation: str
    raw_content: str = ""
    structured_output: Mapping[str, Any] | None = None
    usage: UsageRecord = field(default_factory=UsageRecord)
    error: str | None = None
    prompt_component: str | None = None
    parse_errors: tuple[str, ...] = ()
    attempts: tuple[ValidationAttempt, ...] = ()
    validation_exhausted: bool = False


@dataclass(frozen=True, slots=True)
class RoleOutput:
    feedback: str
    status: ConversationStatus
    raw_content: str = ""
    structured_output: Mapping[str, Any] | None = None
    usage: UsageRecord = field(default_factory=UsageRecord)
    error: str | None = None
    prompt_component: str | None = None
    parse_errors: tuple[str, ...] = ()
    attempts: tuple[ValidationAttempt, ...] = ()
    validation_exhausted: bool = False


@dataclass(frozen=True, slots=True)
class JudgeOutput:
    answer: Answer
    explanation: str
    raw_content: str = ""
    structured_output: Mapping[str, Any] | None = None
    usage: UsageRecord = field(default_factory=UsageRecord)
    error: str | None = None
    prompt_component: str | None = None
    parse_errors: tuple[str, ...] = ()
    attempts: tuple[ValidationAttempt, ...] = ()
    validation_exhausted: bool = False


@dataclass(frozen=True, slots=True)
class TieOutput:
    answer: Answer
    explanation: str
    tied_answers: tuple[Answer, ...]
    raw_content: str = ""
    structured_output: Mapping[str, Any] | None = None
    usage: UsageRecord = field(default_factory=UsageRecord)
    error: str | None = None
    prompt_component: str | None = None
    parse_errors: tuple[str, ...] = ()
    attempts: tuple[ValidationAttempt, ...] = ()
    validation_exhausted: bool = False

    def __post_init__(self) -> None:
        if not self.tied_answers:
            raise ValueError("tie output requires at least one supplied tied answer")


ParsedOutput: TypeAlias = AgentOutput | RoleOutput | JudgeOutput | TieOutput


@runtime_checkable
class AgentProtocol(Protocol):
    async def invoke(self, request: AgentRequest) -> ParsedOutput: ...


@dataclass(frozen=True, slots=True)
class CapacityState:
    remaining_out: tuple[int, ...]
    remaining_in: tuple[int, ...]

    def __post_init__(self) -> None:
        if len(self.remaining_out) != len(self.remaining_in) or any(
            item < 0 for item in (*self.remaining_out, *self.remaining_in)
        ):
            raise ValueError("capacity tuples must have equal length and non-negative values")

    def feasible(self, edge: Edge) -> bool:
        j, i = edge
        return self.remaining_out[j] > 0 and self.remaining_in[i] > 0

    def consume(self, edge: Edge) -> CapacityState:
        if not self.feasible(edge):
            raise ValueError("cannot consume exhausted capacity")
        j, i = edge
        outgoing, incoming = list(self.remaining_out), list(self.remaining_in)
        outgoing[j] -= 1
        incoming[i] -= 1
        return CapacityState(tuple(outgoing), tuple(incoming))


@dataclass(frozen=True, slots=True)
class SampledDecision:
    """Exact conditional routing decision at one permutation position."""

    session: int
    position: int
    initiator_id: AgentId
    receiver_id: AgentId
    scheme: Scheme
    legal_schemes: tuple[Scheme, ...]
    probability: float
    log_probability: Tensor
    free: bool
    forced_zero: bool
    remaining_out: tuple[int, ...]
    remaining_in: tuple[int, ...]
    legal_mask: tuple[bool, ...] = ()
    base_probabilities: tuple[float, ...] = ()
    masked_probabilities: tuple[float, ...] = ()

    def __post_init__(self) -> None:
        if self.session < 1 or self.position < 0 or min(self.initiator_id, self.receiver_id) < 0:
            raise ValueError("sampled decision indices are invalid")
        if not 0.0 < self.probability <= 1.0 or self.scheme not in self.legal_schemes:
            raise ValueError("selected routing probability/action is invalid")
        if self.free == self.forced_zero:
            raise ValueError("a routing position must be either free or forced-zero")
        if self.forced_zero and (self.scheme is not Scheme.ZERO or self.legal_schemes != (Scheme.ZERO,)):
            raise ValueError("forced-zero positions permit and select only zero")
        if self.legal_mask and len(self.legal_mask) != len(SCHEME_ORDER):
            raise ValueError("legal_mask must align with SCHEME_ORDER")

    @property
    def edge(self) -> Edge:
        return self.initiator_id, self.receiver_id

    @property
    def session_index(self) -> int:
        return self.session


RoutingMatrix: TypeAlias = Mapping[Edge, Scheme]


@dataclass(frozen=True, slots=True)
class RoutingAction:
    session: int
    matrix: RoutingMatrix
    decisions: tuple[SampledDecision, ...] = ()
    permutation: tuple[Edge, ...] = ()
    mode: RoutingMode | None = None
    objective_score: float = 0.0
    routing_substream: Mapping[str, Any] | None = None
    inverse_edge_positions: Mapping[Edge, int] | None = None
    routing_generator_state: bytes | None = None

    def __post_init__(self) -> None:
        if self.session < 1 or not self.matrix:
            raise ValueError("routing action needs session >= 1 and a non-empty matrix")
        ids = {value for edge in self.matrix for value in edge}
        size = max(ids) + 1
        expected = {(j, i) for j in range(size) for i in range(size)}
        if set(self.matrix) != expected:
            raise ValueError("routing matrix must contain every ordered edge")
        for (initiator, receiver), scheme in self.matrix.items():
            legal = SELF_SCHEMES if initiator == receiver else CROSS_AGENT_SCHEMES
            if scheme not in legal:
                raise ValueError(f"illegal scheme {scheme!s} on edge {(initiator, receiver)}")
        if self.permutation and (len(self.permutation) != len(expected) or set(self.permutation) != expected):
            raise ValueError("routing permutation must contain every ordered edge exactly once")
        inverse = {edge: position for position, edge in enumerate(self.permutation)}
        if self.inverse_edge_positions is not None and dict(self.inverse_edge_positions) != inverse:
            raise ValueError("inverse edge-position map must match the recorded permutation")
        object.__setattr__(self, "inverse_edge_positions", inverse)
        if self.routing_generator_state is not None and (
            not isinstance(self.routing_generator_state, bytes) or not self.routing_generator_state
        ):
            raise ValueError("routing generator state must be nonempty serialized RNG bytes")
        if self.routing_substream is not None:
            required = {
                "derivation_id",
                "run_seed",
                "trajectory_id",
                "update_id",
                "session_id",
                "seed",
            }
            if set(self.routing_substream) != required:
                raise ValueError("routing_substream must contain the complete seed descriptor")
            if self.routing_substream["session_id"] != self.session:
                raise ValueError("routing_substream session_id must match routing action session")
            # Deferred to avoid a module-import cycle while still validating
            # the version identifier and recomputed seed at the trace boundary.
            from .policies import RoutingSubstream

            try:
                descriptor = RoutingSubstream.from_dict(dict(self.routing_substream))
            except (KeyError, TypeError, ValueError) as error:
                raise ValueError("routing_substream descriptor is invalid") from error
            object.__setattr__(self, "routing_substream", descriptor.to_dict())
        object.__setattr__(self, "matrix", dict(self.matrix))

    def scheme_at(self, edge: Edge) -> Scheme:
        return self.matrix[edge]

    @property
    def selected_edges(self) -> tuple[Edge, ...]:
        return tuple(edge for edge, scheme in self.matrix.items() if scheme.is_active)

    @property
    def session_index(self) -> int:
        return self.session


@dataclass(frozen=True, slots=True)
class HistoryRecord:
    session: int
    event: str
    actor_id: AgentId | None = None
    tag: ConversationTag | None = None
    round_index: int | None = None
    content: str = ""
    prompt_component: str | None = None
    delivery_index: int | None = None
    processing_index: int | None = None
    history_index: int | None = None
    recipient_id: AgentId | None = None
    involved_agent_ids: tuple[AgentId, ...] = ()
    status: str | None = None
    parse_outcome: str | None = None
    answer: str = ""
    explanation: str = ""
    feedback: str = ""
    answer_changed: bool = False
    usage: UsageRecord = field(default_factory=UsageRecord)
    normalized_cost: float | None = 0.0
    usage_missing: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "usage_missing", tuple(dict.fromkeys((*self.usage_missing, *self.usage.missing_coordinates)))
        )
        if self.delivery_index is not None and self.delivery_index < 0:
            raise ValueError("delivery_index must be non-negative")
        if self.processing_index is not None and self.processing_index < 0:
            raise ValueError("processing_index must be non-negative")
        if self.history_index is not None and self.history_index < 1:
            raise ValueError("history_index must be positive")


@dataclass(frozen=True, slots=True)
class SessionStartState:
    session: int
    example: QueryExample
    history: tuple[HistoryRecord, ...]
    answers: tuple[Answer, ...]

    def __post_init__(self) -> None:
        if self.session < 1 or not self.answers:
            raise ValueError("session-start state requires session >= 1 and at least one answer")

    @property
    def query(self) -> str:
        return self.example.query

    @property
    def session_index(self) -> int:
        return self.session


@dataclass(frozen=True, slots=True)
class TerminalRecord:
    session: int
    agent_id: AgentId
    answer: Answer
    tag: ConversationTag | None
    fallback: bool = False

    def __post_init__(self) -> None:
        if self.session < 1 or self.agent_id < 0 or not self.answer:
            raise ValueError("invalid terminal record")
        if self.fallback != (self.tag is None):
            raise ValueError("only untagged effective-list records are fallbacks")

    @property
    def session_index(self) -> int:
        return self.session


@dataclass(slots=True)
class FeedbackState:
    """Step 33: exact answer/whitespace-normalized feedback repetition."""

    seen: list[tuple[str, str]] = field(default_factory=list)
    count: int = 0

    def observe(self, answer: str, feedback: str) -> int:
        key = (answer, " ".join(feedback.split()))
        self.count = self.count + 1 if key in self.seen else 0
        if key not in self.seen:
            self.seen.append(key)
        return self.count


@dataclass(slots=True)
class ConversationTrace:
    tag: ConversationTag
    messages: list[StructuredMessage] = field(default_factory=list)
    outputs: list[ParsedOutput] = field(default_factory=list)
    terminal: TerminalRecord | None = None
    usage: UsageRecord = field(default_factory=UsageRecord)
    normalized_cost: float | None = 0.0
    completed: bool = False
    error: str | None = None
    judge_group_size: int = 1
    completion_status: ConversationStatus | None = None
    safeguard_reason: str | None = None
    safeguard_trigger: SafeguardTrigger | None = None
    feedback_state: FeedbackState = field(default_factory=FeedbackState)
    judge_event_ref: str | None = None

    def append(self, message: StructuredMessage, output: ParsedOutput | None = None) -> None:
        if message.tag != self.tag:
            raise ValueError("trace cannot contain a message from another conversation")
        self.messages.append(message)
        if output is not None:
            self.outputs.append(output)
            self.usage = self.usage + output.usage

    def record_output(self, output: ParsedOutput) -> None:
        """Retain an attempted output without manufacturing a message from it."""

        self.outputs.append(output)
        self.usage = self.usage + output.usage

    def finish(
        self,
        terminal: TerminalRecord,
        *,
        status: ConversationStatus = ConversationStatus.COMPLETE,
        safeguard_reason: str | None = None,
        safeguard_trigger: SafeguardTrigger | None = None,
    ) -> None:
        if terminal.tag != self.tag:
            raise ValueError("terminal record must match trace tag")
        if (status is ConversationStatus.SAFEGUARD_COMPLETE) != (safeguard_reason is not None):
            raise ValueError("safeguard-complete requires exactly one safeguard reason")
        if status is ConversationStatus.SAFEGUARD_COMPLETE and safeguard_trigger is None:
            safeguard_trigger = SafeguardTrigger.VALIDATION_EXHAUSTED
        elif status is not ConversationStatus.SAFEGUARD_COMPLETE and safeguard_trigger is not None:
            raise ValueError("only safeguard-complete may carry a safeguard trigger")
        self.terminal = terminal
        self.completed = True
        self.completion_status = status
        self.safeguard_reason = safeguard_reason
        self.safeguard_trigger = safeguard_trigger


@dataclass(frozen=True, slots=True)
class SafeguardDiagnostics:
    """Completed-conversation safeguard counts, rates, reasons, and usage."""

    completed_conversations: int = 0
    safeguard_completions: int = 0
    round_limit_completions: int = 0
    call_timeout_completions: int = 0
    session_timeout_completions: int = 0
    validation_exhaustion_completions: int = 0
    worker_failure_completions: int = 0
    repeated_feedback_completions: int = 0
    reason_counts: Mapping[str, int] = field(default_factory=dict)
    safeguard_usage: UsageRecord = field(default_factory=UsageRecord)

    def __post_init__(self) -> None:
        counts = (
            self.completed_conversations,
            self.safeguard_completions,
            self.round_limit_completions,
            self.call_timeout_completions,
            self.session_timeout_completions,
            self.validation_exhaustion_completions,
            self.worker_failure_completions,
            self.repeated_feedback_completions,
        )
        if min(counts) < 0:
            raise ValueError("safeguard diagnostic counts must be non-negative")
        if self.safeguard_completions > self.completed_conversations:
            raise ValueError("safeguard completions cannot exceed completed conversations")
        object.__setattr__(self, "reason_counts", dict(self.reason_counts))

    @property
    def safeguard_rate(self) -> float:
        if self.completed_conversations == 0:
            return 0.0
        return self.safeguard_completions / self.completed_conversations

    @classmethod
    def from_traces(cls, traces: Sequence[ConversationTrace]) -> SafeguardDiagnostics:
        completed = tuple(trace for trace in traces if trace.completed)
        safeguarded = tuple(
            trace for trace in completed if trace.completion_status is ConversationStatus.SAFEGUARD_COMPLETE
        )
        trigger_counts = {
            trigger: sum(trace.safeguard_trigger is trigger for trace in safeguarded) for trigger in SafeguardTrigger
        }
        reasons: dict[str, int] = {}
        usage = UsageRecord()
        for trace in safeguarded:
            assert trace.safeguard_reason is not None
            reasons[trace.safeguard_reason] = reasons.get(trace.safeguard_reason, 0) + 1
            usage = usage + trace.usage
        return cls(
            completed_conversations=len(completed),
            safeguard_completions=len(safeguarded),
            round_limit_completions=trigger_counts[SafeguardTrigger.ROUND_LIMIT],
            call_timeout_completions=trigger_counts[SafeguardTrigger.CALL_TIMEOUT],
            session_timeout_completions=trigger_counts[SafeguardTrigger.SESSION_TIMEOUT],
            validation_exhaustion_completions=trigger_counts[SafeguardTrigger.VALIDATION_EXHAUSTED],
            worker_failure_completions=trigger_counts[SafeguardTrigger.WORKER_FAILURE],
            repeated_feedback_completions=trigger_counts[SafeguardTrigger.REPEATED_FEEDBACK],
            reason_counts=reasons,
            safeguard_usage=usage,
        )


@dataclass(frozen=True, slots=True)
class TerminationMetrics:
    session: int
    winner: Answer
    winner_support: float
    runner_up_support: float
    answer_margin: float
    distribution_change: float
    winner_persistent: bool
    routing_opportunity: float
    confident: bool
    stable: bool
    stop: bool
    reason: str = "continue"

    @property
    def session_index(self) -> int:
        return self.session


@dataclass(frozen=True, slots=True)
class SessionResult:
    session: int
    start_state: SessionStartState
    routing: RoutingAction
    traces: tuple[ConversationTrace, ...]
    raw_terminal_answers: Mapping[AgentId, tuple[TerminalRecord, ...]]
    effective_terminal_answers: Mapping[AgentId, tuple[TerminalRecord, ...]]
    resolved_answers: tuple[Answer, ...]
    agent_weights: tuple[float, ...]
    candidate_scores: Mapping[Answer, float]
    weighted_answer: Answer
    weight_tensor: Tensor | None = None
    weight_logit_tensor: Tensor | None = None
    history: tuple[HistoryRecord, ...] = ()
    modal_answers: Mapping[AgentId, tuple[Answer, ...]] = field(default_factory=dict)
    tie_outputs: Mapping[AgentId, TieOutput] = field(default_factory=dict)
    usage: UsageRecord = field(default_factory=UsageRecord)
    normalized_cost: float | None = 0.0
    immediate_reward: float | None = None
    return_value: float | None = None
    future_return: float | None = None
    conversation_credits: Mapping[ConversationTag, float] = field(default_factory=dict)
    termination: TerminationMetrics | None = None
    safeguard_diagnostics: SafeguardDiagnostics = field(default_factory=SafeguardDiagnostics)
    elapsed_seconds: float = 0.0
    resolved_explanations: tuple[str, ...] = ()
    explanation_sources: Mapping[AgentId, ExplanationReference | None] = field(default_factory=dict)

    @property
    def session_index(self) -> int:
        return self.session

    @property
    def weights(self) -> Tensor:
        """Differentiable W output retained for Step 24 training."""

        if self.weight_tensor is not None:
            return self.weight_tensor
        return Tensor(self.agent_weights)

    @property
    def weight_logits(self) -> Tensor | None:
        """Differentiable reliability logits retained for stable W loss."""

        return self.weight_logit_tensor


@dataclass(slots=True)
class Trajectory:
    example: QueryExample
    initial_outputs: tuple[AgentOutput, ...]
    initial_answers: tuple[Answer, ...]
    initial_weights: tuple[float, ...] = ()
    initial_candidate_scores: Mapping[Answer, float] = field(default_factory=dict)
    sessions: list[SessionResult] = field(default_factory=list)
    final_answer: Answer | None = None
    usage: UsageRecord = field(default_factory=UsageRecord)
    completed: bool = False
    error: str | None = None
    prompt_occurrences: tuple[Any, ...] = ()
    call_records: tuple[Mapping[str, Any], ...] = ()
    call_durations: tuple[float, ...] = ()
    elapsed_seconds: float = 0.0
    status: str = "running"
    initial_records: Mapping[AgentId, AgentOutput] = field(default_factory=dict)
    initial_usage: UsageRecord = field(default_factory=UsageRecord)
    accounting_complete: bool = True
    initialization_diagnostics: tuple[Mapping[str, Any], ...] = ()
    failed_session_traces: tuple[ConversationTrace, ...] = ()
    failed_history: tuple[HistoryRecord, ...] = ()
    fallback_records: tuple[Mapping[str, Any], ...] = ()
    operational_halt: Mapping[str, Any] | None = None
    execution_counters: Mapping[str, Any] = field(default_factory=dict)

    @property
    def complete_labelled(self) -> bool:
        return bool(self.completed and self.sessions and self.example.gold_answer is not None)

    @property
    def weighting_eligible(self) -> bool:
        return self.complete_labelled and any(
            self.example.gold_answer in session.resolved_answers and len(set(session.resolved_answers)) > 1
            for session in self.sessions
        )

    @property
    def routing_eligible(self) -> bool:
        """Eligibility for the primary conversation-shaped routing objective."""

        return self.routing_eligible_for("conversation_shaped")

    def routing_eligible_for(self, objective: str) -> bool:
        """Check the selected loss's inputs across the complete trajectory."""

        if objective not in {"conversation_shaped", "standard_reinforce"}:
            raise ValueError("unknown routing objective")
        if objective == "standard_reinforce":
            return self.complete_labelled and all(session.return_value is not None for session in self.sessions)
        return self.complete_labelled and all(
            session.future_return is not None
            and all(trace.tag in session.conversation_credits for trace in session.traces)
            for session in self.sessions
        )

    @property
    def selection_eligible(self) -> bool:
        return self.complete_labelled and all(session.normalized_cost is not None for session in self.sessions)

    def append_session(self, result: SessionResult) -> None:
        expected = len(self.sessions) + 1
        if result.session != expected:
            raise ValueError(f"expected session {expected}, got {result.session}")
        self.sessions.append(result)
        self.usage = self.usage + result.usage

    @property
    def realized_sessions(self) -> int:
        return len(self.sessions)

    @property
    def safeguard_diagnostics(self) -> SafeguardDiagnostics:
        """Aggregate operational-completion reporting over realized sessions."""

        return SafeguardDiagnostics.from_traces(tuple(trace for session in self.sessions for trace in session.traces))

    @staticmethod
    def _required_training_values(
        sessions: Sequence[SessionResult],
        field_name: str,
    ) -> tuple[float, ...]:
        values = tuple(getattr(session, field_name) for session in sessions)
        if any(value is None for value in values):
            raise ValueError(f"{field_name} requires a labelled, fully credited completed trajectory")
        return tuple(float(value) for value in values if value is not None)

    @property
    def complete_returns(self) -> tuple[float, ...]:
        """Return the Step 15 complete returns retained on completed sessions."""

        return self._required_training_values(self.sessions, "return_value")

    @property
    def future_returns(self) -> tuple[float, ...]:
        """Return the Step 17 future-only returns retained on completed sessions."""

        return self._required_training_values(self.sessions, "future_return")


@dataclass(frozen=True, slots=True)
class SharedPrefixResult:
    shared: Trajectory
    prefixes: tuple[Trajectory, ...]
    configurations: tuple[Any, ...]


class TrainingPhase(str, Enum):
    BOOTSTRAP_WEIGHTING = "bootstrap_weighting"
    BOOTSTRAP_ROUTING = "bootstrap_routing"
    WEIGHTING = "weighting"
    ROUTING = "routing"
    PROMPT = "prompt"
    SYSTEM_VALIDATION = "system_validation"


@dataclass(frozen=True, slots=True)
class TrainingRecord:
    phase: TrainingPhase
    outer_cycle: int
    update_index: int
    loss: float | None = None
    validation_score: float | None = None
    best_validation_score: float | None = None
    skipped: bool = False
    reason: str = ""
    prompt_component: str | None = None


def iter_terminal_answers(records: Mapping[AgentId, Sequence[TerminalRecord]]) -> Iterator[Answer]:
    """Iterate answer values without discarding the tagged records themselves."""

    for agent_records in records.values():
        for record in agent_records:
            yield record.answer


# Transitional name while the example is rewritten around ``QueryExample``.
MCQExample = QueryExample
