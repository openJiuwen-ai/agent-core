---
id: F_20261029_1852_optional-handoff-and-resume
title: Optional browser handoff recovery and invocation-exit lifecycle
type: F
author: ShowHsiang
author_email: xiangxiaoneng@h-partners.com
date: 2026-10-29
designs: []
design_note: Implements the approved consolidated multiversion report section 22 using existing browser contracts.
modules:
  - openjiuwen/harness/subagents
  - openjiuwen/harness/tools/browser_move
files:
  - openjiuwen/harness/subagents/browser_agent.py
  - openjiuwen/harness/tools/browser_move/decision/policy_model.py
  - openjiuwen/harness/tools/browser_move/playwright_runtime/phase_contract.py
  - openjiuwen/harness/tools/browser_move/playwright_runtime/runtime.py
  - openjiuwen/harness/tools/browser_move/controllers/action.py
  - openjiuwen/harness/tools/browser_move/playwright_runtime/execution_journal.py
  - openjiuwen/harness/tools/browser_move/playwright_runtime/browser_logging.py
  - tests/unit_tests/harness/tools/browser_move/test_browser_optional_handoff_recovery.py
  - tests/unit_tests/harness/tools/browser_move/test_browser_receipt_diagnostics.py
  - tests/unit_tests/harness/tools/browser_move/test_browser_decision_observation_handoff.py
---

# Optional browser handoff recovery and invocation-exit lifecycle

## Baseline and scope

Changes are based on the service's `release/v0.1.20` worktree, including its existing
uncommitted F26–F30 changes. This is not a transplant from the develop checkout.
The release worktree retains its existing `openjiuwen/harness/docs` layout; this change
does not import develop's document-layout migration. No issue association, commit or
push is included in this task.

The primary evidence is the consolidated report section 22: AC31/AE7/AF2–4 repeated
invalid phase setup; AC32/AE8 successful resumes retaining a consumed iteration exit;
AC20/AE20 submit timeouts with insufficient per-step diagnostics. Model mistakes,
website obstacles and absent transport receipts remain distinct from code defects.

## Decisions

1. **Shrink the common handoff rather than teach a larger management protocol.**
   The model-facing phase card has only op, objective, operations, current target IDs,
   input bindings and resolved read queries. Advanced conditions/cart verification stay
   callable under the original strict schema for compatibility, but are not advertised
   in every model request. Ordinary search/read/clear steps use ordinary tools directly.
   Browser system prompts repeat this once, instead of asking for phase before inspection,
   after uncertainty, during recovery and before each selected-object continuation.
2. **Do not promote unrelated candidate exclusions into a task prerequisite.**
   `local_target_binding_required` remains a dispatch boundary for the affected Jev
   business control. Its mere presence no longer overrides reader, stale-observation or
   other actual fallback reasons. The LLM can inspect or execute ordinary tools; exact
   current object/operation binding still governs delegation of business effects to Jev.
3. **End consecutive failed management work using existing facilities.**
   Runtime reuses the anomaly rail's completed tool-round parser. Two completed phase
   rounds with executed=false and an error omit phase for the next model request and
   request ordinary-tool recovery. This also covers varying invalid set parameters.
   Four consecutive rejected rounds, if the model ignores recovery, use the existing
   model-tool-protocol terminal result. No new persisted counter or recovery manager is
   introduced. Ordinary work, a new user input, successful phase or unknown read breaks
   the streak. A corrected phase after fresh probing is available again. The directive
   uses the existing prompt builder, since core rebuilds the final model window after
   before-model callbacks; modifying only the preview messages would lose the guidance.
4. **Retire invocation exits only after existing resume authorization succeeds.**
   `max_iterations_reached` is consumed with the previous invocation, while original
   deadlines, resume count, journal, raw observations and unresolved effects survive.
   A current task deadline or live non-resumable obstacle is never cleared by this fix.
5. **Improve returned tool facts without inferring unobserved dispatch.**
   Compact step receipts preserve target-preflight/action-attempt/ACK diagnostics and
   explicit-target versus active-element keyboard binding. Available implicit focus
   metadata contains structural attributes only and has a 250 ms best-effort bound using
   the existing Playwright sleep primitive, without VM-global timer dependencies. Journal and compact output keep these
   details; they never grant a target identity or classify a write as safe. Outer RPC
   timeout still means step receipts are unavailable and execution can remain unknown.
6. **Keep observation and model responsibilities separate.**
   Worker guidance preserves selected-variant facts, dialog-versus-confirmation and time
   order. Existing fixed-reader ranges, nearby controls, source regions, raw text/offload,
   targeted probes and ordinary LLM fallback remain the recovery paths. No new cart-row,
   SKU, weather-widget or sorting certification is added. Source preferences remain
   preferences under the user's actual constraints.
7. **Preserve diagnostic logs.**
   Dedicated browser files append on reopen; changing or disabling the configured path
   closes the old owned handler. Tests use a separate path. Rotation remains with the
   existing host logging/deployment policy; no multi-process rotating-file manager is added.

## Reused contracts and rejected alternatives

Keep shared PageState/journal, exact generation/node checks, registered backend capability
intersection, selected-object boundaries, unknown-effect protection, first/idle waits,
task deadline and finite model/resume recovery. Keep 8192/default output and existing
action/iteration/task budgets unchanged. Do not alter Jev confidence thresholds.

Output-limit recovery already returns retained facts and recommends concise synthesis or
only unfinished work under the original deadline. This iteration verifies that path rather
than adding an automatic model retry or replaying ACKed webpage mutations. Scope-aware reader
reuse remains separate from fresh executable identity. Dynamic text and asynchronous updates
retain explicit LLM re-read paths; no universal read quota or site-specific regex is introduced.

## Verification

