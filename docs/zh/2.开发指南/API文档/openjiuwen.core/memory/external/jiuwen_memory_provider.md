# openjiuwen.core.memory.external.jiuwen_memory_provider

`openjiuwen.core.memory.external.jiuwen_memory_provider` 是 openJiuwen 中基于 **Jiuwen（JiuwenMemory / mem2）** 的外部记忆提供者实现，继承自 `MemoryProvider`。它在**构造时**从两种后端中选择一种，对外暴露统一的 `MemoryProvider` 接口：

- `mode="server"`（默认）：通过 `httpx` 异步客户端对接远程 JiuwenMemory HTTP 服务（`POST /v1/<verb>` + `GET /healthz`），不在本地构建引擎，仅需网络客户端。适合 mem2 引擎作为独立进程/容器部署的生产环境；
- `mode="sdk"`：通过 `from jiuwen_memory.api import assemble` 在**进程内**构建本地 JiuwenMemory 内核并直接调用，无 HTTP 开销，无需单独运行服务。要求安装 `JiuwenMemory` 包（`pip install JiuwenMemory`）。适合将引擎嵌入宿主进程的单进程场景。

两种后端暴露相同的工具（`mem2_search` / `mem2_add`）与相同的系统提示词块，消费方对模式无感知。主要能力包括：

- 跨关键词（BM25）、向量、图（graph）召回通道的排序语义检索；
- 原文写入与 LLM 抽取（`infer`）两种写入语义；
- 基于 `tenant_id` + `user_id` 的 Scope 作用域映射；
- 读写分离的 HTTP 超时配置；
- server 模式内置熔断器（circuit breaker），连续失败达到阈值后短路冷却，避免雪崩。

## 模块常量

| 常量 | 类型 | 说明 |
|------|------|------|
| `_READ_TIMEOUT` | `float` | 读请求超时时间（秒），默认值：`30.0`。 |
| `_WRITE_TIMEOUT` | `float` | 写请求超时时间（秒），默认值：`120.0`。`/v1/add` 携带 `infer=true` 会触发真实 LLM 抽取 + 去重，因此写入超时上限高于读取。 |
| `_BREAKER_THRESHOLD` | `int` | 熔断阈值：连续失败达到该次数后熔断器开启，默认值：`5`。 |
| `_BREAKER_COOLDOWN_SECS` | `float` | 熔断冷却时长（秒），默认值：`120.0`。 |
| `_DEFAULT_TOP_K` | `int` | 默认召回数量，默认值：`10`。 |
| `_MAX_TOP_K` | `int` | 召回数量上限，默认值：`50`。 |

模块级定义了两个工具 schema 字典：`SEARCH_SCHEMA`（`mem2_search`）与 `ADD_SCHEMA`（`mem2_add`），用于在 `get_tool_schemas()` 中声明本提供者对外暴露的记忆工具。

> **关于星号导入（`from ... import *`）的说明**：模块末尾的 `__all__` 仅显式导出 `JiuwenMemoryProvider`。`SEARCH_SCHEMA`、`ADD_SCHEMA` 不在 `__all__` 中，星号导入无法引用；如需使用应采用显式导入（如 `from openjiuwen.core.memory.external.jiuwen_memory_provider import SEARCH_SCHEMA`）。

## class openjiuwen.core.memory.external.jiuwen_memory_provider.JiuwenMemoryProvider

```python
class openjiuwen.core.memory.external.jiuwen_memory_provider.JiuwenMemoryProvider(MemoryProvider)
```

`JiuwenMemoryProvider` 是基于 Jiuwen（JiuwenMemory / mem2）的外部记忆提供者，支持两种可切换后端。

**特性**：

- 双后端架构：`server`（远程 HTTP 服务）/ `sdk`（进程内内核），构造时固定，**不可事后切换**；
- 两种后端暴露相同的工具与系统提示词块，消费方无感知；
- `mem2_search` 跨 BM25 / 向量 / 图召回通道的排序检索；
- `mem2_add` 支持原文存储与 LLM 抽取（`infer`）两种写入语义；
- `tenant_id`（组织轴）+ `user_id`（用户/会话轴）构成 Scope，可全局配置也可按调用覆盖；
- server 模式读写超时分离，并内置熔断器。

