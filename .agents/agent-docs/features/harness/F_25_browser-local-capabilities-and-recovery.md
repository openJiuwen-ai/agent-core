# Browser local capabilities and recovery

## Metadata

| Item | Value |
| --- | --- |
| Date | 2026-10-13 |
| Scope | Executor facts, model deadline ownership, local recovery, scoped readers and bound actions |
| Specs | S_05, S_18 |
| Refs | X batch frozen traces and analysis section 24; Refs: #1574 |

## Design

Implement in four verified batches. Preserve permission/target/serial execution boundaries,
one PageState/journal and the current Jev thresholds and request budget. No business
certifier, additional manager, site-specific heuristics or unrestricted script menu.

A: Match the native executor's exact selector-zero-match preflight envelope only for
known targeting tools. Explicit dispatch and arbitrary scripts retain unknown protection.
The model client owns first/idle chunk waits; shared Runtime deadline owns task duration.
Remove hybrid's hidden whole-stream cap and 0.1-second reserved-budget calls. Existing
terminal synthesis handles insufficient start budget; failure reports distinguish client
wait stages, local deadline and upstream/unknown sources without inventing missing usage.

B: Source-grounded navigation uses stable full-link/object recovery identity. Candidate
and late guard reuse the same card probe recipe/provenance, but guard always reads live
DOM. Two invalidations request one local LLM recovery; passive captures do not recover
unrelated objects. Retain historical cards/raw reads separately from executable bindings
in existing PageState/receipts. Repeated-work identity is not interaction revision or text
hash. Existing local backoff suppresses automatic repeated readers across unrelated
diagnostics; LLM direct reads remain available. Real document/scope, relevant interaction
or explicit binding changes reopen reads, without promoting historical nodes to fresh ones.

C: Reuse existing viewport/full-page cards, target extract_text/extract_value, observed
primary-link navigation and bound form/key actions. Describe range/truncation and nearby
control context. Readonly fields remain readable; current-value reads validate identity
without requiring old values. Writes retain exact state guards. No arbitrary values,
selectors, scripts or unlabeled-icon business meaning. One action+target provider request.

D: Replace conflicting wait/handoff instructions with compact existing-tool examples.
Consume post-action observations before waiting; do not guess URL transitions or replay
ACKed writes after failed waits. LLM owns interpretation, refresh decisions, local binding
and user-endpoint judgment; parent preserves constraints without extra investigation.

## Verification plan

Frozen native errors and X-shaped policy sequences; controlled stream/cancellation/usage
tests; generated guard/probe JavaScript against local synthetic DOM. Include negative
cases for genuine unknown writes, stale objects and read-after-async-update escape paths.
Run focused batches before the complete browser tool suite and Swarm prompt regressions.
Offline validation is not a claim of live website completion or speed. No provider calls,
service restarts, commits or pushes are included.

## Implementation and results

Implemented A/B before C/D. Native zero-match errors preserve dispatch truth; the
client retains stream wait ownership and task expiry retains its own diagnostic. Link
recovery uses complete destinations and source document; fixed readers keep historical
content and stable work identity while old targets remain unusable. Actual ACKed local
interactions reopen reader work; changing revision/text alone does not. Partial read-only
batches use their real per-step ACKs with the existing policy receipt/history consumers,
without entering the write journal or losing a successful read after a failed wait.
Targeted recovery compares against the dispatched decision capture, so a probe that
already committed fresh observation is consumed without waiting for another snapshot.

The local menu now includes viewport/full-page capped cards, bound text/value reads,
observed primary links, numeric fields and explicitly bound key uses. Live target reads
allow changed values, retain node/document/signature checks, and do not inherit write
permission. Worker/parent descriptions preserve local objects, source facts and user
constraints; no config knobs, business certifier, new manager or provider calls were added.

Verification on 2026-10-12:

- Batch A: 168 passed, 6 skipped; batch B: 142 passed, 6 skipped. Skips lacked Playwright
  in the SDK-only environment; the final run used the Swarm environment and local Edge.
- Final complete browser suite: **1,404 passed, 0 skipped**, 163.60 seconds. Synthetic DOM
  tests use a separate headless browser; provider fixtures are offline.
- The final suite includes strengthened per-step read ACK cases and both recovery
  callback orders: fresh capture committed before or after the tool receipt.
- Swarm browser config/prompt regressions: **23 passed**. One separately selected Skill
  index test fails before and after this change (isolated pre-change source comparison).
- Ruff: zero diagnostics on new/modified lines; existing unrelated diagnostics retained.

Frozen error envelopes, X-shaped navigation/reader sequences, local DOM replacement,
readonly values, viewport scroll, same-URL appended rows, ordering guards, provider schema,
permission/cancellation and usage accounting are covered. The >60-second progressing
stream check uses a controlled clock, not a real provider latency measurement.

The October 12 local analysis report section 25 records implementation and limitations.
After migration to official develop on October 13, the complete browser, TaskTool,
DeepAgent stream and subagent lifecycle selection passed **1,573 tests, 0 skipped**.
This includes real local Edge DOM fixtures and offline provider fixtures. Live paired
website tasks and speed/decision-quality measurements remain pending; these regression
results do not establish production task completion rates.
