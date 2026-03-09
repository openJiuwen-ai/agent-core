# openjiuwen.core.memory.long_term_memory

`openjiuwen.core.memory.long_term_memory` is the unified **long-term memory management engine** in openJiuwen, responsible for:

- Managing persistence and retrieval of user conversation messages;
- Managing user variable memories (such as preferences, personal information, and other structured data);
- Managing user profiles (long-term memories, extracted from conversations through LLM);
- Supporting multi-tenant isolation based on `scope_id`;
- Supporting vector retrieval, paginated queries, conditional deletion, and other operations.


## class openjiuwen.core.memory.long_term_memory.LongTermMemory

```
class openjiuwen.core.memory.long_term_memory.LongTermMemory(metaclass=Singleton)
```

`LongTermMemory` is the unified **long-term memory management engine** in openJiuwen 0.1.4, using singleton pattern.

> **Note**: Unlike the legacy `MemoryEngine(config: SysMemConfig, ...)`, `LongTermMemory` uses a **parameterless constructor + step-by-step initialization** approach:
> 1. First call `await register_store(...)` to register underlying storage;
> 2. Then call `set_config(MemoryEngineConfig(...))` to set global configuration;
> 3. Optionally configure independent model/vector parameters for different business scenarios through `set_scope_config(scope_id, MemoryScopeConfig(...))`.

```
LongTermMemory()
```

Initialize `LongTermMemory` instance (singleton pattern, multiple calls return the same instance).

**Internal State Initialization**:

- Configuration related: `_sys_mem_config: MemoryEngineConfig | None = None`, `_scope_config: dict[str, MemoryScopeConfig] = {}`;
- Storage related: `kv_store / semantic_store / db_store` are all `None`, need to register through `register_store`;
- Manager related: `scope_user_mapping_manager / message_manager / user_profile_manager / variable_manager / write_manager / search_manager / generator` are all `None`, initialized during `set_config`;
- LLM related: `_base_llm: Tuple[str, Model] | None = None` (set during `set_config`);
- Embedding model cache: `_scope_embedding: dict[str, Embedding] = {}`.


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

Register underlying storage instances, must be completed before calling `set_config`.

**Parameters**:

* **kv_store** (BaseKVStore): **Required**, key-value storage instance for fast access to structured data (such as scope configuration, user variables, etc.). If `None`, will raise `build_error` (`MEMORY_REGISTER_STORE_EXECUTION_ERROR`).
* **vector_store** (BaseVectorStore | None, optional): Vector storage instance for semantic similarity retrieval. If `None`, semantic retrieval functionality is unavailable. Default value: `None`.
* **db_store** (BaseDbStore | None, optional): Relational database storage instance for persisting messages, scope-user mappings, etc. If `None`, message persistence functionality is unavailable. Default value: `None`.
* **embedding_model** (Embedding | None, optional): Global embedding model instance for initializing `semantic_store` embedding capability during registration. If `None`, independent embedding models can be configured for different scopes later through `set_scope_config`. Default value: `None`.

**Exceptions**:

* **build_error**: Raised when `kv_store` is `None` or storage types do not match.

**Example**:

```python
>>> from openjiuwen.core.memory.long_term_memory import LongTermMemory
>>> from openjiuwen.core.foundation.store.kv.db_based_kv_store import DbBasedKVStore
>>> from openjiuwen.core.foundation.store import create_vector_store
>>> from openjiuwen.core.foundation.store.db.default_db_store import DefaultDbStore
>>> from sqlalchemy.ext.asyncio import create_async_engine
>>>
>>> # Create LongTermMemory instance
>>> engine = LongTermMemory()
>>>
>>> project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
>>> resource_dir = os.path.join(project_root, "resources")
>>> os.makedirs(resource_dir, exist_ok=True)
>>> kv_path = os.path.join(resource_dir, "kv_store.db")
>>> # Before using sqlite, you need to install the aiosqlite dependency package. Currently, aiosqlite is an optional dependency
>>> engine = create_async_engine(
>>>     f"sqlite+aiosqlite:///{kv_path}",
>>>     pool_pre_ping=True,
>>>     echo=False,
>>> )
>>> # ---------- KV Store ----------
>>> kv_store = DbBasedKVStore(engine)
>>>
>>> # ---------- Vector Store ----------
>>> # Use Chroma vector storage, the chromadb dependency package must be installed. Currently, chromadb is an optional dependency
>>> vector_store = create_vector_store("chroma", persist_directory="./resources/chroma")
>>> # Or use Milvus vector storage
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
>>> # ---------- Register Storage ----------
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

Set global memory engine configuration and initialize internal managers.

**Parameters**:

* **config** (MemoryEngineConfig): Global engine configuration, containing:
  * `default_model_cfg: ModelRequestConfig`: Default large model request parameters for generating memories;
  * `default_model_client_cfg: ModelClientConfig`: Default large model client configuration;
  * `input_msg_max_len: int`: Maximum input message length (default 8192);
  * `crypto_key: bytes`: AES encryption key (must be 32 bytes long; empty means no encryption).

**Prerequisites**:

- Must have called `register_store` to register `kv_store`, `semantic_store`, `db_store`, otherwise will raise `build_error` (`MEMORY_SET_CONFIG_EXECUTION_ERROR`).

**Exceptions**:

* **build_error**: Raised when `register_store` has not been called or configuration is invalid.

**Example**:

```python
>>> from openjiuwen.core.memory.long_term_memory import LongTermMemory
>>> from openjiuwen.core.memory.config import MemoryEngineConfig
>>> from openjiuwen.core.foundation.llm.schema.config import ModelRequestConfig, ModelClientConfig
>>> 
>>> # Create configuration
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
>>> # Set configuration
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

