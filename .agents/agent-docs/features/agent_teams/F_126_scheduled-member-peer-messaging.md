---
title: 调度模式：可配置的成员间通信
author: alan_cheng
author_note: 按 Git 最早可追溯的文档新增提交署名；不代表代码 owner
id: F_126_scheduled-member-peer-messaging
type: F
modules:
- openjiuwen/agent_teams
files: []
designs: null
design_note: 历史设计关联待核实，不等同于没有关联设计
author_email: chengshuo5@huawei.com
---

# 调度模式：可配置的成员间通信

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-10-22 |
| 状态 | 已实现 |
| 适用范围 | **仅 `dispatch_mode == "scheduled"` 的非 leader 成员**。leader 与 autonomous 成员本来就有完整 `send_message`，开关对它们无效 |
| 范围 | `schema/blueprint.py`（`TeamAgentSpec.enable_member_messaging`）、`schema/team.py`（`TeamSpec` 镜像字段）、`tools/team.py`（`TeamBackend.member_messaging_enabled`）、`agent/agent_configurator.py`、`tools/tool_message.py`（`ScheduledPeerMessageTool`）、`tools/tool_factory.py`（`_SEND_MESSAGE_CLASS` 新列 + `_send_message_reach`）、`tools/tool_team.py`（`build_team` 披露）、`tools/locales/{cn,en}.py` 与 `descs/*/message/send_message_scheduled_peers.md`、`prompts/sections.py`、`prompts/*/dispatch_scheduled_{leader,teammate}.md` 及 `*_messaging_{open,closed}.md` 片段、`rails/team_policy_rail.py`、`spawn/external_cli_spawn.py`、`external/{descriptor,client}.py`、`external/cli_agent/spawn.py`；文档：`docs/specs/{S_08,S_09,S_12}` |
| 测试基线 | `pytest tests/unit_tests/agent_teams/ --ignore=cli --ignore=observability` → 3802 passed / 36 skipped / 3 xfailed，其中新增 `tools/test_scheduled_member_messaging.py` 20 例 |
| Refs | #751 |

## 背景

调度模式（F_62）把成员的 `send_message` 收窄成 `ReportToLeaderTool`：`to` 只能是角色词
`"leader"` 或 `"user"`，没有多播、没有广播。初衷是"leader 路由一切"，成员间要传的信息要么经
leader 转达，要么写进完成说明 / 产物文件给下游读。

实际使用里，上下游成员对接接口字段、确认产物细节这类高频、低决策含量的沟通也被迫绕 leader 一圈：
leader 多一轮推理、信息多一次转述失真，成员还会因为等转达而停摆。用户诉求是**这条限制应该可以
按团队配置放开**，开关放在实验特性「调度模式」下，与「验证者」平级。

## 决策

1. **一个静态 spec 开关**：`TeamAgentSpec.enable_member_messaging: bool = False`。默认关闭，
   行为与改动前逐字一致。它是 spec 级静态配置，**不进 `build_team` 覆盖**——放不放开成员通信是
   团队的形态决定，由部署方（swarm 的实验开关）定，不是 leader 每次建队临场选的。
   autonomous 下忽略（成员本来就能互发）。
2. **门控信号只有一个，落在 `TeamBackend.member_messaging_enabled()`**，与 `fork_enabled()` 同型。
   三个消费点都读它，保证"工具能做什么"与"提示词说能做什么"同源：
   - 工具工厂选 `send_message` 形态；
   - `TeamPolicyRail` 渲染成员的 dispatch section；
   - `BuildTeamTool.render_for_llm` / framework 建队的 `policy_text` 渲染 leader 的准则披露。
   外部 CLI 成员走两条旁路，同样带上开关：`TeamSpec.enable_member_messaging` →
   `external_cli_spawn._build_external_cli_prompt`（提示词）与 `TeamJoinDescriptor.enable_member_messaging`
   → `ExternalTeamClient` 的 `TeamBackend`（stdio MCP 成员的工具）。Claude / Codex 成员用的是
   进程内 teammate backend，天然拿到同一个值。
