# openjiuwen.agent_evolving.agent_rl.online.rail

JiuwenClaw / DeepAgent-side online RL trajectory collection and upload pipeline (the "rail-v1" client). It hooks into the agent invoke lifecycle, captures per-turn token-level data (`prompt_ids`, `completion_token_ids`, `logprobs`) from LLM responses, converts each completed trajectory into a `RailV1Batch`, and uploads it asynchronously to the online-RL gateway at `POST /v1/gateway/upload/batch`.

## class openjiuwen.agent_evolving.agent_rl.online.rail.converter.PerTurnSample

```python
@dataclass
class PerTurnSample(trajectory_id: str, step_index: int, session_id: str, model_id: str, messages: list[dict[str, Any]], response: dict[str, Any], response_text: str, response_tokens: Optional[list[int]] = None, logprobs: Optional[list[float]] = None, prompt_ids: Optional[list[int]] = None, render_fingerprint: dict[str, Any] = field(default_factory=dict), tools: Any = None, meta: dict[str, Any] = field(default_factory=dict))
```

Complete token-level sample for a single LLM turn.

**Fields**:

* **trajectory_id**(str): Trajectory ID.
* **step_index**(int): Step index.
* **session_id**(str): Session ID.
* **model_id**(str): Model ID.
* **messages**(list[dict[str, Any]]): Message list (OpenAI format).
* **response**(dict[str, Any]): LLM response message.
* **response_text**(str): Response text.
* **response_tokens**(Optional[list[int]]): Response token ID list. Default: `None`.
* **logprobs**(Optional[list[float]]): Logprob list. Default: `None`.
* **prompt_ids**(Optional[list[int]]): Prompt token ID list. Default: `None`.
* **render_fingerprint**(dict[str, Any]): Render fingerprint. Default: `{}`.
* **tools**(Any): Tool definitions. Default: `None`.
* **meta**(dict[str, Any]): Metadata. Default: `{}`.

## class openjiuwen.agent_evolving.agent_rl.online.rail.converter.TrajectoryMeta

```python
@dataclass
class TrajectoryMeta(trajectory_id: str, session_id: str, status: str = "ok", total_turns: int = 0, started_at: float = field(default_factory=time.time), ended_at: float = field(default_factory=time.time), extra: dict[str, Any] = field(default_factory=dict))
```

Trajectory-level metadata.

**Fields**:

* **trajectory_id**(str): Trajectory ID.
* **session_id**(str): Session ID.
* **status**(str): Status. Default: `"ok"`.
* **total_turns**(int): Total turns. Default: `0`.
* **started_at**(float): Start timestamp. Default: current time.
* **ended_at**(float): End timestamp. Default: current time.
* **extra**(dict[str, Any]): Extra info. Default: `{}`.

## class openjiuwen.agent_evolving.agent_rl.online.rail.converter.RailV1Batch

```python
@dataclass
class RailV1Batch(protocol_version: str, session_id: str, tenant_id: Optional[str], trajectory_id: str, model_id: str, samples: list[PerTurnSample], trajectory_meta: TrajectoryMeta, prev_feedback: Optional[dict[str, Any]] = None, session_done: bool = False)
```

rail-v1 upload payload.

**Fields**:

* **protocol_version**(str): Protocol version (e.g. `"rail-v1"`).
* **session_id**(str): Session ID.
* **tenant_id**(Optional[str]): Tenant/user ID.
* **trajectory_id**(str): Trajectory ID.
* **model_id**(str): Model ID.
* **samples**(list[PerTurnSample]): Per-turn sample list.
* **trajectory_meta**(TrajectoryMeta): Trajectory metadata.
* **prev_feedback**(Optional[dict[str, Any]]): Previous feedback. Default: `None`.
* **session_done**(bool): Whether the session is done. Default: `False`.

### def to_dict

```python
def to_dict() -> dict[str, Any]
```

Returns `_json_value(asdict(self))`, a JSON-serializable dict.

