# openjiuwen.agent_evolving.agent_rl.online.launcher

JiuwenClaw 在线 RL 循环的编排运行时（interact → trajectory collect → PPO train → LoRA hot-load）。负责解析 CLI 参数、合并配置、拉起各服务进程（vLLM 推理、judge、gateway、训练调度器、JiuwenClaw 应用与 web），并通过信号处理与健康检查实现优雅关闭。

模块分层清晰：`cli.py` 解析参数，`loader.py` 合并配置，`services.py` 拉起进程，`workspace.py` 写入环境文件，`runner.py` 编排循环，`__init__.py` 重导出顶层入口。

## class openjiuwen.agent_evolving.agent_rl.online.launcher.runner.LauncherPaths

```python
@dataclass(frozen=True)
class LauncherPaths(agent_core_root: Path, jiuwenclaw_repo: Path, workspace_root: Path, workspace_env: Path, script_dir: Path)
```

描述运行器所需的磁盘布局的冻结 dataclass。

**字段**：

* **agent_core_root**(Path)：agent-core 仓库根目录。
* **jiuwenclaw_repo**(Path)：JiuwenClaw 仓库根目录。
* **workspace_root**(Path)：工作区根目录。
* **workspace_env**(Path)：工作区 `.env` 文件路径。
* **script_dir**(Path)：脚本目录（日志目录在其下）。

## def openjiuwen.agent_evolving.agent_rl.online.launcher.runner.run_online_rl_loop

```python
def run_online_rl_loop(*, cfg: OnlineRLConfig, cfg_path: Path, paths: LauncherPaths) -> None
```

顶层编排入口。安装 SIGINT/SIGTERM 处理器（抛出 `_ShutdownRequested`），执行启动序列：

1. 创建 `logs/` 目录。
2. `resolve_launch_runtime(cfg, script_dir=paths.script_dir)`。
3. 预检所需端口。
4. 启动推理 vLLM（`enable_runtime_lora=True`），除非 `runtime.skip_vllm`。
5. 启动 judge vLLM（`enable_runtime_lora=False`），除非 `runtime.skip_judge`。
6. 健康检查 vLLM 与 judge。
7. 启动 gateway，健康检查 gateway。
8. 通过 `start_online_training_scheduler` 启动训练调度器。
9. 若 `cfg.jiuwen.enabled`：`ensure_workspace` 后 `start_jiuwenclaw`；否则跳过。
10. `print_launch_summary`。
11. 监管循环：每 30 秒轮询各子进程 `poll()`，任一退出则停止全部并返回。
12. `finally` 中 `_shutdown()` 依次停止调度器、终止 web→claw→gateway→judge→vllm（幂等）。

**参数**（均为关键字参数）：

* **cfg**(OnlineRLConfig)：运行配置。
* **cfg_path**(Path)：配置文件路径。
* **paths**(LauncherPaths)：路径布局。

## def openjiuwen.agent_evolving.agent_rl.online.launcher.cli.build_arg_parser

```python
def build_arg_parser() -> argparse.ArgumentParser
```

构造 CLI 参数解析器，描述为 `'JiuwenClaw online RL loop: interact -> trajectory collect -> PPO train -> LoRA hot-load'`。

**返回**：

`argparse.ArgumentParser`。主要参数（dest / 类型 / 默认值）：

| Flag | Dest | 类型 | 默认值 |
|------|------|------|--------|
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

将解析后的 `Namespace` 转换为嵌套覆盖字典。使用硬编码 `cli_mappings` 表将 CLI 属性名映射为点分配置路径（如 `model_path` → `inference.model_path`），跳过 `None` 值。`--skip-jiuwen` 为真时设置 `jiuwen.enabled` 为 `False`。

**参数**：

* **args**(argparse.Namespace)：解析后的参数。

**返回**：

`dict[str, object]`，嵌套覆盖字典。

## def openjiuwen.agent_evolving.agent_rl.online.launcher.loader.load_runtime_config

```python
def load_runtime_config(*, config_path: str | None, cli_overrides: dict[str, object]) -> tuple[OnlineRLConfig, Path]
```

三层配置合并（OmegaConf）：内置 `BUILTIN_ONLINE_RL_CONFIG` + 可选 YAML（`config_path` 缺失时回退到内置 `online_config.py`）+ CLI 覆盖。返回 Pydantic 校验后的 `OnlineRLConfig` 与解析后的路径。

**参数**（均为关键字参数）：

* **config_path**(str | None)：用户 YAML 路径，为 `None` 时使用内置默认。
* **cli_overrides**(dict[str, object])：CLI 覆盖字典。