Set scope-level memory configuration for the specified `scope_id` and persist it to `kv_store`.

**Parameters**:

* **scope_id** (str): Scope identifier, cannot contain `/`, length cannot exceed 128 characters; if format is invalid, returns `False` and logs error.
* **memory_scope_config** (MemoryScopeConfig): Scope configuration, containing:
  * `model_cfg: ModelRequestConfig | None`: Large model request configuration used under this scope;
  * `model_client_cfg: ModelClientConfig | None`: Large model client configuration used under this scope;
  * `embedding_cfg: EmbeddingConfig | None`: Embedding model configuration used under this scope.

**Returns**:

* **bool**: Returns `True` on success, returns `False` if `scope_id` format is invalid.

**Example**:

```python
>>> from openjiuwen.core.memory.long_term_memory import LongTermMemory
>>> from openjiuwen.core.memory.config import MemoryScopeConfig
>>> from openjiuwen.core.foundation.llm.schema.config import ModelRequestConfig, ModelClientConfig
>>> from openjiuwen.core.retrieval.common.config import EmbeddingConfig
>>> 
>>> # Create scope configuration
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
>>> # Set scope configuration
>>> memory = LongTermMemory()
>>> success = await memory.set_scope_config("my_scope", scope_config)
>>> print(f"Setup result: {success}")
```


### async get_scope_config

```
async def get_scope_config(self, scope_id: str) -> MemoryScopeConfig | None
```

Read scope configuration for the specified `scope_id` from `kv_store` and decrypt API key.

**Parameters**:

* **scope_id** (str): Scope identifier.

**Returns**:

* **MemoryScopeConfig | None**: If configuration exists, returns decrypted configuration object; if it doesn't exist or `scope_id` format is invalid, returns `None`.

**Example**:

```python
>>> from openjiuwen.core.memory.long_term_memory import LongTermMemory
>>> 
>>> # Get scope configuration
>>> memory = LongTermMemory()
>>> scope_config = await memory.get_scope_config("my_scope")
>>> 
>>> if scope_config:
>>>     print(f"Model config: {scope_config.model_cfg}")
>>>     print(f"Client config: {scope_config.model_client_cfg}")
>>> else:
>>>     print("Scope configuration not found")
```


### async delete_scope_config

```
async def delete_scope_config(self, scope_id: str) -> bool
```

Delete scope configuration for the specified `scope_id` (remove from `kv_store` and memory cache).

**Parameters**:

* **scope_id** (str): Scope identifier.

**Returns**:

* **bool**: Returns `True` on successful deletion, returns `False` if `scope_id` format is invalid or deletion fails.

**Example**:

```python
>>> from openjiuwen.core.memory.long_term_memory import LongTermMemory
>>> 
>>> # Delete scope configuration
>>> memory = LongTermMemory()
>>> success = await memory.delete_scope_config("my_scope")
>>> print(f"Deletion result: {success}")
```


### async delete_mem_by_scope

```
async def delete_mem_by_scope(self, scope_id: str) -> bool
```

Delete all memory data under the specified `scope_id` (including messages, user profiles, variables, etc.).

**Parameters**:

* **scope_id** (str): Scope identifier.

**Returns**:

* **bool**: Returns `True` on successful deletion, returns `False` if `scope_id` format is invalid.

