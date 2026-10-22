# Browser fact handoff and stream diagnostics

## Metadata

| Item | Value |
| --- | --- |
| Date | 2026-10-14 |
| Scope | Browser abnormal/normal result projection, local intent handoff, URL literals, stream timing |
| Test baseline | Existing browser, TaskTool and model usage regressions; targeted results recorded below |
| Specs | S_05, S_18 |
| Refs | Analysis section 26, Y12/Y24/Y25/Y26/Y27/Y28/Y30 traces; user requested no issue association |

## Background

Wrapped protocol/repeated-output exceptions bypassed browser structured termination and
TaskTool replaced facts with an empty failed result. Last-six observation selection hid
earlier source pages/sorts. Candidate navigation could hand a broad shopping objective back
to Jev before the LLM selected a variant. URL prose punctuation polluted a hostname, and long
streams had elapsed time without progress metrics.

## Data and transitions

Use the existing phase state, journal, source records and offload handles. A failure carries
code/source/stage/recoverability in the same terminal payload. Recovery checks retain the
shared deadline and resume count. Normal and interrupted projections use the same bounded
excerpt; source references survive, but executable target freshness is unchanged. Existing
BROWSER_TIMING events add stream frame/output counts and producer-wait offsets.

## Decisions

1. Preserve facts on every model/invocation exception, then decide limited recovery. Respect
   pending anomaly-rail retries; exhausted repetition does not gain a second retry budget.
2. Prefer source/scope diversity and deduplication within the six-item terminal excerpt.
   Explicitly describe its bounded nature; keep existing raw recall handles and card regions.
3. At candidate inspection/selection transitions, the LLM supplies existing local objective,
   operations and chosen control bindings. Preserve strict inequalities and endpoints in
   parent delegation. No extra runtime planner or mandatory per-step phase call.
4. Fix generic URL delimiters and report stream progress without saving reasoning bodies,
   changing model budgets or imposing a whole-stream time cap.

## Rejected alternatives

No site-specific SKU/cart validation, automatic replay of acknowledged business actions,
unbounded retries, new evidence store, global read quota or rollback of action capabilities.
No inference that incomplete excerpts, missing conventional URL parameters or failed waits
mean an acknowledged action must be performed again.

## Verification

28 new parameterized cases cover retained ACK/cart facts after a wrapped disconnect,
existing retry/deadline ownership (including the actual anomaly rail), same-query setup
failures, source coverage, literal URL semantics, existing operation scope selection, and
successful/interrupted/cancelled stream metrics.

The full browser/TaskTool/anomaly/client-timeout/stream-close selection ran 1,489 cases:
1,486 passed. Three failures exposed obsolete contract assertions/test context: unspecified
TimeoutError was labelled provider failure; navigation raw text was expected to be discarded;
and a SimpleNamespace callback omitted the real context's retry-consumption method. These
were updated while retaining evidence-field exclusion and no-replay assertions. The final
affected selection (including all three cases and real local DOM fixtures) passed 308/308,
with no skips. Swarm browser prompt injection/routing passed 3/3. Newly changed lines and
the new test file pass Ruff; git diff --check is clean. Pre-existing lint findings remain.

Frozen Y24/Y26 exception strings and retained checkpoints re-project with facts. Y26 retains
the acknowledged Add to Cart step and is resumable at its recorded end time; exhausted Y24
repetition is not granted another budget. The malformed Y25 literal is excluded. Y12's
retained checkpoint now projects the omitted first-sort title, but that checkpoint is after
resume, so this is not a replay of its original pre-resume model input. No model/provider
requests, remote browser tasks, service restart or live shopping actions were performed.

## Known limits

Prompt handoff guidance cannot prove model compliance or improved real-task success. A new
live run is needed to assess candidate selection, targeted verification frequency and long
stream causes. Bounded excerpts do not include every retained fact; raw recall is preferred
before re-reading. Actual target/permission checks remain authoritative for dispatch only.
