---
title: 任务版本与过期感知：任务被修改后，执行者一定能知道
author: alan_cheng
author_note: 按 Git 最早可追溯的文档新增提交署名；不代表代码 owner
id: F_129_task-revision-and-staleness
type: F
modules:
- openjiuwen/agent_teams
files: []
designs: null
design_note: 历史设计关联待核实，不等同于没有关联设计
author_email: chengshuo5@huawei.com
---

# 任务版本与过期感知：任务被修改后，执行者一定能知道

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-10-25 |
| 状态 | 已实现 |
| 适用范围 | 两种调度模式（`autonomous` / `scheduled`）的任务编辑；子任务相关部分只在 scheduled 且开启成员子任务（`max_subtask_depth > 0`，F_128）时出现 |
| 范围 | `tools/models.py`（任务表 `revision` / `observed_revision` / `parent_revision` / `change_notes` 四列）、`team_workspace/session_file_store.py`（版本快照地址）、`tools/database/engine.py`（加列迁移）、`tools/database/task_dao.py`（编辑递增版本、确认版本、换人对齐）、`tools/task_manager.py`、`message_template.py` 与 `agent/coordination/handlers/message.py` / `external/client.py`（交接投递时确认版本）、`agent/coordination/handlers/task_board.py`（调度模式不再为编辑唤醒；自主模式认领通知确认版本）、`team_context.py`（`TaskRevisionTracker`，仅自主模式）、`rails/team_policy_rail.py` 与 `external/runtime.py`、`tools/tool_task.py`（完成闸、`view_task` 确认与过期标记、`MemberSubtaskUpdateTool`、`UpdateTaskTool` 结果提示）、`tools/tool_factory.py` / `tools/tool_permissions.py`、`agent/scheduling/scheduler.py` 与 `render.py`（`_reconcile_revisions` 扫描）、`i18n.py`、`prompts/*/scheduler_task_revised.md` / `scheduler_subtask_review.md`、`tools/locales/{cn,en}.py` 与 `descs/*/task/update_task_subtask.md`、提示词片段；文档：`docs/specs/{S_03,S_08,S_12,S_22}`、相关 AGENTS.md |
| 测试基线 | `pytest tests/unit_tests/agent_teams/` → 4039 passed / 36 skipped / 2 xfailed，其中新增 `tools/test_task_revisions.py` 17 例 |
| Refs | #1435 |

## 背景

leader 修改一个任务后，执行者靠一条 `TASK_UPDATED` 事件得知（`on_task_updated` → "任务内容已更新，重看后继续"）。这条事件只在
修改发生的那一刻发出：执行者的 runtime 不在线（`STOPPED` / `ERROR` / 尚未拉起），事件就丢了，之后也没有任何地方再提起。
执行者会按旧要求继续做，甚至按旧要求提交完成，而 leader 以为改动已经生效。这个缺口在两种调度模式里都存在。

F_128 的成员子任务把问题放大了：

- 父任务被修改时，从它派生出来的子任务可能也要跟着改；有的子任务已经在做，按旧要求做的部分会白做。改动需要一层层往下传，
  而每层"该怎么改"只有当初拆这个任务的成员知道。
- 代码审查发现，修改一个挂起中的父任务会触发"重看后继续"，把执行者叫去继续做一个已挂起的任务。

本特性**把"任务改过、执行者还没看到"变成任务行上随时可查的状态**，任何时刻、任何一方来查都能发现。调度模式下由调度器据此
主动把最新内容推给执行者；自主模式下由事件、下一次输入时的补告知与完成闸共同保证。

## 借鉴的模式

- **Kubernetes `generation` / `observedGeneration`**：期望配置一改 `generation` 就加一，控制器处理完把看到的版本记进
  `observedGeneration`，两者不等就是有改动没处理。控制器靠周期性对账（水平触发）发现差异，不依赖某个事件是否送达。调度器
  "只看任务板、扫描到不动点"本来就是这种对账器。
- **乐观并发（HTTP ETag / If-Match）**：提交时带上"基于哪个版本"，版本对不上就拒绝。
- **构建系统的脏标记（Make / Bazel）**：派生产物记住它来自源的哪个版本，源一变产物就过期。

## 数据结构

任务表新增四列（按 session 的动态任务表，迁移照已有加列写法，老数据全部为 0 / NULL，即"未修改、已看到"）：

| 列 | 类型 | 语义 |
|---|---|---|
| `revision` | `INTEGER NOT NULL DEFAULT 0` | 任务标题 / 内容的版本。建任务时为 0，每次修改标题或内容加一。只有内容编辑会改它（改派、改验收人、加依赖不算） |
| `observed_revision` | `INTEGER NOT NULL DEFAULT 0` | **当前执行者确实看到过的**版本。只在框架把任务内容交到执行者手上时推进（见 D2）。改派、重置时对齐为当前 `revision`——新执行者开工时看到的就是当前版本 |
| `parent_revision` | `INTEGER NOT NULL DEFAULT 0` | 只对子任务有意义：派生它时父任务的 `revision`。父任务 `revision` 大于它，这个子任务就"相对父任务过期" |
| `change_notes` | `TEXT NULL` | 修改说明的 JSON 列表 `[{revision, note, by}]`，随版本加一追加（D8），保留为审计历史 |

