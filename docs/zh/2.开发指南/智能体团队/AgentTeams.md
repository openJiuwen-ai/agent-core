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
- **Transport（传输层）**: 负责智能体间消息传递（支持 `inprocess`、`pyzmq`）
- **Storage（存储层）**: 持久化团队状态、任务列表和消息（支持 `sqlite`、`postgresql`、`memory`）

## 快速开始

```python
import asyncio
import yaml
from openjiuwen.agent_teams import TeamAgentSpec
from openjiuwen.core.runner import Runner

async def main():
    # 初始化 Runner
    await Runner.start()

    # 从 YAML 配置加载团队规格
    with open("config.yaml") as f:
        cfg = yaml.safe_load(f)
    
    spec = TeamAgentSpec.model_validate(cfg)
    leader = spec.build()

    # 流式执行
    async for chunk in Runner.run_agent_team_streaming(
        agent_team=leader,
        inputs={"query": "创建一个 3 人团队，讨论人工智能的未来发展"},
        session="demo_session",
    ):
        print(chunk, end="", flush=True)

    await Runner.stop()

asyncio.run(main())
```

### 配置文件示例（config.yaml）

```yaml
agents:
  leader:
    model:
      model_client_config:
        client_provider: "${MODEL_PROVIDER}"
        api_key: "${API_KEY}"
        api_base: "${API_BASE}"
        timeout: 120
      model_request_config:
        model: "${MODEL_NAME}"
        temperature: 0.2
        top_p: 0.9
    max_iterations: 200
    completion_timeout: 600.0
  teammate:
    model:
      model_client_config:
        client_provider: "${MODEL_PROVIDER}"
        api_key: "${API_KEY}"
        api_base: "${API_BASE}"
        timeout: 120
      model_request_config:
        model: "${MODEL_NAME}"
        temperature: 0.2
        top_p: 0.9
    max_iterations: 200
    completion_timeout: 600.0
    
transport:
  type: inprocess

team_name: demo_team
lifecycle: temporary
teammate_mode: build_mode
spawn_mode: inprocess  # 使用 inprocess 模式，队友在同一进程内运行
leader:
  member_name: team_leader
  display_name: Team Leader
  persona: 项目管理专家
```

### 存储配置（SQLite / PostgreSQL）

```yaml
# SQLite（本地文件）
storage:
  type: sqlite
  params:
    connection_string: ./team_data/team.db

# PostgreSQL（推荐分布式部署）
storage:
  type: postgresql
  params:
    connection_string: postgresql+asyncpg://user:password@host:5432/agent_team
```

说明：
- `postgresql` 模式使用同一个 `connection_string` 字段；
- 运行前需确保 PostgreSQL 服务已启动且可访问；
- 若使用可选依赖安装，请包含 `postgres` extra（`asyncpg`）。

## 配置详解

以上样例涉及的主要配置项说明：

| 配置项 | 说明 |
|--------|------|
| `agents` | 按角色配置 DeepAgent，必须包含 `leader`，`teammate` 可选 |
| `team_name` | 团队名称 |
| `lifecycle` | `temporary`（临时）或 `persistent`（持久） |
| `teammate_mode` | `build_mode`（直接完成）或 `plan_mode`（需审批） |
| `spawn_mode` | `inprocess`（同进程）或 `process`（子进程） |
| `leader` | Leader 身份配置（member_name、display_name、persona） |

其他配置项（transport、storage、predefined_members、workspace 等）详见 [API 文档](../API文档/openjiuwen.agent_teams/agent_teams.md)。

## 执行模式

### 流式执行（推荐）

```python
async for chunk in Runner.run_agent_team_streaming(
    agent_team=leader,
    inputs={"query": "任务描述"},
    session="session_id",
):
    print(chunk, end="", flush=True)
```

### 交互模式

```python
# run_agent_team_streaming() 返回异步迭代器，
# 不能直接传给 create_task()，需要先包装成协程。
async def consume_stream():
    async for chunk in Runner.run_agent_team_streaming(
        agent_team=leader,
        inputs={"query": "初始任务"},
        session="session_id",
    ):
        print(chunk, end="", flush=True)

stream_task = asyncio.create_task(consume_stream())

# 发送后续输入
await leader.interact("补充指令")

# 通过 @mention 向特定队友发送直接消息
await leader.interact("@teammate_member_name 请专注处理数据分析部分")
```

`@member_name message` 语法会将消息直接路由到目标队友，绕过 leader 的 agent 逻辑。发送者在消息表中记录为 `"user"`。

## 恢复与持久化

持久团队（`lifecycle: persistent`）的状态由 session checkpoint 与团队存储共同承载。**所有恢复路径都通过同一个入口触发——把 spec 与目标 session 交给 `Runner.run_agent_team_streaming` 即可**。`runtime` 子系统会根据内存池是否已有该 team 实例、目标 session 是否带持久化数据，自动选择对应分支：

| 场景 | runtime 自动选择 |
|---|---|
| 同进程切到新 session | `new_team_in_session_warm`（复用内存中的 Leader） |
| 切回已持久化过的同名 session | `warm_recover`（复用 Leader，重绑 session checkpoint） |
| 进程重启后冷启动 | `cold_recover`（从 session bucket 重建 Leader，再拉起 Teammates） |
| 暂停后续运行 | `resume_from_pause`（同一 Leader、同一 session） |

```python
# 任意场景统一入口：spec + 目标 session_id，runtime 自己识别冷/热路径
async for chunk in Runner.run_agent_team_streaming(
    agent_team=spec,
    inputs={"query": "下一个任务"},
    session="round_2",
):
    print(chunk)
```

需要绕过 Runner 直接拿 leader 实例（脚本化运维场景）时，可使用 `TeamAgent.recover_from_session(session, team_name, runtime_spec=spec)` + `await agent.recover_team()` 这条低层路径——常规应用走 Runner 即可。

## 健康检查与自动恢复

AgentTeams 内置健康检查机制：

1. Leader 定期检查队友进程状态
2. 检测到队友不健康时自动重启
   - 最多重试 3 次，采用指数退避策略
3. 重启成功后发布 `MemberRestartedEvent` 事件
4. 重启失败后标记队友为 ERROR 状态

## 注意事项

1. **Runner 生命周期**: 所有 TeamAgent 实例必须在 `Runner.start()` 和 `Runner.stop()` 之间运行

2. **环境初始化**: 执行 Python 相关命令前必须完成初始化：
   ```bash
   source .venv/bin/activate
   export PYTHONPATH=.:$PYTHONPATH
   ```

3. **日志记录**: 团队日志会自动按成员分离，便于调试
