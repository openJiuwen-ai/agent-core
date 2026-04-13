# AgentTeams 使用指南

AgentTeams 是一个多智能体协作框架，通过 Leader（负责人）和 Teammates（队友）的协同工作完成复杂任务。

## 核心概念

### Leader（负责人）
- 负责任务分解、分配和协调
- 管理团队成员的生命周期
- 处理用户输入并分配给合适的队友

### Teammate（队友）
- 执行特定领域的任务
- 通过消息系统与 Leader 通信
- 可以独立运行在单独的进程中

### 架构组件
- **Transport（传输层）**: 负责智能体间消息传递（支持 pyzmq、team_runtime）
- **Storage（存储层）**: 持久化团队状态、任务列表和消息（支持 sqlite）
- **CoordinationLoop（协调循环）**: 管理智能体的执行流程和事件处理

## 快速开始

```python
import asyncio
from openjiuwen.agent_teams import (
    create_agent_team,
    DeepAgentSpec,
    TransportSpec,
    StorageSpec,
    WorkspaceSpec,
    TeamModelConfig,
    MessagerTransportConfig
)
from openjiuwen.core.foundation.llm.schema.config import (
    ModelClientConfig,
    ModelRequestConfig,
)
from openjiuwen.core.runner import Runner

async def main():
    # 初始化 Runner
    await Runner.start()

    # 构建模型配置
    model_config = TeamModelConfig(
        model_client_config=ModelClientConfig(
            client_provider="OpenAI",
            api_key="your-api-key",
            api_base="your-api-base-url",
            timeout=120,
        ),
        model_request_config=ModelRequestConfig(
            model="your-model-name",
            temperature=0.2,
            top_p=0.9,
        ),
    )

    # 构建传输配置（Leader 使用）
    transport_config = MessagerTransportConfig(
        backend="pyzmq",
        team_id="demo_team",
        node_id="team_leader",
        direct_addr="tcp://127.0.0.1:{leader_port}",
        pubsub_publish_addr="tcp://127.0.0.1:{pub_port}",
        pubsub_subscribe_addr="tcp://127.0.0.1:{sub_port}",
        metadata={"pubsub_bind": True},
    )

    # 创建团队
    leader = create_agent_team(
        agents={
            "leader": DeepAgentSpec(
                model=model_config,
                workspace=WorkspaceSpec(root_path="./workspace"),
                max_iterations=200,
                completion_timeout=600.0,
            ),
            "teammate": DeepAgentSpec(
                model=model_config,
                workspace=WorkspaceSpec(root_path="./workspace"),
                max_iterations=200,
                completion_timeout=600.0,
            ),
        },
        team_name="demo_team",
        teammate_mode="build_mode",
        transport=TransportSpec(type="pyzmq", params=transport_config.model_dump()),
        storage=StorageSpec(type="sqlite", params={"connection_string": "./team.db"}),
    )

    # 流式执行
    async for chunk in Runner.run_agent_streaming(
        agent=leader,
        inputs={"query": "创建一个 3 人团队，讨论人工智能的未来发展"},
        session="demo_session",
    ):
        print(chunk, end="", flush=True)

    await Runner.stop()

asyncio.run(main())
```

## 配置详解

### DeepAgentSpec（智能体规范）

```python
DeepAgentSpec(
    model=TeamModelConfig(...),        # 模型配置
    workspace=WorkspaceSpec(...),       # 工作空间
    max_iterations=200,               # 最大迭代次数
    completion_timeout=600.0,          # 完成超时时间
    system_prompt="自定义系统提示词",   # 可选：自定义提示词
    tools=[...],                     # 可选：工具列表
    rails=[...],                     # 可选：轨道列表
)
```

### TransportSpec（传输层规范）

```python
# PyZMQ 后端（推荐）
transport_config = MessagerTransportConfig(
    backend="pyzmq",
    team_id="team_id",
    node_id="team_leader",
    direct_addr="tcp://{host}:{direct_port}",
    pubsub_publish_addr="tcp://{host}:{pub_port}",
    pubsub_subscribe_addr="tcp://{host}:{sub_port}",
    metadata={"pubsub_bind": True},
)

transport = TransportSpec(type="pyzmq", params=transport_config.model_dump())
```

**端口分配说明**:
- Leader 需要 3 个端口：direct、pubsub_publish、pubsub_subscribe
- 每个 Teammate 需要 3 个端口
- 建议为每个成员预留连续的端口段

### StorageSpec（存储层规范）

```python
# SQLite 存储
storage = StorageSpec(
    type="sqlite",
    params={"connection_string": "./team.db"},
)
```

## 执行模式

### 流式执行（推荐）

```python
async for chunk in Runner.run_agent_streaming(
    agent=leader,
    inputs={"query": "任务描述"},
    session="session_id",
):
    print(chunk, end="", flush=True)
```