**配置项**：

- `mode`：后端模式，`"server"`（默认）或 `"sdk"`（大小写不敏感）；
- `base_url`：server 模式的 JiuwenMemory 服务地址；
- `api_key`：server 模式可选 Bearer token；
- `config_dict`：sdk 模式的内核装配配置（两级命名空间字典）；
- `tenant_id` / `user_id`：Scope 两轴；
- `read_timeout` / `write_timeout`：server 模式 HTTP 超时；
- `infer_turns` / `save_assistant_turns`：`sync_turn` 的写入策略。

**使用样例**：

```python
>>> from openjiuwen.core.memory.external.jiuwen_memory_provider import JiuwenMemoryProvider
>>>
>>> # server 模式：对接远程 JiuwenMemory HTTP 服务
>>> provider = JiuwenMemoryProvider(
>>>     mode="server",
>>>     base_url="http://127.0.0.1:8137",
>>>     api_key="mem2-xxx",
>>>     tenant_id="default",
>>>     user_id="user_123",
>>> )
>>> await provider.initialize()
>>> resp = await provider.handle_tool_call("mem2_search", {"query": "用户偏好", "top_k": 5})
>>>
>>> # sdk 模式：进程内构建 JiuwenMemory 内核
>>> provider = JiuwenMemoryProvider(
>>>     mode="sdk",
>>>     tenant_id="default",
>>>     user_id="user_123",
>>>     config_dict={...},
>>> )
>>> await provider.initialize()
>>> resp = await provider.handle_tool_call(
>>>     "mem2_add",
>>>     {"content": "项目使用 Python 3.11", "infer": True},
>>> )
```

### __init__

