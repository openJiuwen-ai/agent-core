# Memory Engine (LongTermMemory)

The Memory Engine (`LongTermMemory`) is the unified memory management component provided by openJiuwen in the current version, responsible for managing user conversation messages, variable memories, and long-term user profiles.

Unlike the old documentation's `MemoryEngine/SysMemConfig/MemoryConfig`, **starting from version 0.1.4, memory capabilities are entirely handled by the `LongTermMemory + MemoryEngineConfig + MemoryScopeConfig + AgentMemoryConfig` interface set**, and different business/Agent scenarios are distinguished by `scope_id`.

This chapter is strictly based on the public interfaces under the source code `openjiuwen.core.memory`.

## Core Concepts and Configuration Classes

The core types of the memory engine are defined in the `openjiuwen.core.memory` and `openjiuwen.core.memory.config` modules:

- **`LongTermMemory`** (Memory Engine Core)
  - Responsible for registering underlying storage (KV Store / Vector Store / DB), setting global engine configuration, managing memory configurations for each `scope`, and reading/writing user memories.

- **`MemoryEngineConfig`** (Global Engine Configuration)
  Defines engine-level common configuration:
  - `default_model_cfg: ModelRequestConfig`: Default LLM request parameters for generating memories (model name, temperature, etc.).
  - `default_model_client_cfg: ModelClientConfig`: Default LLM client configuration (`client_provider/api_base/api_key/verify_ssl`, etc.).
  - `forbidden_variables: str`: Forbidden variables (e.g., "user_phone") that cannot be stored. Default: `""` (no forbidden variables).
  - `input_msg_max_len: int`: Maximum length of input messages (default: 8192).
  - `crypto_key: bytes`: AES key for encrypting sensitive fields in storage (must be 32 bytes in length; empty means no encryption).

- **`MemoryScopeConfig`** (Scope-level Configuration)
  Used to define independent model/vector configurations for different `scope_id`:
  - `model_cfg: ModelRequestConfig`: LLM request configuration used in this scope.
  - `model_client_cfg: ModelClientConfig`: LLM client configuration used in this scope.
  - `embedding_cfg: EmbeddingConfig`: Embedding model configuration used in this scope (`model_name/base_url/api_key`).

