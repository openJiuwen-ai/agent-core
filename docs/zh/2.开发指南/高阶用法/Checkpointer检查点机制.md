# Checkpointer 检查点机制

Checkpointer（检查点）是 openJiuwen 框架中用于管理 Agent 和工作流状态持久化和恢复的核心机制。它支持在关键执行节点保存状态，并在需要时恢复状态，从而实现中断恢复、异常恢复等功能。

## 核心概念

### 检查点的作用

Checkpointer 主要负责以下功能：

1. **状态持久化**：在 Agent 和工作流执行的关键节点保存状态
2. **状态恢复**：在重新执行时恢复之前保存的状态
3. **中断恢复**：支持工作流和 Agent 的中断-恢复机制
4. **异常恢复**：在发生异常时保存状态，便于后续恢复

### 命名空间结构

Checkpointer 使用命名空间来组织不同类型的状态：

- **`SESSION_NAMESPACE_AGENT`** (`"agent"`)：Agent 状态在会话下的命名空间
- **`SESSION_NAMESPACE_WORKFLOW`** (`"workflow"`)：工作流状态在会话下的命名空间（工作流自身状态）
- **`WORKFLOW_NAMESPACE_GRAPH`** (`"workflow-graph"`)：图状态在工作流下的命名空间（与工作流自身状态分离）

键的构建格式为：`session_id:namespace:entity_id:suffix`

## 检查点类型

openJiuwen 提供了多种检查点实现：

### 1. InMemoryCheckpointer（内存检查点）

基于内存的检查点实现，所有状态保存在内存中，进程重启后状态会丢失。适用于开发和测试场景。

**特点**：

- 无需额外配置
- 性能高，适合快速开发
- 数据不持久化，进程重启后丢失

**使用示例**：

```python
from openjiuwen.core.session.checkpointer import InMemoryCheckpointer

# 创建内存检查点实例
checkpointer = InMemoryCheckpointer()

# 使用检查点进行状态管理
# checkpointer 会在 Agent 和工作流执行时自动保存和恢复状态
```

### 2. PersistenceCheckpointer（持久化检查点）

基于持久化存储的检查点实现，使用 `BaseKVStore` 接口进行状态持久化，支持任何实现了 `BaseKVStore` 的存储后端。

**支持的存储后端**：

- **SQLite**：基于 SQLite 数据库的存储
- **Shelve**：基于 Python shelve 模块的文件存储

**配置示例**：

```python
from openjiuwen.core.session.checkpointer import (
    CheckpointerFactory,
    CheckpointerConfig,
)

# 使用 SQLite 存储
config = CheckpointerConfig(
    type="persistence",
    conf={
        "db_type": "sqlite",
        "db_path": "checkpointer.db"
    }
)
checkpointer = await CheckpointerFactory.create(config)

# 使用 Shelve 存储
config = CheckpointerConfig(
    type="persistence",
    conf={
        "db_type": "shelve",
        "db_path": "checkpoint"
    }
)
checkpointer = await CheckpointerFactory.create(config)
```

### 3. RedisCheckpointer（Redis 检查点）

基于 Redis 的检查点实现，支持独立 Redis 和 Redis 集群模式。适用于生产环境，支持分布式部署。

**特点**：

- 支持独立 Redis 和 Redis 集群
- 支持 TTL（生存时间）配置
- 支持读取时刷新 TTL
- 适合分布式场景

**配置示例**：

```python
from openjiuwen.core.session.checkpointer import (
    CheckpointerFactory,
    CheckpointerConfig,
)

# 独立 Redis
config = CheckpointerConfig(
    type="redis",
    conf={
        "connection": {
            "url": "redis://localhost:6379"
        }
    }
)
checkpointer = await CheckpointerFactory.create(config)

# Redis 集群模式
config = CheckpointerConfig(
    type="redis",
    conf={
        "connection": {
            "url": "redis://localhost:7000",
            "cluster_mode": True
        }
    }
)
checkpointer = await CheckpointerFactory.create(config)

# 带 TTL 配置
config = CheckpointerConfig(
    type="redis",
    conf={
        "connection": {
            "url": "redis://localhost:6379"
        },
        "ttl": {
            "default_ttl": 5,  # TTL 为 5 分钟
            "refresh_on_read": True  # 读取时刷新 TTL
        }
    }
)
checkpointer = await CheckpointerFactory.create(config)
```