3. **形态选择仍是构造期查表**：`_SEND_MESSAGE_CLASS` 的第二维从 `leader | member` 扩成
   `leader | member | member_peers`，由 `_send_message_reach(role, dispatch_mode, agent_team)` 决定
   列。只有 `("scheduled", "member_peers")` 一格指向新类，leader 和 autonomous 的格子不变。
   `invoke` 里零形态分支（F_57 / S_08 不变量 18）。
4. **新形态 `ScheduledPeerMessageTool(SendMessageTool)`**：完整 reach（单播 / 多播 / 广播），
   并**保留 `"leader"` 角色词**——成员在调度模式里一直被教"发给 leader"，开关打开不该让这句话
   失效；`to="leader"` 与 `targets` 里的 `"leader"` 都在投递时经
   `TeamBackend.resolve_leader_member_name()` 翻译成真实名，团队行缺失时软失败（与
   `ReportToLeaderTool` 同语义）。共享的是 `SendMessageTool` 的真实投递行为，所以直接继承；
   为此 `SendMessageTool.__init__` 多了一个 keyword-only `desc_key`（默认 `"send_message"`），
   参数描述按 desc_key 取 `to` / `targets`。
5. **描述独立一份**：`send_message_scheduled_peers.md`（cn / en）。不复用 autonomous 的
   `send_message.md`——那份讲"通知成员领取任务"，而调度模式的交接契约（完成走 `summary` / `note`、
   不发完成汇报）必须保留；也不复用 `send_message_scheduled.md`——那份的核心是"你不能发给其他成员"。
   新描述把点对点限定为**协作沟通**，明确不能借此互相派活或改任务归属，并说明发给未启动 / 休眠 /
   出错成员的消息会由框架把对方拉起来（见下文「后续修复」）。
6. **提示词用 capability 槽 `{{member_messaging}}`**（只在 `dispatch_scheduled_*` 两个模板里）：
   - leader 版两种取值都非空：关 → 原来那条"成员之间不能直接通信"（原文挪进
     `dispatch_scheduled_leader_messaging_closed.md`，逐字不变）；开 → "成员之间可以直接通信，
     但指派与交接仍归你和调度框架，全队周知仍由你广播"。
   - teammate 版关闭时为空：关闭态的规则本来就只写在 `send_message_scheduled.md` 里，模板不重复；
     槽放在文件最后一行，`strip()` 吃掉空值，不留空行洞。开 → "可以直接联系上下游成员，但不要
     互相派活，广播慎用"。
   autonomous 模板没有这个槽，`build_team_dispatch_section` 只对 scheduled 调 `format`。

## 拒绝的方案

- **把开关做成 `build_team` 参数（像 `enable_task_verification` 那样可覆盖）**：成员的工具在
  spawn 时已装配，leader 建队时改它要么改不到已在跑的成员，要么得引入运行时换工具的路径
  （违反"形态只在构造期选"）。而且这是部署方的形态决定，不需要 leader 介入。
- **只放开点对点、保留广播限制**：评估后与用户确认要完整 reach（单播 + 多播 + 广播），与
  autonomous 成员一致；广播的成本靠描述里的成本信号约束，不靠删工具能力。
- **开关打开时直接复用 `SendMessageTool`**：会丢掉 `"leader"` 角色词（成员不知道 leader 的
  member_name 时只能先查名册），也会拿到 autonomous 那份讲认领流程的描述。
- **在 `ReportToLeaderTool` 里按开关放宽白名单**：等于在 `invoke` 里加形态分支，违反 S_08
  不变量 18；schema 的 `enum` 也得跟着条件化，最终两套契约挤在一个类里。
