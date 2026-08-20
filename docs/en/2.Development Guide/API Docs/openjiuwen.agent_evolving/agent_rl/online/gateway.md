# openjiuwen.agent_evolving.agent_rl.online.gateway

Online-RL Gateway: a FastAPI reverse-proxy that sits in front of an LLM inference endpoint, records per-turn trajectories (with LLM-as-Judge scoring), and exposes a rail-v1 batch upload endpoint. It is launched by the online-RL launcher via a uvicorn factory string.

Subpackage layout: `upstream/` (transport and forwarding) → `trajectory/` (persistence and ingestion) → `app/` (FastAPI routes, wiring, CLI/factory). The sole production entry point is the uvicorn factory `openjiuwen.agent_evolving.agent_rl.online.gateway.app.proxy:create_app`.

## class openjiuwen.agent_evolving.agent_rl.online.gateway.config.GatewayConfig

```python
@dataclass
class GatewayConfig(port: int, host: str = "127.0.0.1", llm_url: str = "http://127.0.0.1:18000", judge_url: str = "http://127.0.0.1:18001", model_id: str = "", judge_model: str = "", request_timeout: float = 120.0, llm_api_key: str = "", judge_api_key: str = "", gateway_api_key: str = "", record_dir: str = "records", log_level: str = "INFO", dump_token_ids: bool = False, lora_repo_root: str = "", redis_url: str = "", upstream_max_retries: int = 2, upstream_retry_backoff_sec: float = 0.2, upstream_retry_max_backoff_sec: float = 2.0, disable_gateway_trajectory_collection: bool = False, single_user_default: bool = True)
```

Gateway runtime config dataclass.

**Fields**:

* **port**(int): Listen port (required).
* **host**(str): Listen address. Default: `"127.0.0.1"`.
* **llm_url**(str): Upstream LLM service URL. Default: `"http://127.0.0.1:18000"`.
* **judge_url**(str): Judge LLM service URL. Default: `"http://127.0.0.1:18001"`.
* **model_id**(str): Model ID. Default: `""`.
* **judge_model**(str): Judge model name. Default: `""`.
* **request_timeout**(float): Request timeout in seconds. Default: `120.0`.
* **llm_api_key**(str): Upstream LLM bearer key. Default: `""`.
* **judge_api_key**(str): Judge bearer key. Default: `""`.
* **gateway_api_key**(str): Gateway's own auth key. Default: `""`.
* **record_dir**(str): Record directory. Default: `"records"`.
* **log_level**(str): Log level. Default: `"INFO"`.
* **dump_token_ids**(bool): Whether to dump token IDs in logs. Default: `False`.
* **lora_repo_root**(str): LoRA repository root. Default: `""`.
* **redis_url**(str): Redis URL. Default: `""`.
* **upstream_max_retries**(int): Upstream max retries. Default: `2`.
* **upstream_retry_backoff_sec**(float): Upstream retry backoff base in seconds. Default: `0.2`.
* **upstream_retry_max_backoff_sec**(float): Upstream retry max backoff in seconds. Default: `2.0`.
* **disable_gateway_trajectory_collection**(bool): Disable trajectory collection. Default: `False`.
* **single_user_default**(bool): Single-user default mode. Default: `True`.

## Constant NON_STANDARD_BODY_KEYS

```python
NON_STANDARD_BODY_KEYS: set[str] = {"session_id", "session_done", "turn_type", "memory_scope", "user_id", "workspace_id"}
```

Non-standard keys stripped from the body before forwarding.

## def openjiuwen.agent_evolving.agent_rl.online.gateway.common.utc_now_iso

```python
def utc_now_iso() -> str
```

Returns the current UTC time as an ISO-8601 string.

## def openjiuwen.agent_evolving.agent_rl.online.gateway.common.fit_list

```python
def fit_list(values: list[float], expected_len: int) -> list[float]
```

Truncates or pads `values` with `0.0` so it has exactly `expected_len` entries; returns `[]` when `expected_len <= 0`.

## def openjiuwen.agent_evolving.agent_rl.online.gateway.message_utils.flatten_message_content

