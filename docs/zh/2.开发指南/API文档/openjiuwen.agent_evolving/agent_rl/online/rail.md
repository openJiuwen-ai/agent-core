# openjiuwen.agent_evolving.agent_rl.online.rail

JiuwenClaw / DeepAgent 侧的在线 RL 轨迹采集与上传流水线（"rail-v1" 客户端）。钩入智能体 invoke 生命周期，从 LLM 响应中捕获每轮 token 级数据（`prompt_ids`、`completion_token_ids`、`logprobs`），将完成的轨迹转换为 `RailV1Batch`，并异步上传至在线 RL gateway 的 `POST /v1/gateway/upload/batch`。

## class openjiuwen.agent_evolving.agent_rl.online.rail.converter.PerTurnSample

```python
@dataclass
class PerTurnSample(trajectory_id: str, step_index: int, session_id: str, model_id: str, messages: list[dict[str, Any]], response: dict[str, Any], response_text: str, response_tokens: Optional[list[int]] = None, logprobs: Optional[list[float]] = None, prompt_ids: Optional[list[int]] = None, render_fingerprint: dict[str, Any] = field(default_factory=dict), tools: Any = None, meta: dict[str, Any] = field(default_factory=dict))
```

单轮 LLM 调用的完整 token 级样本。

**字段**：

* **trajectory_id**(str)：轨迹 ID。
* **step_index**(int)：步骤索引。
* **session_id**(str)：会话 ID。
* **model_id**(str)：模型 ID。
* **messages**(list[dict[str, Any]])：消息列表（OpenAI 格式）。
* **response**(dict[str, Any])：LLM 响应消息。
* **response_text**(str)：响应文本。
* **response_tokens**(Optional[list[int]])：响应 token ID 列表。默认值：`None`。
* **logprobs**(Optional[list[float]])：logprob 列表。默认值：`None`。
* **prompt_ids**(Optional[list[int]])：prompt token ID 列表。默认值：`None`。
* **render_fingerprint**(dict[str, Any])：渲染指纹。默认值：`{}`。
* **tools**(Any)：工具定义。默认值：`None`。
* **meta**(dict[str, Any])：元数据。默认值：`{}`。

## class openjiuwen.agent_evolving.agent_rl.online.rail.converter.TrajectoryMeta

```python
@dataclass
class TrajectoryMeta(trajectory_id: str, session_id: str, status: str = "ok", total_turns: int = 0, started_at: float = field(default_factory=time.time), ended_at: float = field(default_factory=time.time), extra: dict[str, Any] = field(default_factory=dict))
```

轨迹级元数据。

**字段**：

* **trajectory_id**(str)：轨迹 ID。
* **session_id**(str)：会话 ID。
* **status**(str)：状态。默认值：`"ok"`。
* **total_turns**(int)：总轮数。默认值：`0`。
* **started_at**(float)：开始时间戳。默认值：当前时间。
* **ended_at**(float)：结束时间戳。默认值：当前时间。
* **extra**(dict[str, Any])：附加信息。默认值：`{}`。

## class openjiuwen.agent_evolving.agent_rl.online.rail.converter.RailV1Batch

```python
@dataclass
class RailV1Batch(protocol_version: str, session_id: str, tenant_id: Optional[str], trajectory_id: str, model_id: str, samples: list[PerTurnSample], trajectory_meta: TrajectoryMeta, prev_feedback: Optional[dict[str, Any]] = None, session_done: bool = False)
```

rail-v1 上传载荷。

**字段**：

* **protocol_version**(str)：协议版本（如 `"rail-v1"`）。
* **session_id**(str)：会话 ID。
* **tenant_id**(Optional[str])：租户/用户 ID。
* **trajectory_id**(str)：轨迹 ID。
* **model_id**(str)：模型 ID。
* **samples**(list[PerTurnSample])：每轮样本列表。
* **trajectory_meta**(TrajectoryMeta)：轨迹元数据。
* **prev_feedback**(Optional[dict[str, Any]])：前次反馈。默认值：`None`。
* **session_done**(bool)：会话是否结束。默认值：`False`。

### def to_dict

```python
def to_dict() -> dict[str, Any]
```

