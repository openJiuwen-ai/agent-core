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
> 2. Optionally call `register_message_store(...)` to register a custom message store (if not called, a default `SqlMessageStore` will be created from the registered `db_store`);
> 3. Then call `set_config(MemoryEngineConfig(...))` to set global configuration;
> 4. Optionally configure independent model/vector parameters for different business scenarios through `set_scope_config(scope_id, MemoryScopeConfig(...))`.

```
LongTermMemory()
```

Initialize `LongTermMemory` instance (singleton pattern, multiple calls return the same instance).

**Internal State Initialization**:

- Configuration related: `_sys_mem_config: MemoryEngineConfig | None = None`, `_scope_config: dict[str, MemoryScopeConfig] = {}`;
- Storage related: `kv_store / vector_store / db_store / message_store` are all `None`, need to register through `register_store` (and optionally `register_message_store`);
- Memory index: `memory_index: BaseMemoryIndex | None = None`, can be registered via `register_plugin` with a custom index implementation, or automatically registered as `SimpleMemoryIndex` during `register_store`;
- Manager related: `scope_user_mapping_manager / message_manager / fragment_memory_manager / variable_manager / write_manager / search_manager / generator` are all `None`, initialized during `set_config`;
- LLM related: `_base_llm: Model | None = None` (set during `set_config`);
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
* **embedding_model** (Embedding | None, optional): Global embedding model instance for initializing the vector index embedding capability during registration. If `None`, independent embedding models can be configured for different scopes later through `set_scope_config`. Default value: `None`.

**Behavior**:

When both `vector_store` and `embedding_model` are provided, `register_store` automatically calls `register_plugin` to register the default `SimpleMemoryIndex` as `memory_index`. To use a custom `BaseMemoryIndex` implementation, call `register_plugin` manually after `register_store`.

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


### async register_plugin

```
async def register_plugin(
    self,
    name: str,
    cls: type,
    params: dict[str, Any],
) -> None
```

Register a custom `BaseMemoryIndex` plugin instance, used to replace or extend the default vector index implementation.

**Parameters**:

* **name** (str): Plugin name, describing the plugin type (e.g., `'vector'`, `'inverted'`, `'hybrid'`).
* **cls** (type): Plugin class, must inherit from `BaseMemoryIndex`.
* **params** (dict[str, Any]): Initialization parameters passed to the plugin class constructor.

**Behavior**:

- This method instantiates the plugin via `cls(**params)`;
- The **first registered** plugin becomes the default `memory_index` (`self.memory_index`); subsequent registrations do not overwrite the default;
- If `register_store` has already auto-registered `SimpleMemoryIndex`, subsequent manual calls to `register_plugin` will not override the existing default index.

**Prerequisites**:

- No strict prerequisites, but recommended to call after `register_store` and before `set_config`.

**Example**:

```python
>>> from openjiuwen.core.memory.long_term_memory import LongTermMemory
>>> from openjiuwen.core.foundation.store.index.vector_memory_index import VectorMemoryIndex
>>> from openjiuwen.core.foundation.store.base_vector_store import BaseVectorStore
>>> from openjiuwen.core.foundation.store.base_embedding import Embedding
>>>
>>> # Use default VectorMemoryIndex
>>> memory = LongTermMemory()
>>> await memory.register_plugin(
>>>     name="vector",
>>>     cls=VectorMemoryIndex,
>>>     params={
>>>         "vector_store": my_vector_store,
>>>         "embedding_model": my_embedding_model,
>>>     }
>>> )
>>>
>>> # Or use a custom BaseMemoryIndex implementation
>>> class MyCustomIndex(BaseMemoryIndex):
>>>     ...
>>>
>>> await memory.register_plugin(
>>>     name="custom",
>>>     cls=MyCustomIndex,
>>>     params={"custom_param": "value"}
>>> )
```

> **Note**: Custom `BaseMemoryIndex` subclasses must implement the `set_storage_codec(codec)` abstract method to receive an `AesStorageCodec` instance. When `crypto_key` is non-empty during `set_config`, the codec is automatically injected; subclasses call `codec.encode()` on the `text` field before writing and `codec.decode()` after reading to achieve transparent encryption/decryption. See `BaseMemoryIndex` for reference.


### register_message_store

```
def register_message_store(self, message_store: BaseMessageStore) -> None
```

