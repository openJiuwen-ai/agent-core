# Browser reader admission and stale observation recovery

## Metadata

| Item | Value |
| --- | --- |
| Date | 2026-10-13 |
| Scope | Reader reuse, stale observation handoff, optional intent/reporting and source facts |
| Specs | S_05, S_18 |
| Refs | October 9 V8/V21/V22/V23 traces; Refs: #1574 |
| Test baseline | F_22: 1,320 distinct SDK cases passed |

## Background and decision

Homepage countdown changes produced 77 distinct retry keys, allowing two runs to spend
39 Jev actions each rereading the same scope. Independently, unexecuted stale-guard
receipts were invisible to observation refresh, so eight different actions reused one
invalid snapshot. Reuse policy receipts, PageState and the existing bounded repair path.
Separate successful-reader admission from changing content; distinguish stale dispatch
basis from a single ineffective action. Both LLM and Jev share successful read receipts.

FINISH is only a handoff. Clarify optional phase/batch examples, retain strict argument
and target checks, and align final progress with the user-defined endpoint. Keep parent
delegation concise and source limitations explicit. Add bounded nearby text and generic
recommendation-region source facts to existing probes, without business certification.

## Rejected alternatives

- Site-specific countdown/advertisement rules or greater quotas to hide loops.
- A second observation/progress manager, completion judge or compulsory phase.
- Relaxing stale targets, replaying denied actions, or trusting arbitrary scripts as reads.
- Promoting old raw observations to fresh executable bindings.

## Verification plan

Replay changing-text/same-scope reads and the stale-guard context lifecycle, including
LLM/Jev handoff, new documents/queries/intent, ordinary failures and bounded repair
failure. Regress optional phase/schema/reporting, and exercise recommendation/nearby
source facts with a local DOM. Run surrounding browser and parent prompt suites with
isolated logs; no live provider calls or user browser operations.

## Limits

Offline regression verifies routing/contracts, not LLM compliance, website success or
live speed. A restarted service and new real-task run are needed for end-to-end acceptance.

## Completed verification

- SDK: 1,348 distinct cases passed across the browser tool, task-tool and KV-affinity
  suites. The SDK-only run passed 1,288 and skipped 58 browser-dependent cases; all 58
  subsequently passed using an isolated headless Edge profile and synthetic local DOM.
  Two additional LLM/Jev stale-receipt counter cases also passed. No live site, provider
  or user browser was used. An initial new DOM assertion incorrectly expected secondary
  recommendations alongside main results; the fixture was corrected to cover each
  source independently, preserving the existing preference for actual main results.
- Swarm parent prompt suite: 12 passed. The two changed Swarm prompt modules pass Ruff.
  SDK changed-line lint comparison has no new findings; 21 pre-existing findings remain.
  Git whitespace checks pass.
- Frozen V21/V22 replay: 39 and 38 retained full bodies previously produced 77 distinct
  keys. With page, document, interaction and local intent fixed, each trace now produces
  one reader retry key; after the first successful read, no same-scope reader candidate
  is offered. V22 still has 39 recorded read calls; the last lacks retained full text
  and is not included in the body comparison.
- Frozen V8 receipt replay: all eight explicitly stale, unexecuted receipts now join
  the refresh lifecycle instead of selecting the earlier successful action group.
  Regression covers bounded one-shot repair, mixed ACK/rejection groups, repair failure,
  stale-context refusal, new binding admission, and ordinary failure isolation. It does
  not reconstruct the missing historical DOM or relax target guards.

Local evidence is saved under the Swarm checkout's
`.tmp/jev-reader-recovery-20261009/`: `verification-summary.json`, the four JUnit XML
files, `replay.py`, and `replay-results.json`. The existing Chinese test report now
records implementation and live acceptance limits in section 20. No decision threshold,
production budget or configuration change was needed; the service must be restarted to
load these source changes.
