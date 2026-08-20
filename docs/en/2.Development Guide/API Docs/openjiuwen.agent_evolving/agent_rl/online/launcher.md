# openjiuwen.agent_evolving.agent_rl.online.launcher

Orchestration runtime for the JiuwenClaw online RL loop (interact → trajectory collect → PPO train → LoRA hot-load). Parses CLI args, merges config, spawns service processes (vLLM inference, judge, gateway, training scheduler, JiuwenClaw app and web), and provides graceful shutdown via signal handling and health checks.

The module is clearly layered: `cli.py` parses args, `loader.py` merges config, `services.py` spawns processes, `workspace.py` writes env files, `runner.py` orchestrates the loop, and `__init__.py` re-exports the top-level entry points.

## class openjiuwen.agent_evolving.agent_rl.online.launcher.runner.LauncherPaths

```python
@dataclass(frozen=True)
class LauncherPaths(agent_core_root: Path, jiuwenclaw_repo: Path, workspace_root: Path, workspace_env: Path, script_dir: Path)
```

Frozen dataclass describing the on-disk layout consumed by the runner.

**Fields**:

* **agent_core_root**(Path): agent-core repository root.
* **jiuwenclaw_repo**(Path): JiuwenClaw repository root.
* **workspace_root**(Path): Workspace root directory.
* **workspace_env**(Path): Workspace `.env` file path.
* **script_dir**(Path): Script directory (logs dir is under it).

## def openjiuwen.agent_evolving.agent_rl.online.launcher.runner.run_online_rl_loop

```python
def run_online_rl_loop(*, cfg: OnlineRLConfig, cfg_path: Path, paths: LauncherPaths) -> None
```

Top-level orchestration entry. Installs SIGINT/SIGTERM handlers (raising `_ShutdownRequested`), executes the launch sequence:

1. Creates `logs/` directory.
2. `resolve_launch_runtime(cfg, script_dir=paths.script_dir)`.
3. Pre-checks required ports.
4. Starts inference vLLM (`enable_runtime_lora=True`) unless `runtime.skip_vllm`.
5. Starts judge vLLM (`enable_runtime_lora=False`) unless `runtime.skip_judge`.
6. Health-checks vLLM and judge.
7. Starts gateway, health-checks gateway.
8. Starts the training scheduler via `start_online_training_scheduler`.
9. If `cfg.jiuwen.enabled`: `ensure_workspace` then `start_jiuwenclaw`; otherwise skips.
10. `print_launch_summary`.
11. Supervisory loop: every 30s polls each child `Popen.poll()`; if any exited, stops everything and returns.
12. `finally` block calls `_shutdown()` which stops the scheduler and terminates web→claw→gateway→judge→vllm in order (idempotent).

**Parameters** (all keyword-only):

* **cfg**(OnlineRLConfig): Run configuration.
* **cfg_path**(Path): Config file path.
* **paths**(LauncherPaths): Path layout.

## def openjiuwen.agent_evolving.agent_rl.online.launcher.cli.build_arg_parser

```python
def build_arg_parser() -> argparse.ArgumentParser
```

Constructs the CLI argument parser, described as `'JiuwenClaw online RL loop: interact -> trajectory collect -> PPO train -> LoRA hot-load'`.

**Returns**:

`argparse.ArgumentParser`. Main args (dest / type / default):