**返回**：

`tuple[OnlineRLConfig, Path]`，配置对象与解析后的配置文件路径。

## def openjiuwen.agent_evolving.agent_rl.online.launcher.loader.resolve_builtin_online_config_path

```python
def resolve_builtin_online_config_path() -> Path
```

通过模块 `__file__` 定位 `online_config.py` 的磁盘路径。

**返回**：

`Path`，内置配置文件路径。无法定位时抛 `RuntimeError`。

## 常量 DEFAULT_CONFIG_FILENAME

```python
DEFAULT_CONFIG_FILENAME = "online_config.py (built-in)"
```

用于 CLI 帮助文本，指示配置来源。

## class openjiuwen.agent_evolving.agent_rl.online.launcher.services.LaunchRuntime

```python
@dataclass(frozen=True)
class LaunchRuntime(inference_url: str, judge_url: str, gateway_base_url: str, gateway_api_url: str, lora_repo: str, skip_vllm: bool, skip_judge: bool, reuse_inference_for_judge: bool, judge_label: str, ports_to_check: tuple[tuple[str, str, int], ...])
```

持有运行器消费的解析后 URL/标志的冻结 dataclass。`ports_to_check` 每项为 `(name, host, port)`。

**字段**：

* **inference_url**(str)：推理服务 URL。
* **judge_url**(str)：judge 服务 URL。
* **gateway_base_url**(str)：gateway 基础 URL。
* **gateway_api_url**(str)：gateway API URL。
* **lora_repo**(str)：LoRA 仓库根目录。
* **skip_vllm**(bool)：是否跳过 vLLM 启动。
* **skip_judge**(bool)：是否跳过 judge 启动。
* **reuse_inference_for_judge**(bool)：是否复用推理服务作为 judge。
* **judge_label**(str)：judge 标签。
* **ports_to_check**(tuple[tuple[str, str, int], ...])：待预检端口列表。

## def openjiuwen.agent_evolving.agent_rl.online.launcher.services.resolve_launch_runtime

```python
def resolve_launch_runtime(cfg: OnlineRLConfig, *, script_dir: Path) -> LaunchRuntime
```

从配置解析服务 URL、跳过标志、端口检查列表与 LoRA 仓库位置。

**参数**：

* **cfg**(OnlineRLConfig)：运行配置。
* **script_dir**(Path)：脚本目录（用于解析 LoRA 仓库相对路径）。

**返回**：

`LaunchRuntime`。

## def openjiuwen.agent_evolving.agent_rl.online.launcher.services.url_host

```python
def url_host(host: str) -> str
```

将通配绑定主机（`'0.0.0.0'`、`'::'`）规范化为 `'127.0.0.1'`，用于客户端 URL 构造。

**参数**：

* **host**(str)：绑定主机。

**返回**：

`str`，规范化后的主机。

## def openjiuwen.agent_evolving.agent_rl.online.launcher.services.spawn_process

```python
def spawn_process(cmd: list[str], *, env: dict[str, str] | None = None, cwd: str | None = None, log_path: Path | None = None) -> subprocess.Popen
```

通用子进程启动器。提供 `log_path` 时将 stdout+stderr 追加到该文件（自动创建父目录），否则继承父进程 stdio。

**参数**：

* **cmd**(list[str])：命令列表。
* **env**(dict[str, str] | None，可选)：环境变量。默认值：`None`。
* **cwd**(str | None，可选)：工作目录。默认值：`None`。
* **log_path**(Path | None，可选)：日志文件路径。默认值：`None`。

**返回**：

`subprocess.Popen`。

## def openjiuwen.agent_evolving.agent_rl.online.launcher.services.start_vllm_service

```python
def start_vllm_service(service_cfg: VLLMServiceConfig, *, step_label: str, service_name: str, enable_runtime_lora: bool, log_path: Path | None = None) -> subprocess.Popen
```

启动 vLLM OpenAI API server（`python -m vllm.entrypoints.openai.api_server`），参数取自 `service_cfg`。设置 `CUDA_VISIBLE_DEVICES`；当 `enable_runtime_lora=True` 时设置 `VLLM_ALLOW_RUNTIME_LORA_UPDATING=1`。

**参数**（均为关键字参数，除 `service_cfg`）：

* **service_cfg**(VLLMServiceConfig)：服务配置。
* **step_label**(str)：步骤标签（日志用）。
* **service_name**(str)：服务名（日志用）。
* **enable_runtime_lora**(bool)：是否启用运行时 LoRA 更新。
* **log_path**(Path | None，可选)：日志路径。默认值：`None`。

