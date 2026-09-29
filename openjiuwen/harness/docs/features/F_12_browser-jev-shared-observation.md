# Shared observation and bounded Jev coverage after September 24 replay

## Metadata

| Item | Value |
| --- | --- |
| Date | 2026-09-24 |
| Scope | Shared control capabilities, evidence association, recovery accounting, guarded Jev reads and search actions |
| Specs | S_05, S_18 |
| Test baseline | F_11 browser/client/TaskTool regressions and frozen September 24 evening traces |
| Refs | Local request to fix observed failures and evaluate ThinkFlowLab/system1-agents; no issue assigned |

## Background

The evening replay contains 63 unknown-write gates after ordinary search timeouts,
separate sort and first-card reads that fail to join, an incorrect second-result
click, and a search literal made ambiguous by a separately quoted ordering label.
Three generic reads can also consume recovery allowance for the wrong journal entry.

## Decisions and state

Control capability capture belongs to Runtime in both LLM and hybrid modes. Reuse
the metadata capture and exact AX/DOM registry; enrich missing targeted capabilities
before preparing the journal, bounded by five seconds and the task deadline.
Observed search-form relationships and contextual
sort controls classify local UI. Unknown business effects remain unresolved until
independently verified. No model-authored safety declaration lowers an effect class. A source-bound search
query can verify a local submit postcondition; it cannot certify an unknown business effect.

Existing PageState carries a monotonic interaction revision. Evidence can join only
inside the same query, source, page identity, generation and revision. An intervening
potential mutation invalidates the association, even when its URL stays unchanged.
Confirmed historical milestones remain task-bound. Primitive primary-link navigation
retains actual redirect landing metadata instead of discarding its title. Optional phase summaries remain
views; acceptance and execution facts are authoritative.

Explicit phase verification gets at most three recovery attempts per outstanding
effect, retaining its count when other effects are resolved. Ordinary reads use ordinary budgets and cannot steal that allowance.
Action exhaustion retains its existing three-read allowance and original deadline.

Jev can select existing fixed interactive/card probes, observed search fill/Enter,
and navigation to an independently ordered first organic result. Search literals
are distinguished from quoted sort labels; ambiguous multi-query/multi-field work
still goes to the LLM. First-result requests cannot compile an arbitrary result-link
click. Tools stay behind the same permission, budget, task/page/argument and late DOM
guards. LLM and Jev use one execution chain, journal and browser task-turn lock.
Optional mutation allowlists keep registered fixed reads available. Fresh reads change the decision fingerprint; repeated unchanged reads hand back.

## Reference and rejected approaches

ThinkFlowLab/system1-agents supplies useful compact observations, explicit fill versus
submit semantics, history and bounded action choices. It also retains LLM typing and
finalization. We do not adopt its optional unsafe batch loop, loose label rebinding,
provider-error terminalization, or its benchmark numbers as performance evidence here.
No new executor, workflow graph, per-action LLM verifier, arbitrary-script exemption,
lowered confidence threshold or blanket budget increase is introduced.

## Verification

Replay the frozen search timeout, split sort/card evidence, quoted Python query and
second-result selection as deterministic data. Exercise the real probe JavaScript
on isolated DOM fixtures, both model modes, late guard rejection, fallback/re-entry,
budget accounting, and existing browser/client/TaskTool and host routing suites.
Live takeover and end-to-end speed require a new matched run; unit tests cannot certify them.

## Known limits

Unsupported business effects, genuinely ambiguous objectives and uncertain result
order still require LLM handling. Source-bound evidence association is conservative
across new documents and resumed browser sessions. External timeouts, captchas and
site access restrictions are not code-level success guarantees.


## Validation result

Final SDK run: **1111 passed**, including **47 new regressions**. Host routing,
calibration and log-integrity checks: **13 passed**. Real isolated Chromium DOM
checks cover hidden/unnamed search controls, contextual ordering and first-result
reordering between selection and dispatch. Provider, invalid-choice, cancellation,
late arguments, shared task turns and task-deadline boundaries are covered offline.
The full run includes the existing TypeSafe/OpenRouter client suite. Compilation,
targeted fatal-error lint and Git whitespace validation pass.

Reference reviewed at a fixed revision: [system1-agents action space](https://github.com/ThinkFlowLab/system1-agents/blob/0f0b4c9eca03127875cfdf0d941fbd22c16478a4/s1a/browser/action_space.py),
[decision model](https://github.com/ThinkFlowLab/system1-agents/blob/0f0b4c9eca03127875cfdf0d941fbd22c16478a4/s1a/browser/decision_model.py),
[browser front](https://github.com/ThinkFlowLab/system1-agents/blob/0f0b4c9eca03127875cfdf0d941fbd22c16478a4/docs/browser-front.md).
