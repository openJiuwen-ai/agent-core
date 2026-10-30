# Browser P0 closure after runtime simplification

## Metadata

| Item | Value |
| --- | --- |
| Date | 2026-09-24 |
| Scope | Callable intent, recovery handoff, explicit acceptance and terminal facts |
| Specs | S_05, S_18 |
| Test baseline | F_10: 1023 browser/client/TaskTool tests; 13 host tests |
| Refs | Local remaining Jev P0 implementation request; no issue assigned |

## Background

F_10 fixes partial execution, local UI versus business uncertainty, optional intent,
budget classification and shared observation. The September 24 report still exposes
weak condition schemas, mismatched target views, recovery instructions absent from
LLM input, unverified sales ordering, cart capability gaps and incomplete failure handoff.

## Decisions and state

Keep the existing Session and tools. Discriminated condition schemas validate before
budget use; failures carry field paths, expected fields and current bindable targets.
The LLM sees a bounded projection of the same guarded registry used for intent binding.
Bindings remain fresh and unique. Optional intent updates retain a small audit history;
user acceptance is derived from the original goal and task evidence, never from the
model's chosen temporary URL/node condition. Existing query/entity/variant slots keep
logical identity across DOM changes. No general requirement graph is introduced.

Fallback supplies a bounded, per-call recovery message with current intent, missing
facts, execution uncertainty and allowed recovery. A corrected intent or fresh evidence
re-enters Jev through existing fingerprints. The tool layer reports dispatch and observed
feedback; feedback such as Added is retained but does not certify SKU quantities.
The cart adapter interprets observed capabilities and conservative confirmation patterns,
enforces pre-write baseline and immutable cart identity, and compares independent reads.
Optional verify inspect_cart uses a fixed DOM inspector to discover reader hints without
arbitrary scripts. Hints cannot certify completeness. Stable requirement_id rebinds a
cart reader without changing expected deltas or replacing the original baseline.

Explicit supported sort requests and distinct product/shop ratings need task-bound
source evidence. Query matching alone cannot certify ordering, model tool arguments
cannot select evidence variants, and ordinary inferred extraction hints stay advisory.
Terminal and provider-failure paths share authoritative execution/acceptance/budget
projections. Separate observed page blockers, runtime limits and unconfirmed worker
claims. Retry hints cannot authorize replay of uncertain business actions.

Browser models copy parent client configuration and cap first-chunk waiting at 90 seconds,
respect shorter configured waits, and bound waits by task time with a handoff reserve.
No global provider timeout change; no action replay on model failure.
Action-budget exhaustion retains three verification reads under the original deadline;
phase verification shares the bounded unknown-effect recovery lane. Metadata cannot reset it.

## Rejected approaches

No mandatory full phase protocol, new workflow engine, verifier model, arbitrary-script
read exemption, lowered Jev confidence, blanket budget increase or model-authored proof.
No live business mutations in deterministic regression checks.

## Verification

Replay the frozen malformed Ctrip conditions, Taobao sales-sort contradiction and Lazada
click/Added receipt as data. Test schema repairs, target mismatch, fallback/re-entry,
cart read failures and unknown effects, explicit acceptance, provider timeout handoff,
and the existing complete browser/client/TaskTool and host routing suites.

## Known limits

Final validation: 1064 browser/client/TaskTool tests pass (41 new P0 regressions),
plus 13 JiuwenSwarm permission/log/calibration tests. Isolated Edge fixtures execute
the actual Probe and fixed cart-inspection JavaScript. Data-only replay retains
Ctrip partial dispatch, repairs its condition schema without fabricated targets,
rejects Taobao price-asc as sales completion, and distinguishes Lazada's two known
acknowledgements from missing SKU proof. New/changed lines have no Ruff violations;
19 pre-existing E501 lines in probes.py remain unchanged. Both diff checks pass.
No real provider or business-account task was run.

Real provider/site acceptance and runtime P50/P95 require a new matched llm/hybrid run.
Unknown cart layouts or missing stable SKU/full-cart coverage remain partial. General
site adapters, lighter observations and additional Jev tools remain P1.