## class openjiuwen.agent_evolving.agent_rl.online.rail.converter.OnlineTrajectoryConverter

```python
class OnlineTrajectoryConverter(*, tenant_id: Optional[str] = None, model_id: Optional[str] = None, session_done: bool = False)
```

Converts a complete Rail trajectory into a rail-v1 upload payload.

**Parameters** (all keyword-only):

* **tenant_id**(Optional[str], optional): Tenant ID. Default: `None`.
* **model_id**(Optional[str], optional): Model ID. Default: `None`.
* **session_done**(bool): Whether the session is done. Default: `False`.

### def convert

```python
def convert(trajectory: Trajectory, *, tenant_id: Optional[str] = None, session_done: Optional[bool] = None) -> RailV1Batch
```

Iterates `trajectory_steps(trajectory)`; for each step where `step.kind == "llm"` and `step.detail` is an `LLMCallDetail`, builds a `PerTurnSample`. Token fields prefer step-level fields (`step.completion_token_ids`, `step.prompt_token_ids` / `step.meta["prompt_ids"]`, `step.logprobs`) and fall back to `extract_token_ids` / `extract_prompt_ids` / `extract_logprobs`. Builds `TrajectoryMeta` (with `source`/`case_id`/`cost`). Returns a `RailV1Batch` with `protocol_version="rail-v1"`.

**Parameters**:

* **trajectory**(Trajectory): Trajectory object.
* **tenant_id**(Optional[str], optional): Override tenant ID. Default: `None`.
* **session_done**(Optional[bool], optional): Override session-done flag. Default: `None`.

**Returns**:

`RailV1Batch`.

### @staticmethod def extract_prev_feedback

```python
@staticmethod
def extract_prev_feedback(trajectory: Trajectory) -> Optional[dict[str, Any]]
```

Extracts `{"raw_user_text": <text>, "source": "first_user_msg_of_next_batch"}` from the first non-empty user message in the trajectory's LLM steps; returns `None` if none.

## def openjiuwen.agent_evolving.agent_rl.online.rail.llm_response.extract_token_ids

```python
def extract_token_ids(response: Any) -> Optional[list[int]]
```

Best-effort extraction of response token ID list from OpenAI-style / vLLM responses (vLLM `completion_token_ids`/`token_ids`/`response_tokens`).

**Parameters**:

* **response**(Any): LLM response object (dict or attribute-bearing object).

**Returns**:

`Optional[list[int]]`, the token ID list; `None` if absent.

## def openjiuwen.agent_evolving.agent_rl.online.rail.llm_response.extract_prompt_ids

```python
def extract_prompt_ids(response: Any) -> Optional[list[int]]
```

Best-effort extraction of prompt token ID list from vLLM payloads (`prompt_token_ids`/`prompt_ids`).

**Parameters**:

* **response**(Any): LLM response object.

**Returns**:

`Optional[list[int]]`, the prompt token ID list; `None` if absent.

## def openjiuwen.agent_evolving.agent_rl.online.rail.llm_response.extract_logprobs

```python
def extract_logprobs(response: Any) -> Optional[list[float]]
```

Best-effort extraction of logprob list from OpenAI-style / dict responses. Looks at `choices[0].logprobs` and top-level `logprobs`, handling both raw-list and `{content: [{logprob}]}` shapes; each item coerced via `float()`.

**Parameters**:

* **response**(Any): LLM response object.

**Returns**:

`Optional[list[float]]`, the logprob list; `None` if absent or empty.

## class openjiuwen.agent_evolving.agent_rl.online.rail.online_rail.RLOnlineRail

```python
class RLOnlineRail(EvolutionRail)
```

A rail that hooks into the agent invoke lifecycle to (a) enable vLLM token/logprob capture; (b) enrich each LLM step with token-level data; (c) on trajectory completion, convert + enqueue the batch for upload. Inherits from `EvolutionRail`.

Class attribute: `priority = 100`.

