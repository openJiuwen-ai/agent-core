# 信息分层优化：动态 section 改挂 attachment + 入站消息 XML 化

## 元信息
| 项 | 值 |
|---|---|
| 日期 | 2026-06-25 |
| 范围 | 新增 `openjiuwen/agent_teams/inbound_render.py`；`rails/team_policy_rail.py`（动态 section 改挂 attachment + 两个说明 section）；`prompts/sections.py`（`build_team_attachment_notice_section` / `build_team_inbound_tags_section` + HITT section 前缀描述同步）；`prompts/__init__.py`；`i18n.py`（3 个 XML 轨 key）；`agent/coordination/handlers/`（`message.py` / `task_board.py` / `stale_task.py` 改调渲染器） |
| 测试基线 | `test_team_policy_rail.py` + `test_inbound_render.py` + `test_message_handler_render.py` 41 passed |
| Refs | #751 |

## 背景

team system prompt 与入站消息有两个长期痛点，本特性一并解决——但它们是**两类正交机制**，必须分开设计。

**痛点 1 — 动态状态击穿 KV cache。** `TeamPolicyRail` 每轮把 8 个 section `add_section` 进
`SystemPromptBuilder`，其中 3 个是动态的：`team_members`（P:66，spawn/cleanup 即变，真高频）、
`team_hitt`（P:12，human agent 增减才变）、`team_info`（P:65，伪动态，建队后近乎恒定）。
`MtimeSectionCache` 只优化了「重复 DB 查询」，没解决「动态内容仍在 system prompt 里」——
`team_members` 一变，system prompt 该位置之后的所有 token KV cache 全失效；`team_hitt`（P:12）
更靠前，它一变后面 6 个静态 section 跟着遭殃。

**痛点 2 — 入站消息无边界感。** 入站消息当前由 `i18n` 模板硬编码前缀 + 字段糊成一个大字符串，
如 `[收到单播消息] message_id=abc, 来自: leader\n时间: ...\n内容: <对方原话>\n提示: ...`。
对方原话与框架注入的 message_id/时间/提示**全糊在一起，LLM 无法区分「谁说的」「框架补的」**。

## 核心洞察：两类机制，正交分层

- **机制 A — Prompt Attachment**：承载「团队动态状态」（成员/任务/HITT 名单）。跨消息、每轮重新
  收集渲染、注入消息序列尾部、用完即弃。把易变状态移出 system prompt → 前缀稳定。
- **机制 B — User-Message 内嵌 XML 标签**：承载「单条入站消息」。就在那一条 user message 内部，
  用 XML 把「原始消息内容」和「框架注入的元信息/指令」分段包裹。写入对话历史、永久留存。

一句话：**A 管「团队现在是什么样」（横切、易变、尾部、即弃）；B 管「这一条消息是谁发来的、
框架补了什么」（单条、固定、内嵌、留存）。**

## 决策

### 机制 A：动态 3 section → attachment

- `TeamPolicyRail.init(agent)` 取 `agent.prompt_attachment_manager`（`DeepAgent.__init__` 无条件
  建，`deep_agent.py:161`）。`before_model_call(ctx)` 里静态 section 仍 `add_section` 进 builder；
  动态 3 section 经 `bind_context(ctx).add_from_prompt_section(prompt_section=..., kind=<section名>,
  source="agent_teams.team_policy_rail")` 挂 attachment，section 为 None（不再适用）时 `clear_section`。
- `kind` 直接用 section 名（`team_members`/`team_info`/`team_hitt`），渲染时即 `type="..."` 属性，
  attachment 排序键 `(priority, source, section)` 沿用原 priority。
- `MtimeSectionCache` 探针逻辑完全复用——只是产物去向从 builder 换成 attachment_manager。
- session_id 由 writer 从 ctx 解析（`AgentCallbackContext.session`）；解析失败 raise `ValueError`，
  按 harness 既有 rail（`AgentModeRail` / `ExternalMemoryRail`）同款 try/except + warning skip。
- 新增 §5.1 静态说明 section（`team_attachment_notice`，P:17）告诉 LLM：成员/任务/团队状态以
  `<prompt-attachment type=team_members/team_info/team_hitt>` 在消息末尾动态提供、反映当前最新。

### 机制 B：入站消息 XML 化（全量）

- 新增 `inbound_render.py`：纯结构函数 `render_inbound`（`<team-inbound>` + 可选 `<team-note>`）/
  `render_event`（`<team-event>` + 可选 `<team-note>`）。**零 i18n 依赖**——动态数据 + 已本地化的
  文案片段由 handler 传入，结构层只负责 XML 标签 + `html.escape`。`type`/`kind`/`for` 是稳定英文
  契约 token（永不本地化），由 §5.2 说明 section（`team_inbound_tags`，P:18）按名解释。
