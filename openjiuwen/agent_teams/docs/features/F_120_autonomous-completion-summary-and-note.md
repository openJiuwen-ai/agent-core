# 自主模式接入完成摘要与详情，两种模式统一取消完成汇报

## 元信息
| 项 | 值 |
|---|---|
| 日期 | 2026-10-14 |
| 状态 | 已实施 |
| 依赖 | F_119（`summary` 列与迁移、`completion_summary`、`view_task(action='note')`、改派 / 重置清空） |
| 范围 | `prompts/{cn,en}/teammate_policy.md`（共用）、`tools/tool_task.py`（`ClaimTaskTool`）、`agent/coordination/handlers/task_board.py`、`external/format.py`、`i18n.py`、`prompts/{cn,en}/`、`tools/locales/descs/{cn,en}/task/claim_task*.md`；spec：`S_08`（`claim_task` 契约）、`S_03`（看板唤醒内容） |
| 测试基线 | `tests/unit_tests/agent_teams`：3612 passed / 36 skipped / 3 xfailed（排除既有不稳定用例 `test_member_activity.py::test_observe_member_status_emits_after_the_debounce`） |

## 背景

F_119 为调度模式引入了任务行上的完成说明双字段：`summary`（一句话摘要）+ `note`（完整详情），
并让 leader 摘要、上游列表等轻量渲染点只用摘要。自主模式（`dispatch_mode == "autonomous"`，
成员从看板自主认领）目前完全没有用上这套信息：

- **写不进去**：自主成员只有 `claim_task`（`task_id` + `status`），`status=completed` 调
  `task_manager.complete(task_id)` 时不带任何说明（`tool_task.py:1265-1276`）。自主模式下唯一会写
  `note` 的是真人分身调用 `member_complete_task`。
- **leader 看不到刚完成了什么**：任务完成触发 `TASK_COMPLETED`，leader 被唤醒后拿到的是"未完成
  任务看板"（`_nudge_idle_agent`，`task_board.py:493-555`）——刚完成的任务作为终态被过滤掉了，
  既没有 id 也没有结果。最后一个任务完成时 leader 只收到 `dispatcher.all_done_*`，同样不知道
  最后完成了什么。完成详情全靠成员 `send_message` 汇报（`teammate_policy.md` 第 5 步）。
- **下游拿不到上游交付**：上游完成解锁下游时，下游被唤醒拿到的是可认领任务列表
  （`dispatcher.teammate_task_list` + `render_task_line`），不含任何上游信息；`view_task(get)`
  只给 `Blocked by` / `Blocks` 的 id。下游要么靠上游成员主动 `send_message`
  （`teammate_policy.md`"完成后及时通知依赖方"），要么自己逐个 `view_task` 去翻。

## 目标

1. 自主成员完成任务时写入 `summary` / `note`（与调度模式同一套字段、同一套写入语义）。
2. leader 在任务完成的那次唤醒里直接看到"刚完成了什么"（摘要 + 详情提示），包括最后一个任务。
3. 下游在**认领任务的那一刻**看到上游交付的摘要，等价于调度模式开工通知里的"上下游"一节。
4. 完成交接由任务行承载后，成员**不再用 `send_message` 汇报完成、也不再"通知依赖方"**，与调度模式
   （F_119）统一；`send_message` 只保留给需要 leader 知道或决定的事项（见 D5）。

非目标：

- 自主模式的验收闸（`reviewer` / `in_review`）。自主模式的 `update_task` / `create_task` 不接受
  reviewer 字段（`tool_task.py:942-954`），本 feature 不改变这一点。
- 不改变自主模式的唤醒策略（谁被哪些事件唤醒、steer 与否）。

## 设计

### D1 `claim_task` 完成时可写 `summary` / `note`

