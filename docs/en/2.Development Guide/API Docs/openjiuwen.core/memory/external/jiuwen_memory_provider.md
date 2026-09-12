# openjiuwen.core.memory.external.jiuwen_memory_provider

`openjiuwen.core.memory.external.jiuwen_memory_provider` is the **Jiuwen (JiuwenMemory / mem2)-based external memory provider** implementation in openJiuwen, inheriting from `MemoryProvider`. It selects one of two backends **at construction time** while exposing the unified `MemoryProvider` interface:

- `mode="server"` (default): talks to a remote JiuwenMemory HTTP service via an `httpx` async client (`POST /v1/<verb>` + `GET /healthz`). No local engine is built; only a network client is needed. Best for production where the mem2 engine runs as its own process/container;
- `mode="sdk"`: builds a local JiuwenMemory kernel **in-process** via `from jiuwen_memory.api import assemble` and calls it directly — no HTTP hop, no separate server to run. Requires the `JiuwenMemory` package installed (`pip install JiuwenMemory`). Best for embedding the engine into the host process (single process).

Both backends expose the same tools (`mem2_search` / `mem2_add`) and the same system prompt block, so the surrounding consumer is agnostic to the mode. Key capabilities include:

- Ranked semantic search across keyword (BM25), vector, and graph recall channels;
- Two write semantics: verbatim storage and LLM extraction (`infer`);
- Scope mapping based on `tenant_id` + `user_id`;
- Separate read/write HTTP timeouts;
- A built-in circuit breaker in server mode that short-circuits after consecutive failures reach a threshold, preventing cascading failures.

## Module Constants

| Constant | Type | Description |
|----------|------|-------------|
| `_READ_TIMEOUT` | `float` | Read request timeout in seconds. Default: `30.0`. |
| `_WRITE_TIMEOUT` | `float` | Write request timeout in seconds. Default: `120.0`. `/v1/add` with `infer=true` triggers real-LLM extraction + dedup, so writes get a larger ceiling than reads. |
| `_BREAKER_THRESHOLD` | `int` | Circuit breaker threshold: the breaker opens after this many consecutive failures. Default: `5`. |
| `_BREAKER_COOLDOWN_SECS` | `float` | Circuit breaker cooldown duration in seconds. Default: `120.0`. |
| `_DEFAULT_TOP_K` | `int` | Default recall count. Default: `10`. |
| `_MAX_TOP_K` | `int` | Maximum recall count. Default: `50`. |

Two tool schema dictionaries are defined at module level: `SEARCH_SCHEMA` (`mem2_search`) and `ADD_SCHEMA` (`mem2_add`), used to declare the memory tools exposed by this provider in `get_tool_schemas()`.

> **Note on star imports (`from ... import *`)**: The `__all__` at the end of the module only explicitly exports `JiuwenMemoryProvider`. `SEARCH_SCHEMA` and `ADD_SCHEMA` are not in `__all__` and cannot be referenced via star import; use explicit imports instead (e.g., `from openjiuwen.core.memory.external.jiuwen_memory_provider import SEARCH_SCHEMA`).

## class openjiuwen.core.memory.external.jiuwen_memory_provider.JiuwenMemoryProvider

```
class openjiuwen.core.memory.external.jiuwen_memory_provider.JiuwenMemoryProvider(MemoryProvider)
```

`JiuwenMemoryProvider` is a Jiuwen (JiuwenMemory / mem2)-based external memory provider with two switchable backends.

**Features**:

- Dual-backend architecture: `server` (remote HTTP service) / `sdk` (in-process kernel), fixed at construction and **cannot be switched afterwards**;
- Both backends expose the same tools and system prompt block, keeping consumers mode-agnostic;
- `mem2_search`: ranked search across BM25 / vector / graph recall channels;
- `mem2_add`: two write semantics — verbatim storage and LLM extraction (`infer`);
- `tenant_id` (org axis) + `user_id` (user/session axis) form the Scope; configurable globally or overridable per call;
- Separate read/write timeouts in server mode, with a built-in circuit breaker.