两个派生判断，不落成列：

- **执行者有没看到的修改**：`assignee` 非空、任务未终态、`revision > observed_revision`。
- **子任务相对父任务过期**：`parent_task_id` 非空、子任务未终态、`父.revision > 子.parent_revision`。

## 决策

### D1：修改即递增版本，改动和"需要被看到"在同一次写里

`TaskDao.update_task` 在写标题 / 内容的同一条 UPDATE 里 `revision = revision + 1`。没有单独的"待通知"标记：版本差本身就是标记，
写入即持久化，leader 编辑后立刻崩溃也不会丢。`TASK_UPDATED` 事件带上新的 `revision`。

### D2：内容交到执行者手上时推进 `observed_revision`

推进值是展示时渲染出的那个版本（`max(observed, shown)`，不会倒退）：

1. **调度模式的邮件投递**：开工 / 计划闸开工 / 恢复 / 返工交接，以及本特性的修改推送（D3）与子任务复核（D5），都在投递时按当前
   任务行渲染（F_63），渲染出的就是当时的版本。`expand_message` 渲染出 `task.content` 的那次投递成功后，由收件成员一侧推进。
   进程内成员在 `handlers/message.py`，外部成员在 `external/client.py`，两个展开点一起改。调度模式下这是唯一需要的推进点：成员
   从来不需要自己去看任务。
2. **自主模式的认领通知**：`on_task_claimed` 发给自己的指派通知带任务内容，投递后推进。
3. **执行者自己 `view_task(get)` 看自己的任务**：返回里带 `content`，推进。主要服务自主模式（"重看后继续"就是叫它这么做）。
4. 改派 / 重置时 DAO 把 `observed_revision` 对齐为当前 `revision`：新执行者的开工交接会展示当前版本。

补告知（D3 自主模式）只说"任务改了"、不带全文，送达时不推进。

### D3：修改如何送达，两种模式分工

**调度模式：调度器主动推送最新内容。** 调度器新增幂等扫描 `_reconcile_revisions`，与开工 / 交接补发 / 验票 / 退场并列，只看任务板。
对每个有执行者、`revision > observed_revision` 的任务：

| 任务状态 | 处理 |
|---|---|
| 执行中（`PLANNING` / `IN_PROGRESS`） | 用 `_send_as_leader` 发 `scheduler_task_revised`：正文是**最新的完整内容**，说明"任务已被修改，以此为准，按新要求调整后继续" |
| 挂起中的父任务（`BLOCKED` 且 `resume_pending`） | 发 `scheduler_subtask_review`（D5） |
| 还没开工（`PENDING` / 普通 `BLOCKED`） | 不发：开工交接投递时渲染的就是最新内容 |
| 验收中（`IN_REVIEW`） | 不会出现：这个状态下内容被锁定 |

- 投递即确认（D2 第 1 点），条件随之消失，不会重复发；连续改几次，执行者只收到一封、看到的是最终版本。进程内只用
  `(task_id, revision)` 记账省查询，leader 重启后最多重发一封。
- 离线也能收到，全靠现有机制：邮件是持久的邮箱行；`_send_as_leader` 懒启动收件人（`UNSTARTED` 拉起，`DORMANT` / `ERROR` 恢复）；
  整队停止再启动时，调度器激活扫描就是恢复。
- 推送正文永远是**完整的最新内容**，作为准确依据；"改了什么"由 D8 的修改说明与 D9 的差异辅助说明，见 D8–D11。
- `ScheduledTaskBoardHandler.on_task_updated` **不再为自己的任务被编辑而唤醒**（只续轮询），改动统一由调度器的邮件送达。这与送审、
  返工、验收交接改走调度器邮件是同一个做法（S_22 不变量 14）：既不会和邮件重复，也消除了"挂起中的父任务被改时被叫去继续做"。
  时效不变：leader 编辑经 `SCHEDULER_SCAN` 回声立即触发扫描，成员正忙时邮件在下一次模型调用时进入上下文。

**自主模式：tracker 是唯一来源，事件只负责尽早触发它。** 没有调度器，告知只有一种、只由一个对象产出：

1. **告知只有一种**：`TaskRevisionTracker` 渲染的 `<team-event kind="task-revised">`——任务 id、标题、`r{observed} → r{revision}`、
   这段区间内的修改说明，以及"用 `view_task(action=get)` 重看最新要求"。不再有单独的 `task-updated`"重看后继续"提醒。
   "看到没有"仍以 DB 的 `observed_revision` 为准；告知不带正文，不推进它。
