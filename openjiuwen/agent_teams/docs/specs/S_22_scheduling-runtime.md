# Scheduling Runtime（调度模式运行时）

`agent_teams.agent.scheduling` 子系统的设计规约：leader 侧调度决策引擎 + 评审投票。本文描述"系统当前是什么样"。

## 元信息

| 项 | 值 |
|---|---|
| 类型 | spec |
| 关联模块 | `openjiuwen/agent_teams/agent/scheduling/` |
| 最近一次修订日期 | 2026-10-14 |
| 关联 feature | `F_62_scheduled-dispatch-runtime-and-review-voting.md`、`F_63_scheduler-message-templating-and-delivery-render.md`（交接消息的两阶段渲染）、`F_65_runtime-idle-clock-stall-nudge.md`（自主模式改用 idle 时钟后，本模式 stale 计时的钉住策略与遗留）、`F_73_reviewer-feedback-skill-evolution-boundaries.md`（失败 feedback 与团队终态的可选宿主 callback）、`F_118_task-scoped-member-lifetime.md`（第三个幂等扫描：任务内联成员的退场请求；交接消息的上下游简述）、`F_119_scheduled-completion-handoff-via-note.md`（完成交接改走任务行 `summary` / `note`、leader 摘要带完成摘要、验收通过延迟告知） |

## 范围 / 边界

**这个规约管：**

- `TeamScheduler` 的激活条件、触发集、双幂等扫描（开工 / 验票）与内存记账语义。
- dispatch_mode 作为静态 spec 配置的消费方式（调度器构造/激活、verify 裁决策略、handler 类装配）。
- 评审投票的数据面（票表、`review_round`）与判定面（`verdict.judge`、`settle_review`）分工。
- `SchedulerHost` 窄协议与 kernel 接线（组合 wake、`SCHEDULER_SCAN` 回声、激活/失活时机）。
- 成员交接消息的两阶段渲染契约（模板 key / 占位符标准 / 命名空间白名单 / 展开点 / 降级链）。
- 失败轮次 reviewer feedback 与团队终态向宿主发布的可选 callback 契约。

**这个规约不管：**

- 事件总线、粗筛与 6+1 handler——归 `S_03_coordination-protocol`。
- 任务状态机与票表字段布局——归 `S_12_schema-data-models`（本文只引用）。
- 工具形态与权限集——归 `S_08_team-tools-contract`。

## 不变量