```python
def flatten_message_content(content: Any) -> str
```

Normalizes message content to a string: `str` returned as-is; `list` joins `text` parts of `{"type": "text"}` items with spaces; `None` returns `""`; otherwise `str(content)`.

## def openjiuwen.agent_evolving.agent_rl.online.gateway.message_utils.extract_last_user_instruction

```python
def extract_last_user_instruction(messages: list[dict]) -> str
```

Iterates `messages` in reverse, returning the flattened content of the last `role == "user"` message with non-empty text; returns `""` if none.

## class openjiuwen.agent_evolving.agent_rl.online.gateway.upstream.upstream_client.RetryPolicy

```python
@dataclass(frozen=True)
class RetryPolicy(max_retries: int = 2, backoff_base_sec: float = 0.2, backoff_max_sec: float = 2.0)
```

Frozen dataclass for upstream retry policy.

### def backoff_for_attempt

```python
def backoff_for_attempt(attempt: int) -> float
```

Exponential backoff `backoff_base_sec * 2**(attempt-1)`, clamped to `[0.0, backoff_max_sec]`; returns `0.0` for `attempt <= 0`.

## class openjiuwen.agent_evolving.agent_rl.online.gateway.upstream.upstream_client.UpstreamGatewayClient

```python
class UpstreamGatewayClient(Protocol)
```

Structurally-typed (`typing.Protocol`) interface for the upstream transport client.

### async def post_chat_completions

```python
async def post_chat_completions(*, json_body: dict[str, Any], headers: dict[str, str]) -> httpx.Response
```

POST to `/v1/chat/completions`.

### async def request

```python
async def request(*, method: str, url: str, params: dict[str, Any], headers: dict[str, str], content: bytes) -> httpx.Response
```

Sends an arbitrary request.

## class openjiuwen.agent_evolving.agent_rl.online.gateway.upstream.upstream_client.HTTPXUpstreamGatewayClient

```python
class HTTPXUpstreamGatewayClient(*, http_client: httpx.AsyncClient, llm_url: str, retry_policy: RetryPolicy | None = None)
```

httpx implementation of `UpstreamGatewayClient`.

**Parameters** (all keyword-only):

* **http_client**(httpx.AsyncClient): Async HTTP client.
* **llm_url**(str): Upstream LLM URL.
* **retry_policy**(RetryPolicy | None, optional): Retry policy; uses defaults when `None`. Default: `None`.

### async def post_chat_completions

```python
async def post_chat_completions(*, json_body: dict[str, Any], headers: dict[str, str]) -> httpx.Response
```

POSTs to `{llm_url}/v1/chat/completions` with retry via `_request_with_retry`.

### async def request

```python
async def request(*, method: str, url: str, params: dict[str, Any], headers: dict[str, str], content: bytes) -> httpx.Response
```

Sends an arbitrary request via `http_client.request(...)` with retry via `_request_with_retry`.

## class openjiuwen.agent_evolving.agent_rl.online.gateway.upstream.forwarder.Forwarder

```python
class Forwarder(*, upstream_client: UpstreamGatewayClient, model_id: str)
```

LLM request forwarder.

**Parameters** (all keyword-only):

* **upstream_client**(UpstreamGatewayClient): Transport client.
* **model_id**(str): Default model ID.

### async def forward

```python
async def forward(body: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]
```

Cleans the body (removes `NON_STANDARD_BODY_KEYS`, forces `stream=False`, drops `stream_options`, defaults `model`, sets `logprobs=True`/`top_logprobs=1`), calls `post_chat_completions`; raises `HTTPException(502)` on `httpx.HTTPStatusError` (detail = first 500 chars of response text); returns `resp.json()`.

## class openjiuwen.agent_evolving.agent_rl.online.gateway.trajectory.sample_recorder.SampleRecorder

```python
class SampleRecorder(*, sample_file: str, dump_token_ids: bool = False)
```

Lightweight sample counter with optional local JSONL dumps.

**Parameters** (all keyword-only):

