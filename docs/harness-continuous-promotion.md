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

Outputs: `target/` and `full/` under each attempt, `single_harness_state.yaml` / `single_harness_report.yaml` for proofs,
per-case deltas, full-check outcomes and official Best references. Only packages
accepted by both stages and engineering checks can be published and installed.

No runtime tests, builds or real-model experiments were run for this revision.