```python
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

初始化 Jiuwen 记忆提供者。全部参数为**仅限关键字参数**。**不会发起网络请求，也不会导入/构建内核**，仅完成本地状态设置；真正的连接与内核装配发生在 `initialize()`。

**参数**：

* **mode**(str, 可选)：后端模式。`"server"`（默认）对接远程 HTTP 服务；`"sdk"` 在进程内构建本地内核。取值大小写不敏感（内部做 `strip().lower()` 归一化）。构造后固定，不可切换。
* **base_url**(str, 可选)：JiuwenMemory 服务 URL，末尾的 `/` 会被去除。默认值：`"http://127.0.0.1:8137"`。**仅 server 模式生效**。
* **api_key**(str, 可选)：可选 Bearer token，非空时以 `Authorization: Bearer <api_key>` 请求头发送。**仅 server 模式生效**。默认值：`""`。
* **tenant_id**(str, 可选)：Scope 的组织轴（`Scope.org`）。默认值：`"default"`。
* **user_id**(str, 可选)：Scope 的用户/会话轴，映射为 mem2 的 `scope`。默认值：`""`。
* **read_timeout**(float, 可选)：读请求超时时间（秒）。**仅 server 模式生效**。默认值：`30.0`。
* **write_timeout**(float, 可选)：写请求超时时间（秒），实际会被钳制为不低于模块常量 `_WRITE_TIMEOUT`（`120.0`）。**仅 server 模式生效**。默认值：`120.0`。
* **config_dict**(dict[str, Any] | None, 可选)：JiuwenMemory 装配配置（两级命名空间字典），经 `Config.from_dict()` 构建内核配置；`None` 表示使用内置内存默认配置（适合测试，重启即失）。运行时策略覆盖位于 `config_dict["globals"]["policies"]`。**仅 sdk 模式生效**。默认值：`None`。
* **infer_turns**(bool, 可选)：`sync_turn` 是否将用户对话轮经"抽取 + 去重"路径蒸馏为事实（默认 `True`）。设为 `False` 则原样存储对话文本。默认值：`True`。
* **save_assistant_turns**(bool, 可选)：`sync_turn` 是否同时持久化助手回复（默认 `False`，即**仅存储用户轮**，因为助手回复可推导、记忆价值低）。默认值：`False`。

**行为说明**：

- `mode` 不属于 `("server", "sdk")` 时抛出 `ValueError`；
- 根据 `mode` 构造对应的内部后端（`_ServerBackend` 或 `_SDKBackend`），后续所有接口调用均委托该后端。

### name

```python
@property
def name(self) -> str
```

返回提供者标识符，固定为 `"jiuwen_memory"`。

**返回**：

* **str**：`"jiuwen_memory"`。

### mode

```python
@property
def mode(self) -> str
```

返回当前后端模式。

**返回**：

* **str**：`"server"` 或 `"sdk"`（已归一化为小写）。

### is_available

```python
def is_available(self) -> bool
```

检查提供者是否已配置就绪。**不发起任何网络调用**。

**返回**：

* **bool**：server 模式下 `base_url` 非空返回 `True`；sdk 模式下恒返回 `True`（内核在 `initialize()` 时延迟装配）。

### is_initialized

```python
@property
def is_initialized(self) -> bool
```

检查提供者是否已完成初始化（即是否已调用 `initialize()`）。

**返回**：

* **bool**：后端已完成初始化返回 `True`，否则返回 `False`。

### async initialize

```python
async def initialize(self, **kwargs) -> None
```

初始化提供者，行为随模式而异。若已初始化，后端会直接覆盖相关状态并继续。**连接校验失败不会抛出异常**，便于在服务未就绪时也能完成初始化。

**参数**：

* ****kwargs**(Any, 可选)：覆盖参数。提供者层先处理 `infer_turns` / `save_assistant_turns`（覆盖构造时的取值），其余透传给后端。

**server 模式行为**：

- 支持覆盖：`tenant_id`、`user_id`（兼容别名 `scope_id`）、`base_url`、`api_key`；
- `base_url` 为空时抛出 `ValueError("Mem2 server base_url is required.")`；
- `httpx` 未安装时抛出 `RuntimeError`（提示 `pip install httpx`）；
- 创建 `httpx.AsyncClient`（以 `base_url` 为前缀，注入 `Content-Type: application/json` 及可选 `Authorization` 头，默认超时取 `read_timeout`）；
- 通过 `GET /healthz` 探活：状态码 `200` 记录连接成功日志；非 `200` 记录警告；连接异常仅记录警告——**探活失败不阻断初始化**（允许服务稍后启动），后续真实请求失败由熔断器兜底；
- 最终置 `_is_initialized = True`。

**sdk 模式行为**：

- 支持覆盖：`tenant_id`、`user_id`（兼容别名 `scope_id`）、`config_dict`、`infer_turns`、`save_assistant_turns`；
- 导入 `jiuwen_memory` 相关符号（`api.assemble`、`api.legacy_request_context`、`common.type_def.Scope/Modality/Context`、`retrieval.types.DisclosureLevel`），未安装时抛出 `RuntimeError`（提示 `pip install JiuwenMemory`）；
- `config_dict` 非空时通过 `Config.from_dict()` 构建配置（解析失败仅记录警告并回退内置默认配置）；`None` 使用内置内存默认配置；
- `assemble(config=cfg)` 装配内核，失败时抛出 `RuntimeError`；
- 成功后捕获 `Scope` / `Modality` / `Context` / `DisclosureLevel` / `legacy_request_context` 等符号并置 `_is_initialized = True`。

### async shutdown

```python
async def shutdown(self) -> None
```

关闭后端并清理资源。

**行为说明**：

- **server 模式**：调用 HTTP 客户端的 `aclose()`（关闭异常仅记录 debug 日志），置 `_is_initialized = False`；
- **sdk 模式**：仅丢弃内核引用；带 `close()` 的存储（如 sqlite）交给 GC 处理，提供者不做显式关闭——需要干净拆卸的调用方应在提供者之外自行处理。

### system_prompt_block

```python
def system_prompt_block(self) -> str
```

返回供 Agent 系统提示词使用的 Jiuwen 记忆能力说明块。

**返回**：

* **str**：系统提示词片段，形如：

```
# Jiuwen Memory
Active. user=user_123, tenant=default.
Use mem2_search to recall memories, mem2_add to store a fact. Conversation turns are extracted into memories automatically.
```

其中 `user` 为当前 `user_id`（为空时显示 `?`），`tenant` 为当前 `tenant_id`。

### get_tool_schemas

```python
def get_tool_schemas(self) -> List[Dict[str, Any]]
```

返回本提供者对外暴露的全部工具 schema 列表，供 Agent 调用。

**返回**：

* **List[Dict[str, Any]]**：包含以下 2 个 schema 字典：

| 工具名 | 说明 |
|--------|------|
| `mem2_search` | 按语义召回长期记忆，返回跨关键词（BM25）、向量、图召回通道的排序结果。用于回忆用户事实、偏好或历史上下文。参数：`query`（必填，检索内容）、`top_k`（可选，默认 `10`，上限 `50`）。 |
| `mem2_add` | 持久化一条记忆。默认原文存储并索引（不做 LLM 抽取）；设置 `infer=true` 时由服务端从内容中抽取并去重出自包含事实。参数：`content`（必填，记忆文本）、`infer`（可选，默认 `false`）、`tags`（可选，`list[str]` 标签，供后续过滤）。 |

### async handle_tool_call

```python
async def handle_tool_call(self, tool_name: str, args: Dict[str, Any]) -> str
```

分发工具调用到对应的内部处理器，并以 JSON 字符串返回结果。

**参数**：

* **tool_name**(str)：工具名，需与 `get_tool_schemas()` 中的 `name` 一致。
* **args**(Dict[str, Any])：工具参数。

**返回**：

* **str**：JSON 字符串形式的结果。成功时为处理器返回对象的 JSON；失败时为 `{"error": ...}` 形式的 JSON。

**行为说明**：

- 若未初始化，返回 `{"error": "Memory provider not initialized"}`；
- server 模式下若熔断器开启，返回 `{"error": "Mem2 server temporarily unavailable (repeated failures); will retry."}`；
- `mem2_search`：`query` 缺失时返回 `{"error": "Missing required parameter: query"}`；成功返回 `{"results": [...], "count": N}`（`results` 中每项含 `content` / `item_id` / `score`）；
- `mem2_add`：`content` 缺失时返回 `{"error": "Missing required parameter: content"}`；写入失败（server 不可用 / 内核错误）时返回对应错误 JSON；成功返回 `{"result": "stored"|"deduped", "item_id": ..., "content": ..., "tier": ...}`；
- 传入未知工具名时返回 `{"error": "Unknown tool: <tool_name>"}`；
- 其他异常：返回 `{"error": str(e), "results": []}`；server 模式下同时触发熔断失败计数。

**样例**：

```python
>>> # 语义检索记忆
>>> resp = await provider.handle_tool_call(
>>>     "mem2_search",
>>>     {"query": "用户偏好", "top_k": 5},
>>> )
>>>
>>> # 存储新记忆（原文存储）
>>> resp = await provider.handle_tool_call(
>>>     "mem2_add",
>>>     {"content": "项目使用 Python 3.11", "tags": ["project"]},
>>> )
>>>
>>> # 存储新记忆（LLM 抽取 + 去重）
>>> resp = await provider.handle_tool_call(
>>>     "mem2_add",
>>>     {"content": "用户偏好深色主题", "infer": True},
>>> )
```

### async prefetch

```python
async def prefetch(self, query: str, **kwargs) -> str
```

在模型调用前进行后台召回，将相关记忆格式化为上下文字符串注入提示词。

**参数**：

* **query**(str)：用于上下文召回的用户查询文本。
* ****kwargs**(Any, 可选)：召回过滤参数，支持：
  * `top_k`(int, 可选)：召回数量，默认值 `10`，上限 `50`；
  * 其余 kwargs（如 `tenant_id` / `user_id` / `scope_id`）用于按调用覆盖 Scope。

**返回**：

* **str**：格式化的上下文字符串。格式为以 `## Jiuwen Memory` 起始的列表，每行形如 `- 内容`；无命中、未初始化、`query` 为空或（server 模式）熔断器开启时返回 `""`。

