# HITT 人机交互团队模式

HITT（Human-In-The-Team）允许真实用户以团队成员身份加入 AgentTeams。人类成员拥有稳定的 `member_name`，可以接收 Leader 或 Teammate 的消息，也可以通过 `@member_name` 直接向团队成员发送消息。

HITT 适合需要人工判断、审批、补充现场信息或处理模型无法访问的外部系统等场景。它不同于普通的用户补充输入：人类在团队名册中是 `human_agent` 角色，消息带有明确的成员身份，并通过团队消息总线传递。

## 工作方式

HITT 使用“人类成员 + Avatar”模型：

- 外部用户负责真实判断和输入。
- Human Agent Avatar 是该用户在团队运行时中的代理，负责接入团队消息和非 `@mention` 输入。
- `HumanAgentInbox` 是应用侧输入入口，负责把用户消息路由到团队总线或 Avatar。
- `register_human_agent_inbound()` 注册团队到用户的回调，应用可以在回调中推送 WebSocket、消息队列或 UI 通知。

消息路由规则如下：

| 输入 | 路由结果 |
|------|----------|
| `@team_leader 请确认方案` | 以人类成员身份直接发送给 `team_leader` |
| `@reviewer 请复核结果` | 以人类成员身份直接发送给 `reviewer` |
| 不带 `@mention` 的文本 | 交给该人类成员的 Avatar 处理 |
| 团队成员发给人类成员的消息 | 触发注册的 `on_inbound` 回调 |

## 配置 HITT 团队

`TeamAgentSpec.enable_hitt` 是能力上限。只有 Spec 将其设为 `True`，本次团队实例才可以启用 HITT。框架不会自动创建默认人类成员，应用应显式声明名册，或让 Leader 在运行时调用 `spawn_human_agent`。

```python
from openjiuwen.agent_teams import (
    DeepAgentSpec,
    StorageSpec,
    TeamAgentSpec,
    TeamMemberSpec,
    TeamRole,
)

spec = TeamAgentSpec(
    agents={"leader": DeepAgentSpec()},
    team_name="review_team",
    spawn_mode="inprocess",
    enable_hitt=True,
    predefined_members=[
        TeamMemberSpec(
            member_name="operator",
            display_name="人工审核员",
            role_type=TeamRole.HUMAN_AGENT,
            persona="负责高风险操作审批和最终结果验收",
        ),
    ],
    storage=StorageSpec(type="memory"),
)
```

配置约束：

- `enable_hitt=False` 时声明 `HUMAN_AGENT` 预定义成员会在 `build()` 阶段报错。
- `enable_hitt=True` 但没有预定义人类成员是合法配置，可在运行时动态创建。
- `build_team(enable_hitt=False)` 可以关闭已经开放的能力，但不能把 Spec 中关闭的能力提升为开启。
- `member_name` 必须以小写英文字母开头，只能包含小写字母、数字和连字符。

默认情况下，普通 Teammate 不会在系统提示词中看到具体的人类成员名单。确实需要角色透明时，可以设置：

```python
spec = TeamAgentSpec(
    # 省略其他配置
    enable_hitt=True,
    expose_human_agents_to_teammates=True,
)
```

该开关不影响 Leader 和 Human Agent：Leader 始终可以看到完整名册，Human Agent 也会看到包含自身的名册。

## 接入双向消息

下面的代码展示应用侧的核心接入方式。实际运行时，`leader` 应由 `spec.build()` 获得，团队由 Runner 或 `build_team` 流程完成初始化。

```python
from openjiuwen.agent_teams import HumanAgentInbox
from openjiuwen.agent_teams.interaction import HumanAgentInboundEvent

leader = spec.build()
backend = leader.team_backend
# 由 Host 保存的 operator Avatar 运行时实例。
human_avatar = get_operator_avatar()

async def push_to_ui(event: HumanAgentInboundEvent) -> None:
    await websocket.send_json({
        "member": event.member_name,
        "sender": event.sender,
        "content": event.body,
        "broadcast": event.broadcast,
        "message_id": event.message_id,
    })

backend.register_human_agent_inbound("operator", push_to_ui)

inbox = HumanAgentInbox(
    backend,
    backend.message_manager,
    agent_lookup=lambda name: human_avatar if name == "operator" else None,
)

# 人类成员直接联系 Leader。
await inbox.send("@team_leader 我已批准发布")

# 不带 @mention，交给 operator 对应的 Avatar。
await inbox.send("请汇总当前任务状态")
```

`agent_lookup` 必须能够返回目标人类成员对应的运行中 Avatar；如果应用只使用 `@mention` 直达消息，则消息会直接进入团队总线。

## 动态创建人类成员

当 `enable_hitt=True` 时，Leader 会获得 `spawn_human_agent` 工具，可以在团队已经构建后创建人类成员：

```json
{
  "member_name": "domain-expert",
  "display_name": "领域专家",
  "desc": "负责回答设备规范和现场约束问题"
}
```

动态创建后，应用仍需为该成员注册入站回调，并把该用户的输入交给对应的 `HumanAgentInbox`。

## 异常处理

应用可以针对以下公开异常给出明确提示：

| 异常 | 含义 |
|------|------|
| `HumanAgentNotEnabledError` | 当前团队没有启用 HITT |
| `UnknownHumanAgentError` | 指定的人类成员不存在或尚未注册 |

生产环境中还应处理用户离线、重复消息和异步响应延迟。使用 `message_id` 做幂等键，并把回调事件先写入可靠队列，再推送到 UI。

## 最佳实践

1. 使用业务含义稳定的 `member_name`，不要把临时用户昵称作为路由标识。
2. 默认保持 `expose_human_agents_to_teammates=False`，仅在确有协作需要时公开人类身份。
3. 人类可能长时间不响应，不要让 Teammate 通过轮询消息阻塞整个任务；由 Leader 管理等待、超时和任务改派。
4. 对审批类消息同时记录 `message_id`、发送者、决定和时间，便于审计。
5. HITT 负责“谁参与团队”，工具执行是否允许仍应由 [Team 权限护栏](./Team权限护栏.md) 控制。

完整的无模型接入样例可参考 `tests/system_tests/agent_swarm/agent_team_hitt_phase2_e2e.py`。
