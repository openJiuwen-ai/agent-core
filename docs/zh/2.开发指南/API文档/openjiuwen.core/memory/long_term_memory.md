# openjiuwen.core.memory.long_term_memory

`openjiuwen.core.memory.long_term_memory` 是 openJiuwen 中统一的**长期记忆管理引擎**，负责：

- 管理用户对话消息的持久化与检索；
- 管理用户变量记忆（如偏好、个人信息等结构化数据）；
- 管理用户画像（长期记忆，通过 LLM 从对话中提取）；
- 支持基于 `scope_id` 的多租户隔离；
- 支持向量检索、分页查询、按条件删除等操作。


## class openjiuwen.core.memory.long_term_memory.LongTermMemory

```
class openjiuwen.core.memory.long_term_memory.LongTermMemory(metaclass=Singleton)
```

`LongTermMemory` 是 openJiuwen 0.1.4 中统一的**长期记忆管理引擎**，采用单例模式。

> **说明**：与旧版 `MemoryEngine(config: SysMemConfig, ...)` 不同，`LongTermMemory` 采用**无参构造 + 分步初始化**的方式：
> 1. 先调用 `await register_store(...)` 注册底层存储；
> 2. 再调用 `set_config(MemoryEngineConfig(...))` 设置全局配置；
> 3. 可选地通过 `set_scope_config(scope_id, MemoryScopeConfig(...))` 为不同业务场景配置独立的模型/向量参数。

```
LongTermMemory()
```

初始化 `LongTermMemory` 实例（单例模式，多次调用返回同一实例）。

**内部状态初始化**：

- 配置相关：`_sys_mem_config: MemoryEngineConfig | None = None`、`_scope_config: dict[str, MemoryScopeConfig] = {}`；
- 存储相关：`kv_store / semantic_store / db_store` 均为 `None`，需通过 `register_store` 注册；
- 管理器相关：`scope_user_mapping_manager / message_manager / user_profile_manager / variable_manager / write_manager / search_manager / generator` 均为 `None`，在 `set_config` 时初始化；
- LLM 相关：`_base_llm: Tuple[str, Model] | None = None`（在 `set_config` 时设置）；
- 嵌入模型缓存：`_scope_embedding: dict[str, Embedding] = {}`。


### async register_store

```
async def register_store(
    self,
    kv_store: BaseKVStore,
    vector_store: BaseVectorStore | None = None,
    db_store: BaseDbStore | None = None,
    embedding_model: Embedding | None = None,
) -> None
```

注册底层存储实例，必须在调用 `set_config` 之前完成。

**参数**：

* **kv_store**(BaseKVStore)：**必填**，键值存储实例，用于快速访问结构化数据（如 scope 配置、用户变量等）。若为 `None`，会抛出 `build_error`（`MEMORY_REGISTER_STORE_EXECUTION_ERROR`）。
* **vector_store**(BaseVectorStore | None, 可选)：向量存储实例，用于语义相似度检索。若为 `None`，则语义检索功能不可用。默认值：`None`。
* **db_store**(BaseDbStore | None, 可选)：关系型数据库存储实例，用于持久化消息、scope-user 映射等。若为 `None`，则消息持久化功能不可用。默认值：`None`。
* **embedding_model**(Embedding | None, 可选)：全局嵌入模型实例，用于在注册时初始化 `semantic_store` 的嵌入能力。若为 `None`，后续可通过 `set_scope_config` 为不同 scope 配置独立的嵌入模型。默认值：`None`。

**异常**：

* **build_error**：当 `kv_store` 为 `None` 或存储类型不匹配时抛出。

**样例**：

