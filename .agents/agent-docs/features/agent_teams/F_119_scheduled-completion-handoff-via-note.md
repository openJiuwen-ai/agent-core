# 调度模式完成交接改走任务行：成员不再汇报完成，完成说明拆为摘要与详情

## 元信息
| 项 | 值 |
|---|---|
| 日期 | 2026-10-14 |
| 状态 | 已实施 |
| 范围 | `tools/models.py`、`tools/database/{engine,task_dao}.py`、`schema/task.py`、`tools/task_manager.py`、`tools/tool_task.py`、`message_template.py`、`agent/scheduling/{scheduler,render}.py`、`team_context.py`、`rails/team_policy_rail.py`、`external/runtime.py`、`prompts/{cn,en}/`、`tools/locales/descs/{cn,en}/`、`i18n.py`；spec：`S_22` / `S_12` / `S_08` |
| 测试基线 | `tests/unit_tests/agent_teams`：3594 passed / 36 skipped / 3 xfailed（排除既有不稳定用例 `test_member_activity.py::test_observe_member_status_emits_after_the_debounce`） |
| 关联 spec | S_22_scheduling-runtime.md、S_12（任务行字段）、S_08（工具契约） |

## 背景

调度模式下，一个任务走到终态时 leader 会收到两份内容几乎相同的输入：

1. **调度器的终态摘要**（`scheduler.leader_task_done`，`deliver_input` 直投）——只有一行
   "任务 [id]「标题」已完成（验收通过 / 无验证直接完成）"，不带产物信息。
2. **承担者的 `send_message` 汇报**——产物路径、关键决策，和它提交时写进 `note` 的内容高度重合。

第 2 份的来源有三处：

- 通用工作流程 `teammate_policy.md` 第 5 步"标记完成后用 `send_message` 向 Leader 汇报"
  （两种调度模式共用）；
- 验收通过后调度器给承担者发的 `scheduler_verified_report`，明确要求"用 `send_message` 向
  leader 汇报"；
- 配有验证者时，模型常把"提交送审"当成"完成"，送审当下（包括返工后重新提交）就汇报一次。
  会话 `web_1a135c8763a_32d2c060dbdd` 中，诗人返工提交后立刻 `send_message` 告知 leader，
  leader 再回一句"无需额外操作"，双方各多跑一轮模型调用。

另一方面，任务行上的 `note`（F_118 / D7）只有一个字段，承担者往里写的是完整的完成说明
（常常多行、带产物全文或自评）。它被原样用在：

- 下游开工通知"上下游"一节的 `upstream_brief`（每个上游任务一行 `task_id · 标题 · 承担者 · note`）；
- 验收请求的"上游任务"与"承担者的提交说明"。

下游开工时通常只需要知道"上游交付了什么、在哪"，完整 note 既冗长，多行内容拼进单行列表项
还会打乱列表格式。

问题汇总：

- leader 被同一件事唤醒两次（摘要 + 汇报），送审时还会被提前唤醒一次；
- `scheduler_verified_report` 唤醒承担者只为了让它去汇报——它本身对"已通过"无需任何动作；
  且这条未读消息会推迟它的休眠退场（F_118 退场条件含"无未读消息"）；
- 产物交接已结构化地落在任务行上，汇报是重复通道；
- 完成说明只有一个字段，轻量的场景（摘要、上游列表）没有可用的短形态。

## 目标

只影响**调度模式**（`dispatch_mode == "scheduled"`），自主模式行为一律不变（D1 的字段与工具
参数是模式无关的数据扩展，自主模式不读它就无行为变化）。

1. 完成说明拆为 **`summary`（摘要）+ `note`（详情）** 两个字段，轻量渲染只用摘要。
2. 调度模式成员**不再用 `send_message` 汇报完成**（无论有无验证、送审还是通过）。完成交接的唯一
   载体是任务行上的 `summary` / `note`。`send_message` 只保留给澄清、阻塞、改派请求、升级后的
   返工总结、测试中途发现的缺陷等"问题沟通"。
