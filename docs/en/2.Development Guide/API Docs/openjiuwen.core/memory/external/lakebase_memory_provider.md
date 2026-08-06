# openjiuwen.core.memory.external.lakebase_memory_provider

`openjiuwen.core.memory.external.lakebase_memory_provider` is the **LakeBase (DBay)-based external memory provider** implementation in openJiuwen, inheriting from `MemoryProvider`. It interfaces with the LakeBase service via an HTTP async client and is responsible for:

- Semantic memory storage and retrieval based on `pgvector`;
- Multiple memory type management (`fact` / `episode` / `procedural` / `decision` / `rejection` / `convention`);
- Behavioral trait extraction via the digest API;
- Branch and version snapshot capabilities based on copy-on-write, for memory experimentation and rollback;
- Multi-workspace (memory base) switching;
- Built-in circuit breaker for improved robustness under network failures.

## Module Constants

| Constant | Type | Description |
|----------|------|-------------|
| `DEFAULT_BASE_URL` | `str` | Default LakeBase API endpoint. Default: `"http://localhost:8080/api/v1"`. |
| `DEFAULT_TIMEOUT` | `float` | Default HTTP request timeout in seconds. Default: `60.0`. |
| `DEFAULT_PREFETCH_TIMEOUT` | `float` | Default prefetch timeout in seconds. Default: `5.0`. |
| `MEMORY_TYPES` | `list[str]` | Supported memory type enumeration: `fact`, `episode`, `procedural`, `decision`, `rejection`, `convention`. |

A set of tool schema dictionaries named `LKB_*_SCHEMA` (e.g., `LKB_BRANCH_LIST_SCHEMA`, `LKB_BRANCH_CREATE_SCHEMA`, `LKB_VERSION_CREATE_SCHEMA`, etc.) are defined for declaring the memory/branch tools exposed by this provider in `get_tool_schemas()`.

> **Note on star imports (`from ... import *`)**: The `__all__` at the end of the module only explicitly exports the following names: `LakeBaseMemoryProvider`, `MEMORY_TYPES`, `LKB_BRANCH_LIST_SCHEMA`, `LKB_BRANCH_CREATE_SCHEMA`, `LKB_VERSION_CREATE_SCHEMA` — i.e., **only branch/version-related schemas**. `LKB_MEMORY_SEARCH_SCHEMA`, `LKB_MEMORY_ADD_SCHEMA`, and other schemas are not in `__all__` and cannot be referenced via star import; use explicit imports instead (e.g., `from openjiuwen.core.memory.external.lakebase_memory_provider import LKB_MEMORY_SEARCH_SCHEMA`).

## class openjiuwen.core.memory.external.lakebase_memory_provider.LakeBaseMemoryProvider

```
class openjiuwen.core.memory.external.lakebase_memory_provider.LakeBaseMemoryProvider(MemoryProvider)
```

`LakeBaseMemoryProvider` is a LakeBase (DBay)-based external memory provider.

**Features**:

- Semantic memory storage and retrieval via `pgvector`;
- Support for multiple memory types for categorized organization;
- Behavioral trait extraction via digest;
- Memory workspace (base) switching for multi-workspace support;
- Async HTTP client with configurable timeout;
- Built-in circuit breaker that short-circuits after consecutive failures reach a threshold, preventing cascading failures.

**Configuration**:

- `api_key`: LakeBase authentication API key;
- `base_url`: LakeBase API endpoint (default `localhost:8080`);
- `base_id`: Memory workspace identifier;
- `database_id`: Database ID for branch operations;
- `timeout`: HTTP request timeout.

**Usage Example**:

```python
>>> from openjiuwen.core.memory.external.lakebase_memory_provider import LakeBaseMemoryProvider
>>>
>>> provider = LakeBaseMemoryProvider(
>>>     api_key="lk_xxx",
>>>     base_url="http://localhost:8080/api/v1",
>>>     base_id="mem_default",
>>> )
>>> await provider.initialize()
>>> results = await provider.handle_tool_call("lkb_memory_search", {"query": "preferences"})
```