Targeted regressions use real StateCollection copy/merge persistence, real tool call and
message types, actual Runtime denial messages, the runtime before-model entry and core's
final context-window rebuild, production generated executor JS, shared journal and real
file handlers. The final schema audit uses the frozen AF malformed request and the actual
model-facing tool card, without a provider call.

| Validation | Result |
|---|---|
| Complete browser suite plus browser delivery, tool-loop, anomaly/retry and actual core output-error tests | 1,657 passed, 0 failed, 0 skipped |
| Final affected-area rerun after receipt-stage/focus-bound and Chinese prompt cleanup, including browser factory | 219 passed, 0 failed, 0 skipped |
| Common phase description | 3,458 → 810 characters |
| Common phase schema JSON | 3,970 → 1,391 characters |
| Browser system prompt | EN 10,759 → 8,998; CN 4,019 → 3,208 characters |

The two runs overlap; their counts must not be added as unique tests. Both reported only
two existing Pydantic configuration-deprecation warnings. An earlier broad run had one
assertion for the superseded phase-first recovery wording; the test now checks direct-tool
recovery and still verifies optional objective updates permit Jev re-entry. No runtime
assertion was removed to obtain a passing result.

Local evidence lives under `.tmp/jev-section22-20261029/` in the JiuwenSwarm workspace:
`regression-final.xml/.txt`, `targeted-final.xml/.txt`, `input-audit.json` and `input_audit.py`.
The test environment uses the release source via explicit PYTHONPATH and an isolated browser
log path; Node and local headless Edge tests use fixtures, not real shopping pages. No
formatting/static-analysis tools were run. Existing uncommitted work is retained; this
task did not modify the develop checkout, Swarm frontend, SDK dependency pin or service process.

Checks cover invalid/mixed set shapes, re-entry after a real tool observation, successful
binding, unknown verification, retained ACKs, real unknown writes, expired deadlines,
non-resumable CAPTCHA blockers, implicit/explicit Enter and later-step failure.
Slow optional focus inspection cannot prevent the actual keyboard step from being attempted;
no field value is emitted in focus diagnostics. A successful read/wait receipt is marked as
step ACK rather than retaining its earlier attempt-stage label.

No live supplier requests or real shopping actions are used. Input/schema reduction is
measurable offline; it is not evidence of a particular Jev accuracy or latency improvement.

## Remaining acceptance boundaries

- Re-run AC31/AE7/AF2–4 shapes on the actual model: a protocol repair cannot guarantee that
  a model will interpret a user objective correctly. It does bound consecutive unperformed
  phase work and offers normal tools before terminal fallback.
- Partial transport receipts cannot be recovered from a compact RPC whose response never
  arrives. Per-step diagnostics improve returned receipts; they are not an out-of-band ACK
  channel and do not prove which step timed out in the historical AC/AE calls.
- Fixed card extraction is heuristic. Existing range/region/nearby-control facts do not
  make every cart row, selected SKU or discounted price correct. Use original text/current
  controls for the actual gap; no broader semantic classifier was justified here.
- Keep fixed real menus when evaluating Jev and separate reader versus business actions,
  effective versus mistaken actions, task endpoint, LLM/token and elapsed cost. Supplier
  A/B and optimal output-budget selection still need controlled live-model evaluation.

## Develop integration (2026-11-02)

At the user's request, the tested browser changes are integrated into the existing
fork develop branch, without creating a topic branch or changing the running service.
The release-only core output-limit delta is already present in develop. The seven
section-22 implementation files matched their saved pre-change images before the
transplant; the other browser prerequisites are already committed or retained local
F30 changes. Official develop framework updates are preserved.

The original release implementation and validation above remain historical evidence.
Develop uses the migrated `.agents/agent-docs` layout; this feature and F30 now use its
current location, while S_05/S_18 and English/Chinese API docs retain develop additions.
Unrelated proposal documents, frontend/Electron changes, local dependency overrides,
raw logs and user test reports are outside this delivery.

Develop integration regression: **1,658 passed, 0 failed, 0 skipped** in 198.80 seconds
across 63 files. It includes the complete browser directory and current develop model
anomaly/factory, retry lifecycle, tool-loop, result-delivery and core output-error tests.
One release-only client-retry test file is not present in develop and was not transplanted;
the current develop anomaly/factory tests are used instead. The two existing Pydantic
configuration deprecation warnings remain.

Tests used the project Python environment with explicit develop PYTHONPATH, bundled Node,
local headless Edge and isolated temporary/log paths. An initial sandbox run against the
release snapshot encountered temporary-file replacement permissions and missing Chromium;
it was not counted as validation. The develop run above completed all applicable tests
without those environment errors. No provider requests or real shopping actions were made.
No formatter or static-analysis tool was run. Delivery evidence is stored locally under
`.tmp/jev-push-20261102/` in the Swarm workspace; raw logs are excluded from commits.

## Develop PR extraction (2026-11-02)

Issue: https://github.com/openJiuwen-ai/agent-core/issues/2385.

The isolated PR branch starts from official develop `da6045f3b`, after the official
release integration. It carries only the two unmerged browser increments: the
implementation/test/document commits from October 22 and November 2. Earlier scoped
Jev observation and recovery fixes already integrated through release are not
resubmitted. Only document-location and generated-index conflicts needed resolution;
the browser implementation and associated tests match the fork candidate above.

Regression rerun on this extracted branch: **1,658 passed, 0 failed, 0 skipped** in
259.27 seconds across the same 63 files. The two existing Pydantic deprecation warnings
remain. Tests used local headless Edge, isolated browser logs and the existing Python
environment; no live supplier or shopping requests were made. Formatting and static
analysis were not run. Local source worktrees, SDK dependency overrides and unrelated
uncommitted changes are outside this PR.
