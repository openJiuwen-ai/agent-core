---
id: B_20261102_1733_browser-pr-contract-review
title: Browser PR budget and exception contract corrections
type: B
author: ShowHsiang
author_email: xiangxiaoneng@h-partners.com
modules:
  - openjiuwen/harness/subagents
  - openjiuwen/harness/tools/subagent
  - openjiuwen/harness/tools/browser_move/decision
files:
  - openjiuwen/harness/subagents/browser_agent.py
  - openjiuwen/harness/tools/subagent/task_tool.py
  - tests/unit_tests/harness/test_deep_agent_terminal_stream.py
  - tests/unit_tests/harness/tools/browser_move/test_browser_fact_handoff.py
  - tests/unit_tests/harness/tools/browser_move/test_browser_shared_state_delivery.py
  - tests/unit_tests/harness/tools/browser_move/test_browser_local_intent_readers.py
designs: []
design_note: Local corrections to the existing explicit-budget and fact-retention contracts; no new architecture or feature.
---

# Browser PR review corrections

## Findings and scope

PR #2386 (Refs: #2385) review was checked against `31ef2eb5e`. Two findings are reproducible;
the suggested missing-node-guard KeyError is not reachable for the reported inputs.

- `build_menu` already rejects a missing, null or empty node guard before entering
  the ARIA checkbox/switch branch. Six negative cases now lock this boundary. The
  guarded checked-state lookup and production menu are unchanged.
- The browser model clone only checked top-level output-limit aliases. A limit
  supplied in `extra_body` still received a default top-level `max_tokens`, including
  when a reasoning budget would raise that default. This violates explicit-limit
  precedence before a provider request is even constructed.
- TaskTool retained facts after an unexpected child execution exception, but returned
  `ToolOutput(success=True)`. Its custom renderer also omitted the tool failure flag.
  A saved partial task result is useful evidence, not successful execution of the tool.

## Contract and decisions

This corrects the existing [F_26 fact-retention](../../features/harness/F_26_browser-fact-handoff-and-stream-diagnostics.md)
and [F_29 explicit-output-budget](../../features/harness/F_29_browser-shared-state-and-source-delivery.md) decisions;
it does not replace their deadline, resume or observation policies. Current contracts
are maintained in [S_05_tools-contract](../../specs/harness/S_05_tools-contract.md) and
[S_18_subagents-and-lifecycle](../../specs/harness/S_18_subagents-and-lifecycle.md).

The browser clone checks all three existing aliases in the request and its `extra_body`
mapping. Any non-null limit prevents default injection. No supplied carrier is rewritten,
no parent descriptor is mutated, and no conflicting caller configuration is guessed away.

An unknown exception caught at the TaskTool execution boundary produces a failed
ToolOutput with retained data. The same journal, sources and current page remain in the parent
query state. The renderer includes success/error alongside those facts, and the exception
boundary logs the traceback. Classified transport/repeated-output failures keep their
existing partial-result and recovery handling. Unknown errors do not gain a retry;
ACKed or uncertain actions are never replayed automatically.

Discarding facts and returning only an exception string was rejected because it would
reintroduce the earlier lost-ACK/source failure. Treating every partial task as a tool
failure was also rejected: task progress and execution-stack failure are distinct.
No new manager, action gate, model call or budget increase is introduced.

## Regression evidence

- Before the fix: targeted tests produced 8 failures and 17 passes. Four cases showed
  unwanted top-level budget injection; three public `TaskTool.invoke` cases and one
  setup-result case showed false success. The missing-node-guard cases already passed.
- After the initial fix: the same targeted selection passed all 25 cases.
- Tests use the actual model request config, TaskTool invocation/renderer, and real
  Session persistence for unknown execution failures. Controlled child failures occur
  after seeded ACK/source facts; they do not claim a real provider or website replay.
- Full browser, TaskTool, renderer and lifecycle regression: 1,728 passed, 0 failed,
  0 skipped in 261.74 seconds; two existing Pydantic deprecation warnings. This runs
  the previous 63-file PR regression plus TaskTool, KV-cache affinity and renderer tests,
  using the existing Python environment, Node and local headless Edge. No provider calls.

The regression checks preserve typed facts in both the returned body and persisted
query, deny unsupported resume, run cleanup once, and distinguish known transport
interruption from unknown implementation/authentication errors. Existing live-guard,
native dispatch and ordinary subagent tests remain in the regression scope.

## CI follow-up: preserve finalized stream delivery

The Linux/Python 3.11.5 CI report for `798a16bf8` recorded 24,973 passes, one failure,
481 skips and two expected failures. The failing case was
`test_deep_agent_terminal_stream.py::test_real_browser_rail_stream_reaches_tasktool[provider_error-False]`.
The same failure reproduced locally with 54 other related cases passing.

The first implementation inferred tool failure from `terminal_reason` inside the common
result builder. Runtime's existing stream finalizer also uses `browser_execution_error`
for a normally delivered structured blocked result, so the inference was too broad.
This is a regression from this fix, not a CI platform issue or a real provider outage.
The earlier 1,728-case local selection omitted this real DeepAgent stream integration test.

Move the tool failure flag to the actual caught-unknown-exception branch. Keep the
renderer, retained facts and retry limits; do not weaken the CI assertion or expand
provider-error keywords to hide this boundary mistake. Regression coverage now includes
the real DeepAgent/Runtime/TaskTool terminal stream and both invoke-only and streaming
children that raise an unhandled exception. The setup-failure test goes through public
TaskTool.invoke and a valid parent-scoped resume ID instead of calling the result builder.

Follow-up regression: all 1,755 cases passed, with no failures or skips, in 194.64
seconds across 67 test files. This includes the unchanged CI success assertion,
the previous browser/TaskTool/lifecycle selection and the real terminal stream
integration module. Two existing Pydantic deprecation warnings remain. The complete
repository CI suite was not rerun locally.

## Limits

No model provider traffic or production browser actions are performed. Provider-specific
request merge precedence is not redefined; the browser clone simply stops injecting a
second output limit when one already exists. Local formatters/static analyzers are not
run under the repository instructions; remote CI is separate evidence.
