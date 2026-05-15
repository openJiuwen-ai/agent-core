# HITT — Human in the Team

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-05-12 |
| 范围 | `openjiuwen/agent_teams/{interaction,runtime,agent,tools,prompts,rails}`；`tests/unit_tests/agent_teams/{interaction,runtime,agent,tools}` |
| 测试基线 | `pytest tests/unit_tests/agent_teams/` → 843 passed, 16 skipped |
| Refs | `#751` |

## 背景

让真实人类作为一等成员加入 agent team：可以收发消息、被指派任务、操作团队产物，但所有行为都由对应外部用户驱动，自身**不自主**协作。需要一条端到端的工程闭环把这件事承载下来：

- **Human Agent ≠ User**。User 是上帝视角，可以直接和 Leader 对话、广播或 @ 任意成员；Human Agent 是 Team 里一个普通成员，差别只在它的「自主权」全部由对应的外部用户取代。
- 其它成员看 Human Agent 就是一个有独立 `member_name` 的普通 Team Mate；可以被指派任务、可以收发消息。
- Human Agent 自己**不主动发声、不自主认领任务**——所有行为由对应用户经 `interact()` 驱动；其它成员对它的消息也**不进入它的 LLM 上下文**，而是回调透传给外部用户。

附带把 `#` / `$` / `@` 三种路由前缀的解析**收敛到唯一一处**（`interact(str, ...)` 入口的 `parse_interact_str`），所有下游 inbox / dispatcher / TeamAgent 协调循环都只见已分类的 typed payload，再也不允许沿途二次 regex。

## 端到端使用

### 1. 启用 HITT

```python
from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec
from openjiuwen.agent_teams.schema.team import TeamMemberSpec, TeamRole

spec = TeamAgentSpec(
    agents={"leader": DeepAgentSpec(...)},
    team_name="demo",
    enable_hitt=True,                          # ① spec 层能力总开关
    predefined_members=[                       # ② 声明人类成员
        TeamMemberSpec(
            member_name="alice",
            display_name="Alice",
            role_type=TeamRole.HUMAN_AGENT,
            persona="项目负责人",
        ),
    ],
)
```

- `enable_hitt=True` 是 spec 层 capability ceiling。spec 关时，运行时任何强行打开都会被 fail-fast 拒绝。
- 不写 `predefined_members` 时也可以走 `BuildTeamTool(enable_hitt=true)` 在运行时由 leader 注入默认的 `human_agent` 成员（向后兼容路径）。

### 2. 与运行中的团队交互

唯一入口：`Runner.interact_agent_team(payload, team_name=..., session_id=...)`，其中 `payload` 接 `str | InteractPayload`。

`str` 走以下 grammar（**唯一**解析点 `parse_interact_str`）：

```
input := channel? recipients? body
channel := "# " | "$<name> "      # 默认 "# "
recipients := ("@<name> ")*       # 0..N 个，空格隔开
body := <剩余文本>
```

| 输入示例 | 语义 | 投递结果 |
|---|---|---|
| `"hello"` | 默认 god-view，无 @ | leader DeepAgent 收到 `"hello"` |
| `"# 计划是什么"` | 显式 god-view | leader DeepAgent 收到 `"计划是什么"` |
| `"@dev-1 ship the patch"` | god-view + 单 @ | 总线写一条 `from="user"` → `dev-1` |
| `"# @m1 @m2 standup"` | god-view + 多 @ | 总线写 2 条 direct（每个 target 一条），同一 gate ticket 下 fan-out |
| `"@all heads up"` | god-view + 广播 | 总线广播一次，`from="user"` |
| `"$alice 请总结 design.md"` | 人类成员驱动 avatar | `alice` 的 DeepAgent 收到 `"请总结 design.md"` |
| `"$alice @dev-1 ping me"` | alice 作为发送者，发给 dev-1 | 总线一条 direct，`from="alice"` |
| `"$alice @all 周会推迟"` | alice 广播 | 总线广播一次，`from="alice"` |
| `"#hashtag content"` | 无空格，#非前缀 | 全文作为 god-view body 落给 leader |

**行为规则**：
- 多 @ recipient 在同一 interact ticket 下顺序投递，**首次失败短路**返回，全成功返回最后一条 `message_id`。
- `@all` / `@*` 与其它 @ 同时出现时，广播 supersede（已覆盖全员，多余 @ 忽略）。
- 未匹配任何前缀 → 默认 god-view，原文进 leader。
- 显式 `InteractPayload`（`GodViewMessage` / `OperatorMessage` / `HumanAgentMessage`）跳过 grammar，直接走 dispatch；语义见下表。