### __init__

```
LakeBaseMemoryProvider(
    api_key: str,
    base_url: str = DEFAULT_BASE_URL,
    base_id: str = "mem_default",
    database_id: str = "db_agent_memory",
    timeout: float = DEFAULT_TIMEOUT,
)
```

Initializes the LakeBase memory provider. **Does not initiate network requests**; only completes local state setup.

**Parameters**:

* **api_key** (str): LakeBase authentication API key.
* **base_url** (str, optional): LakeBase API endpoint URL; trailing `/` is stripped. Default: `DEFAULT_BASE_URL` (`"http://localhost:8080/api/v1"`).
* **base_id** (str, optional): Memory workspace identifier. Default: `"mem_default"`.
* **database_id** (str, optional): Database ID for branch operations. Default: `"db_agent_memory"`.
* **timeout** (float, optional): HTTP request timeout in seconds. Default: `DEFAULT_TIMEOUT` (`60.0`).

**Internal State Initialization**:

- `_http: httpx.AsyncClient | None = None`: Created during `initialize()`;
- `_is_initialized: bool = False`: Flag indicating whether initialization is complete;
- `_available_bases: list[str] = [base_id]`: List of visited workspaces, used for tracking during switching;
- Circuit breaker state: `_consecutive_failures = 0`, `_breaker_threshold = 5`, `_breaker_cooldown = 120.0`, `_breaker_until = 0.0`.

### name

```
@property
def name(self) -> str
```

Returns the provider identifier, always `"lakebase"`.

**Returns**:

* **str**: `"lakebase"`.

### is_available

```
def is_available(self) -> bool
```

Checks whether the provider is configured and ready. **Does not initiate any network calls**; only validates that `api_key`, `base_url`, and `base_id` are all non-empty.

**Returns**:

* **bool**: `True` if all three are non-empty, otherwise `False`.

### is_initialized

```
@property
def is_initialized(self) -> bool
```

Checks whether the provider has been initialized (i.e., whether `initialize()` has been called).

**Returns**:

* **bool**: `True` if initialized, otherwise `False`.

### current_base_id

```
@property
def current_base_id(self) -> str
```

Returns the currently active memory workspace ID.

**Returns**:

* **str**: The current `base_id`. This value updates after switching via `lkb_memory_switch_base`.

### classmethod from_config

```
@classmethod
def from_config(cls, config: Dict[str, Any]) -> "LakeBaseMemoryProvider"
```

Constructs a provider instance from a configuration dictionary, reading the `config["lakebase"]` subsection.

**Parameters**:

* **config** (Dict[str, Any]): Configuration dictionary, must contain a `lakebase` subsection with supported fields:
  * `api_key` (str, optional): Default `""`;
  * `base_url` (str, optional): Default `DEFAULT_BASE_URL`;
  * `base_id` (str, optional): Default `"mem_default"`;
  * `database_id` (str, optional): Default `"db_agent_memory"`;
  * `timeout` (float, optional): Default `DEFAULT_TIMEOUT`.

**Returns**:

* **LakeBaseMemoryProvider**: The constructed instance (without calling `initialize()`).

**Example**:

```python
>>> from openjiuwen.core.memory.external.lakebase_memory_provider import LakeBaseMemoryProvider
>>>
>>> provider = LakeBaseMemoryProvider.from_config({
>>>     "lakebase": {
>>>         "api_key": "lk_xxx",
>>>         "base_url": "http://localhost:8080/api/v1",
>>>         "base_id": "mem_default",
>>>         "database_id": "db_agent_memory",
>>>         "timeout": 60.0,
>>>     }
>>> })
>>> await provider.initialize()
```

### async initialize

```
async def initialize(self, **kwargs) -> None
```

Initializes the provider: creates the async HTTP client and attempts to validate the connection. Returns immediately if already initialized.

**Parameters**:

* ****kwargs** (Any, optional): Override parameters (e.g., `user_id`, `scope_id`, `session_id`), ignored for LakeBase.

**Behavior**:

