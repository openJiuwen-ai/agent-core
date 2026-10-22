---
name: consult-reference-models
description: Seek independent candidate proposals for early approach selection, costly commitments, conflicting evidence, or stalled reasoning. Use when consult_reference_models is available and another perspective could materially change the next decision.
---

# Consult Reference Models

## Purpose

Reference models provide read-only candidate proposals. Identify the unresolved
choice and how another proposal could change your next action. You remain
responsible for choosing the approach, verifying claims, executing tools, and
completing the task.

## When to Consult

- **Early direction:** The task has a consequential choice of approach,
  decomposition, or diagnosis. With basic task context available, seek
  alternatives before committing to extensive implementation. Do not wait for
  a complete investigation or repeated failures.
- **Reassessment:** New evidence conflicts with the current explanation,
  competing approaches need comparison, or attempts no longer resolve the
  central uncertainty. Consult to find a better hypothesis or next step.
- **Before delivery:** Available checks leave a consequential inference,
  alternative explanation, or overlooked risk unresolved. Seek a critique of
  the reasoning; still perform the required tests and factual checks yourself.

Choose checkpoints from the task's needs. Do not consult automatically at
startup, after a fixed number of steps, or simply because a task is long.

## When to Continue Directly

Continue with the primary model when the task is simple, the next action is
clear, or available evidence already supports the decision. If a focused lookup
or test can resolve the uncertainty, do that first. References cannot supply a
missing user preference or authorization; use the normal clarification process.

Avoid repeating a consultation when the question and evidence have not changed.

## How to Call

1. Check that the preceding conversation or observed tool results contain enough
   of the goal, constraints, completed actions, and open decision for useful
   proposals. Gather key missing facts only when needed.
2. Complete at least one primary-model action turn, including its tool results,
   before consulting. Reading this skill through `skill_tool` can be that turn;
   do not add an otherwise useless action just to qualify.
3. Call `consult_reference_models` with `{}` as the only tool call in that turn.

The tool accepts no question or context arguments. References receive a bounded
snapshot of the preceding conversation and observed tool results. Text first
written alongside this tool call is not part of that snapshot. Do not manufacture
a message or tool call just to pass a question to the references. Do not assume
the full history, system instructions, or unread file and URL contents are
available to references. They cannot execute tools.

## How to Use the Result

The receipt reports response status and counts, not correctness or guaranteed
delivery. Available proposals are supplied temporarily to the next model
request, subject to its context budget. Use only proposals actually visible in
that request; do not assume they persist in conversation history.

Compare their evidence and assumptions against the task and observed results.
Resolve disagreements through reasoning and verification; agreement among
models is not evidence by itself. Adopt useful parts and reject unsupported
claims or actions already completed. You need not execute every proposal.

Use partial results without retrying to collect every reference. If consultation
fails or no proposal reaches the next request, continue using the existing
context. Consult again only when new information creates a meaningful need and
budget remains. Once the tool reports budget exhaustion, stop consulting for
the rest of the current outer agent invocation, including its continuing
task-loop turns.