- handler 全量接线：`MessageHandler._format_message`（普通成员 → `<team-inbound>` + `reply-hint`
  note；human_agent → `for="controller"` + `hitt-silence` note）；`TaskBoardHandler` 的
  task-assigned / plan-approved|rejected / all-done / task-board；`StaleTaskHandler` 的
  stale-claim / stale-pending；`WorkflowHandler` 的 workflow（swarmflow 里程碑播报，leader-only）。
- i18n **新增** 3 个 XML 轨 key（`dispatcher.reply_hint` / `hitt.silence_note` /
  `hitt.assigned_event`），旧糊串模板**保留不动**（`external/format.py` 仍复用）。
- HITT section（`_hitt_section_human_agent_cn/en`）里对旧前缀 `[转发给控制者…]` /
  `[任务指派给控制者]` 的描述同步改写为 `<team-inbound for="controller">` /
  `<team-event kind="task-assigned" for="controller">` + `<team-note kind="hitt-silence">`，
  保证 section 说明与实际渲染一致。

### 关键数据流：本地投递 vs send_message

- **本地 `deliver_input`**（喂自己 LLM）：handler 自己包 XML（`<team-event>` 等）。
- **`send_message`**（进对方 mailbox）：**不自己包**——对方 `_format_message` 会包 `<team-inbound>`，
  自己再包会嵌套。所以 `StaleTaskHandler._leader_nudge_stale_claim` /
  `MemberHandler._nudge_idle_member_with_stale_claims` 保持纯文本，由接收者渲染成 inbound。

## 拒绝的方案

- **双轨 emit 展示 chunk**（设计文档 §4.2 原案：自然语言转前端展示 chunk + XML 喂 LLM）：拒绝。
  `deliver_input` 是喂 LLM 的**输入**，前端 stream 消费的是 agent 的 **output chunk**，从不消费这个
  input——把 input XML 化前端展示不受影响。且 handler 只持有 narrow protocol，拿不到 `stream_queue`。
  双轨 emit 收益为零 + 引入新耦合，直接砍掉，只把 input XML 化。
- **改 i18n 糊模板本身为 XML**：拒绝。`external/format.py` 复用 i18n 文案给外部 agent，改模板会牵连
  它。改为新增独立渲染器 + 新增 XML 轨 key，旧模板原样保留，更 surgical。
- **member 生命周期事件 XML 化**：拒绝。`_handle_leader_member_event` 渲染的 6 个 `member_*` 文本
  最终只 `team_logger.debug`，不 `deliver_input`，不进 LLM——无需渲染。
- **把渲染器放 `prompts/`**：放顶层 `agent_teams/inbound_render.py`（与 `timefmt.py` 同层）。
  `prompts/` 语义是 system-prompt 装配；入站渲染是 LLM-facing 消息文本纯函数，与 timefmt 同类。
- **HITT silence 文案在渲染器自带**：拒绝。运行时文案归 `i18n.py`（架构铁律 4），渲染器零文案。

## 验证

- `tests/unit_tests/agent_teams/test_team_policy_rail.py`：`TestTeamPolicyRailDynamicSections` 重写
  为「动态 section 不在 builder、在 attachment_manager」（`list_by_filter(session_id, section)`），
  静态 section 仍在 builder；缓存命中/失效/mtime 不变意图保留。
- 新增 `test_inbound_render.py`：纯函数 XML 结构 / escape / `for="controller"` / 可选 note / 契约
  token 值。
- 新增 `test_message_handler_render.py`：`_format_message` 普通成员 `<team-inbound>`+reply-hint；
  **HITT 回归**——human_agent → `for="controller"` + `hitt-silence` note + cn load-bearing 关键词
  「严格禁止」「保持静默」（防止 HITT 静默约束被削弱）。
- 合计 41 passed。`policy.build_system_prompt` 老路径核实仅测试在用，机制 A 只动 Rail 路径，R1
  双路径风险解除。

## 已知遗留

- ~~**WorkflowHandler swarmflow 里程碑播报**未 XML 化~~ 已收尾：started / phase / human_prompt /
  human_replied 统一渲染为 `<team-event kind="workflow">`（leader-only 编排进度，单一 kind，子类型
  由 body prose 区分），i18n body 去掉冗余 `[工作流]`/`[Workflow]` 前缀。
- **机制 A 的 KV-cache 实际收益与 LLM 质量**需在真实多轮场景评测（R2）：成员/任务信息从 system
  prompt 顶部移到消息尾部 attachment，§5.1 说明 section 是关键缓冲，需观测「成员是谁」类问题不退化。
- `team_info` 迁移必要性最低（伪动态，`team_dao` 无 update 路径），但本次一并迁移以保持三者一致；
  若后续确认其恒定可考虑留回 system prompt。