2. **每个成员一个 tracker，由 TeamAgent 持有**：
   - 进程内成员：`AgentConfigurator` 构造一次，放进 `PrivateAgentResources`，经 `BuildContext.extras` 注入（与
     `reliability_components` 同一做法），同一个实例也在构造时显式交给 `TeamHarness`。`TeamPolicyRail` 每次 native rebuild
     都新建，借用的都是这一个实例（有组装级单测锁住 rail、harness 与 resources 是同一个实例）。没拿到注入的 rail 不做任务告知、
     只记 debug，不另建一份：第二份基线会把同一修改再说一遍。
   - 外部成员：`external_cli_spawn` 构造并绑定到成员 runtime（runtime 自己持有成员 session），接线不变。
3. **基线以实例内存为准，绑定到一个 session 对象，先认领再送达**：
   - 接口是 `claim(session)` / `release(notice)` / `persist(session)`，外加一步到位的 `announce(session, send)`（认领 → 发送 →
     失败归还 / 成功持久化）。每次调用都要给出它服务的那个成员 session，没有 session 就不认领。
   - 内存里是一个保序集合，绑定到一个 session **对象**（弱引用保存、用 `is` 比较，runtime 放手的 session 不会被 tracker 拖住）。遇到别的对象就清空内存，再从那个对象的 state 读入。
     每个运行周期、resume 进别的 team session、进程重启、从 checkpoint 恢复都会拿到新的子 session 对象，所以认领之前读到的
     一定是这份对话历史对应的基线：一个 session 的告知不会吞掉另一个 session 里同 id 任务的告知（leader 可以自定任务 id），
     回滚后的 state 也不会被回滚之前的条目覆盖。换对象不会丢已认领的条目：每次认领都在送达后立即持久化；写回失败的条目
     记在 `_pending` 里，换到**同一个 session id** 的下一个对象（下一个运行周期）时并入、下次 `persist` 补写，换到别的
     session 则丢弃。
   - `claim` 在不夹 `await` 的一段里完成绑定、判重和标记，再渲染告知。同一成员的 rail 和 runtime 都运行在同一个事件循环上，
     所以哪条路径先认领就由哪条送出，其余路径什么也看不到，不需要任何去重。认领返回凭据 `ClaimedNotice`（文本 + 覆盖的条目），
     送达抛错时 `release(notice)` 按凭据归还，下次照常告知。
   - `TaskRevisionTracker` 对同一任务只保留最新版本的条目：版本只增不减，旧版本永远不会再命中。
   - `announce` 只把"送达本身"放进可归还的范围：送达抛错、或者送达中途被取消（无法确定是否已发出，重复一次比丢一次便宜）
     就归还；送达之后的记账（如外部 runtime 记录本轮输入）放在 `announce` 之外，出错不会导致重发。
   - tracker 的类属性 `WAKES` 声明告知能否作为单独一条输入送出、唤醒空闲成员：`TaskRevisionTracker` 能，`TaskOutcomeTracker`
     不能（F_119：验收通过不唤醒作者），对它调 `announce` 什么也不认领，只能搭车。
4. **session 只做持久化**：认领的那条路径在送达后 `persist(session)`，把内存基线写回。进程内成员的 rail（`ctx.session`）和
   `TeamHarness`（`current_session()`）拿到的是同一个子 session 对象——native 把自己的 session 原样交给内层 agent 的回调上下文，
   有单测锁住；外部成员只有 runtime 一个写入方，所以不存在两个写入方互相覆盖。
5. **三个入口，同一个实例**：
   - **输入接纳**（rail 的 `on_user_message`，steering 批次也走这里）：认领，告知插在本次输入最前。
   - **`TASK_UPDATED` 指向自己**：`TaskBoardHandler.on_task_updated` 只调 `AgentRoundController.announce_task_revisions()`，
     自己不产出文案。TeamAgent 只做转发：runtime 实现了 `TaskNoticeAwareRuntime`（`agent/member_runtime.py`，独立于名册协议
     `TeamContextAwareRuntime`）就调它的 `announce_task_revisions()`，否则没有提前告知，下一次输入照常带上。
     - `TeamHarness`：运行周期内用当前子 session `announce`，告知作为 steer 送出，成员空闲时拉起 round、忙时进入当前 round。
       运行周期外没有 session，不认领，等 rail 在下一次输入时告知。
     - 外部 runtime：用自己的成员 session `announce`，与 `announce_team_context()` 同型。
   - **外部 runtime 发送任意输入**：搭车带上，现有通道不变。
6. 调度模式的 `TaskOutcomeTracker`（F_119）换成同一套接口、同一种归属，行为不变。rail 和 runtime 对两种 tracker 一视同仁。

### D4：完成闸——不会按旧要求默默做完（两种模式）

