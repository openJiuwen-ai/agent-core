# Handlers — 场景 handler 的策略逻辑

`coordination/` 唤醒层的**决策末梢**。`EventDispatcher` 只做"是否该 trigger"的粗筛，
event_key → handler 的路由归 `AsyncCallbackFramework`；**"收到这个事件该唤醒谁、怎么唤醒、
要不要唤醒"全部落在本目录**。

上层结构（EventBus / dispatcher / kernel 三者的接线、三条铁律）见
[`../AGENTS.md`](../AGENTS.md)；跨子模块的协议契约与不变量编号见
[`docs/specs/S_03_coordination-protocol.md`](../../../docs/specs/S_03_coordination-protocol.md)。
本文件只讲**这些 handler 各自的决策规则**，以及它们共用的那套判断维度。

## 类契约

一个 handler 一个业务域。每个类声明 `EVENT_METHOD_MAP: ClassVar[dict[str, str]]`
（`event_key → method_name`）并实现对应 `async` 方法，由
`BaseCoordinationHandler.get_callbacks()` 输出 bound method 注册给 framework。沿用
`core/single_agent/rail/base.py:AgentRail` 的 rails 约定。

- **inner event 的 key 用 `InnerEventType.X.value`**（str-Enum，要纯 str）；
  **transport event 用 `TeamEvent.X` 裸串**（本身就是 str 常量，不带 `.value`）。混用会静默
  注册出一个永不匹配的 key。
- handler **不持有原始 host 引用**。`BaseCoordinationHandler.__init__` 收下 host 后立刻按
  narrow protocol 分成 `self._round` / `self._lifecycle`，加上 `self._poll`（EventBus）、
  `self._blueprint`（静态身份）、`self._infra`（per-process 容器）——五个字段各自表明这段
  代码依赖的是哪个面。新增依赖走对应 protocol 或显式构造参数，**不要**把东西重新塞回
  `DispatcherHost`，那是滑回上帝接口。

## 五个决策维度

每个 handler 的策略都是这五问的组合。读代码或加新分支时按这个顺序过一遍，能避免绝大多数
"该唤醒的没醒 / 不该醒的烧了一轮"的错误：

| 维度 | 问题 | 典型取值 |
|---|---|---|
| **1. 角色** | 这件事该谁反应？ | leader-only（`WorkflowHandler` / `TeamCompletionHandler` / stale-pending 自催）· 非 leader（`on_cleaned` teardown）· 角色无关（`MEMBER_SHUTDOWN` 的 `force`） |
| **2. 自身 vs 他人** | 事件指向我，还是我在旁观？ | self 分支定向 steer；他人则 fall-through 到看板兜底（`on_task_claimed`）、或直接忽略（`on_task_revoked`） |
| **3. 投递方式** | 打断当前 round 吗？ | `use_steer=True` 打断（改派/取消/返工——**再干下去就是白干**）· `use_steer=False` 追加（催促/看板巡视——**只是提醒，不该打断正在干的活**） |
| **4. 前置闸门** | 有没有"根本不该投"的状态？ | `_harness_input_blocked`（成员已 SHUTDOWN / 正在退场）· idle 阈值 + 节流（stale sweep）· 上升沿去重（完成判定）· round-in-flight（完成判定） |
| **5. 调度模式** | 自主模式和调度模式同义吗？ | 不同义就**在装配期换类**（`Scheduled*` 子类），**绝不在方法体里判 `dispatch_mode`** |

维度 3 是最容易写错的一条：**steer 会打断成员正在进行的推理**。判据不是"这条消息重不重要"，
而是"不打断的话，它接下来做的事会不会作废"——任务被改派/取消，继续干就是白干，必须 steer；
催它推进一个它已经在推进的任务，打断反而适得其反，只能 append。

## 各 handler 的策略

### `AgentLifecycleHandler`（5 个事件，无状态）

本地 agent 的生命周期信号，全部直通 host，自己不做业务判断。

| 事件 | 策略 |
|---|---|
| `USER_INPUT`（inner） | 直接 `deliver_input`。**路由已经在更上游做完**（`TeamRuntimeManager._dispatch_god_view` 解析 `@member`），到这里的输入必然是给本 agent 的，handler 不再解析 mention |
| `STANDBY` | `pause_polls()`，仅此 |
| `CLEANED` | 非 leader → `shutdown_self()`。**leader 分支恒 no-op**：persistent leader 必须活过 `clean_team` 去接下一次交互；temporary leader 的收尾由 `StreamController._on_idle_settled` 查 `state.team_cleaned` 这个 latch 驱动，走总线事件会跟 round-end 抢跑 |
| `TOOL_APPROVAL_RESULT` | 目标是自己才 `resume_interrupt`，把审批结果包成 `InteractiveInput` 灌回被中断的工具调用 |
| `TASK_PLAN_RESPONSE` | 同上，plan 闸的恢复路径。**与 `TaskBoardHandler` 在同一 event_key 上 fan-out**，分工见下文 |