| Flag | Dest | Type | Default |
|------|------|------|---------|
| `--config` | `config` | str | None |
| `--model-path` | `model_path` | str | None |
| `--model-name` | `model_name` | str | None |
| `--vllm-gpu` | `vllm_gpu` | str | None |
| `--vllm-tp` | `vllm_tp` | int | None |
| `--vllm-port` | `vllm_port` | int | None |
| `--judge-model-path` | `judge_model_path` | str | None |
| `--judge-model-name` | `judge_model_name` | str | None |
| `--judge-gpu` | `judge_gpu` | str | None |
| `--judge-tp` | `judge_tp` | int | None |
| `--judge-port` | `judge_port` | int | None |
| `--gateway-port` | `gateway_port` | int | None |
| `--redis-url` | `redis_url` | str | None |
| `--threshold` | `threshold` | int | None |
| `--scan-interval` | `scan_interval` | int | None |
| `--train-gpu` | `train_gpu` | str | None |
| `--ppo-config` | `ppo_config` | str | None |
| `--trajectory-batch-size` | `trajectory_batch_size` | int | None |
| `--lora-repo` | `lora_repo` | str | None |
| `--jiuwen-agent-server-port` | `jiuwen_agent_server_port` | int | None |
| `--demo` | `demo` | store_true | None |
| `--inference-url` | `inference_url` | str | None |
| `--judge-url` | `judge_url` | str | None |
| `--skip-jiuwen`/`--skip_jiuwen` | `skip_jiuwen` | store_true | False |
| `--jiuwen-ws-port` | `jiuwen_ws_port` | int | None |
| `--jiuwen-web-host` | `jiuwen_web_host` | str | None |
| `--jiuwen-web-port` | `jiuwen_web_port` | int | None |

## def openjiuwen.agent_evolving.agent_rl.online.launcher.cli.build_cli_overrides

```python
def build_cli_overrides(args: argparse.Namespace) -> dict[str, object]
```

Translates a parsed `Namespace` into a nested override dict. Uses a hardcoded `cli_mappings` table mapping CLI attribute names to dotted config paths (e.g. `model_path` → `inference.model_path`), skipping `None` values. When `--skip-jiuwen` is true, sets `jiuwen.enabled` to `False`.

**Parameters**:

* **args**(argparse.Namespace): Parsed args.

**Returns**:

`dict[str, object]`, a nested override dict.

## def openjiuwen.agent_evolving.agent_rl.online.launcher.loader.load_runtime_config

```python
def load_runtime_config(*, config_path: str | None, cli_overrides: dict[str, object]) -> tuple[OnlineRLConfig, Path]
```

Three-layer config merge (OmegaConf): built-in `BUILTIN_ONLINE_RL_CONFIG` + optional YAML (falls back to built-in `online_config.py` when `config_path` is missing) + CLI overrides. Returns a Pydantic-validated `OnlineRLConfig` and the resolved path.

**Parameters** (all keyword-only):

* **config_path**(str | None): User YAML path; when `None`, uses built-in defaults.
* **cli_overrides**(dict[str, object]): CLI override dict.

**Returns**:

`tuple[OnlineRLConfig, Path]`, the config object and the resolved config file path.

## def openjiuwen.agent_evolving.agent_rl.online.launcher.loader.resolve_builtin_online_config_path

```python
def resolve_builtin_online_config_path() -> Path
```

Locates the on-disk path of `online_config.py` via the module's `__file__` attribute.

**Returns**:

`Path`, the built-in config file path. Raises `RuntimeError` if it cannot be located.

## Constant DEFAULT_CONFIG_FILENAME

```python
DEFAULT_CONFIG_FILENAME = "online_config.py (built-in)"
```

Used in CLI help text to indicate the config source.

## class openjiuwen.agent_evolving.agent_rl.online.launcher.services.LaunchRuntime

```python
@dataclass(frozen=True)
class LaunchRuntime(inference_url: str, judge_url: str, gateway_base_url: str, gateway_api_url: str, lora_repo: str, skip_vllm: bool, skip_judge: bool, reuse_inference_for_judge: bool, judge_label: str, ports_to_check: tuple[tuple[str, str, int], ...])
```

Frozen dataclass holding resolved URLs/flags consumed by the runner. Each entry in `ports_to_check` is `(name, host, port)`.

**Fields**:

* **inference_url**(str): Inference service URL.
* **judge_url**(str): Judge service URL.
* **gateway_base_url**(str): Gateway base URL.
* **gateway_api_url**(str): Gateway API URL.
* **lora_repo**(str): LoRA repository root.
* **skip_vllm**(bool): Whether to skip vLLM launch.
* **skip_judge**(bool): Whether to skip judge launch.
* **reuse_inference_for_judge**(bool): Whether to reuse the inference service as judge.
* **judge_label**(str): Judge label.
* **ports_to_check**(tuple[tuple[str, str, int], ...]): Ports to pre-check.

## def openjiuwen.agent_evolving.agent_rl.online.launcher.services.resolve_launch_runtime

```python
def resolve_launch_runtime(cfg: OnlineRLConfig, *, script_dir: Path) -> LaunchRuntime
```

Resolves service URLs, skip flags, port-check list, and LoRA repo location from config.

**Parameters**:

* **cfg**(OnlineRLConfig): Run configuration.
* **script_dir**(Path): Script directory (used to resolve the LoRA repo relative path).

**Returns**:

`LaunchRuntime`.

## def openjiuwen.agent_evolving.agent_rl.online.launcher.services.url_host

```python
def url_host(host: str) -> str
```

Normalizes wildcard bind hosts (`'0.0.0.0'`, `'::'`) to `'127.0.0.1'` for client-side URL construction.

**Parameters**:

* **host**(str): Bind host.

**Returns**:

`str`, the normalized host.

## def openjiuwen.agent_evolving.agent_rl.online.launcher.services.spawn_process

```python
def spawn_process(cmd: list[str], *, env: dict[str, str] | None = None, cwd: str | None = None, log_path: Path | None = None) -> subprocess.Popen
```

Generic child-process spawner. When `log_path` is provided, appends stdout+stderr to that file (auto-creating parent dirs); otherwise inherits parent stdio.

**Parameters**:

* **cmd**(list[str]): Command list.
* **env**(dict[str, str] | None, optional): Environment variables. Default: `None`.
* **cwd**(str | None, optional): Working directory. Default: `None`.
* **log_path**(Path | None, optional): Log file path. Default: `None`.

**Returns**:

`subprocess.Popen`.

## def openjiuwen.agent_evolving.agent_rl.online.launcher.services.start_vllm_service

```python
def start_vllm_service(service_cfg: VLLMServiceConfig, *, step_label: str, service_name: str, enable_runtime_lora: bool, log_path: Path | None = None) -> subprocess.Popen
```

Launches a vLLM OpenAI API server (`python -m vllm.entrypoints.openai.api_server`) with args from `service_cfg`. Sets `CUDA_VISIBLE_DEVICES`; when `enable_runtime_lora=True`, sets `VLLM_ALLOW_RUNTIME_LORA_UPDATING=1`.

**Parameters** (all keyword-only except `service_cfg`):

* **service_cfg**(VLLMServiceConfig): Service config.
* **step_label**(str): Step label (for logs).
* **service_name**(str): Service name (for logs).
* **enable_runtime_lora**(bool): Whether to enable runtime LoRA updates.
* **log_path**(Path | None, optional): Log path. Default: `None`.

**Returns**:

`subprocess.Popen`.

## def openjiuwen.agent_evolving.agent_rl.online.launcher.services.start_gateway

```python
def start_gateway(*, inference_url: str, judge_url: str, judge_model: str, model_id: str, model_path: str, lora_repo_root: str, gateway_cfg: GatewayServiceConfig, agent_core_root: Path, log_path: Path | None = None) -> subprocess.Popen
```

Launches the agent-core gateway via `python -m uvicorn <DEFAULT_GATEWAY_APP_FACTORY> --factory ...`. Sets `LLM_URL`, `JUDGE_URL`, `JUDGE_MODEL`, `MODEL_ID`, `MODEL_PATH`, `GATEWAY_HOST/PORT`, `RECORD_DIR`, `REDIS_URL`, optional `LORA_REPO_ROOT`, optional `DISABLE_GATEWAY_TRAJECTORY_COLLECTION` and other env vars, running with `cwd=agent_core_root`.

