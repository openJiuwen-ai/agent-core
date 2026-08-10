# F_74 Leader 侧成员活动登记与 team-idle 信号

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-08-05 |
| 范围 | `agent/member_activity.py`、`agent/state.py`、`agent/team_agent.py`、`agent/stream_controller.py`、`agent/coordination/{kernel,dispatcher}.py`、`agent/coordination/handlers/member.py`、`schema/{status,stream}.py` |
| 测试基线 | `tests/unit_tests/agent_teams` 2353 passed / 19 skipped / 3 xfailed |
| 关联 spec | `S_03_coordination-protocol.md`、`S_05_member-spawn-and-stream.md` |
| Refs | #984 |

## 背景

团队的外部调用方需要知道"这个团队现在还有没有在动"——所有成员（**含 leader 自己**）
都停下来时收到一个信号，据此决定下一步投什么。此前拿不到：

- **DB 不是答案**。每问一次就是一次全表扫描，而且 leader 自己的状态刚写进去还没读回来
  就已经过期；更要命的是没有任何一条事件流覆盖全员——`CoordinationKernel._filter_self`
  会丢掉 leader 自己发布的 `MEMBER_STATUS_CHANGED`，所以 leader 永远看不见自己的跃迁。
- **既有的 `TEAM_COMPLETED` 回答的是另一个问题**。它要求"任务全终态 + 成员全 settled +
  无未读消息"三条同时成立，说的是"这活干完了"。团队只是空转（没有任务、或有成员崩在
  ERROR 上）时它永远不发——而那恰恰是调用方最需要知道"没人在动了，该我说话了"的时刻。

## 决策

leader 内存里维护一份全成员状态视图（`MemberActivityRegistry`），从"没人在动"的上升沿
在 leader 的流上吐一个 `team.idle` 标记 chunk。

**数据结构**（`agent/member_activity.py`）：`dict[member_name, MemberStatus]` +
一个 `_armed` 布尔。就这两个字段，没有别的。

- `record(name, status)` 是唯一的判定入口，返回一个 `IdleSignal`：写入状态，若此刻
  **并非**全员静止就武装（`_armed = True`）并返回 `CANCEL`；全员静止且已武装则解除武装
  返回 `SCHEDULE`；其余 `NONE`。
- **`CANCEL` 无条件返回，不去记"是否真有待发"**——取消一个不存在的定时是免费的，而
  另一种做法是让这个数据结构去猜一个它并不拥有的定时器状态。
- `_armed` 初值 **False**：刚建好的团队天然全静止（谁都没启动），这时报 idle 是噪音。
  必须先有人动过，回落才是信号。
- `seed(statuses)` 用 DB 读到的名册整体替换，缺 leader 自己时补上——`build_team` 之前
  leader 没有 DB 行，团队不存在或成员为空都是正常状态，不是错误。
- **本模块不含任何计时概念**，纯数据结构，一次调用一次推理即可读懂。

**两条写入路径，一个判定点**（`TeamAgent.observe_member_status`）：

| 来源 | 路径 |
|---|---|
| leader 自己 | `TeamAgent._update_status`（`StreamController` 的 status_updater），DB 写成不成功都记内存 |
| 其他成员 | `MemberHandler._observe_member_activity` → `TeamLifecycleController.observe_member_status` |

`MEMBER_STATUS_CHANGED` 带状态直接用；`MEMBER_SPAWNED` / `MEMBER_RESTARTED` 不带状态但
含义各自固定（STARTING / RESTARTING），查表映射，不回查 DB。`MEMBER_SHUTDOWN` 不参与——
它之前必有一条到 `SHUTDOWN_REQUESTED` 的状态变更。

**基线**：`CoordinationKernel.start` 在 `update_status(READY)` **之前**调
`host.seed_member_registry()`，所以每个 run cycle（冷启动 / resume / recover）都从 DB
现状起算，而不是继承上个 cycle 的内存残留。

**输出**：`StreamController.emit_team_idle(members)` 投一个
`TeamOutputSchema(type="message", payload={"event_type": "team.idle", "member_count", "members"})`。
与 `team.completed` 同形，但**不关流**——团队只是安静了，下一条消息就可能把它叫醒。

## 2 秒去抖：静止得站得住才算数

成员状态在轮次之间的接缝里频繁穿越静止集——teammate 落到 READY、leader 还差一拍才转
BUSY，这中间几毫秒看起来和一个收工的团队一模一样。所以 `SCHEDULE` 只是**武装**，
`_TEAM_IDLE_DEBOUNCE_SECONDS`（2s）内静止没被打破才真的发。

定时器归 `StreamController`（不是 registry，也不是 TeamAgent）：它本来就管着一个后台
task（`_forward_task`）并在 `stop()` 里收尾，registry 又恰好挂在它已持有的
`TeamAgentState` 上——到点时自己复查一次 `is_idle()` 不需要任何额外注入。

**取消不泄露**，四条一起保证（`test_member_activity.py` 逐条有用例）：