**Configuration**:

- `mode`: backend mode, `"server"` (default) or `"sdk"` (case-insensitive);
- `base_url`: JiuwenMemory server URL (server mode only);
- `api_key`: optional bearer token (server mode only);
- `config_dict`: kernel assembly config for sdk mode (two-level namespace dict);
- `tenant_id` / `user_id`: the two axes of Scope;
- `read_timeout` / `write_timeout`: HTTP timeouts (server mode only);
- `infer_turns` / `save_assistant_turns`: write policies for `sync_turn`.

**Usage Example**:

```python
>>> from openjiuwen.core.memory.external.jiuwen_memory_provider import JiuwenMemoryProvider
>>>
>>> # server mode: talk to a remote JiuwenMemory HTTP service
>>> provider = JiuwenMemoryProvider(
>>>     mode="server",
>>>     base_url="http://127.0.0.1:8137",
>>>     api_key="mem2-xxx",
>>>     tenant_id="default",
>>>     user_id="user_123",
>>> )
>>> await provider.initialize()
>>> resp = await provider.handle_tool_call("mem2_search", {"query": "user preferences", "top_k": 5})
>>>
>>> # sdk mode: build a JiuwenMemory kernel in-process
>>> provider = JiuwenMemoryProvider(
>>>     mode="sdk",
>>>     tenant_id="default",
>>>     user_id="user_123",
>>>     config_dict={...},
>>> )
>>> await provider.initialize()
>>> resp = await provider.handle_tool_call(
>>>     "mem2_add",
>>>     {"content": "The project uses Python 3.11", "infer": True},
>>> )
```

### __init__

```
JiuwenMemoryProvider(
    *,
    mode: str = "server",
    base_url: str = "http://127.0.0.1:8137",
    api_key: str = "",
    tenant_id: str = "default",
    user_id: str = "",
    read_timeout: float = 30.0,
    write_timeout: float = 120.0,
    config_dict: dict[str, Any] | None = None,
    infer_turns: bool = True,
    save_assistant_turns: bool = False,
)
```

Initialize the Jiuwen memory provider. All parameters are **keyword-only**. **No network requests are made and no kernel is imported/built**; only local state is set up. Actual connection and kernel assembly happen in `initialize()`.

**Parameters**:

* **mode**(str, optional): Backend mode. `"server"` (default) talks to a remote HTTP service; `"sdk"` builds a local kernel in-process. Case-insensitive (normalized via `strip().lower()` internally). Fixed at construction; cannot be switched afterwards.
* **base_url**(str, optional): JiuwenMemory server URL; trailing `/` is stripped. Default: `"http://127.0.0.1:8137"`. **Server mode only**.
* **api_key**(str, optional): Optional bearer token; when non-empty it is sent as the `Authorization: Bearer <api_key>` header. **Server mode only**. Default: `""`.
* **tenant_id**(str, optional): The org axis of `Scope` (`Scope.org`). Default: `"default"`.
* **user_id**(str, optional): The user/session axis of Scope, mapped to mem2's `scope`. Default: `""`.
* **read_timeout**(float, optional): Read request timeout in seconds. **Server mode only**. Default: `30.0`.
* **write_timeout**(float, optional): Write request timeout in seconds; effectively clamped to at least the module constant `_WRITE_TIMEOUT` (`120.0`). **Server mode only**. Default: `120.0`.
* **config_dict**(dict[str, Any] | None, optional): JiuwenMemory assembly config (two-level namespace dict), built into a kernel config via `Config.from_dict()`; `None` means the built-in in-memory defaults (good for tests, lost on restart). Runtime policy overrides go under `config_dict["globals"]["policies"]`. **SDK mode only**. Default: `None`.
* **infer_turns**(bool, optional): Whether `sync_turn` distills user turns into deduped facts via the extraction+dedup path (default `True`). Set `False` to store raw conversation text verbatim. Default: `True`.
* **save_assistant_turns**(bool, optional): Whether `sync_turn` also persists the assistant reply (default `False`, i.e., **only the user turn is stored**, since the assistant reply is derivable and low-value as a memory). Default: `False`.

