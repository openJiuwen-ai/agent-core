# openjiuwen.agent_evolving.agent_rl.online.scheduler

在线 RL 训练的轮询调度与 PPO 批次执行组件。后台线程轮询 Redis 轨迹存储中累积的样本，当某用户的样本数达到阈值时触发一次 PPO LoRA 训练批次：转换样本 → 调用 Ray/verl 训练 → 导出 LoRA → 发布到仓库 → 通知 vLLM 热加载。

## class openjiuwen.agent_evolving.agent_rl.online.scheduler.online_training_scheduler.OnlineTrainingScheduler

```python
class OnlineTrainingScheduler(*, redis_url: str = "redis://127.0.0.1:6379/0", poll_interval: float = 30.0, min_samples_for_training: int = 32, base_model_path: str = "", lora_repo: Optional[LoRARepository] = None, notifier: Optional[InferenceNotifier] = None, nproc_per_node: int = 1, training_gpu_ids: str = "", tmp_root: str = "/tmp/agent_rl_online", ppo_config_path: Optional[str] = None)
```

后台线程调度器。轮询 `RedisTrajectoryStore`，对样本数超过阈值的用户启动一个 asyncio 任务运行 PPO 训练批次，同一时刻最多保留一个在途训练任务。

**参数**（均为关键字参数）：

* **redis_url**(str)：Redis 连接 URL。默认值：`"redis://127.0.0.1:6379/0"`。为空字符串时禁用调度器（测试中使用）。
* **poll_interval**(float)：轮询间隔（秒）。默认值：`30.0`。
* **min_samples_for_training**(int)：触发训练的最小样本数阈值。默认值：`32`。
* **base_model_path**(str)：基座模型路径。默认值：`""`。
* **lora_repo**(Optional[LoRARepository]，可选)：LoRA 仓库实例，用于发布训练产物。默认值：`None`。
* **notifier**(Optional[InferenceNotifier]，可选)：vLLM 热加载通知器。默认值：`None`。
* **nproc_per_node**(int)：单节点 GPU 数。默认值：`1`。
* **training_gpu_ids**(str)：训练用 GPU ID 列表（逗号分隔）。默认值：`""`。
* **tmp_root**(str)：训练临时根目录。默认值：`"/tmp/agent_rl_online"`。
* **ppo_config_path**(Optional[str]，可选)：自定义 Hydra PPO YAML 路径。默认值：`None`。

**说明**：

- 构造时内部创建一个 `PPOTrainingExecutor` 并传入 `base_model_path`、`lora_repo`、`notifier`、`nproc_per_node`、`training_gpu_ids`、`ppo_config_path`。

### def start

```python
def start() -> None
```

启动守护轮询线程（线程名 `OnlineTrainScheduler`，目标为 `_poll_loop`）。若已在运行则记录警告并无操作。

### def stop

```python
def stop() -> None
```

发送停止信号、等待线程（15 秒超时）后调用 `self._trainer.close()`。若线程仍存活则记录警告。幂等。

### async def _poll_loop (私有)

```python
async def _poll_loop() -> None
```

后台线程入口。创建专用 asyncio 事件循环，惰性导入 `redis.asyncio.from_url`，构建 `RedisTrajectoryStore`，运行 `_poll_main`；`finally` 中关闭 trainer、Redis 客户端与循环。当 `redis_url` 为空时直接返回。

### async def _poll_main (私有)

```python
async def _poll_main() -> None
```

主循环：未停止时依次执行 `_reap_training_task()` → `_poll_once()` → `sleep(poll_interval)`；循环结束后 `await _reap_training_task(wait=True)` 排空在途任务。每轮异常被捕获并记录。

### async def _poll_once (私有)

```python
async def _poll_once() -> None
```

当存储为空或已有在途训练任务时为空操作。否则调用 `get_users_above_threshold(min_samples_for_training)`，对首个可获取样本的用户调用 `fetch_and_mark_training(user_id, min_samples_for_training)`，创建 `asyncio.create_task(self._train_batch(...))`，每个周期最多启动一个训练任务。

### async def _reap_training_task (私有)

```python
async def _reap_training_task(*, wait: bool = False) -> None
```

收割在途训练任务。无任务时直接返回；除非 `wait=True`，任务未完成时提前返回。否则 await 该任务，异常被记录，`finally` 中清理 `_active_training_task` 与 `_active_training_user`。

### async def _train_batch (私有)

```python
async def _train_batch(*, user_id: str, samples: list[dict[str, Any]], sample_ids: list[str]) -> None
```

执行单个训练批次。调用 `self._trainer.train_batch(user_id=..., samples=..., training_count=self._training_count, tmp_root=self.tmp_root)`；成功时 `mark_trained(sample_ids)`，异常时 `mark_failed(sample_ids)`。存储为空时为空操作。

## def openjiuwen.agent_evolving.agent_rl.online.scheduler.ppo_config.compose_online_ppo_config

```python
def compose_online_ppo_config(*, model_path: str, n_gpus_per_node: int = 2, config_path: Optional[str] = None)
```

组合在线 PPO 训练的 Hydra/OmegaConf 配置。

**参数**（均为关键字参数）：

* **model_path**(str)：基座模型路径，写入 `cfg.actor_rollout_ref.model.path`。
* **n_gpus_per_node**(int)：单节点 GPU 数，写入 `cfg.trainer.n_gpus_per_node`。默认值：`2`。
* **config_path**(Optional[str]，可选)：用户自定义 Hydra YAML 路径。为 `None` 时加载 verl 内置 `ppo_trainer` 配置并合并 `ONLINE_PPO_VERL_HYDRA_OVERLAY`；否则使用 `initialize_config_dir` + `compose(config_name=stem)` 加载该 YAML。

