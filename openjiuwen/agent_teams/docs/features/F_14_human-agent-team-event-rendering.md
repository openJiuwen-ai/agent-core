# Human Agent — Role-Aware Team Event Rendering

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-05-16 |
| 范围 | `openjiuwen/agent_teams/i18n.py`、`openjiuwen/agent_teams/agent/coordination/handlers/task_board.py`、`openjiuwen/agent_teams/agent/coordination/handlers/message.py`、`openjiuwen/agent_teams/prompts/sections.py`、`openjiuwen/agent_teams/interaction/CLAUDE.md`、`openjiuwen/agent_teams/agent/coordination/CLAUDE.md`、`tests/unit_tests/agent_teams/test_team_agent_coordination.py` |
| 测试基线 | `pytest tests/unit_tests/agent_teams/test_team_agent_coordination.py tests/unit_tests/agent_teams/test_hitt.py`：99 通过 |
| Refs | `#751` |

## 背景

coordination 把团队事件（task 指派 / message / broadcast）通过 `deliver_input`
喂进每个成员的 harness 时，渲染走的是同一套 teammate 模板：

- `TaskBoardHandler.on_task_claimed` self 分支 → `dispatcher.task_assigned_to_self`
  （文案："请通过 view_task 工具查看任务详情并执行"）
- `MessageHandler._format_message` → `dispatcher.msg_received`
  （文案："如果对方在提问或等待回复，请务必通过 send_message 工具回复"）

两条都假定收件人是个**自主行动**的成员。human-agent 不是 —— 它是某个外部真人在团队里的
avatar，本身不主动发声、不自主认领任务。teammate 文案灌进 avatar 的 harness 会让 LLM
误以为「这条 input 是给我执行/回复的」。

更尴尬的是，`prompts/sections.py` 的 HITT human_agent section 当时写着：

> 团队其它成员发给你的消息**不会**进入你的上下文 —— 系统会自动把它们透传给用户。

但 `MessageHandler._process_unread_messages` 在每个成员（包括 human-agent）的协调
回路里都会调 `_format_message` + `deliver_input` —— 团队消息**就是**会进入 avatar
的上下文，prompt 与代码长期自相矛盾。

任务指派路径同样：避雷的最初设想是 leader 端 fire 一条 `on_inbound` 回调把任务指派
告诉 SDK，但这条「out-of-band 回调 → SDK → 再次回灌 Inbox」的路径相当于「伪装成
外部用户输入再传一道」，多一次 roundtrip 且容易和 Inbox 真正的用户输入混淆。

## 决策

### 1. 流程对齐 teammate：直接走 `deliver_input`

不新增 on_inbound 回调通道；TaskClaimedEvent / MessageEvent / BroadcastEvent 走
现成的 coordination → `deliver_input` 路径。SDK 既有 `MessageHandler._notify_human_agent_inbound`
out-of-band 回调原样保留，做可选 sink，但不在 task 指派路径上复制。

### 2. 渲染差异化：按 `is_human_agent(member_name)` 分模板

新增 `hitt.*` i18n key（cn + en）：

- `hitt.task_assigned_to_self_human`：`[任务指派给控制者] 你被指派了新任务 [{task_id}] {title}。这是给你的控制者看的通知；除非控制者在 Inbox 明确要求你处理，否则不要自动调任何工具。`
- `hitt.msg_received_for_human`：`[转发给控制者的{msg_type}] message_id={message_id}, 来自: {sender}\n内容: {content}\n提示: 这条消息会原样展示给你的控制者；除非控制者明确要求你转告或回复，否则不要主动调 send_message。`

实现点：

- `TaskBoardHandler.on_task_claimed` self 分支按 `backend.is_human_agent(member_name)`
  二分；human-agent 路径额外用 `task_manager.get(task_id)` 拿 title 内联进文案，
  best-effort 异常吞掉（异常纪律对齐 `MessageHandler._notify_human_agent_inbound`）。
- `MessageHandler._format_message` 从 `@staticmethod` 改为 instance method，加
  `is_human_agent: bool` kwarg；`_process_unread_messages` 在循环外一次性算出
  `is_human_agent` 透传下去，避免每条消息查 backend。

### 3. 术语：「控制者 / controller」专属 avatar 背后真人

HITT 体系内同时存在两类真人：

- **用户 / user**：通过 UserInbox / GodView / Operator 与 leader 交互的外部真用户。
- **控制者 / controller**：通过 HumanAgentInbox 操控某个 human-agent avatar 的实体真人。

avatar 的 prompt section + 灌进 harness 的事件文案，全部用「控制者」指代背后真人 ——
区别 leader 那侧的「用户」。两类人各自有独立的 Inbox 通道，文案不混淆才能让 avatar
LLM 正确归因「这是给谁看的通知」。

