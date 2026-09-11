# F_111 — 被动人类成员与 tool call 透传协议

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-09-11 |
| 范围 | `openjiuwen/agent_teams/{interaction,runtime,agent,tools,prompts,schema,monitor}`；`tests/unit_tests/agent_teams/{interaction,runtime,agent,tools}` |
| 前置 | F_04（HITT）、F_63（消息模板）、F_20（avatar unread flip）、F_26（member scope 工具构造） |
| 状态 | 已实现 |

## 背景

HITT 家族此前只有一种人类成员：`HUMAN_AGENT`（avatar）——内部跑一个 DeepAgent
harness，由控制者经 inbox 驱动。F_04 曾明确拒绝「让 Human Agent 没有
DeepAgent、只做消息转发」的方案 A，理由是无法操作文件产物、完成任务。

本特性以**tool call 透传协议**补上当年缺失的执行通道后，把方案 A 作为
**新增补充角色**复活（不替代 avatar）：**被动人类成员**（`PASSIVE_HUMAN`），
内部没有任何 harness / LLM / 协调循环。成员的「感知 → 决策 → 行动」循环由
外部真人 + 用户设计的协议承担：

- **出站**（团队→人）：消息与任务事件经 leader 侧 HITT 回调直达 SDK/channel；
- **入站**（人→团队）：两类载荷走同一条 `Runner.interact_agent_team` →
  `_dispatch_payload` 路径——自然语言消息（`HumanAgentMessage`，`$name @target`
  grammar）与结构化工具调用（`HumanAgentToolCall`，直接透传执行）。

「跟现有 human 成员的工具操作完全一样」是设计基线：透传执行器为 passive
构造**绑定其成员名**的 `TeamTaskManager` / `TeamMessageManager`（与 avatar 经
`setup_team_backend` 获得的绑定同构），因此工具内的一切身份守卫
（`member_complete_task` 的 assignee 校验、`verify_task` 的 reviewer 校验、
`send_message` 的 sender 归属）原样生效，无任何旁路。

## 核心模型

被动成员 = **纯 DB 名册身份 + 消息总线地址 + leader 进程内的透传工具执行器，
零成员运行时**：

- 有 `team_member` 行（`role="passive_human"`）、出现在名册、标记 `[human]`、
  可被 `@`、可被指派任务；
- 无进程、无 prompt、无模型、无 EventBus 订阅、不进任何 startup/restart 流程；
- **出生即 `READY`**（合法且 settled，startup sweep 的 UNSTARTED 查询永不命中）。

### 任务端到端

```
leader: create_task(assignee="pm-1")
  └─ TaskClaimedEvent 上总线
       └─ leader 的 TaskBoardHandler.on_task_claimed（foreign-claim 分支）
            └─ 目标是 passive 且 autonomous dispatch → 写框架模板消息
               （meta: passive_task_assigned + task refs，content 为空）
                 └─ MESSAGE 事件 → MessageHandler._notify_human_agent_inbound
                      ├─ fire SDK 回调（HumanAgentInboundEvent，含 meta）
                      └─ leader 代标已读（passive 无 runtime，无人 poll）
真人（外部 channel）: 读通知，实际完成工作
  └─ Runner.interact_agent_team(HumanAgentToolCall(
        sender="pm-1", tool_name="member_complete_task",
        tool_args={"task_id": "t-1"}))
       └─ _dispatch_payload 校验（hitt / sender / passive / 许可面）
            └─ PassiveToolExecutor 按 pm-1 身份执行
                 └─ DeliverResult.tool_success(output=..., data=...) 同步返回
                      └─ is_team_completed 不再被该任务阻塞
```

scheduled dispatch 下零新增：scheduler 的 `_send_as_leader` 派发即写模板消息
给 assignee（F_63），且消息落库不依赖 `auto_start_member` 的 CAS 成功——
passive（READY）的派发通知与 verify-gate 审查请求自动到达。

## 行为规则

