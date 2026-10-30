# Browser Jev Policy with LLM Fallback

## Metadata

| Item | Value |
| --- | --- |
| Date | 2026-09-23 |
| Scope | Browser decision policy, bounded action compilation, existing LLM fallback |
| Specs | S_18; execution contracts remain in S_05 and F_06 |
| Baseline | Offline provider, runtime, factory and host configuration regressions |
| Refs | Local Jev integration request; no issue assigned |

## Background

Repeated generative model calls dominate many browser tasks. Jev chooses from
observed actions; it cannot replace tool execution, open text generation or the
existing completion contract.

## Decisions

- Transport is explicitly selected as TypeSafe native or OpenRouter Decisions.
  Both send the same typed state/questions contract. OpenRouter uses
  `/api/alpha/decisions`, model `typesafe/jev-1.13`, and accepts only that
  version or its dated snapshot in the response; it never uses chat completions.
  Provider-specific defaults select the matching model, API base and key env.
  Existing timeout, retry, choice validation and same-task fallback apply to both.
- Both providers reuse the public core `JevSystemOneClient` and its typed wire
  schema. Its default `/v1/systemone` contract remains backward compatible; an
  explicit endpoint_path lets the browser retain its saved `/v1` + `/systemone`
  or `/api/alpha` + `/decisions` configuration without hidden URL rewriting.
  The browser adapter owns the total request/deadline budget, capped retry delay,
  model identity check and safe diagnostic codes. Disable core retries here so
  retry layers cannot multiply requests. Core owns HTTP, authentication headers,
  serialization and strict response types; Choice membership, distribution and
  confidence checks remain in browser policy. Cancellation is never swallowed.
  Strict parsing must reject boolean/string probabilities before coercion can
  make an invalid response appear valid. Keep HTTP client reuse and ownership.
- The host template can opt into hybrid independently of the SDK's llm default.
  The local OpenRouter setup reuses the user's verified configured model key
  through a dedicated local OPENROUTER_API_KEY; no credential is copied into
  source or diagnostics. The previous generic environment key was rejected.
  Model construction and decision diagnostics expose provider/model/route so a
  normal service launch and real browser tasks can be verified without a special launcher.
- RuntimeSettings carries an optional BrowserDecisionConfig. Default llm mode
  preserves the original path. Shadow records decisions without executing them.
- BrowserPolicyModel wraps the normalized browser model. ContextProcessor passes
  an opaque reference to a request-local structured observation through message
  metadata; no prompt parsing or global latest-state variable is used.
- The existing Probe supplies names, values and bounded native select options.
  A local node guard is kept out of model requests and rechecked before execution.
- The optional decision probe is composed into the normal metadata request.
  Its page text, controls and capture identity come from one DOM evaluation;
  the policy consumes that immutable projection and does not initiate probes.
- Shadow requests run in one bounded background task per model, never delay the
  LLM, cannot register executable decisions, and are cancelled during cleanup.
- The runtime rail checks the binding before budget admission. The batch tool
  revalidates and consumes the decision after all permission hooks, immediately
  before dispatch; Jev calls must not use automatic stale-target recovery.
- Jev clicks retain their observed click semantics instead of a navigation rewrite.
  Local guard fields are removed from public tool and recall projections.
- Streaming fallback applies the shared deadline to each producer pull, never
  across a yielded chunk where it could cancel the consumer's unrelated work.
  Pulls retain the caller task so provider ContextVar tokens can be reset on
  exhaustion or early close without a cross-context error.
- Jev chooses a closed action and target/value. Compilation produces a standard
  single-step Batch ToolCall; all existing permission, runtime and evidence hooks
  continue to execute. The policy never performs browser mutations.
- Provider errors, malformed/uncertain choices, unsupported work, missing values,
  no progress and finish proposals use the original model in the same task.
  Failure makes the rest of that task use the LLM, including focused resume;
  another task can try Jev again. Cancellation is propagated, never a fallback.
- An empty action menu is a per-turn handoff: the LLM can navigate from an empty
  page, then Jev can take over once supported observed controls appear.
- Explicit values and exact goal spans may fill ordinary fields; missing or
  generative text returns to the LLM. Fill never implicitly submits.
- Completion remains F_06. Jev does not certify completed, write progress or add
  another verifier. Task deadlines and already executed actions are retained.

## Rejected Alternatives

No new browser process, separate action loop, provider auto-selection, generated
selectors/JavaScript, next-best-action replay or per-step LLM review. No batch of
dependent form actions. No changes to the main agent's model.
Do not keep a second raw HTTP/JSON implementation in the browser adapter, register
Jev as a generative ModelClient, rewrite saved OpenRouter URLs implicitly, or
inherit the standalone client's long timeout and nested retry defaults.

## Verification

Provider responses are mocked in deterministic tests; synthetic local DOM tests
run the generated observation and freshness checks in an installed browser. Cover
invoke/stream, timeout/auth/rate limits, invalid answers, cancellation, unknown
execution outcomes, task isolation, target freshness, permissions, hot reload,
and deep/code/swarm configuration. Live provider/site measurements require an
explicitly configured key and remain separate from offline regressions.

## Known Limits

The first integration supports single observed clicks, ordinary field fills,
native selects and native checkbox updates. Visual controls, scrolling, complex
text, custom comboboxes and unsupported interactions use the original LLM.
Shadow shares the optional control projection with hybrid and therefore still
adds observation work/network contention. It never waits for Jev on the LLM
path. Multi-action execution and delta-only observations are not enabled.
The pre-execution DOM guard is a freshness check, not a transaction with the
subsequent Playwright mutation; existing actionability and runtime recovery still
govern mutations occurring during execution. No live latency/success-rate claim
is derived from offline tests.
Task allowlists, a live decision-mode kill switch, BACK compilation, confidence
calibration and the live Windows/site acceptance matrix remain unimplemented or
unverified. Hard failures and explicit handoffs stay on the LLM for the task;
only empty menus and temporarily unavailable tools allow per-turn re-entry.

## OpenRouter Protocol Sources

- https://openrouter.ai/docs/guides/community/jev
- https://openrouter.ai/docs/api/api-reference/alphadecisions/submit-a-decisions-questions-and-answers-request

The response's resolved model may include a date suffix. Exact matching against
the requested undated model would incorrectly turn every successful request into
LLM fallback. Accept only an exact version match plus an optional eight-digit
snapshot date; pinned snapshots must match exactly. Keep the resolved name in logs.

## Review Validation (2026-09-23)

Shared core client + browser/DeepAgent regressions: 983 passed. Host wiring/config
regressions: 39 passed. No skips; real local Edge DOM with mocked provider
responses. The public client has 50 tests and the five browser Jev-specific
files have 121, totaling 171 focused cases. These include late dispatch,
nonblocking shadow, coherent projection, stream timeout ownership and cleanup,
both provider endpoints, strict types, non-nested retries and client ownership.
Two separate live OpenRouter synthetic decisions succeeded, one using the
normal host configuration loader from the service working directory. The
resolved model was typesafe/jev-1.13-20260917 (687 ms / 781 ms observed). This
checks protocol/auth/configuration, not real-site latency or task success rates.
After the shared-client adaptation, another live OpenRouter request through the
saved service configuration succeeded (same snapshot, 828 ms). TypeSafe direct
is covered by offline protocol and fallback tests, not a live credential check.

## 2026-09-23 后续变更

本文为首次接入历史。持续回退、四种候选动作等现状已由 [F_08](F_08_browser-jev-handover.md) 的分段交接设计更新；当前契约见 S_05 / S_18。