### 3. team→user 反向通道

其它成员发给人类成员的消息**不进入 Human Agent 的 LLM 上下文**，而是通过回调透传给外部用户。SDK 注册：

```python
await Runner.register_human_agent_inbound(
    team_name="demo",
    session_id="s1",
    member_name="alice",
    callback=async_callback,   # async (HumanAgentInboundEvent) -> None
)
```

`HumanAgentInboundEvent` 字段：`member_name` / `sender` / `body` / `broadcast` / `message_id` / `timestamp`。SDK 负责把事件投给真实用户（WebSocket / 队列 / UI 渲染层）。传 `callback=None` 清除注册。

## 行为规则

### Human Agent 的输入与输出

- **唯一输入源**：用户经 `interact()` 投递的内容。其它成员的消息**不会**进入 avatar 的 LLM 上下文。
- **不主动发声**：avatar 没有 `send_message` 工具。需要回复团队时由用户输入 `"$<name> @<member> ...`" 显式路由，inbox 用人类成员身份代发。
- **不自主认领任务**：avatar 没有 `claim_task` 工具。任务必须由 leader 经 `update_task(assignee=<human-member>)` 指派；进入 `CLAIMED` 状态后 leader 不能 cancel / 不能 reassign（HITT 任务锁），完成与否完全取决于人类。

### 工具集差集

> 设计原则：Human Agent 配置 = Team Mate / Leader 配置 − Team 相关的全部 Rail、Tool、Skill；剩下的能力（DeepAgent 默认文件 / shell / subagent / 记忆 / 上下文管理）与 Team Mate 完全保持一致。

最终 `HUMAN_AGENT_TOOLS`（在 `create_team_tools(role="human_agent")` 出口被过滤）：

| 工具 | 状态 | 原因 |
|---|---|---|
| `view_task` | ✅ 保留 | 只读 |
| `member_complete_task` | ✅ 保留 | 成员级 self-only：仅完成 `task.assignee == caller_member_name` 的任务 |
| `workspace_meta` | ✅ 由 `TeamToolRail` 在 workspace 启用时统一注入 | 文件操作前置（锁 / 版本） |
| DeepAgent 默认工具 | ✅ 保留 | 文件读写、shell、subagent 等 |
| `send_message` | ❌ 移除 | 发声走 inbox 透传，agent 不得自主发消息 |
| `claim_task` | ❌ 移除 | 认领是自主决策动作 |
| `update_task` | ❌ Leader 专属 | 任务图操作（cancel / reassign / cancel-all） |
| `create_task` / `build_team` / `clean_team` / `spawn_member` / `shutdown_member` / `approve_*` / `list_members` | ❌ 移除 | 全部是 Leader 协调动作 |

> 不在已有 `update_task` 上加 `caller_role` 分支；新工具 `member_complete_task` 是独立工具。设计原则：工具语义不该按 caller 身份分裂。

### Rail 装配差集

`agent_configurator.setup_agent` 在 `ctx.role == HUMAN_AGENT` 时：

| Rail | 是否挂 | 原因 |
|---|---|---|
| `TeamToolRail` | ✅ | 注册工具入口（role 过滤后只有 HUMAN_AGENT_TOOLS） |
| `TeamPolicyRail` | ✅ | 提示词注入；`team_hitt` section 按 HUMAN_AGENT 分支生成专属文案 |
| `TeamWorkspaceRail` | ✅（workspace 启用时）| 文件协作要用 |
| `FirstIterationGate` | ❌ | avatar 没有 task loop，邮箱轮询也禁用 |
| `TeamToolApprovalRail` | ❌ | 工具调用是用户授权的，不应走 leader 审批 |

### 生命周期

- 与 Teammate 完全对齐：`UNSTARTED → BUSY/READY` 流转、支持 `shutdown_member` / `restart`、参与 session checkpoint。
- 不需要 `prompt` / `initial_message`：avatar 起进程后 idle，只在 inbox 入站时才被驱动。
- 协调 dispatcher 对 `HUMAN_AGENT` role **静音**所有自动触发 LLM 的事件：`POLL_TASK` / `POLL_MAILBOX` / `TASK_CLAIMED`（针对自己）/ stale-claim 自纠等，仅保留 `CLEANED`（收尾）和 `MEMBER_CANCELED`（取消）。

