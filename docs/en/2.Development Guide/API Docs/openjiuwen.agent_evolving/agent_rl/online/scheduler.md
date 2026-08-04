# openjiuwen.agent_evolving.agent_rl.online.scheduler

Online RL training polling scheduler and PPO batch executor. A background thread polls the Redis trajectory store for accumulated samples; when a user's sample count crosses a threshold, it triggers a PPO LoRA training batch: convert samples → call Ray/verl training → export LoRA → publish to repository → notify vLLM to hot-load.

## class openjiuwen.agent_evolving.agent_rl.online.scheduler.online_training_scheduler.OnlineTrainingScheduler

```python
class OnlineTrainingScheduler(*, redis_url: str = "redis://127.0.0.1:6379/0", poll_interval: float = 30.0, min_samples_for_training: int = 32, base_model_path: str = "", lora_repo: Optional[LoRARepository] = None, notifier: Optional[InferenceNotifier] = None, nproc_per_node: int = 1, training_gpu_ids: str = "", tmp_root: str = "/tmp/agent_rl_online", ppo_config_path: Optional[str] = None)
```

Background-thread scheduler. Polls `RedisTrajectoryStore` and starts an asyncio task running a PPO training batch for users whose sample count exceeds the threshold; retains at most one in-flight training task at a time.

**Parameters** (all keyword-only):

* **redis_url**(str): Redis connection URL. Default: `"redis://127.0.0.1:6379/0"`. An empty string disables the scheduler (used in tests).
* **poll_interval**(float): Polling interval in seconds. Default: `30.0`.
* **min_samples_for_training**(int): Minimum sample count threshold to trigger training. Default: `32`.
* **base_model_path**(str): Base model path. Default: `""`.
* **lora_repo**(Optional[LoRARepository], optional): LoRA repository instance for publishing training artifacts. Default: `None`.
* **notifier**(Optional[InferenceNotifier], optional): vLLM hot-load notifier. Default: `None`.
* **nproc_per_node**(int): Number of GPUs per node. Default: `1`.
* **training_gpu_ids**(str): Comma-separated GPU IDs for training. Default: `""`.
* **tmp_root**(str): Training temp root directory. Default: `"/tmp/agent_rl_online"`.
* **ppo_config_path**(Optional[str], optional): Custom Hydra PPO YAML path. Default: `None`.

**Notes**:

- At construction, internally creates a `PPOTrainingExecutor` passing `base_model_path`, `lora_repo`, `notifier`, `nproc_per_node`, `training_gpu_ids`, `ppo_config_path`.

### def start

```python
def start() -> None
```

Starts the daemon polling thread (thread name `OnlineTrainScheduler`, target `_poll_loop`). If already running, logs a warning and does nothing.

### def stop

```python
def stop() -> None
```

Signals stop, joins the thread (15s timeout), then calls `self._trainer.close()`. Logs a warning if the thread is still alive. Idempotent.

### async def _poll_loop (private)

```python
async def _poll_loop() -> None
```

Background thread entry. Creates a dedicated asyncio event loop, lazily imports `redis.asyncio.from_url`, builds `RedisTrajectoryStore`, runs `_poll_main`; in `finally` closes the trainer, Redis client, and loop. Returns immediately when `redis_url` is empty.

### async def _poll_main (private)

```python
async def _poll_main() -> None
```

Main loop: while not stopped — `_reap_training_task()` → `_poll_once()` → `sleep(poll_interval)`; after the loop, `await _reap_training_task(wait=True)` to drain in-flight tasks. Per-iteration exceptions are caught and logged.

### async def _poll_once (private)

```python
async def _poll_once() -> None
```

No-op when the store is `None` or a training task is already active. Otherwise calls `get_users_above_threshold(min_samples_for_training)`; for the first user with fetchable samples calls `fetch_and_mark_training(user_id, min_samples_for_training)`, creates `asyncio.create_task(self._train_batch(...))`, starting at most one training task per cycle.

### async def _reap_training_task (private)

```python
async def _reap_training_task(*, wait: bool = False) -> None
```

Reaps the in-flight training task. Returns immediately if no task; unless `wait=True`, returns early if the task is not done. Otherwise awaits the task; exceptions are logged; `_active_training_task` and `_active_training_user` are cleared in `finally`.

### async def _train_batch (private)

```python
async def _train_batch(*, user_id: str, samples: list[dict[str, Any]], sample_ids: list[str]) -> None
```

Executes a single training batch. Calls `self._trainer.train_batch(user_id=..., samples=..., training_count=self._training_count, tmp_root=self.tmp_root)`; on success calls `mark_trained(sample_ids)`, on exception calls `mark_failed(sample_ids)`. No-op when the store is `None`.

## def openjiuwen.agent_evolving.agent_rl.online.scheduler.ppo_config.compose_online_ppo_config

```python
def compose_online_ppo_config(*, model_path: str, n_gpus_per_node: int = 2, config_path: Optional[str] = None)
```

Composes the Hydra/OmegaConf config for online PPO training.

**Parameters** (all keyword-only):

* **model_path**(str): Base model path, written to `cfg.actor_rollout_ref.model.path`.
* **n_gpus_per_node**(int): Number of GPUs per node, written to `cfg.trainer.n_gpus_per_node`. Default: `2`.
* **config_path**(Optional[str], optional): User-defined Hydra YAML path. When `None`, loads verl's built-in `ppo_trainer` config and merges `ONLINE_PPO_VERL_HYDRA_OVERLAY`; otherwise uses `initialize_config_dir` + `compose(config_name=stem)` to load that YAML.

