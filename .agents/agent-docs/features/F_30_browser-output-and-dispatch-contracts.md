---
id: F_30_browser-output-and-dispatch-contracts
title: Browser output limits and local dispatch contracts
type: F
author: ShowHsiang
author_email: xiangxiaoneng@h-partners.com
date: 2026-10-22
designs: []
design_note: Implements the approved multiversion analysis section 19 using existing browser contracts.
modules:
  - openjiuwen/harness
files:
  - openjiuwen/harness/subagents/browser_agent.py
  - openjiuwen/harness/tools/browser_move/decision/policy_model.py
  - openjiuwen/harness/tools/browser_move/playwright_runtime/runtime.py
  - openjiuwen/harness/tools/browser_move/playwright_runtime/phase_contract.py
  - openjiuwen/harness/tools/browser_move/playwright_runtime/policy_page_action.py
  - tests/unit_tests/harness/tools/browser_move/test_browser_contract_compatibility.py
---

# Browser output limits and local dispatch contracts

## Metadata

| Item | Value |
| --- | --- |
| Date | 2026-10-22 |
| Scope | Existing model failure, phase, primitive receipts and registered page capabilities |
| Specs | S_05, S_18 |
| Test baseline | AB15/36/37 output limit, AB38 unbound business button, AB12 zero match, AB1 missing tabs |
| Refs | Multiversion analysis section 19; no issue association requested |

## Background

Core now rejects output-limit responses before the successful after callback. The browser
exception path must consume the same structured output facts instead of calling this an
unknown non-recoverable request error. A visible control also does not establish which
business operation the LLM selected. Finally primitive batch receipts and page menus must
reflect the executor and actual registered capabilities, not just static tool permissions.

## Decisions and existing state

- Recognize core MODEL_CALL_FAILED with structured length/max_tokens details, including
  wrapped causes. Preserve output tokens and finish reason under the existing model_failure.
  Return model_output_incomplete with the existing facts and resume eligibility. Do not add
  an automatic model/write replay loop. Explicit resume uses the original deadline/count,
  with concise recovery guidance to use retained facts and continue only unexecuted work.
- Jev actions with an unknown/business effect require the existing active phase's exact
  target identity and allowed operation. No phase, targetless objective, or pending handoff
  cannot authorize them. Local UI/read capabilities retain their ordinary paths. This only
  limits Jev candidates; LLM tool execution keeps existing permission and target checks.
- Primitive execution uses the same exact native preflight classifier as native tools,
  before constructing its step receipt. Preserve explicit true/unknown when dispatch may
  have occurred. Compact receipts and arbitrary scripts retain their boundaries.
- Page operations intersect permissions with the existing MCP registry for this runtime's
  server identity. Fixed readers/scroll depend on their actual compact transport; a bounded
  wait is local. Native tools are resolved before mark_dispatched. Missing capabilities
  return a specific undispatched error. There is no second registry or polling service.
  Electron's embedded backend currently omits browser_tabs, so tabs/select_tab are absent
  there. A Chrome backend with browser_tabs registered and permitted keeps both operations.
  Registration changes are read directly on the next capability check; capabilities from
  another browser server cannot leak into this runtime. No packaging-name switch is needed.
- Bound controls excluded by eligibility get bounded target-specific diagnostics. A phase
  binding is not proof of actionability; missing identity still needs the existing probe.

## Rejected alternatives

No site/button-name blacklist, business specification checker, same-name rebinding,
global Jev shutdown, new recovery manager, extra retry budget, 60-second whole-stream cap,
or increase to the 8192/default task/action budgets. No blanket zero-match exemption for
arbitrary scripts. Never interpret a registered capability as user authorization.

## Validation

Regression coverage uses real ReActAgent output validation, rail callbacks and Session
state; verifies retained ACK/raw observations, deadline/resume exhaustion and successful
explicit object rebinding. Native/primitive/compact preflight failures are compared with
post-dispatch errors. Missing/reappearing/denied tools and isolated runtime registries
exercise menu and page-tool consistency without provider or shopping traffic.

Final validation on 2026-10-22:

- SDK browser regression directory plus browser result delivery: **1,576 passed**,
  no skips, 136.60 seconds. Node and local Chromium were available for generated
  executor and local DOM tests. Two existing Pydantic deprecation warnings remain.
- JiuwenSwarm SDK contract, Electron target routing/fallback and browser subagent
  integration: **34 passed, 1 skipped**, 38.19 seconds. The skipped test already
  carries the repository's flaky-browser-integration skip marker.
- Additional focused lifecycle, receipt and capability runs passed; they overlap the
  full SDK run and are not added to its count. The full run includes the corrected
  unknown-effect fixture with an explicit selected target, so the unknown-write
  guard is tested independently of the new missing-binding filter.

Reproduce the SDK scope from the repository root:

```bash
python -m pytest tests/unit_tests/harness/tools/browser_move \
  tests/unit_tests/harness/rails/test_browser_result_delivery.py -o addopts= -q
```

These are offline contracts and local DOM checks. The Electron/Chrome capability
split is covered through isolated registered-tool inventories and routing tests,
not a live packaged application or supplier session. Real-site endpoint quality
and provider output-budget calibration remain subsequent acceptance work.

## Known limits

These fixes do not prove a supplier's optimal output budget, model business reasoning or
live website completion. Generic unknown controls require LLM selection, which may reduce
raw acceptance while preventing unbound mutations. A missing node identity or disabled
control is reported, not guessed into an executable binding.

## Release workspace validation (2026-10-27)

The local uncommitted implementation was copied from develop to the isolated
`release/v0.1.20` workspace at `d3ccf8ab3`, used by the JiuwenSwarm `0.2.8`
environment at `916cf174b`. The original develop worktree remains unchanged.
This is a local test setup only; no commit or push was made.

The release baseline lacked these prerequisites, so their changes were also
applied without creating commits:

- `2a2f39869`: retained browser facts and local decision continuity.
- `13e6f5087`: browser handoff and shared-state regression tests.
- `3fdf76460`: corresponding browser contracts and documentation.
- `5f017e34d`: the existing core structured output-limit error and its tests.

The initial release run exposed nine failures because the core output-limit
contract was missing. After applying that dependency, the browser regression
directory, result delivery, ReActAgent, core single-agent and model anomaly
rail suites passed together: **1,983 passed**, no skips, 141.06 seconds.
Generated JavaScript and DOM tests used Node 24 and local headless Edge.
Existing deprecation and serialization warnings remain. No live provider
or shopping task was executed by these tests.

JiuwenSwarm Electron contracts, SDK runtime compatibility, decision settings
and browser subagent integration: **97 passed, 1 skipped**, 19.88 seconds.
The skip is the existing flaky-browser integration marker. Electron browser
panel, MCP ownership and target acquisition tests: **6 passed**.

The Swarm environment loads this release SDK as an editable local dependency,
not the uncommitted `D:/agent-core` develop checkout. No unrelated develop
commits or document-layout migration were applied to the release workspace.
