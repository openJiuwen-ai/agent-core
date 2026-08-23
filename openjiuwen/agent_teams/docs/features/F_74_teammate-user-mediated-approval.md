# Teammate 工具审批：leader-mediated → user-mediated

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-08-22 |
| 范围 | `agent_teams/schema/blueprint.py`（`team_approval_mode` 字段）、`core/session/interaction/interactive_input.py`（`InteractiveInput.member_name`）、`agent_teams/runtime/manager.py`（`interact` 路由）、`agent_teams/agent/coordination/handlers/message.py`（DB 兜底修复）、`agent_teams/agent/coordination/handlers/stale_task.py`（`has_pending_interrupt` gate）、`agent_teams/rails/team_permission_rail.py`（decided_by 测试固化）；jiuwenswarm `agents/swarm/providers/member_rails.py`、`agents/swarm/config_specs.py`、`agents/swarm/assembly.py`、`server/runtime/agent_adapter/team_helpers.py`、`server/runtime/agent_adapter/interface.py`；relay-claw `JiuwenPermissionBridge.ts`、`RelayClawAgentService.ts` |
| 测试基线 | agent-core：7（Task1）+92（Task5）+76（Task6）+168（Task7）+6（Task8）；jiuwenswarm：107（Task2）+102（Task3）+15（Task4）；relay-claw：3（Task9）+2（Task10）。全 TDD red→green |
| Refs | — |

## 背景 / 目标

用户要的是「team 成员（teammate）调工具时，审批卡弹给 web 前端用户」，而非现状的
「teammate 调工具 → 发团队消息给 leader → leader 调 `approve_tool`」（leader-mediated，
用户看不到卡）。

### 之前的 leader 审批方向证伪

leader 的提示词（`leader_workflow_predefined.md` / `_autonomous` / `hybrid`）逐字「不要
自己做」——leader 按设计**只协调/派发**（`view_task` / `create_task` / `send_message`），
**不直接调 bash 执行**。所以挂在 leader 的 user-facing rail（`57cd588c3` 给 leader 挂
`PERMISSION_INTERRUPT`）永远不触发（leader 不调 bash）。工具执行发生在 **teammate** 上，
而 teammate 的审批是 leader-mediated（设计如此）。要让用户看到卡，必须改 **teammate**
的审批 surfacing。

### enterprise_dev 没得抄

enterprise_dev 的 teammate 审批**也是 leader-mediated**（`TeamApprovalOrchestrator` +
`approve_tool`，注释逐字「never user-facing permission HITL」）。user-mediated teammate
审批是 **dev-stable 新特性**，无参考实现。

### 纯 subprocess teammate 不支持

`external_cli`（spawn_manager.py）和 `inprocess`（接 fan-out）均 fan-out 到 relay；**仅
`Runner.spawn_agent` 纯子进程分支不转发**（注释「chunks would need a messager-bus
equivalent」，本次不修）。user-mediated 仅覆盖 fan-out 到 relay 的 teammate 形态。

## 决策

1. **开关 `team_approval_mode`（`TeamAgentSpec` 字段，默认 user-mediated）**：紧挨
   `enable_permissions` 新增 `team_approval_mode: Literal["leader-mediated",
   "user-mediated"] = "user-mediated"`。默认 user-mediated（feature 默认开）；
   leader-mediated 为 opt-out（opt-out 路径逐字不变）。