- **`AgentMemoryConfig`** (Agent-level Memory Strategy Configuration)
  Describes which "variable memories", "long-term memories", "user profile memories", and "user summary memories" an agent wants to extract and manage:
  - `mem_variables: list[Param]`: Variable memory configuration list (each `Param` defines a variable name, description, type, whether it's required, etc.).
  - `enable_long_term_mem: bool`: Whether to enable long-term memory (default: `True`).
  - `enable_user_profile: bool`: Whether to enable user profile memory generation and use (default: `True`).
  - `enable_semantic_memory: bool`: Whether to enable semantic memory generation and use (default: `True`).
  - `enable_episodic_memory: bool`: Whether to enable episodic memory generation and use (default: `True`).
  - `enable_summary_memory: bool`: Whether to enable user summary memory (default: `True`).

> These classes are defined in the source code at:  
> `openjiuwen.core.memory.config.config` and `openjiuwen.core.memory.__init__`, and can be directly imported via `from openjiuwen.core.memory import MemoryEngineConfig, MemoryScopeConfig, AgentMemoryConfig, LongTermMemory`.

## Detailed Configuration for LLM and Embedding Models

The memory engine relies on three core configuration classes to define LLM and embedding model parameters. These configuration classes are defined in the `openjiuwen.core.foundation.llm.schema.config` and `openjiuwen.core.foundation.store.base_embedding` modules.

### ModelRequestConfig (LLM Request Configuration)

`ModelRequestConfig` is used to configure LLM request parameters to control model generation behavior.

| Parameter               | Type         | Required | Default | Description                                                                 |
| ----------------------- | ------------ | -------- | ------- | --------------------------------------------------------------------------- |
| `model` / `model_name`  | str          | No       | `""`    | Model name, e.g., `gpt-4`. Supports alias `model`                           |
| `temperature`           | float        | No       | `0.95`  | Temperature parameter, controls output randomness. Higher values produce more random output, lower values produce more deterministic output. Range: [0, 1] |
| `top_p`                 | float        | No       | `0.1`   | Top-p sampling parameter, controls nucleus sampling range. Range: [0, 1]    |
| `max_tokens`            | int \| None  | No       | `None`  | Maximum number of tokens to generate                                        |
| `stop`                  | str \| None  | No       | `None`  | Stop sequence, model stops generating when encountering this sequence       |

**Usage Example:**

```python
from openjiuwen.core.foundation.llm.schema.config import ModelRequestConfig

model_cfg = ModelRequestConfig(
    model="gpt-4",
    temperature=0.0
)
```

### ModelClientConfig (LLM Client Configuration)

`ModelClientConfig` is used to configure LLM client connection parameters, including API keys, service addresses, etc.

| Parameter         | Type                  | Required | Default | Description                                                                  |
| ----------------- | --------------------- | -------- | ------- | ---------------------------------------------------------------------------- |
| `client_id`       | str                   | No       | UUID    | Client unique identifier, used for registration in Runner                    |
| `client_provider` | ProviderType \| str   | Yes      | -       | Service provider identifier, supports enum values: `OpenAI`, `OpenRouter`, `SiliconFlow`, `DashScope` |
| `api_key`         | str                   | Yes      | -       | API key                                                                      |
| `api_base`        | str                   | Yes      | -       | API base URL address                                                         |
| `timeout`         | float                 | No       | `60.0`  | Request timeout (seconds)                                                    |
| `max_retries`     | int                   | No       | `3`     | Maximum number of retries                                                    |
| `verify_ssl`      | bool                  | No       | `True`  | Whether to verify SSL certificate                                            |
| `ssl_cert`        | str \| None           | No       | `None`  | SSL certificate file path                                                    |

**Supported ProviderType Enum Values:**

```python
class ProviderType(str, Enum):
    OpenAI = "OpenAI"           # OpenAI official API
    OpenRouter = "OpenRouter"   # OpenRouter aggregation service
    SiliconFlow = "SiliconFlow" # SiliconFlow platform
    DashScope = "DashScope"     # Alibaba Cloud DashScope platform
```

**Usage Example:**

```python
from openjiuwen.core.foundation.llm.schema.config import ModelClientConfig, ProviderType

client_cfg = ModelClientConfig(
    client_id="my_memory_client",
    client_provider=ProviderType.OpenAI,      
    api_key="sk-xxxxxxxx",
    api_base="https://api.openai.com/v1",
    timeout=120.0,                             
    max_retries=3,
    verify_ssl=False,
)
```

**Parameter Constraints:**

- `client_provider`: Must be a registered service provider, otherwise `MODEL_PROVIDER_INVALID` error will be raised
- `api_key` and `api_base`: Required parameters, cannot be empty

### EmbeddingConfig (Embedding Model Configuration)

`EmbeddingConfig` is used to configure embedding model parameters for vector retrieval and semantic search.

| Parameter    | Type          | Required | Default | Description                                      |
| ------------ | ------------- | -------- | ------- | ------------------------------------------------ |
| `model_name` | str           | Yes      | -       | Embedding model name                             |
| `base_url`   | str           | Yes      | -       | API base URL address                             |
| `api_key`    | str \| None   | No       | `None`  | API key (some services may not require it)       |

**Usage Example:**

```python
from openjiuwen.core.foundation.store.base_embedding import EmbeddingConfig

embedding_cfg = EmbeddingConfig(
    model_name="text-embedding-3",       # Embedding model name
    base_url="https://api.openai.com/v1/embeddings",      # API address
    api_key="sk-xxxxxxxx",                     # API key
)
```

### Configuration Class Relationship Diagram

```
MemoryEngineConfig (Global Engine Configuration)
├── default_model_cfg: ModelRequestConfig      # Default model request configuration
└── default_model_client_cfg: ModelClientConfig # Default model client configuration

MemoryScopeConfig (Scope Configuration)
├── model_cfg: ModelRequestConfig              # Scope model request configuration
├── model_client_cfg: ModelClientConfig        # Scope model client configuration
└── embedding_cfg: EmbeddingConfig             # Scope embedding model configuration
```

> **Configuration Priority**: When both global configuration and scope configuration are set, scope configuration takes precedence. That is, configuration in `MemoryScopeConfig` overrides default configuration in `MemoryEngineConfig`.

## Creating a Memory Engine Instance

The following example demonstrates how to create a complete and usable memory engine based on `LongTermMemory`, including:

1. Registering KV store, vector store, and relational database store;
2. Configuring global engine parameters;
3. Creating and returning a reusable `LongTermMemory` instance.

```python
import os
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine

from openjiuwen.core.memory import (
    LongTermMemory,
    MemoryEngineConfig,
    MemoryScopeConfig,
    AgentMemoryConfig,
)
from openjiuwen.core.foundation.store import create_vector_store
from openjiuwen.core.foundation.store.kv.in_memory_kv_store import InMemoryKVStore
from openjiuwen.core.foundation.store.db.default_db_store import DefaultDbStore
from openjiuwen.core.foundation.llm.schema.config import ModelClientConfig, ModelRequestConfig
from openjiuwen.core.common.schema.param import Param
from openjiuwen.core.retrieval.common.config import EmbeddingConfig
from openjiuwen.core.foundation.llm.schema.message import BaseMessage


async def create_memory_engine() -> LongTermMemory:
    """Initialize and return a LongTermMemory instance"""
    engine = LongTermMemory()

    # 1. Create underlying storage
    kv_store = InMemoryKVStore()

    # Vector store (created using create_vector_store)
    # Milvus example
    vector_store = create_vector_store(
        "milvus",
        milvus_uri=os.getenv("MILVUS_URI", f"http://{os.getenv('MILVUS_HOST', 'localhost')}:{os.getenv('MILVUS_PORT', '19530')}"),
        milvus_token=os.getenv("MILVUS_TOKEN"),
    )

    # Relational database store (based on SQLAlchemy AsyncEngine + DefaultDbStore)
    db_user = os.getenv("DB_USER", "user")
    db_password = os.getenv("DB_PASSWORD", "password")
    db_host = os.getenv("DB_HOST", "127.0.0.1")
    db_port = os.getenv("DB_PORT", "3306")
    agent_db_name = os.getenv("AGENT_DB_NAME", "agent_memory")

    async_engine = create_async_engine(
        f"mysql+aiomysql://{db_user}:{db_password}@{db_host}:{db_port}/{agent_db_name}?charset=utf8mb4",
        pool_size=20,
        max_overflow=20,
    )
    db_store = DefaultDbStore(async_engine)

    # Register storage
    await engine.register_store(
        kv_store=kv_store,
        vector_store=vector_store,
        db_store=db_store,
    )

    # 2. Set global engine configuration (default LLM + encryption configuration)
    default_model_cfg = ModelRequestConfig(
        model=os.getenv("MEMORY_MODEL_NAME", "<model_name>"),
        temperature=0.0,
    )
    default_model_client_cfg = ModelClientConfig(
        client_id="default_memory_llm",
        client_provider=os.getenv("MEMORY_MODEL_PROVIDER", "OpenAI"),
        api_key=os.getenv("MEMORY_MODEL_API_KEY", "sk-xxxx"),
        api_base=os.getenv("MEMORY_MODEL_API_BASE", "https://api.openai.com/v1"),
        verify_ssl=False,
    )

    # crypto_key must be 32 bytes; empty means encryption is not enabled
    crypto_key_env = os.getenv("SERVER_AES_MASTER_KEY_ENV", "")
    crypto_key = crypto_key_env.encode("utf-8")[:32].ljust(32, b"\0") if crypto_key_env else b""

    engine_config = MemoryEngineConfig(
        default_model_cfg=default_model_cfg,
        default_model_client_cfg=default_model_client_cfg,
        crypto_key=crypto_key,
    )
    engine.set_config(engine_config)

    return engine
```

> Note: Unlike the old `MemoryEngine.register_store(...).create_mem_engine_instance(SysMemConfig)`, `LongTermMemory` initialization is divided into two steps:  
> 1) `await engine.register_store(...)` to register storage;  
> 2) `engine.set_config(MemoryEngineConfig(...))` to set global configuration.