**Example**:

```python
>>> from openjiuwen.core.memory.long_term_memory import LongTermMemory
>>> 
>>> # Delete all memories under scope
>>> memory = LongTermMemory()
>>> success = await memory.delete_mem_by_scope("my_scope")
>>> print(f"Deletion result: {success}")
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

Add conversation messages to the memory engine and generate memories (user profiles, variables, etc.) according to `agent_config`.

**Parameters**:

* **messages** (list[BaseMessage]): List of messages to add (usually containing user messages and AI replies).
* **agent_config** (AgentMemoryConfig): Agent memory strategy configuration, containing:
  * `mem_variables: list[Param]`: Variable memory configurations to extract (variable name, description, type, etc.);
  * `enable_long_term_mem: bool`: Whether to enable long-term memory generation (default `True`).
  * `enable_user_profile: bool`: Whether to enable user profile generation and use (default `True`).
  * `enable_semantic_memory: bool`: Whether to enable semantic memory generation and use (default `True`).
  * `enable_episodic_memory: bool`: Whether to enable episodic memory generation and use (default `True`).
  * `enable_summary_memory: bool`: Whether to enable user summary memory generation (default `True`).
* **user_id** (str, optional): User identifier. Default value: `"__default__"`.
* **scope_id** (str, optional): Scope identifier; returns directly without exception if format is invalid. Default value: `"__default__"`.
* **session_id** (str, optional): Session identifier. Default value: `"__default__"`.
* **timestamp** (datetime | None, optional): Message timestamp, if `None` uses current UTC time. Default value: `None`.
* **gen_mem** (bool, optional): Whether to generate memories; when `False`, only saves messages without triggering memory extraction. Default value: `True`.
* **gen_mem_with_history_msg_num** (int, optional): Number of historical messages to reference when generating memories. Default value: 5.

**Exceptions**:

* **build_error**: Raised when writing memory fails (`MEMORY_ADD_MEMORY_EXECUTION_ERROR`).

**Example**:

```python
>>> from openjiuwen.core.memory.long_term_memory import LongTermMemory
>>> from openjiuwen.core.memory.config import AgentMemoryConfig
>>> from openjiuwen.core.common.schema.param import Param
>>> from openjiuwen.core.foundation.llm.schema.message import UserMessage, AssistantMessage
>>> 
>>> # Create Agent memory strategy configuration
>>> agent_config = AgentMemoryConfig(
>>>     mem_variables=[
>>>         Param(
>>>             name="favorite_color",
>>>             description="User's favorite color",
>>>             type="string",
>>>             required=False,
>>>         ),
>>>         Param(
>>>             name="age",
>>>             description="User's age",
>>>             type="number",
>>>             required=False,
>>>         ),
>>>     ],
>>>     enable_long_term_mem=True,
>>>     enable_user_profile=True,
>>>     enable_semantic_memory=True,
>>>     enable_episodic_memory=True,
>>>     enable_summary_memory=True,
>>> )
>>> 
>>> # Prepare messages
>>> messages = [
>>>     UserMessage(content="I like blue, I'm 25 years old"),
>>>     AssistantMessage(content="Okay, I've remembered that you like blue and are 25 years old.")
>>> ]
>>> 
>>> # Add messages
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

Get the most recent N messages for the specified user/scope/session, returned in write order.

**Parameters**:

* **user_id** (str, optional): User identifier. Default value: `"__default__"`.
* **scope_id** (str, optional): Scope identifier; returns empty list if format is invalid. Default value: `"__default__"`.
* **session_id** (str, optional): Session identifier. Default value: `"__default__"`.
* **num** (int, optional): Number of messages to retrieve. Default value: 10.

**Returns**:

* **list[BaseMessage]**: Message list, sorted by write time; returns empty list if `scope_id` format is invalid or `message_manager` is not initialized.

**Example**:

```python
>>> from openjiuwen.core.memory.long_term_memory import LongTermMemory
>>> 
>>> # Get recent messages
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

Get a single message and its creation timestamp by message id.

**Parameters**:

* **msg_id** (str): Message unique identifier.

**Returns**:

* **Tuple[BaseMessage, datetime] | None**: If message exists, returns `(message object, creation time)`; if `message_manager` is not initialized or message doesn't exist, returns `None`.

**Example**:

```python
>>> from openjiuwen.core.memory.long_term_memory import LongTermMemory
>>> 
>>> # Get message by ID
>>> memory = LongTermMemory()
>>> result = await memory.get_message_by_id("msg_12345")
>>> 
>>> if result:
>>>     message, timestamp = result
>>>     print(f"Message content: {message.content}")
>>>     print(f"Creation time: {timestamp}")
>>> else:
>>>     print("Message not found")
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

