# Browser decision observation and local handoff

## Metadata

| Item | Value |
| --- | --- |
| Date | 2026-10-13 |
| Scope | Fixed-reader observation, Jev inputs/candidates and independent confidence thresholds |
| Specs | S_05, S_18 |
| Refs | October 8 service traces and October 9 offline VM reproduction; Refs: #1574 |
| Test baseline | Previous browser/TaskTool suite: 1,202 passed, 54 DOM cases skipped |

## Background

All 27 fixed page reads in the observed batch lost their attached decision observation.
The generated wrapper called global timers that do not exist in the installed MCP's Node
VM. Its empty catch hid the failure while preserving the primary read. Twenty-two later
decision windows consequently fell back before Jev was called. Separately, 111 of 116
uncertain responses failed the operation head before target validation. Independent Jev
questions cannot rely on the criteria or answers of another head.

## Decision and state

Keep one PageState, the existing execution journal and task policy caches. Use the supported
Playwright page timeout promise as a race deadline for optional attached observation, not
as a mandatory sleep; do not inject VM globals or change executor privileges. Keep the
primary receipt when observation fails and record the failure in existing timing/policy
events. A compact capture with missing decision data can attempt one bounded metadata
refresh through the existing reconciliation path. No full AX or action replay is added.

Operation criteria include the operation's purpose and bounded examples of actual legal
targets/bound values. Target questions choose under the explicit assumption that their
operation is selected, independently in the same request. Existing LLM fallback messages
show the failed head and encourage a short local objective/observed bindings using the
optional phase tool during complex recovery; simple actions need no phase. Runtime does
not derive a natural-language plan or require conditions to certify completion.

Retain initial tab facts in the existing task policy to distinguish old tabs from newly
observed destinations. Old/visited tab recovery remains available to LLM and explicit local
objectives. Repeated-reader keys/progress use the reader's own content and scope instead
of unrelated controls. Acknowledged search submits are suppressed for the same observed
query; changed values/objective/observations re-admit local work. These are candidate
decisions, not new window gates or task budgets.

`action_min_confidence` and `target_min_confidence` independently override the legacy
`min_confidence`. An omitted override inherits the legacy threshold, preserving existing
SDK callers/configuration. Updated Swarm template and local configuration explicitly set
0.50 and 0.60 respectively. FINISH/HANDOFF validate only the action head and always return
to LLM. Equality passes; malformed responses and exact dispatch guards remain unchanged.

## Rejected alternatives

- Adding unrestricted timer globals to the MCP VM or enabling unsafe development modes.
- Replaying the primary action when only its attached observation failed.
- Requiring a new phase/proof tool before every simple action, or another online judge.
- Adding another observation/progress manager or a global reader quota.
- Making two serial Jev requests, or globally lowering both head thresholds.

## Verification

Run generated observation code in a timer-free Node VM with the actual metadata builder;
check successful reads, optional capture errors, bounded timeout and cache/repair handoff.
Exercise independent threshold boundaries, legacy compatibility, fallback diagnostics,
local objective re-entry, initial versus new tabs, ACK search values and reader scope.
Run the surrounding browser/TaskTool suite and Swarm configuration tests with isolated logs.

Completed on 2026-10-09:

- SDK browser/TaskTool/affinity suite: 1,242 passed. Its 54 optional DOM cases were then
  run in the existing Swarm Playwright environment with an isolated headless Edge.
- Those 54 DOM cases and the new full Page + probe + timer-free Node VM integration
  case all passed (55 total). SDK total: 1,297 distinct passing cases. The new VM case
  verifies that successful observation returns before the timeout promise completes.
- Swarm configuration/resource tests: 27 passed, including both provider configurations.
- Actual SDK/Swarm Ruff checks and Git whitespace checks passed. Actual local config
  resolves to hybrid/OpenRouter, action 0.50 and target 0.60.

No model provider or user website was contacted. Test logs were isolated from the service.
Replaying only the confidence conditions on the saved 165 responses would add 11 concrete
candidates and exclude one previously accepted candidate (48 to at most 58); this is not
a prediction of useful dispatch, live acceptance or speed after the input changes.

## Limits

Confidence is not task accuracy. Offline checks verify contracts and routing, not the
proportion of useful live Jev decisions or task latency. LLM uptake of optional objectives
and website-specific observation quality require a fresh service run.