3. leader 的终态摘要**携带 `summary`**，取代成员汇报。
4. 验收通过**不再唤醒承担者**；"你的任务已通过验收"作为**延迟告知**，在承担者下一次被任何输入
   唤醒时并入那条输入。
5. `view_task` 新增 `note` 动作，取某任务完整的摘要与详情。

## 设计

### D1 完成说明拆为 `summary` + `note`

**数据**

- 任务表新增列 `summary`（nullable text），`TaskDetail` 增加 `summary` 字段。
- DDL 迁移：沿用 F_118 加 `note` 列的做法，在 `engine.py` 动态任务表迁移段 `note` 之后加同形的
  探测 + `ALTER TABLE "<table>" ADD COLUMN summary TEXT`；对已有列的表幂等。
- `note` **不改名**，语义收窄为"详情"（产物路径、关键决策、需要注意的事项的完整说明）。

**写入**

- `member_complete_task` 新增可选参数 `summary`：一句话说明交付了什么、主产物在哪。
- `task_manager.complete(task_id, *, summary=None, note=None)`、`_submit_for_review`、
  `task_dao.complete_task` / `submit_for_review` 同步加 `summary`。
- 写入语义与现有 `note` 一致：**传了就覆盖，不传保留上一次的值**（返工重新提交时可以只更新其一）。
- **完整存储，不在写入时截断**：长度上限只作用于渲染（见下），`view_task` 永远能看到全文。
- schema 上 `summary` 可选：该工具也挂给真人分身（HITT）。调度模式的工具描述把它写成必填约定。

**归属变更时清空**

`summary` / `note` 描述的是**某个承担者的某次交付**。任务离开原承担者后它们就不再成立——新承担者
开工时若看到前任的完成说明，会误以为工作已做过；leader 摘要、下游上游列表也会把前任的说明当成
这次交付。因此任务一旦离开原承担者，两个字段一并清空：

- `task_dao.reassign_task`（原子换人，`PENDING` / `BLOCKED` / `IN_PROGRESS`，状态保留）：在同一条
  条件 `UPDATE` 的 `values(...)` 里加 `summary=None, note=None`，与换人原子生效。
- `task_dao.reset_task`（退回 `PENDING` 并清空 assignee，`PLANNING` / `IN_PROGRESS` /
  `IN_REVIEW`，含 leader 在验收轮数耗尽后的重置）：与 `task.assignee = None` 同处清空。

其余写 assignee 的路径（认领、调度开工、建任务时指派）都是从"无主"到某成员，前置的改派 / 重置
已清空，无需处理。改任务内容（`update_task`）保留 assignee、只发定向通知，不算改派，不清空。

这两处是模式无关的数据层规则：自主模式下 `claim_task` 不写这两个字段，任务行上通常本就为空，
清空不改变自主模式的可见行为。

**读取（摘要形态）**

`message_template` 新增公共函数 `completion_summary(task) -> str`，所有轻量渲染点共用：

1. 有 `summary` 用 `summary`；否则取 `note` 第一个非空行兜底；都没有返回 `""`。
2. 压成单行（换行替换为空格），超过 `SUMMARY_RENDER_LIMIT`（200 字符）截断并加 `…`。

截断只发生在渲染；需要全文的地方提示 `view_task(action='note', task_id=...)`。

### D2 leader 终态摘要携带 `summary`

- `render.render_leader_task_done(task_id, title, *, verified)` 改为接收任务行：
  `render_leader_task_done(task, *, verified)`。两处调用点（`_settle_pass`、直接完成的
  `_digest_completion` → `_digest_task_done`）都已持有或可现查任务行。
- 摘要格式（中文）：

  ```
  [调度器] 任务 [<id>]「<标题>」已完成（验收通过）。
  完成摘要：<completion_summary(task)>
  详细完成说明用 view_task(action='note', task_id='<id>') 查看。
  需要全局进度时用 view_task 查看看板实时状态。
  ```

  - 摘要为空时省略"完成摘要"一行；
  - "详细完成说明"一行仅在**有 `note` 或摘要被截断**时出现。