```python
>>> from openjiuwen.core.memory.long_term_memory import LongTermMemory
>>> from openjiuwen.core.foundation.store.kv.db_based_kv_store import DbBasedKVStore
>>> from openjiuwen.core.foundation.store import create_vector_store
>>> from openjiuwen.core.foundation.store.db.default_db_store import DefaultDbStore
>>> from sqlalchemy.ext.asyncio import create_async_engine
>>>
>>> # 创建 LongTermMemory 实例
>>> engine = LongTermMemory()
>>>
>>> project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
>>> resource_dir = os.path.join(project_root, "resources")
>>> os.makedirs(resource_dir, exist_ok=True)
>>> kv_path = os.path.join(resource_dir, "kv_store.db")
>>> # 使用 sqlite 前需要安装 aiosqlite 依赖包， 目前 aiosqlite 是可选依赖
>>> engine = create_async_engine(
>>>     f"sqlite+aiosqlite:///{kv_path}",
>>>     pool_pre_ping=True,
>>>     echo=False,
>>> )
>>> # ---------- KV Store ----------
>>> kv_store = DbBasedKVStore(engine)
>>>
>>> # ---------- Vector Store ----------
>>> # 使用 Chroma 向量存储， 需要安装 chromadb 依赖包， 目前 chromadb 是可选依赖
>>> vector_store = create_vector_store("chroma", persist_directory="./resources/chroma")
>>> # 或使用 Milvus 向量存储
>>> # vector_store = create_vector_store("milvus", milvus_uri="http://localhost:19530")
>>>
>>>
>>> # ---------- DB Store ----------
>>> db_user = os.getenv("DB_USER", "root")
>>> db_passport = os.getenv("DB_PASSWORD", "root")
>>> db_host = os.getenv("DB_HOST", "124.71.229.79")
>>> db_port = os.getenv("DB_PORT", "33306")
>>> agent_db_name = os.getenv("AGENT_DB_NAME", "jiuwen_agent")
>>>
>>> db_store = DefaultDbStore(create_async_engine(
>>>     f"mysql+aiomysql://{db_user}:{db_passport}@{db_host}:{db_port}/{agent_db_name}?charset=utf8mb4",
>>>     pool_size=20,
>>>     max_overflow=20
>>> ))
>>>
>>> # ---------- 注册存储 ----------
>>> await engine.register_store(
>>>     kv_store=kv_store,
>>>     vector_store=vector_store,
>>>     db_store=db_store
>>> )
>>>
```


### set_config

```
def set_config(self, config: MemoryEngineConfig) -> None
```

设置全局记忆引擎配置，并初始化内部管理器。

**参数**：

* **config**(MemoryEngineConfig)：全局引擎配置，包含：
  * `default_model_cfg: ModelRequestConfig`：默认用于生成记忆的大模型请求参数；
  * `default_model_client_cfg: ModelClientConfig`：默认大模型客户端配置；
  * `input_msg_max_len: int`：输入消息最大长度（默认 8192）；
  * `crypto_key: bytes`：AES 加密密钥（长度必须为 32 字节；为空则不加密）。

**前置条件**：

- 必须已调用 `register_store` 注册 `kv_store`、`semantic_store`、`db_store`，否则会抛出 `build_error`（`MEMORY_SET_CONFIG_EXECUTION_ERROR`）。

**异常**：

* **build_error**：当未调用 `register_store` 或配置无效时抛出。

**样例**：

```python
>>> from openjiuwen.core.memory.long_term_memory import LongTermMemory
>>> from openjiuwen.core.memory.config import MemoryEngineConfig
>>> from openjiuwen.core.foundation.llm.schema.config import ModelRequestConfig, ModelClientConfig
>>> 
>>> # 创建配置
>>> config = MemoryEngineConfig(
>>>     default_model_cfg=ModelRequestConfig(
>>>         model="gpt-3.5-turbo",
>>>         temperature=0.0,
>>>     ),
>>>     default_model_client_cfg=ModelClientConfig(
>>>         client_id="default_memory_llm",
>>>         client_provider="OpenAI",
>>>         api_key="sk-xxxx",
>>>         api_base="https://api.openai.com/v1",
>>>     ),
>>>     input_msg_max_len=8192,
>>>     crypto_key=b"your-32-byte-aes-key-here!!",
>>> )
>>> 
>>> # 设置配置
>>> memory = LongTermMemory()
>>> memory.set_config(config)
```


### async set_scope_config

```
async def set_scope_config(
    self,
    scope_id: str,
    memory_scope_config: MemoryScopeConfig,
) -> bool
```

为指定 `scope_id` 设置作用域级记忆配置，并持久化到 `kv_store`。

**参数**：