`member_complete_task` 与 `claim_task(status=completed)` 在 `revision > observed_revision` 时拒绝，返回"任务 [X] 在你开工后被修改过
（r{observed} → r{revision}），先用 `view_task(action=get)` 看最新要求，按需调整后再提交"。重看即推进，之后照常提交。

- 自主模式：这是主要保证。
- 调度模式：几乎碰不到——只有编辑和提交几乎同时发生、推送邮件还没投递时才会拦下。保留它作为"不会按旧要求做完"的最后一道保证，
  成本只是一次版本比较。

### D5：子任务版本跟着父任务走，过期逐层复核

- 成员拆子任务时（F_128 的 `add_graph(suspend_parent=)`），每个子任务的 `parent_revision` 写为父任务当前的 `revision`。
- 父任务被修改 → 它所有未结束子任务都"相对父任务过期"。父任务挂起中时，D3 的扫描给它的执行者发 `scheduler_subtask_review`：
  最新的父任务内容，加子任务列表（`{{task.subtask_brief}}`：每行 id · 标题 · 执行者 · 状态，过期的标出），请它对照复核。
  `subtask_brief` 是新增的投递时计算字段，加进 `task.*` 白名单，与 `upstream_brief` 同样经 `task_links_getter` 现查。
- `view_task` 也标出过期子任务：list 行带 `[stale: parent r{父} / based on r{子.parent_revision}]`，get 的 `Subtasks:` 行同样标注。
- 父任务执行者用成员版 `update_task`（D6）逐个处理：
  - 需要改：改标题 / 内容 → 子任务 `revision` 加一、`parent_revision` 对齐父任务，D3 的扫描再把最新内容推给子任务执行者；
  - 不用改：`update_task(task_id, confirm=true)` 只对齐 `parent_revision`，不动内容、不打扰子任务执行者；
  - 不要了：`status="cancelled"`，走 F_128 的级联取消。
- 子任务自己也挂起着下一层时，它被改就是下一层的"父任务被改"，同一套规则往下走，不写专门的递归逻辑。

### D6：成员版 `update_task`，只管自己任务下的子任务

新形态 `MemberSubtaskUpdateTool`，`_UPDATE_TASK_CLASS` 改成按 `(dispatch_mode, "leader" | "member")` 查表，新增
`("scheduled", "member")` 一格（`("autonomous", "member")` 只为装配）。`update_task` 加入 `MEMBER_ONLY_TOOLS_SCHEDULED`，与成员
`create_task` 共用门控 `member_subtasks_enabled()`。

- schema：`task_id`（必填）、`title?`、`content?`、`confirm?`（布尔，只对齐 `parent_revision`）、`status?`（只有 `"cancelled"`）。
  不提供 `assignee`、`reviewer`、`max_review_rounds`、`add_blocked_by`，偷传按"给了值即拒绝"处理。
- 权限：目标任务的 `parent_task_id` 非空，且父任务的**当前执行者**是调用者（不按 `created_by`）。不要求调用者此刻有活跃任务：
  复核时它的父任务正挂着。
- 修改走 `TeamTaskManager.update_task`（版本递增、推送与 leader 编辑同一条路），并顺带把 `parent_revision` 对齐父任务；取消走
  `TeamTaskManager.cancel`。描述独立一份 `update_task_subtask.md`（cn / en）。

### D7：leader 侧与提示词

- `UpdateTaskTool` 修改标题 / 内容成功后，结果回带新版本号与"已推送给 [执行者]"（调度模式）；任务有未结束子任务时附一句："这些
  子任务现在相对父任务过期，[执行者] 会收到复核通知；你也可以直接修改或取消它们。"
- 调度模式成员片段：任务被修改时会收到带最新内容的通知，以它为准调整；挂起中的任务被修改时，对照新内容复核过期子任务（改 / 确认 /
  取消），不要继续做被挂起的任务。
- 自主模式成员提示词：收到 `task-revised` 提醒时，提交前必须重看最新要求。
- leader 片段：修改任务不会自动改它的子任务；子任务会被标为过期并由父任务执行者复核。

### D8：修改说明 `change_note`——语义层面的差异

- leader 的 `update_task` 与成员版 `update_task`（D6）新增可选参数 `change_note`：修改者用一两句写"改了什么、为什么改"。只在同时
  修改标题 / 内容时有效，单独传报错。
- 存储：任务表新增一列 `change_notes`（JSON 列表，每项 `{revision, note, by}`），与 `revision` 递增在同一次写里追加。
- 推送：`scheduler_task_revised` / `scheduler_subtask_review` 新增投递时计算字段 `{{task.change_notes}}`，只渲染
  `observed_revision < revision <= 当前` 之间的说明（执行者还没看到的那几次），放在全文前面。自主模式的 `task-revised` 补告知同样
  带上这些说明。
- 执行者看到新版本后（`observed_revision` 推进），更早的说明不再渲染；列表保留在行里，`view_task(get)` 可查看完整修改历史。
- 改派 / 重置时不清空：新执行者开工交接展示的是当前全文，不渲染说明；历史留作审计。