- Creates an `httpx.AsyncClient` with `Authorization: Bearer <api_key>` and `Content-Type: application/json` headers, timeout set to `timeout`;
- Validates the connection via `GET /memory/bases/{base_id}/stats`:
  - Status code `200`: Logs connection success;
  - Other status codes: Logs a warning indicating the base does not yet exist and will be created on first ingest;
  - `httpx.ConnectError` or other exceptions: Only logs a warning without blocking initialization (allows offline startup);
- Sets `_is_initialized = True` regardless of whether validation succeeded.

**Note**: Connection validation failures do not raise exceptions, allowing initialization to complete even when LakeBase is not running; subsequent real requests that still fail are handled by the circuit breaker and exception handling.

### async shutdown

```
async def shutdown(self) -> None
```

Closes the HTTP client and cleans up resources.

**Behavior**:

- If `_http` is not None, calls `aclose()` to close and sets it to None;
- Sets `_is_initialized = False`;
- Logs shutdown completion.

### system_prompt_block

```
def system_prompt_block(self) -> str
```

Returns the LakeBase memory capability description block for Agent system prompts, including usage tips for memory operations, memory types, multi-workspace, branch, and version operations.

**Returns**:

* **str**: System prompt fragment (multi-line string).

### get_tool_schemas

```
def get_tool_schemas(self) -> List[Dict[str, Any]]
```

Returns the full list of tool schemas exposed by this provider for Agent invocation.

**Returns**:

* **List[Dict[str, Any]]**: Contains the following 17 schema dictionaries (9 memory-type tools + 8 branch/version-type tools):

| Tool Name | Description |
|-----------|-------------|
| `lkb_memory_search` | Retrieves memories by semantic similarity, supports filtering by type. |
| `lkb_memory_add` | Stores a new memory; an appropriate `memory_type` must be selected. |
| `lkb_memory_list` | Lists memories with pagination, supports filtering by type. |
| `lkb_memory_get` | Retrieves a single memory by ID. |
| `lkb_memory_delete` | Deletes a memory by ID. |
| `lkb_memory_digest` | Runs reflection to extract behavioral traits from accumulated memories. |
| `lkb_memory_traits` | Lists discovered behavioral traits. |
| `lkb_memory_stats` | Gets memory store statistics (counts, types, etc.). |
| `lkb_memory_switch_base` | Switches to a different memory workspace. |
| `lkb_branch_list` | Lists all branches under the current database. |
| `lkb_branch_create` | Creates a new branch based on the current state. |
| `lkb_branch_delete` | Deletes a branch by ID (cannot delete the default branch). |
| `lkb_branch_promote` | Promotes a branch to default (merges its changes to main). |
| `lkb_branch_restore` | Restores a branch to a specified version or LSN point. |
| `lkb_version_list` | Lists all version snapshots under a branch. |
| `lkb_version_create` | Creates a named version snapshot for backup or rollback points. |
| `lkb_version_delete` | Deletes a version snapshot. |

### async handle_tool_call

```
async def handle_tool_call(self, tool_name: str, args: Dict[str, Any]) -> str
```

Dispatches tool calls to the corresponding internal handler and returns the result as a JSON string.

**Parameters**:

* **tool_name** (str): Tool name, must match the `name` in `get_tool_schemas()`.
* **args** (Dict[str, Any]): Tool arguments.

**Returns**:

* **str**: JSON string result. On success, the JSON of the handler's return object; on failure, a JSON of the form `{"error": ..., "results": []}`.

**Behavior**:

- If not initialized, returns `{"error": "Provider not initialized", "results": []}`;
- If the circuit breaker is open, returns `{"error": "Circuit breaker open (too many failures)", "results": []}`;
- Routes to the corresponding internal handler by `tool_name` and `await`s it;
- On success: resets the circuit breaker, returns the result as JSON (`ensure_ascii=False`);
- `httpx.HTTPStatusError`: Logs the failure, returns error JSON with status code and response body;
- `httpx.ConnectError`: Logs the failure, returns connection failure error JSON;
- Other exceptions: Logs the failure, returns `str(e)` error JSON.