Delete a memory entry (user profile or variable) by specified id.

**Parameters**:

* **mem_id** (str): Memory unique identifier.
* **user_id** (str, optional): User identifier. Default value: `"__default__"`.
* **scope_id** (str, optional): Scope identifier; returns directly if format is invalid. Default value: `"__default__"`.

**Exceptions**:

* **build_error**: Raised when `write_manager` is not initialized (`MEMORY_DELETE_MEMORY_EXECUTION_ERROR`).

**Example**:

```python
>>> from openjiuwen.core.memory.long_term_memory import LongTermMemory
>>> 
>>> # Delete specified memory
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

Delete all types of memories (user profiles, variables, etc.) for the specified user under a scope.

**Parameters**:

* **user_id** (str, optional): User identifier. Default value: `"__default__"`.
* **scope_id** (str, optional): Scope identifier; returns directly if format is invalid. Default value: `"__default__"`.

**Exceptions**:

* **build_error**: Raised when `write_manager` is not initialized.

**Example**:

```python
>>> from openjiuwen.core.memory.long_term_memory import LongTermMemory
>>> 
>>> # Delete all user memories
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

Update memory content by specified id.

**Parameters**:

* **mem_id** (str): Memory unique identifier.
* **memory** (str): New memory content.
* **user_id** (str, optional): User identifier. Default value: `"__default__"`.
* **scope_id** (str, optional): Scope identifier; returns directly if format is invalid. Default value: `"__default__"`.

**Exceptions**:

* **build_error**: Raised when `write_manager` is not initialized (`MEMORY_UPDATE_MEMORY_EXECUTION_ERROR`).

**Example**:

```python
>>> from openjiuwen.core.memory.long_term_memory import LongTermMemory
>>> 
>>> # Update memory content
>>> memory = LongTermMemory()
>>> await memory.update_mem_by_id(
>>>     mem_id="mem_12345",
>>>     memory="Updated memory content",
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

Get user variables (one or more).

**Parameters**:

* **names** (list[str] | str | None, optional):
  * If `None`: Returns all variables for this user under this scope;
  * If `str`: Returns a single variable (`{name: value}`);
  * If `list[str]`: Returns multiple variables (`{name1: value1, name2: value2, ...}`).
  Default value: `None`.
* **user_id** (str, optional): User identifier. Default value: `"__default__"`.
* **scope_id** (str, optional): Scope identifier; returns empty dictionary if format is invalid. Default value: `"__default__"`.

**Returns**:

* **dict[str, str]**: Mapping from variable names to variable values; returns empty dictionary or raises exception if `scope_id` format is invalid or `search_manager` is not initialized.

**Exceptions**:

* **build_error**: Raised when `search_manager` is not initialized or `names` type does not meet expectations (`MEMORY_GET_MEMORY_EXECUTION_ERROR`).

**Example**:

```python
>>> from openjiuwen.core.memory.long_term_memory import LongTermMemory
>>> 
>>> # Get all variables
>>> memory = LongTermMemory()
>>> all_vars = await memory.get_variables(
>>>     user_id="user123",
>>>     scope_id="my_scope"
>>> )
>>> print(f"All variables: {all_vars}")
>>> 
>>> # Get single variable
>>> favorite_color = await memory.get_variables(
>>>     names="favorite_color",
>>>     user_id="user123",
>>>     scope_id="my_scope"
>>> )
>>> print(f"Favorite color: {favorite_color}")
>>> 
>>> # Get multiple variables
>>> some_vars = await memory.get_variables(
>>>     names=["favorite_color", "age"],
>>>     user_id="user123",
>>>     scope_id="my_scope"
>>> )
>>> print(f"Some variables: {some_vars}")
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

Search user memories (user profiles, variables, etc.) based on semantic similarity, returning the N most relevant memories to the query.

**Parameters**:

* **query** (str): Query text.
* **num** (int): Number of memories to return (top-k).
* **user_id** (str, optional): User identifier. Default value: `"__default__"`.
* **scope_id** (str, optional): Scope identifier; returns empty list if format is invalid. Default value: `"__default__"`.
* **threshold** (float, optional): Similarity threshold, memories below this threshold will be filtered. Default value: 0.3.

**Returns**:

* **list[MemResult]**: Memory result list, each `MemResult` contains:
  * `mem_info: MemInfo` (`mem_id / content / type`);
  * `score: float` (similarity score).

**Exceptions**:

* **build_error**: Raised when `search_manager` is not initialized.

**Example**:

```python
>>> from openjiuwen.core.memory.long_term_memory import LongTermMemory
>>> 
>>> # Search user memories
>>> memory = LongTermMemory()
>>> results = await memory.search_user_mem(
>>>     query="User's interests and hobbies",
>>>     num=5,
>>>     user_id="user123",
>>>     scope_id="my_scope",
>>>     threshold=0.4
>>> )
>>> 
>>> for result in results:
>>>     print(f"Content: {result.mem_info.content}")
>>>     print(f"Similarity: {result.score}")
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

Return the total number of memories for the specified user under a scope.

**Parameters**:

* **user_id** (str, optional): User identifier. Default value: `"__default__"`.
* **scope_id** (str, optional): Scope identifier; returns 0 if format is invalid. Default value: `"__default__"`.

**Returns**:

* **int**: Total number of memories; returns 0 if `scope_id` format is invalid.

**Example**:

```python
>>> from openjiuwen.core.memory.long_term_memory import LongTermMemory
>>> 
>>> # Get total number of memories
>>> memory = LongTermMemory()
>>> total = await memory.user_mem_total_num(
>>>     user_id="user123",
>>>     scope_id="my_scope"
>>> )
>>> print(f"Total memories: {total}")
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

Search user summary memories based on semantic similarity, returning the N most relevant summary memories to the query.

**Parameters**:

* **query** (str): Search query string.
* **num** (int): Number of results to return (top-k).
* **user_id** (str, optional): User identifier. Default value: `"__default__"`.
* **scope_id** (str, optional): Scope identifier; returns empty list if format is invalid. Default value: `"__default__"`.
* **threshold** (float, optional): Minimum similarity threshold for results; memories below this threshold will be filtered. Default value: 0.3.

**Returns**:

* **list[MemResult]**: List of memory results, each `MemResult` contains:
  * `mem_info: MemInfo` (`mem_id / content / type`);
  * `score: float` (similarity score).

**Exceptions**:

* **build_error**: Raised when `search_manager` is not initialized.

**Example**:

```python
>>> from openjiuwen.core.memory.long_term_memory import LongTermMemory
>>> 
>>> # Search user summary memories
>>> memory = LongTermMemory()
>>> results = await memory.search_user_history_summary(
>>>     query="Recent conversations about work",
>>>     num=5,
>>>     user_id="user123",
>>>     scope_id="my_scope",
>>>     threshold=0.4
>>> )
>>> 
>>> for result in results:
>>>     print(f"Content: {result.mem_info.content}")
>>>     print(f"Similarity: {result.score}")
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

Get memories for the specified user under a scope in pages.

**Parameters**:

* **user_id** (str, optional): User identifier. Default value: `"__default__"`.
* **scope_id** (str, optional): Scope identifier; returns empty dictionary if format is invalid. Default value: `"__default__"`.
* **page_num** (int, optional): Page number, starting from 1. Default value: 1.
* **page_size** (int, optional): Page size. Default value: 10.

**Returns**:

* **dict[str, Any]**: Contains the following fields:
  * `total: int`: Total number of memories;
  * `page_num: int`: Current page number;
  * `page_size: int`: Page size;
  * `total_pages: int`: Total number of pages;
  * `data: list[MemInfo]`: Memory list for current page.

**Exceptions**:

* **build_error**: Raised when `search_manager` is not initialized (`MEMORY_GET_MEMORY_EXECUTION_ERROR`).

**Example**:

```python
>>> from openjiuwen.core.memory.long_term_memory import LongTermMemory
>>> 
>>> # Get user memories by page
>>> memory = LongTermMemory()
>>> result = await memory.get_user_mem_by_page(
>>>     user_id="user123",
>>>     scope_id="my_scope",
>>>     page_num=2,
>>>     page_size=5
>>> )
>>> 
>>> print(f"Total memories: {result['total']}")
>>> print(f"Current page: {result['page_num']}/{result['total_pages']}")
>>> 
>>> for mem_info in result['data']:
>>>     print(f"ID: {mem_info.mem_id}, Content: {mem_info.content[:50]}...")
```


> **Note**: For all methods, if `user_id`, `scope_id`, `session_id` use the default value `"__default__"`, it means using the system default identifier; in actual business scenarios, it is recommended to pass meaningful business identifiers to support multi-tenant isolation and precise queries.