* **scope_id**(str)：作用域标识符，不能包含 `/`，长度不能超过 128 字符；若格式无效，返回 `False` 并记录错误日志。
* **memory_scope_config**(MemoryScopeConfig)：作用域配置，包含：
  * `model_cfg: ModelRequestConfig | None`：该 scope 下使用的大模型请求配置；
  * `model_client_cfg: ModelClientConfig | None`：该 scope 下使用的大模型客户端配置；
  * `embedding_cfg: EmbeddingConfig | None`：该 scope 下使用的嵌入模型配置。

**返回**：

* **bool**：设置成功返回 `True`，`scope_id` 格式无效返回 `False`。

**样例**：

```python
>>> from openjiuwen.core.memory.long_term_memory import LongTermMemory
>>> from openjiuwen.core.memory.config import MemoryScopeConfig
>>> from openjiuwen.core.foundation.llm.schema.config import ModelRequestConfig, ModelClientConfig
>>> from openjiuwen.core.retrieval.common.config import EmbeddingConfig
>>> 
>>> # 创建作用域配置
>>> scope_config = MemoryScopeConfig(
>>>     model_cfg=ModelRequestConfig(
>>>         model="gpt-4",
>>>         temperature=0.1,
>>>     ),
>>>     model_client_cfg=ModelClientConfig(
>>>         client_id="scope_llm",
>>>         client_provider="OpenAI",
>>>         api_key="sk-yyyy",
>>>         api_base="https://api.openai.com/v1",
>>>     ),
>>>     embedding_cfg=EmbeddingConfig(
>>>         model_name="text-embedding-3-large",
>>>         base_url="https://api.openai.com/v1",
>>>         api_key="sk-zzzz",
>>>     ),
>>> )
>>> 
>>> # 设置作用域配置
>>> memory = LongTermMemory()
>>> success = await memory.set_scope_config("my_scope", scope_config)
>>> print(f"设置结果: {success}")
```


### async get_scope_config

```
async def get_scope_config(self, scope_id: str) -> MemoryScopeConfig | None
```

从 `kv_store` 中读取指定 `scope_id` 的作用域配置，并解密 API key。

**参数**：

* **scope_id**(str)：作用域标识符。

**返回**：

* **MemoryScopeConfig | None**：若配置存在，返回解密后的配置对象；若不存在或 `scope_id` 格式无效，返回 `None`。

**样例**：

```python
>>> from openjiuwen.core.memory.long_term_memory import LongTermMemory
>>> 
>>> # 获取作用域配置
>>> memory = LongTermMemory()
>>> scope_config = await memory.get_scope_config("my_scope")
>>> 
>>> if scope_config:
>>>     print(f"模型配置: {scope_config.model_cfg}")
>>>     print(f"客户端配置: {scope_config.model_client_cfg}")
>>> else:
>>>     print("未找到作用域配置")
```


### async delete_scope_config

```
async def delete_scope_config(self, scope_id: str) -> bool
```

删除指定 `scope_id` 的作用域配置（从 `kv_store` 和内存缓存中移除）。

**参数**：

* **scope_id**(str)：作用域标识符。

**返回**：

* **bool**：删除成功返回 `True`，`scope_id` 格式无效或删除失败返回 `False`。

**样例**：

```python
>>> from openjiuwen.core.memory.long_term_memory import LongTermMemory
>>> 
>>> # 删除作用域配置
>>> memory = LongTermMemory()
>>> success = await memory.delete_scope_config("my_scope")
>>> print(f"删除结果: {success}")
```


### async delete_mem_by_scope

```
async def delete_mem_by_scope(self, scope_id: str) -> bool
```

删除指定 `scope_id` 下的所有记忆数据（包括消息、用户画像、变量等）。

**参数**：

* **scope_id**(str)：作用域标识符。

**返回**：

* **bool**：删除成功返回 `True`，`scope_id` 格式无效返回 `False`。

**样例**：

```python
>>> from openjiuwen.core.memory.long_term_memory import LongTermMemory
>>> 
>>> # 删除作用域下的所有记忆
>>> memory = LongTermMemory()
>>> success = await memory.delete_mem_by_scope("my_scope")
>>> print(f"删除结果: {success}")
```


### async add_messages

