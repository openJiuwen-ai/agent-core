# openjiuwen.core.memory.external.lakebase_memory_provider

`openjiuwen.core.memory.external.lakebase_memory_provider` 是 openJiuwen 中基于 **LakeBase（DBay）** 的**外部记忆提供者**实现，继承自 `MemoryProvider`。它通过 HTTP 异步客户端对接 LakeBase 服务，负责：

- 基于 `pgvector` 的语义记忆存储与检索；
- 多种记忆类型管理（`fact` / `episode` / `procedural` / `decision` / `rejection` / `convention`）；
- 通过 digest API 提取行为特征（trait）；
- 基于写时复制（copy-on-write）的分支（branch）与版本快照（version）能力，用于记忆实验与回滚；
- 多工作空间（memory base）切换；
- 内置熔断器（circuit breaker）以提升网络故障下的鲁棒性。

## 模块常量

| 常量 | 类型 | 说明 |
|------|------|------|
| `DEFAULT_BASE_URL` | `str` | LakeBase API 默认端点，默认值：`"http://localhost:8080/api/v1"`。 |
| `DEFAULT_TIMEOUT` | `float` | HTTP 请求默认超时时间（秒），默认值：`60.0`。 |
| `DEFAULT_PREFETCH_TIMEOUT` | `float` | 预取（prefetch）默认超时时间（秒），默认值：`5.0`。 |
| `MEMORY_TYPES` | `list[str]` | 支持的记忆类型枚举：`fact`、`episode`、`procedural`、`decision`、`rejection`、`convention`。 |

定义了一组以 `LKB_*_SCHEMA` 命名的工具 schema 字典（如 `LKB_BRANCH_LIST_SCHEMA`、`LKB_BRANCH_CREATE_SCHEMA`、`LKB_VERSION_CREATE_SCHEMA` 等），用于在 `get_tool_schemas()` 中声明本提供者对外暴露的记忆/分支工具。

> **关于星号导入（`from ... import *`）的说明**：模块末尾的 `__all__` 仅显式导出以下名称：`LakeBaseMemoryProvider`、`MEMORY_TYPES`、`LKB_BRANCH_LIST_SCHEMA`、`LKB_BRANCH_CREATE_SCHEMA`、`LKB_VERSION_CREATE_SCHEMA`，即**仅导出分支/版本相关 schema**。`LKB_MEMORY_SEARCH_SCHEMA`、`LKB_MEMORY_ADD_SCHEMA` 等其余 schema 不在 `__all__` 中，星号导入无法引用；如需使用应采用显式导入（如 `from openjiuwen.core.memory.external.lakebase_memory_provider import LKB_MEMORY_SEARCH_SCHEMA`）。

## class openjiuwen.core.memory.external.lakebase_memory_provider.LakeBaseMemoryProvider

```
class openjiuwen.core.memory.external.lakebase_memory_provider.LakeBaseMemoryProvider(MemoryProvider)
```

`LakeBaseMemoryProvider` 是基于 LakeBase（DBay）的外部记忆提供者。

**特性**：

- 通过 `pgvector` 实现语义记忆存储与检索；
- 支持多种记忆类型以便分类组织；
- 通过 digest 提取行为特征；
- 支持记忆工作空间（base）切换，实现多工作空间；
- 异步 HTTP 客户端，超时可配置；
- 内置熔断器，连续失败达到阈值后短路冷却，避免雪崩。

**配置项**：

- `api_key`：LakeBase 鉴权 API key；
- `base_url`：LakeBase API 端点（默认 `localhost:8080`）；
- `base_id`：记忆工作空间标识；
- `database_id`：用于分支操作的数据库 ID；
- `timeout`：HTTP 请求超时时间。

**使用样例**：

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

初始化 LakeBase 记忆提供者。**不会发起网络请求**，仅完成本地状态设置。

**参数**：

* **api_key**(str)：LakeBase 鉴权 API key。
* **base_url**(str, 可选)：LakeBase API 端点 URL，末尾的 `/` 会被去除。默认值：`DEFAULT_BASE_URL`（`"http://localhost:8080/api/v1"`）。
* **base_id**(str, 可选)：记忆工作空间标识。默认值：`"mem_default"`。
* **database_id**(str, 可选)：用于分支操作的数据库 ID。默认值：`"db_agent_memory"`。
* **timeout**(float, 可选)：HTTP 请求超时时间（秒）。默认值：`DEFAULT_TIMEOUT`（`60.0`）。

**内部状态初始化**：

