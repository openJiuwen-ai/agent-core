# openjiuwen.dev_tools.agentrl.config

`openjiuwen.dev_tools.agentrl.config` is the **configuration layer** for agentrl, providing Pydantic configuration schemas. Configuration classes are defined in the `config.schemas` module; import from it:

```python
from openjiuwen.dev_tools.agentrl.config.schemas import (
    RLConfig,
    TrainingConfig,
    RolloutConfig,
    AgentRuntimeConfig,
    PersistenceConfig,
    AdaConfig,
)
```

---

## class openjiuwen.dev_tools.agentrl.config.schemas.PersistenceConfig

```
class openjiuwen.dev_tools.agentrl.config.schemas.PersistenceConfig(enabled: bool = False, save_path: str = None, flush_interval: int = 100, save_rollouts: bool = True, save_step_summaries: bool = True)
```

Rollout persistence configuration.

**Parameters**:

* **enabled**(bool, optional): Whether to enable persistence. Default: `False`.
* **save_path**(str, optional): Persistence save path. Default: `None`.
* **flush_interval**(int, optional): Flush interval (steps). Default: `100`.
* **save_rollouts**(bool, optional): Whether to save rollouts. Default: `True`.
* **save_step_summaries**(bool, optional): Whether to save step summaries. Default: `True`.

---

## class openjiuwen.dev_tools.agentrl.config.schemas.TrainingConfig

```
class openjiuwen.dev_tools.agentrl.config.schemas.TrainingConfig(project_name: str = "OpenJiuwenAgentRL", experiment_name: str = "grpo_experiment", train_data_path: Optional[str] = None, val_data_path: Optional[str] = None, train_files: Optional[str] = None, val_files: Optional[str] = None, model_path: str = None, save_path: str = None, algorithm_adv_estimator: str = "grpo", algorithm_use_kl_in_reward: bool = False, algorithm_filter_groups: bool = False, algorithm_norm_adv_by_std_in_grpo: bool = True, whole_trajectory: bool = False, epoch_num: int = 2, total_epochs: int = 2, save_freq: int = 20, test_freq: int = 20, train_batch_size: int = 32, rollout_concurrency: int = 40, visible_device: str = "0,1,2,3", nnodes: int = 1, n_gpus_per_node: int = 4, micro_batch_size_per_gpu: int = 4, max_prompt_length: int = 3072, max_response_length: int = 3072, truncation: str = "truncate", val_before_train: bool = True, critic_warmup: int = 0, logger: List[str] = ["tensorboard"], log_rollout_details: bool = True, log_reward_distribution: bool = False, verl_extra: Dict[str, Any] = {}, verl_config_path: Optional[str] = None)
```

Training configuration covering data, model, algorithm, and Verl trainer parameters.

**Parameters**:

* **project_name**(str, optional): Project name. Default: `"OpenJiuwenAgentRL"`.
* **experiment_name**(str, optional): Experiment name. Default: `"grpo_experiment"`.
* **train_data_path**(str, optional): Training data path. Default: `None`.
* **val_data_path**(str, optional): Validation data path. Default: `None`.
* **train_files**(str, optional): Training file path (backward-compatible alias). Default: `None`.
* **val_files**(str, optional): Validation file path (backward-compatible alias). Default: `None`.
* **model_path**(str, optional): Model path. Default: `None`.
* **save_path**(str, optional): Save path. Default: `None`.
* **algorithm_adv_estimator**(str, optional): Advantage estimator algorithm. Default: `"grpo"`.
* **algorithm_use_kl_in_reward**(bool, optional): Whether to use KL divergence in reward. Default: `False`.
* **algorithm_filter_groups**(bool, optional): Whether to filter groups. Default: `False`.
* **algorithm_norm_adv_by_std_in_grpo**(bool, optional): Whether to normalize advantages by std in GRPO. Default: `True`.
* **whole_trajectory**(bool, optional): Whether to use full trajectory mode. Default: `False`.
* **epoch_num**(int, optional): Number of training epochs. Default: `2`.
* **total_epochs**(int, optional): Total training epochs (alias). Default: `2`.
* **save_freq**(int, optional): Save frequency (steps). Default: `20`.
* **test_freq**(int, optional): Test frequency (steps). Default: `20`.
* **train_batch_size**(int, optional): Training batch size. Default: `32`.
* **rollout_concurrency**(int, optional): Rollout concurrency. Default: `40`.
* **visible_device**(str, optional): Visible GPU devices. Default: `"0,1,2,3"`.
* **nnodes**(int, optional): Number of nodes. Default: `1`.
* **n_gpus_per_node**(int, optional): GPUs per node. Default: `4`.
* **micro_batch_size_per_gpu**(int, optional): Micro batch size per GPU. Default: `4`.
* **max_prompt_length**(int, optional): Max prompt length. Default: `3072`.
* **max_response_length**(int, optional): Max response length. Default: `3072`.
* **truncation**(str, optional): Truncation strategy. Default: `"truncate"`.
* **val_before_train**(bool, optional): Whether to validate before training. Default: `True`.
* **critic_warmup**(int, optional): Critic warmup steps. Default: `0`.
* **logger**(List[str], optional): Logging backend list. Default: `["tensorboard"]`.
* **log_rollout_details**(bool, optional): Whether to log rollout details. Default: `True`.
* **log_reward_distribution**(bool, optional): Whether to log reward distribution. Default: `False`.
* **verl_extra**(Dict[str, Any], optional): Verl config extra overrides. Default: `{}`.
* **verl_config_path**(Optional[str], optional): Verl config file path. Default: `None`.