- `ClaimTaskTool` input schema 新增可选 `summary`、`note`。
- 仅 `status=completed` 时生效，透传给 `task_manager.complete(task_id, summary=..., note=...)`
  （F_119 已扩展的签名）；写入语义沿用 F_119：传了就覆盖、不传保留、完整存储、改派 / 重置清空。
- `status=claimed` 时传入这两个字段：忽略，并在结果里提示"仅完成时生效"（不报错，避免认领被打断）。
- 选择扩展 `claim_task` 而不是把 `member_complete_task` 加进自主成员工具集：自主模式下完成本就是
  `claim_task` 的一个状态；同时挂两个都能"完成"的工具会让模型困惑，也要改工具权限表与描述两处。

### D2 leader 在完成唤醒里看到"刚完成"

- `TaskBoardHandler.on_task_board_event` 把触发事件传给 `_nudge_idle_agent`；当触发事件属于
  `_LEADER_COMPLETION_EVENTS`（`TASK_COMPLETED`，以及验收通过的 `TASK_VERIFIED`——任务带 reviewer
  时完成走的是它）且接收方是 leader 时，在看板之前渲染一段"刚完成"：

  ```
  <team-event kind="task-board">
  刚完成：
  - [<id>] <标题> → <承担者>：<completion_summary(task)>
    详细完成说明用 view_task(action='note', task_id='<id>') 查看。
  未完成任务看板：
  - ...
  </team-event>
  ```

  - 摘要为空时只列 `[<id>] <标题> → <承担者>`；"详细完成说明"一行仅在有 `note` 或摘要被截断时
    出现（与 F_119 / D2 同规则）。
  - 任务行在渲染时现查（事件只带 `task_id`，与 F_119 "note 不进事件"一致）。
- **全部完成**（看板为空，走 `dispatcher.all_done_*`）时同样先列"刚完成"，再给收尾提示——
  否则最后一个任务的结果会丢失。
- 只对 leader 生效；teammate 不因 `TASK_COMPLETED` 被唤醒（`_TEAMMATE_NUDGE_EVENTS` 不变）。
- 轮询触发（`from_poll=True`）不带"刚完成"段：没有触发事件，也避免重复播报。
- i18n 新增 `dispatcher.leader_just_completed`、`dispatcher.leader_just_completed_line`、
  `dispatcher.leader_just_completed_summary`、`dispatcher.leader_just_completed_note_hint`（中英文）；
  渲染在 `task_board._just_completed_block`。
- 调度模式的 `ScheduledTaskBoardHandler` 覆写了 `on_task_board_event`，不经过这里——调度模式由
  调度器终态摘要承担同一职责（F_119 / D2）。

### D3 下游认领时看到上游摘要

- `claim_task(status=claimed)` 成功后，`render_for_llm` 追加"上游交付"一段（标题与提示文案在工具
  locale：`claim_task.upstream_header` / `claim_task.upstream_note_hint`，随工具的 Translator 语言）：本任务每个已完成
  上游一行 `task_id · 标题 · 承担者 · <completion_summary>`，末尾一行
  `view_task(action='note', task_id=...)` 提示。无上游时不加。
- "上游"只指**邻接任务**（直接依赖，一跳），与 F_119 / D3 相同，不递归到上游的上游。
- 数据来源复用 F_119 的 `message_template.task_links`，行由新抽出的 `message_template.upstream_lines`
  生成（`upstream_brief` 也改为调用它，两种模式行格式完全一致）；`ClaimTaskTool.invoke` 在认领成功后
  多查一次依赖，渲染好的行放进 `data["upstream"]`。
- 选择"认领时"而不是"被唤醒时"：认领前成员看到的是可认领列表，列表里每项都附上游会让看板变吵，
  且多数任务最终不会被它认领；认领那一刻才是它真正需要上游信息的时刻，对应调度模式的"开工"。
- `view_task(get)` 的详情视图同时增加"上游"小节（同一行格式），供认领后回看。

### D4 提示词与工具描述（只改自主模式专属的位置）

