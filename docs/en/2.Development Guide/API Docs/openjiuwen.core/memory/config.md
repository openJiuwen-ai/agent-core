# openjiuwen.core.memory.config

`openjiuwen.core.memory.config` is the unified **memory configuration management module** in openJiuwen, responsible for:

- Defining `MemoryEngineConfig` global engine configuration;
- Defining `MemoryScopeConfig` scope-level configuration (for model/vector parameters in different business scenarios);
- Defining `AgentMemoryConfig` Agent-level memory strategy configuration (for defining variable memories to extract and whether to enable long-term memory).


## class openjiuwen.core.memory.config.config.MemoryEngineConfig

```
class openjiuwen.core.memory.config.config.MemoryEngineConfig(default_model_cfg: ModelRequestConfig | None = None, default_model_client_cfg: ModelClientConfig | None = None, input_msg_max_len: int = 8192, crypto_key: bytes = b'')
```

Global memory engine configuration, used to set engine-level common parameters.

**Parameters**:

* **default_model_cfg** (ModelRequestConfig | None, optional): Default LLM request parameters for memory generation (model name, temperature, max tokens, etc.); if `None`, memory generation is disabled (unless configured via `MemoryScopeConfig` for a specific scope). Default: `None`.
* **default_model_client_cfg** (ModelClientConfig | None, optional): Default LLM client configuration (`client_id / client_provider / api_base / api_key / verify_ssl`, etc.); if `None`, memory generation is also disabled (unless configured via scope). Default: `None`.
* **input_msg_max_len** (int, optional): Maximum length of input messages (character count); when generating memory, message content exceeding this length will be truncated. Default: 8192.
* **crypto_key** (bytes, optional): AES encryption key for encrypting sensitive parameters in storage (such as API key); must be 32 bytes in length; if empty bytes `b''`, encryption is disabled. Default: `b''` (no encryption).

**Parameter Validation**:

The `crypto_key` parameter has a `field_validator`:

- If the length is 0, it remains empty (no encryption);
- If the length equals `AES_KEY_LENGTH` (32), validation passes;
- Otherwise, a `ValueError` is raised with the error message: `"Invalid crypto_key, must be empty or {AES_KEY_LENGTH} bytes length"`.

**Example**:

```python
>>> from openjiuwen.core.memory.config import MemoryEngineConfig
>>> from openjiuwen.core.foundation.llm.schema.config import ModelRequestConfig, ModelClientConfig
>>> 
>>> # Create global engine configuration
>>> engine_config = MemoryEngineConfig(
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
>>>     crypto_key=b"your-32-byte-aes-key-here!!",  # 32 bytes
>>> )
```


## class openjiuwen.core.memory.config.config.MemoryScopeConfig

```
class openjiuwen.core.memory.config.config.MemoryScopeConfig(model_cfg: ModelRequestConfig | None = None, model_client_cfg: ModelClientConfig | None = None, embedding_cfg: EmbeddingConfig | None = None)
```

Scope-level memory configuration, used to define independent model and vector parameters for different `scope_id`.

**Parameters**:

* **model_cfg** (ModelRequestConfig | None, optional): LLM request configuration used in this scope (model name, temperature, etc.); if `None`, uses the global `MemoryEngineConfig.default_model_cfg`. Default: `None`.
* **model_client_cfg** (ModelClientConfig | None, optional): LLM client configuration used in this scope (`client_id / api_base / api_key`, etc.); if `None`, uses the global `MemoryEngineConfig.default_model_client_cfg`. Default: `None`.
* **embedding_cfg** (EmbeddingConfig | None, optional): Embedding model configuration used in this scope (`model_name / base_url / api_key`, etc.); if `None`, semantic retrieval may be unavailable (depending on whether a global embedding model is provided). Default: `None`.

> **Note**: The `api_key` parameter in `MemoryScopeConfig` will be automatically encrypted when saved to `kv_store` (using `MemoryEngineConfig.crypto_key`) and automatically decrypted when read.

**Example**:

```python
>>> from openjiuwen.core.memory.config import MemoryScopeConfig
>>> from openjiuwen.core.foundation.llm.schema.config import ModelRequestConfig, ModelClientConfig
>>> from openjiuwen.core.retrieval.common.config import EmbeddingConfig
>>> 
>>> # Create scope configuration
>>> scope_config = MemoryScopeConfig(
>>>     model_cfg=ModelRequestConfig(
>>>         model="gpt-3.5-turbo",
>>>         temperature=0.1,
>>>     ),
>>>     model_client_cfg=ModelClientConfig(
>>>         client_id="scope_llm",
>>>         client_provider="OpenAI",
>>>         api_key="sk-yyyy",
>>>         api_base="https://api.openai.com/v1",
>>>     ),
>>>     embedding_cfg=EmbeddingConfig(
>>>         model_name="text-embedding-3-small",
>>>         base_url="https://api.openai.com/v1",
>>>         api_key="sk-zzzz",
>>>     ),
>>> )
```


## class openjiuwen.core.memory.config.config.AgentMemoryConfig

```
class openjiuwen.core.memory.config.config.AgentMemoryConfig(mem_variables: list[Param] = [], enable_long_term_mem: bool = True, enable_user_profile: bool = True, enable_semantic_memory: bool = True, enable_episodic_memory: bool = True, enable_summary_memory: bool = True)
```

Agent-level memory strategy configuration, describing what types of memories an agent wants to extract and manage.

**Parameters**:

* **mem_variables** (list[Param], optional): Variable memory configuration list; each `Param` defines a variable name, description, type, whether it's required, etc.; when `LongTermMemory.add_messages` is called, variable values will be extracted from conversations based on these configurations and saved. Default: `[]`.
* **enable_long_term_mem** (bool, optional): Whether to enable long-term memory generation; when `True`, user profiles (long-term memory) will be extracted from conversations and saved to semantic storage; when `False`, only messages and variable memories are saved, and user profiles are not generated. Default: `True`.
* **enable_user_profile** (bool, optional): Whether to enable user profile generation and use; when `True`, user personal information (such as name, phone number, etc.) will be extracted from the conversation and saved to the semantic storage, and user profile will be used in semantic retrieval; when `False`, user profile is not generated and used. Default: `True`.
* **enable_semantic_memory** (bool, optional): Whether to enable semantic memory generation and use; when `True`, semantic memory will be extracted from the conversation and saved to the semantic storage, and semantic memory will be used in semantic retrieval; when `False`, semantic memory is not generated and used. Default: `True`.
* **enable_episodic_memory** (bool, optional): Whether to enable episodic memory generation and use; when `True`, episodic memory will be extracted from the conversation and saved to the semantic storage, and episodic memory will be used in semantic retrieval; when `False`, episodic memory is not generated and used. Default: `True`.
* **enable_summary_memory** (bool, optional): Whether to enable user summary memory generation; when `True`, user summaries (such as recent conversation content) will be extracted from conversations and saved to semantic storage; when `False`, user summary memory is not generated. Default: `True`.

> **Note**: The `Param` type is defined in `openjiuwen.core.common.schema.param`, typically containing parameters such as `name / description / type / required`.

**Example**:

```python
>>> from openjiuwen.core.memory.config import AgentMemoryConfig
>>> from openjiuwen.core.common.schema.param import Param
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
```


## Usage Example

```python
>>> from openjiuwen.core.memory import (
>>>     MemoryEngineConfig,
>>>     MemoryScopeConfig,
>>>     AgentMemoryConfig,
>>> )
>>> from openjiuwen.core.foundation.llm.schema.config import (
>>>     ModelClientConfig,
>>>     ModelRequestConfig,
>>> )
>>> from openjiuwen.core.retrieval.common.config import EmbeddingConfig
>>> from openjiuwen.core.common.schema.param import Param
>>> 
>>> # 1. Create global engine configuration
>>> engine_config = MemoryEngineConfig(
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
>>>     crypto_key=b"your-32-byte-aes-key-here!!",  # 32 bytes
>>> )
>>> 
>>> # 2. Create scope configuration (optional)
>>> scope_config = MemoryScopeConfig(
>>>     model_cfg=ModelRequestConfig(
>>>         model="gpt-3.5-turbo",
>>>         temperature=0.1,
>>>     ),
>>>     model_client_cfg=ModelClientConfig(
>>>         client_id="scope_llm",
>>>         client_provider="OpenAI",
>>>         api_key="sk-yyyy",
>>>         api_base="https://api.openai.com/v1",
>>>     ),
>>>     embedding_cfg=EmbeddingConfig(
>>>         model_name="text-embedding-3-small",
>>>         base_url="https://api.openai.com/v1",
>>>         api_key="sk-zzzz",
>>>     ),
>>> )
>>> 
>>> # 3. Create Agent memory strategy configuration
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
```