```
async def add_messages(
    self,
    messages: list[BaseMessage],
    agent_config: AgentMemoryConfig,
    *,
    user_id: str = "__default__",
    scope_id: str = "__default__",
    session_id: str = "__default__",
    timestamp: datetime | None = None,
    gen_mem: bool = True,
    gen_mem_with_history_msg_num: int = 5,
) -> None
```

添加对话消息到记忆引擎，并根据 `agent_config` 生成记忆（用户画像、变量等）。

**参数**：

* **messages**(list[BaseMessage])：要添加的消息列表（通常包含用户消息和 AI 回复）。
* **agent_config**(AgentMemoryConfig)：Agent 记忆策略配置，包含：
  * `mem_variables: list[Param]`：需要提取的变量记忆配置（变量名、描述、类型等）；
  * `enable_long_term_mem: bool`：是否开启长期记忆生成（默认 `True`）。
*   * `enable_fragment_memory: bool`：是否开启记忆片段生成（默认 `True`）。  
*   * `enable_summary_memory: bool`：是否开启用户摘要记忆生成（默认 `True`）。  
* **user_id**(str, 可选)：用户标识符。默认值：`"__default__"`。
* **scope_id**(str, 可选)：作用域标识符；格式无效时直接返回，不抛异常。默认值：`"__default__"`。
* **session_id**(str, 可选)：会话标识符。默认值：`"__default__"`。
* **timestamp**(datetime | None, 可选)：消息时间戳，若为 `None` 则使用当前 UTC 时间。默认值：`None`。
* **gen_mem**(bool, 可选)：是否生成记忆；为 `False` 时仅保存消息，不触发记忆提取。默认值：`True`。
* **gen_mem_with_history_msg_num**(int, 可选)：生成记忆时参考的历史消息数量。默认值：5。

**异常**：

* **build_error**：当写入记忆失败时抛出（`MEMORY_ADD_MEMORY_EXECUTION_ERROR`）。

**样例**：

```python
>>> from openjiuwen.core.memory.long_term_memory import LongTermMemory
>>> from openjiuwen.core.memory.config import AgentMemoryConfig
>>> from openjiuwen.core.common.schema.param import Param
>>> from openjiuwen.core.foundation.llm.schema.message import UserMessage, AssistantMessage
>>> 
>>> # 创建 Agent 记忆策略配置
>>> agent_config = AgentMemoryConfig(
>>>     mem_variables=[
>>>         Param(
>>>             name="favorite_color",
>>>             description="用户喜欢的颜色",
>>>             type="string",
>>>             required=False,
>>>         ),
>>>         Param(
>>>             name="age",
>>>             description="用户年龄",
>>>             type="number",
>>>             required=False,
>>>         ),
>>>     ],
>>>     enable_long_term_mem=True,
>>>     enable_fragment_memory=True,
>>>     enable_summary_memory=True,
>>> )
>>> 
>>> # 准备消息
>>> messages = [
>>>     UserMessage(content="我喜欢蓝色，我今年25岁"),
>>>     AssistantMessage(content="好的，我记住了您喜欢蓝色，今年25岁。")
>>> ]
>>> 
>>> # 添加消息
>>> memory = LongTermMemory()
>>> await memory.add_messages(
>>>     messages=messages,
>>>     agent_config=agent_config,
>>>     user_id="user123",
>>>     scope_id="my_scope",
>>>     session_id="session456"
>>> )
```


### async get_recent_messages

```
async def get_recent_messages(
    self,
    user_id: str = "__default__",
    scope_id: str = "__default__",
    session_id: str = "__default__",
    num: int = 10,
) -> list[BaseMessage]
```

获取指定用户/scope/会话的最近 N 条消息，按写入顺序返回。

**参数**：

* **user_id**(str, 可选)：用户标识符。默认值：`"__default__"`。
* **scope_id**(str, 可选)：作用域标识符；格式无效时返回空列表。默认值：`"__default__"`。
* **session_id**(str, 可选)：会话标识符。默认值：`"__default__"`。
* **num**(int, 可选)：要获取的消息数量。默认值：10。

**返回**：

* **list[BaseMessage]**：消息列表，按写入时间顺序排列；若 `scope_id` 格式无效或 `message_manager` 未初始化，返回空列表。

**样例**：