`prompts/sections.py::_hitt_section_human_agent_cn/en` 全文用「控制者 / controller」
替换原「用户 / user」措辞，并把过时的「不会进入你的上下文 / do not enter your context」
说法换成正确描述：会以 `[转发给控制者的…]` / `[任务指派给控制者]` 前缀进入上下文，
但 avatar 不应自主响应。

## 拒绝的方案

### A. leader-side `on_inbound` 通知任务指派给 SDK，由 SDK 选择如何告诉控制者

最初草案：在 `TaskBoardHandler.on_task_claimed` 检测到 assignee 是 human-agent 时，
leader 进程 fire 一个 `HumanAgentInboundEvent`（body 装任务指派文本）走
`backend.get_human_agent_inbound(recipient)` 回调出去。

代价 & 拒绝原因：

- 这条路径相当于把团队内部事件包装成「外部用户输入」再传一道，等于 avatar harness 收到
  控制者输入和团队事件无法区分。本质是绕开 coordination 把团队事件回灌到 Inbox 入口。
- 增加 SDK 侧需要订阅 on_inbound 才能感知任务指派的隐性依赖；缺这个订阅就什么都看不到。
- 同样的事情走 coordination 的 `deliver_input` + 差异化渲染就能完成 —— 既然 avatar 的
  harness 跑着，let it see the framed input，让 prompt section 约束行为即可。

### B. 给 `OnInbound` 加 `kind: Literal["message","task_assigned"]` 字段或新增独立
`OnTaskAssigned` 回调

如果坚持走 SDK 回调路径，仍然要决定回调载荷如何区分 message vs task assignment。讨论
过给 `HumanAgentInboundEvent` 加 `kind` discriminator，或者干脆拆出
`HumanAgentTaskAssignedEvent` + `register_human_agent_task_assigned` 配套接口。
都被否：A 已经否掉这条路径整体，B 的不同实现只是给 A 涂口红，不解决「绕一道」的本质问题。

### C. 不动 prompt，只改代码 i18n

留着「团队消息不会进入你的上下文」这种自相矛盾的旧文案，反正 avatar LLM 会被新前缀
（`[…给控制者…]`）唤醒「这是给控制者的」识别。

代价：prompt 与运行时不一致是技术债，每次新员工读 HITT prompt 都会困惑「为什么文档写
不进上下文但代码就在灌」。修一次彻底比留坑省事。

## 验证

新增 5 条单测放 `tests/unit_tests/agent_teams/test_team_agent_coordination.py`：

| 用例 | 覆盖 |
|---|---|
| `test_task_claimed_for_self_uses_teammate_template` | 非 human-agent 仍走 `dispatcher.task_assigned_to_self`、含 `view_task`、不含「控制者」 |
| `test_task_claimed_for_self_uses_human_template_when_human_agent` | human-agent self 分支走 `hitt.task_assigned_to_self_human`、内联 title、不含 `view_task` |
| `test_task_claimed_for_human_self_swallows_title_lookup_error` | title 查询抛错时不破坏 dispatch，body 仍带 task_id 前缀 |
| `test_format_message_uses_teammate_template_when_not_human` | 非 human-agent 仍走 `dispatcher.msg_received` |
| `test_format_message_uses_human_template_when_human_agent` | human-agent 走 `hitt.msg_received_for_human`，区分 direct / broadcast 前缀 |

回归：`tests/unit_tests/agent_teams/test_team_agent_coordination.py` + `test_hitt.py` 99
全过，无回归。`test_hitt_section_human_agent_send_message_is_user_driven_*` 等既有 prompt
section 用例对术语调整不敏感，仍然通过（关键词如 `relay channel` / `转发通道` / `不允许`
/ `Never` 在改后文案里都保留）。

## Follow-up：加强禁止性语义（同日补丁）

第一轮落地后实测发现：仅靠"`[…给控制者…]`前缀 + 弱措辞建议"还不足以让 avatar LLM
稳定克制自主行为。模型倾向于看到「看起来像回复的输入」就调 `send_message`，看到
任务指派就想 `member_complete_task`。原因是文案里用「不要 / do not」级别的劝阻语义
还是给模型留了语义灰度。

第二轮把所有面向 avatar 的文案统一升级到「严格禁止 / strictly forbidden」级别，
并覆盖三个动作维度（互斥列举，避免模型钻空子）：

1. **禁止主动回复**——含 `send_message` 调用、含纯文本输出表达意图或承诺；
2. **禁止任何自主工具调用**——含 `member_complete_task` / `claim_task` / 文件 /
   shell / 其它任何工具，无论用什么借口去"回应"或"推进"团队事件；
3. **要求保持静默并等待控制者明确指令**——把"什么时候允许行动"的边界推到极致：
   **只有**控制者在 Inbox 里直接下达指令时才能行动。