### `MemberHandler`（7 个事件，持 `_team_clean_requested`）

`on_member_event` 先按角色分流：

- **leader 分支**：把 6 类 `MEMBER_*` 渲染成一行 `team_logger.debug` —— **纯观测，不唤醒任何人**。
  leader 跨进程催成员那条路已经在 F_53 删掉了。唯一的副作用在 `MEMBER_STATUS_CHANGED` 上：
  `_maybe_clean_team_after_shutdown` 在**每个非 leader 成员都到达终态 SHUTDOWN** 后自动
  `clean_team`，`_team_clean_requested` 保证只做一次。存在理由——自然语言的"解散团队"常常
  只做到 `shutdown_member` 就停了，而 persistent 团队根本不给 leader 暴露 `clean_team` 工具。
- **非 leader 分支**：只反应指向自己的事件。`MEMBER_CANCELED` → `cancel_agent()`；
  `MEMBER_SHUTDOWN` **只在 `force` 时** `shutdown_self()`。
  **graceful 退场不在这里 teardown** —— 它骑 `MessageHandler` 的邮箱 drain + harness-input
  闸门（`S_03` 机制 18）。`force` 的语义是"立刻拆，不给收尾的 round"，对所有非 leader 角色
  一视同仁，human avatar 也不例外，**所以这里没有角色分支**。

不指向自己的事件还有一个副作用：`_announce_external_team_context`。成员名册变动
（`TEAM_CONTEXT_EVENTS` = `MEMBER_SPAWNED` / `MEMBER_SHUTDOWN`）时调一句
`runtime.announce_team_context()`——**handler 自己不构造任何内容**，待发什么由 runtime 持有的
`TeamContextTracker` 判定，没有更新就是 no-op。这条存在的理由是实时性：外部 CLI 成员没有
rail，团队状态平时搭下一条外发消息的车（`CliRuntimeBase.send`），而名册变动不该等到下次有人
给它发消息。两条路共用同一个 tracker，所以不会重复投递。非外部成员 `_external_runtime()`
返回 None，整条路径静默跳过；runtime 已 TERMINATED 时同样跳过。`REFRESH_TEAM_CONTEXT`
（inner，kernel 启动时入队）是同一动作的主动触发入口。见 [[F_70]]。

### `MessageHandler`（4 个事件，无状态）

三条投递路径（`on_message_or_broadcast` / `on_poll_mailbox` / `on_member_shutdown_drain`）
**统一先过 `_harness_input_blocked` 闸门**。这是本目录最重要的一个闸门，**状态驱动而非事件
驱动**——状态从 DB 读，不信 `MEMBER_SHUTDOWN` 事件的 payload，因为 `shutdown_member` 是先写
`SHUTDOWN_REQUESTED` 再发事件，事件到达时 DB 已是权威：

| 成员状态 | 处置 |
|---|---|
| `SHUTDOWN` | 丢弃投递。绝不喂一个已死的 harness |
| `SHUTDOWN_REQUESTED` + 无 in-flight round | **不唤醒 harness，直接 `shutdown_self()`** —— 为了递一句"你被移出团队了"而烧一整轮 LLM 说告别，纯属浪费 |
| `SHUTDOWN_REQUESTED` + round 在飞 | 放行，steer 进正在跑的 round，靠 round-end 关流 |

其余策略：

- **leader 专属两件事**（在 MESSAGE / BROADCAST 上）：`_ack_user_bound_message` 替 `user`
  伪成员标记已读（它没有 agent 进程去 poll 自己的邮箱），`_notify_human_agent_inbound` 通知
  SDK 侧回调。收件人按**可达**筛选 = status ∉ {`SHUTDOWN`}——`SHUTDOWN_REQUESTED` 必须留在
  可达集里，否则"你已被移出团队"这条通知恰好送不到该收它的人。
- **投递前先 `_expand`**（`message_template.expand_message`，F_63）：普通消息原样过；框架
  模板消息（`content` 空 + `meta`）在**投递这一刻**按当前任务行渲染正文，所以排队很久的交接
  不会投出过期简报。**四个消费点都必须展开**：`_format_message`、`_notify_human_agent_inbound`
  （HITT 人类成员可能是 assignee/reviewer）、`_bridge_deliverable_for`（relay 给无 DB 的远程
  执行者的必须是展开后的文本）、以及 external 侧的 `read_inbox`。