### 交互模式

```python
# 启动后台流式任务
stream_task = asyncio.create_task(
    Runner.run_agent_streaming(
        agent=leader,
        inputs={"query": "初始任务"},
        session="session_id",
    )
)

# 发送后续输入
await leader.interact("补充指令")

# 通过 @mention 向特定队友发送直接消息
await leader.interact("@teammate_member_id 请专注处理数据分析部分")
```

`@member_id message` 语法会将消息直接路由到目标队友，绕过 leader 的 agent 逻辑。发送者在消息表中记录为 `"user"`。

## 团队生命周期模式

### Temporary（临时模式）
- 任务完成后自动解散团队
- 适用于一次性任务
- 默认模式

```python
leader = create_agent_team(
    ...,
    lifecycle="temporary",  # 或省略此参数
)
```

### Persistent（持久模式）
- 团队状态和成员跨会话保留
- `invoke()` / `stream()` 完成后团队进入待命状态，不会关闭
- 队友进程保持存活，通过 `TEAM_STANDBY` 事件暂停轮询
- 后续 `invoke()` 调用自动恢复协调循环
- 支持通过 `resume_persistent_team()` 跨会话恢复
- 适用于长期运行的团队

```python
leader = create_agent_team(
    ...,
    lifecycle="persistent",
)
```

## 恢复与持久化

### 恢复持久团队

对于仍处于待命状态的持久团队（同一进程），使用 `resume_persistent_team()` 开始新一轮：

```python
from openjiuwen.agent_teams import resume_persistent_team

# 在新会话中恢复（团队进程仍然存活）
leader = await resume_persistent_team(leader, new_session_id="round_2")

# 运行下一轮
async for chunk in leader.stream(inputs={"query": "下一个任务"}):
    print(chunk)
```

### 从会话恢复

用于崩溃恢复或跨进程还原，使用 `recover_agent_team()`：

```python
from openjiuwen.agent_teams.factory import recover_agent_team

# 恢复团队 Leader
leader = await recover_agent_team(session_id="previous_session_id")

# 恢复所有队友
await leader.recover_team()

# 继续执行
async for chunk in leader.stream(inputs={"query": "继续任务"}):
    print(chunk)
```

## 队友执行模式

### Plan Mode（计划模式）
- 队友完成任务需要 Leader 批准
- 适用于需要严格控制的场景

```python
leader = create_agent_team(
    ...,
    teammate_mode="plan_mode",
)
```

### Build Mode（构建模式）
- 队友直接完成任务，无需批准
- 适用于信任队友的场景
- 默认模式

```python
leader = create_agent_team(
    ...,
    teammate_mode="build_mode",
)
```

## 预定义团队成员

可以预配置团队成员，跳过动态 `spawn_member` 步骤。提供 `predefined_members` 后，所有成员自动注册到数据库，leader 使用简化的工作流，不包含 `spawn_member` 工具。

```python
from openjiuwen.agent_teams.schema.team import TeamMemberSpec, TeamRole

leader = create_agent_team(
    agents={...},
    team_name="my_team",
    predefined_members=[
        TeamMemberSpec(
            member_id="analyst",
            name="DataAnalyst",
            role_type=TeamRole.TEAMMATE,
            persona="数据分析专家",
        ),
        TeamMemberSpec(
            member_id="writer",
            name="ReportWriter",
            role_type=TeamRole.TEAMMATE,
            persona="技术写作专家",
        ),
    ],
    transport=TransportSpec(...),
    storage=StorageSpec(...),
)
```

## 健康检查与自动恢复

AgentTeams 内置健康检查机制：

1. Leader 定期检查队友进程状态
2. 检测到队友不健康时自动重启
   - 最多重试 3 次，采用指数退避策略
3. 重启成功后发布 `MemberRestartedEvent` 事件
4. 重启失败后标记队友为 ERROR 状态

## 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `API_BASE` | LLM API 基础 URL | - |
| `API_KEY` | LLM API 密钥 | - |
| `MODEL_NAME` | 模型名称 | - |
| `MODEL_PROVIDER` | 模型提供商 | OpenAI |
| `MODEL_TIMEOUT` | 模型请求超时（秒） | 120 |
| `LLM_SSL_VERIFY` | SSL 证书验证 | true |
| `IS_SENSITIVE` | 敏感信息模式 | false |

## 注意事项

1. **Runner 生命周期**: 所有 TeamAgent 实例必须在 `Runner.start()` 和 `Runner.stop()` 之间运行

2. **环境初始化**: 执行 Python 相关命令前必须完成初始化：
   ```bash
   source .venv/bin/activate
   export PYTHONPATH=.:$PYTHONPATH
   ```

3. **端口分配**: 使用 pyzmq 后端时，确保 Leader 和队友使用不同的端口组合

4. **日志记录**: 团队日志会自动按成员分离，便于调试