| 位置 | 改动 |
|---|---|
| `prompts/*/dispatch_autonomous_teammate.md` | 第 4 步改为 `claim_task(status=completed, summary=..., note=...)`：`summary` 一句话说明交付了什么、主产物在哪，`note` 写完整的产物路径与关键决策；认领结果里会附上游交付摘要 |
| `prompts/*/dispatch_autonomous_leader.md` | 说明任务完成时看板唤醒会附"刚完成"摘要，需要详情用 `view_task(action='note')` |
| `tools/locales/descs/*/task/claim_task.md` | 新增 `summary` / `note` 参数说明（仅完成时生效），示例补一条带摘要的完成调用；说明认领结果附上游摘要 |
| `i18n.py` `dispatcher.task_plan_approved_to_self` | 文案里的 `claim_task(status='completed')` 补"并写上 summary / note" |

### D5 两种模式都取消 `send_message` 完成汇报

D2 / D3 落地后，leader 在完成那次唤醒里已拿到摘要、下游在认领时已拿到上游摘要，`teammate_policy.md`
"完成后用 `send_message` 向 Leader 汇报"与"完成后及时通知依赖方"成为重复通道，且多唤醒 leader 一轮
（成员汇报与看板唤醒是两次独立输入，先后不定，任一顺序都是重复）。自主模式与调度模式统一取消。

**统一措辞**（两个 dispatch 段共用的意图）：

> 完成交接由任务的 `summary` / `note` 承载，不要用 `send_message` 汇报完成。只有存在需要 leader
> 知道或决定的事项（偏离计划、范围变化、遗留风险、阻塞）时才发消息，且只写这些事项本身。

**`teammate_policy.md`（共用，中英文）**：

| 位置 | 原文 | 改为 |
|---|---|---|
| 核心职责 5 | "关键汇报：向 Leader 汇报完成结果和重要决策，而非每个执行细节" | "关键沟通：完成结果写进任务的 `summary` / `note`；只把需要 Leader 知道或决定的事项（偏离计划、范围变化、遗留风险、阻塞）发给 Leader，而非每个执行细节" |
| 工作流程 5 | "用 `send_message` 向 Leader 发送完成汇报（…）。汇报一次即结束…" | "按《任务下发与获取》一节的约定完成交接——完成说明写进任务的 `summary` / `note`，**不要**用 `send_message` 汇报完成。有需要 Leader 知道或决定的事项时才发消息，只写事项本身。不要为收到的致谢/确认消息再次回复客套话" |
| 协作精神 | "主动关注上下游任务，完成后及时通知依赖方" | "主动关注上下游任务；你的交付通过任务的 `summary` / `note` 交给依赖方，写清产物在哪、关键决策是什么" |

"严禁用普通文本输出传递消息"一条不变（它约束的是"要发的消息必须走 `send_message`"，不是"必须
汇报"）。

**dispatch 段**：

- `dispatch_autonomous_teammate.md`：第 4 步（D4）之后补统一措辞。
- `dispatch_scheduled_teammate.md`：F_119 / D8 中"注明本条优先于通用工作流程'完成后汇报'一步"
  的说法随本节失效——通用流程已不再要求汇报，改为与自主段相同的统一措辞即可。

**leader 侧**：

- `task_state_autonomous.md`："质量把关靠……你在成员汇报成果时亲自审阅、必要时用 `send_message`
  要求返工"改为"……你在看板唤醒的'刚完成'段读到摘要时审阅，需要时用 `view_task(action='note')`
  看详情，必要时用 `send_message` 要求返工"。
- `dispatch_autonomous_leader.md`："等待成员通知"的表述改为"任务完成时看板唤醒会附'刚完成'摘要"
  （与 D4 该行合并）。

## 数据结构

