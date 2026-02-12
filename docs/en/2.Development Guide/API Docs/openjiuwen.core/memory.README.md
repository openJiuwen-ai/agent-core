# memory

`openjiuwen.core.memory` is the **long-term memory management module** of the openJiuwen framework, providing capabilities for memory storage, access, and processing during Agent execution.

**Classes**：

| CLASS                                 | DESCRIPTION |
|---------------------------------------|-------------|
| [LongTermMemory](./memory/long_term_memory.md)  | Long-term memory engine class (singleton), responsible for message persistence, memory generation and retrieval. |
| [MemoryEngineConfig](./memory/config.md) | Global engine configuration class (default model, encryption key, etc.). |
| [MemoryScopeConfig](./memory/config.md) | Scope-level configuration class (configure independent model/vector parameters for different scopes). |
| [AgentMemoryConfig](./memory/config.md) | Agent-level memory strategy configuration class (defines variable memories to extract and whether to enable long-term memory). |
| [BaseKVStore](./memory/store.md)      | KV storage abstract base class. |
| [BaseDbStore](./memory/store.md)      | Relational database storage abstract base class. |