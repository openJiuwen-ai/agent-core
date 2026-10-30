# Browser runtime reduction

## Metadata

| Item | Value |
| --- | --- |
| Date | 2026-10-07 |
| Scope | Browser execution gates, optional intent, completion reporting and Jev handoff |
| Specs | S_05, S_18 |
| Refs | Local runtime reduction request; no issue assigned |

## Evidence and decision

October 7 traces show a cart reader rejecting valid pages, repeated reader rebinding
creating permanent requirements, 20 extraction attempts ending an unfinished hotel
task with minutes remaining, and three recovery refusals terminating unrelated work.
Playwright actionability timeouts before dispatch were recorded as unknown writes.
These are counterexamples to the effectiveness of the former semantic gates, not
evidence for another layer of proof or larger per-phase quotas.

Reuse the existing Session, PageState and execution journal. Phase counters and
semantic progress are advisory. Remove phase ceilings, verification-read allowances,
replan trial/denial ceilings and the special price-interval veto. Keep the task deadline,
model/tool timeouts, iteration bound, serial dispatch, permissions and exact target guards.
Jev keeps its bounded cost and local failed-target suppression; LLM recovery has the
same tools and execution facts. No task-specific exception or new controller is added.
An unrelated LLM read cannot erase failed actions. Successful recovery clears only the
matching action; new target state/bindings obtain their existing distinct candidate keys.
Task/resume URLs share the Markdown-aware parser, and visited destinations are retained
across local intent changes. FINISH is an LLM handoff even with unmapped adapter fields.

Business acceptance and optional phase conditions describe what the adapters observed;
they cannot veto ordinary actions or certify the whole business task. A completion is
the worker's judgment supported by task observations, subject to real execution blockers
and unresolved writes. Keep the existing result envelope for consumers, explicitly state
its scope, and retain unmapped fields, acceptance and raw observations as diagnostics.
Do not add another verifier or an automatic proof-repair loop.

The cart reader remains optional for callers that can bind it. No baseline is required
before an otherwise valid action; a successful click does not force a cart-verification
protocol. Explicit reader results retain honest before/after semantics, but do not add
global completion requirements. No automatic cart RPC runs on every observation.
Replacing local intent replaces its conditions; unchanged conditions may retain facts,
and journal uncertainty survives every intent change.

Use the executor's actionability call log to recognize a proven unperformed primitive
in both modes, independent of the old lean switch. Missing/truncated logs, post-action
timeouts and arbitrary scripts remain uncertain. Fixed reads and known local UI stay
available while uncertain business effects cannot be blindly replayed.

## Rejected alternatives

No site-specific SKU/room/card reader additions, new judge model, proof schema, semantic
permission layer, workflow manager, per-case budget increases or unsafe batch bypass.
The old strict/lean experiment no longer selects different execution truth.

## Verification and limits

Use offline real-shaped receipts and general mocked rail/tool regressions: both decision
modes, multiple tool entrances, changed descriptions, repeated reads and recovery,
unknown writes, invalid/stale targets, partial batches and deadlines. Validate actual
dispatch/handoff boundaries, not only helper return values. Live provider latency, site
behavior and end-to-end task success require
a fresh service run; removing false vetoes does not promise successful bookings or carts.

Final validation on October 7:

- Browser unit suite plus TaskTool: 1,134 passed; 52 local DOM cases skipped in the SDK
  environment because Playwright was absent there. Those same 52 cases then passed using
  JiuwenSwarm's installed Playwright and an isolated headless Edge, including partial-batch
  execution and exact target guards. No user browser/profile or live business site was used.
- After the final legacy-resume cleanup, 160 affected cases passed, including the newly
  added regression. Across the runs, 1,187 distinct cases are covered; reruns are not added
  to that total. The new reduction regression module contains 41 parameterized cases.
- Ruff under the SDK configuration and git diff whitespace checks pass. Production Python
  changes remove 378 net lines across eight files. Existing public tool names and the
  structured result envelope are retained; strict/lean no longer changes the guard contract.
- User-facing browser docs and S_05/S_18 are synchronized. Existing explicit absence,
  concrete contradiction checks and externally declared field contracts are retained;
  this change does not claim that every remaining semantic heuristic is independently reliable.

No commit, push, service restart, live model request or real booking/cart task was performed.
JiuwenSwarm already uses this SDK as an editable dependency; restart the existing service
normally to load the changed modules. No new YAML option or extra service is required.