返回 `_json_value(asdict(self))`，即可 JSON 序列化的字典。

## class openjiuwen.agent_evolving.agent_rl.online.rail.converter.OnlineTrajectoryConverter

```python
class OnlineTrajectoryConverter(*, tenant_id: Optional[str] = None, model_id: Optional[str] = None, session_done: bool = False)
```

将完整的 Rail 轨迹转换为 rail-v1 上传载荷。

**参数**（均为关键字参数）：

* **tenant_id**(Optional[str]，可选)：租户 ID。默认值：`None`。
* **model_id**(Optional[str]，可选)：模型 ID。默认值：`None`。
* **session_done**(bool)：会话是否结束。默认值：`False`。

### def convert

```python
def convert(trajectory: Trajectory, *, tenant_id: Optional[str] = None, session_done: Optional[bool] = None) -> RailV1Batch
```

迭代 `trajectory_steps(trajectory)`，对每个 `step.kind == "llm"` 且 `step.detail` 为 `LLMCallDetail` 的步骤构建 `PerTurnSample`。token 字段优先取步骤级字段（`step.completion_token_ids`、`step.prompt_token_ids` / `step.meta["prompt_ids"]`、`step.logprobs`），回退到 `extract_token_ids` / `extract_prompt_ids` / `extract_logprobs`。构建 `TrajectoryMeta`（含 `source`/`case_id`/`cost`）。返回 `protocol_version="rail-v1"` 的 `RailV1Batch`。

**参数**：

* **trajectory**(Trajectory)：轨迹对象。
* **tenant_id**(Optional[str]，可选)：覆盖租户 ID。默认值：`None`。
* **session_done**(Optional[bool]，可选)：覆盖会话结束标志。默认值：`None`。

**返回**：

`RailV1Batch`。

### @staticmethod def extract_prev_feedback

```python
@staticmethod
def extract_prev_feedback(trajectory: Trajectory) -> Optional[dict[str, Any]]
```

从轨迹 LLM 步骤中首个非空用户消息提取 `{"raw_user_text": <text>, "source": "first_user_msg_of_next_batch"}`；无则返回 `None`。

## def openjiuwen.agent_evolving.agent_rl.online.rail.llm_response.extract_token_ids

```python
def extract_token_ids(response: Any) -> Optional[list[int]]
```

尽力从 OpenAI 风格 / vLLM 响应中提取响应 token ID 列表（vLLM `completion_token_ids`/`token_ids`/`response_tokens`）。

**参数**：

* **response**(Any)：LLM 响应对象（dict 或属性对象）。

**返回**：

`Optional[list[int]]`，token ID 列表；不存在时返回 `None`。

## def openjiuwen.agent_evolving.agent_rl.online.rail.llm_response.extract_prompt_ids

```python
def extract_prompt_ids(response: Any) -> Optional[list[int]]
```

尽力从 vLLM 载荷中提取 prompt token ID 列表（`prompt_token_ids`/`prompt_ids`）。

**参数**：

* **response**(Any)：LLM 响应对象。

**返回**：

`Optional[list[int]]`，prompt token ID 列表；不存在时返回 `None`。

## def openjiuwen.agent_evolving.agent_rl.online.rail.llm_response.extract_logprobs

```python
def extract_logprobs(response: Any) -> Optional[list[float]]
```

尽力从 OpenAI 风格 / dict 响应中提取 logprob 列表。查找 `choices[0].logprobs` 与顶层 `logprobs`，兼容原始列表与 `{content: [{logprob}]}` 结构，每项经 `float()` 转换。

**参数**：

* **response**(Any)：LLM 响应对象。

**返回**：

`Optional[list[float]]`，logprob 列表；不存在或为空时返回 `None`。

## class openjiuwen.agent_evolving.agent_rl.online.rail.online_rail.RLOnlineRail

```python
class RLOnlineRail(EvolutionRail)
```

钩入智能体 invoke 生命周期的 rail，用于：(a) 启用 vLLM token/logprob 捕获；(b) 为每个 LLM 步骤补充 token 级数据；(c) 轨迹完成后转换并入队上传。继承自 `EvolutionRail`。

类属性：`priority = 100`。