* **sample_file**(str): JSONL file path.
* **dump_token_ids**(bool): Whether to dump full token IDs. Default: `False`.

### async def record_sample

```python
async def record_sample(sample: dict[str, Any]) -> None
```

Increments the counter; appends either the full sample (when `dump_token_ids`) or a trimmed version via `_sample_for_log`.

### async def snapshot_stats

```python
async def snapshot_stats() -> dict[str, int]
```

Returns `{"total_samples": self._total_samples}`.

## class openjiuwen.agent_evolving.agent_rl.online.gateway.trajectory.pending_judge_store.PendingJudgeStore

```python
class PendingJudgeStore(*, redis: Any, ttl_sec: int = 24 * 3600)
```

Redis-backed pending delayed-judge store for rail-v1 samples. Raises `ValueError` when `redis` is `None`.

**Parameters** (all keyword-only):

* **redis**(Any): Redis client.
* **ttl_sec**(int): Per-sample TTL in seconds. Default: `86400`.

### async def put

```python
async def put(sample: dict[str, Any]) -> None
```

Writes the sample JSON to a per-sample key (with TTL) and adds it to a per-session sorted set (scored by creation timestamp).

### async def get_by_session

```python
async def get_by_session(session_id: str) -> list[dict[str, Any]]
```

Returns all pending samples for a session via `ZRANGE` + `MGET`, decoding bytes.

### async def pop_one

```python
async def pop_one(session_id: str, trajectory_id: str, step_index: int) -> Optional[dict[str, Any]]
```

Atomically (via pipeline) deletes the sample key and removes it from the session sorted set; returns the decoded sample or `None`.

### async def pop_earliest

```python
async def pop_earliest(session_id: str) -> Optional[dict[str, Any]]
```

Returns the earliest pending sample (by sorted-set order) via `get_by_session` + `pop_one`.

### async def pop_all

```python
async def pop_all(session_id: str) -> list[dict[str, Any]]
```

Pops and returns all pending samples for a session.

## class openjiuwen.agent_evolving.agent_rl.online.gateway.trajectory.judge_dispatcher.JudgeDispatcher

```python
class JudgeDispatcher(*, pending_store: Any, record_sample: Any, judge_scorer: Optional[Any] = None)
```

Delayed-judge dispatcher.

**Parameters** (all keyword-only):

* **pending_store**(Any): Pending-sample store.
* **record_sample**(Any): `record_sample` coroutine.
* **judge_scorer**(Optional[Any], optional): A `JudgeScorer` instance. Default: `None`.

### async def on_prev_feedback

```python
async def on_prev_feedback(session_id: str, prev_feedback: Optional[dict[str, Any]]) -> int
```

Extracts feedback text; pops the earliest pending sample for the session; finalizes it (tag `"prev_feedback"`); records it; returns 1, else 0.

### async def on_session_done

```python
async def on_session_done(session_id: str) -> int
```

Pops all pending samples for the session; finalizes each (the last tagged `"session_done"`, others `"session_flush"`); records each; returns the count.

## class openjiuwen.agent_evolving.agent_rl.online.gateway.trajectory.rail_ingest.RailBatchIngestor

```python
class RailBatchIngestor(*, pending_judge_store: Any, judge_dispatcher: Any, default_user_id: str = "")
```

rail-v1 batch upload ingestor.

**Parameters** (all keyword-only):

* **pending_judge_store**(Any): Pending-sample store.
* **judge_dispatcher**(Any): Judge dispatcher.
* **default_user_id**(str): Default user ID. Default: `""`.

### async def ingest_rail_batch

```python
async def ingest_rail_batch(payload: dict[str, Any]) -> dict[str, Any]
```

Validates `protocol_version == "rail-v1"`, required `session_id`/`trajectory_id`, and that `samples` is a list; calls `judge_dispatcher.on_prev_feedback(session_id, payload.get("prev_feedback"))`; iterates samples, normalizing each via `_normalize_rail_sample` and putting them in the pending store, counting accepted/rejected (first error captured). Raises `ValueError` if all rejected. If `payload.get("session_done")`, calls `judge_dispatcher.on_session_done(session_id)`. Returns a dict: `protocol_version`, `session_id`, `trajectory_id`, `accepted`, `rejected`, `judged`, `session_flushed`.