2. **member_name round-trip（全链路）**：user-mediated 时 teammate 的
   `TeamPermissionRail` 拆掉 `TeamApprovalOrchestrator` host → ASK 走基类
   `self.interrupt()`（host=None 与 host 返 `"interrupt"` 都收敛到基类
   `tool_security_rail.py:528`，无隐藏 auto-deny）→ `chat.ask_user_question`。chunk 顶层
   `member_name`（`source_member`）由 `stream_controller._tag_chunk` 在 forward 时打。
   `team_helpers._enrich_teammate_event` 把 `source_member` 提升为
   `parsed["member_name"]`。relay 的 `JiuwenPermissionBridge.ingestAskUserQuestion` 捕获
   `input.payload.member_name` → `BridgeRecordState.memberName` →
   `submitAuthorizationDecision` 透传 → `submitAnswer` 的 chat.send params 回传
   `member_name` → sidecar `interface.py._build_interactive_input_from_answers` 从
   `params.get("member_name")` 缝进 `InteractiveInput.member_name`（sibling 字段，**构造后
   赋值，不改 `__init__`**——该类自定义 `__init__(raw_inputs=_sentinel)` 不吃 kwargs，改
   签名会破坏 10+ 调用点的 `raw_inputs` 语义）→ `manager.interact()` 读
   `payload.member_name` 路由 →
   `entry.agent.team_backend.approve_tool(member_name, tool_call_id, approved, feedback,
   auto_confirm)`（五参映射，对齐 `team.py:585` 签名）→ 复用现成 teammate-resume
   （`on_tool_approval_result` → teammate 自己的 `resume_interrupt`）。

3. **`team_approval_mode` 传递（三者皆快照，不支持热切换）**：
   - **member_rails（选 rail host，jiuwenswarm）**：读 `RailSpec.params`——
     `assembly.py` 把 `spec.team_approval_mode` 作 kwarg 透传给 `config_specs`，
     `config_specs` 烘进 TEAM_PERMISSION `RailSpec.params`，provider 读
     `inp.team_approval_mode`。骑 `DeepAgentSpec.rails` JSON 跨进程存活（spawned teammate
     / cold recovery 都过 seed/JSON）。
   - **team_helpers（放行规则，jiuwenswarm）**：读 enriched `team_spec` 快照
     （`get_swarm_enriched_team_spec`，请求期 enrich 时冻结）。用
     `getattr(team_spec, "team_approval_mode", "user-mediated")` 防御（mock 缺字段时退回
     user-mediated = 与字段默认一致）。
   - **manager（路由，agent-core）**：读 `entry.agent.spec`（activate 期快照，与
     `entry.agent.team_backend` 同访问器路径；`stale_task.py` 读 `blueprint.spec` 同范例）。
   - 三者皆快照：member_rails 装配期、manager activate 期、team_helpers 请求期 enrich。
     运行中改 `team_approval_mode` 三处都不生效（需重建 rail / 重新 activate / 下次 enrich）。

4. **兼容（既有路径逐字不变）**：
   - **auto-approve 跳 teammate 帧（relay）**：auto-approve 分支加 gate
     `!permissionPayload.member_name`——teammate 帧（带 member_name）跳过 auto-approve
     强制走人工卡；leader 帧无 member_name 仍 auto-approve（leader-mediated 逐字不变）。
   - **hide-teammate env 调序（jiuwenswarm）**：`JIUWENSWARM_TEAM_HIDE_TEAMMATE` 的 hide
     过滤从 `parse_stream_chunk` **之前**移到**之后**，并对 `chat.ask_user_question` 豁免
     （hide 只滤 teammate 普通输出帧，不滤审批 ask）。env 默认 OFF。
   - **stale_task gate（agent-core，必需）**：`_check_stale_claimed_tasks` 在 relevant check
     后、idle 检查前加 `if self._round.has_pending_interrupt(): return`——pending interrupt
     挂着时 idle 早已超阈，无 gate 会误注入催促 + 连续 3 窗口后误上报「卡死」。GC 仍跑。
     user-mediated 下有客户端在场时 relay `autoCancel` 会 re-arm 延期，teammate 可能挂远超
     150s，故 gate 从「缓解」升级为「必需」。
   - **DB 兜底修复（agent-core）**：`message.py` 的 `_try_parse_approval_payload` 原返纯
     dict，`team_harness.is_pending_interrupt_resume_valid` 的 `isinstance(InteractiveInput)`
     guard 永远 drop dict → DB 兜底失效（零测试覆盖）。修复：加
     `_approval_to_interactive_input` helper 按 `tool_call_id` 构造 `InteractiveInput`
     （镜像 event 路径 `agent_lifecycle.py:106-113`），再 `resume_interrupt`；加
     `is_pending_interrupt_resume_valid` 幂等 guard（event 先清则 DB 不 re-resume）。不删
     兜底（删则无 event 丢失时的恢复）。