落点：

- `i18n.py` 的 `hitt.task_assigned_to_self_human` / `hitt.msg_received_for_human`
  文案改写：在 body 里直接列出"严格禁止"+ 三类禁止行为 +「保持静默」+「等控制者
  Inbox 明确指令」四要素，cn + en 等价。
- `prompts/sections.py` 的 `_hitt_section_human_agent_cn/en`：
  - `## 你的输入` 段的「团队事件通知」条目同步升级；
  - `## 行为准则` 段把"主动发声"和"任务指派通知"两条规则也升级到「严格禁止 / strictly
    forbidden」级别，并补充对纯文本「领命」承诺的禁止。
- 测试侧把 `test_format_message_uses_human_template_when_human_agent` 和
  `test_task_claimed_for_self_uses_human_template_when_human_agent` 里
  「不要主动调 send_message」断言换成更稳的关键词组合：`"严格禁止" + "保持静默"
  + "send_message"`（+ 任务路径加 `"member_complete_task"`）。新增
  `test_hitt_section_human_agent_strictly_forbids_autonomous_behavior_cn/en`
  锁住 prompt section 里的 strict-prohibition 关键词与通知前缀。

### 拒绝过的方案（Follow-up）

- **在 send_message tool 的 `invoke` 里给 caller=human_agent 加静态护栏拒收**：与
  [[feedback_no_role_aware_tool_hacks]] 冲突；工具语义不能按 caller-role 分支。
  控制权放在 prompt + 输入文案，是单一职责设计。
- **改 `deliver_input` 给 avatar 加一个"only accept controller input"中间层**：会
  破坏 teammate 路径的代码共用，且把语义判断硬塞进 coordination 层（不该做决策）。
- **干脆不让团队事件进 avatar harness**：等价于回到旧的「不进上下文 + on_inbound
  绕一道」方案——见上文「拒绝的方案 A」。

## 已知遗留

1. **HITT prompt 仍较长**：human_agent section 又涨了一段（详述 `[…给控制者…]` 前缀
   语义和不要自主调工具的强约束）。下次 prompt 优化时可考虑用更紧凑的「行为表」格式
   替换段落，但目前可读性优先。
2. **broadcast 来源识别**：当 broadcast 落到 avatar 时，prefix 是
   `[转发给控制者的广播消息]`，sender 是发广播的成员；但 avatar 看不到「这条广播
   还顺带发给了哪些其它成员」。如果将来 controller 要做去重 / 集中显示，需要扩展
   元信息。
3. **on_inbound 路径与 deliver_input 路径并行**：当前两条都对 message/broadcast 触发
   （leader 进程 fire on_inbound，human-agent 进程 deliver_input）。SDK 如果只看
   on_inbound，会和 avatar harness 看到的同一条消息分两路出现。下次清理时可以考虑
   只保留一条 —— 但本次保守起见保留向后兼容。

## Follow-up：dispatch 白名单漏改导致团队事件从未送达（2026-05-26）

### 症状

本特性上线后，human-agent avatar 实际**从未**收到 message / broadcast / 任务指派
事件——`MessageHandler._format_message` 的 `is_human_agent` 分支、
`TaskBoardHandler.on_task_claimed` 的 `is_self_human` 分支这两段渲染逻辑是死代码，
`hitt.msg_received_for_human` / `hitt.task_assigned_to_self_human` 从未实际触发。

### 根因

本特性首次落地（见上文「范围」）改了 8 个文件，**唯独漏了
`agent/coordination/dispatcher.py`**。`dispatch()` 里有一段更早的 human-agent 粗筛
白名单（旧设计是「avatar harness 静音团队事件、由 leader 的
`_notify_human_agent_inbound` 回调通知真人」），它在 transport 事件分支只放行
`{CLEANED, MEMBER_SHUTDOWN, MEMBER_CANCELED, STANDBY}` 四类生命周期事件，把
`MESSAGE` / `BROADCAST` / `TASK_CLAIMED` 在进 framework 之前就 `return` 掉了。本特性
新增的 handler 渲染分支因此永远走不到——新旧两套设计直接冲突，而白名单是漏网的旧逻辑。

测试没抓到：本特性新增的 5 个用例全部**直接调 handler 方法**
（`dispatcher.task_board.on_task_claimed(...)` / `handler._format_message(...)`），
绕过了 `dispatch()` 这道粗筛门；而走 `dispatch()` 的 human-agent 用例只覆盖了白名单
放行的生命周期事件（`MEMBER_SHUTDOWN` 等）。两层之间的缝隙正好漏掉了本特性。

### 修复