### D9：长内容附带行级差异，不替代全文

- 内容长度超过阈值（模块常量，初值 2000 字符，与 `MAX_CONTENT_CHARS` 同量级）时，推送邮件在全文之外附一段差异：旧版本为执行者
  已看到的 `observed_revision`，新版本为当前内容。差异按段落 / 行计算（`difflib.unified_diff`，上下文 2 行），只作定位辅助，邮件
  明确说明"以下方完整内容为准"。
- 快照：`TaskDao.update_task` 覆写正文前，若任务有执行者且 `observed_revision` 版本的快照尚不存在，把被覆盖的版本另存为
  `tasks/<task_id>.r<N>.md`（`SessionFileStore` 新增 `KIND_TASK_REVISION` 地址）。只保留"执行者已看到的那一版"：
  `observed_revision` 推进后，比它更旧的快照在下一次写入时删除。短内容不存快照。
- 快照缺失（IO 失败、老数据）时不附差异，只推全文与修改说明，不影响正确性。
- 投递时计算字段 `{{task.revision_diff}}`，加进 `task.*` 白名单；短内容或无快照时渲染为空，模板里讲差异的段落随之消失
  （capability 槽式的空串处理）。

### D10：连续多次修改合并为一次推送

执行者还没看到上一版又被修改时，`_reconcile_revisions` 只按当前状态发一封：全文是最终版本，修改说明按 `revision` 顺序列出全部未看到的
说明（D8），差异以 `observed_revision` 为基准（D9），一次跨越多个版本。不为每个中间版本各发一封。版本设计（D3 投递即确认）天然保证这点，
本决策只要求模板与计算字段按"区间"而不是"上一版"渲染。

### D11：上下文未压缩时只推差异（后续阶段）

长内容、且执行者看到 `observed_revision` 之后**上下文没有被压缩过**，它手里一定还有旧全文，这时可以只推修改说明 + 差异，省掉重复全文。

- 需要的信号：成员侧在每次上下文压缩时把"压缩时已看到的版本"记下（`compacted_through_revision`，每个任务一项，存成员 session state
  或任务行）。`observed_revision > compacted_through_revision` 表示看到新版本后未压缩。harness 已有压缩事件，成员 rail 订阅后写入。
- 调度器渲染时据此选择"只推差异"或"推全文 + 差异"。判断不确定（信号缺失、外部 CLI 成员拿不到压缩事件）时一律推全文。
- 本阶段需要把压缩状态从成员进程传回 leader 侧的调度器，复杂度高于 D8–D10；**本次先实现 D8–D10，D11 单独排期**，在出现"长任务被频繁修改"
  的实际场景、并验证 D9 的差异质量后再做。

## 拒绝的方案

- **只走事件唤醒**：边沿触发，成员不在线就等于没有通知，执行者可能按旧要求做完并提交。
- **"待复核"标记列 + 调度器发邮件**：能可靠送达，但只覆盖挂起中的父任务，正在执行的任务被修改仍然靠事件；而且是为一种场景单独加的
  标记。版本差是更一般的"待处理"标记，同时覆盖两种模式、活跃任务和子任务。
- **调度模式也让成员自己重看（`task-revised` 提醒 + `view_task(get)` 确认）**：调度模式的交接一律由框架把内容推给成员并顺带拉起它，
  成员从来不需要自己去看任务；让它"收到提醒再去查"是多一轮往返，还要再加一条补发通道。直接推送最新内容，投递即确认。
- **调度模式保留事件唤醒、与邮件并存**：同一次修改会通知两遍，且事件那条是错误的"重看后继续"（对挂起中的父任务尤其如此）。
- **默认只推差异**：差异要成立，执行者上下文里必须还有旧全文，而长任务经常发生上下文压缩，框架确认不了这一点；任务内容是散文，
  改一个词整段显示一删一增，模型读行级差异容易看错。所以默认推全文，差异只作附带（D9），只推差异留到能确认基准还在时（D11）。
- **为每个版本都存快照**：只有"执行者已看到的那一版"是差异基准，其余版本没有用处（D9 只保留这一版）。
- **补告知送达即视为已看到**：补告知不带全文，执行者可能读到提醒却没去看内容；完成闸只认真正交到它手上的版本。
- **不要 `parent_revision`，只看父任务自己的 `observed_revision`**：执行者看过父任务新版本就算处理过子任务。少一列，但无法按子任务追踪
  哪些已复核，执行者看完父任务后忘了改某个子任务，系统不会再提醒。
- **框架把父任务改动自动写进子任务 / 父任务一改就取消全部子任务**：前者做不到（语义判断），后者浪费已完成、未受影响的子任务。
- **完成闸只提醒不拒绝**：提醒会被忽略，拒绝才能保证"按旧要求做完"不会悄悄发生；重看一次即可通过，代价很小。

## 验证

