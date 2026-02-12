# openjiuwen.agent_evolving.constant

`openjiuwen.agent_evolving.constant` provides default values and valid ranges for hyperparameters used in self-evolving training.

---

## class openjiuwen.agent_evolving.constant.TuneConstant

Default hyperparameters and validation boundaries for single self-evolving training (dataclass style, used by Trainer, CaseLoader, Evaluator, etc.).

* **default_example_num**(int): Default number of examples per epoch. Default: `1`.
* **default_iteration_num**(int): Default number of training epochs. Default: `3`.
* **default_max_sampled_example_num**(int): Maximum number of sampled examples. Default: `10`.
* **default_parallel_num**(int): Default parallelism for inference/evaluation. Default: `1`.
* **default_max_num_sample_error_cases**(int): Maximum number of sampled error cases (for logging, etc.). Default: `10`.
* **default_early_stop_score**(float): Early stop score threshold, training stops when validation score reaches this value. Default: `1.0`.
* **min_iteration_num**(int): Minimum allowed iterations. Default: `1`.
* **max_iteration_num**(int): Maximum allowed iterations. Default: `20`.
* **min_parallel_num**(int): Minimum allowed parallelism. Default: `1`.
* **max_parallel_num**(int): Maximum allowed parallelism. Default: `20`.
* **min_example_num**(int): Minimum allowed examples. Default: `0`.
* **max_example_num**(int): Maximum allowed examples. Default: `20`.