1. **调度器只在 `spec.dispatch_mode == "scheduled"` 的 leader kernel 上存在**：`CoordinationKernel.setup` 构造（休眠态）；其余 kernel（teammate、autonomous leader）的 `scheduler` 恒为 None。
2. **激活条件 = team 已建成**。两个激活点：`kernel.notify_team_built()`（build_team 成功回调内，先于任何 teammate spawn / create_task）与 `kernel.start()`（team 行已存在——warm resume / 冷恢复）。`pause()` / `stop()` 即 `deactivate()`。
3. **dispatch_mode 是静态 spec 配置**（F_62 评审定稿）：`TeamAgentSpec.dispatch_mode` 在构建期决定一切——rails/工具形态/提示词按模式装配、每套一份互不混写；`TeamSpec.dispatch_mode` 镜像它随 spawn 载荷到达成员进程；`build_team` **不选择协调模式**（`team_info.dispatch_mode` 列只是记录，spec 是运行时真相）；运行期没有任何模式切换路径。
4. **调度器不理解事件，只理解看板**：任何触发（`task_*` / `member_*` transport 事件、`POLL_TASK`、`SCHEDULER_SCAN`、激活）都跑同一组三个幂等扫描（开工 / 验票 / 退场）到有界不动点（`_MAX_SCAN_PASSES`）。恢复没有独立路径——激活扫描就是恢复。事件仅承担两个扫描推不出的职责：终态摘要与 `TASK_LIST_DRAINED` 收尾（事件只发一次）。
5. **交接 = leader 身份邮箱消息，投递即启动**：`_send_as_leader` 先写邮箱行（持久）再 `host.auto_start_member`（`UNSTARTED→STARTING` 拉起、`ERROR→RESTARTING` 复活、`DORMANT→RESTARTING` 唤醒，三条都是 CAS、幂等；给休眠成员派新任务即把它叫醒，F_118）。成员在线与否不是开工前置条件；成员侧零新代码（走既有 `MessageHandler`）。行里存的是**投递载荷不是文案**（`content=""` + `meta`），文案在投递时按模板渲染——见「消息面」段与 `S_12` 的 meta 三铁律。
6. **`SchedulerHost.deliver_input` 只准注入 leader 自身**（终态摘要 / 升级 / 收尾）。对成员的输入永远走邮箱。
7. **开工规则**：成员无活跃任务（`{PLANNING, IN_PROGRESS, IN_REVIEW}`）时，取其名下 `updated_at` 最早的 `PENDING(assignee)` 任务 `start_task`（DAO CAS；plan_mode 成员落 `PLANNING`）。并发/重复触发由 CAS 与一活跃探针消歧。
8. **验票规则**：每轮 `(task_id, review_round)` 送审派发一次（内存去重，崩溃后至多重发一轮）；`verdict.judge` 三值判定——`pass ≥ ceil(threshold×n)` 过、fail 票使配额不可达即败、否则未定；settle 经 `task_manager.settle_review` 的 `IN_REVIEW` 源态 CAS，单判定者 + CAS 保证不双结算。
9. **升级规则**：败局且 `review_round ≥ max_review_rounds`（任务列，NULL → `spec.default_max_review_rounds`）→ 不再自动打回，任务**留 `IN_REVIEW`**，升级注入 leader；未定且本轮开启超 `spec.review_stall_timeout` → 停摆升级（附已投/未投名单）。两类升级共用 `(task_id, review_round)` 去重；升级后决定性迟票仍可正常 settle。软催办（600s，包内常量 `_REVIEW_RENUDGE_SECONDS`）只发给未投票 reviewer、每轮每窗一次。
10. **投票判定策略在调度器，票据事实在 DB**：`verify_task`（scheduled 团队）只追加票行 + 发 `TASK_REVIEW_VOTE`，不翻转状态；autonomous 团队维持首裁即决。工具描述随语义分离（desc_key：`verify_task` / `verify_task_scheduled`）。策略更换 = 替换 `verdict.judge`，不碰票表与状态机。
11. **leader 自发事件经 `SCHEDULER_SCAN` 回声可见**：`kernel._filter_self` 丢弃 self 事件时，若调度器激活且事件是 `task_*`，改投 `InnerEventType.SCHEDULER_SCAN`。coordination 无 handler 监听该 inner 事件；调度器视其为纯扫描提示。
12. **异常语义**：`TeamScheduler.on_event` 吞普通异常（log + 下次触发重试幂等扫描），绝不让 bus loop 挂掉——与 `AsyncCallbackFramework.trigger` 的 swallow 语义对齐。
13. **内存记账不持久化**：送审去重 / 催办节流 / 升级去重 / 摘要去重都是进程内状态；leader 重启最坏重发一次送审或升级。看板真相只在 DB。
14. **各分发模式拥有自己的 task-board / stale handler 类，选择只发生在装配点**：
    `ScheduledTaskBoardHandler` / `ScheduledStaleTaskHandler` 是调度模式的 task-board / stale
    handler（verify 闸三事件与 `TASK_REVIEW_VOTE` 一律路由到 resume_polls-only 的
    `on_task_board_event`——交接是调度器的 leader 身份邮箱消息，事件自反应会双投递；poll tick
    只扫自己的 stale 活跃任务作漏投递兜底，无 leader stale-pending 自催——排队
    `PENDING(assignee)` 是常态）；自主模式类保持原实现。装配规则：`EventDispatcher` 构造期
    按静态 spec 模式查字面量类表装配（未知值 KeyError），每个角色相同、运行期不变。
    **handler 方法内禁止出现 dispatch_mode 分支。**
