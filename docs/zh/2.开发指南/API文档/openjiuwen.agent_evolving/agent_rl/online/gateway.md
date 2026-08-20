# openjiuwen.agent_evolving.agent_rl.online.gateway

Online-RL Gateway：基于 FastAPI 的反向代理，位于 LLM 推理端点之前，记录每轮轨迹（含 LLM-as-Judge 评分），并暴露 rail-v1 批量上传端点。由在线 RL launcher 通过 uvicorn 工厂字符串启动。

子包结构：`upstream/`（传输与转发）→ `trajectory/`（持久化与摄入）→ `app/`（FastAPI 路由、装配、CLI/工厂）。唯一的生产入口是 uvicorn 工厂 `openjiuwen.agent_evolving.agent_rl.online.gateway.app.proxy:create_app`。

## class openjiuwen.agent_evolving.agent_rl.online.gateway.config.GatewayConfig

```python
@dataclass
class GatewayConfig(port: int, host: str = "127.0.0.1", llm_url: str = "http://127.0.0.1:18000", judge_url: str = "http://127.0.0.1:18001", model_id: str = "", judge_model: str = "", request_timeout: float = 120.0, llm_api_key: str = "", judge_api_key: str = "", gateway_api_key: str = "", record_dir: str = "records", log_level: str = "INFO", dump_token_ids: bool = False, lora_repo_root: str = "", redis_url: str = "", upstream_max_retries: int = 2, upstream_retry_backoff_sec: float = 0.2, upstream_retry_max_backoff_sec: float = 2.0, disable_gateway_trajectory_collection: bool = False, single_user_default: bool = True)
```

gateway 运行时配置 dataclass。

**字段**：

* **port**(int)：监听端口（必填）。
* **host**(str)：监听地址。默认值：`"127.0.0.1"`。
* **llm_url**(str)：上游 LLM 服务 URL。默认值：`"http://127.0.0.1:18000"`。
* **judge_url**(str)：judge LLM 服务 URL。默认值：`"http://127.0.0.1:18001"`。
* **model_id**(str)：模型 ID。默认值：`""`。
* **judge_model**(str)：judge 模型名。默认值：`""`。
* **request_timeout**(float)：请求超时（秒）。默认值：`120.0`。
* **llm_api_key**(str)：上游 LLM Bearer key。默认值：`""`。
* **judge_api_key**(str)：judge Bearer key。默认值：`""`。
* **gateway_api_key**(str)：gateway 自身鉴权 key。默认值：`""`。
* **record_dir**(str)：记录目录。默认值：`"records"`。
* **log_level**(str)：日志级别。默认值：`"INFO"`。
* **dump_token_ids**(bool)：是否在日志中输出 token ID。默认值：`False`。
* **lora_repo_root**(str)：LoRA 仓库根目录。默认值：`""`。
* **redis_url**(str)：Redis URL。默认值：`""`。
* **upstream_max_retries**(int)：上游最大重试次数。默认值：`2`。
* **upstream_retry_backoff_sec**(float)：上游重试退避基数（秒）。默认值：`0.2`。
* **upstream_retry_max_backoff_sec**(float)：上游重试最大退避（秒）。默认值：`2.0`。
* **disable_gateway_trajectory_collection**(bool)：禁用轨迹采集。默认值：`False`。
* **single_user_default**(bool)：单用户默认模式。默认值：`True`。

## 常量 NON_STANDARD_BODY_KEYS

```python
NON_STANDARD_BODY_KEYS: set[str] = {"session_id", "session_done", "turn_type", "memory_scope", "user_id", "workspace_id"}
```

转发前从 body 中剥离的非标准键。

## def openjiuwen.agent_evolving.agent_rl.online.gateway.common.utc_now_iso

```python
def utc_now_iso() -> str
```

返回当前 UTC 时间的 ISO-8601 字符串。

## def openjiuwen.agent_evolving.agent_rl.online.gateway.common.fit_list

```python
def fit_list(values: list[float], expected_len: int) -> list[float]
```

截断或用 `0.0` 填充 `values` 使其恰好有 `expected_len` 项；`expected_len <= 0` 时返回 `[]`。

