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