```python
>>> from openjiuwen.core.memory.long_term_memory import LongTermMemory
>>> 
>>> # 获取最近消息
>>> memory = LongTermMemory()
>>> messages = await memory.get_recent_messages(
>>>     user_id="user123",
>>>     scope_id="my_scope",
>>>     session_id="session456",
>>>     num=5
>>> )
>>> 
>>> for msg in messages:
>>>     print(f"{msg.role}: {msg.content}")
```


### async get_message_by_id

```
async def get_message_by_id(self, msg_id: str) -> Tuple[BaseMessage, datetime] | None
```

根据消息 id 获取单条消息及其创建时间戳。

**参数**：

* **msg_id**(str)：消息唯一标识符。

**返回**：

* **Tuple[BaseMessage, datetime] | None**：若消息存在，返回 `(消息对象, 创建时间)`；若 `message_manager` 未初始化或消息不存在，返回 `None`。

**样例**：

```python
>>> from openjiuwen.core.memory.long_term_memory import LongTermMemory
>>> 
>>> # 根据ID获取消息
>>> memory = LongTermMemory()
>>> result = await memory.get_message_by_id("msg_12345")
>>> 
>>> if result:
>>>     message, timestamp = result
>>>     print(f"消息内容: {message.content}")
>>>     print(f"创建时间: {timestamp}")
>>> else:
>>>     print("未找到消息")
```


### async delete_mem_by_id

```
async def delete_mem_by_id(
    self,
    mem_id: str,
    user_id: str = "__default__",
    scope_id: str = "__default__",
) -> None
```

删除指定 id 的记忆条目（用户画像或变量）。

**参数**：

* **mem_id**(str)：记忆唯一标识符。
* **user_id**(str, 可选)：用户标识符。默认值：`"__default__"`。
* **scope_id**(str, 可选)：作用域标识符；格式无效时直接返回。默认值：`"__default__"`。

**异常**：

* **build_error**：当 `write_manager` 未初始化时抛出（`MEMORY_DELETE_MEMORY_EXECUTION_ERROR`）。

**样例**：

```python
>>> from openjiuwen.core.memory.long_term_memory import LongTermMemory
>>> 
>>> # 删除指定记忆
>>> memory = LongTermMemory()
>>> await memory.delete_mem_by_id(
>>>     mem_id="mem_12345",
>>>     user_id="user123",
>>>     scope_id="my_scope"
>>> )
```


### async delete_mem_by_user_id

```
async def delete_mem_by_user_id(
    self,
    user_id: str = "__default__",
    scope_id: str = "__default__",
) -> None
```

删除指定用户在某 scope 下的所有类型记忆（用户画像、变量等）。

**参数**：

* **user_id**(str, 可选)：用户标识符。默认值：`"__default__"`。
* **scope_id**(str, 可选)：作用域标识符；格式无效时直接返回。默认值：`"__default__"`。

**异常**：

* **build_error**：当 `write_manager` 未初始化时抛出。

**样例**：

```python
>>> from openjiuwen.core.memory.long_term_memory import LongTermMemory
>>> 
>>> # 删除用户的所有记忆
>>> memory = LongTermMemory()
>>> await memory.delete_mem_by_user_id(
>>>     user_id="user123",
>>>     scope_id="my_scope"
>>> )
```


### async update_mem_by_id

```
async def update_mem_by_id(
    self,
    mem_id: str,
    memory: str,
    user_id: str = "__default__",
    scope_id: str = "__default__",
) -> None
```

更新指定 id 的记忆内容。

**参数**：

* **mem_id**(str)：记忆唯一标识符。
* **memory**(str)：新的记忆内容。
* **user_id**(str, 可选)：用户标识符。默认值：`"__default__"`。
* **scope_id**(str, 可选)：作用域标识符；格式无效时直接返回。默认值：`"__default__"`。

**异常**：

* **build_error**：当 `write_manager` 未初始化时抛出（`MEMORY_UPDATE_MEMORY_EXECUTION_ERROR`）。

**样例**：

