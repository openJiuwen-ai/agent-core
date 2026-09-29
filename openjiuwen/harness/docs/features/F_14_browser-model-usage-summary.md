# Browser model usage at task completion

## Metadata

| Item | Value |
| --- | --- |
| Date | 2026-09-25 |
| Scope | Browser LLM/Jev client accounting and existing task-end logging |
| Specs | S_18 |
| Test baseline | Browser status logging, policy and provider boundary regressions |
| Refs | User request for per-task model calls, tokens and elapsed time; no issue assigned |

## Background

The existing task-end `model_calls` counts decision windows. It cannot distinguish
a Jev request followed by an LLM fallback, and rejected Jev usage is absent from
the final assistant response. Task totals therefore need to observe both clients.

## Decisions and state

A small session-local meter stores completed counters and currently active calls.
The existing Browser status logger resets it at invocation start and emits
`model_usage.llm`, `model_usage.jev` and `model_usage.total` at `task_end`.
Each group contains `calls`, `input_tokens`, `output_tokens`, `total_tokens`,
`elapsed_ms`, `usage_reported_calls`, `usage_unknown_calls`, `failed_calls`,
`cancelled_calls`, `pending_calls`, and `token_usage_complete`.

Policy client entry/exit points account for Jev acceptance, rejection, handoff,
shadow, LLM fallback, exceptions and cancellation. The logger handles unwrapped
LLM responses and missing model-end callbacks. A policy-window marker prevents
counting the final assistant response again or inventing a call after a preflight
deadline rejection. Stream chunks use the latest cumulative usage exactly once.
Pending calls remain explicit in a snapshot; accounting never waits for shadow.
An invocation ID prevents a late shadow completion from changing another run.

The SDK Session recursively merges dictionaries. Saving the meter first deletes
its dedicated key, then writes the complete snapshot synchronously without an
await between the operations. This removes completed calls and previous-run
handles instead of merging them back into the active set. Other Session state
is unaffected. Real Session regressions cover completion, duplicate callbacks,
concurrent active calls and invocation reset.

`model_calls` retains its original decision-window meaning. Client retries stay
inside a logical call and its duration. Only returned token usage is known; absent,
invalid, partial or cancelled usage is flagged, and sums are known lower bounds.
The meter stores only numeric counters, call IDs and timing, never prompts or keys.

`task_end.elapsed_ms` is invocation wall-clock duration including tools and runtime.
`model_usage.total.elapsed_ms` sums client wait durations, excluding tool execution;
shadow may overlap the LLM. Resumed invocations emit separate totals. This scope
matches the existing start/end events; offline aggregation can group the events.
Without policy context, timings use the existing model callback interval and can
include callback overhead. They are not provider-reported inference durations.

## Rejected approaches

Do not infer cost from takeover rate, overwrite the LLM response's usage with Jev
usage, add cumulative stream chunks, estimate unreported tokens, create another
runtime controller, or change task budgets and routing for observability.

## Verification

Exercise pure LLM, accepted Jev, low-confidence and explicit handoff fallback,
provider failure, cancellation, stream terminal usage, preflight rejection,
concurrent shadow, invocation isolation and repeated callbacks. Run existing
status/policy/provider regressions without live model calls or browser tasks.

## Known limits

These counters are client invocations rather than billable HTTP attempts. A failed
retry without usage cannot be priced from local logs. Pending shadow requests at
task end and calls cancelled before final usage remain explicitly unknown.

## Validation result

The initial overlay passed 160 tests with 3 existing skips. After rebasing the
metrics changes onto the concurrently updated local decision loop, all 21 new
accounting cases passed; the selected suite returned 157 passed and 3 skipped,
with 3 legacy policy assertions failing. Running those same 3 tests against the
unmodified current SDK reproduced all 3 failures (old whole-window recovery gates
versus the new action-scoped behavior). These tests and decision logic are outside
this accounting change. Targeted fatal-error lint passed.

The final publication snapshot excludes the live host observer and UI transport.
Its SDK regression suite initially passed 1,120 cases, with 51 local Chromium
fixtures skipped. Re-running those fixtures with installed Edge plus the affected
accounting, status and policy tests passed 134 cases without skips. Four new real
Session cases first reproduced stale active handles and then passed after replacing
the meter snapshot. In total, 1,175 distinct SDK cases are covered; overlapping
re-runs are not added. Host routing, configuration, calibration and log-redaction
regressions passed 69 cases against this SDK snapshot. No live model calls were used.

Python syntax, fatal Ruff checks and Git whitespace checks passed. The Makefile's
format, import, spelling, Ruff and Pylint commands were also run directly because
GNU Make was unavailable. These advisory checks still report formatting, style,
fixture-import and inference warnings; this publication does not include broad
format-only changes. The spelling report concerns the intentional `preserv` regex
stem. The baseline already reports 22 line-length and 16 import-order findings.