**Behavior**:

- Raises `ValueError` if `mode` is not one of `("server", "sdk")`;
- Constructs the corresponding internal backend (`_ServerBackend` or `_SDKBackend`) based on `mode`; all subsequent interface calls are delegated to it.

### name

```
@property
def name(self) -> str
```

Returns the provider identifier, fixed as `"jiuwen_memory"`.

**Returns**:

* **str**: `"jiuwen_memory"`.

### mode

```
@property
def mode(self) -> str
```

Returns the current backend mode.

**Returns**:

* **str**: `"server"` or `"sdk"` (normalized to lowercase).

### is_available

```
def is_available(self) -> bool
```

Check if the provider is configured and ready. **No network calls**.

**Returns**:

* **bool**: In server mode, returns `True` if `base_url` is non-empty; in sdk mode, always returns `True` (the kernel is assembled lazily in `initialize()`).

### is_initialized

```
@property
def is_initialized(self) -> bool
```

Check if the provider has completed initialization (i.e., `initialize()` has been called).

**Returns**:

* **bool**: `True` if the backend is initialized, otherwise `False`.

### async initialize

```
async def initialize(self, **kwargs) -> None
```

Initialize the provider; behavior varies by mode. If already initialized, the backend overwrites the relevant state and continues. **Connection probe failures do not raise**, so initialization can complete even when the service is not yet up.

**Parameters**:

* ****kwargs**(Any, optional): Override parameters. The provider layer first handles `infer_turns` / `save_assistant_turns` (overriding construction-time values); the rest is passed through to the backend.

**Server mode behavior**:

- Supported overrides: `tenant_id`, `user_id` (alias `scope_id` accepted), `base_url`, `api_key`;
- Raises `ValueError("Mem2 server base_url is required.")` if `base_url` is empty;
- Raises `RuntimeError` if `httpx` is not installed (hint: `pip install httpx`);
- Creates an `httpx.AsyncClient` (with `base_url` as the prefix, `Content-Type: application/json` and the optional `Authorization` header injected, default timeout set to `read_timeout`);
- Probes liveness via `GET /healthz`: status `200` logs a successful connection; non-`200` logs a warning; connection errors only log a warning — **probe failure does not block initialization** (the server may come up later); subsequent real request failures are handled by the circuit breaker;
- Finally sets `_is_initialized = True`.

**SDK mode behavior**:

- Supported overrides: `tenant_id`, `user_id` (alias `scope_id` accepted), `config_dict`, `infer_turns`, `save_assistant_turns`;
- Imports the `jiuwen_memory` symbols (`api.assemble`, `api.legacy_request_context`, `common.type_def.Scope/Modality/Context`, `retrieval.types.DisclosureLevel`); raises `RuntimeError` if not installed (hint: `pip install JiuwenMemory`);
- If `config_dict` is non-empty, builds the config via `Config.from_dict()` (parse failures only log a warning and fall back to built-in defaults); `None` uses built-in in-memory defaults;
- Assembles the kernel via `assemble(config=cfg)`; raises `RuntimeError` on failure;
- On success, captures the `Scope` / `Modality` / `Context` / `DisclosureLevel` / `legacy_request_context` symbols and sets `_is_initialized = True`.

### async shutdown

```
async def shutdown(self) -> None
```

Close the backend and clean up resources.

**Behavior**:

- **Server mode**: calls the HTTP client's `aclose()` (close failures only logged at debug level) and sets `_is_initialized = False`;
- **SDK mode**: only drops the kernel reference; stores with `close()` (e.g., sqlite) are left to GC — callers wanting clean teardown should handle it outside the provider.

### system_prompt_block

```
def system_prompt_block(self) -> str
```

Return the Jiuwen memory capability block for the Agent's system prompt.

**Returns**:

* **str**: The system prompt fragment, in the form:

```
# Jiuwen Memory
Active. user=user_123, tenant=default.
Use mem2_search to recall memories, mem2_add to store a fact. Conversation turns are extracted into memories automatically.
```

where `user` is the current `user_id` (shown as `?` when empty) and `tenant` is the current `tenant_id`.

### get_tool_schemas

```
def get_tool_schemas(self) -> List[Dict[str, Any]]
```

Return the full list of tool schemas exposed by this provider for Agent invocation.

**Returns**:

* **List[Dict[str, Any]]**: A list containing the following 2 schema dictionaries:

| Tool | Description |
|------|-------------|
| `mem2_search` | Search long-term memories by meaning; returns ranked hits across keyword (BM25), vector, and graph recall channels. Use this to recall user facts, preferences, or past context. Parameters: `query` (required, what to search for), `top_k` (optional, default `10`, max `50`). |
| `mem2_add` | Store a durable memory. By default the content is stored verbatim and indexed (no LLM extraction); set `infer=true` to have the server extract and dedup self-contained facts from the content instead. Parameters: `content` (required, the text to remember), `infer` (optional, default `false`), `tags` (optional, `list[str]` labels for later filtering). |

### async handle_tool_call

```
async def handle_tool_call(self, tool_name: str, args: Dict[str, Any]) -> str
```

Dispatch a tool call to the corresponding internal handler and return the result as a JSON string.

**Parameters**:

* **tool_name**(str): Tool name; must match a `name` in `get_tool_schemas()`.
* **args**(Dict[str, Any]): Tool arguments.

**Returns**:

* **str**: The result as a JSON string. On success, the JSON of the handler's returned object; on failure, JSON in the `{"error": ...}` form.

**Behavior**:

- If not initialized, returns `{"error": "Memory provider not initialized"}`;
- In server mode, if the circuit breaker is open, returns `{"error": "Mem2 server temporarily unavailable (repeated failures); will retry."}`;
- `mem2_search`: if `query` is missing, returns `{"error": "Missing required parameter: query"}`; on success returns `{"results": [...], "count": N}` (each item in `results` contains `content` / `item_id` / `score`);
- `mem2_add`: if `content` is missing, returns `{"error": "Missing required parameter: content"}`; on write failure (server unavailable / kernel error) returns the corresponding error JSON; on success returns `{"result": "stored"|"deduped", "item_id": ..., "content": ..., "tier": ...}`;
- Returns `{"error": "Unknown tool: <tool_name>"}` for unknown tool names;
- Other exceptions: returns `{"error": str(e), "results": []}`; in server mode this also increments the circuit breaker failure count.

**Example**:

```python
>>> # Semantic memory search
>>> resp = await provider.handle_tool_call(
>>>     "mem2_search",
>>>     {"query": "user preferences", "top_k": 5},
>>> )
>>>
>>> # Store a new memory (verbatim)
>>> resp = await provider.handle_tool_call(
>>>     "mem2_add",
>>>     {"content": "The project uses Python 3.11", "tags": ["project"]},
>>> )
>>>
>>> # Store a new memory (LLM extraction + dedup)
>>> resp = await provider.handle_tool_call(
>>>     "mem2_add",
>>>     {"content": "The user prefers dark themes", "infer": True},
>>> )
```

### async prefetch

```
async def prefetch(self, query: str, **kwargs) -> str
```

Background recall before the model call; formats relevant memories into a context string injected into the prompt.

**Parameters**:

* **query**(str): The user query text used for context recall.
* ****kwargs**(Any, optional): Recall filter parameters, supporting:
  * `top_k`(int, optional): Recall count, default `10`, max `50`;
  * Other kwargs (e.g., `tenant_id` / `user_id` / `scope_id`) override the Scope per call.

**Returns**:

* **str**: The formatted context string. A list starting with `## Jiuwen Memory`, each line of the form `- content`; returns `""` when there are no hits, when not initialized, when `query` is empty, or (in server mode) when the circuit breaker is open.