| 规则 | 实现 |
|---|---|
| 出生即 READY，无任何运行时 | `TeamBackend.spawn_passive_human`；build_team 预配分支；通用 predefined 循环排除 PASSIVE_HUMAN |
| 恢复/拉起保险（双层互指，F_14 教训） | `RecoveryManager.recover_team` 跳过（保持 READY）；`SpawnManager.spawn_teammate` 顶部结构性拒绝（warning + return None） |
| 可被指派任务（r2 反转 F_04 方案 A 的拒绝理由） | `_validate_assignees` 对 passive **不加**拒绝 |
| HITT 任务锁对 passive 主动生效 | `_is_human_agent_locked` 走泛化后的 `is_live_human_agent`（DAO 家族查询覆盖 `passive_human`）——leader 不可取消/改派/编辑 passive 持有的活跃任务 |
| shutdown 守卫 + 直落 | live-human 活跃任务守卫在前（拒关，force 可过）；通过后 `READY→SHUTDOWN` 直接落终态（无进程消费 MEMBER_SHUTDOWN，两阶段会卡 SHUTDOWN_REQUESTED 破坏 settled），仍发布 `MemberShutdownEvent` |
| cancel-all skip | `skip_assignees=live_human_agent_names()` 泛化后含 passive |
| 裸消息输入严格失败 | `$passive hi`（无 @）→ `passive_member_no_avatar`（通信模式重设计前占位） |
| 任务停滞无人自催 | 成员自催 sweep 是循环内 self-only，passive 无循环——leader 只能 `send_message` 催促或 force shutdown 收回（与 avatar 一致的既有 HITT 语义） |

## 透传协议契约

### 入站 payload

```python
HumanAgentToolCall(
    sender: str,      # passive human 成员名
    tool_name: str,   # 如 "member_complete_task"
    tool_args: dict,  # 与工具 input_params 一一对应
)
```

经 `Runner.interact_agent_team(payload, team_name=..., session_id=...)` 进入；
不做 str grammar（SDK 侧结构化构造）。`router.resolve_targets` 对其原样透传
（`_named_target` 只认 Operator/HumanAgentMessage）。

### 许可面 `PASSIVE_HUMAN_TOOLS`

`HUMAN_AGENT_TOOLS | {"claim_task"}`：view_task / member_complete_task /
verify_task / send_message / claim_task。claim_task 是对 avatar 的**有意差异**
——avatar 不自主 claim 是 LLM 行为治理，而 passive 的每次透传本身就是操作者的
显式意图。scheduled dispatch 下执行器减去 `claim_task`、`send_message` 换
report-to-leader 形态（对齐 `MEMBER_ONLY_TOOLS_SCHEDULED` 惯例）。

许可面不经过 `create_team_tools`（passive 没有 harness，没有 LLM 工具表）；
执行器直接构造工具实例（先例：scheduler 的 `reviewer_tm`、F_26 external
client）。

### 同步返回

`DeliverResult` 新增 `output: Optional[str]`（工具 `map_result` 文本）与
`data: Optional[dict]`（`ToolOutput.data`），工厂 `tool_success(output=, data=)`。
外部协议请求-响应闭环一次往返。

### 失败码（稳定 token）

| token | 含义 |
|---|---|
| `passive_member_no_avatar` | `$passive` 裸消息输入（无 @ 目标） |
| `tool_passthrough_avatar_not_supported` | avatar 型 human_agent 的 sender 传入 tool call（治理链完整性：avatar 的 LLM 工具循环 + plan 审批链是唯一治理路径） |
| `unknown_tool:<name>` / `tool_not_permitted:<name>` | 工具名不在许可面 |
| `unknown_human_agent` / `human_agent_not_enabled` | sender 非人类成员 / HITT 未开 |
| `tool_passthrough_no_runtime` | 结构上拿不到 ActiveTeam entry（防御） |

工具执行失败：`DeliverResult.failure(reason=<工具错误文本>)`。