```python
>>> from openjiuwen.core.memory.long_term_memory import LongTermMemory
>>> 
>>> # 更新记忆内容
>>> memory = LongTermMemory()
>>> await memory.update_mem_by_id(
>>>     mem_id="mem_12345",
>>>     memory="更新后的记忆内容",
>>>     user_id="user123",
>>>     scope_id="my_scope"
>>> )
```


### async get_variables

```
async def get_variables(
    self,
    names: list[str] | str | None = None,
    user_id: str = "__default__",
    scope_id: str = "__default__",
) -> dict[str, str]
```

获取用户变量（一个或多个）。

**参数**：

* **names**(list[str] | str | None, 可选)：
  * 若为 `None`：返回该用户在该 scope 下的所有变量；
  * 若为 `str`：返回单个变量（`{name: value}`）；
  * 若为 `list[str]`：返回多个变量（`{name1: value1, name2: value2, ...}`）。
  默认值：`None`。
* **user_id**(str, 可选)：用户标识符。默认值：`"__default__"`。
* **scope_id**(str, 可选)：作用域标识符；格式无效时返回空字典。默认值：`"__default__"`。

**返回**：

* **dict[str, str]**：变量名到变量值的映射；若 `scope_id` 格式无效或 `search_manager` 未初始化，返回空字典或抛出异常。

**异常**：

* **build_error**：当 `search_manager` 未初始化或 `names` 类型不符合预期时抛出（`MEMORY_GET_MEMORY_EXECUTION_ERROR`）。

**样例**：

```python
>>> from openjiuwen.core.memory.long_term_memory import LongTermMemory
>>> 
>>> # 获取所有变量
>>> memory = LongTermMemory()
>>> all_vars = await memory.get_variables(
>>>     user_id="user123",
>>>     scope_id="my_scope"
>>> )
>>> print(f"所有变量: {all_vars}")
>>> 
>>> # 获取单个变量
>>> favorite_color = await memory.get_variables(
>>>     names="favorite_color",
>>>     user_id="user123",
>>>     scope_id="my_scope"
>>> )
>>> print(f"喜欢的颜色: {favorite_color}")
>>> 
>>> # 获取多个变量
>>> some_vars = await memory.get_variables(
>>>     names=["favorite_color", "age"],
>>>     user_id="user123",
>>>     scope_id="my_scope"
>>> )
>>> print(f"部分变量: {some_vars}")
```


### async search_user_mem

```
async def search_user_mem(
    self,
    query: str,
    num: int,
    user_id: str = "__default__",
    scope_id: str = "__default__",
    threshold: float = 0.3,
) -> list[MemResult]
```

基于语义相似度搜索用户记忆（用户画像、变量等），返回与查询最相关的 N 条记忆。

**参数**：

* **query**(str)：查询文本。
* **num**(int)：要返回的记忆数量（top-k）。
* **user_id**(str, 可选)：用户标识符。默认值：`"__default__"`。
* **scope_id**(str, 可选)：作用域标识符；格式无效时返回空列表。默认值：`"__default__"`。
* **threshold**(float, 可选)：相似度阈值，低于该阈值的记忆会被过滤。默认值：0.3。

**返回**：

* **list[MemResult]**：记忆结果列表，每个 `MemResult` 包含：
  * `mem_info: MemInfo`（`mem_id / content / type`）；
  * `score: float`（相似度分数）。

**异常**：

* **build_error**：当 `search_manager` 未初始化时抛出。

**样例**：

```python
>>> from openjiuwen.core.memory.long_term_memory import LongTermMemory
>>> 
>>> # 搜索用户记忆
>>> memory = LongTermMemory()
>>> results = await memory.search_user_mem(
>>>     query="用户的兴趣爱好",
>>>     num=5,
>>>     user_id="user123",
>>>     scope_id="my_scope",
>>>     threshold=0.4
>>> )
>>> 
>>> for result in results:
>>>     print(f"内容: {result.mem_info.content}")
>>>     print(f"相似度: {result.score}")
>>>     print("---")
```


### async user_mem_total_num

```
async def user_mem_total_num(
    self,
    user_id: str = "__default__",
    scope_id: str = "__default__",
) -> int
```

返回指定用户在某 scope 下的记忆总数。

**参数**：

* **user_id**(str, 可选)：用户标识符。默认值：`"__default__"`。
* **scope_id**(str, 可选)：作用域标识符；格式无效时返回 0。默认值：`"__default__"`。