1. **至多一个 pending task**：`schedule_team_idle()` 开头先 `cancel_team_idle()`。
2. **先清引用再 cancel**：`cancel_team_idle` 里 `self._idle_marker_task = None` 排在
   `task.cancel()` **之前**——done callback 是稍后在 loop 上跑的，它只在
   `self._idle_marker_task is task` 时清空，于是"取消旧的、立刻武装新的"不会被那个迟到
   的回调把新定时器抹掉。
3. **两条 teardown 路径都取消**：`stop()`（round-end，走 `finalize_round`）与
   `close_stream()`（外部 `stop_team` 等不经 stop 的关流路径）。没有定时器能活过它要写入
   的那条流。
4. **CancelledError 不 catch**：让 task 以 cancelled 状态结束——吞掉它既会把取消谎报成
   正常完成，也可能拖住 loop 关闭。done callback 里 `task.cancelled()` 先返回，非取消
   路径才 `task.exception()`（**必须取**，否则 GC 时打印 "Task exception was never
   retrieved" 把真正的失败盖掉）。

到点后还会**再查一次** `registry.is_idle()` 才发。取消路径已经覆盖了这一点，这一步是
双保险：marker 永远不可能和 registry 的当前状态矛盾。

## 静止态是新的一组，不是复用 `MEMBER_SETTLED_STATUSES`

`MEMBER_QUIESCENT_STATUSES` = `{UNSTARTED, READY, PAUSED, STOPPED, SHUTDOWN, ERROR}`，
活跃集是它的补集 `{STARTING, BUSY, RESTARTING, SHUTDOWN_REQUESTED}`。

和完成判定用的 `MEMBER_SETTLED_STATUSES` 差在 `UNSTARTED` / `ERROR` 两项，两个集合**不能
合并**：settled 问"干完了吗"，所以崩掉的（ERROR）和没启动的（UNSTARTED）不算数；
quiescent 问"现在动没动"，这两种明摆着没在动。合成一个集合的后果只能二选一——要么让
一个成员崩着的团队被判成完成，要么让 idle 信号被一个永远不会再跑的成员永久压住。

`SHUTDOWN_REQUESTED` 归入活跃：成员可能还在跑最后一轮，真的走了会落到 `SHUTDOWN`。

## 拒绝的方案

- **每次判定查 DB**。全表扫描，且 leader 自己的状态在 DB 里天然滞后于内存；事件流本就
  只差 leader 自己这一块，补齐它比换数据源便宜得多。
- **复用 `TEAM_COMPLETED`，放宽它的三条件**。那是在破坏一个已有语义去凑另一个语义。
  "干完了"和"没人动"是两个问题，各自有各自的消费者。
- **发一个新的 `TeamEvent` 上总线**。调用方要的是流上的信号，总线事件还得再桥回流；
  真有跨进程消费者时再加不迟。
- **把 registry 放 `TeamInfra`**（handler 天然能访问 `self._infra`，省一个 protocol
  方法）。infra 是 per-process 基础设施容器（messager / db / workspace），registry 是
  leader 的运行时观测状态，混进去就是腐化那一层的语义。按四象限规则它属于
  `TeamAgentState`，handler 侧走 `TeamLifecycleController` 新增的一个窄方法。
- **新建一个 handler 收成员事件**。成员生命周期就是 `MemberHandler` 的业务域，一个域一个
  handler；新 handler 只会让 `MEMBER_*` 的 fan-out 多一跳。

## 顺带修正

`TeamAgent.invoke` 的 `last_result` 现在跳过团队标记 chunk（`is_team_event_marker`）。
非流式调用方要的是 agent 最后产出的内容，不是框架的记账；`team.idle` 比
`team.completed` 频繁得多，不修的话 invoke 的返回值会经常变成一个 marker。

## 验证

`tests/unit_tests/agent_teams/agent/test_member_activity.py`（26 例）：registry 的信号
语义（冷启动不发 / 上升沿一次 / 重新武装 / 等齐所有成员）、静止态归属、seed 的空团队与
整体替换、marker chunk 形状与不关流、`TeamAgent.observe_member_status` 的两条路径、
`MemberHandler` 三类事件的喂入与未知状态值的忽略。

去抖与取消泄露单独六例：过窗后才发、瞬时静止被取消且不发、连续武装只留一个 timer 且只发
一次、`close_stream` / `stop` 各自取消待发、`cancel` 幂等、迟到的 done callback 不清掉新
timer、到点复查发现团队又动了则丢弃。测试用 `monkeypatch` 把窗口压到 20ms，不真等 2s。

`tests/unit_tests/agent_teams` 全量 2353 passed，无 "Task was destroyed but it is pending" /
"Task exception was never retrieved" 警告。

## 已知遗留

- **崩溃成员会把 idle 永久压住**：teammate 进程硬崩不发状态事件，registry 停在 BUSY。
  与既有完成判定读 DB 停在 BUSY 是同一类问题，交给可靠性框架的心跳/异常检测处置。
- **`spawn_member` 注册（写 `UNSTARTED` 行）不发事件**，所以未拉起的新成员不在 registry
  里。不影响判定正确性（UNSTARTED 本就是静止态），只影响 `member_count` 的即时性，下个
  run cycle 的 seed 会补齐。
