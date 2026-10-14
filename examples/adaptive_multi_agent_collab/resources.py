"""Request admission and a physical expenditure ledger, independent of rewards.

No client or tokenizer is downloaded here. A production experiment declares its
backend tokenizer, serialization, tariff and limits before constructing clients.
"""

from __future__ import annotations

import asyncio
import copy
import importlib.metadata
import hashlib
import json
import math
import re
import time
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Protocol

from .config import Config, ResourceConfig
from .failure_policy import (
    ACCOUNTING_OBSERVER,
    CALL_TIMING,
    GENERATION_DISPATCH_GATE,
    DEADLINE_CAUSE_PROTOCOL,
    HARD_FAILURES,
    PROVIDER_FAILURES,
    FailureKind,
    FailurePolicyConfig,
    classify_failure,
    cancel_with_cause,
    tag_cancellation,
    logical_summary,
    new_policy_state,
    validate_policy_state,
)
from .model_io import invoke_model
from .schemas import UsageRecord
from .shutdown import raise_if_stopping

RESOURCE_PROTOCOL = "independent_stage_paid_reservations_v3"


def validate_reconciliation_receipt(state: Mapping[str, Any], receipt: Mapping[str, Any]) -> None:
    """Lazy public boundary for validated accounting lineage."""
    from .reconciliation import validate_reconciliation_receipt as validate

    validate(state, receipt)


async def bounded_call(awaitable: Any, deadline: float) -> Any:
    """Bound cancellation cleanup even if a backend suppresses cancellation.

    A late completion is discarded. Its task retains the semaphore acquired by
    its dispatch body until it actually exits, preserving physical concurrency.
    """
    task = asyncio.create_task(awaitable)

    def discard(done: asyncio.Task[Any]) -> None:
        if not done.cancelled():
            done.exception()

    try:
        done, _ = await asyncio.wait({task}, timeout=max(0.0, deadline - time.monotonic()))
        if done:
            return task.result()
        failure = TimeoutError("logical-call-deadline")
        setattr(failure, "cancellation_cause", "call-deadline")
        raise failure
    except BaseException as failure:
        cause = tag_cancellation(failure) if isinstance(failure, asyncio.CancelledError) else getattr(
            failure, "cancellation_cause", "shutdown-cleanup"
        )
        timing = CALL_TIMING.get() or {}
        setattr(failure, "timing_phase", timing.get("phase", "generation"))
        setattr(failure, "provider_completed", timing.get("provider_completed", False))
        if not task.done():
            cancel_with_cause(task, cause)
            await asyncio.wait({task}, timeout=0.05)
        if task.done():
            # model_io attaches partial native usage to cancellation errors.
            # Preserve the bill without reviving the cancelled answer.
            try:
                late = task.result()
                partial = getattr(late, "usage_metadata", None)
                if partial is not None:
                    setattr(failure, "usage", partial)
            except BaseException as partial_error:
                for name in ("usage", "usage_record", "timing_phase", "provider_completed"):
                    partial = getattr(partial_error, name, None)
                    if partial is not None:
                        setattr(failure, name, partial)
            discard(task)
        else:
            task.add_done_callback(discard)
        raise


class ResourceFailure(RuntimeError):
    def __init__(self, reason: str, usage_record: UsageRecord | None = None) -> None:
        super().__init__(reason)
        self.usage_record = usage_record if usage_record is not None else UsageRecord()


class AccountingFailure(ResourceFailure):
    """A positively weighted cost coordinate could not be measured."""


class BudgetExhausted(ResourceFailure):
    """An incomplete stage, never validation non-improvement."""


class PolicyStopped(BudgetExhausted):
    """Durable terminal drain: no new calls, but dispatched calls may finish."""


class UncertaintyCapacity(ResourceFailure):
    """Temporary in-flight uncertainty capacity; waiting never dispatches a call."""


