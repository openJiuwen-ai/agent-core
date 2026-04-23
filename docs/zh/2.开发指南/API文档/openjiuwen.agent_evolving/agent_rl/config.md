# openjiuwen.agent_evolving.agent_rl.config

`openjiuwen.agent_evolving.agent_rl.config` 是 agentrl 的**配置层**，提供 Pydantic 配置 schema。配置类定义于 `config.offline_config` 模块，请从该模块导入：

```python
from openjiuwen.agent_evolving.agent_rl.config.offline_config import (
    RLConfig,
    TrainingConfig,
    RolloutConfig,
    AgentRuntimeConfig,
    PersistenceConfig,
    AdaConfig,
)
```

## class openjiuwen.agent_evolving.agent_rl.config.offline_config.PersistenceConfig

```python
class openjiuwen.agent_evolving.agent_rl.config.offline_config.PersistenceConfig(enabled: bool = False, save_path: str = None, flush_interval: int = 100, save_rollouts: bool = True, save_step_summaries: bool = True)
```

Rollout 持久化配置。

**参数**：

* **enabled**(bool，可选)：是否启用持久化。默认值：`False`。
* **save_path**(str，可选)：持久化保存路径。默认值：`None`。
* **flush_interval**(int，可选)：刷新间隔（步数）。默认值：`100`。
* **save_rollouts**(bool，可选)：是否保存 rollouts。默认值：`True`。
* **save_step_summaries**(bool，可选)：是否保存步数摘要。默认值：`True`。

## class openjiuwen.agent_evolving.agent_rl.config.offline_config.TrainingConfig

```python
class openjiuwen.agent_evolving.agent_rl.config.offline_config.TrainingConfig(project_name: str = "OpenJiuwenAgentRL", experiment_name: str = "grpo_experiment", train_data_path: Optional[str] = None, val_data_path: Optional[str] = None, train_files: Optional[str] = None, val_files: Optional[str] = None, model_path: str = None, save_path: str = None, algorithm_adv_estimator: str = "grpo", algorithm_use_kl_in_reward: bool = False, algorithm_filter_groups: bool = False, algorithm_norm_adv_by_std_in_grpo: bool = True, whole_trajectory: bool = False, epoch_num: int = 2, total_epochs: int = 2, save_freq: int = 20, test_freq: int = 20, train_batch_size: int = 32, rollout_concurrency: int = 40, visible_device: str = "0,1,2,3", nnodes: int = 1, n_gpus_per_node: int = 4, micro_batch_size_per_gpu: int = 4, max_prompt_length: int = 3072, max_response_length: int = 3072, truncation: str = "truncate", val_before_train: bool = True, critic_warmup: int = 0, logger: List[str] = ["tensorboard"], log_rollout_details: bool = True, log_reward_distribution: bool = False, verl_extra: Dict[str, Any] = {}, verl_config_path: Optional[str] = None)
```

训练配置，覆盖数据、模型、算法和 Verl 训练器参数。

**参数**：

* **project_name**(str，可选)：项目名称。默认值：`"OpenJiuwenAgentRL"`。
* **experiment_name**(str，可选)：实验名称。默认值：`"grpo_experiment"`。
* **train_data_path**(str，可选)：训练数据路径。默认值：`None`。
* **val_data_path**(str，可选)：验证数据路径。默认值：`None`。
* **train_files**(str，可选)：与 `train_data_path` 同义，用于指定训练数据文件路径；二者任选其一配置即可。默认值：`None`。
* **val_files**(str，可选)：与 `val_data_path` 同义，用于指定验证数据文件路径；二者任选其一配置即可。默认值：`None`。
* **model_path**(str，可选)：模型路径。默认值：`None`。
* **save_path**(str，可选)：保存路径。默认值：`None`。
* **algorithm_adv_estimator**(str，可选)：优势估计器算法。默认值：`"grpo"`。
* **algorithm_use_kl_in_reward**(bool，可选)：是否在奖励中使用 KL 散度。默认值：`False`。
* **algorithm_filter_groups**(bool，可选)：是否过滤分组。默认值：`False`。
* **algorithm_norm_adv_by_std_in_grpo**(bool，可选)：是否在 GRPO 中按标准差归一化优势。默认值：`True`。
* **whole_trajectory**(bool，可选)：是否使用完整轨迹模式。默认值：`False`。
* **epoch_num**(int，可选)：训练轮数。默认值：`2`。
* **total_epochs**(int，可选)：总训练轮数（别名）。默认值：`2`。
* **save_freq**(int，可选)：保存频率（步数）。默认值：`20`。
* **test_freq**(int，可选)：测试频率（步数）。默认值：`20`。
* **train_batch_size**(int，可选)：训练批次大小。默认值：`32`。
* **rollout_concurrency**(int，可选)：Rollout 并发数。默认值：`40`。
* **visible_device**(str，可选)：可见 GPU 设备。默认值：`"0,1,2,3"`。
* **nnodes**(int，可选)：节点数。默认值：`1`。
* **n_gpus_per_node**(int，可选)：每节点 GPU 数。默认值：`4`。
* **micro_batch_size_per_gpu**(int，可选)：每 GPU 微批次大小。默认值：`4`。
* **max_prompt_length**(int，可选)：最大提示词长度。默认值：`3072`。
* **max_response_length**(int，可选)：最大响应长度。默认值：`3072`。
* **truncation**(str，可选)：截断策略。默认值：`"truncate"`。
* **val_before_train**(bool，可选)：是否在训练前验证。默认值：`True`。
* **critic_warmup**(int，可选)：Critic 预热步数。默认值：`0`。
* **logger**(List[str]，可选)：日志后端列表。默认值：`["tensorboard"]`。
* **log_rollout_details**(bool，可选)：是否记录 rollout 详情。默认值：`True`。
* **log_reward_distribution**(bool，可选)：是否记录奖励分布。默认值：`False`。
* **verl_extra**(Dict[str, Any]，可选)：Verl 配置额外覆盖项。默认值：`{}`。
* **verl_config_path**(Optional[str]，可选)：Verl 配置文件路径。默认值：`None`。