**返回**：

`subprocess.Popen`。

## def openjiuwen.agent_evolving.agent_rl.online.launcher.services.start_gateway

```python
def start_gateway(*, inference_url: str, judge_url: str, judge_model: str, model_id: str, model_path: str, lora_repo_root: str, gateway_cfg: GatewayServiceConfig, agent_core_root: Path, log_path: Path | None = None) -> subprocess.Popen
```

通过 `python -m uvicorn <DEFAULT_GATEWAY_APP_FACTORY> --factory ...` 启动 agent-core gateway。设置 `LLM_URL`、`JUDGE_URL`、`JUDGE_MODEL`、`MODEL_ID`、`MODEL_PATH`、`GATEWAY_HOST/PORT`、`RECORD_DIR`、`REDIS_URL`、可选 `LORA_REPO_ROOT`、可选 `DISABLE_GATEWAY_TRAJECTORY_COLLECTION` 等环境变量，以 `cwd=agent_core_root` 运行。

**参数**（均为关键字参数）：

* **inference_url**(str)：推理 URL。
* **judge_url**(str)：judge URL。
* **judge_model**(str)：judge 模型名。
* **model_id**(str)：模型 ID。
* **model_path**(str)：模型路径。
* **lora_repo_root**(str)：LoRA 仓库根目录。
* **gateway_cfg**(GatewayServiceConfig)：gateway 服务配置。
* **agent_core_root**(Path)：agent-core 根目录。
* **log_path**(Path | None，可选)：日志路径。默认值：`None`。

**返回**：

`subprocess.Popen`。

## def openjiuwen.agent_evolving.agent_rl.online.launcher.services.start_online_training_scheduler

```python
def start_online_training_scheduler(*, cfg: OnlineRLConfig, runtime: LaunchRuntime)
```

惰性导入 `InferenceNotifier`、`OnlineTrainingScheduler`、`LoRARepository`，构造并启动调度器（轮询 `RedisTrajectoryStore`，当待处理轨迹达 `threshold` 时触发 PPO LoRA 训练）。返回调度器实例，调用 `scheduler.start()` 后返回。

**参数**（均为关键字参数）：

* **cfg**(OnlineRLConfig)：运行配置。
* **runtime**(LaunchRuntime)：解析后的运行时信息。

## def openjiuwen.agent_evolving.agent_rl.online.launcher.services.start_jiuwenclaw

```python
def start_jiuwenclaw(*, jiuwenclaw_repo: Path, workspace_root: Path, trajectory_gateway_url: str, model_path: str, trajectory_mode: str, trajectory_batch_size: int, app_host: str, ws_port: int, web_host: str, web_port: int) -> tuple[subprocess.Popen, subprocess.Popen | None]
```

启动 JiuwenClaw 应用（`python -m jiuwenclaw.app`），可选启动 web 前端（当存在 `web/dist` 目录时运行 `python -m jiuwenclaw.app_web`）。从环境解析 `RL_ONLINE_TENANT_ID`（回退 `WEB_USER_ID` 或 `'local-web-user'`），注入 `WEB_USER_ID` 与 JSON 编码的 `CUSTOM_HEADERS`（`{'x-user-id': ...}`）。使用 `build_trajectory_env_updates` 构造轨迹环境变量。

**参数**（均为关键字参数）：

* **jiuwenclaw_repo**(Path)：JiuwenClaw 仓库根目录。
* **workspace_root**(Path)：工作区根目录。
* **trajectory_gateway_url**(str)：轨迹 gateway URL。
* **model_path**(str)：模型路径。
* **trajectory_mode**(str)：轨迹模式。
* **trajectory_batch_size**(int)：轨迹批大小。
* **app_host**(str)：应用监听地址。
* **ws_port**(int)：WebSocket 端口。
* **web_host**(str)：web 前端监听地址。
* **web_port**(int)：web 前端端口。

**返回**：

`tuple[subprocess.Popen, subprocess.Popen | None]`，应用进程与可选 web 进程。

## def openjiuwen.agent_evolving.agent_rl.online.launcher.services.print_launch_summary

```python
def print_launch_summary(*, cfg: OnlineRLConfig, cfg_path: Path, runtime: LaunchRuntime, web_started: bool) -> None
```

输出格式化的启动摘要（配置路径、web/WS URL、vLLM 推理/judge URL、gateway、Redis、轨迹模式/日志、LoRA 仓库、训练阈值、批大小、扫描间隔、训练 GPU、使用提示）。web 前端行根据 `cfg.jiuwen.enabled` 与 `web_started` 条件性包含。

