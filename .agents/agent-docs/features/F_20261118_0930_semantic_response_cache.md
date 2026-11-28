---
id: F_20261118_0930_semantic_response_cache
title: Optional RedisVL read-only response cache implementation
type: F
author: Liuchenbing-2026
author_email: liuchen2026fly@163.com
modules: [openjiuwen/extensions/semantic_cache]
files: [openjiuwen/extensions/semantic_cache/cache.py, openjiuwen/extensions/semantic_cache/redisvl.py, examples/semantic_cache/read_only_qa.py]
designs: [D-20261120-2155-semantic-response-cache]
---

# Implementation and decisions

Base: develop at 46014823ee957e1b8fb4b82c92630a4c38ed9e32 (rebased from fbe93360f85dc761e6d9fd2141c59e4da4d28ccc).
Branch: feat/redisvl-response-cache-v2.

New extension contract is S_01_semantic_response_cache. No previous extension
spec is replaced. Core Model and callback/usage contracts are unchanged.

The first increment uses an application wrapper with a provider-neutral backend
protocol and optional RedisVL adapter. This keeps rollout explicit and prevents
silent interception of tools, histories or stateful agent calls. The Model bridge
is an example, not a new global model hook. CacheResult is deliberately separate
from AssistantMessage so a hit does not counterfeit provider usage metadata.
`RedisVLBackend` uses a per-instance `ContextVar` to reuse the lookup embedding
when the same task immediately stores that `(scope_key, prompt)` miss, avoiding
duplicate miss embedding without caching vectors across requests.
To harden high-concurrency usage, `SemanticResponseCache` adds opt-in single-flight
miss coalescing (`coalesce_misses=False`, keyed by `(scope.key, prompt)` with
`asyncio.shield` isolation, skipping redundant queued backend lookups while
in-flight and holding the entry until best-effort `store` finishes), optional
`max_concurrent_backend` independent lookup/store semaphore backpressure
(preventing lookup starvation while dropping saturated best-effort stores instead
of queuing), optional consecutive-failure circuit breaker (`circuit_breaker_threshold`
/ `circuit_breaker_cooldown`) with half-open single-probe recovery, runtime
`enabled` kill-switch, malformed candidate / non-finite vector fail-open validation,
PII-free warning/info logging via `openjiuwen.core.common.logging.llm_logger`,
and an immutable `CacheStats` telemetry snapshot via `.stats`.

The application owns authorization, trusted scope, answer equivalence and storage
approval. Scope filters supplement semantic retrieval; they do not solve semantic
contradictions. All-positive similarity validation is unsafe. The tests intentionally
use identical vectors for opposite questions to exercise policy rejection.

## Validation and limits

Remote CPU container: Python 3.12.16, clean isolated venv `pip install -e ".[semantic-cache]"`
(`openjiuwen 0.1.20`, `redisvl 0.32.1`, `redis-py 8.1.0`, `openai 3.57.0`), Redis Server
8.2.3. With clean `PYTHONPATH`, `pip check` reported `No broken requirements found.`.
No local Mac dependencies installed; no NPU used.

Verified results after the final code edit:
- Unit & real Redis integration (`PYTHONASYNCIODEBUG=1`, zero slow-task warnings):
  40/40 unit tests (`test_cache.py`, `test_example.py`, `test_redisvl_backend.py`)
  passed in 0.459s, and 6/6 real Redis integration tests (`test_redisvl.py`) passed
  in 3.645s including 32-concurrency per-tenant stampede coalescing, hot `enabled`
  toggle, and circuit-breaker open/bypass/recovery.
- Line coverage (`trace.Trace` over all 46 UT+ST tests, 12.090s, 46/46 passed):
  97.8% (`360/368` lines across `openjiuwen/extensions/semantic_cache/`, including
  99.0% in `cache.py` and 93.3% in `redisvl.py`).
- Live real SDK + Redis + hosted LLM E2E (`examples/semantic_cache/read_only_qa.py:answer_question`
  with real `openjiuwen.core.foundation.llm.Model`, `ProviderType.DeepSeek` /
  `deepseek-flash`, offline 4-CPU `langcache-embed-v2@c6c325ba`, Redis 8.2.3 with
  documented `connection_kwargs`): passed default-off gating, cold miss store,
  paraphrase hit (`+0` `Model.invoke`), adversarial negation/condition rejection
  (`validator_rejections=2`, `0` wrong hits), tenant isolation, 8-way stampede
  coalescing (`1` real `Model.invoke`, `7` cache hits), runtime `enabled` toggle,
  and live Redis pause/unpause outage circuit-breaker trip (`2` fast bypasses,
  0 crashes) + automatic single-probe recovery (`cache_hit=True`, `+0` `Model.invoke`)
  with PII-free `llm_logger` logs (8 semantic-cache lines, 0 canary/answer/key leaks).

Unit tests use mocked backend/model callables; real-Redis system tests use synthetic
vectors to test mechanics cleanly. Full repository-wide test suite and upstream CI
have not yet run. No formatter or static checker (`ruff`/`pylint`/`mypy`/`make check`)
was invoked directly per `AGENTS.md`; AST/import-order/complexity/docstring compliance
was audited via Python inspection (`returns<=5`, `locals<=12`, `branches<=9`,
`line_length<=120`).

## Issues and remaining work

- Initial integration test used deprecated CustomTextVectorizer; switched to
  CustomVectorizer and reran the same real integration suite.
- Standalone performance/concurrency harnesses remain outside the repository test
  suite and are not overstated as openJiuwen SDK-wide acceptance.
- Full container suites (40 unit tests, 6 Redis integration tests, clean extras
  packaging, and live-SDK end-to-end verification) ran against base commit
  `fbe93360f85dc761e6d9fd2141c59e4da4d28ccc`; subsequent redaction of raw
  exception text in `_record_backend_error` warning logs was verified locally
  across all 41 unit tests (`PYTHONASYNCIODEBUG=1`, in-memory `llm_logger` stub)
  and rebased cleanly onto `46014823ee957e1b8fb4b82c92630a4c38ed9e32`.
  Maintainer acceptance of design `D-20261120-2155-semantic-response-cache` and
  RFC #2738, as well as upstream CI gates (`ruff`/`pylint`/`mypy`/repo-wide
  pytest), remains open. Optional dependency is bounded to the tested RedisVL
  minor line (`>=0.32.0,<0.33`). Timeout relies on cooperative async callbacks.