## class openjiuwen.agent_evolving.agent_rl.online.gateway.trajectory.persistence.GatewayTrajectoryRuntime

```python
class GatewayTrajectoryRuntime(config: Any, *, redis: Optional[Any] = None)
```

Trajectory persistence and rail-ingest wiring; owns scored-sample persistence. Raises `ValueError` when `redis` is `None`.

**Parameters**:

* **config**(Any): `GatewayConfig` (with `record_dir`, `single_user_default`).
* **redis**(Optional[Any], optional): Redis client. Default: `None`.

### @property def store_backend

```python
@property
def store_backend() -> str
```

Returns `type(self._trajectory_store).__name__`.

### @property def rail_ingestor

```python
@property
def rail_ingestor() -> RailBatchIngestor
```

Returns the ingestor; raises `RuntimeError` if uninitialized.

### def set_judge_scorer

```python
def set_judge_scorer(judge_scorer: Optional[Any]) -> None
```

Rebuilds a `JudgeDispatcher` (with `pending_store`, `record_sample`, `judge_scorer`) and a `RailBatchIngestor` (with `pending_judge_store`, `judge_dispatcher`, `default_user_id`) and stores both.

### async def record_sample

```python
async def record_sample(sample: dict[str, Any]) -> None
```

Normalizes `user_id` (defaulting to `_default_user_id`, raising `ValueError` if missing); saves to the Redis trajectory store and records via `SampleRecorder`.

### async def snapshot_stats

```python
async def snapshot_stats() -> dict[str, Any]
```

Returns merged stats: `total_samples`, `trajectory_store_backend`, `trajectory_store_total`, `trajectory_store_pending`, `trajectory_store_training`, `trajectory_store_trained`, `trajectory_store_failed`.

## def openjiuwen.agent_evolving.agent_rl.online.gateway.trajectory.sample_payloads.build_sample

```python
def build_sample(*, user_id: str, session_id: str, turn_num: int, mode: str, io_mode: str, model: Any, messages: list[dict[str, Any]], tools: Any, assistant_message: dict[str, Any], usage: dict[str, Any], finish_reason: Optional[str], prompt_text: str, prompt_ids: list[int], response_text: str, response_ids: list[int], response_logprobs: list[float], tool_calls: list[dict[str, Any]], request_extras: Optional[dict[str, Any]] = None, sample_id: Optional[str] = None, created_at: Optional[str] = None, extra_fields: Optional[dict[str, Any]] = None) -> dict[str, Any]
```

Builds the normalized sample dict with `sample_id` (default uuid4), `created_at` (default `utc_now_iso()`), `user_id`, `session_id`, `turn_num`, `mode`, `io_mode`, `model`, nested `request` (messages, tools, **request_extras), nested `response` (message, usage, finish_reason), and nested `trajectory` (`input_ids = prompt_ids + response_ids`, `attention_mask`, `response_mask`, `prompt_text`, `prompt_ids`, `response_text`, `response_ids`, `response_logprobs`, `tool_calls`). Merges `extra_fields` at the top level if provided.

## def openjiuwen.agent_evolving.agent_rl.online.gateway.trajectory.sample_payloads.coerce_logprobs

```python
def coerce_logprobs(values: Any, expected_len: int) -> list[float]
```

Converts arbitrary logprob values to floats (skipping non-numeric), then `fit_list(out, expected_len)` to a fixed length.

## def openjiuwen.agent_evolving.agent_rl.online.gateway.app.bootstrap.build_app_from_config

```python
def build_app_from_config(config: GatewayConfig, *, http_client: Any = None, redis_client: Any = None) -> FastAPI
```