**Supported tool names**: `lkb_memory_search`, `lkb_memory_add`, `lkb_memory_list`, `lkb_memory_get`, `lkb_memory_delete`, `lkb_memory_digest`, `lkb_memory_traits`, `lkb_memory_stats`, `lkb_memory_switch_base`, `lkb_branch_list`, `lkb_branch_create`, `lkb_branch_delete`, `lkb_branch_promote`, `lkb_branch_restore`, `lkb_version_list`, `lkb_version_create`, `lkb_version_delete`. Returns `{"error": "Unknown tool: <tool_name>", "results": []}` for unknown tool names.

**Example**:

```python
>>> # Semantic memory search
>>> resp = await provider.handle_tool_call(
>>>     "lkb_memory_search",
>>>     {"query": "user preferences", "top_k": 5, "memory_types": ["fact"]}
>>> )
>>>
>>> # Store a new memory
>>> resp = await provider.handle_tool_call(
>>>     "lkb_memory_add",
>>>     {"content": "Project uses Python 3.11", "memory_type": "convention", "importance": 0.8}
>>> )
>>>
>>> # Create a version snapshot
>>> resp = await provider.handle_tool_call(
>>>     "lkb_version_create",
>>>     {"name": "before_refactor", "description": "Backup before refactoring"}
>>> )
```

### async prefetch

```
async def prefetch(self, query: str, **kwargs) -> str
```

Performs background recall before model invocation, formatting relevant memories into a context string injected into the prompt.

**Parameters**:

* **query** (str): User query text for context recall.
* ****kwargs** (Any, optional): Recall filter parameters, supports:
  * `top_k` (int, optional): Number of results to recall, default `5`;
  * `memory_types` (list[str] | None, optional): Filter by memory type.

**Returns**:

* **str**: Formatted context string. Format is a list starting with `## Related Memories`, each line in the form `- [type] content (score: 0.00)`; returns `""` when no memories are found or when not initialized / `query` is empty.

**Behavior**:

- Returns `""` immediately when not initialized or `query` is empty;
- Calls internal `_recall` for semantic retrieval;
- Returns `""` when recall results are empty;
- On any exception, logs a warning and returns `""` without raising, to avoid impacting the main flow.

### async sync_turn

```
async def sync_turn(self, user_msg: str, assistant_msg: str, **kwargs) -> None
```

Persists a conversation turn as an `episode` type memory to LakeBase.

**Parameters**:

* **user_msg** (str): User message content.
* **assistant_msg** (str): Assistant response content.
* ****kwargs** (Any, optional): Optional metadata, supports:
  * `importance` (float, optional): Importance score, default `0.4`;
  * `metadata` (dict | None, optional): Structured metadata.

**Behavior**:

- Returns immediately when not initialized or `user_msg` is empty;
- If the circuit breaker is open, logs a warning and skips this write;
- Combines user message and assistant response into an `episode` memory for writing; resets the circuit breaker on success;
- On exception, logs the failure and triggers the circuit breaker counter; only logs a warning without raising.

### async on_session_end

```
async def on_session_end(self, messages: List[Dict[str, Any]]) -> None
```

Session end hook. The current implementation is a no-op, reserved for triggering digest and other processing at session end.

**Parameters**:

* **messages** (List[Dict[str, Any]]): Session message list.

**Note**: This method currently performs no logic; subclasses or future versions can override as needed.

## Tool Call Result Structure

The JSON structure returned by each tool via `handle_tool_call` (after deserialization) is as follows:

### lkb_memory_search

```json
{
  "memories": [ /* list of memory objects */ ],
  "count": 0,
  "base_id": "mem_default"
}
```

### lkb_memory_add

```json
{
  "success": true,
  "memory_id": null,
  "memory_type": "fact",
  "base_id": "mem_default"
}
```

### lkb_memory_list

```json
{
  "memories": [ /* list of memory objects */ ],
  "total": 0,
  "base_id": "mem_default"
}
```