**Behavior**:

- Calls the backend `search` to perform the retrieval; retrieval is already internally guarded (exceptions return an empty list), so `prefetch` never raises;
- Returns `""` when recall is empty.

### async sync_turn

```
async def sync_turn(self, user_msg: str, assistant_msg: str, **kwargs) -> None
```

Persist a completed conversation turn as memory. **By default only the user turn is stored** — the assistant reply is derivable and low-value, unless explicitly enabled.

**Parameters**:

* **user_msg**(str): The user message content.
* **assistant_msg**(str): The assistant reply content.
* ****kwargs**(Any, optional):
  * `infer`(bool, optional): Whether to use the extraction + dedup path; defaults to the `infer_turns` value determined at construction/`initialize()` (i.e., `True`). When `True`, the server distills the user message into deduped facts (mem0-style); when `False`, the raw text is stored verbatim;
  * `save_assistant`(bool, optional): Whether to also persist the assistant reply; defaults to `save_assistant_turns` (i.e., `False`);
  * Other kwargs (e.g., `tenant_id` / `user_id` / `scope_id`) are passed through to the backend `add` to override the Scope per call.

**Behavior**:

- The user message is written with `tags=["conversation", "user"]`, with the `infer` semantics above;
- When `save_assistant` is enabled, the assistant reply is written with `infer=False` (verbatim) and `tags=["conversation", "assistant"]`;
- In server mode, the write is skipped entirely when the circuit breaker is open; write failures only log warnings/increment counters and never raise;
- When `user_msg` is empty, the user turn is not written.

### async on_session_end

```
async def on_session_end(self, messages: List[Dict[str, Any]]) -> None
```

Session end hook. The current implementation is a no-op — there is no server-side session lifecycle to close.

**Parameters**:

* **messages**(List[Dict[str, Any]]): The session message list.

**Note**: This method currently performs no logic; subclasses or future versions may override it as needed.

## Dual-Backend Architecture

The two backends implement the same read/write and tool-dispatch interfaces; `JiuwenMemoryProvider` only delegates.

### _ServerBackend (server mode)

- All operations are dispatched via `POST /v1/<verb>` with a JSON body, where `<verb>` selects the handler (`add` / `search` / ...); liveness is probed at `GET /healthz`;
- Separate read/write timeouts: read operations (e.g., search) use `read_timeout` (default `30.0` seconds), writes use `write_timeout` (floor of `120.0` seconds, because `infer=true` triggers real-LLM extraction + dedup);
- Each call resolves `tenant_id` / `scope` via `_scope_payload()` (`user_id` → `scope`, `tenant_id` → org axis), both overridable per call;
- The core method `_post_verb()` records a breaker failure and returns `None` on any failure (network exception or non-2xx), letting callers degrade to empty results instead of propagating exceptions;
- Circuit breaker state is shared across the read and write paths (see below).

### _SDKBackend (sdk mode)

- Builds the kernel via `from jiuwen_memory.api import assemble` (the same assembly entry the HTTP server uses) and calls `api.add` / `api.search` directly — no network hop;
- JiuwenMemory's `LocalMemoryAPI.add` / `search` are *sync* methods that internally `asyncio.run()` the engine, so this backend dispatches those calls to a worker thread via `asyncio.to_thread` to avoid blocking the host event loop;
- Search uses `DisclosureLevel.L2` (full content), matching server-mode behavior;
- An empty `units` return from a write means all content was deduped on the infer path, mapped to `{"item_id": None}`;
- `config_dict` is a two-level namespace dict; `None` → built-in in-memory defaults (good for tests, lost on restart); runtime policy overrides go under `config_dict["globals"]["policies"]`;
- In-process calls have no network failure surface — **no circuit breaker**.

## Write Semantics (Mem2 Write Modes)

Mem2's write semantics mirror the upstream server's multiple modes; this provider actually exposes the following two:

| Mode | Trigger | Behavior |
|------|---------|----------|
| Verbatim (default) | `infer` unset or `false` | The raw `content` is stored verbatim and indexed — no LLM extraction, durable fact-as-written. |
| LLM extraction | `infer=true` | The server runs synchronous LLM extraction (dedup-aware), storing derived facts instead of the raw message. This is the mem0-like path and the default for `sync_turn`, so conversations are distilled into facts rather than dumped raw. |

> **Note**: The module docstring also mentions a `procedural=true` write mode (summarizing the turn into one PROCEDURAL execution history), but the current implementation does not expose that entry via `sync_turn` or tool parameters.

## Scope Mapping

mem2 scopes by `tenant_id` + a single `scope` string; this provider maps them onto `Scope(org=tenant_id, user=scope)`:

- `user_id` (the caller convention) is mem2's `scope` (the per-user/per-session axis);
- `tenant_id` is the org axis, defaulting to `"default"`;
- Values can be overridden at construction, in `initialize()`, or **per call** (kwargs `tenant_id` / `user_id`, with the alias `scope_id` accepted).

## Tool Call Result Structures

The JSON (after deserialization) returned by each tool via `handle_tool_call`:

### mem2_search

```json
{
  "results": [
    {"content": "The user prefers dark themes", "item_id": "unit_xxx", "score": 0.87}
  ],
  "count": 1
}
```

### mem2_add

```json
{
  "result": "stored",
  "item_id": "unit_xxx",
  "content": "The user prefers dark themes",
  "tier": "hot"
}
```

> **Note**: `result` being `"stored"` means a new item was stored (`item_id` non-empty); `"deduped"` means the content was judged duplicate on the infer path and not stored (`item_id` is `null`). `tier` is the memory tier information (returned by the kernel/server).

### Error Returns

```json
{"error": "Memory provider not initialized"}
{"error": "Mem2 server temporarily unavailable (repeated failures); will retry."}
{"error": "Missing required parameter: query"}
{"error": "Missing required parameter: content"}
{"error": "Mem2 add failed (server unavailable)."}
{"error": "Mem2 add failed (kernel error)."}
{"error": "Unknown tool: <tool_name>"}
{"error": "<exception message>", "results": []}
```

## Circuit Breaker (Server Mode)

`_ServerBackend` includes a lightweight circuit breaker for scenarios where the JiuwenMemory service is unavailable:

- **Failure counting**: each `_post_verb()` failure (network exception or non-2xx status) triggers `_record_failure()`, incrementing the consecutive-failure counter; the exception path of `handle_tool_call` also counts; read and write paths **share** the same counter;
- **Breaker threshold**: after the consecutive failure count reaches `_BREAKER_THRESHOLD` (default `5`), the breaker opens, recording a cooldown deadline based on the monotonic clock (`time.monotonic()`), cooling down for `_BREAKER_COOLDOWN_SECS` (default `120.0` seconds);
- **While open**: `handle_tool_call` returns a temporarily-unavailable error JSON; `search` / `prefetch` return empty results; `add` returns `None`; `sync_turn` skips writes — no network requests are made;
- **Cooldown recovery**: after the cooldown expires, the next check automatically resets the counter and recovers (half-open);
- **Success reset**: any successful call triggers `_record_success()`, resetting the failure count to zero.

SDK mode is in-process with no network failure surface, so it has no circuit breaker.

## JiuwenMemory HTTP Endpoints (Server Mode)

In server mode this provider performs actual operations via the following JiuwenMemory REST endpoints (`base_url` as the prefix):

| Operation | Method & Path |
|-----------|---------------|
| Generic dispatch | `POST /v1/<verb>` (`<verb>` such as `add` / `search`; the JSON body carries the parameters and selects the handler) |
| Semantic search | `POST /v1/search` |
| Memory ingest | `POST /v1/add` |
| Health check | `GET /healthz` |

Request bodies uniformly carry `tenant_id` / `scope` (resolved by `_scope_payload()`); write requests additionally carry `content` / `tags` / `metadata` (`metadata.infer` is set when `infer=true`).