## Configuring Scope and Agent Memory Strategy

The memory engine isolates memories from different businesses/Agents through `scope_id`. Each `scope_id` corresponds to a set of `MemoryScopeConfig` and several `AgentMemoryConfig`.

### Configuring MemoryScopeConfig (Scope-level Model and Vector Configuration)

```python
from openjiuwen.core.retrieval.common.config import EmbeddingConfig
from openjiuwen.core.memory import MemoryScopeConfig


async def configure_scope(engine: LongTermMemory) -> None:
    scope_id = "app_demo_scope"

    scope_model_cfg = ModelRequestConfig(
        model=os.getenv("MEMORY_SCOPE_MODEL", "<model_name>"),
        temperature=0.1,
    )
    scope_model_client_cfg = ModelClientConfig(
        client_id="scope_llm_client",
        client_provider=os.getenv("MEMORY_SCOPE_PROVIDER", "OpenAI"),
        api_key=os.getenv("MEMORY_SCOPE_API_KEY", "sk-xxxx"),
        api_base=os.getenv("MEMORY_SCOPE_API_BASE", "https://api.openai.com/v1"),
        verify_ssl=False,
    )
    embed_cfg = EmbeddingConfig(
        model_name=os.getenv("EMBED_MODEL_NAME", "text-embedding-v3"),
        api_key=os.getenv("EMBED_API_KEY", "sk-embed-xxx"),
        base_url=os.getenv("EMBED_API_BASE", "https://api.openai.com/v1/embeddings"),
    )

    scope_cfg = MemoryScopeConfig(
        model_cfg=scope_model_cfg,
        model_client_cfg=scope_model_client_cfg,
        embedding_cfg=embed_cfg,
    )

    ok = await engine.set_scope_config(scope_id, scope_cfg)
    assert ok, "Failed to set MemoryScopeConfig"
```

