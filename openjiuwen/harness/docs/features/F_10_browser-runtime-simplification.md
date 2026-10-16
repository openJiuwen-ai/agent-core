# Browser runtime simplification and shared execution facts

## Metadata

| Item | Value |
| --- | --- |
| Date | 2026-09-24 |
| Scope | Optional intent, execution receipts, observation and bounded business verification |
| Specs | S_05, S_18 |
| Refs | Local Jev simplification request; no issue assigned |

## Background

The September 24 traces contain 26 failed phase calls out of 32, configuration
calls exhausting extraction budgets, stale control requirements, and partial
batches or native Ref-not-found failures becoming permanent unknown-write locks.
The F_09 implementation also mixed cart strategy with the generic execution log
and evaluated shared conditions inside the Jev publisher.

## Decisions

Keep the existing Session, lifecycle and tool executor. Make browser_phase an
optional intent update: objective is sufficient, observed value bindings and
conditions are optional. Keep durable evidence/cart requirements; replace the
previous intent's temporary control/URL conditions without erasing user evidence
requirements or execution uncertainty. No model-authored proof or WorkingMemory
rewrite protocol. Multi-value or multi-source work with no local intent hands
back to the LLM; there is no conjunction-regex requirement for phase configuration.

Existing observation acquisition evaluates conditions and reconciles execution
facts in both llm and hybrid. Management calls do not spend action budgets or
invalidate page state. Recovery reads have a bounded lane before the pending
replan gate and do not reset task budgets/deadlines. Intent validation precedes
state changes. VERIFY remains an optional explicit fresh read.

The execution journal is authoritative and records each batch step, including
acknowledged, rejected-before-dispatch, not-started and unknown results. Proven
preflight failures are not unknown writes. Known local UI effects do not block
unrelated work; unknown business/unknown-script effects retain reconciliation
protection. Recent actions and policy receipts project these facts and cannot
certify business success from generic page progress.

Move the bounded cart reader and comparison to cart_verification. Cart-related
effects require a valid pre-write baseline; ordinary search fills do not close
baseline collection. Unknown arbitrary code still does. Empty carts need positive
coverage proof; target-SKU and whole-cart preservation are explicit scopes. Reads
never establish a new baseline after a possible cart effect. Parent handoff carries
execution facts and allowed recovery, with identical business gates in llm/hybrid.

Same-intent updates are idempotent. Satisfied optional field conditions do not
block the next search submission; document changes expire temporary node bindings
without certifying their success. Auto cart verification reads only conditions
attached to pending effects, with a five-second total allowance per observation
under the original deadline, and emits component timing and canonical effect logs.

## Rejected approaches

No new state engine, workflow graph, event bus, verification agent, per-step model
judge, threshold reduction, automatic unknown-write retry, or blanket budget
increase. Do not remove side-effect guards just to increase Jev calls. Keep native
provider clients, permissions, cancellation and fallback behavior.

## Verification and limits

Add focused regressions from Ctrip partial filters, missing native refs, repeated
phase configuration, search then cart baseline, stale node replacement, and cart
timeout reconciliation. Run the browser/client suite and relevant host routing
checks. Real provider/network/captcha and unsupported cart DOMs remain acceptance
limitations; offline tests cannot establish production speedup.

Final validation: 1023 browser/client/TaskTool tests pass, including 28 simplification
regressions and isolated Edge DOM fixtures; 13 JiuwenSwarm permission/log/calibration
tests pass. Ruff checks under the SDK configuration and both repositories' diff
whitespace checks pass. A data-only replay of the frozen September 24 Ctrip batch
retains acknowledged / rejected-before-dispatch / not-started / not-started steps
with zero unknown writes. Fourteen phase calls spend zero extraction attempts.
No live provider or business-account task was run.