Production entry that assembles a FastAPI app from a `GatewayConfig`. Configures logging; creates/accepts a Redis async client (requires `redis_url` or an injected `redis_client`, else raises `ValueError`); creates/accepts an `httpx.AsyncClient`; constructs `HTTPXUpstreamGatewayClient` with a `RetryPolicy`; constructs `Forwarder`; constructs `GatewayTrajectoryRuntime(config, redis=redis_client)`; optionally constructs a `JudgeScorer` when `config.judge_url` is set, then `set_judge_scorer(judge_scorer)`; optionally loads `LoRARepository`; defines an inner `async def close_resources()` that closes owned http/redis clients; returns `build_gateway_app(config=..., forwarder=..., upstream_client=..., trajectory_runtime=..., close_resources=..., lora_repo=...)`.

**Parameters**:

* **config**(GatewayConfig): Config.
* **http_client**(Any, optional): Injected HTTP client. Default: `None`.
* **redis_client**(Any, optional): Injected Redis client. Default: `None`.

## def openjiuwen.agent_evolving.agent_rl.online.gateway.app.server.build_gateway_app

```python
def build_gateway_app(*, config: Any, forwarder: Forwarder, upstream_client: UpstreamGatewayClient, trajectory_runtime: GatewayTrajectoryRuntime, close_resources: Callable[[], Awaitable[None]], lora_repo: Any = None) -> FastAPI
```

Creates a `FastAPI(title="Online-RL Gateway", lifespan=...)` whose lifespan calls `close_resources()` on shutdown. Registers routes:

- `GET /health` → `{"status": "ok"}`
- `GET /v1/gateway/stats` → auth-gated; returns `_snapshot_stats(...)` with the current request count (guarded by an `asyncio.Lock`).
- `POST /v1/gateway/upload/batch` → auth-gated; calls `trajectory_runtime.rail_ingestor.ingest_rail_batch(payload)`, returns `{"ok": True, "result": result}`; `ValueError` → `HTTPException(400)`.
- `POST /v1/chat/completions` → auth-gated; increments counter; parses JSON body; resolves `user_id`; injects latest LoRA; pops `stream`; calls `_forward_chat_completions`; returns `StreamingResponse(stream_chat_response(...))` if the client wanted a stream, else `JSONResponse`.
- `GET/POST/PUT/PATCH/DELETE/OPTIONS/HEAD /{path:path}` (catch-all proxy) → auth-gated; forwards to `upstream_client.request(...)` targeting `{config.llm_url}/{path}`, strips hop-by-hop headers, returns `Response`.

**Parameters** (all keyword-only):

* **config**(Any): `GatewayConfig`.
* **forwarder**(Forwarder): Forwarder.
* **upstream_client**(UpstreamGatewayClient): Upstream client.
* **trajectory_runtime**(GatewayTrajectoryRuntime): Trajectory runtime.
* **close_resources**(Callable[[], Awaitable[None]]): Close-resources coroutine.
* **lora_repo**(Any, optional): LoRA repository. Default: `None`.

## async def openjiuwen.agent_evolving.agent_rl.online.gateway.app.http_helpers.ensure_gateway_auth

```python
async def ensure_gateway_auth(gateway_api_key: str, authorization: Optional[str]) -> None
```

No-op when `gateway_api_key` is empty; otherwise requires a `Bearer ` token matching the key, raising `HTTPException(401)` if missing and `HTTPException(403)` if invalid.

## def openjiuwen.agent_evolving.agent_rl.online.gateway.app.http_helpers.build_upstream_headers

```python
def build_upstream_headers(request: Request, *, llm_api_key: str) -> dict[str, str]
```

Copies inbound request headers, dropping `host`/`content-length`/`connection` and any `x-forwarded-*`; injects `Authorization: Bearer {llm_api_key}` when `llm_api_key` is set.

## async def openjiuwen.agent_evolving.agent_rl.online.gateway.app.http_helpers.stream_chat_response

```python
async def stream_chat_response(response_json: dict[str, Any], *, model_id: str)
```

Async generator (yields SSE strings) that wraps a non-streaming chat response into a synthetic SSE stream: emits a first chunk (with delta role/content/tool_calls/reasoning_content, token_ids, logprobs, prompt_token_ids), a final chunk (with `finish_reason` and `usage`), then `data: [DONE]`.

