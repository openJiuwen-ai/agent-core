"""Disabled-by-default, bounded operational tolerance; never a retry policy.

The journal is independent of trainable checkpoints. Unknown amounts retain
their full reservations, and a hard failure always overrides this policy.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import math
from dataclasses import asdict, dataclass
from contextvars import ContextVar, Token
from contextlib import AbstractAsyncContextManager
from enum import Enum
from types import TracebackType
from typing import Any, Mapping

import httpx

FAILURE_POLICY_PROTOCOL = "bounded_provider_accounting_v1"
ACCOUNTING_OBSERVER: ContextVar[Any] = ContextVar("adaptive_accounting_observer", default=None)
DISPATCH_ADMISSION_PROTOCOL = "acknowledged_final_post_admission_v1"
# The audited gateway invokes this synchronous, request-local gate immediately
# before opening its generation POST. Receipt recovery deliberately does not.
GENERATION_DISPATCH_GATE: ContextVar[Any] = ContextVar("adaptive_generation_dispatch_gate", default=None)
DEADLINE_CAUSE_PROTOCOL = "explicit_deadline_cause_v1"
_DEADLINE_SCOPES: ContextVar[tuple[Any, ...]] = ContextVar("adaptive_deadline_scopes", default=())
CALL_TIMING: ContextVar[dict[str, Any] | None] = ContextVar("adaptive_call_timing", default=None)


@dataclass(frozen=True, slots=True)
class _CancellationCause:
    cause: str


def cancel_with_cause(task: asyncio.Task[Any], cause: str) -> bool:
    """Propagate an explicit scheduler/operator cause through SDK cancellation."""
    return task.cancel(_CancellationCause(cause))


def cancellation_cause(error: BaseException | None = None) -> str:
    """Identify a fired deadline before asyncio translates CancelledError.

    No elapsed-time inference is used. An extra cancellation of the deadline's
    owning task is external, even if its deadline fired in the same loop turn.
    """
    tagged = getattr(error, "cancellation_cause", None)
    if isinstance(tagged, str):
        return tagged
    if error is not None and error.args and isinstance(error.args[0], _CancellationCause):
        return error.args[0].cause
    scopes = _DEADLINE_SCOPES.get()
    expired = [scope for scope in scopes if scope.timeout.expired()]
    for scope in reversed(expired):
        owned_expirations = sum(other.task is scope.task for other in expired)
        if scope.task.cancelling() > scope.cancel_count + owned_expirations:
            return "operator-cancellation"
        return scope.kind
    return "operator-cancellation"


def tag_cancellation(error: BaseException, *, phase: str | None = None) -> str:
    cause = cancellation_cause(error)
    setattr(error, "cancellation_cause", cause)
    if phase is not None:
        setattr(error, "timing_phase", phase)
    return cause


class deadline_scope(AbstractAsyncContextManager["deadline_scope"]):
    """Native asyncio deadline with cause visibility while inner calls unwind."""

    def __init__(self, kind: str, *, deadline: float | None = None, timeout: float | None = None) -> None:
        if (deadline is None) == (timeout is None):
            raise ValueError("provide exactly one deadline or timeout")
        self.kind = kind
        self.deadline = deadline
        self.duration = timeout
        self.task: asyncio.Task[Any]
        self.timeout: asyncio.Timeout
        self.token: Token[tuple[Any, ...]]

    async def __aenter__(self) -> deadline_scope:
        task = asyncio.current_task()
        assert task is not None
        self.task = task
        self.cancel_count = self.task.cancelling()
        deadline = self.deadline
        if deadline is None:
            assert self.duration is not None
            deadline = asyncio.get_running_loop().time() + self.duration
        self.timeout = asyncio.timeout_at(deadline)
        self.token = _DEADLINE_SCOPES.set((*_DEADLINE_SCOPES.get(), self))
        await self.timeout.__aenter__()
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, error: BaseException | None, traceback: TracebackType | None,
    ) -> bool | None:
        if isinstance(error, asyncio.CancelledError):
            tag_cancellation(error)
        try:
            await self.timeout.__aexit__(exc_type, error, traceback)
            return None
        except TimeoutError as translated:
            setattr(translated, "cancellation_cause", getattr(error, "cancellation_cause", self.kind))
            raise
        finally:
            _DEADLINE_SCOPES.reset(self.token)


def set_call_phase(phase: str, *, provider_completed: bool | None = None) -> None:
    """Shared request-local phase, including child tasks created by the SDK."""
    state = CALL_TIMING.get()
    if state is not None:
        state["phase"] = phase
        if provider_completed is not None:
            state["provider_completed"] = provider_completed


@dataclass(frozen=True, slots=True)
class FailurePolicyConfig:
    enabled: bool = False
    max_unresolved_requests: int = 5
    max_unresolved_reserved_usd: float = 0.50
    max_consecutive_provider_failures: int = 5

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise ValueError("failure policy enabled must be an explicit boolean")
        for name in ("max_unresolved_requests", "max_consecutive_provider_failures"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        value = self.max_unresolved_reserved_usd
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError("max_unresolved_reserved_usd must be finite and positive")


def proposed_failure_policy() -> FailurePolicyConfig:
    """Explicit, unactivated candidate; retain legacy defaults and fingerprints."""

    return FailurePolicyConfig(enabled=True, max_unresolved_requests=8)


class FailureKind(str, Enum):
    PROVIDER = "provider-unavailable"
    TRANSPORT = "transport-unavailable"
    TIMEOUT = "ambiguous-timeout"
    INTERRUPTED = "interrupted-generation"
    REQUEST = "request-rejected"
    REFUSAL = "provider-refusal"
    SCHEMA = "output-schema"
    CREDENTIAL = "credential-rejected"
    BILLING = "billing-capacity"
    BINDING = "serving-binding"
    COUNTING = "counting-bound"
    CORRUPTION = "receipt-corruption"
    INTEGRATION = "integration-error"
    MISSING = "accounting-incomplete"


PROVIDER_FAILURES = frozenset({FailureKind.PROVIDER, FailureKind.TRANSPORT, FailureKind.TIMEOUT})
HARD_FAILURES = frozenset(
    {
        FailureKind.CREDENTIAL,
        FailureKind.BILLING,
        FailureKind.BINDING,
        FailureKind.COUNTING,
        FailureKind.CORRUPTION,
        FailureKind.INTEGRATION,
    }
)


def http_failure_kind(status: int) -> FailureKind:
    if status in (401, 403):
        return FailureKind.CREDENTIAL
    if status == 402:
        return FailureKind.BILLING
    if status in (408, 429, 500, 502, 503, 504):
        return FailureKind.PROVIDER
    return FailureKind.REQUEST


def classify_failure(error: BaseException) -> FailureKind:
    """Use typed transport evidence, never broad substring retry heuristics."""
    tagged = getattr(error, "failure_kind", None)
    if tagged is not None:
        try:
            return FailureKind(tagged)
        except ValueError:
            return FailureKind.CORRUPTION
    if isinstance(error, asyncio.CancelledError):
        if cancellation_cause(error) in {"call-deadline", "transport-timeout"}:
            return FailureKind.TIMEOUT
        return FailureKind.INTERRUPTED
    if isinstance(error, (TimeoutError, httpx.TimeoutException)):
        return FailureKind.TIMEOUT
    if isinstance(error, (httpx.TransportError, ConnectionError)):
        return FailureKind.TRANSPORT
    # The installed public Model.stream wraps its typed wait_for timeout in a
    # BaseError. Follow only the explicit exception chain, never its prose.
    if isinstance(error.__cause__, (TimeoutError, httpx.TimeoutException)):
        return FailureKind.TIMEOUT
    return FailureKind.INTEGRATION


class ProviderFailure(RuntimeError):
    """Classified failure without any assertion that replay is safe."""

    def __init__(self, message: str, kind: FailureKind) -> None:
        super().__init__(message)
        self.failure_kind = kind.value


def fingerprint(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def new_policy_state(
    config: FailurePolicyConfig, authorization: Mapping[str, Any], bindings: Mapping[str, Any]
) -> dict[str, Any]:
    if not config.enabled:
        raise ValueError("the candidate policy must be explicitly enabled before activation")
    if authorization.get("approved") is not True or not bindings or not all(isinstance(key, str) for key in bindings):
        raise ValueError("policy activation requires explicit authorization and condition bindings")
    return {
        "protocol": FAILURE_POLICY_PROTOCOL,
        "config": asdict(config),
        "authorization": copy.deepcopy(dict(authorization)),
        "authorization_sha256": fingerprint(authorization),
        "bindings": copy.deepcopy(dict(bindings)),
        "historical_adoptions": [],
        "events": [],
    }


def validate_policy_state(state: Mapping[str, Any], entries: list[dict[str, Any]]) -> None:
    if state.get("protocol") != FAILURE_POLICY_PROTOCOL or not FailurePolicyConfig(**state["config"]).enabled:
        raise ValueError("invalid failure-policy protocol or configuration")
    if (
        state.get("authorization_sha256") != fingerprint(state.get("authorization"))
        or state.get("authorization", {}).get("approved") is not True
        or not state.get("bindings")
    ):
        raise ValueError("failure-policy authorization or bindings changed")
    pending: dict[int, str] = {}
    terminal: set[int] = set()
    for index, event in enumerate(state.get("events", [])):
        if event.get("event_id") != index or event.get("after_entries", -1) not in range(len(entries) + 1):
            raise ValueError("corrupt failure-policy event identity")
        kind = event.get("event")
        if kind == "logical-start":
            logical = event.get("logical_request_id")
            if logical != len(pending) or not isinstance(event.get("backend"), str):
                raise ValueError("corrupt logical provider request registration")
            pending[logical] = event["backend"]
        elif kind == "logical-finish":
            logical = event.get("logical_request_id")
            if (
                logical not in pending
                or logical in terminal
                or event.get("outcome")
                not in {"provider-completed", "provider-failed", "other-failure", "not-dispatched"}
            ):
                raise ValueError("corrupt terminal logical provider outcome")
            terminal.add(logical)
        elif kind == "recovery-disposition":
            dispatch = event.get("dispatch_id")
            if (
                isinstance(dispatch, bool)
                or not isinstance(dispatch, int)
                or not 0 <= dispatch < len(entries)
                or event.get("disposition") not in {"terminal-unresolved", "accounting-resolved"}
                or not isinstance(event.get("evidence"), dict)
                or not event["evidence"]
            ):
                raise ValueError("invalid explicit request-recovery disposition")
        elif kind == "drain":
            if not isinstance(event.get("reason"), str):
                raise ValueError("corrupt failure-policy draining decision")
        else:
            raise ValueError("unknown failure-policy event")
    for entry in entries:
        logical = entry.get("provider_logical_request_id")
        if logical is not None and logical not in pending:
            raise ValueError("physical receipt refers to an unknown logical request")
        intents: list[str] = []
        for index, event in enumerate(entry.get("recovery_events", [])):
            if (
                event.get("event_id") != index
                or event.get("event") not in {"gateway-frame", "metadata-lookup-intent", "metadata-lookup-result"}
                or not isinstance(event.get("details"), dict)
            ):
                raise ValueError("corrupt bounded metadata-recovery journal")
            if event["event"] == "metadata-lookup-intent":
                generation = event["details"].get("generation_id")
                if not isinstance(generation, str) or not generation or (intents and generation != intents[0]):
                    raise ValueError("metadata-recovery generation identity changed")
                intents.append(generation)
        if len(intents) > 3:
            raise ValueError("metadata-recovery attempt limit exceeded")


def logical_summary(state: Mapping[str, Any]) -> dict[str, Any]:
    """Fold each backend's registered order, not nondeterministic finish order."""
    starts = [item for item in state["events"] if item["event"] == "logical-start"]
    finishes = {item["logical_request_id"]: item for item in state["events"] if item["event"] == "logical-finish"}
    streaks: dict[str, int] = {}
    blocked: set[str] = set()
    for item in starts:
        backend = item["backend"]
        streaks.setdefault(backend, 0)
        if backend in blocked:
            continue
        terminal = finishes.get(item["logical_request_id"])
        if terminal is None:
            blocked.add(backend)
            continue
        if terminal["outcome"] == "provider-completed":
            streaks[backend] = 0
        elif terminal["outcome"] == "provider-failed":
            streaks[backend] += 1
        # A refusal, schema issue, or admission refusal is not a provider
        # failure and does not masquerade as a successful provider completion.
    return {
        "backend_streaks": streaks,
        "pending_logical_ids": [
            item["logical_request_id"] for item in starts if item["logical_request_id"] not in finishes
        ],
    }