## def openjiuwen.agent_evolving.agent_rl.online.gateway.message_utils.flatten_message_content

```python
def flatten_message_content(content: Any) -> str
```

将消息 content 归一化为字符串：`str` 原样返回；`list` 时以空格连接 `{"type": "text"}` 项的 `text`；`None` 返回 `""`；否则 `str(content)`。

## def openjiuwen.agent_evolving.agent_rl.online.gateway.message_utils.extract_last_user_instruction

```python
def extract_last_user_instruction(messages: list[dict]) -> str
```

逆序遍历 `messages`，返回最后一个 `role == "user"` 且文本非空消息的扁平化内容；无则返回 `""`。

## class openjiuwen.agent_evolving.agent_rl.online.gateway.upstream.upstream_client.RetryPolicy

```python
@dataclass(frozen=True)
class RetryPolicy(max_retries: int = 2, backoff_base_sec: float = 0.2, backoff_max_sec: float = 2.0)
```

上游重试策略冻结 dataclass。

### def backoff_for_attempt

```python
def backoff_for_attempt(attempt: int) -> float
```

指数退避 `backoff_base_sec * 2**(attempt-1)`，钳制到 `[0.0, backoff_max_sec]`；`attempt <= 0` 时返回 `0.0`。

## class openjiuwen.agent_evolving.agent_rl.online.gateway.upstream.upstream_client.UpstreamGatewayClient

```python
class UpstreamGatewayClient(Protocol)
```

上游传输客户端的结构化类型（`typing.Protocol`）接口。

### async def post_chat_completions

```python
async def post_chat_completions(*, json_body: dict[str, Any], headers: dict[str, str]) -> httpx.Response
```

POST `/v1/chat/completions`。

### async def request

```python
async def request(*, method: str, url: str, params: dict[str, Any], headers: dict[str, str], content: bytes) -> httpx.Response
```

发送任意请求。

## class openjiuwen.agent_evolving.agent_rl.online.gateway.upstream.upstream_client.HTTPXUpstreamGatewayClient

```python
class HTTPXUpstreamGatewayClient(*, http_client: httpx.AsyncClient, llm_url: str, retry_policy: RetryPolicy | None = None)
```

`UpstreamGatewayClient` 的 httpx 实现。

**参数**（均为关键字参数）：

* **http_client**(httpx.AsyncClient)：异步 HTTP 客户端。
* **llm_url**(str)：上游 LLM URL。
* **retry_policy**(RetryPolicy | None，可选)：重试策略，为 `None` 时使用默认。默认值：`None`。

### async def post_chat_completions

```python
async def post_chat_completions(*, json_body: dict[str, Any], headers: dict[str, str]) -> httpx.Response
```

POST 到 `{llm_url}/v1/chat/completions`，经 `_request_with_retry` 重试。

### async def request

```python
async def request(*, method: str, url: str, params: dict[str, Any], headers: dict[str, str], content: bytes) -> httpx.Response
```

通过 `http_client.request(...)` 发送任意请求，经 `_request_with_retry` 重试。

## class openjiuwen.agent_evolving.agent_rl.online.gateway.upstream.forwarder.Forwarder

```python
class Forwarder(*, upstream_client: UpstreamGatewayClient, model_id: str)
```

LLM 请求转发器。

**参数**（均为关键字参数）：

* **upstream_client**(UpstreamGatewayClient)：传输客户端。
* **model_id**(str)：默认模型 ID。

### async def forward

```python
async def forward(body: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]
```

清理 body（移除 `NON_STANDARD_BODY_KEYS`、强制 `stream=False`、丢弃 `stream_options`、默认 `model`、设置 `logprobs=True`/`top_logprobs=1`），调用 `post_chat_completions`，`httpx.HTTPStatusError` 时抛 `HTTPException(502)`（detail 为响应文本前 500 字符），返回 `resp.json()`。

## class openjiuwen.agent_evolving.agent_rl.online.gateway.trajectory.sample_recorder.SampleRecorder

```python
class SampleRecorder(*, sample_file: str, dump_token_ids: bool = False)
```

轻量样本计数与可选本地 JSONL 转储。