- `_http: httpx.AsyncClient | None = None`：在 `initialize()` 时创建；
- `_is_initialized: bool = False`：标记是否完成初始化；
- `_available_bases: list[str] = [base_id]`：已访问过的工作空间列表，用于切换时追踪；
- 熔断器状态：`_consecutive_failures = 0`、`_breaker_threshold = 5`、`_breaker_cooldown = 120.0`、`_breaker_until = 0.0`。

### name

```
@property
def name(self) -> str
```

返回提供者标识符，固定为 `"lakebase"`。

**返回**：

* **str**：`"lakebase"`。

### is_available

```
def is_available(self) -> bool
```

检查提供者是否已配置就绪。**不发起任何网络调用**，仅校验 `api_key`、`base_url`、`base_id` 均非空。

**返回**：

* **bool**：三者均非空返回 `True`，否则返回 `False`。

### is_initialized

```
@property
def is_initialized(self) -> bool
```

检查提供者是否已完成初始化（即是否已调用 `initialize()`）。

**返回**：

* **bool**：已完成初始化返回 `True`，否则返回 `False`。

### current_base_id

```
@property
def current_base_id(self) -> str
```

返回当前生效的记忆工作空间 ID。

**返回**：

* **str**：当前 `base_id`。通过 `lkb_memory_switch_base` 切换后该值会随之更新。

### classmethod from_config

```
@classmethod
def from_config(cls, config: Dict[str, Any]) -> "LakeBaseMemoryProvider"
```

从配置字典构造提供者实例，读取 `config["lakebase"]` 子节。

**参数**：

* **config**(Dict[str, Any])：配置字典，需包含 `lakebase` 子节，支持字段：
  * `api_key`(str, 可选)：默认值 `""`；
  * `base_url`(str, 可选)：默认值 `DEFAULT_BASE_URL`；
  * `base_id`(str, 可选)：默认值 `"mem_default"`；
  * `database_id`(str, 可选)：默认值 `"db_agent_memory"`；
  * `timeout`(float, 可选)：默认值 `DEFAULT_TIMEOUT`。

**返回**：

* **LakeBaseMemoryProvider**：构造出的实例（未调用 `initialize()`）。

**样例**：

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

初始化提供者：创建异步 HTTP 客户端并尝试校验连接。若已初始化则直接返回。

**参数**：

* ****kwargs**(Any, 可选)：覆盖参数（如 `user_id`、`scope_id`、`session_id`），对 LakeBase 而言会被忽略。

**行为说明**：

- 创建 `httpx.AsyncClient`，注入 `Authorization: Bearer <api_key>` 与 `Content-Type: application/json` 请求头，超时取 `timeout`；
- 通过 `GET /memory/bases/{base_id}/stats` 校验连接：
  - 状态码 `200`：记录连接成功日志；
  - 其他状态码：记录警告，表示 base 暂不存在，将在首次 ingest 时创建；
  - `httpx.ConnectError` 或其他异常：仅记录警告，不阻断初始化（允许离线启动）；
- 无论校验是否成功，最终置 `_is_initialized = True`。

**说明**：连接校验失败不会抛出异常，便于在 LakeBase 未运行时也能完成初始化；后续真实请求若仍失败，会由熔断器与异常处理兜底。

### async shutdown

```
async def shutdown(self) -> None
```

关闭 HTTP 客户端并清理资源。

**行为说明**：

- 若 `_http` 非空，调用 `aclose()` 关闭并置空；
- 置 `_is_initialized = False`；
- 记录关闭完成日志。

### system_prompt_block

```
def system_prompt_block(self) -> str
```

返回供 Agent 系统提示词使用的 LakeBase 记忆能力说明块，包含记忆操作、记忆类型、多工作空间、分支与版本操作的使用提示。

**返回**：

* **str**：系统提示词片段（多行字符串）。

### get_tool_schemas

```
def get_tool_schemas(self) -> List[Dict[str, Any]]
```

返回本提供者对外暴露的全部工具 schema 列表，供 Agent 调用。

**返回**：

* **List[Dict[str, Any]]**：包含以下 17 个 schema 字典（9 个 memory 类工具 + 8 个 branch/version 类工具）：

| 工具名 | 说明 |
|--------|------|
| `lkb_memory_search` | 按语义相似度检索记忆，支持按类型过滤。 |
| `lkb_memory_add` | 存储新记忆，需选择合适的 `memory_type`。 |
| `lkb_memory_list` | 分页列出记忆，可按类型过滤。 |
| `lkb_memory_get` | 按 ID 获取单条记忆。 |
| `lkb_memory_delete` | 按 ID 删除记忆。 |
| `lkb_memory_digest` | 运行反思，从累积记忆中提取行为特征。 |
| `lkb_memory_traits` | 列出已发现的行为特征。 |
| `lkb_memory_stats` | 获取记忆库统计信息（数量、类型等）。 |
| `lkb_memory_switch_base` | 切换到不同的记忆工作空间。 |
| `lkb_branch_list` | 列出当前数据库下的所有分支。 |
| `lkb_branch_create` | 基于当前状态创建新分支。 |
| `lkb_branch_delete` | 按 ID 删除分支（不能删除默认分支）。 |
| `lkb_branch_promote` | 将分支提升为默认（合并其变更到 main）。 |
| `lkb_branch_restore` | 将分支恢复到指定版本或 LSN 点。 |
| `lkb_version_list` | 列出分支下的所有版本快照。 |
| `lkb_version_create` | 创建命名版本快照，用于备份或回滚点。 |
| `lkb_version_delete` | 删除版本快照。 |