- **渲染**（`_format_message` → `inbound_render.render_inbound`）：human-agent 加
  `for="controller"` + `hitt-silence` note；普通消息带 `reply-hint` note，正文经
  `i18n.reply_hint_for(sender)` 按发件人分化（`user` 取无条件版，其余取条件版）——**选哪条
  文案的逻辑收在 i18n 一处**，`external/format.py` 复用同一函数。**模板消息不带 reply-hint**：
  框架指令的正确响应是调工具，不是回信。

### `TaskBoardHandler`（15 个事件，无状态）

按"事件指向谁"分成三组：

**A. 定向所有权变更** —— 指向自己就 `deliver_input(use_steer=True)` 打断：

| 事件 | 自身分支 | 非自身 |
|---|---|---|
| `TASK_CLAIMED` | 被指派：steer 通知（**跳过看板兜底**，定向消息已点名任务） | fall-through 看板（实际只 leader 会被喂） |
| `TASK_REVOKED` | 改派走人：**停手** | 直接忽略 |
| `TASK_CANCELLED` | 任务取消：**停手** | fall-through 看板 |
| `TASK_UPDATED` | 内容被编辑：重看后继续，**任务仍归你** | fall-through 看板 |

改派经 DAO 原子 CAS 交换 assignee，**不发 `TASK_RELEASED`**（任务从没回到公共池）。见 F_54 / F_56。

**B. verify 闸三事件（F_59）** —— 同样是定向 steer：`SUBMITTED_FOR_REVIEW` 看 payload 的
`reviewer` 列是否含自己（含则 steer 去 `verify_task`，否则看板兜底）；`REVISION_REQUESTED` /
`VERIFIED` 的 `member_name` 都是 author，自身则分别 steer 返工、steer 去找新活（author 在
验证期被这一个活跃任务卡住，验完才放）。

**C. 看板巡视** —— `on_task_board_event` → `_nudge_idle_agent`，角色分化最关键的一处：

- **leader 收每个 board 事件都巡视全部未完成任务**（它owns 看板级决策，必须看见每次跃迁）
- **teammate 只被 `_TEAMMATE_NUDGE_EVENTS` 唤醒** = `TASK_CREATED` / `TASK_UNBLOCKED` /
  `TASK_RELEASED`，即**只有会扩大可处理池的三类**。其余跃迁要么缩小池（`CLAIMED`）、要么
  移除任务（`COMPLETED` / `CANCELLED`）、要么不改变可处理集（`UPDATED` / `STARTED`），
  唤醒 teammate 只会白烧一轮去重扫没变的板。
- `_nudge_idle_agent` 给 teammate 只渲染 **claimable（pending 且无 assignee）+ assignee ==
  自己的未完成任务**；别人 in-flight 的活不喂给 teammate——唤醒它是为了让它接活，不是让它
  围观别人干活。
- **`resume_polls()` 对每个事件都触发**：上面的过滤只砍 nudge，不停 poll。
- **板子一律 `use_steer=False`**（维度 3）：巡视只是提醒，从不说"你手上的活作废了"，
  没有理由打断成员的推理。它因此进 follow-up 队列，这一轮结束才读到。**同一批排队的板子
  会被压掉，只留最新一条**——每条都是全量快照，前面的在下一条渲染出来的瞬间就过期了；
  剔除发生在 `TeamPolicyRail.on_user_message` 上，按整条输入丢弃、不解析正文（[[F_71]]）。
  **leader 的板子不压**：它读的是快照之间的差异（哪个任务出现、哪个动了）来决定重规划还是
  收尾，压掉就等于删掉它要看的信号。

**`on_initial_task_poll`（inner，F_69）** 是 C 组的一次性入口：成员 runtime 起来时由
`kernel.enqueue_initial_task_poll()` 投一次，对称于首启邮箱 sweep —— 那条收停机期间来的
**消息**，这条收停机期间指派来的**活**（`TASK_CLAIMED` 是瞬时事件，`UNSTARTED` 的成员根本
收不到）。空片静默不烧轮次。**刻意不挂在周期 `POLL_TASK` 上**：挂上去就变成 teammate 每 30s
重扫一次没变化的板。

### `StaleTaskHandler`（1 个事件，持 4 个私有记账容器）

`POLL_TASK` 上的两个 sweep，按 assignee 把看板划成互不重叠的两半：