### 出站 `HumanAgentInboundEvent`

新增可选 `meta: Optional[dict]` 字段——模板消息行的框架 meta（template key +
refs）原样透传，外部协议可机器可读地识别「任务指派通知 + task_id」而无需解析
渲染文本；普通消息为 `None`。

## 决策

1. **任务指派解禁**（反转 r1 的 v1 拒绝）：透传 `member_complete_task` 使任务
   不再搁浅，F_04 方案 A 的否决理由失效。
2. **透传仅 passive**：avatar 传入即拒（见失败码表）。
3. **任务通知统一走消息通道**：autonomous 模式由 leader 侧 TaskBoardHandler
   转写为 F_63 模板消息；不新增出站事件类型——投递 / 代标 / 完成判定全复用。
   只有 leader 写（每成员的 handler 都收到同一事件，无条件写会按协调循环数
   重复投递）。
4. **已读代标是 passive 专属**：avatar 绝不代标（F_20：avatar 靠自身 poll 后
   flip）；passive 无 runtime 无人 poll，不代标则 `is_team_completed` 的
   `has_unread_messages` 永真。代标无条件（镜像 `_ack_user_bound_message` 对
   `user` 伪成员的 ack）：回调投递即消费，无论是否注册回调。
5. **能力开关复用 `enable_hitt`**：passive 属 HITT 家族，同一 spec 层 ceiling
   （`_validate_hitt_consistency` 覆盖 PASSIVE_HUMAN 预配）。
6. **`[human]` 标记覆盖两 flavor**：同伴需知道这是真人（按人类节奏沟通）。

## 拒绝的方案

- **为 passive 造一个轻量 harness**：引入第三种运行时形态，恢复/拉起/审批链
  全要开洞；透传执行器（纯工具实例，无进程）以 1/N 的复杂度达成同等能力。
- **tool call 转换为自然语言给外部、回复再解析回工具**：用户明确要求直接
  透传而非转换——结构化往返保真且协议可机器校验。
- **avatar 也开放透传**：绕过 plan_mode 审批链（approve_tool）与 LLM 行为
  治理，治理面出现旁路。
- **新增出站事件类型（任务事件直达回调）**：消息通道已具备投递 / 代标 /
  unread 判定的全部机制，模板消息一次建设覆盖所有任务事件。

## 验证

- `tests/unit_tests/agent_teams/runtime/test_dispatch_payload.py`：裸输入失败、
  透传成功 / 工具失败 / avatar 拒绝 / 未知 sender 拒绝 / hitt 关闭拒绝
- `tests/unit_tests/agent_teams/interaction/test_passive_tool_executor.py`：
  身份绑定（本人任务完成 / 他人任务拒绝）、send_message 落总线 from=passive、
  claim、许可面（autonomous / scheduled 差异）、未知工具、never-raises
- `tests/unit_tests/agent_teams/test_hitt.py`：spec 校验、spawn READY、预配注册、
  指派成功（r2 锚点）、任务锁 / cancel-all / shutdown 守卫与直落
- `tests/unit_tests/agent_teams/test_team_agent_coordination.py`：回调 + 代标 +
  meta 透传、无回调也代标、avatar 不代标（F_20 回归）、任务指派通知
  （leader 写 / teammate 不写）
- `pytest tests/unit_tests/agent_teams/` 全量 + `make check`

## 已知遗留

- 裸消息输入（`$passive` 不带 @）的路由语义——留给通信模式重设计
  （@ 变通知触发器、全员可见）。
- passive 任务停滞时无人自催（与 avatar 一致），leader 需 send_message 催促
  或 force shutdown 后收回任务。
- 透传面仅覆盖 team tools；native harness 工具（read_file / bash 等）不在
  v1 范围（文件产物协作走 avatar）。扩大许可面只需改 `PASSIVE_HUMAN_TOOLS`
  常量 + 执行器构造分支 + 补测试。