### 唯一解析点

| 位置 | 职责 |
|---|---|
| `parse_interact_str`（router.py） | **唯一**解析 `#` / `$` / `@` 前缀的地方 |
| `_dispatch_payload`（runtime/manager.py） | 按 typed payload 类型路由，**不再** parse `@` |
| `HumanAgentInbox.send` | 按 `to` 哑路由：`None`=驱动 avatar / `"all"`,`"*"`=广播 / `<name>`=点对点。**不解析 body** |
| TeamAgent 协调 dispatcher | USER_INPUT 单行 `deliver_input(content)`，没有 mention 处理 |

## 接口契约

### Public API

```python
# 顶层入口
await Runner.interact_agent_team(
    payload: str | InteractPayload,
    *, team_name: str, session_id: str,
) -> DeliverResult

await Runner.register_human_agent_inbound(
    *, team_name: str, session_id: str,
    member_name: str,
    callback: Optional[Callable[[HumanAgentInboundEvent], Awaitable[None]]],
) -> bool
```

### 结构化 Payload（typed dispatch，bypass grammar）

```python
@dataclass(frozen=True, slots=True)
class GodViewMessage:
    body: str                                  # 直发 leader DeepAgent

@dataclass(frozen=True, slots=True)
class OperatorMessage:
    body: str
    target: Optional[str] = None               # None=广播 / member=直发

@dataclass(frozen=True, slots=True)
class HumanAgentMessage:
    body: str
    sender: str                                # 必填，必须是已注册人类成员
    target: Optional[str] = None               # None=驱动 avatar / "*","all"=广播 / member=直发
```

### Spec 层 HITT 开关

| 字段 | 位置 | 语义 |
|---|---|---|
| `TeamAgentSpec.enable_hitt: bool` | spec 层 | capability ceiling；False 时运行时不允许打开 |
| `TeamAgentSpec.predefined_members[*].role_type == TeamRole.HUMAN_AGENT` | spec 层 | 显式声明人类成员（推荐）|
| `BuildTeamTool(enable_hitt=true)` | 运行时 | leader 在建团时启用，注入默认 `human_agent` 保留成员 |

### DeliverResult 失败码

- `"not_active"` — 团队未激活
- `"gate_closed"` — runtime 关闭中
- `"no_team_backend"` — 操作需要 backend 但不可用
- `"human_agent_not_enabled"` — 团队没有任何人类成员
- `"unknown_human_agent"` — 指定的 sender 不是注册过的人类成员
- `"unknown_member:<target>"` — `@target` / explicit `to=<target>` 不在 roster
- `"send_failed:<target>"` / `"broadcast_failed"` — 总线写失败
- `"agent_unavailable"` — avatar 未启动或无 agent_lookup

## 决策

1. **Human Agent 走标准 spawn 路径**：`_spawn_human_agent` 落库改 `MemberStatus.UNSTARTED`，由 leader 的 startup sweep 经 `_on_teammate_created` → `spawn_teammate` 起 DeepAgent。`spawn_manager.build_context_from_db` 从 `TeamBackend.is_human_agent` 推断 role。**不引入新角色、不分裂代码路径**。
2. **不在已有 `update_task` 上加 caller-role self-only 校验**：新增独立的 `member_complete_task` 工具。设计原则：工具语义不该按 caller 身份分裂；要做就拆出独立工具。
3. **`@` / `#` / `$` 解析全部上提到 `interact(str)` 入口**：`parse_interact_str` 是唯一解析点；inbox / dispatcher / TeamAgent 都只见 typed payload。`HumanAgentInbox.send` 退化为按 `to` 哑路由。
4. **多 @ recipients fan-out**：parser 返回 `list[InteractPayload]`，manager 在同一 gate ticket 下顺序投递，首次失败短路。`@all` / `@*` 与其它 @ 共存时广播 supersede。
5. **未知 target 严格失败**：`@unknown` / `to="unknown"` 一律返回 `DeliverResult.failure("unknown_member:<target>")`，不 silent fallback 到 leader。LLM 不该处理「用户写错了名字」。
6. **HumanAgentMessage 的 `target` 语义重新定义**：`None`=驱动 avatar，`"*"` / `"all"`=广播为 sender，`<name>`=点对点。inbox 不再解析 body 里的 `@all`。
7. **`enable_hitt` 是分层 AND 开关**：spec 层 capability ceiling × 运行时 instance override 两层都允许才有人类成员；spec 关时运行时强行打开 fail-fast。
8. **team→user 反向通道是回调钩子**：leader 协调 dispatcher 在 MESSAGE / BROADCAST 事件命中人类成员收件人时，调用 `TeamBackend.get_human_agent_inbound` 注册的回调，喂一个 `HumanAgentInboundEvent`。avatar 的 LLM 永远不消费这些消息。

