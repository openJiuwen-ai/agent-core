# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Application-owned eligibility and authorization; backend-owned retrieval.

No model hooks or process-global state are changed. Authorization must run before
this wrapper, including on hits. Scope values must come from trusted application
state, never directly from the user's request.
"""

import asyncio
import hashlib
import json
import math
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from time import monotonic as _monotonic
from typing import Protocol

from openjiuwen.core.common.logging import llm_logger


@dataclass(frozen=True)
class CacheScope:
    """Immutable partition; revise any value when its underlying contract changes."""

    tenant: str
    access: str
    application: str
    model: str
    template: str
    context: str
    generation: str
    embedding: str

    def __post_init__(self) -> None:
        if any(not isinstance(value, str) or not value.strip() for value in asdict(self).values()):
            raise ValueError("Every cache scope field must be a non-empty string")

    @property
    def key(self) -> str:
        """Compute the canonical SHA-256 partition key for this scope."""
        payload = json.dumps(asdict(self), sort_keys=True, ensure_ascii=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CacheCandidate:
    """Candidate prompt/response entry retrieved from a cache partition."""

    prompt: str
    response: str
    scope_key: str


@dataclass(frozen=True)
class GeneratedAnswer:
    """Application confirms completeness, absence of refusal and storage eligibility.

    Only the final text is stored. Do not include reasoning, credentials or
    provider usage metadata. Unknown eligibility defaults to no storage.
    """

    text: str
    reusable: bool = False


@dataclass(frozen=True)
class CacheResult:
    """Result returned by ``SemanticResponseCache.invoke``."""

    text: str
    cache_hit: bool


@dataclass(frozen=True)
class CacheStats:
    """Point-in-time counters for semantic cache observability."""

    lookups: int = 0
    hits: int = 0
    coalesced_hits: int = 0
    misses: int = 0
    bypasses: int = 0
    validator_rejections: int = 0
    backend_errors: int = 0
    store_successes: int = 0
    store_errors: int = 0
    store_skipped_overload: int = 0
    circuit_open_bypasses: int = 0


class CacheBackend(Protocol):
    """Protocol implemented by scoped semantic cache storage backends."""

    async def lookup(self, prompt: str, scope_key: str) -> CacheCandidate | None:
        """Retrieve a candidate inside exactly one partition."""

    async def store(self, candidate: CacheCandidate) -> None:
        """Store with a bounded TTL; hits must not extend that TTL."""


class SemanticResponseCache:
    """Explicit wrapper; deliberately not a drop-in replacement for Model.

    Callbacks are async and must cooperate with cancellation. Cache failures are
    best-effort misses; policy and generator failures propagate. The caller owns
    backend resources and closes them after all invocations have finished.
    """

    def __init__(
        self,
        backend: CacheBackend,
        *,
        enabled: bool = False,
        timeout: float = 0.5,
        coalesce_misses: bool = False,
        max_concurrent_backend: int | None = None,
        circuit_breaker_threshold: int | None = None,
        circuit_breaker_cooldown: float = 30.0,
    ):
        if not isinstance(enabled, bool):
            raise TypeError("enabled must be a bool")
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or timeout <= 0
        ):
            raise ValueError("timeout must be positive and finite")
        if max_concurrent_backend is not None and (
            isinstance(max_concurrent_backend, bool)
            or not isinstance(max_concurrent_backend, int)
            or max_concurrent_backend <= 0
        ):
            raise ValueError("max_concurrent_backend must be a positive integer or None")
        if circuit_breaker_threshold is not None and (
            isinstance(circuit_breaker_threshold, bool)
            or not isinstance(circuit_breaker_threshold, int)
            or circuit_breaker_threshold <= 0
        ):
            raise ValueError("circuit_breaker_threshold must be a positive integer or None")
        if (
            isinstance(circuit_breaker_cooldown, bool)
            or not isinstance(circuit_breaker_cooldown, (int, float))
            or not math.isfinite(circuit_breaker_cooldown)
            or circuit_breaker_cooldown <= 0
        ):
            raise ValueError("circuit_breaker_cooldown must be positive and finite")
        self._backend = backend
        self._enabled = enabled
        self._timeout = float(timeout)
        self._coalesce_misses = bool(coalesce_misses)
        self._in_flight: dict[tuple[str, str], asyncio.Future[GeneratedAnswer | None]] = {}
        self._lookup_semaphore = (
            asyncio.Semaphore(max_concurrent_backend) if max_concurrent_backend is not None else None
        )
        self._store_semaphore = (
            asyncio.Semaphore(max_concurrent_backend) if max_concurrent_backend is not None else None
        )
        self._circuit_breaker_threshold = circuit_breaker_threshold
        self._circuit_breaker_cooldown = float(circuit_breaker_cooldown)
        self._consecutive_backend_errors = 0
        self._circuit_open_until = 0.0
        self._circuit_probe_in_flight = False
        self._lookups = 0
        self._hits = 0
        self._coalesced_hits = 0
        self._misses = 0
        self._bypasses = 0
        self._validator_rejections = 0
        self._backend_errors = 0
        self._store_successes = 0
        self._store_errors = 0
        self._store_skipped_overload = 0
        self._circuit_open_bypasses = 0

    @property
    def enabled(self) -> bool:
        """Whether semantic caching is currently active."""
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        """Toggle semantic caching on or off at runtime."""
        if not isinstance(value, bool):
            raise TypeError("enabled must be a bool")
        self._enabled = value

    @property
    def circuit_open(self) -> bool:
        """Whether the backend circuit breaker is currently open."""
        return self._circuit_breaker_threshold is not None and _monotonic() < self._circuit_open_until

    @property
    def stats(self) -> CacheStats:
        """Return a snapshot of cache counters."""
        return CacheStats(
            lookups=self._lookups,
            hits=self._hits,
            coalesced_hits=self._coalesced_hits,
            misses=self._misses,
            bypasses=self._bypasses,
            validator_rejections=self._validator_rejections,
            backend_errors=self._backend_errors,
            store_successes=self._store_successes,
            store_errors=self._store_errors,
            store_skipped_overload=self._store_skipped_overload,
            circuit_open_bypasses=self._circuit_open_bypasses,
        )

    def _try_enter_backend(self) -> tuple[bool, bool]:
        """Return ``(allowed, is_probe)``, allowing one half-open probe after cooldown."""
        if self._circuit_breaker_threshold is None:
            return (True, False)
        now = _monotonic()
        if now < self._circuit_open_until:
            return (False, False)
        if self._circuit_open_until > 0.0:
            if self._circuit_probe_in_flight:
                return (False, False)
            self._circuit_probe_in_flight = True
            return (True, True)
        return (True, False)

    def _record_backend_success(self, *, is_probe: bool = False) -> None:
        """Reset consecutive backend errors after a healthy lookup and close the breaker."""
        was_open = self._circuit_open_until > 0.0 or is_probe
        self._consecutive_backend_errors = 0
        self._circuit_open_until = 0.0
        if was_open:
            llm_logger.info("Semantic cache circuit breaker closed after backend recovery")

    def _record_backend_error(self, op: str, scope_key: str, exc: Exception, *, is_probe: bool = False) -> None:
        """Log backend error without user content and open the breaker at threshold."""
        llm_logger.warning("Semantic cache %s failed (scope=%s): %s", op, scope_key[:12], type(exc).__name__)
        if self._circuit_breaker_threshold is None:
            return
        self._consecutive_backend_errors += 1
        if is_probe or self._consecutive_backend_errors >= self._circuit_breaker_threshold:
            self._circuit_open_until = _monotonic() + self._circuit_breaker_cooldown
            llm_logger.warning(
                "Semantic cache circuit breaker opened for %.1fs after %d consecutive errors",
                self._circuit_breaker_cooldown,
                self._consecutive_backend_errors,
            )

    async def _lookup_candidate(self, prompt: str, scope_key: str, *, is_probe: bool = False) -> CacheCandidate | None:
        """Look up a candidate from the backend, failing open on errors or malformed results."""
        key = (scope_key, prompt)
        if not is_probe and self._coalesce_misses and key in self._in_flight:
            return None
        try:
            async with asyncio.timeout(self._timeout):
                if self._lookup_semaphore is not None:
                    async with self._lookup_semaphore:
                        if not is_probe and self._coalesce_misses and key in self._in_flight:
                            return None
                        candidate = await self._backend.lookup(prompt, scope_key)
                else:
                    candidate = await self._backend.lookup(prompt, scope_key)
            if candidate is not None and (
                not isinstance(candidate, CacheCandidate)
                or not isinstance(candidate.prompt, str)
                or not isinstance(candidate.response, str)
                or not candidate.response.strip()
                or not isinstance(candidate.scope_key, str)
            ):
                raise TypeError("backend lookup returned malformed CacheCandidate")
            self._record_backend_success(is_probe=is_probe)
            return candidate
        except Exception as exc:  # Cache availability cannot break authorized generation.
            self._backend_errors += 1
            self._record_backend_error("lookup", scope_key, exc, is_probe=is_probe)
            return None
        finally:
            if is_probe:
                self._circuit_probe_in_flight = False

    async def _store_best_effort(self, candidate: CacheCandidate) -> None:
        """Persist an eligible candidate to the backend without failing the request."""
        if self.circuit_open or self._circuit_probe_in_flight:
            return
        if self._store_semaphore is not None and self._store_semaphore.locked():
            self._store_skipped_overload += 1
            return
        try:
            async with asyncio.timeout(self._timeout):
                if self._store_semaphore is not None:
                    async with self._store_semaphore:
                        await self._backend.store(candidate)
                else:
                    await self._backend.store(candidate)
            self._store_successes += 1
        except Exception as exc:  # Preserve the successful model answer on cache failure.
            self._store_errors += 1
            self._record_backend_error("store", candidate.scope_key, exc)

    async def _validate_candidate(
        self,
        prompt: str,
        candidate: CacheCandidate,
        can_reuse: Callable[[str, CacheCandidate], Awaitable[bool]],
    ) -> bool:
        """Validate candidate equivalence via the application callback, failing closed on errors."""
        # Policy errors are intentionally outside the cache-failure handler.
        async with asyncio.timeout(self._timeout):
            accepted = await can_reuse(prompt, candidate)
        if not accepted:
            self._validator_rejections += 1
        return bool(accepted)

    async def _generate_validated(
        self,
        prompt: str,
        generate: Callable[[str], Awaitable[GeneratedAnswer]],
    ) -> GeneratedAnswer:
        """Invoke the generator and validate its ``GeneratedAnswer`` shape."""
        answer = await generate(prompt)
        if not isinstance(answer, GeneratedAnswer) or not isinstance(answer.text, str):
            raise TypeError("generate must return GeneratedAnswer with string text")
        return answer

    async def _generate_and_store(
        self,
        prompt: str,
        scope_key: str,
        generate: Callable[[str], Awaitable[GeneratedAnswer]],
    ) -> CacheResult:
        """Record a miss, invoke generation, and store the answer when reusable."""
        self._misses += 1
        answer = await self._generate_validated(prompt, generate)
        if answer.reusable and answer.text.strip():
            await self._store_best_effort(CacheCandidate(prompt, answer.text, scope_key))
        return CacheResult(answer.text, cache_hit=False)

    async def _invoke_coalesced(
        self,
        prompt: str,
        scope_key: str,
        generate: Callable[[str], Awaitable[GeneratedAnswer]],
        can_reuse: Callable[[str, CacheCandidate], Awaitable[bool]],
    ) -> CacheResult:
        """Coalesce identical concurrent misses within ``(scope_key, prompt)``."""
        key = (scope_key, prompt)
        existing = self._in_flight.get(key)
        if existing is not None:
            shared = await asyncio.shield(existing)
            if shared is not None:
                shared_candidate = CacheCandidate(prompt, shared.text, scope_key)
                if await self._validate_candidate(prompt, shared_candidate, can_reuse):
                    self._hits += 1
                    self._coalesced_hits += 1
                    return CacheResult(shared.text, cache_hit=True)
            return await self._generate_and_store(prompt, scope_key, generate)

        fut: asyncio.Future[GeneratedAnswer | None] = asyncio.get_running_loop().create_future()
        self._in_flight[key] = fut
        shared_answer: GeneratedAnswer | None = None
        try:
            try:
                answer = await self._generate_validated(prompt, generate)
                if answer.reusable and answer.text.strip():
                    shared_answer = answer
            finally:
                if not fut.done():
                    fut.set_result(shared_answer)
                if shared_answer is None and self._in_flight.get(key) is fut:
                    del self._in_flight[key]

            self._misses += 1
            if shared_answer is not None:
                await self._store_best_effort(CacheCandidate(prompt, shared_answer.text, scope_key))
            return CacheResult(answer.text, cache_hit=False)
        finally:
            if self._in_flight.get(key) is fut:
                del self._in_flight[key]

    async def invoke(
        self,
        prompt: str,
        *,
        scope: CacheScope,
        generate: Callable[[str], Awaitable[GeneratedAnswer]],
        can_reuse: Callable[[str, CacheCandidate], Awaitable[bool]],
        eligible: bool = False,
    ) -> CacheResult:
        """Use only for authorized, immutable, single-turn text QA.

        ``can_reuse`` must check answer equivalence, including negation, entities
        and time sensitivity. Similarity alone is not a sufficient validator.
        ``eligible=False`` bypasses both lookup and storage.
        """
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("prompt must be non-empty text")
        if not isinstance(scope, CacheScope):
            raise TypeError("scope must be a CacheScope")
        if not isinstance(eligible, bool):
            raise TypeError("eligible must be a bool")
        if not self._enabled or not eligible:
            self._bypasses += 1
            answer = await self._generate_validated(prompt, generate)
            return CacheResult(answer.text, cache_hit=False)

        allowed, is_probe = self._try_enter_backend()
        if not allowed:
            self._circuit_open_bypasses += 1
            answer = await self._generate_validated(prompt, generate)
            return CacheResult(answer.text, cache_hit=False)

        scope_key = scope.key
        self._lookups += 1
        candidate = await self._lookup_candidate(prompt, scope_key, is_probe=is_probe)
        if candidate is not None and candidate.scope_key == scope_key:
            if await self._validate_candidate(prompt, candidate, can_reuse):
                self._hits += 1
                return CacheResult(candidate.response, cache_hit=True)

        if self._coalesce_misses:
            result = await self._invoke_coalesced(prompt, scope_key, generate, can_reuse)
        else:
            result = await self._generate_and_store(prompt, scope_key, generate)
        return result