15. **调度模式的 stale 计时仍读 `task.updated_at`**（F_65 范围外）：自主模式已改用运行时
    idle 时钟（`S_03` 不变量 20），调度模式则由 `ScheduledStaleTaskHandler` **覆写**
    `_check_stale_claimed_tasks` / `_self_nudge_stale_claim` 钉住 pre-F_65 实现（活跃集含
    `IN_REVIEW`、阈值 `_STALE_CLAIM_SECONDS`=10min），使自主基类切换不波及本模式。代价是
    同款缺陷仍在：成员 idle 跨长 pause 后 resume，其活跃任务会被 `now - updated_at` 误判为
    stale。迁移到 idle 时钟是 `F_65_runtime-idle-clock-stall-nudge.md` 记录的已知遗留。
16. **review feedback callback 是可选旁路，不是调度判定的一部分**（F_73）：scheduler 只从
    host harness 查找已声明式挂载且启用该能力的 `TeamSkillEvolutionRail`；每个失败 settle 后按
    `(task_id, review_round)` 至多后台调用一次 `rail.handle_review_feedback(...)`，异常只记录、不改变
    任务状态。团队看板全部终态时，scheduler 先等待当前 callback 收敛，再至多调用一次
    `rail.finalize_review_feedback(...)`。scheduler 不读取轨迹、不归因 Skill、不调用 LLM、不写演进
    存储，也不负责 Rail 的构造和审批传输。

17. **退场扫描：任务内联成员的退场由调度器请求、成员自落**（F_118）。`_reconcile_retirements`
    与开工 / 验票并列，候选 = `TeamBackend.list_retirable_members()`（`options.lifetime` 非空且
    status == `READY`——**不含 `BUSY`**：成员在 round 中途被请求退场，它正在说的话的回复会落在
    一个即将自落的成员身上而无人唤醒；`READY` 的状态事件在 round 结束时本来就会再触发扫描），
    三条件同时成立才调**普通** `shutdown_member`：
    (a) 名下无非终态任务（`open_task_counts_by_assignee`，`IN_REVIEW` / `PENDING` / `BLOCKED` 都算
    开放——提交后的 author 要留着返工；验收通过不再唤醒 author，见下文"验收通过的延迟告知"）；(b) 本成员无未读消息
    （`members_with_unread`，堵"刚被唤醒、`READY` 已写、消息还没读"的窗口——否则机制 18 会在
    无 round 在飞时直接落终态，消息永远没人读）；(c) 已请求 / 已退场 / `UNSTARTED` /
    `RESTARTING` 的不在候选集里。调度器**不直写** `DORMANT`、不拆 handle：请求与自落两阶段
    由 S_03 机制 18 承接，终态由成员行的 `lifetime` 经 `retire_target` 决定；休眠类退场
    `shutdown_member` **不发告别通知**（它只会躺着未读，且会让机制 18 的休眠延迟永远成立），
    请求与自落之间到达的真实消息由机制 18 的「休眠例外」先投递再自落。被唤醒的成员再次满足
    三条件即再次休眠，没有"醒了就常驻"的特例。

## 接口契约

```python
@runtime_checkable
class SchedulerHost(Protocol):
    async def deliver_input(self, content: Any, *, use_steer: bool = True) -> None:
        """Inject content into the leader's own input stream."""
        ...

    async def auto_start_member(self, member_name: str) -> bool:
        """Best-effort start of one UNSTARTED member runtime."""
        ...


class TeamScheduler:
    def __init__(self, host: SchedulerHost, *, blueprint: TeamAgentBlueprint, infra: TeamInfra) -> None: ...

    @property
    def is_active(self) -> bool: ...

    async def activate(self) -> None: ...      # 幂等；激活即恢复扫描
    def deactivate(self) -> None: ...
    async def on_event(self, event: CoordinationEvent) -> None: ...
```

可选 Rail 入口使用 duck-typed 契约，不进入 `SchedulerHost`：

```python
rail = next(
    candidate
    for candidate in host.harness.find_rails(TeamSkillEvolutionRail)
    if candidate.review_feedback_evolution_enabled
)

await rail.handle_review_feedback({
    "team_id": str,
    "session_id": str,
    "task_id": str,
    "review_round": int,
    "task_title": str,
    "task_content": str,
    "assignee": str,
    "feedback": str,
})

await rail.finalize_review_feedback({
    "team_id": str,
    "session_id": str,
})
```