**行为说明**：

- 调用后端 `search` 完成检索，检索内部已兜底（异常返回空列表），因此 `prefetch` 不会抛出；
- 召回为空时返回 `""`。

### async sync_turn

```python
async def sync_turn(self, user_msg: str, assistant_msg: str, **kwargs) -> None
```

将一轮完成的对话持久化为记忆。**默认仅存储用户轮**——助手回复可推导、记忆价值低，除非显式开启。

**参数**：

* **user_msg**(str)：用户消息内容。
* **assistant_msg**(str)：助手回复内容。
* ****kwargs**(Any, 可选)：
  * `infer`(bool, 可选)：是否走"抽取 + 去重"路径，默认取构造/`initialize()` 时确定的 `infer_turns`（即 `True`）。`True` 时服务端将用户消息蒸馏为去重事实（mem0 风格）；`False` 时原文存储；
  * `save_assistant`(bool, 可选)：是否同时持久化助手回复，默认取 `save_assistant_turns`（即 `False`）；
  * 其余 kwargs（如 `tenant_id` / `user_id` / `scope_id`）透传给后端 `add`，用于按调用覆盖 Scope。

**行为说明**：

- 用户消息以 `tags=["conversation", "user"]` 写入，`infer` 语义如上；
- 开启 `save_assistant` 时，助手回复以 `infer=False`（原文）、`tags=["conversation", "assistant"]` 写入；
- server 模式下熔断器开启时直接跳过本次写入；写入失败仅记录警告/计数，不抛出；
- `user_msg` 为空时不写用户轮。