```python
class RLOnlineRail(*, session_id: str, gateway_endpoint: str, tenant_id: Optional[str] = None, uploader: Optional[TrajectoryUploader] = None, converter: Optional[OnlineTrajectoryConverter] = None, session_done_on_invoke_end: bool = True, **kwargs: Any)
```

**参数**（均为关键字参数）：

* **session_id**(str)：会话 ID。
* **gateway_endpoint**(str)：gateway 上传端点 URL。
* **tenant_id**(Optional[str]，可选)：租户 ID。默认值：`None`。
* **uploader**(Optional[TrajectoryUploader]，可选)：上传器实例，为 `None` 时默认 `TrajectoryUploader(gateway_endpoint)`。默认值：`None`。
* **converter**(Optional[OnlineTrajectoryConverter]，可选)：转换器实例，为 `None` 时默认 `OnlineTrajectoryConverter(tenant_id=tenant_id)`。默认值：`None`。
* **session_done_on_invoke_end**(bool)：invoke 结束时是否标记会话完成。默认值：`True`。
* **kwargs**(Any)：透传给基类 `EvolutionRail`。

### async def _on_before_invoke

```python
async def _on_before_invoke(ctx: AgentCallbackContext) -> None
```

重置 `_llm_step_count`，记录 `_started_at`，启用 token 捕获，解析 `_tenant_id`，初始化 `_builder` 元数据（`session_id`、`source="rl_online"`、`tenant_id`、`status`、`started_at`）。

### async def _on_after_model_call

```python
async def _on_after_model_call(ctx: AgentCallbackContext) -> None
```

递增步骤计数；对最后一个 `llm` 步骤，当基类 rail 未填充时，用 `extract_*` 辅助函数补充 `prompt_token_ids`/`completion_token_ids`/`logprobs`；在 `step.meta` 中标注 `turn_id`、`source`、`tenant_id`。

### async def on_model_exception

```python
async def on_model_exception(ctx: AgentCallbackContext) -> None
```

设置 builder 元数据 `status="invoke_error"` 与 `exception=repr(ctx.exception)`。

### async def _on_after_invoke

```python
async def _on_after_invoke(ctx: AgentCallbackContext) -> None
```

调用 `_reset_trajectory_builder()`（继承自基类），使每个上传轨迹限定于单次 invoke。

### async def run_evolution

```python
async def run_evolution(trajectory: Trajectory, ctx: Optional[AgentCallbackContext] = None, *, snapshot: Optional[dict[str, Any]] = None) -> None
```

设置轨迹资源属性（`ended_at`、`tenant_id`、`status`），调用 `self._converter.convert(trajectory, tenant_id=..., session_done=...)`，若 `batch.samples` 非空则 `await self._uploader.enqueue(batch)`；无样本时 debug 日志。

## class openjiuwen.agent_evolving.agent_rl.online.rail.uploader.TrajectoryUploader

```python
class TrajectoryUploader(gateway_endpoint: str, *, capacity: int = 256, max_retries: int = 5, backoff_base_sec: float = 0.2, wal_dir: str | Path = "records/rail_v1_wal", api_key: str = "", client: Optional[httpx.AsyncClient] = None, timeout: float = 30.0)
```

异步上传 rail-v1 批次到 gateway 端点 `POST /v1/gateway/upload/batch`，带容量受限的内存队列（满时丢弃最旧）、指数退避重试、JSON 文件 WAL（失败时持久化）。

**参数**：

* **gateway_endpoint**(str)：gateway 基础 URL。
* **capacity**(int)：内存队列容量。默认值：`256`。
* **max_retries**(int)：最大重试次数。默认值：`5`。
* **backoff_base_sec**(float)：退避基数（秒）。默认值：`0.2`。
* **wal_dir**(str | Path)：WAL 目录。默认值：`"records/rail_v1_wal"`。
* **api_key**(str)：Bearer API key。默认值：`""`。
* **client**(Optional[httpx.AsyncClient]，可选)：外部注入的 HTTP 客户端。默认值：`None`。
* **timeout**(float)：请求超时（秒）。默认值：`30.0`。

### async def enqueue