## 检查点生命周期

### Agent 检查点生命周期

Agent 检查点在以下时机进行状态管理：

1. **`pre_agent_execute`**：Agent 执行前，恢复 Agent 状态
2. **`interrupt_agent_execute`**：Agent 需要中断等待用户交互时，保存 Agent 状态
3. **`post_agent_execute`**：Agent 执行完成后，保存 Agent 状态

**执行流程**：

```text
开始执行 Agent
    ↓
pre_agent_execute (恢复状态)
    ↓
执行 Agent 逻辑
    ↓
如果需要中断 → interrupt_agent_execute (保存状态)
    ↓
执行完成 → post_agent_execute (保存状态)
```

### 工作流检查点生命周期

工作流检查点在以下时机进行状态管理：

1. **`pre_workflow_execute`**：工作流执行前，恢复或清理工作流状态
2. **`post_workflow_execute`**：工作流执行后，保存或清理工作流状态

**执行流程**：

```text
开始执行工作流
    ↓
pre_workflow_execute
    ├─ 如果是 InteractiveInput → 恢复工作流状态
    └─ 如果不是 InteractiveInput → 检查状态
        ├─ 状态存在且未启用强制删除 → 抛出异常
        └─ 状态存在且启用强制删除 → 清理状态
    ↓
执行工作流逻辑
    ↓
post_workflow_execute
    ├─ 发生异常 → 保存状态并抛出异常
    ├─ 正常完成 → 清理状态
    └─ 需要中断 → 保存状态
```

## 使用检查点

### 在 Runner 中配置检查点

Runner 是 openJiuwen 框架的核心执行器，在 Runner 启动时会自动初始化配置的检查点。这是推荐的使用方式，因为 Runner 会统一管理检查点实例，确保所有 Agent 和工作流使用相同的检查点配置。

#### 配置方式

通过 `RunnerConfig` 的 `checkpointer_config` 字段配置检查点：

```python
from openjiuwen.core.runner import Runner
from openjiuwen.core.runner.runner_config import RunnerConfig
from openjiuwen.core.session.checkpointer import CheckpointerConfig

# 创建 Runner 配置
runner_config = RunnerConfig()

# 配置检查点
runner_config.checkpointer_config = CheckpointerConfig(
    type="in_memory",  # 或 "persistence"、"redis"
    conf={}
)

# 设置 Runner 配置
Runner.set_config(runner_config)

# 启动 Runner（会自动初始化检查点）
await Runner.start()
```

#### 使用内存检查点

适用于开发和测试环境：

```python
from openjiuwen.core.runner import Runner
from openjiuwen.core.runner.runner_config import RunnerConfig
from openjiuwen.core.session.checkpointer import CheckpointerConfig

runner_config = RunnerConfig()
runner_config.checkpointer_config = CheckpointerConfig(
    type="in_memory",
    conf={}
)
Runner.set_config(runner_config)
await Runner.start()
```

#### 使用持久化检查点（SQLite）

适用于单机生产环境，使用 SQLite 作为存储后端：

```python
from openjiuwen.core.runner import Runner
from openjiuwen.core.runner.runner_config import RunnerConfig
from openjiuwen.core.session.checkpointer import CheckpointerConfig

runner_config = RunnerConfig()
runner_config.checkpointer_config = CheckpointerConfig(
    type="persistence",
    conf={
        "db_type": "sqlite",
        "db_path": "checkpointer.db"  # SQLite 数据库文件路径
    }
)
Runner.set_config(runner_config)
await Runner.start()
```

#### 使用持久化检查点（Shelve）

使用 Python shelve 模块作为存储后端：