class TransientFailure(RuntimeError):
    """Explicit backend evidence allowing one safe transport retry.

    Generic exceptions, rate-limit names and timeouts alone never qualify.
    A backend integration must supply the documented non-generation/idempotency
    guarantee; retries keep the body and generation seed unchanged.
    """

    def __init__(
        self,
        message: str,
        *,
        documentation: str,
        generation_not_started: bool = False,
        idempotent_replay: bool = False,
        retry_after: float = 1.0,
        usage: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        if not documentation or not (generation_not_started or idempotent_replay):
            raise ValueError("safe transport retry requires documented replay evidence")
        if not math.isfinite(retry_after) or retry_after < 0:
            raise ValueError("Retry-After must be finite and nonnegative")
        self.documentation = documentation
        self.generation_not_started = generation_not_started
        self.retry_after = retry_after
        self.usage = usage


@dataclass(frozen=True)
class BackendResourceProfile:
    tokenizer_name: str
    tokenizer_revision: str
    tokenizer_kind: str
    context_limit: int
    max_output_tokens: int
    input_price: float
    output_price: float
    currency: str
    tariff_revision: str
    min_output_tokens: int = 1
    cached_input_price: float | None = None
    # Maximum applicable cache-write rate (including any TTL premium). Native
    # billed cost is required for positive writes when their subtype is absent.
    cache_write_price: float | None = None
    reasoning_price: float | None = None
    chat_serialization: str = "native"
    counting_method: str = "local"
    chat_tokens_per_message: int | None = None
    chat_priming_tokens: int | None = None
    reasoning_in_output_budget: bool = False
    reasoning_allowance: int | None = None
    reference_input_price: float | None = None
    reference_output_price: float | None = None
    reference_pricing_date: str | None = None
    billing_fee_multiplier: float = 1.0
    request_fee: float = 0.0
    conservative_tokens_per_byte: float | None = None
    estimator_approval: str | None = None
    estimator_evidence: str | None = None
    tokenizer_cache_directory: str | None = None
    counting_safety_factor: float = 1.0
    counting_safety_tokens: int = 0
    reservation_safety_factor: float = 1.0
    billing_mode: str = "unknown"
    require_native_accounting: bool = False

    def __post_init__(self) -> None:
        for name in ("tokenizer_name", "tokenizer_revision", "currency", "tariff_revision"):
            if not getattr(self, name).strip():
                raise ValueError(f"backend resource {name} is required")
        if self.tokenizer_kind not in {"transformers", "tiktoken", "conservative"}:
            raise ValueError("unsupported backend tokenizer")
        if self.counting_method not in {"local", "estimated"}:
            raise ValueError("local request token counts must be labelled local or estimated")
        if self.chat_serialization != "native":
            raise ValueError("this adapter requires native system/user roles")
        if self.context_limit < 1 or not 1 <= self.min_output_tokens <= self.max_output_tokens:
            raise ValueError("invalid backend context or output range")
        for name in (
            "input_price",
            "output_price",
            "cached_input_price",
            "cache_write_price",
            "reasoning_price",
            "reference_input_price",
            "reference_output_price",
            "request_fee",
        ):
            value = getattr(self, name)
            if value is not None and (not math.isfinite(value) or value < 0):
                raise ValueError("tariffs are finite nonnegative currency units per token")
        if (self.reference_input_price is None) != (self.reference_output_price is None):
            raise ValueError("both frozen reference rates must be declared together")
        if self.reference_input_price is not None and not self.reference_pricing_date:
            raise ValueError("frozen reference pricing requires its recorded date")
        if not math.isfinite(self.billing_fee_multiplier) or self.billing_fee_multiplier < 1:
            raise ValueError("billing fee multiplier must be finite and at least one")
        for name in ("counting_safety_factor", "reservation_safety_factor"):
            if not math.isfinite(getattr(self, name)) or getattr(self, name) < 1:
                raise ValueError(f"{name} must be finite and at least one")
        if self.counting_safety_tokens < 0 or self.billing_mode not in {"unknown", "credits", "byok"}:
            raise ValueError("invalid counting allowance or billing mode")
        if self.tokenizer_kind == "conservative" and (
            self.counting_method != "estimated"
            or self.conservative_tokens_per_byte is None
            or not math.isfinite(self.conservative_tokens_per_byte)
            or self.conservative_tokens_per_byte <= 0
            or not self.estimator_approval
            or self.chat_tokens_per_message is None
            or self.chat_priming_tokens is None
        ):
            raise ValueError("conservative counting requires explicit framing and provisional approval")
        if self.reasoning_in_output_budget and (self.reasoning_allowance is None or self.reasoning_allowance < 1):
            raise ValueError("reasoning backends need an explicit total generation allowance")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> BackendResourceProfile:
        return cls(**dict(value))


class TokenCounter(Protocol):
    def count_input(self, messages: Sequence[Mapping[str, str]], schema: Any = None) -> int: ...
    def count_output(self, text: str) -> int: ...


class LocalTokenCounter:
    """Pinned backend tokenizer; chat framing is declared rather than guessed."""

    def __init__(self, profile: BackendResourceProfile) -> None:
        self.profile = profile
        if profile.tokenizer_kind == "conservative":
            self.tokenizer = None
            return
        if profile.tokenizer_kind == "transformers":
            if not re.fullmatch(r"[a-f0-9]{40}", profile.tokenizer_revision):
                raise ValueError("backend tokenizer requires an immutable 40-character revision")
            from transformers import AutoTokenizer

            self.tokenizer = AutoTokenizer.from_pretrained(
                profile.tokenizer_name, revision=profile.tokenizer_revision, local_files_only=True
            )
            if not self.tokenizer.chat_template:
                raise ValueError("backend tokenizer has no recorded chat template")
        else:
            if importlib.metadata.version("tiktoken") != profile.tokenizer_revision:
                raise ValueError("tiktoken package revision differs from the declared tokenizer revision")
            if profile.chat_tokens_per_message is None or profile.chat_priming_tokens is None:
                raise ValueError("tiktoken needs declared backend chat framing counts")
            if profile.counting_method != "estimated":
                raise ValueError("tiktoken framing counts must be labelled estimated")
            # get_encoding can download missing vocabulary. Construct only from
            # an explicitly present cache; deny that operation in offline mode.
            import os
            from pathlib import Path

            cache = profile.tokenizer_cache_directory or os.environ.get("TIKTOKEN_CACHE_DIR")
            if not cache or not Path(cache).is_dir():
                raise ValueError("TIKTOKEN_CACHE_DIR must point to a prepopulated local vocabulary cache")
            import tiktoken
            import tiktoken.load

            original_read = tiktoken.load.read_file
            previous_cache = os.environ.get("TIKTOKEN_CACHE_DIR")

            def no_download(path: str) -> bytes:
                raise FileNotFoundError(f"tokenizer vocabulary is not cached: {path}")

            # Initialization is synchronous, before workflows or engine threads.
            tiktoken.load.read_file = no_download
            os.environ["TIKTOKEN_CACHE_DIR"] = cache
            try:
                self.tokenizer = tiktoken.get_encoding(profile.tokenizer_name)
            finally:
                tiktoken.load.read_file = original_read
                if previous_cache is None:
                    os.environ.pop("TIKTOKEN_CACHE_DIR", None)
                else:
                    os.environ["TIKTOKEN_CACHE_DIR"] = previous_cache

    def count_input(self, messages: Sequence[Mapping[str, str]], schema: Any = None) -> int:
        from .output_contract import schema_counting_messages

        effective = [*messages, *schema_counting_messages(schema)]
        if self.profile.tokenizer_kind == "transformers":
            if schema is not None and self.profile.counting_method != "estimated":
                raise ValueError("native schema framing requires an explicitly estimated counting profile")
            raw = len(self.tokenizer.apply_chat_template(effective, tokenize=True, add_generation_prompt=True))
        elif self.profile.tokenizer_kind == "conservative":
            raw = int(self.profile.chat_priming_tokens or 0) + sum(
                int(self.profile.chat_tokens_per_message or 0)
                + sum(self.count_output(value) for value in item.values())
                for item in effective
            )
        else:
            raw = int(self.profile.chat_priming_tokens or 0) + sum(
                int(self.profile.chat_tokens_per_message or 0)
                + sum(len(self.tokenizer.encode(value)) for value in item.values())
                for item in effective
            )
        return math.ceil(raw * self.profile.counting_safety_factor) + self.profile.counting_safety_tokens

    def count_output(self, text: str) -> int:
        if self.profile.tokenizer_kind == "conservative":
            return math.ceil(len(text.encode("utf-8")) * float(self.profile.conservative_tokens_per_byte))
        if self.profile.tokenizer_kind == "transformers":
            return len(self.tokenizer.encode(text, add_special_tokens=False, truncation=False))
        return len(self.tokenizer.encode(text))


class ExpenditureLedger:
    """Every physical request is retained; unresolved charges stay reserved.

    The ledger is monotonic expenditure, so restoring a selected model must never
    erase costs incurred while trying later checkpoints.
    """

    def __init__(self, limits: ResourceConfig, *, concurrency: int = 3) -> None:
        self.limits = limits
        self.stage = "development"
        self.entries: list[dict[str, Any]] = []
        self.halted: dict[str, Any] | None = None
        self.accounting_reconciliations: list[dict[str, Any]] = []
        self.halt_releases: list[dict[str, Any]] = []
        self.reconciliation_audit: dict[str, Any] | None = None
        self.accounting_lookup_intents: list[dict[str, Any]] = []
        self.scopes: dict[str, dict[str, Any]] = {}
        self.active_scope: str | None = None
        self.scope_activations: list[dict[str, Any]] = []
        self.historical_accounting_exceptions: list[dict[str, Any]] = []
        self.limit_amendments: list[dict[str, Any]] = []
        self.halt_events: list[dict[str, Any]] = []
        self._historical_exception_context: dict[str, Any] | None = None
        self.failure_policy: dict[str, Any] | None = None
        self.campaign_binding: dict[str, Any] = {}
        self._failure_policy_guard: Callable[[], bool] | None = None
        self._recovered_inflight: set[int] = set()
        self.semaphore = asyncio.Semaphore(concurrency)
        self.on_change: Callable[[], None] | None = None
        self._lock = threading.RLock()
        self.durable_journal = None
        self._journal_entry_count = 0
        self._observation_invariants: dict[tuple[int, str], Any] = {}

    def persist(self, entry: dict[str, Any] | None = None) -> None:
        if self.durable_journal is not None:
            with self._lock:
                new = self.entries[self._journal_entry_count:]
                selected = list(new)
                if entry is not None and entry not in selected:
                    selected.append(entry)
                entries = [copy.deepcopy({k: v for k, v in item.items() if k != "recovery_events"}) for item in selected]
                try:
                    self.durable_journal.changed(self.state_dict(include_entries=False), entries)
                except Exception as error:
                    raise AccountingFailure("durable accounting write admission failed; dispatch stopped") from error
                self._journal_entry_count = len(self.entries)
        if self.on_change is not None:
            self.on_change()

    async def flush_durable(self) -> None:
        if self.durable_journal is not None:
            try:
                await self.durable_journal.flush()
            except Exception as error:
                raise AccountingFailure("durable accounting persistence failed; dispatch stopped") from error

    async def record_progress(self, kind: str, payload: Any) -> None:
        if self.durable_journal is not None:
            try:
                await self.durable_journal.progress(kind, payload)
            except Exception as error:
                raise AccountingFailure("durable partial progress persistence failed; dispatch stopped") from error

    async def write_artifact(self, path: str | Path, value: Any) -> None:
        if self.durable_journal is None:
            raise AccountingFailure("durable artifact publication requires the campaign journal")
        try:
            await self.durable_journal.artifact(Path(path), value)
        except Exception as error:
            raise AccountingFailure("durable report publication failed") from error

    def state_dict(self, *, include_entries: bool = True) -> dict[str, Any]:
        with self._lock:
            result = {
                "protocol": RESOURCE_PROTOCOL,
                "limits": asdict(self.limits),
                "stage": self.stage,
                **({"entries": copy.deepcopy(self.entries)} if include_entries else {}),
                "halted": copy.deepcopy(self.halted),
            }
            if self.accounting_reconciliations or self.halt_releases:
                result["accounting_reconciliations"] = copy.deepcopy(self.accounting_reconciliations)
                result["halt_releases"] = copy.deepcopy(self.halt_releases)
            if self.scopes:
                result["scopes"] = copy.deepcopy(self.scopes)
                result["active_scope"] = self.active_scope
                result["scope_activations"] = copy.deepcopy(self.scope_activations)
            if self.reconciliation_audit is not None:
                result["reconciliation_audit"] = copy.deepcopy(self.reconciliation_audit)
                result["accounting_lookup_intents"] = copy.deepcopy(self.accounting_lookup_intents)
            if self.historical_accounting_exceptions:
                result["historical_accounting_exceptions"] = copy.deepcopy(self.historical_accounting_exceptions)
            if self.limit_amendments:
                result["limit_amendments"] = copy.deepcopy(self.limit_amendments)
            if self.halt_events:
                result["halt_events"] = copy.deepcopy(self.halt_events)
            if self.failure_policy is not None:
                result["failure_policy"] = copy.deepcopy(self.failure_policy)
            if self.campaign_binding:
                result["campaign_binding"] = copy.deepcopy(self.campaign_binding)
            return result

    def load_state_dict(self, value: Mapping[str, Any]) -> None:
        """Reject malformed restores atomically, including their audit extensions."""
        with self._lock:
            fields = (
                "entries",
                "stage",
                "halted",
                "accounting_reconciliations",
                "halt_releases",
                "reconciliation_audit",
                "scopes",
                "active_scope",
                "scope_activations",
                "accounting_lookup_intents",
                "historical_accounting_exceptions",
                "halt_events",
                "limit_amendments",
                "failure_policy",
                "campaign_binding",
            )
            before = {name: copy.deepcopy(getattr(self, name)) for name in fields}
            guard = self._failure_policy_guard
            existing_policy = copy.deepcopy(self.failure_policy)
            try:
                self._load_state_dict(value)
                self._historical_exception_context = None
                self._failure_policy_guard = guard if existing_policy is not None else None
                self._recovered_inflight = {
                    item["dispatch_id"] for item in self.entries if item["status"] in {"reserved", "dispatched"}
                }
            except BaseException:
                for name, prior in before.items():
                    setattr(self, name, prior)
                raise

    def _load_state_dict(self, value: Mapping[str, Any]) -> None:
        with self._lock:
            if value.get("protocol") != RESOURCE_PROTOCOL or value.get("limits") != asdict(self.limits):
                raise ValueError("resource ledger configuration mismatch")
            binding = value.get("campaign_binding", {})
            if not isinstance(binding, dict) or (self.campaign_binding and self.campaign_binding != binding):
                raise ValueError("ledger restore would replace independent campaign identity")
            if binding and (
                binding.get("protocol") != "independent_campaign_v1"
                or set(binding) != {"protocol", "campaign_id", "directory", "registry_path", "manifest_path"}
            ):
                raise ValueError("invalid independent campaign ledger binding")
            self.campaign_binding = copy.deepcopy(binding)
            entries = copy.deepcopy(list(value["entries"]))
            if value.get("stage") not in {"compatibility", "pilot", "development", "test"}:
                raise ValueError("invalid saved expenditure stage")
            for index, entry in enumerate(entries):
                if entry.get("dispatch_id") != index or entry.get("stage") not in {
                    "compatibility",
                    "pilot",
                    "development",
                    "test",
                }:
                    raise ValueError("corrupt physical dispatch identity or stage")
                for name in ("reserved", "charged"):
                    charge = entry.get(name)
                    if charge is None and name == "charged":
                        continue
                    if (
                        isinstance(charge, bool)
                        or not isinstance(charge, (int, float))
                        or not math.isfinite(charge)
                        or charge < 0
                    ):
                        raise ValueError("corrupt monetary amount in saved ledger")
            # Reloading an earlier joint checkpoint keeps the physical expenditure
            # suffix; a fresh process may restore the entire saved ledger.
            merged = copy.deepcopy(self.entries)
            mutable = {"status", "charged", "usage", "elapsed_seconds", "error", "recovery_events"}
            for index in range(min(len(entries), len(merged))):
                current, saved = merged[index], entries[index]
                if current == saved:
                    continue
                if {k: v for k, v in current.items() if k not in mutable} != {
                    k: v for k, v in saved.items() if k not in mutable
                }:
                    raise ValueError("ledger restore would rewrite physical request identity")
                current_pending = current["status"] in {"reserved", "dispatched"}
                saved_pending = saved["status"] in {"reserved", "dispatched"}
                if not current_pending and not saved_pending:
                    raise ValueError("ledger restore would rewrite finalized expenditure")
                if current_pending:
                    current_recovery, saved_recovery = current.get("recovery_events", []), saved.get(
                        "recovery_events", []
                    )
                    common_recovery = min(len(current_recovery), len(saved_recovery))
                    if current_recovery[:common_recovery] != saved_recovery[:common_recovery]:
                        raise ValueError("ledger restore would rewrite bounded metadata-recovery history")
                    if len(current_recovery) > len(saved_recovery):
                        saved["recovery_events"] = copy.deepcopy(current_recovery)
                    merged[index] = saved
            merged.extend(entries[len(merged) :])
            saved_halt = value.get("halted")
            if saved_halt is not None and (
                not isinstance(saved_halt, Mapping) or not isinstance(saved_halt.get("reason"), str)
            ):
                raise ValueError("corrupt campaign halt state")
            self.entries = merged
            saved_policy = copy.deepcopy(value.get("failure_policy"))
            if saved_policy is not None:
                validate_policy_state(saved_policy, merged)
                if self.failure_policy is not None:
                    current = self.failure_policy
                    if {k: v for k, v in current.items() if k not in {"events", "historical_adoptions"}} != {
                        k: v for k, v in saved_policy.items() if k not in {"events", "historical_adoptions"}
                    }:
                        raise ValueError("ledger restore would rewrite failure-policy activation")
                    common = min(len(current["events"]), len(saved_policy["events"]))
                    if current["events"][:common] != saved_policy["events"][:common]:
                        raise ValueError("ledger restore would rewrite failure-policy history")
                    if len(current["events"]) > len(saved_policy["events"]):
                        saved_policy = current
                    prior = current.get("historical_adoptions", [])
                    saved = saved_policy.get("historical_adoptions", [])
                    common_adoptions = min(len(prior), len(saved))
                    if prior[:common_adoptions] != saved[:common_adoptions]:
                        raise ValueError("ledger restore would rewrite historical uncertainty adoption")
                    if len(prior) > len(saved):
                        saved_policy["historical_adoptions"] = copy.deepcopy(prior)
                self.failure_policy = saved_policy
            self.stage = str(value["stage"])
            saved_audit = value.get("reconciliation_audit")
            if saved_audit is not None:
                if not isinstance(saved_audit, dict) or self.reconciliation_audit not in (None, saved_audit):
                    raise ValueError("ledger restore would rewrite the registered accounting audit")
                self.reconciliation_audit = copy.deepcopy(saved_audit)
            for field in (
                "accounting_reconciliations",
                "halt_releases",
                "accounting_lookup_intents",
                "historical_accounting_exceptions",
                "halt_events",
                "limit_amendments",
            ):
                current = getattr(self, field)
                saved = copy.deepcopy(value.get(field, []))
                if not isinstance(saved, list) or current[: min(len(current), len(saved))] != saved[: len(current)]:
                    raise ValueError("ledger restore would rewrite accounting audit history")
                setattr(self, field, current if len(current) >= len(saved) else saved)
            for index, event in enumerate(self.halt_events):
                if (
                    event.get("event_id") != index
                    or not isinstance(event.get("reason"), str)
                    or event.get("stage") not in {"compatibility", "pilot", "development", "test"}
                    or isinstance(event.get("after_entries"), bool)
                    or not isinstance(event.get("after_entries"), int)
                    or not 0 <= event["after_entries"] <= len(merged)
                ):
                    raise ValueError("corrupt monotonic halt event history")
            saved_scopes = copy.deepcopy(value.get("scopes", {}))
            if not isinstance(saved_scopes, dict) or any(
                name in self.scopes and self.scopes[name] != scope for name, scope in saved_scopes.items()
            ):
                raise ValueError("ledger restore would rewrite a registered expenditure scope")
            self.scopes.update(saved_scopes)
            for scope_id, scope in self.scopes.items():
                if (
                    not isinstance(scope_id, str)
                    or not scope_id.strip()
                    or not isinstance(scope, dict)
                    or scope.get("stage") != "compatibility"
                    or isinstance(scope.get("max_attempts"), bool)
                    or not isinstance(scope.get("max_attempts"), int)
                    or not 1 <= scope["max_attempts"] <= 24
                    or isinstance(scope.get("budget"), bool)
                    or not isinstance(scope.get("budget"), (int, float))
                    or not 0 < scope["budget"] <= 0.5
                    or not isinstance(scope.get("bindings"), dict)
                ):
                    raise ValueError("corrupt saved expenditure scope")
                self._validate_extended_scope(scope_id, scope)
            if any(
                item.get("scope_id") is not None
                and (item["scope_id"] not in self.scopes or self.scopes[item["scope_id"]]["stage"] != item["stage"])
                for item in merged
            ):
                raise ValueError("physical dispatch has an invalid expenditure scope")
            activations = copy.deepcopy(value.get("scope_activations", []))
            common = min(len(self.scope_activations), len(activations))
            if not isinstance(activations, list) or self.scope_activations[:common] != activations[:common]:
                raise ValueError("ledger restore would rewrite scope activation history")
            if len(activations) > len(self.scope_activations):
                self.scope_activations = activations
            for activation in self.scope_activations:
                if (
                    not isinstance(activation, dict)
                    or activation.get("scope_id") not in (None, *self.scopes)
                    or isinstance(activation.get("after_entries"), bool)
                    or not isinstance(activation.get("after_entries"), int)
                    or not 0 <= activation["after_entries"] <= len(merged)
                ):
                    raise ValueError("corrupt scope activation history")
            self.active_scope = self.scope_activations[-1]["scope_id"] if self.scope_activations else None
            if activations and value.get("active_scope") != activations[-1]["scope_id"]:
                raise ValueError("saved active scope disagrees with its durable history")
            if not activations and value.get("active_scope") is not None:
                raise ValueError("saved active scope has no durable activation history")
            if self.active_scope is not None and self.active_scope not in self.scopes:
                raise ValueError("unknown active expenditure scope")
            self.halted = self.halted or (copy.deepcopy(dict(saved_halt)) if saved_halt is not None else None)
            if self.accounting_reconciliations or self.halt_releases or self.accounting_lookup_intents:
                from .reconciliation import validate_accounting_history

                validate_accounting_history(self.state_dict())
                if (
                    any(item["prior_halt"] == self.halted for item in self.halt_releases)
                    and not self._unreleased_halt_events()
                ):
                    self.halted = None
            if self.historical_accounting_exceptions:
                from .continuation import validate_exception_history

                validate_exception_history(self.state_dict())
            if self.failure_policy and self.failure_policy.get("historical_adoptions"):
                self._adopted_halt()

    def register_historical_exception(self, receipt: Mapping[str, Any]) -> None:
        """Record the explicit exception without clearing or settling its old halt."""
        from .continuation import validate_historical_exception_receipt

        with self._lock:
            existing = next(
                (
                    item
                    for item in self.historical_accounting_exceptions
                    if item["exception_id"] == receipt["exception_id"]
                ),
                None,
            )
            if existing is not None:
                if existing != receipt:
                    raise ValueError("historical accounting exception identity changed")
                return
            from .continuation import EXTENSION_PROTOCOL, LIMIT_AMENDMENT_PROTOCOL

            if receipt.get("protocol") == EXTENSION_PROTOCOL:
                proposed = self.state_dict()
                after_limits = receipt["authorization"]["authorized_limits"]
                amendment = {
                    "protocol": LIMIT_AMENDMENT_PROTOCOL,
                    "exception_id": receipt["exception_id"],
                    "source_ledger_sha256": receipt["source_ledger_sha256"],
                    "authorization_sha256": receipt["authorization_sha256"],
                    "before_limits": asdict(self.limits),
                    "after_limits": after_limits,
                    "after_entries": len(self.entries),
                }
                proposed["limits"] = copy.deepcopy(after_limits)
                proposed["limit_amendments"] = [*self.limit_amendments, amendment]
                proposed["historical_accounting_exceptions"] = [*self.historical_accounting_exceptions, dict(receipt)]
                validate_historical_exception_receipt(
                    proposed,
                    receipt,
                    condition_fingerprint=receipt["condition_fingerprint"],
                    plan_fingerprint=receipt["plan_fingerprint"],
                )
                self.limits = ResourceConfig(**after_limits)
                self.limit_amendments.append(copy.deepcopy(amendment))
                self.historical_accounting_exceptions.append(copy.deepcopy(dict(receipt)))
                self.persist()
                return
            if self.historical_accounting_exceptions:
                raise ValueError("the historical exception cannot be replaced or broadened without new authority")
            validate_historical_exception_receipt(
                self.state_dict(),
                receipt,
                condition_fingerprint=receipt["condition_fingerprint"],
                plan_fingerprint=receipt["plan_fingerprint"],
                require_registered=False,
            )
            self.historical_accounting_exceptions.append(copy.deepcopy(dict(receipt)))
            self.persist()

    def activate_historical_exception(
        self,
        exception_id: str,
        *,
        condition_fingerprint: str,
        plan_fingerprint: str,
        scope_id: str,
        execution_guard: Callable[[], bool],
    ) -> None:
        """Create only process-local permission inside the actual guarded recheck."""
        with self._lock:
            from .continuation import validate_historical_exception_receipt

            receipt = next(
                (item for item in self.historical_accounting_exceptions if item["exception_id"] == exception_id), None
            )
            if receipt is None:
                raise AccountingFailure("unknown historical accounting exception")
            try:
                validate_historical_exception_receipt(
                    self.state_dict(),
                    receipt,
                    condition_fingerprint=condition_fingerprint,
                    plan_fingerprint=plan_fingerprint,
                )
            except (ValueError, OSError, KeyError, TypeError) as error:
                raise AccountingFailure("historical accounting exception cannot activate: " + str(error)) from error
            context = {
                "exception_id": exception_id,
                "condition_fingerprint": condition_fingerprint,
                "plan_fingerprint": plan_fingerprint,
                "scope_id": scope_id,
                "execution_guard": execution_guard,
                "activation_entry_count": len(self.entries),
            }
            self._validate_historical_exception_context(context)
            self._historical_exception_context = context

    def clear_historical_exception_context(self) -> None:
        with self._lock:
            self._historical_exception_context = None

    def _validate_historical_exception_context(self, context: Mapping[str, Any]) -> None:
        from .continuation import validate_historical_exception_receipt

        receipt = next(
            (
                item
                for item in self.historical_accounting_exceptions
                if item["exception_id"] == context.get("exception_id")
            ),
            None,
        )
        if receipt is None or self.stage != "compatibility" or self.active_scope != context.get("scope_id"):
            raise AccountingFailure("historical accounting exception is outside its exact recheck context")
        if not callable(context.get("execution_guard")) or context["execution_guard"]() is not True:
            raise AccountingFailure("historical accounting exception requires the active campaign lock")
        scope = self.scopes.get(self.active_scope, {})
        if (
            self.active_scope != "recheck:" + context["plan_fingerprint"]
            or scope.get("stage") != "compatibility"
            or scope.get("max_attempts") != receipt["authorization"]["additional_physical_attempts"]
            or scope.get("budget") != receipt["authorization"]["additional_budget_usd"]
            or scope.get("bindings", {}).get("condition_fingerprint") != context["condition_fingerprint"]
            or scope.get("bindings", {}).get("plan_fingerprint") != context["plan_fingerprint"]
        ):
            raise AccountingFailure("historical accounting exception cannot authorize a different scope or condition")
        try:
            validate_historical_exception_receipt(
                self.state_dict(),
                receipt,
                condition_fingerprint=context["condition_fingerprint"],
                plan_fingerprint=context["plan_fingerprint"],
                allow_owned_inflight=True,
                owned_after=context["activation_entry_count"],
            )
        except (ValueError, OSError, KeyError, TypeError) as error:
            raise AccountingFailure("historical accounting exception is no longer eligible: " + str(error)) from error

    @property
    def blocking_halt(self) -> dict[str, Any] | None:
        with self._lock:
            try:
                self.assert_dispatch_allowed()
            except (AccountingFailure, PolicyStopped) as error:
                return copy.deepcopy(self.halt_events[-1] if self.halt_events else self.halted) or {
                    "reason": str(error),
                    "stage": self.stage,
                    "policy_stop": isinstance(error, PolicyStopped),
                }
            return None

    @property
    def policy_enabled(self) -> bool:
        return self.failure_policy is not None

    def activate_failure_policy(
        self,
        config: FailurePolicyConfig,
        *,
        authorization: Mapping[str, Any],
        bindings: Mapping[str, Any],
        execution_guard: Callable[[], bool],
    ) -> None:
        """Bind explicit audited activation; this never releases a historical halt.

        The caller validates approval/source lineage under its campaign lock.
        Loading state alone cannot recreate the process-local lock capability.
        """
        with self._lock:
            if not callable(execution_guard) or execution_guard() is not True:
                raise AccountingFailure("failure-policy activation requires the active campaign lock")
            candidate = new_policy_state(config, authorization, bindings)
            if self.failure_policy is not None:
                if {k: v for k, v in self.failure_policy.items() if k not in {"events", "historical_adoptions"}} != {
                    k: v for k, v in candidate.items() if k not in {"events", "historical_adoptions"}
                }:
                    raise ValueError("failure-policy activation cannot replace its authorization or condition")
            else:
                self.failure_policy = candidate
            self._failure_policy_guard = execution_guard
            self.persist()

    def adopt_historical_hold(
        self,
        dispatch_id: int,
        *,
        authorization: Mapping[str, Any],
        bindings: Mapping[str, Any],
        execution_guard: Callable[[], bool],
    ) -> None:
        """Explicitly accept only the preserved historical 400 uncertainty.

        This is neither settlement nor a verified upper bound on its missing
        bill. The original halt and every unknown usage field remain untouched.
        """
        from .continuation import EXCEPTED_DISPATCH, EXCEPTED_GENERATION, EXCEPTED_REQUEST, RETAINED_RESERVATION
        from .failure_policy import fingerprint
        from .reconciliation import ACCOUNTING_HALT, generation_identity

        with self._lock:
            if (
                self.failure_policy is None
                or not callable(execution_guard)
                or execution_guard() is not True
                or authorization.get("approved") is not True
                or dict(bindings) != self.failure_policy["bindings"]
            ):
                raise AccountingFailure("historical adoption requires matching explicit activation and campaign lock")
            if dispatch_id != EXCEPTED_DISPATCH or len(self.entries) <= 402:
                raise ValueError("only the exact recorded dispatch 400 can be adopted")
            entry = self.entries[dispatch_id]
            request_hash = hashlib.sha256(
                json.dumps(entry.get("effective_input"), ensure_ascii=False, sort_keys=True).encode()
            ).hexdigest()
            if (
                entry.get("stage") != "pilot"
                or generation_identity(entry) != EXCEPTED_GENERATION
                or request_hash != EXCEPTED_REQUEST
                or entry.get("request_sha256") != EXCEPTED_REQUEST
                or not math.isclose(entry["reserved"], RETAINED_RESERVATION, rel_tol=0, abs_tol=1e-15)
                or self.effective_charge(entry) is not None
                or any(
                    entry.get("usage", {}).get(key) is not None
                    for key in ("input_tokens", "output_tokens", "reference_cost", "total_cost")
                )
            ):
                raise ValueError("historical receipt identity, unknown fields or reservation changed")
            if (
                self.halted is None
                or self.halted.get("reason") != ACCOUNTING_HALT
                or self._unreleased_halt_events()
                or any(self.effective_charge(self.entries[index]) is None for index in (401, 402))
                or {item["dispatch_id"] for item in self.accounting_reconciliations} != {401, 402}
            ):
                raise ValueError("historical adoption cannot cover other halts or incomplete 401/402 settlements")
            existing = self.failure_policy["historical_adoptions"]
            candidate = {
                "dispatch_id": dispatch_id,
                "generation_id": EXCEPTED_GENERATION,
                "request_sha256": EXCEPTED_REQUEST,
                "entry_sha256": fingerprint(entry),
                "reserved": RETAINED_RESERVATION,
                "prior_halt": copy.deepcopy(self.halted),
                "prior_halt_events": copy.deepcopy(self.halt_events),
                "settlements_sha256": fingerprint(
                    [item for item in self.accounting_reconciliations if item["dispatch_id"] in (401, 402)]
                ),
                "authorization": copy.deepcopy(dict(authorization)),
                "authorization_sha256": fingerprint(authorization),
                "bindings": copy.deepcopy(dict(bindings)),
                "decision": "historical accounting unresolved; continuation explicitly authorized with reservation retained",
            }
            if existing:
                if existing != [candidate]:
                    raise ValueError("historical adoption cannot be replaced or broadened")
                return
            if {item["dispatch_id"] for item in self.entries if self.effective_charge(item) is None} != {dispatch_id}:
                raise ValueError("unexpected additional unresolved historical request")
            existing.append(candidate)
            self.persist()

    def _adopted_halt(self) -> bool:
        from .failure_policy import fingerprint

        if self.failure_policy is None or not self.failure_policy.get("historical_adoptions"):
            return False
        if len(self.failure_policy["historical_adoptions"]) != 1:
            raise AccountingFailure("corrupt historical adoption list")
        item = self.failure_policy["historical_adoptions"][0]
        normally_reconciled = (
            self.effective_charge(self.entries[400]) is not None
            and self.halted is None
            and any(record.get("prior_halt") == item.get("prior_halt") for record in self.halt_releases)
        )
        if (
            item.get("dispatch_id") != 400
            or item.get("bindings") != self.failure_policy["bindings"]
            or item.get("authorization", {}).get("approved") is not True
            or item.get("authorization_sha256") != fingerprint(item.get("authorization"))
            or (item.get("prior_halt") != self.halted and not normally_reconciled)
            or item.get("prior_halt_events") != self.halt_events
            or item.get("entry_sha256") != fingerprint(self.entries[400])
            or item.get("settlements_sha256")
            != fingerprint(
                [record for record in self.accounting_reconciliations if record["dispatch_id"] in (401, 402)]
            )
        ):
            raise AccountingFailure("historical uncertainty adoption no longer matches its exact evidence")
        return not normally_reconciled

    def _policy_event(self, event: str, **fields: Any) -> None:
        assert self.failure_policy is not None
        self.failure_policy["events"].append(
            {
                "event_id": len(self.failure_policy["events"]),
                "event": event,
                "after_entries": len(self.entries),
                **copy.deepcopy(fields),
            }
        )
        self.persist()

    def _policy_drain(self, reason: str) -> None:
        assert self.failure_policy is not None
        if not any(item["event"] == "drain" for item in self.failure_policy["events"]):
            self._policy_event("drain", reason=reason)

    def uncertainty_summary(self) -> dict[str, Any]:
        """Unknown native usage counts once; known debits never count as holds."""
        with self._lock:
            inflight, terminal, held = [], [], 0.0
            for entry in self.entries:
                charged = self.effective_charge(entry)
                if charged is None:
                    held += entry["reserved"]
                if entry["status"] in {"reserved", "dispatched"} and not self._has_recovery(entry["dispatch_id"]):
                    inflight.append(entry["dispatch_id"])
                elif charged is None or self.effective_usage(entry).get("accounting_complete") is False:
                    terminal.append(entry["dispatch_id"])
            return {
                "inflight_ids": inflight,
                "terminal_unresolved_ids": terminal,
                "retained_reservations": held,
                "reserved_slots": len(inflight) + len(terminal),
            }

    def _has_recovery(self, dispatch_id: int) -> bool:
        return self.failure_policy is not None and any(
            item["event"] == "recovery-disposition" and item["dispatch_id"] == dispatch_id
            for item in self.failure_policy["events"]
        )

    def record_policy_recovery(
        self,
        dispatch_id: int,
        *,
        disposition: str,
        evidence: Mapping[str, Any],
        execution_guard: Callable[[], bool],
    ) -> None:
        """Audited terminal disposition, never a settlement or permission to replay."""
        with self._lock:
            if (
                self.failure_policy is None
                or not callable(execution_guard)
                or execution_guard() is not True
                or not evidence
                or disposition not in {"terminal-unresolved", "accounting-resolved"}
                or isinstance(dispatch_id, bool)
                or not isinstance(dispatch_id, int)
                or not 0 <= dispatch_id < len(self.entries)
            ):
                raise ValueError("request recovery needs explicit disposition, evidence and campaign lock")
            entry = self.entries[dispatch_id]
            if (disposition == "accounting-resolved") != (self.effective_charge(entry) is not None):
                raise ValueError("recovery disposition disagrees with authoritative accounting state")
            prior = next(
                (
                    item
                    for item in self.failure_policy["events"]
                    if item["event"] == "recovery-disposition" and item["dispatch_id"] == dispatch_id
                ),
                None,
            )
            if prior is not None:
                if prior["disposition"] != disposition or prior["evidence"] != dict(evidence):
                    raise ValueError("recorded recovery disposition cannot be replaced")
                return
            self._policy_event(
                "recovery-disposition", dispatch_id=dispatch_id, disposition=disposition, evidence=dict(evidence)
            )
            self._check_policy_thresholds()

    def observe_accounting(self, entry: dict[str, Any], event: str, details: Mapping[str, Any]) -> Any:
        """Persist final-frame progress and GET intents before bounded recovery I/O."""
        with self._lock:
            events = entry.setdefault("recovery_events", [])
            if event == "metadata-lookup-intent":
                generation = details.get("generation_id")
                if not isinstance(generation, str) or not generation:
                    raise AccountingFailure("metadata recovery needs its original generation identity")
                prior = [item for item in events if item["event"] == "metadata-lookup-intent"]
                if any(item["details"].get("generation_id") != generation for item in prior):
                    raise AccountingFailure("metadata recovery changed generation identity")
                if len(prior) >= 3:
                    return False
            from .persistence import INVARIANT_FIELDS
            isolated = {}
            for key, value in details.items():
                identity = (entry["dispatch_id"], key)
                if key in INVARIANT_FIELDS:
                    if identity not in self._observation_invariants or self._observation_invariants[identity] != value:
                        self._observation_invariants[identity] = copy.deepcopy(value)
                    isolated[key] = self._observation_invariants[identity]
                else:
                    isolated[key] = copy.deepcopy(value)
            record = {"event_id": len(events), "event": event, "details": isolated}
            events.append(record)
            if self.durable_journal is None:
                self.persist()
                return True
        async def durable_observation() -> bool:
            try:
                await self.durable_journal.observation(entry["dispatch_id"], record)
            except Exception as error:
                raise AccountingFailure("accounting observation persistence failed; dispatch stopped") from error
            return True
        return durable_observation()

    def _check_policy_thresholds(self) -> None:
        if self.failure_policy is None:
            return
        summary = self.uncertainty_summary()
        config = FailurePolicyConfig(**self.failure_policy["config"])
        terminal_holds = sum(
            entry["reserved"]
            for entry in self.entries
            if entry["dispatch_id"] in summary["terminal_unresolved_ids"] and self.effective_charge(entry) is None
        )
        if len(summary["terminal_unresolved_ids"]) >= config.max_unresolved_requests:
            self._policy_drain("terminal unresolved physical-request limit reached")
        elif terminal_holds >= config.max_unresolved_reserved_usd - 1e-12:
            self._policy_drain("terminal unresolved reservation limit reached")
        elif any(
            value >= config.max_consecutive_provider_failures
            for value in logical_summary(self.failure_policy)["backend_streaks"].values()
        ):
            self._policy_drain("consecutive logical provider-failure limit reached")

    def begin_logical_request(self, backend: str, identity: Mapping[str, Any] | None = None) -> int | None:
        with self._lock:
            if self.failure_policy is None:
                return None
            self.assert_dispatch_allowed()
            token = sum(item["event"] == "logical-start" for item in self.failure_policy["events"])
            self._policy_event(
                "logical-start", logical_request_id=token, backend=backend, identity=dict(identity or {})
            )
            return token

    def finish_logical_request(self, token: int | None) -> None:
        with self._lock:
            if token is None or self.failure_policy is None:
                return
            if any(
                item["event"] == "logical-finish" and item["logical_request_id"] == token
                for item in self.failure_policy["events"]
            ):
                return
            if not any(
                item["event"] == "logical-start" and item["logical_request_id"] == token
                for item in self.failure_policy["events"]
            ):
                raise AccountingFailure("unknown logical provider request")
            entries = [item for item in self.entries if item.get("provider_logical_request_id") == token]
            if any(
                item["status"] in {"reserved", "dispatched"} and not self._has_recovery(item["dispatch_id"])
                for item in entries
            ):
                # An interrupted physical dispatch is not automatically replayed
                # or classified as a known zero; it requires explicit recovery.
                self._policy_drain("interrupted physical dispatch requires reconciliation")
                return
            outcome = "not-dispatched"
            dispatched = [item for item in entries if item.get("usage", {}).get("model_calls", 1) != 0]
            if dispatched:
                usage = dispatched[-1].get("usage", {})
                kind = usage.get("failure_kind")
                outcome = (
                    "provider-completed"
                    if usage.get("provider_completed") is True
                    else "provider-failed" if kind in {item.value for item in PROVIDER_FAILURES} else "other-failure"
                )
            self._policy_event("logical-finish", logical_request_id=token, outcome=outcome)
            self._check_policy_thresholds()

    def effective_charge(self, entry: Mapping[str, Any]) -> float | None:
        """Read an audited settlement without rewriting a historical receipt."""
        with self._lock:
            for record in self.accounting_reconciliations:
                if record["dispatch_id"] == entry["dispatch_id"]:
                    return float(record["usage"]["total_cost"])
            return entry["charged"]

    def effective_usage(self, entry: Mapping[str, Any]) -> dict[str, Any]:
        """Settlement overlays accounting only; original observations stay frozen."""
        with self._lock:
            usage = copy.deepcopy(entry.get("usage", {}))
            for record in self.accounting_reconciliations:
                if record["dispatch_id"] == entry["dispatch_id"]:
                    usage.update(record["usage"])
                    required = {"input_tokens", "output_tokens", "reference_cost", "total_cost"}
                    if entry.get("billing_tariff", {}).get("require_native_accounting"):
                        required.update({"reasoning_tokens", "cache_tokens", "is_byok"})
                    usage["missing_usage"] = sorted(key for key in required if usage.get(key) is None)
                    usage["accounting_complete"] = not usage["missing_usage"]
            return usage

    def register_scope(
        self,
        scope_id: str,
        *,
        stage: str = "compatibility",
        max_attempts: int = 6,
        budget: float = 0.25,
        bindings: Mapping[str, Any] | None = None,
    ) -> None:
        """Freeze an additional pass ceiling; registration never resets spend."""
        with self._lock:
            if not isinstance(scope_id, str) or not scope_id.strip() or stage != "compatibility":
                raise ValueError("a recheck scope requires a named compatibility pass")
            if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or not 1 <= max_attempts <= 24:
                raise ValueError("a recheck scope permits at most 24 physical attempts")
            if isinstance(budget, bool) or not isinstance(budget, (int, float)) or not 0 < budget <= 0.5:
                raise ValueError("a recheck scope permits at most USD 0.50")
            scope = {
                "stage": stage,
                "max_attempts": max_attempts,
                "budget": budget,
                "bindings": copy.deepcopy(dict(bindings or {})),
            }
            if scope_id in self.scopes and self.scopes[scope_id] != scope:
                raise ValueError("an expenditure scope cannot change after registration")
            self._validate_extended_scope(scope_id, scope)
            self.scopes[scope_id] = scope
            self.persist()

    def _validate_extended_scope(self, scope_id: str, scope: Mapping[str, Any]) -> None:
        if scope["max_attempts"] <= 6 and scope["budget"] <= 0.25:
            return
        from .continuation import EXTENSION_PROTOCOL

        receipt = next(
            (
                item
                for item in self.historical_accounting_exceptions
                if item["scope_id"] == scope_id and item.get("protocol") == EXTENSION_PROTOCOL
            ),
            None,
        )
        if (
            receipt is None
            or scope["max_attempts"] != receipt["authorization"]["additional_physical_attempts"]
            or scope["budget"] != receipt["authorization"]["additional_budget_usd"]
            or scope["bindings"].get("condition_fingerprint") != receipt["condition_fingerprint"]
            or scope["bindings"].get("plan_fingerprint") != receipt["plan_fingerprint"]
        ):
            raise ValueError("expanded scope requires its exact audited successive exception")

    def activate_scope(self, scope_id: str | None) -> None:
        with self._lock:
            if scope_id is not None and scope_id not in self.scopes:
                raise ValueError("unknown expenditure scope")
            if self.active_scope == scope_id:
                return
            self.scope_activations.append({"scope_id": scope_id, "after_entries": len(self.entries)})
            self.active_scope = scope_id
            self.persist()

    def scope_summary(self, scope_id: str) -> dict[str, Any]:
        with self._lock:
            scope = self.scopes[scope_id]
            entries = [item for item in self.entries if item.get("scope_id") == scope_id]
            paid = sum(self.effective_charge(item) or 0.0 for item in entries)
            unresolved = sum(item["reserved"] for item in entries if self.effective_charge(item) is None)
            return {
                "scope_id": scope_id,
                **copy.deepcopy(scope),
                "paid": paid,
                "unresolved": unresolved,
                "occupied": paid + unresolved,
                "attempts": len(entries),
            }

    def record_reconciliation(self, record: Mapping[str, Any]) -> None:
        """Append authenticated metadata evidence; historical request fields stay intact."""
        from .reconciliation import validate_accounting_history

        with self._lock:
            existing = next(
                (item for item in self.accounting_reconciliations if item["dispatch_id"] == record["dispatch_id"]), None
            )
            if existing is not None:
                if existing != record:
                    raise ValueError("a reconciled dispatch cannot be settled twice")
                return
            proposed = self.state_dict()
            proposed["accounting_reconciliations"] = [*self.accounting_reconciliations, copy.deepcopy(dict(record))]
            validate_accounting_history(proposed)
            self.accounting_reconciliations.append(copy.deepcopy(dict(record)))
            self.persist()

    def release_reconciled_halt(self, release: Mapping[str, Any]) -> None:
        """Only a complete, explicitly dispositioned accounting audit releases a halt."""
        from .reconciliation import validate_accounting_history

        with self._lock:
            if release in self.halt_releases:
                return
            if release.get("prior_halt") != self.halted or self.halted is None:
                raise ValueError("the release does not identify the current halt")
            if self.halt_events:
                source = self._accounting_source_halt_events(release.get("source_ledger_sha256"))
                if source != self.halt_events:
                    raise AccountingFailure("a historical accounting release cannot mask a new fatal halt event")
            proposed = self.state_dict()
            proposed["halt_releases"] = [*self.halt_releases, copy.deepcopy(dict(release))]
            validate_accounting_history(proposed)
            self.halt_releases.append(copy.deepcopy(dict(release)))
            self.halted = None
            self.persist()

    def _accounting_source_halt_events(self, source_hash: str | None) -> list[dict[str, Any]]:
        if self.reconciliation_audit is None:
            return []
        path = Path(self.reconciliation_audit["audit_directory"]) / "source_ledger.json"
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != source_hash:
            raise ValueError("halt release original accounting source changed")
        return json.loads(raw).get("halt_events", [])

    def _unreleased_halt_events(self) -> list[dict[str, Any]]:
        released = 0
        for release in self.halt_releases:
            source = self._accounting_source_halt_events(release.get("source_ledger_sha256"))
            if self.halt_events[: len(source)] == source:
                released = max(released, len(source))
        return self.halt_events[released:]

    def halt(self, reason: str) -> None:
        """An accounting/binding violation is sticky across checkpoints and stages."""
        with self._lock:
            self.halt_events.append(
                {
                    "event_id": len(self.halt_events),
                    "reason": reason,
                    "stage": self.stage,
                    "after_entries": len(self.entries),
                }
            )
            if self.halted is None:
                self.halted = {"reason": reason, "stage": self.stage, "after_entries": len(self.entries)}
            self.persist()

    def assert_dispatch_allowed(self) -> None:
        with self._lock:
            if self.durable_journal is not None:
                try:
                    self.durable_journal.assert_healthy()
                except Exception as error:
                    raise AccountingFailure("accounting persistence unavailable; dispatch stopped") from error
            if self.campaign_binding:
                from .campaigns import assert_registered_dispatch

                try:
                    assert_registered_dispatch(self)
                except (ValueError, KeyError, OSError) as error:
                    raise AccountingFailure(str(error)) from error
            events = self._unreleased_halt_events()
            if events:
                raise AccountingFailure("campaign dispatch halted by new fatal event: " + events[-1]["reason"])
            if self.failure_policy is not None:
                if not callable(self._failure_policy_guard) or self._failure_policy_guard() is not True:
                    raise AccountingFailure(
                        "failure-policy dispatch requires explicit activation under the campaign lock"
                    )
                # A hard historical halt is examined before operational drain.
                adopted = self._adopted_halt()
                if self.halted is not None and not adopted:
                    raise AccountingFailure("campaign dispatch halted: " + self.halted["reason"])
                self._check_policy_thresholds()
                if any(not self._has_recovery(index) for index in self._recovered_inflight):
                    raise PolicyStopped("interrupted physical dispatch requires an explicit recovery disposition")
                drain = next((item for item in self.failure_policy["events"] if item["event"] == "drain"), None)
                if drain is not None:
                    raise PolicyStopped(drain["reason"])
                return
            if self.historical_accounting_exceptions:
                if self.halted is None and self.effective_charge(self.entries[400]) is not None:
                    return
                if self._historical_exception_context is None:
                    raise AccountingFailure(
                        "historical accounting exception requires an explicit scoped execution context"
                    )
                self._validate_historical_exception_context(self._historical_exception_context)
                return
            if self.halted is not None:
                raise AccountingFailure("campaign dispatch halted: " + self.halted["reason"])

    @property
    def dispatch_stop_reason(self) -> dict[str, Any] | None:
        try:
            self.assert_dispatch_allowed()
        except (AccountingFailure, PolicyStopped) as error:
            return {
                "reason": str(error),
                "operational_drain": isinstance(error, PolicyStopped),
                "hard_failure": isinstance(error, AccountingFailure),
            }
        return None

    def stage_summary(self, stage: str) -> dict[str, Any]:
        with self._lock:
            entries = [item for item in self.entries if item["stage"] == stage]
            paid = sum(self.effective_charge(item) or 0.0 for item in entries)
            unresolved = sum(item["reserved"] for item in entries if self.effective_charge(item) is None)
            return {
                "stage": stage,
                "paid": paid,
                "unresolved": unresolved,
                "occupied": paid + unresolved,
                "attempts": len(entries),
                "budget": getattr(self.limits, f"{stage}_budget", None),
                "unresolved_dispatch_ids": [
                    item["dispatch_id"] for item in entries if self.effective_charge(item) is None
                ],
            }

    def reserve(self, maximum: float, *, kind: str, model: str, metadata: Mapping[str, Any]) -> dict[str, Any]:
        with self._lock:
            self.assert_dispatch_allowed()
            if isinstance(maximum, bool) or not math.isfinite(maximum) or maximum < 0:
                raise ValueError("a physical reservation must be finite and nonnegative")
            if self.failure_policy is not None:
                summary = self.uncertainty_summary()
                policy = FailurePolicyConfig(**self.failure_policy["config"])
                if (
                    summary["reserved_slots"] + 1 > policy.max_unresolved_requests
                    or summary["retained_reservations"] + maximum > policy.max_unresolved_reserved_usd + 1e-12
                ):
                    terminal_holds = sum(
                        entry["reserved"]
                        for entry in self.entries
                        if entry["dispatch_id"] in summary["terminal_unresolved_ids"]
                        and self.effective_charge(entry) is None
                    )
                    # Wait only when completion of existing physical requests
                    # could make admission feasible. Terminal uncertainty stays
                    # reserved; waiting cannot erase it or replenish a limit.
                    if (
                        summary["inflight_ids"]
                        and len(summary["terminal_unresolved_ids"]) + 1 <= policy.max_unresolved_requests
                        and terminal_holds + maximum <= policy.max_unresolved_reserved_usd + 1e-12
                    ):
                        raise UncertaintyCapacity("in-flight uncertainty slots or monetary allowance are occupied")
                    self._policy_drain("next request cannot fit the remaining uncertainty allowance")
                    raise PolicyStopped("next request cannot fit the remaining uncertainty allowance")
            budget = getattr(self.limits, f"{self.stage}_budget", None)
            if (
                self.stage == "compatibility"
                and self.stage_summary(self.stage)["attempts"] >= self.limits.compatibility_max_attempts
            ):
                raise BudgetExhausted("compatibility physical generation-attempt ceiling reached")
            if budget is None or self.limits.currency is None:
                raise BudgetExhausted(f"finite {self.stage} monetary budget and currency are not configured")
            occupied = sum(
                self.effective_charge(item) if self.effective_charge(item) is not None else item["reserved"]
                for item in self.entries
                if item["stage"] == self.stage
            )
            if occupied + maximum > budget:
                raise BudgetExhausted(f"{self.stage} monetary budget cannot reserve this request")
            scope_id = self.active_scope
            if scope_id is not None:
                scope = self.scope_summary(scope_id)
                if scope["stage"] != self.stage:
                    raise BudgetExhausted("active expenditure scope belongs to a different stage")
                if scope["attempts"] >= scope["max_attempts"] or scope["occupied"] + maximum > scope["budget"]:
                    raise BudgetExhausted("compatibility recheck scope attempt or monetary ceiling reached")
            if any(name in metadata for name in ("dispatch_id", "stage", "charged", "reserved", "scope_id", "status")):
                raise ValueError("request metadata cannot override ledger accounting identity")
            entry = {
                "dispatch_id": len(self.entries),
                "stage": self.stage,
                "kind": kind,
                "model": model,
                "category": (
                    "initialization"
                    if kind == "initial"
                    else ("textgrad" if kind.startswith("textgrad") else "collaboration")
                ),
                "reserved": maximum,
                "charged": None,
                "status": "reserved",
                **dict(metadata),
            }
            if scope_id is not None:
                entry["scope_id"] = scope_id
            if self._historical_exception_context is not None:
                entry["historical_exception_id"] = self._historical_exception_context["exception_id"]
            self.entries.append(entry)
            self.persist(entry)  # The asynchronous durable barrier precedes physical I/O.
            return entry

    def settle(self, entry: dict[str, Any], metadata: Mapping[str, Any], *, status: str, elapsed: float) -> None:
        with self._lock:
            paid = metadata.get("total_cost")
            if paid is not None and (isinstance(paid, bool) or not math.isfinite(paid) or paid < 0):
                self.halt("invalid actual charge; reservation unresolved")
                raise AccountingFailure("invalid actual charge; reservation remains unresolved")
            entry.update(
                status=status, usage=dict(metadata), charged=metadata.get("total_cost"), elapsed_seconds=elapsed
            )
            self.persist(entry)
            if paid is None:
                if self.failure_policy is None:
                    self.halt("generation charge unresolved; retain reservation and review before dispatch")
            if entry["charged"] is not None and entry["charged"] > entry["reserved"] + 1e-12:
                entry["status"] = "tariff-or-usage-exceeded-reservation"
                self.halt("actual charge exceeded reservation")
                self.persist(entry)
                raise AccountingFailure("actual charge exceeded declared maximum; review the resource profile")
            self._check_policy_thresholds()


@dataclass(frozen=True)
class ResourceResponse:
    response: Any
    metadata: Mapping[str, Any]
    attempts: tuple[Mapping[str, Any], ...]


class ResourceController:
    """Final composed-request admission, finite retries and physical accounting."""

    def __init__(
        self,
        profile: BackendResourceProfile,
        counter: TokenCounter,
        ledger: ExpenditureLedger,
        *,
        limits: ResourceConfig,
        failure_policy: FailurePolicyConfig | None = None,
    ) -> None:
        self.profile, self.counter, self.ledger, self.limits = profile, counter, ledger, limits
        self.expected_failure_policy = failure_policy
        if profile.currency != limits.currency:
            raise ValueError("backend tariff and expenditure budget currencies differ")

    def preflight(self, messages: Sequence[Mapping[str, str]], kind: str, *, schema: Any = None) -> tuple[int, int]:
        if schema is not None and kind != "role":
            raise ValueError("native role schemas must not be applied to other call types")
        count = self.counter.count_input(messages, schema=schema)
        ceiling, output = self.limits.caps(kind)
        if count < 0 or count > ceiling:
            raise ResourceFailure(f"input-token-limit: {count} > {ceiling}")
        if self.profile.reasoning_in_output_budget:
            output = int(self.profile.reasoning_allowance or 0)
        if not self.profile.min_output_tokens <= output <= self.profile.max_output_tokens:
            raise ResourceFailure("output-token-cap is outside the backend-supported range")
        if count + output + self.limits.context_reserve > self.profile.context_limit:
            raise ResourceFailure("backend-context-limit")
        return count, output

    def preflight_envelope(
        self, variants: Sequence[Sequence[Mapping[str, str]]], kind: str, *, schema: Any = None
    ) -> tuple[int, int]:
        """Admit every complete original/repair request before any generation.

        Count complete compositions independently: token counts are not additive
        across text boundaries, chat serializations, or different backends.
        This check makes no reservation and never changes physical expenditure.
        """

        if not variants:
            raise ValueError("request admission needs at least the original complete input")
        try:
            counts = [self.preflight(messages, kind, schema=schema) for messages in variants]
        except ResourceFailure as error:
            raise ResourceFailure("repair-envelope: " + str(error), error.usage_record) from error
        return max(count for count, _ in counts), counts[0][1]

    def _usage(self, response: Any, input_count: int, *, complete: bool) -> dict[str, Any]:
        from .adapter import usage_to_dict

        result = (
            usage_to_dict(getattr(response, "usage_metadata", response if isinstance(response, Mapping) else None))
            or {}
        )
        if getattr(response, "sdk_usage_limited", False):
            # The installed public SDK materializes absent provider coordinates
            # as zero. A nonempty request cannot have zero input tokens, and
            # default monetary zero is not a free provider call. Retain the raw
            # SDK view, then count/derive or mark unresolved rather than relabel it.
            result["raw_sdk_usage"] = dict(result)
            result["sdk_usage_snapshots"] = copy.deepcopy(getattr(response, "sdk_usage_snapshots", ()))
            if result.get("input_tokens") == 0 and input_count > 0:
                result["input_tokens"] = None
            if result.get("output_tokens") == 0 and str(getattr(response, "content", "") or ""):
                result["output_tokens"] = None
            if result.get("total_cost") == 0:
                result["total_cost"] = None
            if self.profile.cached_input_price is not None and result.get("cache_tokens") == 0:
                result["cache_tokens"] = None
        for target, aliases in {
            "input_tokens": ("prompt_tokens",),
            "output_tokens": ("completion_tokens",),
            "cache_tokens": ("cached_input_tokens",),
            "reasoning_tokens": ("internal_reasoning_tokens",),
        }.items():
            for alias in aliases:
                if result.get(target) is None and result.get(alias) is not None:
                    result[target] = result[alias]
        methods = {
            name: "provider" for name in ("input_tokens", "output_tokens", "total_cost") if result.get(name) is not None
        }
        if complete and not self.profile.require_native_accounting:
            if result.get("input_tokens") is None:
                result["input_tokens"] = input_count
                methods["input_tokens"] = self.profile.counting_method
            if result.get("output_tokens") is None and not self.profile.reasoning_in_output_budget:
                result["output_tokens"] = self.counter.count_output(str(getattr(response, "content", "") or ""))
                methods["output_tokens"] = self.profile.counting_method
        required = ["input_tokens", "output_tokens"]
        if self.profile.cached_input_price is not None:
            required.append("cache_tokens")
        if self.profile.cache_write_price is not None:
            required.append("cache_write_tokens")
        if self.profile.reasoning_price is not None:
            required.append("reasoning_tokens")
        for part, total in (
            ("cache_tokens", "input_tokens"),
            ("cache_write_tokens", "input_tokens"),
            ("reasoning_tokens", "output_tokens"),
        ):
            if result.get(part) is not None and result.get(total) is not None and result[part] > result[total]:
                raise AccountingFailure("usage subcategory exceeds its inclusive token total")
        if (
            result.get("total_cost") is None
            and (not self.profile.require_native_accounting or self.profile.billing_mode == "credits")
            and all(result.get(key) is not None for key in required)
            and not result.get("cache_write_tokens")
        ):
            cached = result.get("cache_tokens", 0) or 0
            reasoning = result.get("reasoning_tokens", 0) or 0
            # Cached/reasoning counts are subsets of input/output totals in this
            # profile. A different billing convention requires a new adapter.
            result["total_cost"] = (
                (result["input_tokens"] - cached) * self.profile.input_price
                + cached
                * (
                    self.profile.cached_input_price
                    if self.profile.cached_input_price is not None
                    else self.profile.input_price
                )
                + (result["output_tokens"] - reasoning) * self.profile.output_price
                + reasoning
                * (
                    self.profile.reasoning_price
                    if self.profile.reasoning_price is not None
                    else self.profile.output_price
                )
            )
            result["total_cost"] = result["total_cost"] * self.profile.billing_fee_multiplier + self.profile.request_fee
            methods["total_cost"] = "derived"
            if any(methods.get(key) == "estimated" for key in ("input_tokens", "output_tokens")):
                result["estimated_paid_cost"] = result.pop("total_cost")
                methods["estimated_paid_cost"] = methods.pop("total_cost")
        # Scientific reference pricing uses inclusive input/output counts once,
        # with no cache discount, promotional credit, fee or run-time repricing.
        result["reference_cost"] = None
        if (
            self.profile.reference_input_price is not None
            and result.get("input_tokens") is not None
            and result.get("output_tokens") is not None
        ):
            result["reference_cost"] = (
                result["input_tokens"] * self.profile.reference_input_price
                + result["output_tokens"] * self.profile.reference_output_price
            )
            methods["reference_cost"] = (
                "estimated"
                if any(methods.get(key) == "estimated" for key in ("input_tokens", "output_tokens"))
                else "reference-tariff"
            )
        result["reference_tariff"] = {
            "input_per_token": self.profile.reference_input_price,
            "output_per_token": self.profile.reference_output_price,
            "date": self.profile.reference_pricing_date,
            "currency": self.profile.currency,
        }
        for key in (
            "input_tokens",
            "output_tokens",
            "cache_tokens",
            "cache_write_tokens",
            "reasoning_tokens",
            "total_cost",
            "reference_cost",
        ):
            value = result.get(key)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0
            ):
                raise AccountingFailure(f"invalid available usage coordinate: {key}")
        result["accounting_methods"] = methods
        result["missing_usage"] = [
            key for key in ("input_tokens", "output_tokens", "reference_cost", "total_cost") if result.get(key) is None
        ]
        return result

    def reservation_maximum(self, input_count: int, output_cap: int, kind: str) -> float:
        """The unchanged tariff-based worst-case reservation used by dispatch."""
        reserved_input = self.limits.caps(kind)[0] if self.profile.counting_method == "estimated" else input_count
        maximum = reserved_input * max(
            self.profile.input_price, self.profile.cached_input_price or 0.0, self.profile.cache_write_price or 0.0
        )
        maximum += output_cap * max(self.profile.output_price, self.profile.reasoning_price or 0.0)
        maximum = maximum * self.profile.billing_fee_multiplier + self.profile.request_fee
        return maximum * self.profile.reservation_safety_factor

    def _accounting_status(self, metadata: dict[str, Any], *, confirmed_not_generated: bool = False) -> None:
        missing = list(metadata.get("missing_usage", []))
        if self.profile.require_native_accounting and not confirmed_not_generated:
            missing.extend(key for key in ("reasoning_tokens", "cache_tokens", "is_byok") if metadata.get(key) is None)
        metadata["missing_usage"] = list(dict.fromkeys(missing))
        metadata["accounting_complete"] = confirmed_not_generated or not missing

    def _known_bound_violation(self, metadata: Mapping[str, Any], input_count: int, output_cap: int, kind: str) -> str | None:
        """Apply positive native-count evidence equally to success and failure.

        Missing usage is uncertainty, not proof that a bound was exceeded.
        """
        native_input = metadata.get("input_tokens")
        if (
            self.profile.counting_method == "estimated"
            and metadata.get("accounting_methods", {}).get("input_tokens") == "provider"
            and native_input is not None and native_input > input_count
        ):
            return "native input exceeded conservative estimate"
        if native_input is not None and native_input > self.limits.caps(kind)[0]:
            return "provider input count exceeded declared input-token-limit"
        if metadata.get("output_tokens") is not None and metadata["output_tokens"] > output_cap:
            return "provider output count exceeded declared output-token-limit"
        return None

    async def invoke(
        self,
        model: Any,
        *,
        messages: Sequence[Mapping[str, str]],
        kind: str,
        deadline: float,
        settings: Mapping[str, Any],
        output_parser: Any = None,
        logical_request_id: int | None = None,
    ) -> ResourceResponse:
        raise_if_stopping()
        self.assert_policy_config()
        self.ledger.assert_dispatch_allowed()
        owned = logical_request_id is None
        if owned:
            logical_request_id = self.ledger.begin_logical_request(self.backend_identity(model), {"kind": kind})
        timing_token = CALL_TIMING.set({"phase": "admission", "provider_completed": False})
        try:
            return await self._invoke(
                model,
                messages=messages,
                kind=kind,
                deadline=deadline,
                settings=settings,
                output_parser=output_parser,
                logical_request_id=logical_request_id,
            )
        except asyncio.CancelledError as error:
            # Admission/capacity waiting can be cancelled before _invoke has a
            # reservation or physical receipt. It is not a generation attempt.
            if getattr(error, "usage_record", None) is None:
                setattr(error, "usage_record", UsageRecord(raw_metadata={
                    "not_dispatched": True, "cancellation_cause": tag_cancellation(error),
                    "timing_phase": "admission",
                }))
            raise
        finally:
            try:
                if owned:
                    self.ledger.finish_logical_request(logical_request_id)
                await self.ledger.flush_durable()
            finally:
                CALL_TIMING.reset(timing_token)

    def assert_policy_config(self) -> None:
        """Reject mismatched conditions before even registering a logical call."""
        expected = self.expected_failure_policy
        if expected is not None:
            actual = self.ledger.failure_policy
            if (expected.enabled and (actual is None or actual["config"] != asdict(expected))) or (
                not expected.enabled and actual is not None
            ):
                raise AccountingFailure("configured failure policy differs from the explicitly activated ledger")

    def backend_identity(self, model: Any) -> str:
        client = getattr(model, "model_client_config", None)
        binding = getattr(client, "openrouter_binding", None)
        if isinstance(binding, Mapping):
            return str(binding.get("model")) + "@" + str(binding.get("provider_endpoint"))
        config = getattr(model, "model_config", None)
        return str(getattr(config, "model", None) or getattr(config, "model_name", None) or self.profile.tokenizer_name)

    async def _invoke(
        self,
        model: Any,
        *,
        messages: Sequence[Mapping[str, str]],
        kind: str,
        deadline: float,
        settings: Mapping[str, Any],
        output_parser: Any,
        logical_request_id: int | None,
    ) -> ResourceResponse:
        input_count, output_cap = self.preflight(messages, kind, schema=settings.get("response_format"))
        reserved_input = self.limits.caps(kind)[0] if self.profile.counting_method == "estimated" else input_count
        maximum = self.reservation_maximum(input_count, output_cap, kind)
        model_name = str(
            settings.get("model")
            or getattr(getattr(model, "model_config", None), "model", None)
            or getattr(getattr(model, "model_config", None), "model_name", "configured-model")
        )
        physical: list[Mapping[str, Any]] = []
        for retry in range(self.limits.transport_retries + 1):
            raise_if_stopping()
            if time.monotonic() >= deadline:
                raise ResourceFailure("logical-call-deadline", _physical_usage(physical))
            reservation_metadata = {
                "input_count": input_count,
                "input_count_method": self.profile.counting_method,
                "output_cap": output_cap,
                "reserved_input_tokens": reserved_input,
                "reservation_safety_factor": self.profile.reservation_safety_factor,
                "billing_tariff": asdict(self.profile),
                "transport_attempt": retry,
                "seed": settings.get("seed"),
                "decoding_arguments": dict(settings),
                "request_sha256": hashlib.sha256(
                    json.dumps(list(messages), ensure_ascii=False, sort_keys=True).encode()
                ).hexdigest(),
                "effective_input": copy.deepcopy(list(messages)),
                "native_schema": copy.deepcopy(settings.get("response_format")),
                "complete_request_sha256": hashlib.sha256(
                    json.dumps(
                        {"messages": list(messages), "response_format": settings.get("response_format")},
                        ensure_ascii=False,
                        sort_keys=True,
                    ).encode()
                ).hexdigest(),
            }
            if logical_request_id is not None:
                reservation_metadata["provider_logical_request_id"] = logical_request_id
            while True:
                raise_if_stopping()
                if time.monotonic() >= deadline:
                    raise ResourceFailure("logical-call-deadline", _physical_usage(physical))
                try:
                    entry = self.ledger.reserve(maximum, kind=kind, model=model_name, metadata=reservation_metadata)
                    break
                except UncertaintyCapacity:
                    if time.monotonic() + 0.01 >= deadline:
                        raise ResourceFailure("uncertainty-capacity admission deadline", _physical_usage(physical))
                    await asyncio.sleep(0.01)
            started = time.monotonic()
            dispatched = False
            metadata: dict[str, Any] = {}
            response: Any = None
            lease = {"acquired": False, "finished": False, "release_requested": False, "released": False}

            def confirm_dispatch() -> None:
                nonlocal dispatched
                # No await separates this final check from marking the physical
                # operation. For the audited client this runs at its POST edge,
                # after SDK/client setup, not merely before invoking the SDK.
                self.ledger.assert_dispatch_allowed()
                raise_if_stopping()
                if time.monotonic() >= deadline:
                    raise ResourceFailure("logical-call-deadline")
                dispatched = True
                timing = CALL_TIMING.get()
                if timing is not None:
                    timing["phase"] = "generation"

            def release_lease() -> None:
                # Accounting is part of the guarded physical operation. Keep a
                # queued request from overtaking a fatal receipt decision. A
                # cancellation-suppressing backend retains its permit until it
                # really exits, even after the caller has timed out.
                if lease["acquired"] and lease["finished"] and lease["release_requested"] and not lease["released"]:
                    lease["released"] = True
                    self.ledger.semaphore.release()

            try:

                async def dispatch() -> Any:
                    await self.ledger.semaphore.acquire()
                    lease["acquired"] = True
                    try:
                        self.ledger.assert_dispatch_allowed()
                        raise_if_stopping()
                        if time.monotonic() >= deadline:
                            raise ResourceFailure("logical-call-deadline")
                        entry["status"] = "dispatched"
                        self.ledger.persist(entry)
                        await self.ledger.flush_durable()
                        self.ledger.assert_dispatch_allowed()
                        raise_if_stopping()
                        if time.monotonic() >= deadline:
                            raise ResourceFailure("logical-call-deadline")
                        client_config = getattr(model, "model_client_config", None)
                        audited_gateway = getattr(client_config, "client_provider", None) == "AdaptiveOpenRouter"
                        if not audited_gateway:
                            confirm_dispatch()
                        dispatch_token = GENERATION_DISPATCH_GATE.set(confirm_dispatch if audited_gateway else None)
                        observer_token = ACCOUNTING_OBSERVER.set(
                            (lambda event, details: self.ledger.observe_accounting(entry, event, details))
                            if self.ledger.policy_enabled or self.ledger.durable_journal is not None
                            else None
                        )
                        try:
                            return await invoke_model(
                                model,
                                messages=list(messages),
                                output_parser=output_parser,
                                timeout=max(0.001, deadline - time.monotonic()),
                                max_tokens=output_cap,
                                **dict(settings),
                            )
                        finally:
                            ACCOUNTING_OBSERVER.reset(observer_token)
                            GENERATION_DISPATCH_GATE.reset(dispatch_token)
                    finally:
                        lease["finished"] = True
                        release_lease()

                await self.ledger.flush_durable()
                response = await bounded_call(dispatch(), deadline)
                metadata = self._usage(response, input_count, complete=True)
                metadata["physical_dispatch_id"] = entry["dispatch_id"]
                metadata["transport_attempt"] = retry
                metadata["finish_reason"] = getattr(response, "finish_reason", None) or metadata.get("finish_reason")
                metadata["raw_output"] = str(getattr(response, "content", "") or "")
                metadata["provider_completed"] = True
                if self.ledger.policy_enabled:
                    self._accounting_status(metadata)
                    metadata["failure_kind"] = FailureKind.MISSING.value if metadata["missing_usage"] else None
                    if str(metadata.get("finish_reason", "")).casefold() in {"content_filter", "refusal"}:
                        metadata["failure_kind"] = FailureKind.REFUSAL.value
                physical.append(metadata)
                self.ledger.settle(entry, metadata, status="completed", elapsed=time.monotonic() - started)
                if (
                    not self.ledger.policy_enabled
                    and self.profile.require_native_accounting
                    and (
                        metadata["missing_usage"]
                        or any(
                            metadata["accounting_methods"].get(name) != "provider"
                            for name in ("input_tokens", "output_tokens")
                        )
                    )
                ):
                    self.ledger.halt("required native usage or account debit is unavailable")
                    raise AccountingFailure(
                        "required native usage or account debit is unavailable", _physical_usage(physical)
                    )
                bound_violation = self._known_bound_violation(metadata, input_count, output_cap, kind)
                if bound_violation is not None:
                    self.ledger.halt(bound_violation)
                    raise AccountingFailure(bound_violation, _physical_usage(physical))
                if self.profile.require_native_accounting and (
                    metadata.get("response_identity_verified") is not True
                    or (
                        not self.ledger.policy_enabled
                        and (metadata.get("reasoning_tokens") is None or not isinstance(metadata.get("is_byok"), bool))
                    )
                ):
                    self.ledger.halt("required serving, non-thinking or billing-mode evidence is unavailable")
                    raise AccountingFailure(
                        "required serving, non-thinking or billing-mode evidence is unavailable",
                        _physical_usage(physical),
                    )
                return ResourceResponse(response, metadata, tuple(physical))
            except ResourceFailure as exc:
                if isinstance(exc, AccountingFailure):
                    self.ledger.halt(str(exc))
                if not physical or physical[-1].get("physical_dispatch_id") != entry["dispatch_id"]:
                    if response is not None and not metadata:
                        from .adapter import usage_to_dict

                        metadata = usage_to_dict(getattr(response, "usage_metadata", None)) or {}
                        metadata["raw_output"] = str(getattr(response, "content", "") or "")
                    if not dispatched and self.ledger.policy_enabled:
                        metadata.update(
                            input_tokens=0,
                            output_tokens=0,
                            reference_cost=0.0,
                            total_cost=0.0,
                            accounting_complete=True,
                            missing_usage=[],
                            accounting_methods={"total_cost": "confirmed-not-generated"},
                        )
                    metadata.update(
                        model_calls=int(dispatched), physical_dispatch_id=entry["dispatch_id"], transport_attempt=retry,
                    )
                    physical.append(
                        {
                            "model_calls": int(dispatched),
                            "physical_dispatch_id": entry["dispatch_id"],
                            "transport_attempt": retry,
                            "error": str(exc),
                            **metadata,
                        }
                    )
                    entry.update(status="accounting-failed", error=str(exc), usage=dict(metadata))
                    if not dispatched:
                        entry.update(status="not-dispatched", charged=0.0)
                    else:
                        try:
                            self.ledger.settle(
                                entry, metadata, status="accounting-failed", elapsed=time.monotonic() - started
                            )
                        except AccountingFailure:
                            # Invalid money stays reserved; valid known debit
                            # remains settled even when token evidence failed.
                            pass
                    self.ledger.persist(entry)
                exc.usage_record = _physical_usage(physical)
                raise
            except (Exception, asyncio.CancelledError) as exc:
                failure_kind = FailureKind.PROVIDER if isinstance(exc, TransientFailure) else classify_failure(exc)
                timing = CALL_TIMING.get() or {}
                known_hard_failure = timing.get("hard_failure")
                if known_hard_failure is not None:
                    failure_kind = classify_failure(known_hard_failure)
                phase = getattr(exc, "timing_phase", timing.get("phase", "admission"))
                provider_completed = bool(getattr(exc, "provider_completed", timing.get("provider_completed", False)))
                cause = getattr(exc, "cancellation_cause", None)
                if isinstance(exc, asyncio.CancelledError):
                    cause = tag_cancellation(exc, phase=phase)
                elif isinstance(exc, TimeoutError) and cause is None:
                    cause = "transport-timeout"
                if provider_completed and failure_kind in PROVIDER_FAILURES:
                    # Receipt recovery is accounting work, not another
                    # generation. The completed provider call resets its streak.
                    failure_kind = FailureKind.MISSING
                generation_not_started = isinstance(exc, TransientFailure) and getattr(exc, "generation_not_started")
                safe_zero = not dispatched or generation_not_started
                try:
                    raw = (
                        {
                            "input_tokens": 0,
                            "output_tokens": 0,
                            "total_cost": 0.0,
                            "reference_cost": 0.0,
                            "accounting_methods": {"total_cost": "confirmed-not-generated"},
                        }
                        if safe_zero
                        else self._usage(getattr(exc, "usage", None), input_count, complete=False)
                    )
                except AccountingFailure as usage_error:
                    from .adapter import usage_to_dict

                    raw = usage_to_dict(getattr(exc, "usage", None)) or {}
                    raw.update(
                        model_calls=int(dispatched), physical_dispatch_id=entry["dispatch_id"], transport_attempt=retry
                    )
                    physical.append(raw)
                    self.ledger.halt(str(usage_error))
                    entry.update(status="accounting-failed", error=str(usage_error), usage=dict(raw))
                    try:
                        self.ledger.settle(entry, raw, status="accounting-failed", elapsed=time.monotonic() - started)
                    except AccountingFailure:
                        pass
                    self.ledger.persist(entry)
                    usage_error.usage_record = _physical_usage(physical)
                    raise
                raw = {
                    **raw,
                    "error": f"{type(exc).__name__}: {exc}",
                    "model_calls": int(dispatched),
                    "physical_dispatch_id": entry["dispatch_id"],
                    "transport_attempt": retry,
                    "deadline_cause_protocol": DEADLINE_CAUSE_PROTOCOL,
                    "cancellation_cause": cause,
                    "timing_phase": phase,
                }
                if self.ledger.policy_enabled:
                    self._accounting_status(raw, confirmed_not_generated=safe_zero)
                    raw.update(
                        failure_kind=failure_kind.value,
                        provider_completed=provider_completed,
                    )
                if isinstance(exc, TransientFailure):
                    raw["generation_not_started"] = generation_not_started
                physical.append(raw)
                self.ledger.settle(
                    entry, raw, status="failed" if dispatched else "not-dispatched", elapsed=time.monotonic() - started
                )
                bound_violation = self._known_bound_violation(raw, input_count, output_cap, kind)
                if bound_violation is not None:
                    self.ledger.halt(bound_violation)
                    raise AccountingFailure(bound_violation, _physical_usage(physical)) from exc
                if self.ledger.policy_enabled and failure_kind in HARD_FAILURES:
                    self.ledger.halt("hard " + failure_kind.value + ": " + str(known_hard_failure or exc))
                elif dispatched and not isinstance(exc, TransientFailure) and not self.ledger.policy_enabled:
                    # Gateway binding, native-accounting and partial-stream errors
                    # cannot be converted into an ordinary workflow fallback.
                    if self.profile.require_native_accounting:
                        self.ledger.halt("production request failed: " + type(exc).__name__)
                if known_hard_failure is not None:
                    raise AccountingFailure(str(known_hard_failure), _physical_usage(physical)) from exc
                if isinstance(exc, asyncio.CancelledError):
                    setattr(exc, "usage_record", _physical_usage(physical))
                    raise
                stop = self.ledger.dispatch_stop_reason
                if stop is not None:
                    error_type = AccountingFailure if stop["hard_failure"] else PolicyStopped
                    raise error_type(str(exc), _physical_usage(physical)) from exc
                if isinstance(exc, TransientFailure) and retry < self.limits.transport_retries:
                    retry_after = getattr(exc, "retry_after")
                    if time.monotonic() + retry_after < deadline:
                        await asyncio.sleep(retry_after)
                        continue
                raise ResourceFailure(str(exc), _physical_usage(physical)) from exc
            finally:
                await self.ledger.flush_durable()
                lease["release_requested"] = True
                release_lease()
        raise AssertionError("finite transport loop fell through")


def _physical_usage(records: Sequence[Mapping[str, Any]]) -> UsageRecord:
    from .adapter import _attempt_usage

    usage = sum((_attempt_usage(dict(item), 0.0) for item in records), UsageRecord())
    return replace(usage, raw_metadata={"physical_attempts": tuple(dict(item) for item in records)})


def build_resource_controller(
    profile: BackendResourceProfile | Mapping[str, Any], ledger: ExpenditureLedger, config: Config
) -> ResourceController:
    if not isinstance(profile, BackendResourceProfile):
        profile = BackendResourceProfile.from_dict(profile)
    return ResourceController(
        profile, LocalTokenCounter(profile), ledger, limits=config.resources, failure_policy=config.failure_policy
    )