| sweep | 覆盖 | 全部前置 |
|---|---|---|
| `_check_stale_claimed_tasks`（**self-only**，每个成员扫自己） | `_own_stalled_tasks()`：持有的 `{PLANNING, IN_PROGRESS}`；**一个活跃任务都没有时**回落到名下最早的**一个** `PENDING(assignee=self)`（F_69） | 本成员 `idle_seconds() >= stale_claim_idle_timeout` + per-task 节流 |
| `_check_stale_pending_tasks`（**leader-only**） | **`assignee` 为空**的 `PENDING` | leader 自己 idle 超 `stale_pending_idle_timeout` **且** roster 里至少一个非 leader 成员 READY |

几条不能改错的判断：

- **停滞时长读运行时 idle 时钟（`idle_seconds()`），不读 DB `task.updated_at`**（F_65）。
  pause 冻结 `updated_at` 而墙钟继续走，长 pause→resume 后必然报假停滞。且
  `idle_seconds()` 忙时为 `None`，使"正在干活的成员被判停滞"在类型层面不可表达。
- **排除 `IN_REVIEW`**：author 等 reviewer 裁决时的 idle 是设计使然。
- **待开工是 fallback 不是增补，且只取一个**：`claim_task` 有一活跃限额，成员手上有活时
  排队中的指派本就开不了工，催了只换来拒绝。
- **leader 那条要求"有人 READY"**：全员都忙时排队是常态，不是停滞，催 leader 是噪音。
- 投递一律 `use_steer=False`（维度 3：催促不该打断正在干的活），body 只带
  id + title + 当前处境 + idle 分钟，**详情让成员自己调 `view_task`**。
- 自催连续 `_STALE_CLAIM_ESCALATE_STREAK`(3) 个窗口无效 → 由**停滞成员自己** `send_message`
  上报 leader。方向是成员→leader，与 F_53 砍掉的 leader→成员**方向相反**，不违反其设计。
- 四个记账容器（`_last_stale_nudge` / `_last_pending_nudge` / `_stale_claim_streak` /
  `_escalated_claims`）全是**私有**：self-only 之后不再需要跨 handler 共享 throttle。每次
  sweep 按当前结果做差集 GC。**节流用墙钟秒（`time.time`），停滞判定用 idle 时钟**，两者
  单位与用途不同，不要混。

### `TeamCompletionHandler`（3 个事件，持上升沿标志 + 回调表）

- `on_poll_task`：**leader-only**，且要求 round 真的空闲——`has_in_flight_round()` /
  `is_agent_running()` / `has_pending_interrupt()` **三个条件全否**才继续（中途 leader 自身
  status 是 BUSY，本来也过不了完成判定，早退只是省一次 DB 全扫）。`is_team_completed()` 的
  三条件（任务全终态 + 成员全 settled + 无任何未读消息含广播）由 `TeamBackend` 判。
  按**上升沿**发 `TEAM_COMPLETED`，落沿自动重新武装。
  **persistent 团队额外调 `conclude_completed_round` 结束 leader 流触发 auto-pause；
  temporary 不调**（它靠 leader 的 `clean_team` 收尾）。
- `rearm()`：清上升沿标志，`kernel.start` 每次调，使每个 run cycle 独立判定——一个 pause 后
  resume 的团队可以再次得出"完成"，不必先离开完成态。
- `on_task_list_drained`：fire `_completion_callbacks`（`TeamAgent` 在 DeepAgent 建好后把
  `TeamSkillRail.notify_team_completed` 注册进来；**注册表非空本身就是 fan-out 的 gate**，
  没有该 rail 的成员表为空，天然不触发）。

### `WorkflowHandler`（1 个事件，无状态）

**leader-only**，只叙述 swarmflow 的**中途**里程碑：`workflow_started` / `phase`（文案带
`run_id`，多 run 并行时区分局数）+ human 等待（`human_prompt` / `human_replied`，**不带
run_id**——人工交互靠 `correlation_id` 路由，设计如此）。`deliver_input(use_steer=True)`。

两条边界：**per-agent 事件不播报**（太吵，归 4 层 `WorkflowRun`）；**完成 / 失败结果不在此
叙述**——由 NativeHarness 异步工具框架经 `format_*` 闭包 + `harness.send` 回灌（`S_20`/`S_21`）。

### `ReliabilityHandler`（opt-in，**不在本目录**）

定义在 `reliability/handler.py`，仅 `TeamAgentSpec.reliability.enabled` 时由
`EventDispatcher` 在 7 个固定 handler **之后**条件注册。leader-only 的
`on_anomaly_detected` / `on_message`，外加 `handle_local_anomaly`（leader 自监控的直投入口，
不是事件 handler）。见 `S_19`。

