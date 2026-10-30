# Browser runtime phase contracts and verified handover

## Metadata

| Item | Value |
| --- | --- |
| Date | 2026-09-24 |
| Scope | P0 phase, binding, progress, execution uncertainty and completion |
| Specs | S_18 |
| Refs | Local Jev integration request; no issue assigned |

## Background

September 23 traces contain completed-sort reversals, a successful English search
being offered a Chinese replacement, FINISH disabling Jev for the remaining task,
and a timed-out cart mutation followed by another write. Executor success and a
changed DOM did not prove business completion. Host redaction also damaged JSON
floating point metrics and calibration silently omitted those records.

> Superseded in part by F_10: phase conditions are optional, temporary bindings
> no longer accumulate, and baseline closure is scoped to possible business effects.
> The following records the original F_09 design and its original validation.

## State and decisions

Extend the existing session task state, not a second agent loop. An optional
`browser_phase` tool lets the LLM set a bounded current objective, operations,
observed target/value bindings and typed conditions. It also reads/verifies those
conditions without arbitrary JavaScript. The runtime owns versions, observations,
condition evidence and immutable cart baselines. Replacing a phase retains its
outstanding requirements. Jev can request verification but cannot set conditions
to satisfied, authorize tools, or erase requirements.

The shared working-context projection goes to both models. Fresh local control
and URL facts validate simple conditions; evidence-slot conditions require runtime
provenance and the requested query/variant. A narrow cart reader uses explicit DOM
selectors, stable SKU attributes, quantities and a unique numeric cart-line count.
The line count must match the observed rows; a partial/virtualized list cannot
prove that all original items were preserved. Absent/ambiguous identity or a
missing pre-action baseline stays unknown. Baseline creation is closed after any
possibly executed browser write, including arbitrary evaluate/run_code calls;
navigation and fixed reads remain available before the baseline. Generic
website/cart adapters are P1. Verification refreshes control/URL observations;
a failed read invalidates the cached capture instead of certifying stale facts.

Only current-phase values are paired with observed fields. A successful LLM fill
locks that node's query against synonym overwrite until a new phase binds another
value. A previous LLM query cannot authorize Enter before that new value is filled.
Selected controls and satisfied phase targets are removed from the action menu.
Jev FINISH requests phase validation and LLM synthesis/planning; a new phase version
can re-enter without resetting the task budget or deadline.

Execution records distinguish prepared, dispatched, acknowledged, observed,
verified, rejected-before-dispatch and dispatched-unknown. Uncertain writes require
read-only reconciliation before another write by either model. A generic changed
snapshot cannot clear an unknown business mutation. Final completion rejects
unresolved contracts and unknown writes, while inferred extraction hints remain
advisory for existing pure LLM information tasks.

## Rejected approaches

No confidence reduction, blanket inferred-field completion gate, model self-certified
cart success, automatic mutation retry, new executor, free-form Jev arguments, or
per-step LLM judge. Do not send node guards/selectors to Jev. Keep both providers
and the existing permission/deadline/cancellation lifecycle.

## Verification and limits

Replay sanitized ordering, synonym overwrite, FINISH re-entry, and cart timeout
trajectories; test guard refusal versus post-dispatch failure, persistent state and
partial completion, host JSON masking and incomplete-log auditing. Real provider
latency and website business completion still require a fresh acceptance run.
Lightweight observation, broader readers and helper models remain P1.

Final offline validation: 976 browser/client tests pass (including 32 new phase
regressions and real isolated DOM fixtures); 83 focused JiuwenSwarm log/config/
permission tests pass. The expanded host suite has 1327 passes and 13 Windows
path/platform failures; all 13 reproduce with the pre-change HEAD modules loaded
in memory. No live provider or website task was run. Reprocessing the frozen
September 23 trace reports 6 malformed Jev events and 3 missing parsable responses
explicitly, instead of silently omitting them. These are log integrity results,
not evidence of increased real-world speed or completion rate.