```python
from openjiuwen.core.runner import Runner
from openjiuwen.core.runner.runner_config import RunnerConfig
from openjiuwen.core.session.checkpointer import CheckpointerConfig

runner_config = RunnerConfig()
runner_config.checkpointer_config = CheckpointerConfig(
    type="persistence",
    conf={
        "db_type": "shelve",
        "db_path": "checkpoint"  # Shelve 文件路径（不含扩展名）
    }
)
Runner.set_config(runner_config)
await Runner.start()
```

#### 使用 Redis 检查点

适用于分布式生产环境，支持独立 Redis 和 Redis 集群：

```python
from openjiuwen.core.runner import Runner
from openjiuwen.core.runner.runner_config import RunnerConfig
from openjiuwen.core.session.checkpointer import CheckpointerConfig

# 独立 Redis
runner_config = RunnerConfig()
runner_config.checkpointer_config = CheckpointerConfig(
    type="redis",
    conf={
        "connection": {
            "url": "redis://localhost:6379"
        }
    }
)
Runner.set_config(runner_config)
await Runner.start()

# Redis 集群模式
runner_config = RunnerConfig()
runner_config.checkpointer_config = CheckpointerConfig(
    type="redis",
    conf={
        "connection": {
            "url": "redis://localhost:7000",
            "cluster_mode": True
        }
    }
)
Runner.set_config(runner_config)
await Runner.start()

# 带 TTL 配置的 Redis
runner_config = RunnerConfig()
runner_config.checkpointer_config = CheckpointerConfig(
    type="redis",
    conf={
        "connection": {
            "url": "redis://localhost:6379"
        },
        "ttl": {
            "default_ttl": 60,  # 60 分钟过期
            "refresh_on_read": True  # 读取时刷新 TTL
        }
    }
)
Runner.set_config(runner_config)
await Runner.start()
```

#### Runner 初始化流程

Runner 在启动时会执行以下步骤：

1. **检查配置**：检查 `RunnerConfig.checkpointer_config` 是否配置
2. **懒加载 Provider**：对于 `redis` 类型，会懒加载导入 Redis checkpointer provider 以确保注册
3. **创建实例**：通过 `CheckpointerFactory.create()` 创建检查点实例
4. **设置为默认**：将创建的检查点设置为默认检查点，供所有 Agent 和工作流使用
5. **日志记录**：记录检查点初始化成功或失败的信息

**注意事项**：

- 如果配置了 `redis` 类型但未安装 Redis 依赖，Runner 启动会失败并提示安装依赖
- 检查点初始化失败会导致 Runner 启动失败
- 一旦 Runner 启动成功，所有通过 Runner 执行的 Agent 和工作流都会自动使用配置的检查点
- **Provider 注册机制**：Runner 会自动处理 provider 的注册，无需手动导入

### 在 Agent 中使用

Agent 会自动使用 Runner 配置的检查点进行状态管理。如果 Runner 已配置检查点，Agent 无需额外配置：

```python
from openjiuwen.core.application import LLMAgent
from openjiuwen.core.runner import Runner

# Runner 已配置检查点并启动
# 创建 Agent（会自动使用 Runner 配置的检查点）
agent = LLMAgent(...)
# Agent 执行时会自动使用检查点进行状态管理
```

如果需要在 Runner 外部单独使用检查点：

```python
from openjiuwen.core.application import LLMAgent
from openjiuwen.core.session.checkpointer import (
    CheckpointerFactory,
    CheckpointerConfig,
)

# 如果使用 Redis checkpointer，需要先导入以注册 provider
# from openjiuwen.extensions.checkpointer.redis import checkpointer  # noqa: F401

# 配置检查点
checkpointer_config = CheckpointerConfig(
    type="in_memory",  # 或 "persistence"、"redis"
    conf={}
)
checkpointer = await CheckpointerFactory.create(checkpointer_config)
CheckpointerFactory.set_default_checkpointer(checkpointer)

# 创建 Agent（检查点会自动集成）
agent = LLMAgent(...)
# Agent 执行时会自动使用检查点进行状态管理
```

### 在工作流中使用