### async on_session_end

```python
async def on_session_end(self, messages: List[Dict[str, Any]]) -> None
```

会话结束钩子。当前实现为空操作——没有需要在服务端关闭的会话生命周期。

**参数**：

* **messages**(List[Dict[str, Any]])：会话消息列表。

**说明**：本方法当前不执行任何逻辑，子类或后续版本可按需覆写。

## 双后端架构

两种后端实现相同的读写与工具分发接口，`JiuwenMemoryProvider` 仅做委托。

### _ServerBackend（server 模式）

- 所有操作经 `POST /v1/<verb>` 以 JSON body 分发，`<verb>` 选择处理器（`add` / `search` / ...）；探活走 `GET /healthz`；
- 读写超时分离：检索等读操作用 `read_timeout`（默认 `30.0` 秒），写入用 `write_timeout`（下限 `120.0` 秒，因为 `infer=true` 触发真实 LLM 抽取 + 去重）；
- 每次调用通过 `_scope_payload()` 解析 `tenant_id` / `scope`（`user_id` → `scope`，`tenant_id` → 组织轴），均可按调用覆盖；
- 核心方法 `_post_verb()` 在任何失败（网络异常或非 2xx）时记录熔断失败并返回 `None`，调用方降级为空结果，不向上传播异常；
- 熔断器状态在读写路径间共享（详见下文）。

### _SDKBackend（sdk 模式）

- 通过 `from jiuwen_memory.api import assemble` 构建内核（与 HTTP 服务端同一装配入口），直接调用 `api.add` / `api.search`，无网络跳转；
- JiuwenMemory 的 `LocalMemoryAPI.add` / `search` 为同步方法且内部会 `asyncio.run()`，因此本后端通过 `asyncio.to_thread` 在工作线程中执行这些调用，避免阻塞宿主事件循环；
- 检索使用 `DisclosureLevel.L2`（返回全文内容），与 server 模式行为对齐；
- 写入返回空 `units` 表示内容在 infer 路径下全部被去重，映射为 `{"item_id": None}`；
- `config_dict` 为两级命名空间字典，`None` → 内置内存默认配置（适合测试，重启丢失）；运行时策略覆盖位于 `config_dict["globals"]["policies"]`；
- 进程内调用无网络故障面，**不设熔断器**。

## 写入语义（Mem2 写入模式）

Mem2 的写入语义镜像上游服务的多种模式，本提供者实际暴露以下两种：

