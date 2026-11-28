---
id: S_01_semantic_response_cache
title: Opt-in read-only semantic response cache
type: S
author: Liuchenbing-2026
author_email: liuchen2026fly@163.com
modules: [openjiuwen/extensions/semantic_cache]
files: [openjiuwen/extensions/semantic_cache/cache.py, openjiuwen/extensions/semantic_cache/redisvl.py]
---

# Semantic response cache contract

This opt-in extension wraps an application generation callable. It does not
intercept Model, alter global routing or claim to implement authorization.
The caller authorizes before every request, including potential hits.

- Default disabled; request eligibility defaults false. Bypass performs one
  generation and neither reads nor writes cache.
- Scope has eight mandatory trusted nonempty fields. Canonical JSON SHA-256 is
  the partition key. RedisVL uses an exact TAG filter before vector retrieval;
  both adapter and wrapper verify the returned partition.
- A candidate is reusable only after the application's async validator accepts
  it. No generic negation/entailment detector is included. Unknown applicability
  must reject or bypass. Policy errors propagate without model execution.
- A hit returns final text with cache_hit=True and no generation call. It does
  not synthesize provider usage/callbacks/metadata or refresh TTL.
- Misses invoke generation once. Only nonempty text explicitly marked reusable
  is stored; the application owns completion, refusal and privacy checks.
- Backend lookup/store exceptions are best-effort failures. Model and policy
  exceptions, and task cancellation, propagate. Async operations must cooperate
  with cancellation for configured timeouts to bound waiting.
- Within a single miss task, `RedisVLBackend` may reuse the lookup embedding for
  the immediately following matching `(scope_key, prompt)` store via task-local
  `ContextVar` state without retaining embeddings across separate requests.
- Optional in-process miss coalescing (`coalesce_misses=True`) keys in-flight
  requests by `(scope.key, prompt)` and shares only reusable final answers; each
  waiter still executes its own `can_reuse` validator and falls back to
  independent generation if the leader fails, cancels or yields non-reusable
  output. Waiter cancellation does not cancel the leader.
- Optional `max_concurrent_backend` bounds concurrent backend `lookup` and
  best-effort `store` calls via independent `asyncio.Semaphore` instances so
  slow lookups do not starve fast writes; when the store semaphore is saturated,
  best-effort `store` skips immediately (`CacheStats.store_skipped_overload`)
  rather than queuing.
- Optional backend circuit breaker (`circuit_breaker_threshold` and
  `circuit_breaker_cooldown`) trips after consecutive lookup/store errors,
  bypasses backend calls cleanly (`CacheStats.circuit_open_bypasses`), and
  recovers via a single half-open probe after cooldown (releasing probe state on
  cancellation). Runtime `cache.enabled` allows instant hot disable/enable,
  malformed candidates or non-finite/empty vectors fail open without calling
  Redis or generation failure, and warning logs omit user prompt/response text.
- `SemanticResponseCache.stats` exposes an immutable `CacheStats` counter
  snapshot (`lookups`, `hits`, `coalesced_hits`, `misses`, `bypasses`,
  `validator_rejections`, `backend_errors`, `store_successes`, `store_errors`,
  `store_skipped_overload`, `circuit_open_bypasses`).
- RedisVL resources are caller-owned, as are embedder and lifecycle. RedisVL
  startup uses a dedicated index with scope_key TAG; the dimension, dtype and
  embedding revision must match the embed callable. Only the optional extra
  imports RedisVL. No implicit downloads on base-extension import.
- Unsupported: streaming, tools/actions, mutable/multiturn context, multimodal,
  structured outputs, cross-process single-flight, distributed authorization and
  general semantic correctness guarantees.

Canonical public usage: docs/en/Extensions/semantic_response_cache.md.
Behavioral verification: tests/unit_tests/extensions/semantic_cache and
tests/system_tests/extensions/semantic_cache. Real-service tests use synthetic
vectors and do not establish model performance or semantic quality.