- 版本：编辑标题 / 内容递增 `revision`，改派 / 验收人 / 依赖不递增；改派、重置后 `observed_revision` 对齐；迁移后老数据全为 0。
- 推进点：调度邮件投递（进程内、外部成员两个展开点）推进到渲染出的版本；自主认领通知、执行者 `view_task(get)` 推进；补告知不推进；
  别人 `view_task(get)` 不推进。
- 调度推送：执行中任务被修改 → 执行者收到一封带最新内容的 `scheduler_task_revised`，投递后不重发；连改三次只收到一封、内容为最终
  版本；执行者离线 → 被懒启动后读到；整队重启后仍补发；未开工任务被修改不推送，开工交接带最新内容；调度模式下编辑不再触发
  "重看后继续"。
- 自主补告知：离线期间被修改，成员下次输入带 `task-revised`；同一版本只提醒一次；外部 CLI 成员同样收到；调度模式成员没有这个 tracker。
- 完成闸：两种模式下未看到最新版本时 `member_complete_task` / `claim_task(completed)` 被拒并给出版本差；看到后通过；未被修改的任务
  不受影响。
- 子任务：拆分时 `parent_revision` 等于父版本；父任务被改后子任务标为过期；挂起中的父任务执行者收到 `scheduler_subtask_review`；
  `update_task` 改 / 确认 / 取消三种处理；改后的子任务由扫描推送给它的执行者；两层子任务逐层复核。
- 修改说明：`change_note` 随编辑落行；推送只渲染执行者未看到的那几条，按版本顺序；单独传 `change_note` 报错；看到新版本后不再渲染旧说明。
- 差异：长内容推送附差异、短内容不附；基准为 `observed_revision` 的快照；快照只保留一版，`observed_revision` 推进后旧快照被删；快照
  缺失时只推全文。
- 合并：执行者未看到期间连续修改三次，只收到一封，修改说明三条按序、差异跨越三个版本。
- 回归：F_128、调度器、task board handler、`test_tool_variants.py` 既有测试全部通过；开关关闭时子任务部分不出现。

实现后的测试：新增 `tests/unit_tests/agent_teams/tools/test_task_revisions.py`（17 例，真实内存 SQLite + 真实 `TeamScheduler`，
正文落在临时目录），覆盖版本递增与修改说明、改派 / 重置 / 认领对齐、只有执行者本人查看才算看到、两种模式的完成闸、调度推送
（连改三次一封、内容为最终版本、修改说明按序、投递后不重发、懒启动收件人）、开工前的修改随开工交接送达、长内容差异与短内容无差异、
父任务修改后的子任务复核邮件与 `view_task` 过期标记、成员版 `update_task` 的修改 / 确认 / 取消与权限、leader 编辑结果回报、
自主模式补告知只提醒一次、按模式选择 tracker；`test_database.py` 的迁移用例补上四列。

## 实现中确定的细节

- **认领对齐 `observed_revision`**：自主模式成员是从看板（带正文）认领的，`TaskDao.claim_task` 在认领 CAS 里把它对齐为当前版本；
  否则一个认领前就被改过的任务，认领后会被完成闸误拦。
- **开工 / 恢复交接计入已推送版本**：任务在开工前被修改、开工扫描与修改推送扫描在同一次扫描里先后看到它时，开工交接投递时本来就
  渲染最新版本，调度器发出开工交接时把 `(task_id, revision)` 记进 `_revision_pushed`，不再另发一封修改推送。
- **只有真正的修改才发 `TASK_UPDATED`**：内容与当前正文相同、或只确认子任务（只动 `parent_revision`）时不加版本、不发事件。
- **补告知 tracker 与 F_119 共用接线**：`build_task_event_tracker(dispatch_mode)` 调度模式给 `TaskOutcomeTracker`、自主模式给
  `TaskRevisionTracker`，rail 与外部 CLI runtime 的绑定点不变（方法名沿用 `bind_task_outcome_tracker`）。
- **事件 XML 里用 `→`**：`render_event` 会转义 `>`，版本区间写成 `r0 → r1`；工具错误文本里仍是 `r0 -> r1`。
- **修正了一处过时的提示词**：自主模式成员模板写着"任务被 `update_task` 修改会重置为 pending、认领被撤销"，这早已不是现状
  （编辑保持归属，F_56），随本特性改为"任务仍归你，会收到提醒，提交前必须看过最新版本"。

## 审查后修复（2026-10-25）

代码审查发现的问题合成一个 `fix(swarm)` 提交修复：

- **人类成员不受完成闸约束**：人类在自己的界面看任务，不经过那几条会记录"已看到"的通知；任务开工前被改过的话，人类成员会一直被
  拒绝完成。`_unseen_revision_error` 对在队人类成员（`is_live_human_agent`）放行；HITT 指派通知送达后同样记录当前版本。
