# RSI Reliability Implementation Plan

> **For agentic workers:** Use executing-plans or focused parallel implementers; preserve the shared file ownership below.

**Goal:** Repair the RSI optimization chain diagnosed in task rsi-3c09cbd6, including interactive author/repair agents, trustworthy runtime evidence, resilient judging, and configurable solver budgets.

**Architecture:** Keep the native Harness/Agent APIs and existing candidate gates. Equip author and repair agents with bounded filesystem tools and enforce declared writes in the controller. Capture execution evidence before interpreting capability use; preserve unknown when evidence is incomplete. Retry an entire unusable judge assessment while keeping format-only repair separate. Retain partial progress inside a batch without weakening final publication gates.

**Tech Stack:** Python 3.11+, uv, pytest, native DeepAgent rails/tools.

**Spec:** User-approved changes in this conversation; author and repair must be interactive tool agents, not one-shot file generators. Solver exhaustion itself is not a defect. Default solver iterations remain 100.

## Global Constraints

- Work only in agent-core/release-v0.1.20 and jiuwenswarm/0.2.8-2; both began clean.
- Do not change cached sources, installed application files, experiment artifacts, keys, or reference checkouts.
- Do not run formatters or static analysis. Do not commit or push without authorization.
- Add meaningful focused regression coverage and run affected suites; do not launch live paid experiments.
- Local openjiuwen dependency remains an uncommitted workspace change.

## Review Focus

- Missing or oversized evidence must never become proof of absent behavior or overwrite unseen file content.
- Tool-enabled authors/repairers must not write outside declared candidate paths or alter publication/audit files.
- Judge truncation, invalid JSON, unavailable repair, and cancellation must remain distinguishable and recoverable.
- Candidate changes must preserve final regression/retention gates; partial progress is batch-local only.
- Runtime iteration configuration must affect execution and evaluation identity consistently.

## Tasks

- [x] 1. Switch JiuwenSwarm openjiuwen sources to the specified editable workspace; lock/sync and confirm resolution. Owner: controller.
- [x] 2. Add native read/search/write/edit tools to action execution and repair; remove direct one-shot skill/tool path; preserve scoped writes and actual-change verification; include relevant Harness/host context. Owner: author worker (member_optimizer factory, executor, verification, prompts, associated tests).
- [x] 3. Repair Judge search sandbox compatibility, valid grading closeout, whole-assessment retry, and configured output budgets. Owner: judge worker (judger modules, member_optimizer/model_config.py, associated tests).
- [x] 4. Find and repair missing role trajectories; archive useful execution evidence, make capability observation complete/unknown explicit, and use configurable solver_max_iterations. Owner: trajectory worker (evaluator/case_backend.py, trajectory paths/runtime adapters/trajectory usage, associated tests). Controller owns iterative.py.
- [x] 5. Add solver_max_iterations configuration and tighten candidate timing/unknown semantics, partial progress continuation, diagnosis evidence validation, and rail contract integration. Owner: controller (config, single_harness, analyzer, integration tests).
- [x] 6. Update English/Chinese RSI docs, run affected regression suites and integration smoke checks, review all diffs, record any remaining limitations. Owner: controller.

## Progress

- Initial state: clean worktrees at JiuwenSwarm 013745314 and agent-core 073ece6ed; no local environment yet.
- Ruling: User has explicitly approved implementation in the specified worktrees; no new workspace or design approval is required.
- Ruling: Existing diagnostics are hypotheses; verify runtime producer failure and fix the cause rather than treating the unrelated online-training warning as evidence.

- Task 1 complete: JiuwenSwarm pyproject/uv.lock resolve openjiuwen editable to the specified release workspace; uv sync --check is clean in both environments.
- Task 2 complete: interactive scoped author/repair tools, no implicit shell, actual-change checks, missing-baseline rejection, repair no-progress stop, and invocation resource cleanup. Real mocked-transport read/edit/final-answer cycles verified.
- Task 3 complete: bounded native Judge search, assessment-level retries/closeout, explicit output-budget preservation, and completion diagnostics.
- Task 4 complete: native run root/capture regression reproduced missing archive before the fix and real LLM/tool steps afterward. Missing/truncated evidence is explicit; invocation/stream steering are isolated and forwarded.
- Task 5 complete: solver budget API/profile/runtime wiring, evidence-inconclusive primary/epoch handling, evidenced criterion-level batch progress, and task-clause citation checks.
- Task 6 complete: English/Chinese docs and final merged review. Independent review found missing stream forwarding, missing-quality handling, and epoch evidence handling; all three corrected with regression coverage. Remaining review was completed by the controller after workers became unavailable.
- Validation: agent-core `.venv/bin/python -m pytest tests/unit_tests/rsi tests/unit_tests/harness/test_single_round_steering.py tests/unit_tests/harness/test_deep_agent.py tests/unit_tests/harness/test_deep_agent_rail_event_routing.py -q --no-cov --tb=short` -> 2379 passed, 3 skipped, 6 warnings (68.68s), log /tmp/rsi-final-green.log.
- Validation: JiuwenSwarm `.venv/bin/python -m pytest tests/unit_tests/rsi -q --no-cov -W ignore::SyntaxWarning --tb=short` -> 254 passed, 1 warning (7.12s), log /tmp/swarm-rsi-final-regressions.log. The SyntaxWarning filter avoids an existing third-party pysbd import warning being promoted to SyntaxError by this repository's warning policy.
- Limitations: no live paid model run, desktop rebuild, commit, or push. Task-clause validation strengthens factual grounding but cannot prove arbitrary model-generated causal/mathematical claims. Judge search is bounded literal search, as documented. Existing experiment artifacts and installed application remain unchanged.