- 摘要仍是 `deliver_input` 直投的 i18n 运行时串（S_22：leader 摘要不经邮箱、无投递时展开）。
  i18n 新增 `scheduler.leader_task_done_summary`、`scheduler.leader_task_done_note_hint`（中英文）。

### D3 上游任务只给摘要

- `upstream_brief` 每行改为 `task_id · 标题 · 承担者 · <completion_summary(上游任务)>`。
- "上游"只指**邻接任务**（本任务的直接依赖，`task_links` → `get_task_dependencies` 只取一跳边），
  不递归到上游的上游：间接依赖的交付若对本任务有用，应由直接上游在自己的完成说明里转述。
- 列表后追加一行固定提示（有上游任务时）："需要某个上游任务的详细完成说明时，用
  `view_task(action='note', task_id=...)` 查看。"——每行不重复挂提示，避免列表变吵。
- 影响两处：下游**开工通知**的"上下游"一节，与**验收请求**的"上游任务"一节（二者共用
  `upstream_brief`）。
- 注意：开工通知里出现的是**上游任务**（已完成）的摘要；**本任务**开工时尚未完成，没有
  `summary` / `note`，开工通知里不会出现本任务的完成说明。

### D4 验收请求的"承担者的提交说明"给摘要 + 完整详情

- 校验者必须看到完整内容才能验收，这一节不截断：先列 `summary`（原文），再列完整 `note`。
- 只有一个字段时只列那一个；都没有时整节省略（现状）。

### D5 验收通过不再发 `scheduler_verified_report`

- `_settle_pass` 删除 `_send_as_leader(task.assignee, render.meta_verified_report(task))`；
  `render.meta_verified_report` 与 `prompts/{cn,en}/scheduler_verified_report.md` 一并删除
  （无其他引用）。
- 效果：通过时承担者不被唤醒、邮箱不留未读，`_reconcile_retirements` 可以按时让它退场。

### D6 延迟告知：任务结果通道（task outcome）

**语义**：承担者名下、**经验收通过**而完成的任务，若尚未告知承担者，则在它下一次输入时以一条
`<team-event kind="task-verified">` 并入。直接完成（无验证）的任务不告知——承担者自己调的
`member_complete_task`，它已经知道。

**数据来源与基线**：

- 真相在任务表：`assignee == 本成员`、`status == completed`、`reviewer` 非空。
- "已告知"记在成员自己的 session state，独立的键 `task_outcome_announced`（`TASK_OUTCOME_STATE_KEY`，
  `list[str]`，已告知的 task_id）。与对话历史同一个 checkpoint 写入，崩溃/暂停后二者不会错位
  （沿用 F_70 的理由）。
- 不复用 `team_prompt_context` 这个键：`TeamContextTracker.commit` 整体写回它在 `pending_text` 时
  拷贝的 baseline，两个 tracker 共用一个键时，后提交的一方会用旧拷贝覆盖另一方刚写入的字段。
- 读取用现有 `task_manager.get_tasks_by_assignee(member, status="completed")`，每次输入一次查询，
  不依赖事件（scheduler 进程内记账不持久化，事件也可能丢）。

**投递位置——始终内联进输入，不进 `team.context` 附件**：

- 这是**事件**（"某任务刚通过了"），不是**状态**。`team.context` 附件是当前态通道（未重新登记即
  视为撤回、靠内容 hash 判变化），把事件塞进去会被当成"持续有效的上下文"反复携带或在下次变化时
  被覆盖。因此结果通道与 `team.context` 分开：无论是否装了 attachment manager，都直接
  `parts.insert(...)` 并入被接纳的输入，提交成功后再 commit 基线。
- 落点：`TeamPolicyRail.on_user_message`（输入接纳点）。不在 `_announce_unattached_state`
  （工具循环中途）追加——延迟告知只需在下一次真实输入时出现，中途不打断。