**参数**（均为关键字参数）：

* **sample_file**(str)：JSONL 文件路径。
* **dump_token_ids**(bool)：是否转储完整 token ID。默认值：`False`。

### async def record_sample

```python
async def record_sample(sample: dict[str, Any]) -> None
```

递增计数器；追加完整样本（`dump_token_ids=True`）或经 `_sample_for_log` 裁剪后的版本。

### async def snapshot_stats

```python
async def snapshot_stats() -> dict[str, int]
```

返回 `{"total_samples": self._total_samples}`。

## class openjiuwen.agent_evolving.agent_rl.online.gateway.trajectory.pending_judge_store.PendingJudgeStore

```python
class PendingJudgeStore(*, redis: Any, ttl_sec: int = 24 * 3600)
```

Redis 后端的延迟 judge 待处理样本存储。`redis` 为 `None` 时抛 `ValueError`。

**参数**（均为关键字参数）：

* **redis**(Any)：Redis 客户端。
* **ttl_sec**(int)：每条样本 TTL（秒）。默认值：`86400`。

### async def put

```python
async def put(sample: dict[str, Any]) -> None
```

将样本 JSON 写入按样本键（带 TTL）并加入按会话的有序集合（按创建时间戳评分）。

### async def get_by_session

```python
async def get_by_session(session_id: str) -> list[dict[str, Any]]
```

通过 `ZRANGE` + `MGET` 返回某会话所有待处理样本（解码 bytes）。

### async def pop_one

```python
async def pop_one(session_id: str, trajectory_id: str, step_index: int) -> Optional[dict[str, Any]]
```

通过 pipeline 原子删除样本键并从会话有序集合移除，返回解码样本或 `None`。

### async def pop_earliest

```python
async def pop_earliest(session_id: str) -> Optional[dict[str, Any]]
```

通过 `get_by_session` + `pop_one` 返回最早的待处理样本。

### async def pop_all

```python
async def pop_all(session_id: str) -> list[dict[str, Any]]
```

弹出并返回某会话所有待处理样本。

## class openjiuwen.agent_evolving.agent_rl.online.gateway.trajectory.judge_dispatcher.JudgeDispatcher

```python
class JudgeDispatcher(*, pending_store: Any, record_sample: Any, judge_scorer: Optional[Any] = None)
```

延迟 judge 分发器。

**参数**（均为关键字参数）：

* **pending_store**(Any)：待处理样本存储。
* **record_sample**(Any)：`record_sample` 协程。
* **judge_scorer**(Optional[Any]，可选)：`JudgeScorer` 实例。默认值：`None`。

### async def on_prev_feedback

```python
async def on_prev_feedback(session_id: str, prev_feedback: Optional[dict[str, Any]]) -> int
```

提取反馈文本；弹出该会话最早的待处理样本并终结（标签 `"prev_feedback"`）；记录；返回 1，否则 0。

### async def on_session_done

```python
async def on_session_done(session_id: str) -> int
```

弹出该会话所有待处理样本并逐个终结（最后一个标签 `"session_done"`，其余 `"session_flush"`）；记录；返回计数。

## class openjiuwen.agent_evolving.agent_rl.online.gateway.trajectory.rail_ingest.RailBatchIngestor

```python
class RailBatchIngestor(*, pending_judge_store: Any, judge_dispatcher: Any, default_user_id: str = "")
```

rail-v1 批量上传摄入器。

**参数**（均为关键字参数）：

* **pending_judge_store**(Any)：待处理样本存储。
* **judge_dispatcher**(Any)：judge 分发器。
* **default_user_id**(str)：默认用户 ID。默认值：`""`。

### async def ingest_rail_batch

```python
async def ingest_rail_batch(payload: dict[str, Any]) -> dict[str, Any]
```

校验 `protocol_version == "rail-v1"`、必填 `session_id`/`trajectory_id`、`samples` 为列表；调用 `judge_dispatcher.on_prev_feedback(session_id, payload.get("prev_feedback"))`；逐个经 `_normalize_rail_sample` 归一化并放入待处理存储，统计 accepted/rejected（首个错误捕获）。全部拒绝时抛 `ValueError`。若 `payload.get("session_done")` 调用 `judge_dispatcher.on_session_done(session_id)`。返回 dict：`protocol_version`、`session_id`、`trajectory_id`、`accepted`、`rejected`、`judged`、`session_flushed`。