### property resolved_train_files(self) -> Optional[str]

Returns training data path, preferring train_files over train_data_path.

### property resolved_val_files(self) -> Optional[str]

Returns validation data path, preferring val_files over val_data_path.

---

## class openjiuwen.dev_tools.agentrl.config.schemas.RolloutConfig

```
class openjiuwen.dev_tools.agentrl.config.schemas.RolloutConfig(actor_optimizer_lr: float = 1e-6, actor_use_kl_loss: bool = False, actor_kl_loss_coef: float = 0.02, actor_entropy_coef: float = 0.0, actor_clip_ratio_low: float = 0.2, actor_clip_ratio_high: float = 0.3, actor_loss_agg_mode: str = "seq-mean-token-mean", rollout_n: int = 8)
```

Rollout / Actor optimizer configuration.

**Parameters**:

* **actor_optimizer_lr**(float, optional): Actor optimizer learning rate. Default: `1e-6`.
* **actor_use_kl_loss**(bool, optional): Whether to use KL loss. Default: `False`.
* **actor_kl_loss_coef**(float, optional): Actor KL loss coefficient. Default: `0.02`.
* **actor_entropy_coef**(float, optional): Actor entropy coefficient. Default: `0.0`.
* **actor_clip_ratio_low**(float, optional): Actor clip ratio lower bound. Default: `0.2`.
* **actor_clip_ratio_high**(float, optional): Actor clip ratio upper bound. Default: `0.3`.
* **actor_loss_agg_mode**(str, optional): Actor loss aggregation mode. Default: `"seq-mean-token-mean"`.
* **rollout_n**(int, optional): Rollout sample count. Default: `8`.

Multi-round rollout limits are **not** configured on `RolloutConfig`. When Ada is enabled, use `RLConfig.ada` (see [AdaConfig](#class-openjiuwendev_toolsagentrlconfigschemasadaconfig)) for `rollout_max_round` and related fields.

---

## class openjiuwen.dev_tools.agentrl.config.schemas.AgentRuntimeConfig

```
class openjiuwen.dev_tools.agentrl.config.schemas.AgentRuntimeConfig(system_prompt: Any = "You are a helpful assistant.", temperature: float = 0.7, top_p: float = 0.9, max_new_tokens: int = 512, presence_penalty: float = 0.0, frequency_penalty: float = 0.0)
```

Agent runtime hyperparameters.

**Parameters**:

* **system_prompt**(Any, optional): System prompt. Default: `"You are a helpful assistant."`.
* **temperature**(float, optional): Sampling temperature. Default: `0.7`.
* **top_p**(float, optional): Top-p sampling parameter. Default: `0.9`.
* **max_new_tokens**(int, optional): Max new tokens. Default: `512`.
* **presence_penalty**(float, optional): Presence penalty. Default: `0.0`.
* **frequency_penalty**(float, optional): Frequency penalty. Default: `0.0`.

---

## class openjiuwen.dev_tools.agentrl.config.schemas.AdaConfig

```
class openjiuwen.dev_tools.agentrl.config.schemas.AdaConfig(rollout_max_round: int = 2, final_keep_per_prompt: int = 8)
```

Additional parameters for Ada rollout variant.

Ada is enabled when `RLConfig.ada` is provided (not None). Multi-round rollout limits and Ada sampling are wired through Hydra (`trainer.rollout_max_round`, `JiuwenRL` custom hooks), not through `RolloutConfig`.

**Parameters**:

* **rollout_max_round**(int, optional): Rollout max rounds. Default: `2`.
* **final_keep_per_prompt**(int, optional): Final keep count per prompt. Default: `8`.

---

## class openjiuwen.dev_tools.agentrl.config.schemas.RLConfig

```
class openjiuwen.dev_tools.agentrl.config.schemas.RLConfig(training: TrainingConfig, rollout: RolloutConfig = RolloutConfig(), runtime: AgentRuntimeConfig = AgentRuntimeConfig(), persistence: PersistenceConfig = PersistenceConfig(), ada: Optional[AdaConfig] = None)
```

Top-level RL configuration.

**Parameters**:

* **training**(TrainingConfig): Training configuration (required).
* **rollout**(RolloutConfig, optional): Rollout configuration. Default: `RolloutConfig()`.
* **runtime**(AgentRuntimeConfig, optional): Runtime configuration. Default: `AgentRuntimeConfig()`.
* **persistence**(PersistenceConfig, optional): Persistence configuration. Default: `PersistenceConfig()`.
* **ada**(Optional[AdaConfig], optional): Ada configuration. If provided, Ada rollout variant is enabled. Default: `None`.