> `set_scope_config(scope_id, MemoryScopeConfig)` automatically encrypts the configuration and writes it to KV storage, and caches it in memory; subsequent `add_messages/search_user_mem/...` will select the corresponding LLM and embedding model based on the scope's configuration.

### Configuring AgentMemoryConfig (Variable Memory Fields)

```python
from openjiuwen.core.memory import AgentMemoryConfig
from openjiuwen.core.common.schema.param import Param


agent_mem_cfg = AgentMemoryConfig(
    mem_variables=[
        Param.string("Name", "User name", required=False),
        Param.string("Occupation", "User occupation", required=False),
        Param.string("Residence", "User residence", required=False),
        Param.string("Hobby", "User hobby", required=False),
        Param.string("Age", "User age", required=False),
    ],
    enable_long_term_mem=True,
    enable_user_profile=True,
    enable_semantic_memory=True,
    enable_episodic_memory=True,
    enable_summary_memory=True
)
```

`AgentMemoryConfig` is passed when calling `add_messages` to guide the memory engine on which variables and long-term memories to extract from conversations.


## Writing Messages and Generating Memories (add_messages)

The memory engine no longer provides the old `add_conversation_messages(user_id, group_id, messages, timestamp)` interface. Instead, it uses `LongTermMemory.add_messages` to complete "write messages + extract memories":