- **只看不读不算看到**：外部成员 `read_inbox(mark_read=False)` 只预览、不消费消息，也不再推进 `observed_revision`；只有标记已读的
  拉取才记录已看到的版本，与"消息算送达"同一时刻。
- **补告知只提醒进行中的任务**：自主模式的 `TaskRevisionTracker` 只看 `PLANNING` / `IN_PROGRESS`（新常量 `TASK_WORKING_STATUSES`）。
  还没开工的预指派任务被修改，认领时版本本来就会对齐，提醒只是噪音。查询改为 DAO 的窄查询 `get_unseen_edits`，不再按成员读出全部
  任务并回读溢出正文。
- **修改告知只有一个来源**：在线执行者原来会同时收到 `TASK_UPDATED` 的 `task-updated` 提醒和 tracker 的 `task-revised` 补告知，
  同一次修改说两遍。改为 D3 所述：tracker 是唯一来源，事件只触发它。评估过、没有采用的做法：
  - **按输入内容去重**（rail 删掉被补告知覆盖的那条提醒）：一度实现过，又撤掉了。它依赖事件块的文本格式，而且只在两条落进同一次
    输入时才有效；`before_steering_drain` 的分批上限把提醒推到下一批时，仍会重复。
  - **handler 送达提醒后替 tracker 写 session 基线**：进程内成员空闲时，session 不在内存里，handler 只能写 checkpoint；而紧接着拉起的
    round 会从 checkpoint 恢复出自己的副本，round 结束时整体写回，把 handler 记下的基线覆盖掉。
  - **任务行新增 `notified_revision`，以数据库条件更新认领**：可行，进程重启后也不会重复告知；但要加列和迁移。内存基线不改表，与
    团队上下文告知（F_70）同样把基线存在 session 里，代价只是进程崩溃后最多重复告知一次。
- **编辑结果如实说送达方式**：`update_task` 结果的 `pushed_to` 改为 `assignee` + `delivery`，按编辑时的任务状态取值：调度模式执行中
  `pushed`、挂起中的父任务 `review_requested`；自主模式执行中 `notified`；两种模式未开工都是 `on_start`；验收中与已结束的不回报。
  原来对未开工任务也说"已推送"，与调度器的实际行为不符。
- **内容没变时修改说明不再静默丢弃**：leader 传了 `change_note`、但标题和内容都没变时，工具报错并提示改用 `send_message`，不再回报
  `updated`。没有在 DAO 层"内容不变也加版本"：空版本会触发推送和完成闸，代价太大。
- **过期判定只有一份**：`stale_against(task, parent_revision)` 下沉到 `tools/database/graph.py`，`TeamTaskManager` 的列表 / 详情与
  `message_template.subtask_brief` 共用，终态统一用 `TASK_TERMINAL_STATUSES`。
- **带过滤的列表不再重读整个看板**：缺的父任务版本经 `get_task_revisions` 只投影 `task_id` / `revision` 两列补齐。
- **快照文件读写移出事件循环**：`_seen_revision_snapshot` 返回要做的文件操作，`update_task` 提交后用 `asyncio.to_thread` 执行；
  `get_revision_snapshot` 改为异步。正文溢出本身的读写仍是同步的（既有行为），本次不动。

- **实现中定下的两处细节**：
  - `claim()` 返回认领凭据 `ClaimedNotice`（文本 + 覆盖的条目），`release(notice)` 按凭据归还。不能只记"最近一次认领"：A 路径认领后
    在 `await` 送达时，B 路径的一次空认领会把它清掉，A 送达失败时就撤不回了。
  - 任何读写都先绑定到当前 session 的基线（第二轮审查后由 `seed` 改为按 session id 的 `_bind`）：从没读过 checkpoint 的实例
    直接写回，会用内存里的列表覆盖 checkpoint 里已有的基线。
- 告知的开头文案补上原来提醒里的意思：任务仍归你、无需重新认领，先重看再继续；`dispatcher.task_content_updated_to_self` 文案删除。

### 第二轮审查后的修复（2026-10-26）

- **基线按 session 绑定**：原先内存基线跟着 TeamAgent 跨 session 存活，而且每个实例只读一次 checkpoint。同一个 TeamAgent
  resume 进另一个 session 后，旧 session 的已告知条目会吞掉新 session 里同 id 任务的告知，新 session 自己的基线也读不进来。
  改为绑定 session id，换 session 时清空并重新读入。
- **认领之前先读基线**：事件路径原先不经 session 就认领，进程正常重启后，只要第一次输入之前来了 `TASK_UPDATED`，重启前告知过的
  修改都会再说一遍。现在 `claim(session)` 必带 session，事件路径用 `TeamHarness.current_session()`。
- **任务告知协议独立**：`announce_task_revisions` 从名册协议 `TeamContextAwareRuntime` 里拿出来，放进独立的
  `TaskNoticeAwareRuntime`。原先没实现新方法的 runtime 会连名册刷新一起失效。
