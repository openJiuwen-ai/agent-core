# Scheduling — 调度模式决策引擎（F_62）

leader 侧的调度分发 runtime，与 `coordination/`（唤醒层）平齐：coordination 只唤醒、不决策；本包只决策、不直接触碰其他成员的 round——所有成员交接都是 **leader 身份的邮箱消息**。仅在 `spec.dispatch_mode == "scheduled"`（静态配置）的 leader kernel 上构造，团队建成后激活。

## 文件地图

| 文件 | 职责 |
|---|---|
| `scheduler.py` | `TeamScheduler`：事件粗筛 + 五个幂等扫描（开工 / 交接补发 / 验票 / 退场 / 修改推送）+ `SchedulerHost` 窄协议 + 已挂载 `TeamSkillEvolutionRail` 的 reviewer feedback 旁路（F_73） |
| `review_feedback_evolution.py` | `ReviewFeedbackEvolutionCoordinator`：与产品无关的逐 Task Feedback 归因、团队终态汇总演进与新 Skill 候选路由；由 `TeamSkillEvolutionRail` 持有，复用其标准 host-event 队列 |
| `reviewer_spec.py` | `build_reviewer_agent_spec`：把成员 spec 收窄为临时 reviewer 的基础 spec——框架级裁剪（文件工具只读、关 todo 规划、不挂子代理）恒生效，宿主可再传 rail / 内置工具类型白名单；宿主组装好的放 `TeamAgentSpec.reviewer_agent`，缺省时调度器用 teammate spec 回退 |
| `verdict.py` | 纯函数投票判定（`settle_review_tally`：二元票池（verifier+challenger）一票否决 + 检视者分数池平均≥0.85）——策略可整体替换，不碰票据存储与状态机 |
| `render.py` | 两类收件人两套机制：**成员**交接只组投递载荷 `meta_*`（`{template, refs, params}`，`content=""`），文案在 `prompts/<lang>/scheduler_*.md`、投递时渲染（F_63），`is_task_start_meta` 反向判定一条已存邮箱行是否是绑定某任务的开工交接（模板 key 不出本文件，B_06；含父任务恢复用的 `scheduler_task_resume`，F_128）；**leader** 摘要/升级走 `deliver_input` 直投，仍是 `i18n.py` 的 `scheduler.leader_*` 短串；终态摘要带承担者的完成摘要（`completion_summary`，渲染截断）与按需的 `view_task(action='note')` 提示，取代成员的完成汇报；验收通过不再给承担者发消息，结果经其 rail 的 `TaskOutcomeTracker` 在下次输入时告知（F_119） |

## 核心不变量

1. **调度器不理解事件，只理解看板。** 任何触发（task/member transport 事件、`POLL_TASK`、`SCHEDULER_SCAN` 回声、激活）都跑同一组五个幂等扫描；恢复 = 激活扫描本身，无独立恢复路径。事件仅有两个例外职责：终态摘要与 `TASK_LIST_DRAINED` 收尾（事件只发一次，扫描推不出"刚刚发生"）。收尾标志在扫描看到看板又有未终结任务时复位，所以是"每次看板清空收尾一次"，不是整个生命周期一次；清空事件晚到、看板已有未终结任务时不发收尾（B_09）。
2. **交接 = leader 身份邮箱消息，投递即启动。** `_send_as_leader` 先落邮箱行（持久，离线成员首次 sweep 补投）再 `host.auto_start_member`（复用 `UNSTARTED→STARTING` CAS，幂等）。成员在线与否**不是**开工前置条件，调度器不做在线过滤。
3. **发送存意图，投递才成文（F_63）。** 邮箱行的 `content` 恒空，`meta` 带模板 key + `refs`；文案由 `message_template.expand_message` 在**投递时刻**按收件人语言渲染，`{{task.*}}` 取的是**当时**的任务行——所以队列里躺了很久的交接不会投出过期的任务简报，leader 中途 `update_task` 也立刻对未投递消息生效。能表查的一律进 `refs` 现查；`params` 只放表答不出的瞬时值（某轮 fail feedback 聚合、解析后的轮数上限）。**不要**为了"省一次查询"把任务正文快照进消息。
4. **`SchedulerHost.deliver_input` 只准注入 leader 自身**（终态摘要 / 升级 / 收尾），对成员永不直投。
5. **判定在本包，票据在 DB。** `verify_task`（scheduled 团队）只追加票行；`verdict.settle_review_tally` 三值判定（二元票池一票否决 + inspector 分数平均）；settle 经 `task_manager.settle_review` 的 `IN_REVIEW` 源态 CAS——单判定者 + CAS 保证不双结算。
6. **轮数升级与停摆升级共用一条注入路径**，按 `(task_id, review_round)` 去重；升级后任务留 `IN_REVIEW`，决定性迟票仍可正常 settle（不再重复升级）。
7. **leader 自发事件的可见性靠 `SCHEDULER_SCAN` 回声**：kernel 的 `_filter_self` 丢弃 self 事件时，若调度器激活且是 `task_*` 事件，改投一个 `SCHEDULER_SCAN` inner 事件——coordination 无 handler 监听它，调度器把它当纯扫描提示。
8. **异常语义与 coordination 对齐**：`on_event` 吞普通异常（log + 下次触发重试），绝不让 bus loop 挂掉。
9. **review feedback 是可选旁路**（F_73）：失败轮次 settle 后，scheduler 从 host harness 查找已挂载且
   开启该能力的 `TeamSkillEvolutionRail`，后台调用其窄入口，不阻塞返工；全部任务终态时先等待已启动
   callback，再调用同一 Rail 的终态汇总入口。callback 按 `(task_id, review_round)` 进程内去重、
   异常只记录。scheduler 不理解 LLM、轨迹、Skill 归因、审批或持久化，也不自行构造 Rail。