- **成员的 `send_message` 自己拉起休眠 / 未启动的同伴**：`recover_member` / `autostart_unstarted` 是
  leader-only 的拉起漏斗（S_05 不变量 1），成员 backend 不持有 spawn 回调；放开它等于让任意成员
  拉起进程。拉起仍归 leader，见「后续修复」。

## 验证

- 新增 `tests/unit_tests/agent_teams/tools/test_scheduled_member_messaging.py`（20 例）：
  形态选择（关 → `ReportToLeaderTool`；开 → `ScheduledPeerMessageTool`；leader / autonomous 不变）、
  cn / en 描述渲染无残留槽、点对点 / 多播 / 广播投递、`"leader"` 角色词在 `to` 与 `targets` 的解析
  与软失败、未知成员拒绝、leader / teammate dispatch 提示词随开关切换且 autonomous 不受影响、
  `build_team` 披露读 backend 门控、spec / descriptor 透传。
- 既有 `test_tool_variants.py` / `test_tool_message.py` / `test_locales_variants.py` /
  `prompts/` 全部通过，默认关闭时提示词与工具逐字不变。`test_team_policy_rail.py` 的
  `_FakeTeamBackend` 补了 `member_messaging_enabled()`（rail 在 init 时探测它）。

## 已知遗留

- swarm 侧（`modes.team.jiuwen_team.enable_member_messaging` + 实验特性子开关）在 swarm 仓库
  单独落地，依赖本改动合入后 bump core `rev`。

## 后续修复：成员间消息由 leader 代为拉起收件人（2026-10-23）

**问题**：初版落地后，成员发给一个从未启动（UNSTARTED）或已休眠 / 出错（DORMANT / ERROR）的同伴的
消息只落进邮箱，没有任何进程去读——直到 leader 点名或调度器开工才被读到。调度模式下成员通常要等
派到任务才启动，打开成员间通信后这几乎是常态；autonomous 成员互发消息同样受影响。

**决策**：拉起权不下放，仍由 leader 执行。leader 本来就订阅全队消息事件（给发往 `user` 的消息
自动 ack），在 `MessageHandler.on_message_or_broadcast` 里新增 `_wake_peer_recipients`，按 leader
自己 `send_message` 的规则处理**非 leader 发出**的消息：

- 点对点 / 多播（每个收件人一条 `MESSAGE` 事件）：收件人不是 `user` / leader 时调新增的
  `TeamBackend.wake_member(name)`——先读一次状态，UNSTARTED 走 `startup_member`（回调用构造期注入的
  `on_member_started`），ERROR / DORMANT 走 `recover_member`，其余状态不写库直接返回。
- 广播：`autostart_unstarted()`，只启动 UNSTARTED，**不唤醒** DORMANT / ERROR（与 F_118 的
  "广播不唤醒"一致）。
- leader 自己发的消息跳过（工具已处理，调度器交接自有拉起规则）；拉起失败只记日志，不影响后续
  ack 与人类成员通知。被拉起的成员启动后经首次邮箱轮询读到这条消息（F_33），DORMANT 恢复后读完
  未读消息（F_118），无需重投。

两种 dispatch 模式都生效，不挂在 `enable_member_messaging` 上。

**拒绝的方案**：
- 成员 `send_message` 发一种"请 leader 拉起"的新消息 / 事件：多一种协议与工具层的成员专属分支，
  而 leader 已能看到原事件。
- 交给 `TeamScheduler`：它只在调度模式 leader 上构造，autonomous 覆盖不到。

**验证**：新增 `tests/unit_tests/agent_teams/test_peer_message_wake.py`（12 例，真实内存 SQLite +
leader `TeamBackend`）：点对点拉起 UNSTARTED、恢复 DORMANT / ERROR、运行中成员不动、广播只启动
UNSTARTED、leader 自发 / 发往 `user` / 发往 leader 跳过、拉起失败回滚 CAS 且不抛出、
`on_message_or_broadcast` 接线、非 leader backend 的 `wake_member` 恒为 False。
