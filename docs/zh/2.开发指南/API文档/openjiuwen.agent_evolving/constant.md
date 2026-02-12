# openjiuwen.agent_evolving.constant

`openjiuwen.agent_evolving.constant` 提供自演进训练所用的超参默认值与合法取值范围。

---

## class openjiuwen.agent_evolving.constant.TuneConstant

单次自演进训练的超参默认值与校验边界（数据类风格，用于 Trainer、CaseLoader、Evaluator 等）。

* **default_example_num**(int)：每轮示例数量默认值。默认值：`1`。
* **default_iteration_num**(int)：默认训练轮数。默认值：`3`。
* **default_max_sampled_example_num**(int)：最大采样示例数。默认值：`10`。
* **default_parallel_num**(int)：推理/评估默认并行数。默认值：`1`。
* **default_max_num_sample_error_cases**(int)：最多采样的错误样本数（用于日志等）。默认值：`10`。
* **default_early_stop_score**(float)：早停分数阈值，验证分数达到该值即停止。默认值：`1.0`。
* **min_iteration_num**(int)：允许的最小迭代轮数。默认值：`1`。
* **max_iteration_num**(int)：允许的最大迭代轮数。默认值：`20`。
* **min_parallel_num**(int)：允许的最小并行数。默认值：`1`。
* **max_parallel_num**(int)：允许的最大并行数。默认值：`20`。
* **min_example_num**(int)：允许的最小示例数。默认值：`0`。
* **max_example_num**(int)：允许的最大示例数。默认值：`20`。