## 拒绝的方案

- **v2 `!isTeamMode` auto-approve gate**：会关掉**整个 team 模式**的 auto-approve，误伤
  leader-mediated（opt-out）路径里 leader 自己的 user-facing ask——与「leader-mediated 逐字不变」
  矛盾。改用 `!payload.member_name` 精准只跳 teammate 帧（teammate 帧带 member_name，leader
  帧不带）。

- **`context.config.get("team_approval_mode")` 读开关**：`SwarmBuildContext.config` 是整份
  config.yaml / tenant 快照，而 `team_approval_mode` 是 `TeamAgentSpec` 字段，在 config 里
  **嵌在 team 段**（不在顶层）→ 生产返 None → 回落 leader-mediated → user-mediated 在生产
  线不触发（测试过只是因 fixture 塞了顶层）。改走 `RailSpec.params`：照 `enable_permissions`
  / `permissions_config` 同款，从 `TeamAgentSpec` 透到 provider，骑 `DeepAgentSpec.rails`
  JSON 跨进程存活。`BuildContext.extras` 也不行——不进 `to_seed`，spawned teammate 跨进程
  会丢 extras → 丢 mode。

- **spec (a′)「零改 `parse_confirm_payload` / caller 传 `TeamPermissionConfirmResponse`」**：
  spec 原假设 user-mediated resume 走 `TeamPermissionConfirmResponse` 命中
  `:138-147` 保留分支。**实测不成立**：`on_tool_approval_result`（`agent_lifecycle.py:106-113`）
  构造的是 **dict**（非 `TeamPermissionConfirmResponse`）→ 命中 `:175-188` dict 分支硬编码
  `"leader"`。`:138-147` 保留分支**根本不被命中**。`ToolApprovalResultEvent` 亦无
  `decided_by` 字段（事件层丢失）。DB 兜底路径（`message.py`）是第二构造点，修复后同样落
  `"leader"`。decided_by 审计误标是 **audit-only 非阻断**（不影响 routing/resume），全链路
  修 defer post-smoke（见「已知遗留」）。

- **teammate 侧超时自动 deny**：需 6 处新 API（`pending_interrupt_age_seconds` 跨
  `AgentRoundController` protocol + 4 impl 层 + `MemberRuntime` protocol + external
  runtime stub）+ interrupt state 加创建时间戳（`core/single_agent` 跨子系统）+ 2 个
  handler hook 点（base + scheduled 各一）+ deny 机制歧义（`approve_tool` 自发 deny 往返 vs
  直接 `resume_interrupt` 合成 denied II）。defer post-smoke——relay 150s `autoCancel` 无
  客户端时发真实 deny（经 (c′) memberName 路由）已覆盖主要场景，唯一缺口是 relay 重启清空
  bridge 内存 map。

## 验证基线

单测按 task 落地，全 TDD red→green：

- **agent-core**：
  - Task 1（基础字段）：`team_approval_mode` 默认 user-mediated / leader-mediated opt-out + `InteractiveInput.member_name`
    默认 None / 构造后赋值 / 既有构造不破 → 7 passed。
  - Task 5（manager 路由）：user-mediated + member_name≠leader 调 `approve_tool`（五参
    kwargs 断言）；member_name==leader/None/leader-mediated fall through `resume_interrupt`
    → 92 passed（88 既有 + 4 新）。
  - Task 6（decided_by）：pin `:138-147` 保留契约（`decided_by="user"` 穿过；`None` →
    `"leader"`）→ 2+74 passed（test-only，零生产代码改）。
  - Task 7（DB 兜底）：`_approval_to_interactive_input` 构造 II + 幂等 guard → 4+164 passed。
  - Task 8（stale gate）：pending interrupt 时不触发催促/卡死上报；无 pending 时现状催促不变
    → 6 passed。