Register a custom `BaseMessageStore` implementation. This allows external code to provide a custom message store (e.g., using a different database backend) instead of the default `SqlMessageStore`.

Must be called before `set_config()`. If not called, `LongTermMemory` will create a default `SqlMessageStore` from the registered `db_store`.

**Parameters**:

* **message_store** (BaseMessageStore): A `BaseMessageStore` implementation instance.

**Exceptions**:

* **build_error**: Raised when `message_store` is not a `BaseMessageStore` instance (`MEMORY_REGISTER_STORE_EXECUTION_ERROR`).

**Example**:

```python
>>> from openjiuwen.core.memory.long_term_memory import LongTermMemory
>>> from openjiuwen.core.foundation.store.base_message_store import BaseMessageStore
>>> from openjiuwen.core.memory.manage.mem_model.sql_message_store import SqlMessageStore
>>> from openjiuwen.core.memory.manage.mem_model.sql_db_store import SqlDbStore
>>>
>>> # Create a custom message store
>>> sql_db_store = SqlDbStore(db_store)
>>> custom_message_store = SqlMessageStore(
>>>     crypto_key=b"your-32-byte-aes-key-here!!",
>>>     sql_db_store=sql_db_store,
>>>     table_name="custom_messages"
>>> )
>>>
>>> # Register the custom message store
>>> memory = LongTermMemory()
>>> memory.register_message_store(custom_message_store)
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
  * `forbidden_variables: str`: Forbidden variables (e.g., "user_phone") that cannot be stored. Default: `""` (no forbidden variables).
  * `input_msg_max_len: int`: Maximum input message length (default 8192);
  * `crypto_key: bytes`: AES encryption key (must be 32 bytes long; empty means no encryption).

**Prerequisites**:

- Must have called `register_store` to register `kv_store` and `db_store`, otherwise will raise `build_error` (`MEMORY_SET_CONFIG_EXECUTION_ERROR`).
- Must have registered `memory_index` (via `register_plugin` or auto-registered by `register_store`), otherwise will raise `build_error`.

**Behavior**:

- Managers (`FragmentMemoryManager`, `SummaryManager`, `WriteManager`) uniformly use `memory_index` (`BaseMemoryIndex`) as the backend. The `UserMemStore` fallback path is no longer supported.
- If `crypto_key` is non-empty, an `AesStorageCodec` is automatically created and injected via `memory_index.set_storage_codec()`, enabling transparent AES-256-GCM encryption/decryption of the memory content `text` field at the storage layer.

**Exceptions**:

* **build_error**: Raised when `register_store` has not been called or configuration is invalid.

**Internal Managers Initialized**:

This method initializes the following internal managers:

* `scope_user_mapping_manager`: Manages the mapping between scopes and users;
* `message_manager`: Handles message storage and retrieval operations. Initialized in two ways:
  - If a custom `message_store` was registered via `register_message_store()` before calling `set_config()`, it uses the registered `message_store`;
  - Otherwise, creates a default `SqlMessageStore` using the registered `db_store`, with `crypto_key` from config and table name `"user_message"`;
* `fragment_memory_manager`: Manages user profile, episodic memory, and semantic memory;
* `variable_manager`: Manages user variable storage and retrieval;
* `summary_manager`: Manages user summary memory;
* `write_manager`: Coordinates write operations across all memory types;
* `search_manager`: Handles search queries across all memory types;
* `generator`: Generates memory content from messages using LLM;
* `_base_llm`: Base large language model instance (initialized if `default_model_cfg` and `default_model_client_cfg` are provided).

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
>>>     forbidden_variables="user_id, phone_number, email",
>>>     input_msg_max_len=8192,
>>>     crypto_key=b"your-32-byte-aes-key-here!!",
>>> )
>>> 
>>> # Set configuration
>>> memory = LongTermMemory()
>>> memory.set_config(config)
```


### async migrate_between_indices

```
async def migrate_between_indices(
    source_index: BaseMemoryIndex,
    target_index: BaseMemoryIndex,
) -> None
```

Copy data from one `BaseMemoryIndex` to another. Suitable for data migration between different index implementations (e.g., from `SimpleMemoryIndex` to `VectorMemoryIndex`). Source data is preserved after migration.

**Parameters**:

* **source_index** (BaseMemoryIndex): Source `BaseMemoryIndex` instance to read data from.
* **target_index** (BaseMemoryIndex): Target `BaseMemoryIndex` instance to write data into.

**Behavior**:

- This method iterates through all `(user_id, scope_id)` combinations in `source_index`, reads documents in batches (100 per batch), and writes them to `target_index`;
- Source data remains unchanged after migration;
- Migration is idempotent — if a document with the same ID already exists in the target index, it will be overwritten (upsert semantics).

**Example**:

```python
>>> from openjiuwen.core.memory.long_term_memory import LongTermMemory
>>> from openjiuwen.core.foundation.store.index.simple_memory_index import SimpleMemoryIndex
>>>
>>> # Assume an existing SimpleMemoryIndex instance
>>> old_index = SimpleMemoryIndex(kv_store=kv_store, vector_store=vector_store, embedding_model=embed)
>>> new_index = VectorMemoryIndex(...)
>>>
>>> # Migrate data from old index to new index
>>> await LongTermMemory.migrate_between_indices(source_index=old_index, target_index=new_index)
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
) -> AddMemResult
```

Add conversation messages to the memory engine and generate memories (user profiles, variables, etc.) according to `agent_config`. Also supports **instructive memory** functionality: when users include explicit memory instructions in the conversation (e.g., "remember...", "change... to...", "delete..."), the engine automatically recognizes and performs the corresponding add, update, or delete operations.

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

**Returns**:

* **AddMemResult**: The memory extraction result for this call, containing the following fields:
  * `variables: list[VariableUnit]`: List of extracted variable memories;
  * `user_profile: list[FragmentMemoryUnit]`: List of extracted user profile memories;
  * `semantic_memory: list[FragmentMemoryUnit]`: List of extracted semantic memories;
  * `episodic_memory: list[FragmentMemoryUnit]`: List of extracted episodic memories;
  * `summary: list[SummaryUnit]`: List of extracted summary memories.

When `gen_mem=False`, `scope_id` format is invalid, LLM is not initialized, or no user messages are present, returns an empty `AddMemResult()` (all fields are empty lists).

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

**Instructive Memory Example**:

```python
>>> # User modifies existing memory through explicit instruction
>>> update_messages = [
>>>     UserMessage(content="Change my age to 30"),
>>>     AssistantMessage(content="Okay, I've updated your age information.")
>>> ]
>>> result = await memory.add_messages(
>>>     messages=update_messages,
>>>     agent_config=agent_config,
>>>     user_id="user123",
>>>     scope_id="my_scope",
>>> )
>>> # result.user_profile will contain FragmentMemoryUnit with operation_type=UPDATE
>>> 
>>> # User deletes existing memory through explicit instruction
>>> delete_messages = [
>>>     UserMessage(content="Delete my age information"),
>>>     AssistantMessage(content="Okay, I've deleted your age information.")
>>> ]
>>> result = await memory.add_messages(
>>>     messages=delete_messages,
>>>     agent_config=agent_config,
>>>     user_id="user123",
>>>     scope_id="my_scope",
>>> )
>>> # result.user_profile will contain FragmentMemoryUnit with operation_type=DELETE
```


## class openjiuwen.core.memory.long_term_memory.AddMemResult

```
class openjiuwen.core.memory.long_term_memory.AddMemResult(BaseModel)
```

Return value model for the `add_messages` method, encapsulating all memory extraction results for this call.

**Fields**:

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `variables` | `list[VariableUnit]` | `[]` | List of extracted variable memories |
| `user_profile` | `list[FragmentMemoryUnit]` | `[]` | List of extracted user profile memories |
| `semantic_memory` | `list[FragmentMemoryUnit]` | `[]` | List of extracted semantic memories |
| `episodic_memory` | `list[FragmentMemoryUnit]` | `[]` | List of extracted episodic memories |
| `summary` | `list[SummaryUnit]` | `[]` | List of extracted summary memories |

**Notes**:

- Each `FragmentMemoryUnit` contains an `operation_type` field (`ADD` / `UPDATE` / `DELETE`) to distinguish the operation type.
- Instructive memory UPDATE and DELETE operations are represented as `FragmentMemoryUnit` with the corresponding `operation_type` in the return result.
- When `add_messages` does not perform memory extraction for any reason (`gen_mem=False`, invalid `scope_id`, LLM not initialized, etc.), returns an empty `AddMemResult()`.


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
  * `mem_info: MemInfo` (`mem_id / content / type / timestamp`);
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
  * `mem_info: MemInfo` (`mem_id / content / type / timestamp`);
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