## class openjiuwen.agent_evolving.agent_rl.online.gateway.trajectory.persistence.GatewayTrajectoryRuntime

```python
class GatewayTrajectoryRuntime(config: Any, *, redis: Optional[Any] = None)
```

轨迹持久化与 rail 摄入装配；持有评分样本持久化。`redis` 为 `None` 时抛 `ValueError`。

**参数**：

* **config**(Any)：`GatewayConfig`（含 `record_dir`、`single_user_default`）。
* **redis**(Optional[Any]，可选)：Redis 客户端。默认值：`None`。

### @property def store_backend

```python
@property
def store_backend() -> str
```

返回 `type(self._trajectory_store).__name__`。

### @property def rail_ingestor

```python
@property
def rail_ingestor() -> RailBatchIngestor
```

返回摄入器；未初始化时抛 `RuntimeError`。

### def set_judge_scorer

```python
def set_judge_scorer(judge_scorer: Optional[Any]) -> None
```

重建 `JudgeDispatcher`（传入 `pending_store`、`record_sample`、`judge_scorer`）与 `RailBatchIngestor`（传入 `pending_judge_store`、`judge_dispatcher`、`default_user_id`）并存储。

### async def record_sample

```python
async def record_sample(sample: dict[str, Any]) -> None
```

归一化 `user_id`（默认 `_default_user_id`，缺失抛 `ValueError`）；保存到 Redis 轨迹存储并通过 `SampleRecorder` 记录。

### async def snapshot_stats

```python
async def snapshot_stats() -> dict[str, Any]
```

返回合并统计：`total_samples`、`trajectory_store_backend`、`trajectory_store_total`、`trajectory_store_pending`、`trajectory_store_training`、`trajectory_store_trained`、`trajectory_store_failed`。

## def openjiuwen.agent_evolving.agent_rl.online.gateway.trajectory.sample_payloads.build_sample

```python
def build_sample(*, user_id: str, session_id: str, turn_num: int, mode: str, io_mode: str, model: Any, messages: list[dict[str, Any]], tools: Any, assistant_message: dict[str, Any], usage: dict[str, Any], finish_reason: Optional[str], prompt_text: str, prompt_ids: list[int], response_text: str, response_ids: list[int], response_logprobs: list[float], tool_calls: list[dict[str, Any]], request_extras: Optional[dict[str, Any]] = None, sample_id: Optional[str] = None, created_at: Optional[str] = None, extra_fields: Optional[dict[str, Any]] = None) -> dict[str, Any]
```

构建归一化样本字典。包含 `sample_id`（默认 uuid4）、`created_at`（默认 `utc_now_iso()`）、`user_id`、`session_id`、`turn_num`、`mode`、`io_mode`、`model`、嵌套 `request`（messages、tools、**request_extras）、嵌套 `response`（message、usage、finish_reason）、嵌套 `trajectory`（`input_ids = prompt_ids + response_ids`、`attention_mask`、`response_mask`、`prompt_text`、`prompt_ids`、`response_text`、`response_ids`、`response_logprobs`、`tool_calls`）。合并 `extra_fields` 到顶层。

## def openjiuwen.agent_evolving.agent_rl.online.gateway.trajectory.sample_payloads.coerce_logprobs

```python
def coerce_logprobs(values: Any, expected_len: int) -> list[float]
```

将任意 logprob 值转为 float（跳过非数值），再 `fit_list(out, expected_len)` 到固定长度。

## def openjiuwen.agent_evolving.agent_rl.online.gateway.app.bootstrap.build_app_from_config

```python
def build_app_from_config(config: GatewayConfig, *, http_client: Any = None, redis_client: Any = None) -> FastAPI
```