1. `dispatcher.py` 白名单增加放行 `TeamEvent.MESSAGE` / `TeamEvent.BROADCAST` /
   `TeamEvent.TASK_CLAIMED`，并重写注释说明两组放行（生命周期 vs F_14 团队事件）。
   任务板巡视事件（`TASK_CREATED` / `TASK_UPDATED` / ... → `_nudge_idle_agent`）
   **继续静音**——它们没有 human-agent 渲染分支，会驱使 avatar 自主扫描任务板找活，
   与本特性「avatar 不自主行动」矛盾。
2. `TaskBoardHandler.on_task_claimed`：`is_self_human` 判断提前到入口算一次；human-agent
   收到「指派给别人」的 `TASK_CLAIMED` 时**不** fall-through 到 `on_task_board_event`
   →`_nudge_idle_agent`（avatar 不做任务板巡视），只有指派给自己的认领才 deliver。
3. 补 3 个走 `dispatch()` 的端到端回归用例（`test_team_agent_coordination.py`）：
   `test_human_agent_dispatch_delivers_message_broadcast_and_task_claimed`（三类穿过
   白名单）、`test_human_agent_dispatch_mutes_task_board_survey_events`（巡视类被静音）、
   `test_human_agent_ignores_other_member_task_claim`（指派给别人不 nudge）。

### 拒绝的方案（Follow-up）

- **放行全部 task 事件（含 `TASK_CREATED` 等板变动）**：会让 `_nudge_idle_agent` 把
  「可认领任务列表」灌给 avatar、诱导它自主认领，与本特性核心矛盾，且这些事件没有
  human-agent 渲染分支。只放行「指派给自己」语义明确的 `TASK_CLAIMED`。

### 验证

`pytest tests/unit_tests/agent_teams/test_team_agent_coordination.py test_hitt.py
test_coordination_lifecycle.py test_coordination_loop.py`：129 通过，无回归。

## Follow-up：human-agent 不启动周期 poll timer（2026-05-26）

### 背景

上一个 follow-up 放行 `MESSAGE` / `BROADCAST` 给 human-agent 后，
`MessageHandler.on_message_or_broadcast` 里那句对所有 role 共用的
`await self._poll.resume_polls()` 被激活——human-agent 收到消息会重启周期 poll
timer。顺藤摸瓜发现更根本的问题：`EventBus.start()` 本就**无条件**为所有 role 起
`POLL_MAILBOX` / `POLL_TASK` 两个 timer，**包括 human-agent**；而 human-agent 的这两个
poll inner event 在 dispatch 入口全程被 mute，于是 timer 自启动起就纯空转（每 30s 两次
无用唤醒，被 dispatch 在 `framework.trigger` 之前 return）。`STANDBY → pause_polls` 是
设计者已知此空转后打的补丁，message → `resume_polls` 又会把它撤销。功能无影响（poll 被
mute 兜住），但代码起了一个全程用不到的 timer，不诚实。

### 决策：EventBus 按 role 根本不起 poll timer

`EventBus.__init__` 从 `role` 派生 `_periodic_poll_enabled = role != HUMAN_AGENT`；
`start` 与 `resume_polls` 共用新私有方法 `_start_poll_tasks()`，门控收到这一个点
（顺手消除两处重复的 `create_task`）。human-agent 的 bus 主事件循环照常跑（transport
事件仍送达），只是周期 poll task 不再创建。连带效果：dispatch 的 `POLL_*` human-agent
短路降为纯防御性双保险；`STANDBY` 的 `pause_polls` 对 human-agent 变 no-op（无 timer 可
停），白名单仍保留 `STANDBY` 是为对齐 teammate 路径，不连锁改动。

安全性：human-agent 的 `POLL_MAILBOX` / `POLL_TASK` 本就被 dispatch 完全 mute，不起 timer
等价于现状（功能上零差异），只是省掉空转——不损失任何既有能力。

### 拒绝的方案

- **在 `on_message_or_broadcast` 里对 human-agent 跳过 `resume_polls`**：治标——只挡住
  message 触发的重启，挡不住 `start()` 启动即空转的根本；且在所有 role 共用的 handler
  里塞 caller-role 分支，与 [[feedback_no_role_aware_tool_hacks]] 同源的"共用路径别加
  role hack"精神相悖。根因在 EventBus 起了用不到的 timer，就在 EventBus 治。

### 验证

新增 3 条单测（`test_coordination_loop.py`）：
`test_human_agent_bus_does_not_start_poll_timers`（human-agent start 后无 poll task）、
`test_non_human_bus_starts_poll_timers`（leader / teammate 仍起）、
`test_human_agent_resume_polls_stays_noop`（pause 后 resume 不复活 timer，pause 标志仍
清零）。`pytest test_coordination_loop.py test_coordination_lifecycle.py
test_team_agent_coordination.py test_hitt.py test_persistent_team.py`：143 通过，无回归。