**参数**（均为关键字参数）：

* **cfg**(OnlineRLConfig)：运行配置。
* **cfg_path**(Path)：配置文件路径。
* **runtime**(LaunchRuntime)：运行时信息。
* **web_started**(bool)：web 是否已启动。

## 常量

```python
DEFAULT_GATEWAY_APP_FACTORY = 'openjiuwen.agent_evolving.agent_rl.online.gateway.app.proxy:create_app'
EXISTING_SERVICE_HEALTH_TIMEOUT = 30.0
```

- `DEFAULT_GATEWAY_APP_FACTORY`：uvicorn 应用工厂目标字符串。
- `EXISTING_SERVICE_HEALTH_TIMEOUT`：复用外部管理服务时的健康检查超时（秒）。

## def openjiuwen.agent_evolving.agent_rl.online.launcher.workspace.build_trajectory_env_updates

```python
def build_trajectory_env_updates(*, gateway_url: str, model_path: str, trajectory_batch_size: int, trajectory_mode: str, trajectory_tenant_id: str | None = None) -> dict[str, str]
```

返回 JiuwenClaw Rail（在线 RL 轨迹上传）所需的环境变量字典。

**参数**（均为关键字参数）：

* **gateway_url**(str)：gateway URL。
* **model_path**(str)：模型路径。
* **trajectory_batch_size**(int)：轨迹批大小。
* **trajectory_mode**(str)：轨迹模式。
* **trajectory_tenant_id**(str | None，可选)：租户 ID。默认值：`None`。

**返回**：

`dict[str, str]`，包含 `USE_RL_ONLINE_RAIL='1'`、`ENABLE_TRAJECTORY_COLLECTION='false'`、`TRAJECTORY_GATEWAY_URL`、`TRAJECTORY_TOKENIZER_PATH`、`TRAJECTORY_BATCH_SIZE`、`TRAJECTORY_MODE`，以及可选的 `RL_ONLINE_TENANT_ID`。

## def openjiuwen.agent_evolving.agent_rl.online.launcher.workspace.ensure_workspace

```python
def ensure_workspace(*, config_env: Path, gateway_url: str, model_name: str, model_path: str, trajectory_mode: str, trajectory_gateway_url: str | None = None, trajectory_batch_size: int = 8) -> None
```

确保 JiuwenClaw `.env` 文件指向 gateway。文件不存在时惰性导入并调用 `jiuwenclaw.utils.prepare_workspace(overwrite=False, preferred_language='zh')`；随后合并（保留既有键）一组值：`API_BASE`、`API_KEY='EMPTY'`、`MODEL_NAME`、`MODEL_PROVIDER='OpenAI'`、`WEB_USER_ID`、`CUSTOM_HEADERS`（JSON）、`EMBED_*`、`BROWSER_RUNTIME_MCP_ENABLED='0'`、`EVOLUTION_AUTO_SCAN='false'`，以及 `build_trajectory_env_updates` 的结果。写回完整文件。

**参数**（均为关键字参数）：

* **config_env**(Path)：`.env` 文件路径。
* **gateway_url**(str)：gateway URL。
* **model_name**(str)：模型名。
* **model_path**(str)：模型路径。
* **trajectory_mode**(str)：轨迹模式。
* **trajectory_gateway_url**(str | None，可选)：轨迹 gateway URL，为 `None` 时使用 `gateway_url`。默认值：`None`。
* **trajectory_batch_size**(int)：轨迹批大小。默认值：`8`。

## 被使用情况

- [run_online_rl.py](file:///Users/dongdong/Desktop/project/agent-core/examples/jiuwenrl_online/run_online_rl.py)：用户可见的启动脚本。导入 `build_arg_parser`、`build_cli_overrides`、`load_runtime_config`、`LauncherPaths`、`run_online_rl_loop`，构造 `LauncherPaths` 后调用主入口。这是唯一的生产外部消费者。
- [test_launcher_runner.py](file:///Users/dongdong/Desktop/project/agent-core/tests/unit_tests/agent_evolving/agent_rl/online/test_launcher_runner.py)：测试 `run_online_rl_loop` 信号关闭、`print_launch_summary`、`ensure_workspace`、`start_jiuwenclaw`。
- [README.md](file:///Users/dongdong/Desktop/project/agent-core/examples/jiuwenrl_online/README.md)：引用 `print_launch_summary` 输出。