### property resolved_train_files(self) -> Optional[str]

返回训练数据路径，优先使用 train_files 而非 train_data_path。

### property resolved_val_files(self) -> Optional[str]

返回验证数据路径，优先使用 val_files 而非 val_data_path。

## class openjiuwen.agent_evolving.agent_rl.config.offline_config.RolloutConfig

```python
class openjiuwen.agent_evolving.agent_rl.config.offline_config.RolloutConfig(actor_optimizer_lr: float = 1e-6, actor_use_kl_loss: bool = False, actor_kl_loss_coef: float = 0.02, actor_entropy_coef: float = 0.0, actor_clip_ratio_low: float = 0.2, actor_clip_ratio_high: float = 0.3, actor_loss_agg_mode: str = "seq-mean-token-mean", rollout_n: int = 8)
```

Rollout / Actor 优化器配置。

**参数**：

* **actor_optimizer_lr**(float，可选)：Actor 优化器学习率。默认值：`1e-6`。
* **actor_use_kl_loss**(bool，可选)：是否使用 KL 损失。默认值：`False`。
* **actor_kl_loss_coef**(float，可选)：Actor KL 损失系数。默认值：`0.02`。
* **actor_entropy_coef**(float，可选)：Actor 熵系数。默认值：`0.0`。
* **actor_clip_ratio_low**(float，可选)：Actor 裁剪比率下限。默认值：`0.2`。
* **actor_clip_ratio_high**(float，可选)：Actor 裁剪比率上限。默认值：`0.3`。
* **actor_loss_agg_mode**(str，可选)：Actor 损失聚合模式。默认值：`"seq-mean-token-mean"`。
* **rollout_n**(int，可选)：Rollout 采样数。默认值：`8`。

多轮 rollout 上限不在 `RolloutConfig` 中配置；启用 Ada 时由 `RLConfig.ada`（见 [AdaConfig](#class-openjiuwenagent_evolvingagent_rlconfigoffline_configadaconfig)）提供 `rollout_max_round` 等字段。

## class openjiuwen.agent_evolving.agent_rl.config.offline_config.AgentRuntimeConfig

```python
class openjiuwen.agent_evolving.agent_rl.config.offline_config.AgentRuntimeConfig(system_prompt: Any = "You are a helpful assistant.", temperature: float = 0.7, top_p: float = 0.9, max_new_tokens: int = 512, presence_penalty: float = 0.0, frequency_penalty: float = 0.0)
```

Agent 运行时超参数。

**参数**：

* **system_prompt**(Any，可选)：系统提示词。默认值：`"You are a helpful assistant."`。
* **temperature**(float，可选)：采样温度。默认值：`0.7`。
* **top_p**(float，可选)：Top-p 采样参数。默认值：`0.9`。
* **max_new_tokens**(int，可选)：最大新 token 数。默认值：`512`。
* **presence_penalty**(float，可选)：存在惩罚。默认值：`0.0`。
* **frequency_penalty**(float，可选)：频率惩罚。默认值：`0.0`。

## class openjiuwen.agent_evolving.agent_rl.config.offline_config.AdaConfig

```python
class openjiuwen.agent_evolving.agent_rl.config.offline_config.AdaConfig(rollout_max_round: int = 2, final_keep_per_prompt: int = 8)
```

Ada rollout 变体的额外参数。

当 `RLConfig.ada` 被提供（不为 None）时，Ada 被启用；此时多轮 rollout 的轮数上限等由本类字段与 Hydra 中的 `trainer.rollout_max_round` / `JiuwenRL` 自定义函数联动，而非写在 `RolloutConfig`。

**参数**：

* **rollout_max_round**(int，可选)：Rollout 最大轮数。默认值：`2`。
* **final_keep_per_prompt**(int，可选)：每个提示词最终保留数。默认值：`8`。

## class openjiuwen.agent_evolving.agent_rl.config.offline_config.RLConfig

```python
class openjiuwen.agent_evolving.agent_rl.config.offline_config.RLConfig(training: TrainingConfig, rollout: RolloutConfig = RolloutConfig(), runtime: AgentRuntimeConfig = AgentRuntimeConfig(), persistence: PersistenceConfig = PersistenceConfig(), ada: Optional[AdaConfig] = None)
```

顶级 RL 配置。

**参数**：

* **training**(TrainingConfig)：训练配置（必需）。
* **rollout**(RolloutConfig，可选)：Rollout 配置。默认值：`RolloutConfig()`。
* **runtime**(AgentRuntimeConfig，可选)：运行时配置。默认值：`AgentRuntimeConfig()`。
* **persistence**(PersistenceConfig，可选)：持久化配置。默认值：`PersistenceConfig()`。
* **ada**(Optional[AdaConfig]，可选)：Ada 配置。若提供，则启用 Ada rollout 变体。默认值：`None`。