## 跨 handler fan-out 的三处协作

多个 handler 注册同一 event_key 时，framework 按注册顺序串行 fan-out
（`(lifecycle, member, message, task_board, stale_task, team_completion, workflow)`，
同 priority 下 Python 稳定排序）。**绝不在一个 handler 内部调另一个 handler 的方法。**

| event_key | 参与方 | 分工契约 |
|---|---|---|
| `MEMBER_SHUTDOWN` | `MemberHandler.on_member_event` → `MessageHandler.on_member_shutdown_drain` | 前者只管 `force`（立刻拆），graceful 退场交给后者的 drain + 闸门。顺序有意义：先更新成员状态，再 drain 邮箱 |
| `POLL_TASK` | `StaleTaskHandler.on_poll_task` → `TeamCompletionHandler.on_poll_task` | **顺序是必须的**：stale 清扫可能 `deliver_input` 让 leader 转 busy，完成判定必须看到这个变化，否则会把"刚被催起来干活的团队"判成已完成 |
| `TASK_PLAN_RESPONSE` | `AgentLifecycleHandler.on_task_plan_response` → `TaskBoardHandler.on_task_plan_decision` | **靠 `tool_call_id` 是否存在互斥**：有它说明成员卡在 plan 闸的中断上，由前者 `resume_interrupt` 恢复，后者见到 `tool_call_id` 就早退不再投递；没有它才由后者 `deliver_input` 发审批结果通知。两边都只反应 `member_name == self` |

## 调度模式变体（F_62）

`task_board.py` / `stale_task.py` 各有一对模式类。**`dispatch_mode` 是静态 spec 配置**：
`EventDispatcher` 构造期按 `blueprint.spec.dispatch_mode` 查 `_TASK_BOARD_CLASS` /
`_STALE_TASK_CLASS` 字面量表装配（未知值 KeyError），运行期不变，**绝不在 handler 方法体内
判模式**——这是维度 5 的全部内容。

调度类重写的是**组合点**，不是逻辑分支：

- `ScheduledTaskBoardHandler`：`EVENT_METHOD_MAP` 把 verify 闸三事件 + `TASK_REVIEW_VOTE`
  全部路由到 `on_task_board_event`，而后者被重写成 **resume_polls-only**（交接是调度器的
  leader 身份邮箱消息，再自反应就是双投递；leader 的感知走调度器的摘要/升级）。
  `on_initial_task_poll` 覆写为 no-op（同理：调度器的 handoff 是邮箱里的持久行，首启邮箱
  sweep 会 drain 它）。**继承** `TASK_CLAIMED` / `REVOKED` / `CANCELLED` / `UPDATED` 的定向
  steer——`update_task` 在两个模式下同义。
- `ScheduledStaleTaskHandler`：`on_poll_task` 只扫自己的 stale 活跃任务（漏投递的兜底），
  **不做 leader stale-pending 自催**（调度模式下排队中的 `PENDING(assignee)` 是常态，调度器
  会 `start_task`）。它还覆写 `_check_stale_claimed_tasks` / `_self_nudge_stale_claim`
  **钉住 pre-F_65 的 `task.updated_at` 计时**（活跃集含 `IN_REVIEW`），使自主基类切到 idle
  时钟后调度模式行为逐字不变。同款 pause 缺陷在调度模式下仍在，记为 F_65 的已知遗留。

## 加一个 handler / 一个事件之前

1. **先问该不该做**：coordination 不做业务决策。新行为是不是应该做成一个 team tool，让 LLM
   自己决定调？（`../AGENTS.md` 铁律 1）
2. 新事件 = 在对应业务域 handler 的 `EVENT_METHOD_MAP` 加一行 + 写方法。**不改
   `dispatcher.dispatch()`** —— 它只管粗筛，不做路由。
3. 按五个维度想清楚策略，特别是维度 3（steer 会打断成员的推理）和维度 5（模式差异换类，
   不写 if）。
4. 需要跨域响应就让两个 handler 各注册同一 event_key，写清分工契约并评估 fan-out 顺序。
5. **异常自己记**：`framework.trigger()` 吞普通 `Exception`（log + continue），只有
   `AbortError` 上抛。handler 内部失败既不会中断 dispatcher，也不会阻断同 event 上其它
   callback，所以关键失败必须自己 `team_logger.error(..., exc_info=True)`，不能把 framework
   的 swallow 当隐式错误处理。
6. 改动触及契约就同步 `S_03`，触及本目录的策略就同步本文件。