### lkb_memory_get

```json
{
  "memory": { /* memory object */ },
  "base_id": "mem_default"
}
```

### lkb_memory_delete

```json
{
  "success": true,
  "deleted_id": 123,
  "base_id": "mem_default"
}
```

### lkb_memory_digest

```json
{
  "success": true,
  "traits": [ /* list of trait objects */ ],
  "base_id": "mem_default"
}
```

### lkb_memory_traits

```json
{
  "traits": [ /* list of trait objects */ ],
  "base_id": "mem_default"
}
```

### lkb_memory_stats

```json
{
  "stats": { /* statistics object */ },
  "base_id": "mem_default"
}
```

### lkb_memory_switch_base

```json
{
  "success": true,
  "old_base_id": "mem_default",
  "new_base_id": "mem_experiment",
  "available_bases": ["mem_default", "mem_experiment"]
}
```

### Branch and Version Tools

`lkb_branch_list` / `lkb_version_list` return `{ "<branches|versions>": [...], "count": N, "database_id": "..." }`;
`lkb_branch_create` / `lkb_version_create` return `{ "success": true, "<branch|version>": { /* object */ }, "database_id": "..." }`;
`lkb_branch_delete` / `lkb_version_delete` return `{ "success": true, "deleted_<branch|version>_id": "<id>", "database_id": "..." }`;
`lkb_branch_promote` returns `{ "success": true, "promoted_branch_id": "<id>", "database_id": "..." }`;
`lkb_branch_restore` returns `{ "success": true, "restored_branch_id": "<id>", "database_id": "..." }`.

> **Note**: The `base_id` / `database_id` fields identify the workspace and database on which the operation was performed, facilitating result source identification in multi-workspace scenarios.

## Circuit Breaker Mechanism

`LakeBaseMemoryProvider` includes a lightweight circuit breaker to handle LakeBase unavailability:

- **Failure counting**: Each request failure (`HTTPStatusError` / `ConnectError` / other exceptions) triggers `_record_failure()`, incrementing `_consecutive_failures`;
- **Breaker threshold**: After consecutive failures reach `_breaker_threshold` (default `5`), the breaker opens, recording `_breaker_until = now + _breaker_cooldown` (default cooldown `120.0` seconds);
- **During break**: When `handle_tool_call` and `sync_turn` detect the breaker is open, they short-circuit and return an error (or skip the write) without making network requests;
- **Cooldown recovery**: After the cooldown period elapses, the next check automatically resets the counter and recovers (half-open);
- **Success reset**: Any successful call triggers `_reset_breaker()`, clearing the failure counter to zero.

## LakeBase HTTP Endpoints

This provider performs actual operations through the following LakeBase REST endpoints (`base_url` as prefix):

| Operation | Method & Path |
|-----------|---------------|
| Write memory | `POST /memory/bases/{base_id}/ingest` |
| Semantic retrieval | `POST /memory/bases/{base_id}/recall` |
| List memories | `GET /memory/bases/{base_id}/memories` |
| Get single memory | `GET /memory/bases/{base_id}/memories/{memory_id}` |
| Delete memory | `DELETE /memory/bases/{base_id}/memories/{memory_id}` |
| Extract traits | `POST /memory/bases/{base_id}/digest` |
| List traits | `GET /memory/bases/{base_id}/traits` |
| Store statistics | `GET /memory/bases/{base_id}/stats` |
| List branches | `GET /databases/{database_id}/branches` |
| Create branch | `POST /databases/{database_id}/branches` |
| Delete branch | `DELETE /databases/{database_id}/branches/{branch_id}` |
| Promote branch | `POST /databases/{database_id}/branches/{branch_id}/promote` |
| Restore branch | `POST /databases/{database_id}/branches/{branch_id}/restore` |
| List versions | `GET /databases/{database_id}/branches/{branch_id}/versions` |
| Create version | `POST /databases/{database_id}/branches/{branch_id}/versions` |
| Delete version | `DELETE /databases/{database_id}/branches/{branch_id}/versions/{version_id}` |