```python
from datetime import datetime, timezone
from openjiuwen.core.foundation.llm.schema.message import BaseMessage


async def add_conversation(engine: LongTermMemory):
    user_id = "user1"
    scope_id = "app_demo_scope"
    session_id = "session_001"

    user_profile_messages = [
        BaseMessage(role="user", content="My name is Zhang San, I like playing badminton"),
        BaseMessage(role="assistant", content="Hello Zhang San, nice to meet you"),
        BaseMessage(role="user", content="I am a software engineer, living in Hangzhou"),
    ]
    episodic_messages = [
        BaseMessage(role="user", content="Yesterday I participated in the Hangzhou Marathon and finished in 4 hours and 32 minutes"),
        BaseMessage(role="assistant", content="That's amazing! A full marathon is quite a challenge, 4 hours and 32 minutes is a great result"),
        BaseMessage(role="user", content="Yes, last week I also went to Beijing for a business trip and attended a tech summit"),
    ]
    semantic_messages = [
        BaseMessage(role="user", content="Python is an interpreted programming language created by Guido van Rossum in 1991"),
        BaseMessage(role="assistant", content="Yes, Python is known for its concise syntax and rich ecosystem"),
        BaseMessage(role="user", content="Hangzhou is the capital city of Zhejiang Province and an important internet industry center in China"),
    ]

    timestamp = datetime.now(timezone.utc)
    for messages in [user_profile_messages, episodic_messages, semantic_messages]:
        await engine.add_messages(
            messages=messages,
            agent_config=agent_mem_cfg,
            user_id=user_id,
            scope_id=scope_id,
            session_id=session_id,
            timestamp=timestamp,
            gen_mem=True,                  # Whether to generate long-term memory
            gen_mem_with_history_msg_num=5 # How many historical messages to use when generating memory
        )
```

> Internally it will automatically:  
> - Write messages to the message table;  
> - Combine historical messages with `AgentMemoryConfig`, call the LLM to extract variables/user profiles/episodic memories/semantic memories/summary memories;
> - Write the extracted long-term memories to vector storage and DB.

Description of user profile, episodic, semantic and summary memory types:
- User profile memory (user_profile): Affirmative or negative statements about the user, including basic identity, interests and preferences, interpersonal relationships, asset status, etc. For example: "User likes playing badminton"  
- Episodic memory (episodic_memory): Descriptions of specific events experienced by the user. For example: "User participated in the Hangzhou Marathon on March 15, 2025"  
- Semantic memory (semantic_memory): Other information fragments, including concept definitions, factual information, long-term rules, etc. For example: "Python is an interpreted programming language"
- Summary memory (summary_memory): A summary of each conversation between the user and the agent.

## Querying Variable Memories (get_variables)

Use `get_variables` to query currently extracted variable memories by `user_id + scope_id`:

```python
variables = await engine.get_variables(user_id="user1", scope_id="app_demo_scope")
print(variables)
# Possible output:
# {"Name": "Zhang San", "Occupation": "Software Engineer", "Residence": "Hangzhou", "Hobby": "Badminton", "Age": "20"}
```

If you only want to read some variables, you can pass the `names` parameter:

```python
name_only = await engine.get_variables(user_id="user1", scope_id="app_demo_scope", names=["Name", "Occupation"])
```


## Paginated Viewing of Long-term Memories (get_user_mem_by_page)

Long-term memories (such as user profiles) can be viewed through the pagination interface. You can specify viewing specific types of memories using the `memory_type` parameter (memory_type types: USER_PROFILE/EPISODIC_MEMORY/SEMANTIC_MEMORY):

```python
from openjiuwen.core.memory.long_term_memory import MemoryType

# View specific type of memories
mem_list = await engine.get_user_mem_by_page(
    user_id="user1",
    scope_id="app_demo_scope",
    page_size=10,
    page_idx=1,
    memory_type=MemoryType.USER_PROFILE,  # Only view user profile type memories
)

for mem in mem_list:
    print(mem.mem_id, mem.type, mem.content)
```


## Semantic Memory Retrieval (search_user_mem)

`search_user_mem` provides vector similarity-based memory retrieval:

```python
# Search user profile memory
search_results = await engine.search_user_mem(
    query="What is the user's occupation?",
    num=1,
    user_id="user1",
    scope_id="app_demo_scope",
    threshold=0.3,  # Filter results below the threshold
)

for item in search_results:
    mem = item.mem_info
    print(f"mem_id={mem.mem_id}, type={mem.type}, score={item.score:.4f}, content={mem.content}")
```
```python
# Search user episodic memory
search_results = await engine.search_user_mem(
    query="When did the user participate in the marathon?",
    num=1,
    user_id="user1",
    scope_id="app_demo_scope",
    threshold=0.3,  # Filter results below the threshold
)

for item in search_results:
    mem = item.mem_info
    print(f"mem_id={mem.mem_id}, type={mem.type}, score={item.score:.4f}, content={mem.content}")
```
```python
# Search user semantic memory
search_results = await engine.search_user_mem(
    query="What type of language is Python?",
    num=1,
    user_id="user1",
    scope_id="app_demo_scope",
    threshold=0.3,  # Filter results below the threshold
)

for item in search_results:
    mem = item.mem_info
    print(f"mem_id={mem.mem_id}, type={mem.type}, score={item.score:.4f}, content={mem.content}")
```

The return value is a list of `MemResult`, each containing:

- `mem_info: MemInfo`: Includes `mem_id/content/type`;
- `score: float`: Similarity score.

## Semantic Retrieval of User History Summary Memory (search_user_history_summary)

`search_user_history_summary` provides vector similarity-based history summary memory retrieval:

```python
search_results = await engine.search_user_history_summary(
    query="What is the user's occupation?",
    num=5,
    user_id="user1",
    scope_id="app_demo_scope",
    threshold=0.3,  # Filter results below the threshold
)

for item in search_results:
    mem = item.mem_info
    print(f"mem_id={mem.mem_id}, type={mem.type}, score={item.score:.4f}, content={mem.content}")
```

The return value is a list of `MemResult`, each containing:

- `mem_info: MemInfo`: Includes `mem_id/content/type`;
- `score: float`: Similarity score.


## Updating and Deleting Memories

### Updating Variable Memories (update_variables)

```python
await engine.update_variables(
    variables={"Hobby": "Basketball"},
    user_id="user1",
    scope_id="app_demo_scope",
)
```

### Deleting Variable Memories (delete_variables)

```python
await engine.delete_variables(
    names=["Hobby"],
    user_id="user1",
    scope_id="app_demo_scope",
)
```

### Deleting Long-term Memory by Memory ID (delete_mem_by_id)

```python
# Assume a mem_id has been obtained through search_user_mem or get_user_mem_by_page
await engine.delete_mem_by_id(
    mem_id="mem_123",
    user_id="user1",
    scope_id="app_demo_scope",
)
```

### Deleting All Memories by User or Scope

```python
# Delete all memories for a user in a scope
await engine.delete_mem_by_user_id(user_id="user1", scope_id="app_demo_scope")

# Delete all memories for all users in a scope (and scope configuration)
await engine.delete_mem_by_scope(scope_id="app_demo_scope")
await engine.delete_scope_config(scope_id="app_demo_scope")
```


## Statistics and Auxiliary Queries

- **Get Statistics**: Use `get_user_mem_by_page` / `user_mem_total_num` to count the number of memories for a user in a scope.
- **Get Recent Messages**: `get_recent_messages(user_id, scope_id, session_id, num)` returns the most recent `BaseMessage` items.
- **Query by Message ID**: `get_message_by_id(msg_id)` returns the corresponding message and timestamp.

These interfaces are defined in the source code at `openjiuwen.core.memory.long_term_memory.LongTermMemory` and `openjiuwen.core.memory.knowledge_base`. The naming and signatures in the documentation are fully consistent with the source code and no longer use the deleted old interfaces such as `SysMemConfig/MemoryConfig/MemoryEngine.register_store/create_mem_engine_instance/add_conversation_messages/list_user_mem/update_user_variable/delete_user_variable`.