无新增存储：复用 F_119 的 `summary` / `note` 列与清空规则。`ClaimTaskTool` 结果在认领时可能新增
`data["upstream"]`（`list[str]`，已渲染的上游行）与 `data["fields_ignored"]`（认领时传了
`summary` / `note`）；`view_task(get)` 结果可能新增 `data["upstream"]`（同格式）。均仅用于渲染。

## 决策

1. **复用 F_119 的字段与渲染函数**：两种模式同一套完成说明，`completion_summary` / `upstream_brief`
   一处实现，行格式一致。
2. **写入走 `claim_task(status=completed)`**：不引入第二个完成工具。
3. **leader 的"刚完成"挂在既有看板唤醒上**：不新增唤醒、不改唤醒策略，只给已经发生的那次唤醒补上
   它缺的内容；全部完成时同样补上。
4. **上游摘要在认领时给**：认领是自主模式里"开工"的时刻，信息到达时机与调度模式开工通知对齐。
5. **两种模式都取消完成汇报**：完成交接统一由任务行承载；`send_message` 只承载需要 leader 知道或
   决定的事项。避免同一件事两次唤醒 leader，也避免模型把"完成后汇报"按字面执行、重复转述摘要。

## 拒绝的方案

- **把 `member_complete_task` 加入自主成员工具集**：两个完成入口，模型易混；`claim_task` 的
  `completed` 状态本就承担完成。
- **在可认领列表的每个任务后附上游摘要**：看板随任务数线性膨胀，多数任务不会被该成员认领；
  teammate 看板还会被 `_drop_superseded` 等机制反复刷新，放大开销。
- **为"刚完成"单独发一条事件 / 唤醒**：leader 本来就会因 `TASK_COMPLETED` 被看板唤醒，再发一条是
  两次输入、两轮调用。
- **保留完成汇报、只收窄内容（如"任务已完成，详见摘要"）**：没有新信息，只是多唤醒 leader 一次；
  若收窄为"只报摘要装不下的事项"，就等同于本设计的 D5，而通用流程里残留的"完成后汇报"一步会被
  模型按字面执行。
- **把完成说明放进事件 payload**：违背"note 不进事件、投递时现查"的约定（F_118 / F_119），事件丢失
  或重放时还会携带过期内容。

## 验证

| 用例文件 | 覆盖 |
|---|---|
| `tools/test_claim_task_completion.py` | `completed` 写入 `summary` / `note`；`claimed` 时传入被忽略并提示；认领列出直接上游摘要（不含上游的上游、不含详情）与 `view_task(action='note')` 提示；无上游不加段；schema 声明两字段且必填仍只有 `task_id` / `status`；`view_task(get)` 显示上游交付 |
| `agent/test_mode_handlers.py` | leader 完成唤醒先列"刚完成"（摘要、详情提示、位于看板之前）；全部完成时"刚完成"在收尾提示之前；其他事件触发不含；teammate 不因完成被唤醒 |
| `prompts/test_completion_handoff_wording.py` | `teammate_policy` 不再要求完成汇报 / 通知依赖方；两个 dispatch 段都要求经 `summary` / `note` 交接且不发完成汇报；调度段去掉"优先于通用工作流程"的过渡措辞（中英文） |

## spec 修订

| spec | 修订 |
|---|---|
| `S_08` | `claim_task` 新增 `summary` / `note`（仅 `status=completed` 生效）；认领结果附上游摘要；`view_task(get)` 增上游小节 |
| `S_03` | leader 看板唤醒在 `TASK_COMPLETED` 触发时附"刚完成"段，全部完成时同样附上；teammate 唤醒集合不变 |

## 已知遗留

- 自主模式的验收闸若将来开放，reviewer 收到的 `dispatcher.task_submitted_for_review_to_reviewer`
  也应带上摘要与详情（参照 F_119 / D4）。
- F_119 先于本 feature 实施时，`dispatch_scheduled_teammate.md` 里"优先于通用流程"的过渡措辞会
  短暂存在，本 feature 实施时按 D5 收敛。
