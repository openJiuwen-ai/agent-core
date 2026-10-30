# Browser receipts and local recovery

## Metadata

| Item | Value |
| --- | --- |
| Date | 2026-10-13 |
| Scope | Browser tool receipts, source binding, target compilation and local intent handoff |
| Specs | S_05, S_18 |
| Refs | October 8 U2/U3/U5/U16 traces and October 9 offline reproductions; Refs: #1574 |

## Decision

Keep one PageState and execution journal. A failed read/wait is a partial receipt, not an
unknown mutation. Earlier action ACKs, each failed condition and timeout remain visible;
optional failure is not success. An actual uncertain write remains dispatched_unknown.
Attempting a text wait cannot certify that its expected text was observed.

Probe source validation uses the current runtime page binding, with the old phase URL
only as a fallback when no current binding exists. Reject genuinely foreign observations
before policy/evidence processing and synchronize the rejection, execution state and tool
message. Preserve the original observation through the existing raw recall path.

A registered navigation-only target has no click locator. A single click may still use
the existing equivalent navigation path; a batch expecting a popup must instead return
the actual capability error and observed primary URL before dispatch. Never erase the
target and report a missing argument, silently change popup intent, or guess a selector.

The model-facing batch schema offers the common waits (selector, text, load state, URL,
selected state and tab); specialized legacy waits stay supported by the controller.
Required parameters and exact target/generation checks remain. After a batch ACKs an
action but a subsequent extract cannot resolve its DOM target, make at most one bounded
refresh through the existing metadata/probe channel. Return current bindings and a read
recovery hint; do not replay fill or automatically substitute a similar control. If
refresh fails, preserve the original partial receipt and the concrete capture error.

In normal complex-task interpretation/recovery, LLM can update existing objective,
target_ids and allowed_operations once for the remaining local fragment. A read-only
fragment excludes mutation candidates using existing constrain_actions; enough facts
should lead directly to an LLM answer. Simple tasks need no phase, and Runtime does not
infer completion, create a business plan or maintain another candidate controller.

## Rejected alternatives

- Global unknown/reader gates, larger quotas, another progress or recovery manager.
- Replaying ACKed actions because a wait/read failed, or treating optional waits as success.
- Relaxing generation/node guards or classifying arbitrary scripts as read-only.
- Domain-specific advertisement rules, automatic business completion certification.

## Verification

Regress the saved failure shapes and real local DOM rerender behavior, including positive
and negative source binding, failed wait evidence, genuine uncertain mutation, and normal
local operation/object constraints. Use isolated logs, no user browser or provider calls.
Completed on 2026-10-09:

- SDK browser/TaskTool/session affinity suite: 1,264 passed in the SDK environment;
  all 56 initially skipped browser cases passed with isolated headless Edge in the
  existing Swarm Playwright environment (1,320 distinct passing cases total).
- The new regression file has 23 passing cases, including the real DOM rerender and
  read recovery case. The refreshed value is readable without repeating fill/click.
- Saved U5/U16 receipts were replayed through the current journal: U5 now reports
  partial/executed=true with failed wait intact; U16 retains five ACKs and failed/unstarted
  reads. Capability and source comparison fixtures preserve the reproduction limits.
- Actual SDK Ruff and Git whitespace checks pass. No live provider/website tasks ran;
  tests use dedicated log files and a browser profile separate from the user's service.

## Limits

Offline/fixture results prove contracts, not website task completion or LLM uptake of the
optional handoff. A new live run is needed to assess useful Jev coverage and latency.