- **进程内的告知由 `TeamHarness` 自己发**：TeamAgent 不再按 runtime 类型特判，只转发给实现了该协议的 runtime。
- **rail 不再自建 tracker**：缺注入时关掉任务告知并记 warning，不再悄悄形成第二份基线。
- **基线是保序集合**，修订 tracker 每个任务只留最新版本，不再线性查找、无限增长。三处重复的认领、送达、归还、持久化组合
  收进 `announce(session, send)`。
- 核实过一条疑点：停止后的 runtime 发送会抛错（`NativeHarness.send` 在 TERMINATED 下抛错），`announce` 会归还认领，不存在
  "认领了却悄悄送不到"。

### 第三轮审查后的修复（2026-10-26）

- **基线绑定到 session 对象而不是 session id**：同一个 session id 的 state 回滚到较早的 checkpoint（恢复命名 checkpoint、fork）
  后，按 id 判断不会重新读入，还会把回滚前的条目写回去，执行者历史里已没有的告知再也不会重发。恢复总会拿到新的子 session
  对象，按对象判断就会重新读入。顺带去掉了 `id(session)` 兜底（对象回收后 id 可能被复用）。
- **送达之后的错误不再归还认领**：外部 runtime 原先在 `announce` 的发送回调里记录本轮输入，记录出错会把已送达的告知归还、
  之后重发；记录移到 `announce` 之后。
- **验收通过不会被主动送出**：`TeamHarness` 与两个外部 runtime 的 `announce_task_revisions` 原先不区分 tracker，调度模式
  注入的 `TaskOutcomeTracker` 一旦被调用，就会把"验收通过"作为 steer 发出去并拉起作者；新增 `WAKES` 声明，统一在
  `announce` 里拦下。
- **`TeamHarness` 显式拿到 tracker**：`TeamHarness.build(task_event_tracker=...)` 由 configurator 传入，不再在方法里临时
  import rail 的 extras 访问器；会话取 `current_session()`。
- **缺注入的告警每个成员只打一次**，并带上团队与角色。
- 核实了"单一写入方"的前提：native 把自己的 session 原样交给内层 agent，rail 的 `ctx.session` 就是 `TeamHarness` 的当前
  session；补了单测锁住。

### 第四轮审查后的修复（2026-10-26）

- **弱引用绑定 session**：tracker 原先强引用上一次绑定的子 session，运行周期结束后整份 state（含对话历史）要等下次绑定才
  释放。改为 `weakref.ref`，对象回收后下次调用自然重新读入。
- **写回失败的条目跨周期补写**：按对象绑定后，`persist` 失败的条目只在内存里，下一个周期换新对象重新读入就丢了，会重复
  告知。新增 `_pending`：认领时加入、写回成功清空、归还时移除；换到同一 session id 的新对象时并入并补写。
- **组装接线有了单测**：非 leader 成员的 `PrivateAgentResources.task_event_tracker`、`TeamHarness._task_event_tracker`、
  `TeamPolicyRail._outcome_tracker` 是同一个实例；leader 三者都没有。rail 缺注入的告警随之降为 debug，模块级去重集合删掉。
- **外部 runtime 的输入记录放进 `finally`**：拿到回执就记录，`announce` 后半段出错也不漏；改用单个局部变量而不是列表。
- 新增的 tracker 字段注解改为 `X | None`（本模块禁止 `Optional`）。

测试基线：`pytest tests/unit_tests/agent_teams/` → 4073 passed / 36 skipped / 2 xfailed。

## 已知遗留

- 完成闸改变了两种模式下完成工具的行为（被修改后需要先看到最新内容），属于对现有行为的收紧；外部 / MCP 成员同样受约束。
- 推进 `observed_revision` 需要成员一侧写库（邮件投递、认领通知、`view_task`），每次是一条小 UPDATE。
- 自主模式不会为了告知修改去拉起离线成员（runtime 没起来时不认领）：要等它起来后的第一次输入才告知。
- 成员版 `update_task` 修改子任务后，结果仍写"最新内容已发给执行者"，未按子任务状态区分送达方式。
- 修改告知的基线以内存为准：只有进程在认领之后、这次持久化之前崩溃，重启后同一版本才会再告知一次。
- 写回失败与 state 回滚同时发生时，那条告知不会重发：为避免重复告知，补写优先于回滚后的历史。
- 整个 round 被 `abort(immediate)` 丢弃时，这一轮里送达的告知随之消失，但 session 对象没变、基线不回滚，这次修改不会再告知，
  由完成闸兜底。
- `release(notice)` 只覆盖送达抛错的情况。送达成功即视为已告知；之后那条输入如果在 runtime 内部被丢弃，这次告知就丢了，由完成闸兜底，
  不会按旧要求默默做完。
- 成员仍不能改派子任务、调整子任务依赖。
- D11（上下文未压缩时只推差异）单独排期，本次不实现。
- 差异阈值是模块常量，未做成 spec 配置；有实际调参需求再放开。