工作流也会自动使用 Runner 配置的检查点进行状态管理。如果 Runner 已配置检查点，工作流无需额外配置：

```python
from openjiuwen.core.workflow import Workflow
from openjiuwen.core.runner import Runner

# Runner 已配置检查点并启动
# 创建工作流（会自动使用 Runner 配置的检查点）
workflow = Workflow()
# 工作流执行时会自动使用检查点进行状态管理
```

如果需要在 Runner 外部单独使用检查点：

```python
from openjiuwen.core.workflow import Workflow
from openjiuwen.core.session.checkpointer import (
    CheckpointerFactory,
    CheckpointerConfig,
)

# 如果使用 Redis checkpointer，需要先导入以注册 provider
# from openjiuwen.extensions.checkpointer.redis import checkpointer  # noqa: F401

# 配置检查点
checkpointer_config = CheckpointerConfig(
    type="persistence",
    conf={
        "db_type": "sqlite",
        "db_path": "workflow_checkpoint.db"
    }
)
checkpointer = await CheckpointerFactory.create(checkpointer_config)
CheckpointerFactory.set_default_checkpointer(checkpointer)

# 创建工作流
workflow = Workflow()
# 工作流执行时会自动使用检查点进行状态管理
```

### 手动管理检查点

你也可以手动管理检查点。**重要**：如果使用扩展的 checkpointer（如 Redis），需要先导入相应的模块以确保 provider 注册。

#### 使用内置 Checkpointer（自动注册）

`in_memory` 和 `persistence` 类型的 provider 会在导入 `openjiuwen.core.session.checkpointer` 时自动注册：

```python
from openjiuwen.core.session.checkpointer import (
    CheckpointerFactory,
    CheckpointerConfig,
    InMemoryCheckpointer,
)

# 使用内存检查点（自动注册）
checkpointer = InMemoryCheckpointer()
CheckpointerFactory.set_default_checkpointer(checkpointer)

# 或通过工厂创建
config = CheckpointerConfig(type="in_memory", conf={})
checkpointer = await CheckpointerFactory.create(config)
CheckpointerFactory.set_default_checkpointer(checkpointer)

# 使用持久化检查点（自动注册）
config = CheckpointerConfig(
    type="persistence",
    conf={"db_type": "sqlite", "db_path": "checkpoint.db"}
)
checkpointer = await CheckpointerFactory.create(config)
CheckpointerFactory.set_default_checkpointer(checkpointer)
```

#### 使用扩展 Checkpointer（需要提前导入）

对于扩展的 checkpointer（如 Redis），需要先导入相应的模块以确保 provider 注册：

```python
from openjiuwen.core.session.checkpointer import (
    CheckpointerFactory,
    CheckpointerConfig,
)

# 重要：必须先导入 Redis checkpointer 模块以注册 provider
from openjiuwen.extensions.checkpointer.redis import checkpointer  # noqa: F401

# 现在可以使用 Redis checkpointer
config = CheckpointerConfig(
    type="redis",
    conf={"connection": {"url": "redis://localhost:6379"}}
)
checkpointer = await CheckpointerFactory.create(config)
CheckpointerFactory.set_default_checkpointer(checkpointer)
```

**Provider 注册机制说明**：

- **内置 Provider**（`in_memory`、`persistence`）：在导入 `openjiuwen.core.session.checkpointer` 时自动注册，无需额外操作
- **扩展 Provider**（`redis`）：需要显式导入相应的模块才能注册
  - 导入方式：`from openjiuwen.extensions.checkpointer.redis import checkpointer`
  - 导入时装饰器 `@CheckpointerFactory.register("redis")` 会执行，完成注册
- **在 Runner 中使用**：Runner 会自动处理 provider 的注册，无需手动导入

#### 手动管理检查点实例

```python
from openjiuwen.core.session.checkpointer import CheckpointerFactory

# 获取检查点
checkpointer = CheckpointerFactory.get_checkpointer()

# 检查会话是否存在
exists = await checkpointer.session_exists("session_id")

# 释放会话资源
await checkpointer.release("session_id")

# 释放特定 Agent 的资源
await checkpointer.release("session_id", agent_id="agent_id")
```

