# Two-stage continuous Harness promotion

New task profiles opt into `continuous_two_stage_v2`. The prior experimental
`continuous_margin_v1` policy is not silently migrated; start a new task.
Legacy epoch profiles retain their original grading protocol.

```yaml
promotion:
  policy_version: continuous_two_stage_v2
  min_improvement: 0.05
  non_target_max_regression: 0.05
evaluator:
  score_mode: continuous
```

LLM `case.score` is the trusted continuous rubric aggregate in [0,1]. Candidate
statistics and full/Best means use these values. `passed` is an informational
completion indicator; partial credit above or below that flag can be improved.

Each candidate uses one frozen per-case baseline from the official parent:

1. Target precheck: EVERY target must exceed its own baseline by MORE than 0.05.
   A target mean cannot compensate for a target failing this condition.
2. Full evaluation: rerun ALL cases, including targets. EVERY target must remain
   strictly above its baseline; EVERY non-target must be at least baseline minus
   0.05 (inclusive); the full mean must be strictly above the parent full mean.
   All three checks are required. The mean does not need another 0.05 gain.

There is no third evaluation and no target-result reuse in the full stage.
Existing engineering and semantic behavior checks remain. Missing/invalid scores
cannot be dropped or treated as valid zeroes. Same-content packages with the same
baseline and protocol share their recorded trial decision instead of redrawing.

Promotion makes the full evaluation's actual score vector the NEXT candidate's
baseline. A historical peak or target precheck score never replaces that vector.
Whole packages are accepted independently; epochs are scheduling budgets without
filtering and recombining provisional packages at the end.

Both candidate judge stages receive the SAME frozen same-case baseline answer,
artifacts, continuous score, criterion scores and reasons in a read-only snapshot.
Baseline grading details are inline; larger files have accessible inventory and
lossless pages. The full-stage judge does NOT see the candidate's target precheck
score. Baseline material never enters Solver inputs. Historical grades are
comparison evidence, not required scores or reference solutions. Missing or
changed recorded artifacts invalidate the comparison rather than silently grading
without baseline evidence.

Outputs: `target/` and `full/` under each attempt, `baseline_context.json` per
judge, and `single_harness_state.yaml` / `single_harness_report.yaml` for proofs,
per-case deltas, full-check outcomes and official Best references. Only packages
accepted by both stages and engineering checks can be published and installed.

No runtime tests, builds or real-model experiments were run for this revision.

Action Planner corrections have their own `member_optimizer.action_planning_max_attempts`
budget (default 10, including the initial draft). Parsing, schema, and semantic
feedback share this counter; the existing no-progress stop still applies.
Keep this budget identical across both ablation groups.

Planner issue membership is checked using `attributed_issue_ids` and action
dependencies. Descriptive text may compare or exclude another issue; mentioning
its ID does not expand the action's scope. Each action still declares exactly
one diagnosed issue. Exhausted parsing, schema, or plan validation feedback is
recorded as `planning_rejected`, allowing the run to continue with its unchanged
parent package. Runtime/configuration failures still propagate as errors.

Internal optimizer agents create only their working directory, without personal
workspace templates. In particular, Action Execution and Repair must not generate
`IDENTITY.md` or `SOUL.md` beside package files: these can be interpreted as
loadable prompt sidecars, including through case-insensitive filename matching.
Explicitly declared package identity/soul edits remain supported.

Restart both the service front and resident Runtime Worker after changing code;
an existing worker retains its already imported Planner configuration.

### Candidate registry validation and repair

RSI writes Prompt file references relative to the package root, for example
`prompt_sections/files/foo.md`, and Skill mounts as directories such as
`skills/foo`. Action execution and Repair share safe alias normalization and
conflict validation. Equivalent registrations collapse; conflicting same-name
Prompt registrations or Skill mount options require explicit correction.
Unrelated entries and metadata are preserved. Ambiguous, missing, absolute or
escaping paths are reported instead of guessed.

The verifier checks effective Prompt bindings with the native binder and an
isolated identity-bearing prompt builder; it does not run a case or candidate
hooks. Repair remains bounded and succeeds only after verification passes.
Generation failures retain their original status, errors and artifact paths as
`candidate_generation_failed` (or the explicit planning/skip reason). They do not
enter the valid-package trial cache or become `candidate_content_unchanged`.

### Unavailable diagnosis

An `AnalysisUnavailableError` is recorded at the attempt boundary instead of
aborting the experiment. Source diagnosis failures consume the existing finite
attempt budget; no candidate is generated from them. A rejected candidate whose
follow-up diagnosis is unavailable keeps its original score and rejection, plus
the diagnostic error, raw-attempt references and per-case failure details. It is
not retained as a local repair parent. Remaining attempts/cases continue from the
official parent Harness; neither scores nor promotion criteria are relaxed.

Reports expose `analysis_unavailable_count` and `analysis_failures`. Unavailable
diagnosis is never converted into a successful no-issues analysis. The legacy
epoch path skips the affected analysis batch and continues later batches.
