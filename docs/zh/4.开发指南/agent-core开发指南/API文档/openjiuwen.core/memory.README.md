# memory

`openjiuwen.core.memory` 是 openJiuwen 框架的**长期记忆管理模块**，提供 Agent 运行过程中的记忆存取、处理等能力。

**Classes**：

| CLASS                                 | DESCRIPTION |
|---------------------------------------|-------------|
| [LongTermMemory](memory/long_term_memory.md)  | 长期记忆引擎类（单例），负责消息持久化、记忆生成与检索。 |
| [MemoryEngineConfig](./memory/config.md) | 全局引擎配置类（默认模型、加密密钥等）。 |
| [MemoryScopeConfig](./memory/config.md) | 作用域级配置类（为不同 scope 配置独立的模型/向量参数）。 |
| [AgentMemoryConfig](./memory/config.md) | Agent 级记忆策略配置类（定义需要提取的变量记忆和是否开启长期记忆）。 |
| [BaseKVStore](./memory/store.md)      | KV 存储抽象基类。 |
| [BaseDbStore](./memory/store.md)      | 关系型数据库存储抽象基类。 |