从 `GatewayConfig` 装配 FastAPI 应用的生产入口。配置日志；创建/接受 Redis 异步客户端（需 `redis_url` 或注入 `redis_client`，否则抛 `ValueError`）；创建/接受 `httpx.AsyncClient`；构造 `HTTPXUpstreamGatewayClient`（带 `RetryPolicy`）；构造 `Forwarder`；构造 `GatewayTrajectoryRuntime(config, redis=redis_client)`；可选构造 `JudgeScorer`（当 `config.judge_url` 设置）并 `set_judge_scorer`；可选加载 `LoRARepository`；定义内部 `async def close_resources()` 关闭持有的 http/redis 客户端；返回 `build_gateway_app(config=..., forwarder=..., upstream_client=..., trajectory_runtime=..., close_resources=..., lora_repo=...)`。

**参数**：

* **config**(GatewayConfig)：配置。
* **http_client**(Any，可选)：注入的 HTTP 客户端。默认值：`None`。
* **redis_client**(Any，可选)：注入的 Redis 客户端。默认值：`None`。

## def openjiuwen.agent_evolving.agent_rl.online.gateway.app.server.build_gateway_app

```python
def build_gateway_app(*, config: Any, forwarder: Forwarder, upstream_client: UpstreamGatewayClient, trajectory_runtime: GatewayTrajectoryRuntime, close_resources: Callable[[], Awaitable[None]], lora_repo: Any = None) -> FastAPI
```

创建 `FastAPI(title="Online-RL Gateway", lifespan=...)`，lifespan 中调用 `close_resources()`。注册路由：

- `GET /health` → `{"status": "ok"}`
- `GET /v1/gateway/stats` → 鉴权；返回 `_snapshot_stats(...)` 与当前请求计数（`asyncio.Lock` 保护）。
- `POST /v1/gateway/upload/batch` → 鉴权；调用 `trajectory_runtime.rail_ingestor.ingest_rail_batch(payload)`，返回 `{"ok": True, "result": result}`；`ValueError` → `HTTPException(400)`。
- `POST /v1/chat/completions` → 鉴权；递增计数；解析 JSON body；解析 `user_id`；注入最新 LoRA；弹出 `stream`；调用 `_forward_chat_completions`；流式时返回 `StreamingResponse(stream_chat_response(...))`，否则 `JSONResponse`。
- `GET/POST/PUT/PATCH/DELETE/OPTIONS/HEAD /{path:path}`（全量代理）→ 鉴权；转发至 `upstream_client.request(...)` 目标 `{config.llm_url}/{path}`，剥离逐跳头，返回 `Response`。

**参数**（均为关键字参数）：

* **config**(Any)：`GatewayConfig`。
* **forwarder**(Forwarder)：转发器。
* **upstream_client**(UpstreamGatewayClient)：上游客户端。
* **trajectory_runtime**(GatewayTrajectoryRuntime)：轨迹运行时。
* **close_resources**(Callable[[], Awaitable[None]])：关闭资源协程。
* **lora_repo**(Any，可选)：LoRA 仓库。默认值：`None`。

## async def openjiuwen.agent_evolving.agent_rl.online.gateway.app.http_helpers.ensure_gateway_auth

```python
async def ensure_gateway_auth(gateway_api_key: str, authorization: Optional[str]) -> None
```

`gateway_api_key` 为空时空操作；否则要求 `Bearer ` token 匹配，缺失抛 `HTTPException(401)`，不匹配抛 `HTTPException(403)`。

## def openjiuwen.agent_evolving.agent_rl.online.gateway.app.http_helpers.build_upstream_headers

```python
def build_upstream_headers(request: Request, *, llm_api_key: str) -> dict[str, str]
```

复制入站请求头，丢弃 `host`/`content-length`/`connection` 与所有 `x-forwarded-*`；`llm_api_key` 设置时注入 `Authorization: Bearer {llm_api_key}`。

## async def openjiuwen.agent_evolving.agent_rl.online.gateway.app.http_helpers.stream_chat_response

```python
async def stream_chat_response(response_json: dict[str, Any], *, model_id: str)
```

异步生成器（产出 SSE 字符串），将非流式 chat 响应包装为合成 SSE 流：首个 chunk（含 delta role/content/tool_calls/reasoning_content、token_ids、logprobs、prompt_token_ids）、最终 chunk（含 `finish_reason` 与 `usage`），随后 `data: [DONE]`。