**Returns**:

An OmegaConf `DictConfig`. Sets `cfg.trainer.default_local_dir` to `/tmp/online_ppo_ckpt` by default, and calls `OmegaConf.resolve(cfg)` to interpolate variables before returning.

## class openjiuwen.agent_evolving.agent_rl.online.scheduler.ppo_executor.PPOTrainingExecutor

```python
class PPOTrainingExecutor(*, base_model_path: str, lora_repo: Optional[LoRARepository], notifier: Optional[InferenceNotifier], nproc_per_node: int, training_gpu_ids: str, ppo_config_path: Optional[str])
```

Owns the Ray/verl PPO runner lifecycle (lazy init, kill on close) and executes a single training batch.

**Parameters** (all keyword-only):

* **base_model_path**(str): Base model path.
* **lora_repo**(Optional[LoRARepository]): LoRA repository for publishing artifacts.
* **notifier**(Optional[InferenceNotifier]): vLLM hot-load notifier.
* **nproc_per_node**(int): Number of GPUs per node.
* **training_gpu_ids**(str): Comma-separated GPU IDs for training.
* **ppo_config_path**(Optional[str]): Custom PPO config path.

### async def aclose

```python
async def aclose() -> None
```

Closes the notifier (if present, swallowing exceptions) then calls `self.close()`. Used by the scheduler teardown.

### def close

```python
def close() -> None
```

If `_ppo_runner` is set, lazily imports `ray`, calls `ray.kill(self._ppo_runner, no_restart=True)` (swallowing exceptions), then resets `_ppo_runner`, `_ppo_initialized`. No-op if no runner.

### async def train_batch

```python
async def train_batch(*, user_id: str, samples: list[dict[str, Any]], training_count: int, tmp_root: str) -> Optional[str]
```

Executes one PPO training batch.

**Parameters** (all keyword-only):

* **user_id**(str): User identifier.
* **samples**(list[dict[str, Any]]): Training sample list.
* **training_count**(int): Training count (used for naming the run directory).
* **tmp_root**(str): Temp root directory.

**Returns**:

`Optional[str]`, the published LoRA path; `None` when `lora_repo` is not configured.

**Notes**:

- Creates `run_dir = Path(tmp_root)/f"run_{training_count}_{uuid.uuid4().hex[:8]}"`.
- Calls `_run_ppo_training_sync` via `asyncio.to_thread`.
- If a `published_lora_path` is returned and `notifier` is set, calls `notify_update(user_id, published_lora_path)` (failures are non-fatal).
- In `finally`, `shutil.rmtree(run_dir / "fsdp_ckpt", ignore_errors=True)`.

### def _init_ppo_trainer (private)

```python
def _init_ppo_trainer() -> None
```

Idempotent initialization. Lazily imports `ray`, `compose_online_ppo_config`, `OnlineTaskRunner`, `get_ppo_ray_runtime_env`; if Ray is not initialized, builds the runtime env (injecting `CUDA_VISIBLE_DEVICES`) and calls `ray.init(runtime_env=..., namespace="OnlineRL")`; composes the PPO config; creates the detached Ray actor `OnlineTaskRunner.options(name="online_ppo_runner", lifetime="detached").remote()`, calls `ray.get(self._ppo_runner.init_trainer.remote(config))`.

### def _run_ppo_training_sync (private)

```python
def _run_ppo_training_sync(*, user_id: str, samples: list[dict[str, Any]], run_dir: Path) -> Optional[str]
```

Synchronously executes training. Ensures trainer initialization; reads `pad_token_id` from `AutoTokenizer` (defaults to 0 on failure); reads `max_prompt_length`, `max_response_length`, `truncation` (default `"truncate"`), `filter_overlong_prompts` (default `False`) from `self._ppo_config.data`; constructs `VerlDataProtoConverter` to convert samples into `DataProto`; `ray.get(self._ppo_runner.train_on_batch.remote(data_proto))` trains; `ray.get(self._ppo_runner.export_lora.remote(str(run_dir), self.base_model_path))` exports LoRA; if `lora_repo` is set, publishes and returns the version path, otherwise returns `None`.

## Usage

- [services.py](file:///Users/dongdong/Desktop/project/agent-core/openjiuwen/agent_evolving/agent_rl/online/launcher/services.py): `start_online_training_scheduler` constructs `OnlineTrainingScheduler` and calls `.start()`.
- [runner.py](file:///Users/dongdong/Desktop/project/agent-core/openjiuwen/agent_evolving/agent_rl/online/launcher/runner.py): Invokes the above function as step [3/5] of the launch sequence; calls `.stop()` in the shutdown path.
- [rl_optimizer.py](file:///Users/dongdong/Desktop/project/agent-core/openjiuwen/agent_evolving/agent_rl/optimizer/rl_optimizer.py): `start_training()` constructs the scheduler; `train_on_batch(samples)` independently calls `compose_online_ppo_config`.
- [test_online_training_scheduler.py](file:///Users/dongdong/Desktop/project/agent-core/tests/unit_tests/agent_evolving/agent_rl/online/test_online_training_scheduler.py): Disables polling with `redis_url=""`, injects fake store/trainer, directly calls private `_train_batch` to verify `mark_trained`/`mark_failed` behavior.