- 仅调度模式开启：rail 已有 `dispatch_mode` 构造参数，非 scheduled 时通道关闭，自主模式维持现有
  `dispatcher.task_verified_to_self` 实时唤醒行为。
- 外部 CLI 成员（`external/runtime.py`）同样在下一条发往 SDK 的消息前并入，与 team context 的
  两步调用对称（`pending` → 投递 → `commit`）。

**实现形态**：在 `team_context.py` 新增独立的小类 `TaskOutcomeTracker`（不并入
`TeamContextTracker`，后者的输出会进 `team.context` 附件），接口与之对齐：

```python
class TaskOutcomeTracker:
    def __init__(self, *, team_backend: "TeamBackend | None", member_name: str | None) -> None: ...
    async def pending_text(self, session: Any) -> str | None: ...
    async def commit(self, session: Any) -> None: ...
```

告知文案走进程级运行时语言（`i18n.t("team_context.task_verified_intro")`），与名册提示一致，
因此构造参数不带 `language`。只在调度模式、非 leader 成员上构造（leader 不是任何任务的承担者）。

渲染（中文）：

```
<team-event kind="task-verified">
你此前提交的以下任务已通过验收并完成，无需再做任何事，也不要为此联系 leader：
- poem-wuyan-jueju · 创作一首原创五言绝句并定稿
</team-event>
```

**生命周期边界**：

- 承担者休眠（DORMANT）期间通过的任务：不唤醒；下次被点名或派活唤醒时，首条输入并入告知。
- 承担者已 SHUTDOWN 且不再被唤醒：告知永不送达，可接受（它不再有后续动作）。
- 通道不写邮箱 → 不影响 `members_with_unread`，不阻塞退场 / 休眠落定 / 团队收尾判定。

### D7 `view_task` 新增 `note` 动作

- `ViewTaskToolV2` 的 `action` 枚举新增 `"note"`，必填 `task_id`。
- 返回 `{"task_id", "title", "status", "assignee", "summary", "note"}`（不含 `content`，避免被
  `render_for_llm` 误判为详情视图）；`render_for_llm` 新增分支：

  ```
  Task #<id> "<title>" (<status>, assignee: <assignee>)
  Summary: <完整 summary>
  Completion note:
  <完整 note>
  ```

  缺哪个字段就省略哪段；都没有时返回 `Task #<id> has no completion summary or note.`。
- 不截断（这就是取全文的入口）。所有挂载 `view_task` 的角色都可用。
- `view_task(get)` 的详情视图同时显示 `Summary:` 与 `Note:`。
- 描述文件 `view_task*.md`（中英文各变体）补充该动作说明。

### D8 提示词与工具描述（只改调度模式专属的位置）

| 位置 | 改动 |
|---|---|
| `prompts/*/dispatch_scheduled_teammate.md` | 第 4 步改为：用 `member_complete_task(summary=..., note=...)` 提交——`summary` 一句话说明交付了什么、主产物在哪，`note` 写完整的产物路径与关键决策；完成（含送审）**不要** `send_message` 汇报，调度器会把结果连同摘要转告 leader；验收通过时会在你下次收到输入时告知。注明本条优先于通用工作流程"完成后汇报"一步 |
| `prompts/*/scheduler_task_start.md` | "交付方式"：提交时写 `summary` 与 `note`，它们会交给 leader 与下游；提交后不需要另行汇报 |
| `prompts/*/scheduler_rework.md` | 末尾加"重新提交后无需告知 Leader；有变化时更新 `summary` / `note`" |
| `prompts/*/scheduler_review_submission_note.md` | 改为同时承载摘要与详情（D4） |
| `tools/locales/descs/*/message/send_message_scheduled.md` | 用途去掉"汇报完成结果"，补"完成情况由任务的 `summary` / `note` 交接，不要用 `send_message` 汇报完成或送审" |
| `tools/locales/descs/*/task/member_complete_task_scheduled.md` | 新增 `summary` 参数说明并写为必填约定；`note` 说明为详情；二者是唯一的完成交接内容 |
| `tools/locales/descs/*/task/member_complete_task.md` | 新增 `summary` 参数说明（可选） |
| `MemberCompleteTaskTool` | input schema 增 `summary`；`render_for_llm` 仅调度模式附加一句：调度器会把结果和你的摘要转告 leader，不要另发完成汇报。判定经新增的 `TeamTaskManager.dispatch_mode` 只读属性 |
| `prompts/*/dispatch_scheduled_leader.md` | "通过后自动通知 author 向 leader 汇报"改为"终态摘要由调度器带完成摘要送达，成员不会另发完成汇报；需要详情时用 `view_task(action='note')`"。测试任务中途报 bug、升级后返工总结两条保持不变 |

