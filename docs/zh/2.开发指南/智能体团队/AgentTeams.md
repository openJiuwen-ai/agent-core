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
- **Storage（存储层）**: 持久化团队状态、任务列表和消息（支持 `sqlite`、`memory`）

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
# 启动后台流式任务
stream_task = asyncio.create_task(
    Runner.run_agent_team_streaming(
        agent_team=leader,
        inputs={"query": "初始任务"},
        session="session_id",
    )
)

# 发送后续输入
await leader.interact("补充指令")

# 通过 @mention 向特定队友发送直接消息
await leader.interact("@teammate_member_name 请专注处理数据分析部分")
```

`@member_name message` 语法会将消息直接路由到目标队友，绕过 leader 的 agent 逻辑。发送者在消息表中记录为 `"user"`。

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

# 恢复团队（包含 Leader 和所有队友）
leader = await recover_agent_team(session_id="previous_session_id")

# 继续执行
async for chunk in leader.stream(inputs={"query": "继续任务"}):
    print(chunk)
```

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