| 模式 | 触发方式 | 行为 |
|------|----------|------|
| 原文存储（默认） | `infer` 未设置或为 `false` | 原样存储 `content` 并索引，不做 LLM 抽取，事实按原文持久化（durable fact-as-written）。 |
| LLM 抽取 | `infer=true` | 服务端同步执行 LLM 抽取（去重感知），存储派生事实而非原始消息。这是 mem0 风格路径，也是 `sync_turn` 的默认路径，使对话被蒸馏为事实而非原文堆存。 |

> **说明**：模块 docstring 中还提及 `procedural=true` 写入模式（将对话轮汇总为一条 PROCEDURAL 执行历史），但当前实现未在 `sync_turn` 或工具参数中暴露该入口。

## Scope 映射

mem2 以 `tenant_id` + 单一 `scope` 字符串界定作用域，本提供者将其映射为 `Scope(org=tenant_id, user=scope)`：

- `user_id`（调用方约定）即 mem2 的 `scope`（按用户/按会话轴）；
- `tenant_id` 为组织轴，默认 `"default"`；
- 可在构造时、`initialize()` 时或**每次调用**（kwargs 传 `tenant_id` / `user_id`，兼容别名 `scope_id`）覆盖。

## 工具调用结果结构

各工具经 `handle_tool_call` 返回的 JSON（反序列化后）结构如下：

### mem2_search

```json
{
  "results": [
    {"content": "用户偏好深色主题", "item_id": "unit_xxx", "score": 0.87}
  ],
  "count": 1
}
```

### mem2_add

```json
{
  "result": "stored",
  "item_id": "unit_xxx",
  "content": "用户偏好深色主题",
  "tier": "hot"
}
```

> **说明**：`result` 为 `"stored"` 表示已新存（`item_id` 非空）；为 `"deduped"` 表示 infer 路径下内容被判定为重复未新存（`item_id` 为 `null`）。`tier` 为记忆层级信息（由内核/服务端返回）。

### 错误返回

```json
{"error": "Memory provider not initialized"}
{"error": "Mem2 server temporarily unavailable (repeated failures); will retry."}
{"error": "Missing required parameter: query"}
{"error": "Missing required parameter: content"}
{"error": "Mem2 add failed (server unavailable)."}
{"error": "Mem2 add failed (kernel error)."}
{"error": "Unknown tool: <tool_name>"}
{"error": "<异常信息>", "results": []}
```

## 熔断器机制（server 模式）

`_ServerBackend` 内置轻量熔断器以应对 JiuwenMemory 服务不可用场景：

- **失败计数**：`_post_verb()` 每次失败（网络异常或非 2xx 状态码）触发 `_record_failure()`，连续失败计数自增；`handle_tool_call` 的异常路径同样计数；读写路径**共享**同一计数；
- **熔断阈值**：连续失败达到 `_BREAKER_THRESHOLD`（默认 `5`）次后熔断器开启，记录冷却截止时间（基于单调时钟 `time.monotonic()`），冷却 `_BREAKER_COOLDOWN_SECS`（默认 `120.0` 秒）；
- **熔断期间**：`handle_tool_call` 返回临时不可用错误 JSON；`search` / `prefetch` 返回空结果；`add` 返回 `None`；`sync_turn` 跳过写入——均不再发起网络请求；
- **冷却恢复**：超过冷却时间后，下次检查自动重置计数并恢复（半开）；
- **成功重置**：任意一次成功调用触发 `_record_success()`，将失败计数清零。

sdk 模式为进程内调用，无网络故障面，不设熔断器。

## JiuwenMemory HTTP 端点（server 模式）

本提供者在 server 模式下通过以下 JiuwenMemory REST 端点完成实际操作（`base_url` 为前缀）：

| 操作 | 方法 & 路径 |
|------|-------------|
| 通用操作分发 | `POST /v1/<verb>`（`<verb>` 如 `add` / `search`，由 JSON body 携带参数选择处理器） |
| 语义检索 | `POST /v1/search` |
| 写入记忆 | `POST /v1/add` |
| 健康检查 | `GET /healthz` |

请求 body 中统一携带 `tenant_id` / `scope`（由 `_scope_payload()` 解析），写入类请求另携带 `content` / `tags` / `metadata`（`infer=true` 时写入 `metadata.infer`）。