**Parameters** (all keyword-only):

* **inference_url**(str): Inference URL.
* **judge_url**(str): Judge URL.
* **judge_model**(str): Judge model name.
* **model_id**(str): Model ID.
* **model_path**(str): Model path.
* **lora_repo_root**(str): LoRA repository root.
* **gateway_cfg**(GatewayServiceConfig): Gateway service config.
* **agent_core_root**(Path): agent-core root directory.
* **log_path**(Path | None, optional): Log path. Default: `None`.

**Returns**:

`subprocess.Popen`.

## def openjiuwen.agent_evolving.agent_rl.online.launcher.services.start_online_training_scheduler

```python
def start_online_training_scheduler(*, cfg: OnlineRLConfig, runtime: LaunchRuntime)
```

Lazily imports `InferenceNotifier`, `OnlineTrainingScheduler`, `LoRARepository`, constructs and starts the scheduler (polls `RedisTrajectoryStore` and triggers PPO LoRA training when pending trajectories reach `threshold`). Returns the scheduler instance, calling `scheduler.start()` before returning.

**Parameters** (all keyword-only):

* **cfg**(OnlineRLConfig): Run configuration.
* **runtime**(LaunchRuntime): Resolved runtime info.

## def openjiuwen.agent_evolving.agent_rl.online.launcher.services.start_jiuwenclaw

```python
def start_jiuwenclaw(*, jiuwenclaw_repo: Path, workspace_root: Path, trajectory_gateway_url: str, model_path: str, trajectory_mode: str, trajectory_batch_size: int, app_host: str, ws_port: int, web_host: str, web_port: int) -> tuple[subprocess.Popen, subprocess.Popen | None]
```

Launches the JiuwenClaw app (`python -m jiuwenclaw.app`) and optionally its web frontend (`python -m jiuwenclaw.app_web`) when a `web/dist` directory exists. Resolves `RL_ONLINE_TENANT_ID` from env (falling back to `WEB_USER_ID` or `'local-web-user'`), injects `WEB_USER_ID` and a JSON-encoded `CUSTOM_HEADERS` (`{'x-user-id': ...}`). Uses `build_trajectory_env_updates` for trajectory env vars.

**Parameters** (all keyword-only):

* **jiuwenclaw_repo**(Path): JiuwenClaw repository root.
* **workspace_root**(Path): Workspace root.
* **trajectory_gateway_url**(str): Trajectory gateway URL.
* **model_path**(str): Model path.
* **trajectory_mode**(str): Trajectory mode.
* **trajectory_batch_size**(int): Trajectory batch size.
* **app_host**(str): App listen address.
* **ws_port**(int): WebSocket port.
* **web_host**(str): Web frontend listen address.
* **web_port**(int): Web frontend port.

**Returns**:

`tuple[subprocess.Popen, subprocess.Popen | None]`, the app process and optional web process.

## def openjiuwen.agent_evolving.agent_rl.online.launcher.services.print_launch_summary

```python
def print_launch_summary(*, cfg: OnlineRLConfig, cfg_path: Path, runtime: LaunchRuntime, web_started: bool) -> None
```

Logs a formatted summary banner (config path, web/WS URLs, vLLM inference/judge URLs, gateway, Redis, trajectory mode/log, LoRA repo, train threshold, batch size, scan interval, train GPUs, usage hint). The web frontend line is conditionally included based on `cfg.jiuwen.enabled` and `web_started`.

**Parameters** (all keyword-only):

* **cfg**(OnlineRLConfig): Run configuration.
* **cfg_path**(Path): Config file path.
* **runtime**(LaunchRuntime): Runtime info.
* **web_started**(bool): Whether the web frontend was started.

## Constants

```python
DEFAULT_GATEWAY_APP_FACTORY = 'openjiuwen.agent_evolving.agent_rl.online.gateway.app.proxy:create_app'
EXISTING_SERVICE_HEALTH_TIMEOUT = 30.0
```