未挂载或未启用对应 Rail 时等价于未启用。task callback 由 scheduler 作为后台任务执行；终态入口
等待已启动 callback 后执行。callback 不得依赖 scheduler 重试提供 exactly-once
语义：去重状态只在当前 scheduler 进程内有效。

kernel 侧：

- `CoordinationKernel.scheduler` 属性暴露实例（测试/调试）。
- `_build_wake_callback()`：有调度器时组合 "coordination dispatch → scheduler.on_event"，否则裸 dispatch。
- `notify_team_built()`：build_team 成功回调（`TeamAgent._mark_team_built`）→ 存在调度器则 `activate()`。

配置消费（全部只在 leader 侧，不跨进程镜像）：`TeamAgentSpec.verify_vote_threshold`（默认 2/3，(0,1]）、`default_max_review_rounds`（默认 3，≥1）、`review_stall_timeout`（默认 1800s，>0）、`enable_task_verification`（提示词驱动，`CapabilityOverrides` 可在 build_team 覆盖；该参数只在 scheduled 下向模型暴露——本节这些配置都只有 scheduled 才有消费者，见 [[F_76]] 与 `S_08` 不变量 21b）。

## 数据结构

- 票表 / `review_round` / `max_review_rounds` / `team_info` 能力列：见 `S_12`。
- 调度器内存记账：`_review_dispatched: set[(task_id, round)]`、`_renudged_at: dict[(task_id, round), ms]`、`_escalated: set[(task_id, round)]`、`_digested_tasks: set[task_id]`、`_all_done_announced: bool`、`_review_feedback_dispatched: set[(task_id, round)]`、`_review_feedback_tasks: set[asyncio.Task]`、`_team_review_feedback_dispatched: bool`。其中团队终态 callback 标记在 `activate()` 复位；失败轮次去重随 scheduler 实例生命周期保留。

## 消息面

两类收件人，两套机制——**成员走模板（投递时渲染），leader 走 i18n 短串（直投）**。

### 成员交接：两阶段渲染（F_63）

调度器**不组装文案**，只组装投递载荷：`render.meta_*` 产出
`{"template", "refs", "params"}`，`_send_as_leader` 以 `content=""` + `meta=...` 落邮箱行。
文案在**投递时刻**由 `message_template.expand_message` 渲染——按收件人语言加载
`prompts/<lang>/<template>.md`，用 `refs` 指向的**当前**任务/成员行填充占位符。

| 场景 | 收件人 | template key |
|---|---|---|
| 开工（build / plan 闸） | assignee | `scheduler_task_start` / `scheduler_task_start_plan` |
| 送审派发 / 催办 | 每个 reviewer / 未投票 reviewer | `scheduler_review_request` / `scheduler_review_renudge` |
| 打回返工（`params`: 聚合 fail feedback + 解析后轮数上限） | author | `scheduler_rework` |

**占位符标准** `{{namespace.field}}`：单遍 `re.sub` 替换，**填充值永不二次扫描**（任务正文
是 LLM 写的，防占位符注入）；命名空间**字段白名单**（无 getattr 直通，新增 DB 列不会
无意泄进提示词）；未知命名空间/字段渲染为 `<missing:ns.field>`（模板 bug，不炸投递）。

| 命名空间 | 数据源 | 时机 | 白名单 |
|---|---|---|---|
| `task.*` | 任务行（`refs.task`） | 投递时现查 | `task_id` `title` `content` `status` `assignee` `reviewer` `review_round` `max_review_rounds` + 两个计算字段 `upstream_brief` / `downstream_brief`（F_118 / D7、F_119：经可选 `task_links_getter` 多查一次依赖表与相关任务行，只取**直接依赖**（一跳）；上游每条 `task_id · title · assignee · <完成摘要>`（`message_template.completion_summary`：优先 `summary`，否则 `note` 首个非空行，压单行、渲染截断到 `SUMMARY_RENDER_LIMIT`），非空时末尾追加一行 `view_task(action='note', task_id=...)` 取全文提示；下游每条 `task_id · title · assignee`，空集渲染为语言固定短句"无"/"none"；两个投递点共用 `message_template.task_links` 绑定该 getter，缺 getter 时渲染为空集短句） |
| `member.*` | 成员行（`refs.member`） | 投递时现查 | `member_name` `display_name` `desc` |
| `param.*` | `meta.params` | 发送时定格 | 键即字段（标量）；只放表答不出的瞬时值 |