## 拒绝的方案

**A. 让 Human Agent 没有 DeepAgent，只做消息转发**

Phase 1 的状态。无法满足「操作文件产物、完成任务」的目标——任何需要工具调用的动作都得发明新路径。直接复用 Teammate 的 spawn 通道就能解决，无需平行实现。

**B. 在 `update_task` 上加 `caller_role` 分支让 human agent 也能调**

工具语义按 caller 身份分裂 = 入参文档撒谎 + 错误码语义模糊 + 未来再加角色复杂度爆炸。Linus 风格："好代码没有特殊情况"。拆独立工具更清晰。

**C. 在 `_dispatch_payload` 内部解析 `@`（GodViewMessage("@m1 hi") 等价于 OperatorMessage(target="m1", body="hi")）**

中间方案。一度做了，但用户后来明确：所有 `@` / `#` / `$` 应该在 **interact 入口** 一次性解析完，下游只处理 typed payload。这样 GodViewMessage / OperatorMessage / HumanAgentMessage 的类型契约最清晰——拿到啥就是啥，没有歧义解析。

**D. 给 HumanAgentInbox.send 保留 body 内的 `@` 解析，作为「方便方法」**

违反「解析唯一」原则。两处解析迟早会发散（Phase 1 时 HumanAgentInbox 用裸 `"unknown_member"`，dispatch 用 `"unknown_member:<target>"`——同一概念两套错误码就是症状）。把 inbox 改成完全哑的路由器后，所有错误码统一。

**E. 让 Human Agent 自动消费别人发来的消息（push 到 LLM 上下文）**

破坏「avatar 不自主」的定位。用户希望事件透过到自己手里，自己决定怎么响应。回调钩子 + `HumanAgentInboundEvent` 让 SDK 完全控制 UX。

## 验证

- 单测：`pytest tests/unit_tests/agent_teams/` → 843 passed, 16 skipped
- 新增：
  - `interaction/test_router.py`：19 个 grammar 用例覆盖 `#` / `$` / `@` / 多 @ / `@all` supersede / 边角输入
  - `interaction/test_human_agent_inbox.py`：dumb-routing 风格的 send 测试（驱动 avatar / 广播 / 直发 / `to="unknown"` 严格失败）
  - `runtime/test_dispatch_payload.py`：结构化 payload + str 三前缀 + multi-cast 路径
  - `tools/test_member_complete_task.py`：self-only 校验、caller ≠ assignee 拒绝
  - `agent/test_human_agent_setup.py`：HUMAN_AGENT_TOOLS、Rails 裁剪、role-aware spawn
- 端到端示例：`examples/agent_teams/agent_team_hitt_phase2_e2e.py` 演示 inbox `@` 路由、no-mention 驱动 avatar、leader→user `on_inbound` 回调
- 静态：`make check` ruff format / ruff lint / spelling 全过；pylint 评分维持基线水平

## 已知遗留

- **subprocess 模式的 `agent_lookup` 不可用**：`HumanAgentInbox.send` 在 `to is None` 时需要拿到 avatar 的 TeamAgent 实例直接 `deliver_input`。当前只对 `spawn_mode="inprocess"` 实现了；subprocess 模式返回 `DeliverResult.failure("agent_unavailable")`。跨进程驱动要走 Runner 已有的 follow_up/steer 通道，留作下次。
- **多人类成员 + 同时输入** 的并发语义没有显式测试。`InteractGate` 保证 admit/done 的串行化，但多个不同 sender 的 `$alice ...` / `$bob ...` 并发到达时的行为未做基线对照。
- **`HumanAgentInboundEvent` 的投递语义是 best-effort**：回调抛异常会被记日志吞掉，不会阻塞 dispatcher 也不会重投。生产 SDK 需要自己负责持久化 / 重试。
