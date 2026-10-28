# Harness promotion configuration

New single-Harness runs use `continuous_two_stage_v2` by default, both when
constructed in Python and when loaded from YAML or the bundled template.
`promotion.policy_version` is the only runtime policy selector. Continuous
promotion also selects continuous evaluator scores.

```yaml
promotion:
  policy_version: continuous_two_stage_v2
  min_improvement: 0.05
  non_target_max_regression: 0.05
```

For an existing epoch run, explicitly select `legacy_epoch_v1`. The YAML loader
also accepts the old `scheduling.promotion_policy: epoch_full_evaluation` and
converts it to that legacy policy. It does not retain a second scheduling field.
Combining that old selector with an explicit continuous policy raises an error;
remove the old selector to create a continuous run. Direct Python callers should
pass `promotion=PromotionConfig(policy_version="legacy_epoch_v1")` instead of
passing a policy to `OrchestratorSchedulingConfig`.

Resuming a run with a different policy remains rejected. Keep the original
policy when resuming historical tasks; create a new task to change policies.