```python
async def enqueue(batch: Any) -> None
```

序列化 `batch.to_dict()`（或 `dict(batch)`），队列满时丢弃最旧条目并递增 `queue_drop_total`，追加后确保 worker 任务已启动并通知条件变量。

### async def shutdown

```python
async def shutdown() -> None
```

设置 `_closed`，通知所有等待者，await worker，排空剩余队列入 WAL，关闭持有的 httpx 客户端。

### async def replay_wal

```python
async def replay_wal() -> None
```

回放 `wal_dir` 中所有 `*.json` 文件（按名排序），逐个 POST，成功则删除文件。

## def openjiuwen.agent_evolving.agent_rl.online.rail.factory.is_rl_online_rail_enabled_from_env

```python
def is_rl_online_rail_enabled_from_env() -> bool
```

当环境变量 `USE_RL_ONLINE_RAIL`（去除空白并小写后）为 `"1"`、`"true"`、`"yes"`、`"on"` 之一时返回 `True`。

## def openjiuwen.agent_evolving.agent_rl.online.rail.factory.build_rl_online_rail_from_env

```python
def build_rl_online_rail_from_env() -> Optional[RLOnlineRail]
```

从环境变量构建完整装配的 `RLOnlineRail`。`USE_RL_ONLINE_RAIL` 未启用时返回 `None`。

**返回**：

`Optional[RLOnlineRail]`，装配后的 rail；禁用或导入失败时返回 `None`（失败时记录警告）。

**说明**：

读取的环境变量：

| 环境变量 | 作用 | 默认值 |
|---------|------|-------|
| `USE_RL_ONLINE_RAIL` | 启用开关 | — |
| `TRAJECTORY_GATEWAY_URL` | gateway 基础 URL | `http://127.0.0.1:18080` |
| `TRAJECTORY_GATEWAY_API_KEY` | 可选 Bearer token | `""` |
| `RL_ONLINE_TENANT_ID` | 租户/用户命名空间 | `None` |

## 被使用情况

- [\_\_init\_\_.py](file:///Users/dongdong/Desktop/project/agent-core/openjiuwen/agent_evolving/agent_rl/__init__.py)：通过 `__getattr__` 懒导出 `RLOnlineRail`（规范外部导入路径为 `openjiuwen.agent_evolving.agent_rl.RLOnlineRail`），并列入 `__all__`。
- [workspace.py](file:///Users/dongdong/Desktop/project/agent-core/openjiuwen/agent_evolving/agent_rl/online/launcher/workspace.py) 与 [services.py](file:///Users/dongdong/Desktop/project/agent-core/openjiuwen/agent_evolving/agent_rl/online/launcher/services.py)：为生成的 JiuwenClaw 进程设置 `USE_RL_ONLINE_RAIL=1`、`TRAJECTORY_GATEWAY_URL`、`RL_ONLINE_TENANT_ID` 等环境变量（不直接导入 rail 类）。
- [server.py](file:///Users/dongdong/Desktop/project/agent-core/openjiuwen/agent_evolving/agent_rl/online/gateway/app/server.py)：注册 `POST /v1/gateway/upload/batch` 端点，接收上传；由 [rail_ingest.py](file:///Users/dongdong/Desktop/project/agent-core/openjiuwen/agent_evolving/agent_rl/online/gateway/trajectory/rail_ingest.py) 中的 `RailBatchIngestor` 消费。
- [test_rl_online_rail.py](file:///Users/dongdong/Desktop/project/agent-core/tests/unit_tests/agent_evolving/agent_rl/online/test_rl_online_rail.py)：导入并构造 `RLOnlineRail`。
- [test_online_gateway_e2e.py](file:///Users/dongdong/Desktop/project/agent-core/tests/system_tests/agent_evolving/agent_rl/online/test_online_gateway_e2e.py)：通过 `importlib` 动态导入 `RLOnlineRail` 与 `TrajectoryUploader`。
- [test_gateway_support.py](file:///Users/dongdong/Desktop/project/agent-core/tests/unit_tests/agent_evolving/agent_rl/online/test_gateway_support.py)：导入 `OnlineTrajectoryConverter` 并测试 `convert` / `to_dict`。