### async handle_tool_call

```
async def handle_tool_call(self, tool_name: str, args: Dict[str, Any]) -> str
```

分发工具调用到对应的内部处理器，并以 JSON 字符串返回结果。

**参数**：

* **tool_name**(str)：工具名，需与 `get_tool_schemas()` 中的 `name` 一致。
* **args**(Dict[str, Any])：工具参数。

**返回**：

* **str**：JSON 字符串形式的结果。成功时为处理器返回对象的 JSON；失败时为 `{"error": ..., "results": []}` 形式的 JSON。

**行为说明**：

- 若未初始化，返回 `{"error": "Provider not initialized", "results": []}`；
- 若熔断器开启，返回 `{"error": "Circuit breaker open (too many failures)", "results": []}`；
- 根据 `tool_name` 路由到对应内部处理器并 `await`；
- 成功：重置熔断器，返回结果的 JSON（`ensure_ascii=False`）；
- `httpx.HTTPStatusError`：记录失败，返回带状态码与响应体的错误 JSON；
- `httpx.ConnectError`：记录失败，返回连接失败错误 JSON；
- 其他异常：记录失败，返回 `str(e)` 错误 JSON。

**支持的工具名**：`lkb_memory_search`、`lkb_memory_add`、`lkb_memory_list`、`lkb_memory_get`、`lkb_memory_delete`、`lkb_memory_digest`、`lkb_memory_traits`、`lkb_memory_stats`、`lkb_memory_switch_base`、`lkb_branch_list`、`lkb_branch_create`、`lkb_branch_delete`、`lkb_branch_promote`、`lkb_branch_restore`、`lkb_version_list`、`lkb_version_create`、`lkb_version_delete`。传入未知工具名时返回 `{"error": "Unknown tool: <tool_name>", "results": []}`。

**样例**：

```python
>>> # 语义检索记忆
>>> resp = await provider.handle_tool_call(
>>>     "lkb_memory_search",
>>>     {"query": "用户偏好", "top_k": 5, "memory_types": ["fact"]}
>>> )
>>>
>>> # 存储新记忆
>>> resp = await provider.handle_tool_call(
>>>     "lkb_memory_add",
>>>     {"content": "项目使用 Python 3.11", "memory_type": "convention", "importance": 0.8}
>>> )
>>>
>>> # 创建版本快照
>>> resp = await provider.handle_tool_call(
>>>     "lkb_version_create",
>>>     {"name": "before_refactor", "description": "重构前备份"}
>>> )
```

### async prefetch

```
async def prefetch(self, query: str, **kwargs) -> str
```

在模型调用前进行后台召回，将相关记忆格式化为上下文字符串注入提示词。

**参数**：

* **query**(str)：用于上下文召回的用户查询文本。
* ****kwargs**(Any, 可选)：召回过滤参数，支持：
  * `top_k`(int, 可选)：召回数量，默认值 `5`；
  * `memory_types`(list[str] | None, 可选)：按记忆类型过滤。

**返回**：

* **str**：格式化的上下文字符串。格式为以 `## Related Memories` 起始的列表，每行形如 `- [类型] 内容 (score: 0.00)`；无记忆或未初始化/`query` 为空时返回 `""`。

**行为说明**：

- 未初始化或 `query` 为空时直接返回 `""`；
- 调用内部 `_recall` 完成语义检索；
- 召回为空时返回 `""`；
- 任意异常仅记录警告并返回 `""`，不抛出，避免影响主流程。

### async sync_turn

```
async def sync_turn(self, user_msg: str, assistant_msg: str, **kwargs) -> None
```

将一轮对话以 `episode` 类型记忆持久化到 LakeBase。

**参数**：

* **user_msg**(str)：用户消息内容。
* **assistant_msg**(str)：助手回复内容。
* ****kwargs**(Any, 可选)：可选元数据，支持：
  * `importance`(float, 可选)：重要度分数，默认值 `0.4`；
  * `metadata`(dict | None, 可选)：结构化元数据。

**行为说明**：

