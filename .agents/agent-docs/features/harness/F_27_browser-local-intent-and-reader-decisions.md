# Browser local intent continuity and reader decisions

## Metadata

| Item | Value |
| --- | --- |
| Date | 2026-10-14 |
| Scope | Existing phase lifecycle, literal reader bindings, same-request decision semantics |
| Specs | S_05, S_18 |
| Test baseline | Y31 stale search form; query-specific probes; fixed reader target uncertainty |
| Refs | Multiversion analysis section 7; user requested no issue association |

## Background

A phase bound to the search form continued excluding result-page controls. Fixed operation
coverage did not provide LLM-resolved query literals. Reader target questions used generic
control language although their choices were already compiled reading ranges.
Retained Y31 controls also expose an independent menu hole: div[role=checkbox] reached a
checkbox branch that emitted actions only for native input checkboxes. Rebinding alone
could not expose these breakfast/star/rating controls.

## State and transitions

Keep the existing active_phase_contract. A captured different document from all bound nodes,
or an acknowledged LLM operation on another bound control, marks a local handoff reason.
The old objective/values are historical until explicitly replaced. Offer one LLM interpretation
turn for that phase, not a recurring global gate. Pending handoff retains the operation ceiling;
fixed reads and observed local UI remain possible, but unbound unknown-impact writes do not.
No target is rebound by label. Missing/partial observations alone do not expire bindings.

Optional read_queries (at most six bounded literals) belong to the same local phase. They
compile only fixed find/probe_interactives operations, never selectors, scripts or field values.
They are not carried from an expired local fragment. Readers use existing receipt scope and
successful-read suppression, retaining explicit LLM re-read and changed-scope exits.

## Decisions

1. Detect stale local ownership using actual document/control identity and ACKs. Rebinding
   remains an LLM action; inspection-only operation ceilings survive handoff.
2. Provide a short current objective plus resolved reading queries in the existing phase call.
   Do not require phases for simple queries or infer translations/business plans in Runtime.
3. Express fixed reader choices as fully bound scope/parameters/output, distinct from control
   selection. Both independent heads remain in one provider-neutral request. Preserve action
   and target thresholds, exact guards, permissions and serial execution.
4. Replay deterministic menu transitions and compare input variants with identical candidate
   IDs/steps. Report coverage separately from confidence. Actual model quality and endpoint
   latency need model/live evaluation; deterministic replay does not prove a confidence gain.
5. An observed clickable ARIA checkbox/switch with explicit matching checked state may use
   the existing click path. Native input checkboxes retain set_checked. The live checked-state
   guard, local object/operation scope and permission checks remain unchanged; no site-specific
   filter interpretation or assumed business approval is added.

## Rejected alternatives

No new planner/evidence framework; no automatic same-label rebinding, arbitrary query/script
generation, broad business-write release, per-step phase requirement, reader threshold bypass,
extra op-then-target request, larger action budgets, or repeated reads for statistical takeover.

## Verification

- Browser regression and corrected affected rerun cover 1,472 distinct passing cases, including
  38 new lifecycle/query/reader/ARIA cases. The full run had one assertion failure in the new
  DOM fixture (it assumed name rather than the production observed label); after correcting
  that assertion, all 143 affected cases passed. No production code changed between those runs.
- Frozen Y31 control/argument replay reproduces the old form ownership and ARIA menu gaps.
  Explicit current binding now exposes the breakfast/star/rating clicks and the bound price
  fill. Only three of the five old form bindings survived, so this is a mechanism replay,
  not a complete reconstruction of the historical model input.
- Twenty-four real OpenRouter Jev calls compared identical menus/parameters/thresholds in
  four sanitized synthetic local contexts, two input versions and three repeats. Both versions
  accepted the correct action in 12/12 cases. Query-control action/target confidence means
  changed from 0.587/0.677 to 0.847/0.770; first-card target confidence from 0.900 to 0.980.
  These small samples demonstrate a possible input improvement, not a production acceptance,
  calibration, completion or latency gain. Payload characters increased by about 8-15%.
- Changed-line lint and diff whitespace checks pass. Test artifacts, paired inputs, replay
  limitations and measured results are retained with multiversion analysis section 11.
- Swarm runtime/config/subagent integration checks: 52 passed; one pre-existing test remains
  explicitly skipped by its flaky-test marker. No running service was restarted for these checks.

## Known limits

The LLM still must select objects, resolve ambiguous requirements and translate literal terms.
Stale detection identifies loss of a local binding, not task completion. The model can still
choose an irrelevant legal action; improved input cannot guarantee useful execution.