**返回**：

OmegaConf `DictConfig`。设置 `cfg.trainer.default_local_dir` 默认为 `/tmp/online_ppo_ckpt`，并调用 `OmegaConf.resolve(cfg)` 解析变量后返回。

## class openjiuwen.agent_evolving.agent_rl.online.scheduler.ppo_executor.PPOTrainingExecutor

```python
class PPOTrainingExecutor(*, base_model_path: str, lora_repo: Optional[LoRARepository], notifier: Optional[InferenceNotifier], nproc_per_node: int, training_gpu_ids: str, ppo_config_path: Optional[str])
```

持有 Ray/verl PPO runner 生命周期（惰性初始化、关闭时 kill）并执行单个训练批次。

**参数**（均为关键字参数）：

* **base_model_path**(str)：基座模型路径。
* **lora_repo**(Optional[LoRARepository])：LoRA 仓库，用于发布产物。
* **notifier**(Optional[InferenceNotifier])：vLLM 热加载通知器。
* **nproc_per_node**(int)：单节点 GPU 数。
* **training_gpu_ids**(str)：训练用 GPU ID 列表。
* **ppo_config_path**(Optional[str])：自定义 PPO 配置路径。

### async def aclose

```python
async def aclose() -> None
```

关闭通知器（若存在，吞掉异常）后调用 `self.close()`。用于调度器拆卸。

### def close

```python
def close() -> None
```

若 `_ppo_runner` 已设置，惰性导入 `ray`，调用 `ray.kill(self._ppo_runner, no_restart=True)`（吞掉异常），随后重置 `_ppo_runner`、`_ppo_initialized`。无 runner 时为空操作。

### async def train_batch

```python
async def train_batch(*, user_id: str, samples: list[dict[str, Any]], training_count: int, tmp_root: str) -> Optional[str]
```

执行一次 PPO 训练批次。

**参数**（均为关键字参数）：

* **user_id**(str)：用户标识符。
* **samples**(list[dict[str, Any]])：训练样本列表。
* **training_count**(int)：训练计数（用于命名运行目录）。
* **tmp_root**(str)：临时根目录。

**返回**：

`Optional[str]`，发布的 LoRA 路径；未配置 `lora_repo` 时返回 `None`。

**说明**：

- 创建 `run_dir = Path(tmp_root)/f"run_{training_count}_{uuid.uuid4().hex[:8]}"`。
- 通过 `asyncio.to_thread` 调用 `_run_ppo_training_sync`。
- 若返回 `published_lora_path` 且 `notifier` 已设置，调用 `notify_update(user_id, published_lora_path)`（失败非致命）。
- `finally` 中 `shutil.rmtree(run_dir / "fsdp_ckpt", ignore_errors=True)`。

### def _init_ppo_trainer (私有)

```python
def _init_ppo_trainer() -> None
```

幂等初始化。惰性导入 `ray`、`compose_online_ppo_config`、`OnlineTaskRunner`、`get_ppo_ray_runtime_env`；若 Ray 未初始化则构建 runtime env（注入 `CUDA_VISIBLE_DEVICES`）并 `ray.init(runtime_env=..., namespace="OnlineRL")`；组合 PPO 配置；创建 detached Ray actor `OnlineTaskRunner.options(name="online_ppo_runner", lifetime="detached").remote()`，调用 `ray.get(self._ppo_runner.init_trainer.remote(config))`。

### def _run_ppo_training_sync (私有)

```python
def _run_ppo_training_sync(*, user_id: str, samples: list[dict[str, Any]], run_dir: Path) -> Optional[str]
```

同步执行训练。确保 trainer 初始化；从 `AutoTokenizer` 读取 `pad_token_id`（失败默认 0）；从 `self._ppo_config.data` 读取 `max_prompt_length`、`max_response_length`、`truncation`（默认 `"truncate"`）、`filter_overlong_prompts`（默认 `False`）；构造 `VerlDataProtoConverter` 转换样本为 `DataProto`；`ray.get(self._ppo_runner.train_on_batch.remote(data_proto))` 训练；`ray.get(self._ppo_runner.export_lora.remote(str(run_dir), self.base_model_path))` 导出 LoRA；若 `lora_repo` 已设置则发布并返回版本路径，否则返回 `None`。

## 被使用情况

- [services.py](file:///Users/dongdong/Desktop/project/agent-core/openjiuwen/agent_evolving/agent_rl/online/launcher/services.py)：`start_online_training_scheduler` 构造 `OnlineTrainingScheduler` 并 `.start()`。
- [runner.py](file:///Users/dongdong/Desktop/project/agent-core/openjiuwen/agent_evolving/agent_rl/online/launcher/runner.py)：启动序列第 [3/5] 步调用上述函数，关闭路径调用 `.stop()`。
- [rl_optimizer.py](file:///Users/dongdong/Desktop/project/agent-core/openjiuwen/agent_evolving/agent_rl/optimizer/rl_optimizer.py)：`start_training()` 构造调度器；`train_on_batch(samples)` 独立调用 `compose_online_ppo_config`。
- [test_online_training_scheduler.py](file:///Users/dongdong/Desktop/project/agent-core/tests/unit_tests/agent_evolving/agent_rl/online/test_online_training_scheduler.py)：以 `redis_url=""` 禁用轮询，注入伪造 store/trainer，直接调用私有 `_train_batch` 验证 `mark_trained`/`mark_failed` 行为。