- 未初始化或 `user_msg` 为空时直接返回；
- 若熔断器开启，记录警告并跳过本次写入；
- 将用户消息与助手回复拼合为 `episode` 记忆写入，成功后重置熔断器；
- 异常时记录失败并触发熔断器计数，仅记录警告不抛出。

### async on_session_end

```
async def on_session_end(self, messages: List[Dict[str, Any]]) -> None
```

会话结束钩子。当前实现为空操作，预留用于在会话结束时触发 digest 等处理。

**参数**：

* **messages**(List[Dict[str, Any]])：会话消息列表。

**说明**：本方法当前不执行任何逻辑，子类或后续版本可按需覆写。

## 工具调用结果结构

各工具经 `handle_tool_call` 返回的 JSON（反序列化后）结构如下：

### lkb_memory_search

```json
{
  "memories": [ /* 记忆对象列表 */ ],
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
  "memories": [ /* 记忆对象列表 */ ],
  "total": 0,
  "base_id": "mem_default"
}
```

### lkb_memory_get

```json
{
  "memory": { /* 记忆对象 */ },
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
  "traits": [ /* 特征对象列表 */ ],
  "base_id": "mem_default"
}
```

### lkb_memory_traits

```json
{
  "traits": [ /* 特征对象列表 */ ],
  "base_id": "mem_default"
}
```

### lkb_memory_stats

```json
{
  "stats": { /* 统计对象 */ },
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

### 分支与版本类工具

`lkb_branch_list` / `lkb_version_list` 返回 `{ "<branches|versions>": [...], "count": N, "database_id": "..." }`；
`lkb_branch_create` / `lkb_version_create` 返回 `{ "success": true, "<branch|version>": { /* 对象 */ }, "database_id": "..." }`；
`lkb_branch_delete` / `lkb_version_delete` 返回 `{ "success": true, "deleted_<branch|version>_id": "<id>", "database_id": "..." }`；
`lkb_branch_promote` 返回 `{ "success": true, "promoted_branch_id": "<id>", "database_id": "..." }`；
`lkb_branch_restore` 返回 `{ "success": true, "restored_branch_id": "<id>", "database_id": "..." }`。

> **说明**：`base_id` / `database_id` 字段用于标识操作所作用的工作空间与数据库，便于多工作空间场景下定位结果来源。

## 熔断器机制

`LakeBaseMemoryProvider` 内置轻量熔断器以应对 LakeBase 不可用场景：

- **失败计数**：每次请求失败（`HTTPStatusError` / `ConnectError` / 其他异常）触发 `_record_failure()`，`_consecutive_failures` 自增；
- **熔断阈值**：连续失败达到 `_breaker_threshold`（默认 `5`）次后，熔断器开启，记录 `_breaker_until = now + _breaker_cooldown`（默认冷却 `120.0` 秒）；
- **熔断期间**：`handle_tool_call` 与 `sync_turn` 检测到熔断器开启时直接短路返回错误（或跳过写入），不再发起网络请求；
- **冷却恢复**：超过冷却时间后，下次检查自动重置计数并恢复（半开）；
- **成功重置**：任意一次成功调用触发 `_reset_breaker()`，将失败计数清零。

## LakeBase HTTP 端点

本提供者通过以下 LakeBase REST 端点完成实际操作（`base_url` 为前缀）：

| 操作 | 方法 & 路径 |
|------|-------------|
| 写入记忆 | `POST /memory/bases/{base_id}/ingest` |
| 语义检索 | `POST /memory/bases/{base_id}/recall` |
| 列出记忆 | `GET /memory/bases/{base_id}/memories` |
| 获取单条 | `GET /memory/bases/{base_id}/memories/{memory_id}` |
| 删除记忆 | `DELETE /memory/bases/{base_id}/memories/{memory_id}` |
| 提取特征 | `POST /memory/bases/{base_id}/digest` |
| 列出特征 | `GET /memory/bases/{base_id}/traits` |
| 库统计 | `GET /memory/bases/{base_id}/stats` |
| 列出分支 | `GET /databases/{database_id}/branches` |
| 创建分支 | `POST /databases/{database_id}/branches` |
| 删除分支 | `DELETE /databases/{database_id}/branches/{branch_id}` |
| 提升分支 | `POST /databases/{database_id}/branches/{branch_id}/promote` |
| 恢复分支 | `POST /databases/{database_id}/branches/{branch_id}/restore` |
| 列出版本 | `GET /databases/{database_id}/branches/{branch_id}/versions` |
| 创建版本 | `POST /databases/{database_id}/branches/{branch_id}/versions` |
| 删除版本 | `DELETE /databases/{database_id}/branches/{branch_id}/versions/{version_id}` |