```python
class RLOnlineRail(*, session_id: str, gateway_endpoint: str, tenant_id: Optional[str] = None, uploader: Optional[TrajectoryUploader] = None, converter: Optional[OnlineTrajectoryConverter] = None, session_done_on_invoke_end: bool = True, **kwargs: Any)
```

**Parameters** (all keyword-only):

* **session_id**(str): Session ID.
* **gateway_endpoint**(str): Gateway upload endpoint URL.
* **tenant_id**(Optional[str], optional): Tenant ID. Default: `None`.
* **uploader**(Optional[TrajectoryUploader], optional): Uploader instance; when `None`, defaults to `TrajectoryUploader(gateway_endpoint)`. Default: `None`.
* **converter**(Optional[OnlineTrajectoryConverter], optional): Converter instance; when `None`, defaults to `OnlineTrajectoryConverter(tenant_id=tenant_id)`. Default: `None`.
* **session_done_on_invoke_end**(bool): Whether to mark the session done on invoke end. Default: `True`.
* **kwargs**(Any): Forwarded to the base `EvolutionRail`.

### async def _on_before_invoke

```python
async def _on_before_invoke(ctx: AgentCallbackContext) -> None
```

Resets `_llm_step_count`, records `_started_at`, enables token capture, resolves `_tenant_id`, and seeds `self._builder` meta (`session_id`, `source="rl_online"`, `tenant_id`, `status`, `started_at`).

### async def _on_after_model_call

```python
async def _on_after_model_call(ctx: AgentCallbackContext) -> None
```

Increments step count; for the last `llm` step, fills `prompt_token_ids`/`completion_token_ids`/`logprobs` via the `extract_*` helpers when the base rail left them empty; annotates `step.meta` with `turn_id`, `source`, `tenant_id`.

### async def on_model_exception

```python
async def on_model_exception(ctx: AgentCallbackContext) -> None
```

Sets builder meta `status="invoke_error"` and `exception=repr(ctx.exception)`.

### async def _on_after_invoke

```python
async def _on_after_invoke(ctx: AgentCallbackContext) -> None
```

Calls `_reset_trajectory_builder()` (inherited from the base class) so each uploaded trajectory is scoped to a single invoke.

### async def run_evolution

```python
async def run_evolution(trajectory: Trajectory, ctx: Optional[AgentCallbackContext] = None, *, snapshot: Optional[dict[str, Any]] = None) -> None
```

Sets trajectory resource attributes (`ended_at`, `tenant_id`, `status`), calls `self._converter.convert(trajectory, tenant_id=..., session_done=...)`, and if `batch.samples` is non-empty, `await self._uploader.enqueue(batch)`; debug-logs if no samples.

## class openjiuwen.agent_evolving.agent_rl.online.rail.uploader.TrajectoryUploader

```python
class TrajectoryUploader(gateway_endpoint: str, *, capacity: int = 256, max_retries: int = 5, backoff_base_sec: float = 0.2, wal_dir: str | Path = "records/rail_v1_wal", api_key: str = "", client: Optional[httpx.AsyncClient] = None, timeout: float = 30.0)
```

Asynchronously uploads rail-v1 batches to the gateway endpoint `POST /v1/gateway/upload/batch`, with a bounded in-memory queue (drops oldest when full), exponential-backoff retries, and a JSON-file write-ahead-log (WAL) for durability on failure.

**Parameters**:

* **gateway_endpoint**(str): Gateway base URL.
* **capacity**(int): In-memory queue capacity. Default: `256`.
* **max_retries**(int): Max retries. Default: `5`.
* **backoff_base_sec**(float): Backoff base in seconds. Default: `0.2`.
* **wal_dir**(str | Path): WAL directory. Default: `"records/rail_v1_wal"`.
* **api_key**(str): Bearer API key. Default: `""`.
* **client**(Optional[httpx.AsyncClient], optional): Externally injected HTTP client. Default: `None`.
* **timeout**(float): Request timeout in seconds. Default: `30.0`.

### async def enqueue

```python
async def enqueue(batch: Any) -> None
```

