# Browser receipts, observation reuse and local continuity

## Metadata

| Item | Value |
| --- | --- |
| Date | 2026-10-08 |
| Scope | Executor receipts, shared PageState and Jev candidate continuity |
| Specs | S_05, S_18 |
| Refs | October 8 live traces, findings A/B/C; no issue assigned |

## Evidence and design

Seven click timeouts stopped before dispatch. ANSI styling hid their call-log entries,
and six compact results prematurely asserted execution after target preflight. The
executor must distinguish preflight, invocation uncertainty and acknowledgment. A
successful earlier sub-operation remains acknowledged even if a later operation fails.
Only complete executor logs can prove an unperformed invocation. Do not replay an
uncertain click through a second executor retry loop; Playwright owns actionability retry.

Local offload recall must not advance the page interaction revision. Reuse fixed card
reads for the same page, revision and parameters within the existing short observation
reuse window. Mutations, navigation, changed observations and explicit waiting invalidate
reuse. Arbitrary evaluate remains potentially mutating. Receipt counters, observation
freshness flags and LLM history must not count as page progress; repeated identical card
content must not become progress merely because it was invalidated and read again.

Reuse the shared journal and current PageState to keep Jev on the local work already
started by the LLM. Observed matching query parameters retire duplicate search submits;
previously visited tab destinations are recovery choices for the LLM, while new observed
tabs remain Jev choices. An acknowledged business action on the same object is not a
default Jev retry, even after re-render/navigation. Changed bindings and an explicit new
local phase can express different work. These are candidate-level decisions: fixed reads
and unrelated local UI stay available, and the LLM retains recovery authority.

Expose a compact factual local context alongside the existing optional objective. The
LLM should use the existing phase objective/bindings for complex fragments when needed;
simple searches need no extra phase call. Runtime does not infer a business plan from the
full task, and Jev never certifies completion. No new controller, proof requirement,
global read budget or site-specific rule is introduced.

## Verification plan

Replay all seven original executor errors through compact/primitive journal boundaries;
execute generated JavaScript against local stubs and an isolated browser. Check partial
typing, post-dispatch timeout, missing/truncated log and explicit execution evidence.
Test recall and fixed-reader reuse/invalidation, content-based progress, shared LLM/Jev
receipts, tab/search continuity, distinct objects/bindings and normal fallback/re-entry.
Live task completion and latency still require fresh service runs.

## Implemented and verified

- Compact preflight leaves `executed=false`; invocation is uncertain until ACK. Complete
  ANSI-normalized timeout logs can resolve an invocation as unperformed in the shared
  journal. Partial typing and post-dispatch errors retain their uncertainty. Playwright
  still retries actionability internally; the outer click replay loop is removed.
- Offload recall neither invalidates lists nor imports a historical URL into the live
  PageState. A single card-reader cache in PageState reuses identical scope for at most
  five seconds, with mutation/navigation/wait/observed-view invalidation. Cached results
  carry `diagnostics.observation_reused`. Arbitrary evaluate retains its mutation boundary.
- Read content is retained for comparison when stale, but not displayed as fresh.
  `new_observation` is distinct from `observed_state_change`; receipt metadata/history
  cannot manufacture progress. Reader failure keys include factual content so changed
  content re-admits the reader, without treating revision churn as progress.
- Candidate exclusions `search_already_observed`, `visited_tab_recovery` and
  `acknowledged_action_recovery` describe the local restriction. Unrelated candidates
  remain eligible. Both models see the factual `local_context`; LLM tool permissions and
  recovery authority are unchanged. The full task remains background when no optional
  objective exists; runtime does not derive a plan or certify business completion.

Validation on October 8: 1,172 browser/TaskTool cases passed in the SDK environment;
54 DOM cases skipped there for missing Playwright all passed with JiuwenSwarm's installed
Playwright and isolated headless Edge. Total: **1,226 distinct passing cases**, including
40 new A/B/C cases. The seven original executor errors are preserved in the regression
fixture, exercised for both decision sources. The last Batch-only rerun (24 cases) is
not added to that total. Ruff for changed Python files and `git diff --check` pass.

No live provider calls or real booking/cart actions were used for verification. Restart
the existing service to load the editable SDK; no YAML or frontend change is required.

## Follow-up limits from the October 8 service traces

The short reader cache does not cover repeated reads separated by potentially mutating
evaluate calls. No-progress detection now fires, but unrelated control observations can
change the reader action key and re-admit an identical card read. Tab filtering uses
visits within the current query; an old tab from a previous query can still become a Jev
candidate. These remain candidate/cache handoff gaps, not reasons to restore a global
read budget or business-certification gates. The service run did not reproduce the original
actionability-timeout trajectory, so offline receipt coverage is not a live closure claim.