**降级链**：模板缺失 / `refs` 指向的行已删 / meta 解析失败 → 投递点**从 meta 现场合成**
一行 fallback（template key + task_id，成员 `view_task` 兜底），不预存副本；meta 整体缺失 =
普通消息，原样投 `content`。

`summary` / `note` 是任务行列、**不进事件**：`TASK_COMPLETED` / `TASK_SUBMITTED_FOR_REVIEW` 仍只是唤醒信号，
正文从行里现查——与"能表查的一律现查"同一原则。

**展开点共四处**（缺一处即漏投空消息）：`handlers/message.py` 的
`_format_message`（进程内邮箱）与 `_notify_human_agent_inbound`（HITT 人类成员回调——
人类可以是 assignee/reviewer）、`_bridge_deliverable_for`（bridge relay 必须转发**展开后**
文本，远程执行者无 DB）、`external/format.py` + `client.read_inbox`（外部成员 pull）。
模板消息**不挂 `reply-hint` note**——框架指令的响应是调工具，不是回信。

### leader 直投：i18n `scheduler.*`

| 场景 | 收件人 | key |
|---|---|---|
| 终态摘要 / 轮数升级 / 停摆升级 / 收尾 | leader（`deliver_input`，非 steer） | `scheduler.leader_task_done` / `scheduler.leader_escalation_rounds` / `scheduler.leader_escalation_stall` / `scheduler.leader_all_done` |

leader 摘要不经邮箱 → 无 meta 通道、无投递时展开，维持 i18n 运行时短串。终态摘要
（`render.render_leader_task_done(task, verified=)`）由多行组成：标题行（`scheduler.leader_task_done`）、
完成摘要行（`scheduler.leader_task_done_summary`，摘要为空时省略）、详情提示行
（`scheduler.leader_task_done_note_hint`，仅在有 `note` 或摘要被截断时出现）、看板提示行
（`scheduler.leader_task_done_board_hint`）。它取代成员的 `send_message` 完成汇报（F_119）：调度模式
成员完成或送审都不再汇报。

### 验收通过的延迟告知（F_119）

验收通过**不唤醒** author：`_settle_pass` 只结算并给 leader 发终态摘要，不再投递邮箱消息
（`scheduler_verified_report` 已删除）。"通过"这一事实由 author 自己的 rail 在它**下一次输入**时
告知——`team_context.TaskOutcomeTracker` 查 author 名下 `completed` 且带 reviewer、尚未告知的任务，
渲染为 `<team-event kind="task-verified">` 内联进被接纳的输入（`TeamPolicyRail.on_user_message`；
外部 CLI 成员在下一条消息里），投递后把 task_id 记入成员 session state 的
`task_outcome_announced`。不经邮箱 → 不影响 `members_with_unread`，不阻塞退场 / 休眠落定 /
团队收尾；事件语义 → 不进 `team.context` 附件。仅调度模式、非 leader 成员启用。

## 与其它 spec 的关系

- **`S_03_coordination-protocol`**：wake_callback 组合、`SCHEDULER_SCAN` 的产生点（`_filter_self`）、调度器生命周期挂点在该 spec 的 kernel 段登记；coordination"不做决策"铁律由本包承接决策而保持成立。
- **`S_08_team-tools-contract`**：scheduled 形态 `create_task`（assignee 必填 + `max_review_rounds`）、`verify_task` 按模式二分的语义与描述形态、`build_team` 不选协调模式（不变量 21）。
- **`S_12_schema-data-models`**：状态机（`start` 边的 PLANNING/IN_PROGRESS 落点）、票表、任务行新列、`TASK_REVIEW_VOTE` 事件、消息表 `meta` 投递载荷列与其三铁律。
- **`F_73_reviewer-feedback-skill-evolution-boundaries`**：定义 scheduler callback 之外的归因、成员轨迹、Skill Rail 与创建审批边界；本文只约束 callback 的派发时机和失败隔离。