- **jiuwenswarm**：
  - Task 2（member_rails）：user-mediated 建 rail 无 host（`_host.request_permission_confirmation
    is None`）；leader-mediated 带 orchestrator；enrich 透传 `team_approval_mode` 进
    `RailSpec.params` → 107 passed（3 approval-mode + 104 assembly）。
  - Task 3（team_helpers）：(b1) 放行 `source=permission_interrupt` ask、过滤普通 ask；
    (b2) hide ON + user-mediated 时审批 ask 不被丢 → 102 passed（4 新 + 98 既有）。
  - Task 4（interface 缝入）：`params.get("member_name")` 缝进
    `InteractiveInput.member_name`；无 member_name 时为 None；不改 `__init__` → 15 passed
    （2 新 + 4 passthrough + 9 dedup 回归）。
- **relay-claw**：
  - Task 9（member_name round-trip）：`ingestAskUserQuestion` 捕获
    `payload.member_name` → `record.memberName` → `submitAnswer` 回传；无 member_name 时
    graceful omit → 3 passed（build + types exit 0，零回归）。
  - Task 10（auto-approve skip）：teammate 帧（带 member_name）跳过 auto-approve → 人工卡；
    leader 帧（无 member_name）auto-approve 不变 → 2 passed（零回归）。

**smoke 待跑**（用户测）：inprocess team → teammate 调 bash → 弹 AuthorizationCard →
批准 → teammate resume → bash 执行。看：`permissions.log` `before_tool_call` +
`interrupt.ask`；relay `chat.ask_user_question` + `authorization:request` + `chat.send`
（带 member_name）；sidecar `interface.py` 缝入 + `team_backend.approve_tool` 调用；
teammate `resume_interrupt` delivered；bash 结果回。

## 已知遗留

1. **`decided_by` audit 落 `"leader"`（user-mediated）**：user-mediated resume 走 dict 路径
   命中 `parse_confirm_payload` `:175-188` 硬编码 `"leader"`（spec (a′) 假设的
   `TeamPermissionConfirmResponse` `:138-147` 保留分支不被命中）；`ToolApprovalResultEvent`
   无 `decided_by` 字段；DB 兜底路径（Task 7 修复后）是第二构造点同样落 `"leader"`。
   audit-only 非阻断（不影响 routing/resume/smoke）。**全链路修 defer post-smoke**：
   `approve_tool` 加 `decided_by` 参数 → `ToolApprovalResultEvent` 加字段（event schema）
   → `on_tool_approval_result` 读它放进 dict → `parse_confirm_payload` dict 分支读
   `data.get("decided_by","leader")` → manager 传 `user` → `message.py` DB path。
   ~5-6 文件 + event schema。Task 6 已落测试 pin `:138-147` 保留契约作回归基线。

2. **teammate 侧超时 deny 未实现**：relay 重启清空 bridge 内存 map 时，teammate pending
   interrupt 永远无人应答。relay 150s `autoCancel` 无客户端时发真实 deny（经 (c′)
   memberName 路由）已覆盖**有 bridge 记录**的场景；缺口是 bridge 记录本身丢失。defer
   post-smoke（需跨子系统新 API + interrupt state 加时间戳 + deny 机制定夺）。

3. **pre-existing `test_manifest_catalog::test_config_specs_bakes_attribute_params` fail**
   （`57cd588c3` 引入，非本特性）：code profile leader 挂 `PERMISSION_INTERRUPT` 与该 stale
   测试矛盾（同仓 `test_leader_gets_permission_interrupt_when_enable_permissions` 断言相反）。
   基线即 fail，单独处理。
