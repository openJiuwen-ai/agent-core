# RSI Bounded Feedback Implementation Plan

**Goal:** Complete the user-approved output/validation/feedback loops without unbounded retries.

**Design:** Each stage has `output_repair.max_attempts=3` (including the initial attempt) and `max_stagnant_attempts=2`. Count unchanged error plus unchanged output/file state as stagnation. Keep transport failures separate from output correction. Preserve valid diagnoses; never infer semantic correctness by adding a missing boolean. Failed behavior observations are not reusable successes.

**Constraints:** Use the selected release-v0.1.20 workspace; preserve existing changes. No commits, service restart, paid experiments, formatters, static analysis, or added/run tests in this task (developer instruction). Existing finite agent tool iteration limits remain in force.

## Tasks

- [x] Add shared configurable feedback limits and progress tracking.
- [x] Repair Analyzer output incrementally, preserve valid diagnoses and raw attempts, distinguish schema from evidence conflict.
- [x] Bound Executor/Planner/attribution output correction and handle malformed nested structures.
- [x] Feed Repair no-change/transient failures back within its existing limit; retain scope enforcement.
- [x] Repair Behavior Check observations before caching; supply Judge with specific previous output errors.
- [x] Review configuration propagation and cancellation/permanent failure behavior; update Chinese/English docs.

## Decisions

- User approved the previously described fixes and explicitly requires a finite attempt limit; execute inline in the existing selected worktree.
- `max_attempts` includes the first attempt. Existing stage retry limits can further reduce the allowance; nested planning finalizers share the same allowance.
- Changes to quotes must be grounded in the task; validation reports field paths, without treating missing schema fields as proof of semantic contradiction.

## Completion record

- Implemented finite feedback loops, nested-budget sharing, partial diagnosis preservation, and successful-only behavior cache reuse.
- Froze API controls into new Swarm task profiles; retained existing Judge retry validation (maximum 6 attempts).
- Read-only independent review found permanent Analyzer failures being retried, exceptions containing JSON being misread as diagnoses, and a Judge legacy-cap mismatch. All were corrected. Exceptions never enter diagnosis parsing; accepted diagnoses require core fields.
- No tests, paid calls, formatters, static checks, service restarts, commits, or pushes were performed. Runtime behavior is not yet regression-tested.

## Follow-up: Judge runtime pre-parsing (rsi-f4b2a6b4)

- Evidence: the task's third baseline solver completed in 49 iterations. Judge returned 2009 tokens with finish_reason=stop and malformed score syntax (`0.85"`). run_judge_agent parsed before returning, so JSONDecodeError bypassed output capture/correction after one attempt.
- Fix: runtime returns raw text only; the assessment loop saves it, validates, and performs bounded feedback repair. Added grading_events.jsonl with links to actual saved outputs and explicit recovery/stop state.
- The original 3-attempt/stagnation budget remains unchanged. No tests or live experiment were run for this follow-up.