## 中断恢复机制

Checkpointer 支持工作流和 Agent 的中断恢复机制。

### 工作流中断恢复

当工作流需要用户交互时，会触发中断并保存状态：

```python
from openjiuwen.core.workflow import WorkflowComponent
from openjiuwen.core.session import InteractiveInput

class InteractiveNode(WorkflowComponent):
    async def invoke(self, inputs, session, context):
        # 触发中断，等待用户输入
        user_input = await session.interact("请输入您的选择：")
        return {"result": user_input}

# 首次执行，触发中断
output = await workflow.invoke({"input": "test"}, session)

# 恢复执行，提供用户输入
user_input = InteractiveInput(raw_inputs="用户选择")
output = await workflow.invoke(user_input, session)
```

### Agent 中断恢复

Agent 也支持中断恢复机制：

```python
# Agent 执行过程中触发中断
# 检查点会自动保存 Agent 状态

# 恢复执行时，检查点会自动恢复 Agent 状态
```

## 最佳实践

### 1. 选择合适的检查点类型

- **开发/测试环境**：使用 `InMemoryCheckpointer`，简单快速
- **单机生产环境**：使用 `PersistenceCheckpointer` 配合 SQLite 或 Shelve
- **分布式生产环境**：使用 `RedisCheckpointer`，支持集群模式

### 2. 配置 TTL（仅 Redis）

对于 Redis 检查点，建议配置 TTL 以避免数据无限增长：

```python
config = CheckpointerConfig(
    type="redis",
    conf={
        "connection": {"url": "redis://localhost:6379"},
        "ttl": {
            "default_ttl": 60,  # 60 分钟过期
            "refresh_on_read": True  # 读取时刷新，保持活跃会话
        }
    }
)
```

### 3. 异常处理

检查点会在异常发生时自动保存状态，但你需要确保：

- 异常发生后能够正确恢复状态
- 定期清理过期或无效的状态
- 监控检查点存储的使用情况

### 4. 状态清理

定期清理不再需要的状态：

```python
# 释放特定会话的资源
await checkpointer.release("session_id")

# 释放特定 Agent 的资源
await checkpointer.release("session_id", agent_id="agent_id")
```

## 故障排查

### 常见问题

1. **Provider 未注册错误**
   - **问题**：使用 `CheckpointerFactory.create()` 时提示 provider 不存在
   - **原因**：扩展的 checkpointer（如 Redis）需要先导入模块才能注册 provider
   - **解决方法**：

     ```python
     # 对于 Redis checkpointer，需要先导入
     from openjiuwen.extensions.checkpointer.redis import checkpointer  # noqa: F401
     
     # 然后再创建
     config = CheckpointerConfig(type="redis", conf={...})
     checkpointer = await CheckpointerFactory.create(config)
     ```

   - **注意**：在 Runner 中使用时，Runner 会自动处理导入，无需手动操作

2. **状态恢复失败**
   - 检查检查点配置是否正确
   - 检查存储后端是否正常运行
   - 检查会话 ID 是否正确
   - 检查 provider 是否已正确注册

3. **状态未保存**
   - 检查检查点是否正确配置
   - 检查是否在正确的执行节点调用保存方法
   - 检查存储后端是否有写入权限
   - 检查 provider 是否已正确注册

4. **状态冲突**
   - 确保同一会话 ID 不会并发执行
   - 检查是否有多个检查点实例同时操作同一会话

### 调试技巧

```python
# 检查会话是否存在
exists = await checkpointer.session_exists("session_id")
print(f"Session exists: {exists}")

# 获取图状态存储
graph_store = checkpointer.graph_store()
# 可以进一步检查图状态
```

## 参考

- [Checkpointer API 文档](../API文档/openjiuwen.core/session/checkpointer.md)
- [Redis Checkpointer API 文档](../API文档/openjiuwen.extensions/checkpointer/checkpointer.md)
- [Session 状态管理](./Session/状态管理.md)
- [Session 中断恢复](./Session/中断恢复.md)
