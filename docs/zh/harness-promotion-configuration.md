# Harness 晋级策略配置

新建单 Harness 任务默认使用 `continuous_two_stage_v2`。Python 直接构造、YAML
加载及内置模板使用同一默认策略；连续晋级同时启用连续评测分数。
`promotion.policy_version` 是运行时唯一的策略选择字段。

```yaml
promotion:
  policy_version: continuous_two_stage_v2
  min_improvement: 0.05
  non_target_max_regression: 0.05
```

历史 epoch 任务应显式选择 `legacy_epoch_v1`。YAML 加载器仍接受旧配置
`scheduling.promotion_policy: epoch_full_evaluation`，并将其转换为 legacy 策略，
运行时不保留第二个策略字段。若旧字段与显式连续策略同时出现，加载器报错；
新建连续任务时应删除旧字段。Python 调用方使用
`promotion=PromotionConfig(policy_version="legacy_epoch_v1")` 指定旧策略，
不再向 `OrchestratorSchedulingConfig` 传递晋级策略。

恢复任务时仍拒绝策略变化。恢复历史任务应保留原策略；切换策略需创建新任务。