10. **退场由调度器请求、成员自落**（F_118）：`_reconcile_retirements` 对 `options.lifetime` 非空且 `READY`（不含 `BUSY`——round 中途请求会搁浅它正在等的回复）的成员，在"名下无非终态任务 + 无未读消息"同时成立时调普通 `shutdown_member`（休眠类不发告别通知）；终态（`SHUTDOWN` / `DORMANT`）由成员按 `retire_target` 自落（S_03 机制 18）。调度器**不直写** `DORMANT`、不拆 handle。给休眠成员派新任务时交接经 `auto_start_member` 唤醒它（`DORMANT→RESTARTING`）。
11. **交接以邮箱为真相，改派不漏人**（B_06）：开工扫描只在任务离开 `PENDING` 的那一刻发交接；leader `update_task(assignee=...)` 改派一个已活跃的任务（`TASK_REASSIGNABLE_STATUSES` 保留状态）不经过那一刻，新 owner 只收到瞬时 `TASK_CLAIMED`——runtime 未起则无人接收，且调度模式无首启板巡视。`_reconcile_handoffs` 对每个活跃任务（`PLANNING` / `IN_PROGRESS`）核对**当前** owner 的邮箱里是否有 leader 身份、`refs.task` 绑定该任务的 `scheduler_task_start*` / `scheduler_task_resume` 行（已读未读皆可），没有就补发同一条开工交接（同样懒启动 owner）。幂等性来自邮箱本身加进程内 `_handoff_known` 记账（`(task_id, owner)`，本进程发过或查到过即记），重启后每对至多一次邮箱读。调度器仍不理解"刚刚改派"这个事件，只看板 + 邮箱两张表。

12. **修改以版本差为真相，调度器推送最新内容**（F_129）：`_reconcile_revisions` 读窄投影 `unseen_revisions()`（有执行者、未终态、`revision > observed_revision`），执行中任务发 `scheduler_task_revised`（修改说明 + 最新完整内容 + 长内容差异），挂起中的父任务发 `scheduler_subtask_review`（加子任务列表，过期标出），没开工的不发——开工交接本来就带最新版本。投递渲染即推进 `observed_revision`，条件自然消失；`_revision_pushed` 只防投递前重发，开工 / 恢复交接发出时也记一笔。调度器不消费 `TASK_UPDATED` 本身。

13. **已退役负责人的任务不开工、不补发**（B_10）：`SHUTDOWN` 成员无法被任何路径拉起。每次扫描在多遍循环前取一次 `TeamBackend.list_retired_members()`（名字的窄投影），开工与交接补发扫描经 `_skip_retired_owner` 跳过这些负责人的任务（交接补发扫描先判退役、再查 `_handoff_known`）。负责人是本进程按策略退场的（`_reconcile_retirements` 对 `lifetime="shutdown"` 成员请求成功时记入 `_policy_retired`）时，按 `(task_id, owner)` 向 leader `deliver_input` 一次 `scheduler.leader_escalation_retired_owner`——它只在名下清空时才会被退场，名下的未结束任务一定是离开后才交给它的；leader 自己 `shutdown_member` 的成员（不论 lifetime）遗留任务归 leader 处置，不升级。`_stranded_escalated` 每次扫描后只保留仍搁浅的对。`DORMANT` 不算退役。

## 生命周期（kernel 接线）

- `CoordinationKernel.setup` 仅在 `spec.dispatch_mode == "scheduled"` 的 leader 上构造（休眠态）；其余 kernel 不构造。
- 激活点两个：`notify_team_built()`（build_team 成功回调，先于任何 spawn / create_task）与 `kernel.start()`（team 行已存在——warm resume / 冷恢复）。
- wake 路径：`kernel._build_wake_callback()` 组合 "coordination dispatch → scheduler.on_event"。
- `pause()` / `stop()` 即 `deactivate()`；内存记账（送审去重 / 催办节流 / 升级去重 / 摘要去重 / 交接已知对 / 已推送版本 / 按策略退场成员 / 搁浅已升级对）不持久化——重启后最坏重发一次送审或升级、每个活跃任务多读一次邮箱、按策略退场者名下的搁浅任务不再提醒，board 真相在 DB。
- task review feedback callback 不进入状态机关键路径；团队终态 callback 是旁路汇总边界，不改变
  all-done 摘要与 pause/stop 决策。

## 配置消费

`default_max_review_rounds`（默认 3）、`review_stall_timeout`（默认 1800s）只在本包消费，不跨进程镜像。催办间隔 `_REVIEW_RENUDGE_SECONDS`（600s）是包内常量。

设计上下文见 `.agents/agent-docs/features/agent_teams/F_62_scheduled-dispatch-runtime-and-review-voting.md`（调度器 + 投票）、
`.agents/agent-docs/features/agent_teams/F_63_scheduler-message-templating-and-delivery-render.md`（交接消息的两阶段渲染）与
`.agents/agent-docs/features/agent_teams/F_73_reviewer-feedback-skill-evolution-boundaries.md`（review feedback 外部接入边界）、
`.agents/agent-docs/bugfix/agent_teams/B_06_scheduled-reassign-active-task-handoff-gap.md`（改派活跃任务的交接缺口）、`.agents/agent-docs/bugfix/agent_teams/B_10_task-assigned-to-retired-member-stalls.md`（任务派给已退役成员后无人执行）及
`.agents/agent-docs/specs/agent_teams/S_22_scheduling-runtime.md`。