- `DEFAULT_GATEWAY_APP_FACTORY`: The uvicorn app factory target string.
- `EXISTING_SERVICE_HEALTH_TIMEOUT`: Health-check timeout in seconds when reusing an externally-managed service.

## def openjiuwen.agent_evolving.agent_rl.online.launcher.workspace.build_trajectory_env_updates

```python
def build_trajectory_env_updates(*, gateway_url: str, model_path: str, trajectory_batch_size: int, trajectory_mode: str, trajectory_tenant_id: str | None = None) -> dict[str, str]
```

Returns a dict of env vars for JiuwenClaw Rail (online-RL trajectory upload).

**Parameters** (all keyword-only):

* **gateway_url**(str): Gateway URL.
* **model_path**(str): Model path.
* **trajectory_batch_size**(int): Trajectory batch size.
* **trajectory_mode**(str): Trajectory mode.
* **trajectory_tenant_id**(str | None, optional): Tenant ID. Default: `None`.

**Returns**:

`dict[str, str]`, containing `USE_RL_ONLINE_RAIL='1'`, `ENABLE_TRAJECTORY_COLLECTION='false'`, `TRAJECTORY_GATEWAY_URL`, `TRAJECTORY_TOKENIZER_PATH`, `TRAJECTORY_BATCH_SIZE`, `TRAJECTORY_MODE`, and optionally `RL_ONLINE_TENANT_ID`.

## def openjiuwen.agent_evolving.agent_rl.online.launcher.workspace.ensure_workspace

```python
def ensure_workspace(*, config_env: Path, gateway_url: str, model_name: str, model_path: str, trajectory_mode: str, trajectory_gateway_url: str | None = None, trajectory_batch_size: int = 8) -> None
```

Ensures the JiuwenClaw `.env` file at `config_env` points to the gateway. If the file does not exist, lazily imports and calls `jiuwenclaw.utils.prepare_workspace(overwrite=False, preferred_language='zh')`; then merges (preserving existing keys) a set of values: `API_BASE`, `API_KEY='EMPTY'`, `MODEL_NAME`, `MODEL_PROVIDER='OpenAI'`, `WEB_USER_ID`, `CUSTOM_HEADERS` (JSON), `EMBED_*`, `BROWSER_RUNTIME_MCP_ENABLED='0'`, `EVOLUTION_AUTO_SCAN='false'`, plus trajectory updates from `build_trajectory_env_updates`. Writes back the full file.

**Parameters** (all keyword-only):

* **config_env**(Path): `.env` file path.
* **gateway_url**(str): Gateway URL.
* **model_name**(str): Model name.
* **model_path**(str): Model path.
* **trajectory_mode**(str): Trajectory mode.
* **trajectory_gateway_url**(str | None, optional): Trajectory gateway URL; when `None`, uses `gateway_url`. Default: `None`.
* **trajectory_batch_size**(int): Trajectory batch size. Default: `8`.

## Usage

- [run_online_rl.py](file:///Users/dongdong/Desktop/project/agent-core/examples/jiuwenrl_online/run_online_rl.py): The user-facing launcher script. Imports `build_arg_parser`, `build_cli_overrides`, `load_runtime_config`, `LauncherPaths`, `run_online_rl_loop`, constructs a `LauncherPaths`, then calls the main entry. This is the sole production external consumer.
- [test_launcher_runner.py](file:///Users/dongdong/Desktop/project/agent-core/tests/unit_tests/agent_evolving/agent_rl/online/test_launcher_runner.py): Tests `run_online_rl_loop` signal shutdown, `print_launch_summary`, `ensure_workspace`, `start_jiuwenclaw`.
- [README.md](file:///Users/dongdong/Desktop/project/agent-core/examples/jiuwenrl_online/README.md): References `print_launch_summary` output.