**不改**（自主模式会用到）：`teammate_policy.md`、i18n 中 `dispatcher.task_verified_to_self` /
`dispatcher.task_revision_requested_to_self`、`claim_task` 的参数与返回结果。

## 数据结构

任务表（动态表，每 session 一张）：

| 列 | 类型 | 含义 | 写入时机 |
|---|---|---|---|
| `summary`（新增） | `TEXT NULL` | 完成摘要，完整存储 | `complete` / `_submit_for_review` 传入时覆盖；`reassign_task` / `reset_task` 清空 |
| `note`（已有，语义收窄） | `TEXT NULL` | 完成详情 | 同上 |

成员 session state 新增键：

| 字段 | 类型 | 含义 | 写入时机 |
|---|---|---|---|
| `task_outcome_announced`（独立键） | `list[str]` | 已告知承担者"验收通过"的 task_id | 告知文本随输入投递成功后 `commit` |

`task_outcome_announced` 规模受该成员名下经验收任务数约束，无需裁剪。

## 决策

1. **完成说明拆为摘要 + 详情**：轻量渲染点（leader 摘要、上游列表）只用摘要，校验者与
   `view_task` 用全文。比"对单个 note 截断"更可控——截出来的半段 note 往往语义不完整。
2. **摘要完整存储、渲染截断**：写入不报错、不丢信息；上限只约束上下文开销，全文始终可经
   `view_task(action='note')` 取回。
3. **归属变更即清空完成说明**：完成说明属于"某承担者的某次交付"，换人或重置后保留只会误导新
   承担者、leader 与下游；在 DAO 的同一次写入里清空，与换人原子生效。
4. **`note` 不改名**：避免改列名、迁移已有数据与多处描述；只收窄语义为"详情"。
5. **完成交接只走任务行**：`summary` / `note` 已是结构化、被下游与验收请求复用的交接载体，汇报是
   重复通道。
6. **延迟告知走 session state 基线，不走邮箱**：邮箱没有"不唤醒"的直发行（广播不唤醒只是因为
   休眠成员没有 runtime；在线成员照样被唤醒，30 秒轮询也会捞出所有未读），且任何未读行都会阻塞
   退场。基线方案不唤醒、不阻塞退场、与对话历史同 checkpoint。
7. **结果通道与 `team.context` 附件分离、始终内联**：事件不进当前态通道（见 D6）。
8. **只在调度模式生效**：自主模式的验收通过本就以事件实时唤醒作者，且其汇报约定不在本次范围。

## 拒绝的方案

- **只截断单个 note，不拆字段**：截断点落在任意位置，摘要语义残缺；下游/leader 需要的"交付了
  什么、在哪"不一定在开头。
- **摘要超长直接报错让模型重交**：工作已完成后因格式被打回，多一轮调用；改为渲染截断 +
  全文可查。
- **由框架从 note 自动生成摘要（取首行 / LLM 总结）作为主路径**：首行未必是摘要，LLM 总结
  增加调用与延迟。首行仅作为未提供 `summary` 时的兜底。
- **`note` 改名为 `detail`**：要改列名与迁移、已有会话数据、多处描述，收益只是命名对称。
- **保留 `scheduler_verified_report`，改成"无需汇报"**：仍白白唤醒承担者一轮，并留下未读阻塞
  退场。