## def openjiuwen.agent_evolving.agent_rl.online.gateway.app.request_context.resolve_trace_id

```python
def resolve_trace_id(request: Request) -> str
```

Returns the `x-request-id` header if present, else a synthesized `uuid.uuid4().hex[:8]`.

## def openjiuwen.agent_evolving.agent_rl.online.gateway.app.request_context.require_messages

```python
def require_messages(body: dict[str, Any]) -> list[dict[str, Any]]
```

Validates that `body["messages"]` is a non-empty list; raises `HTTPException(400)` otherwise.

## def openjiuwen.agent_evolving.agent_rl.online.gateway.app.request_context.require_user_id

```python
def require_user_id(request: Request, config: Any) -> str
```

Reads the `x-user-id` header; if empty and `config.single_user_default` is truthy, falls back to a default ID; raises `HTTPException(400)` if still empty.

## def openjiuwen.agent_evolving.agent_rl.online.gateway.app.proxy.create_app

```python
def create_app()
```

Factory entry for `uvicorn ...gateway.app.proxy:create_app --factory`; builds config from env via `_build_config_from_env()` then returns `build_app_from_config(config)`.

## def openjiuwen.agent_evolving.agent_rl.online.gateway.app.proxy.main

```python
def main() -> None
```

CLI entry point. Parses args (`--host`, `--port` required, `--llm-url`, `--judge-url`, `--model-id`, `--judge-model`, `--record-dir`, `--lora-repo-root`, `--log-level`), builds a `GatewayConfig`, calls `build_app_from_config`, then runs via `uvicorn.run(...)`.

## Usage

- [services.py](file:///Users/dongdong/Desktop/project/agent-core/openjiuwen/agent_evolving/agent_rl/online/launcher/services.py): References the gateway only as a uvicorn factory **string** (`DEFAULT_GATEWAY_APP_FACTORY = 'openjiuwen.agent_evolving.agent_rl.online.gateway.app.proxy:create_app'`), spawning `uvicorn ... --factory` as a subprocess. This is the sole production entry point.
- [judge_scorer.py](file:///Users/dongdong/Desktop/project/agent-core/openjiuwen/agent_evolving/agent_rl/online/judge/judge_scorer.py): Provides `JudgeScorer`, constructed by `app/bootstrap.py`, whose `score(...)` is called by `JudgeDispatcher._finalize_sample`.
- [redis_trajectory_store.py](file:///Users/dongdong/Desktop/project/agent-core/openjiuwen/agent_evolving/agent_rl/storage/redis_trajectory_store.py): Provides `RedisTrajectoryStore`, constructed by `trajectory/persistence.py`; `save_sample(...)` and `stats()` are used by `GatewayTrajectoryRuntime`.
- [lora_repo.py](file:///Users/dongdong/Desktop/project/agent-core/openjiuwen/agent_evolving/agent_rl/storage/lora_repo.py): Provides `LoRARepository`, optionally constructed by `app/bootstrap.py`; `get_latest(user_id)` is called by `app/server.py`'s `_inject_latest_lora`.
- Tests: [test_forwarder.py](file:///Users/dongdong/Desktop/project/agent-core/tests/unit_tests/agent_evolving/agent_rl/online/gateway/test_forwarder.py), [test_processor_components.py](file:///Users/dongdong/Desktop/project/agent-core/tests/unit_tests/agent_evolving/agent_rl/online/gateway/test_processor_components.py), [test_upstream_client.py](file:///Users/dongdong/Desktop/project/agent-core/tests/unit_tests/agent_evolving/agent_rl/online/gateway/test_upstream_client.py), [test_gateway_support.py](file:///Users/dongdong/Desktop/project/agent-core/tests/unit_tests/agent_evolving/agent_rl/online/test_gateway_support.py), [test_online_gateway_e2e.py](file:///Users/dongdong/Desktop/project/agent-core/tests/system_tests/agent_evolving/agent_rl/online/test_online_gateway_e2e.py).