Serializes `batch.to_dict()` (or `dict(batch)`), drops the oldest entry (and increments `queue_drop_total`) when at capacity, appends, ensures a worker task, and notifies the condition variable.

### async def shutdown

```python
async def shutdown() -> None
```

Sets `_closed`, notifies all waiters, awaits the worker, drains remaining queue into the WAL, and closes the owned httpx client.

### async def replay_wal

```python
async def replay_wal() -> None
```

Replays any `*.json` files in `wal_dir` (sorted), posting each and unlinking on success.

## def openjiuwen.agent_evolving.agent_rl.online.rail.factory.is_rl_online_rail_enabled_from_env

```python
def is_rl_online_rail_enabled_from_env() -> bool
```

Returns `True` when env `USE_RL_ONLINE_RAIL` (stripped, lowercased) is one of `"1"`, `"true"`, `"yes"`, `"on"`.

## def openjiuwen.agent_evolving.agent_rl.online.rail.factory.build_rl_online_rail_from_env

```python
def build_rl_online_rail_from_env() -> Optional[RLOnlineRail]
```

Builds a fully-wired `RLOnlineRail` from environment variables. Returns `None` unless `USE_RL_ONLINE_RAIL` is truthy.

**Returns**:

`Optional[RLOnlineRail]`, the assembled rail; `None` when disabled or import fails (failure logs a warning).

**Notes**:

Environment variables consumed:

| Variable | Purpose | Default |
|----------|---------|---------|
| `USE_RL_ONLINE_RAIL` | Enable switch | — |
| `TRAJECTORY_GATEWAY_URL` | Gateway base URL | `http://127.0.0.1:18080` |
| `TRAJECTORY_GATEWAY_API_KEY` | Optional Bearer token | `""` |
| `RL_ONLINE_TENANT_ID` | Tenant/user namespace | `None` |

## Usage

- [\_\_init\_\_.py](file:///Users/dongdong/Desktop/project/agent-core/openjiuwen/agent_evolving/agent_rl/__init__.py): Lazy-exports `RLOnlineRail` via `__getattr__` (canonical external import path is `openjiuwen.agent_evolving.agent_rl.RLOnlineRail`) and lists it in `__all__`.
- [workspace.py](file:///Users/dongdong/Desktop/project/agent-core/openjiuwen/agent_evolving/agent_rl/online/launcher/workspace.py) and [services.py](file:///Users/dongdong/Desktop/project/agent-core/openjiuwen/agent_evolving/agent_rl/online/launcher/services.py): Set `USE_RL_ONLINE_RAIL=1`, `TRAJECTORY_GATEWAY_URL`, `RL_ONLINE_TENANT_ID` and other env vars for the spawned JiuwenClaw process (do not import rail classes directly).
- [server.py](file:///Users/dongdong/Desktop/project/agent-core/openjiuwen/agent_evolving/agent_rl/online/gateway/app/server.py): Registers the `POST /v1/gateway/upload/batch` endpoint that receives uploads; consumed by the `RailBatchIngestor` in [rail_ingest.py](file:///Users/dongdong/Desktop/project/agent-core/openjiuwen/agent_evolving/agent_rl/online/gateway/trajectory/rail_ingest.py).
- [test_rl_online_rail.py](file:///Users/dongdong/Desktop/project/agent-core/tests/unit_tests/agent_evolving/agent_rl/online/test_rl_online_rail.py): Imports and constructs `RLOnlineRail`.
- [test_online_gateway_e2e.py](file:///Users/dongdong/Desktop/project/agent-core/tests/system_tests/agent_evolving/agent_rl/online/test_online_gateway_e2e.py): Dynamically imports `RLOnlineRail` and `TrajectoryUploader` via `importlib`.
- [test_gateway_support.py](file:///Users/dongdong/Desktop/project/agent-core/tests/unit_tests/agent_evolving/agent_rl/online/test_gateway_support.py): Imports `OnlineTrajectoryConverter` and tests `convert` / `to_dict`.