## def openjiuwen.agent_evolving.agent_rl.online.gateway.app.request_context.resolve_trace_id

```python
def resolve_trace_id(request: Request) -> str
```

返回 `x-request-id` 头，无则合成 `uuid.uuid4().hex[:8]`。

## def openjiuwen.agent_evolving.agent_rl.online.gateway.app.request_context.require_messages

```python
def require_messages(body: dict[str, Any]) -> list[dict[str, Any]]
```

校验 `body["messages"]` 为非空列表；否则抛 `HTTPException(400)`。

## def openjiuwen.agent_evolving.agent_rl.online.gateway.app.request_context.require_user_id

```python
def require_user_id(request: Request, config: Any) -> str
```

读取 `x-user-id` 头；为空且 `config.single_user_default` 为真时回退默认 ID；仍为空抛 `HTTPException(400)`。

## def openjiuwen.agent_evolving.agent_rl.online.gateway.app.proxy.create_app

```python
def create_app()
```

uvicorn 工厂入口（`uvicorn ...gateway.app.proxy:create_app --factory`）。经 `_build_config_from_env()` 构建配置后返回 `build_app_from_config(config)`。

## def openjiuwen.agent_evolving.agent_rl.online.gateway.app.proxy.main

```python
def main() -> None
```

CLI 入口。解析参数（`--host`、`--port` 必填，`--llm-url`、`--judge-url`、`--model-id`、`--judge-model`、`--record-dir`、`--lora-repo-root`、`--log-level`），构造 `GatewayConfig`，调用 `build_app_from_config`，`uvicorn.run(...)`。

## 被使用情况

- [services.py](file:///Users/dongdong/Desktop/project/agent-core/openjiuwen/agent_evolving/agent_rl/online/launcher/services.py)：以字符串 `DEFAULT_GATEWAY_APP_FACTORY = 'openjiuwen.agent_evolving.agent_rl.online.gateway.app.proxy:create_app'` 引用 gateway，通过 `uvicorn ... --factory` 子进程启动。这是 gateway 唯一的生产入口。
- [judge_scorer.py](file:///Users/dongdong/Desktop/project/agent-core/openjiuwen/agent_evolving/agent_rl/online/judge/judge_scorer.py)：提供 `JudgeScorer`，由 `app/bootstrap.py` 构造，`JudgeDispatcher._finalize_sample` 调用 `score(...)`。
- [redis_trajectory_store.py](file:///Users/dongdong/Desktop/project/agent-core/openjiuwen/agent_evolving/agent_rl/storage/redis_trajectory_store.py)：提供 `RedisTrajectoryStore`，由 `trajectory/persistence.py` 构造，`save_sample(...)` 与 `stats()` 被 `GatewayTrajectoryRuntime` 使用。
- [lora_repo.py](file:///Users/dongdong/Desktop/project/agent-core/openjiuwen/agent_evolving/agent_rl/storage/lora_repo.py)：提供 `LoRARepository`，可选由 `app/bootstrap.py` 构造，`get_latest(user_id)` 由 `app/server.py` 的 `_inject_latest_lora` 调用。
- 测试：[test_forwarder.py](file:///Users/dongdong/Desktop/project/agent-core/tests/unit_tests/agent_evolving/agent_rl/online/gateway/test_forwarder.py)、[test_processor_components.py](file:///Users/dongdong/Desktop/project/agent-core/tests/unit_tests/agent_evolving/agent_rl/online/gateway/test_processor_components.py)、[test_upstream_client.py](file:///Users/dongdong/Desktop/project/agent-core/tests/unit_tests/agent_evolving/agent_rl/online/gateway/test_upstream_client.py)、[test_gateway_support.py](file:///Users/dongdong/Desktop/project/agent-core/tests/unit_tests/agent_evolving/agent_rl/online/test_gateway_support.py)、[test_online_gateway_e2e.py](file:///Users/dongdong/Desktop/project/agent-core/tests/system_tests/agent_evolving/agent_rl/online/test_online_gateway_e2e.py)。