- **新增"不唤醒"邮箱标志（message 表加列）**：要改动态表结构与迁移，且 `members_with_unread`、
  休眠落定、团队收尾三处判定都要区分该标志；收益与基线方案相同、代价大得多。
- **把告知并入下一次开工通知（`scheduler_task_start`）**：承担者下一次被唤醒不一定是开工
  （也可能是 leader 或用户的消息），会漏告知。
- **把告知放进 `TeamContextTracker` / `team.context` 附件**：见 D6，事件与当前态语义冲突。
- **只按"任务有无验证者"决定是否汇报**：仍保留了"直接完成后汇报"这条重复通道；统一走任务行
  更简单。

## 验证

| 用例文件 | 覆盖 |
|---|---|
| `test_database.py` | `test_task_migration_adds_summary_column_idempotently`：旧表补 `summary` 列、保留已有 `note`、重复执行幂等 |
| `test_task_manager.py` | `summary` 落盘；返工重新提交不传则保留；完整存储不截断；`reassign` 清空（状态保留）；`reassign` CAS 未命中不动字段；`reset` 在 `IN_REVIEW` 也清空；`update_task` 改内容不清空；`dispatch_mode` 只读属性 |
| `test_message_template.py` | `completion_summary` 优先摘要 / 回退 note 首行 / 空 / 折行与截断；`completion_summary_truncated`；开工通知上游只给摘要 + `view_task(action='note')` 提示（中英文）、无上游不带提示；`upstream_brief([])` 为空 |
| `agent/test_team_scheduler.py` | 验收通过不再给承担者发消息；leader 摘要带摘要并按条件附详情提示（有 note / 摘要被截断 / 都没有）；验收请求"提交说明"含摘要与完整 note；验收请求上游只给摘要 |
| `test_task_outcome_tracker.py` | 只告知经验收的完成；commit 后不重复、未 commit 会再次给出；新通过的只告知新的；无可告知时 commit 为空操作；无 backend / session / member 时关闭；文案随运行时语言 |
| `test_team_policy_rail.py` | 调度模式告知随下一条输入只出现一次；装 attachment manager 时告知仍内联、不进 `team.context` 附件；自主模式与 leader 不产出告知 |
| `external/test_cli_runtime_team_context.py` | 外部 CLI 告知随下一条消息发出、位于 team context 之后、只发一次；独立的 `announce_team_context` 不带告知 |
| `external/test_member_runtime.py` | SDK 外部成员告知随 send 发出、作为独立输入记录、只提交一次 |
| `tools/test_member_complete_task.py` | `summary` 落盘且不回显；schema 声明 `summary`；调度模式结果附"不要另发完成汇报"，自主模式不附；`view_task(action='note')` 返回不截断的摘要与详情、不含 `content`、无文本 / 缺 `task_id` / 任务不存在；`view_task(get)` 显示两字段 |

## spec 修订

| spec | 修订 |
|---|---|
| `S_22` | 交接表删除 `scheduler_verified_report` 行；leader 摘要一节说明携带完成摘要与详情提示；`task.*` 计算字段 `upstream_brief` 的行格式改为摘要；新增"验收通过延迟告知走成员 session state 基线、不经邮箱"；退场条件注释（"验收后汇报"）更新 |
| `S_12` | 任务行新增 `summary` 列、`note` 语义收窄为详情；`TaskDetail.summary`；改派 / 重置清空 `summary` / `note` |
| `S_08` | `member_complete_task` 新增 `summary`；`view_task` 新增 `note` 动作 |

## 已知遗留

- 自主模式仍保留"完成后 `send_message` 汇报"，与调度模式不一致——由 F_120 / D5 统一取消，届时
  D8 中"优先于通用工作流程"的过渡措辞一并收敛。
- 自主模式的 `claim_task(status=completed)` 不收 `summary` / `note`——由 F_120 / D1 补参数。