**返回**：

* **int**：记忆总数；若 `scope_id` 格式无效，返回 0。

**样例**：

```python
>>> from openjiuwen.core.memory.long_term_memory import LongTermMemory
>>> 
>>> # 获取记忆总数
>>> memory = LongTermMemory()
>>> total = await memory.user_mem_total_num(
>>>     user_id="user123",
>>>     scope_id="my_scope"
>>> )
>>> print(f"记忆总数: {total}")
```


### async search_user_history_summary

```
async def search_user_history_summary(
    self,
    query: str,
    num: int,
    user_id: str = "__default__",
    scope_id: str = "__default__",
    threshold: float = 0.3,
) -> list[MemResult]
```

基于语义相似度搜索用户摘要记忆，返回与查询最相关的 N 条摘要记忆。

**参数**：

* **query**(str)：搜索查询字符串。
* **num**(int)：要返回的结果数量（top-k）。
* **user_id**(str, 可选)：用户标识符。默认值：`"__default__"`。
* **scope_id**(str, 可选)：作用域标识符；格式无效时返回空列表。默认值：`"__default__"`。
* **threshold**(float, 可选)：结果的最小相似度阈值；低于该阈值的记忆会被过滤。默认值：0.3。

**返回**：

* **list[MemResult]**：记忆结果列表，每个 `MemResult` 包含：
  * `mem_info: MemInfo`（`mem_id / content / type`）；
  * `score: float`（相似度分数）。

**异常**：

* **build_error**：当 `search_manager` 未初始化时抛出。

**样例**：

```python
>>> from openjiuwen.core.memory.long_term_memory import LongTermMemory
>>> 
>>> # 搜索用户摘要记忆
>>> memory = LongTermMemory()
>>> results = await memory.search_user_history_summary(
>>>     query="最近关于工作的对话",
>>>     num=5,
>>>     user_id="user123",
>>>     scope_id="my_scope",
>>>     threshold=0.4
>>> )
>>> 
>>> for result in results:
>>>     print(f"内容: {result.mem_info.content}")
>>>     print(f"相似度: {result.score}")
>>>     print("---")
```


### async get_user_mem_by_page

```
async def get_user_mem_by_page(
    self,
    user_id: str = "__default__",
    scope_id: str = "__default__",
    page_num: int = 1,
    page_size: int = 10,
) -> dict[str, Any]
```

分页获取指定用户在某 scope 下的记忆。

**参数**：

* **user_id**(str, 可选)：用户标识符。默认值：`"__default__"`。
* **scope_id**(str, 可选)：作用域标识符；格式无效时返回空字典。默认值：`"__default__"`。
* **page_num**(int, 可选)：页码，从 1 开始。默认值：1。
* **page_size**(int, 可选)：每页大小。默认值：10。

**返回**：

* **dict[str, Any]**：包含以下字段：
  * `total: int`：总记忆数；
  * `page_num: int`：当前页码；
  * `page_size: int`：每页大小；
  * `total_pages: int`：总页数；
  * `data: list[MemInfo]`：当前页的记忆列表。

**异常**：

* **build_error**：当 `search_manager` 未初始化时抛出（`MEMORY_GET_MEMORY_EXECUTION_ERROR`）。

**样例**：

```python
>>> from openjiuwen.core.memory.long_term_memory import LongTermMemory
>>> 
>>> # 分页获取用户记忆
>>> memory = LongTermMemory()
>>> result = await memory.get_user_mem_by_page(
>>>     user_id="user123",
>>>     scope_id="my_scope",
>>>     page_num=2,
>>>     page_size=5
>>> )
>>> 
>>> print(f"总记忆数: {result['total']}")
>>> print(f"当前页: {result['page_num']}/{result['total_pages']}")
>>> 
>>> for mem_info in result['data']:
>>>     print(f"ID: {mem_info.mem_id}, 内容: {mem_info.content[:50]}...")
```


> **说明**：所有方法中涉及的 `user_id`、`scope_id`、`session_id` 若使用默认值 `"__default__"`，表示使用系统默认标识符；在实际业务中，建议传入有意义的业务标识符以支持多租户隔离和精确查询。