# Browser observation-first results

## Metadata

| Item | Value |
| --- | --- |
| Date | 2026-10-08 |
| Scope | Default browser task state, output contracts, completion and parent handoff |
| Specs | S_05, S_18 |
| Refs | Local runtime contraction request; no issue assigned |

## Background

October 8 traces contain readable source text with unmapped title/rating/duration
fields, recommendation cards mistaken for cart rows, and a parent answer that
overstates a child observation. A runtime-generated field checklist cannot certify
these business meanings. Keeping such a checklist as advisory still adds repeated
inference, projections and prompt content.

## Decision and state

Reuse Session, PageState, source observations and the execution journal. Default
tasks use observations and do not infer required fields, result counts or comparison
slots from natural-language goals. Existing explicitly supplied output contracts
remain opt-in through the existing requirements_source=explicit state; they check
the declared output shape, not the business meaning of the whole task.

Legacy inferred requirements are retired when a task is loaded/resumed, preserving
source observations, execution uncertainty, local intent and the original deadline.
Default observation recording and context/result projection skip the field-slot and
business acceptance pipeline. Source text and tool-produced records remain available.

Completion is the worker judgment, constrained by actual execution blockers,
unknown writes, empty answers and absence of task observations. Current search URL,
field coverage, optional conditions and inferred absence cannot independently rewrite
completion or create an automatic repair invocation. Keep precise execution-target
validation and do not reinterpret arbitrary scripts as safe reads.

An explicit parent correction can resume a worker-completed task with the existing
resume_task_id, original deadline and one-resume limit. Repeating a completed query
without that explicit request returns the existing result; no semantic classifier
opens another run. Correction preserves observations, bindings and the journal.

The existing terminal envelope carries status, worker judgment, execution receipts,
source observations and the worker summary. Diagnostic blocker lists are explicitly
non-exhaustive. Parent and resume views retain that same information; rendering does
not duplicate the entire result in answer, browser_result and resume_context. No
new judge, per-claim proof schema, state manager, timeout budget or YAML switch.

Common wait guidance favors existing bound target/text/URL waits. A selector-dependent
wait still requires its target; optional wait failure cannot erase an earlier action ACK.

## Rejected alternatives

- Extending business field aliases until every website fits the runtime schema.
- Requiring another reader or another LLM call to certify every response.
- Treating an empty blocker projection as proof that the page has no blockers.
- Dropping target freshness, permissions, journal uncertainty or shared deadlines.

## Verification

Regressions cover readable unmapped text, multiple source pages, legacy inferred state,
explicit output contracts, returning to a results page after reading a detail, parent
handoff/rendering, and preserved execution gates. Navigation receipts must still agree
with PageState before metadata is attributed to a source.

- SDK browser, TaskTool and affinity suite: 1,196 distinct cases passed after the focused
  rerun. The initial final run had 1,195 passes and one outdated test that fed an internally
  returned resume ID back as an explicit request; the test now uses the real caller boundary.
- The SDK interpreter skipped 54 local DOM cases because Playwright is not installed there.
  All 54 passed using the Swarm interpreter and isolated headless Edge pages (193.17 s).
- Combined: 1,250 distinct cases passed, including 28 new observation-result regressions.
  Ruff passed for all 22 Python files changed in this contraction. No live website, model
  endpoint or user browser profile was used.
- Existing extraction-adapter tests now declare their output contracts explicitly.
  Removed NL-inference tests are replaced by regressions asserting that ordinary goals
  do not create required fields, slots or result counts. Execution checks were not relaxed.

## Limits

Source observations do not prove the worker's business interpretation. Reader scope
and answer quality still require real-task evaluation; no speed or completion guarantee
is inferred from offline tests. Existing local A/B/C receipt and continuity fixes are retained.

## Follow-up limits from the October 8 service traces

All 17 browser invocations used observation-based requirements. Field-slot certification
did not return, but worker/parent wording can still overstate a partial result, and a
parent can narrow the delegated objective before runtime executes it. Optional wait
failure preserves the action ACK in the journal, while the aggregate batch can still
report uncertainty. These are handoff and receipt-reporting issues; this change does
not claim to resolve them or introduce a business-answer judge.
