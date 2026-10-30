# Browser Jev Segmented Handover

## Metadata

| Item | Value |
| --- | --- |
| Date | 2026-09-23 |
| Scope | Browser decision input, routing, guarded actions and fallback transport |
| Specs | S_05, S_18 |

## Background

September 23 traces exposed message-envelope values in fill candidates, sticky
fallbacks, missing rejected-response diagnostics, and an LLM recovery path failing
on dictionary tool arguments or unpaired Unicode surrogates.

## Current design

Historical implementation baseline. The September 24 P0 audit found that the
progress projection and unknown-write enforcement below were incomplete. F_09
and S_18 supersede the FINISH scope, field binding and postcondition contracts.

- Runtime supplies the normalized original goal, current subtask, observed controls
  and execution receipts. Jev selects a closed action; the LLM supplies planning,
  missing values, recovery and the final answer. Page text never grants authority.
- Session task state persists evaluation budget, routing scope, meaningful state
  fingerprints and execution history across focused continuation/model recreation.
  Low confidence, HANDOFF, transient service errors and observation failures yield
  the current segment. A changed executable state or current subtask permits another
  evaluation; timestamps, capture IDs and regenerated target IDs alone do not.
  Configuration/protocol/auth/billing failures disable Jev for the task. FINISH
  yields final synthesis. The original deadline and completion protocol still apply.
- Eligible controls are filtered and ranked before truncation. Literal search fill,
  observed autocomplete options and guarded Enter complement click/select/check.
  A bounded page-action helper supports explicit HTTP(S) navigation, back only when
  observed history permits, and one viewport scroll when space remains. It uses the
  same permission hooks and runtime budgets, checks the underlying capability, then
  verifies document/page/arguments immediately before execution. It accepts no code.
- Pending or ambiguous actions are never replayed automatically. Post-action state
  reconciliation distinguishes observed progress from an executor success receipt.
- Every model window has a route event. Logical evaluation IDs link request,
  sanitized response diagnostics (including rejected choices), HTTP attempts,
  compilation and execution. Logs omit goals, values, selectors, page text and keys.

## Rejected alternatives

No threshold reduction without labelled samples; no per-step LLM judge, second
executor loop, arbitrary Jev tools/scripts, speculative values or deadline resets.
No automatic retry of dispatched browser actions.

## Validation and known limits

Offline protocol, session-resume, permission, guard and isolated DOM tests cover the
boundaries above. Production speed and accuracy require a fresh paired task run.
Confidence calibration reports must use human-labelled decisions; unlabelled or
synthetic coverage tests cannot justify changing the production threshold.

Final local validation: 1,090 core tests and 102 JiuwenSwarm tests passed without
failures or skips, including isolated Edge DOM, both provider protocols, late
dispatch guards, host permission routes and Chat/Responses request encoding.
These counts include existing regression suites. Ruff E/F/I/ASYNC passed on the
decision modules and new helpers. Real-site latency and labelled confidence
calibration remain acceptance work; the production threshold stays at 0.65.
