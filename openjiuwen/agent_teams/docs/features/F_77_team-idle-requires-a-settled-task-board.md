# F_77 team.idle 追加任务板条件

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-08-10 |
| 范围 | `agent/stream_controller.py`、`agent/team_agent.py` |
| 测试基线 | `tests/unit_tests/agent_teams` 2477 passed / 39 skipped / 3 xfailed |
| 关联 spec | `S_05_member-spawn-and-stream.md` |
| Refs | #984 |

## 背景

F_74 的 `team.idle` 只问了一半问题。它的判据是"全体成员静止且这份静止扛过了 2s 去抖"，
而成员静止只说明**此刻没人在动**——一个任务板上还挂着 5 个 `PENDING`、成员恰好都在
两轮之间的团队完全满足这个条件。消费方读到 `team.idle` 会当成"该我说话了"去投下一条
输入，而实际上团队只是还没把手上的活接上。

2s 去抖挡的是几毫秒级的接缝，挡不住这种**结构性**的空窗：等一个人类成员回话、
调度器还没把下一个任务派出去、依赖没解开的任务在排队——这些都能安静远超 2s，且都
不是"没活干了"。

## 决策

`team.idle` 变成两条件，**顺序固定**：

1. 全体成员静止（`MemberActivityRegistry.is_idle()`，`MEMBER_QUIESCENT_STATUSES`），
   且这份静止扛过 `_TEAM_IDLE_DEBOUNCE_SECONDS`（2s）——原样保留 F_74 的语义。
2. **其后**再问任务板：不存在非终态任务（终态 = `TASK_TERMINAL_STATUSES` =
   `{COMPLETED, CANCELLED}`）。**空板同样算数**——从没派过任务的团队，成员一停就是
   真闲下来了；要求"至少有一个任务"会让这类团队永远发不出 marker。

顺序不是排版偏好。板查询是一次数据库往返，成员还在动的时候问它既浪费一次 IO，也是在
回答一个此刻还没人关心的问题。用户提的需求原文就是"在原来所有成员静止之后再判断"，
实现按这个次序落地。

**判定归属分三层，各自只答自己那半个问题**：

| 层 | 回答 |
|---|---|
| `MemberActivityRegistry` | 成员那半个：有没有东西在动。不含计时概念，也**不含任务板概念** |
| `StreamController` | 组合：等去抖、复查成员、再问板，两条都过才 `emit_team_idle` |
| `TeamAgent.is_task_board_settled` | 板那半个：一次聚合 COUNT，`non_terminal == 0` |

板探针经构造参数 `task_board_probe: Callable[[], Awaitable[bool]] | None` 注入，与既有的
`request_completion_poll_callback` 同一套路。`StreamController` 拿不到 `TeamBackend`
（四象限里它只持 `state` + `resources`），而把 backend 塞给它才是真正会腐化那一层的做法：
它是流适配器，不该学会怎么读任务表。

**读法是 `task.count_tasks_terminality`**，一条 `COUNT + SUM(CASE)` 聚合，不是
`list_tasks()` 全量拉回再 Python 侧遍历。这个查询在每个扛过去抖的 idle 边上都会跑；
`_maybe_publish_task_list_drained` 早就为同一个问题准备好了这条聚合，复用它即可。

**两个边界的取值**：

- 没有 backend（`build_team` 之前、非 leader 的最小装配）→ 没有板，判为静止。
- 读库抛错 → 判为**未**静止，压住 marker 并记 warning。压住只是延后一个非终态信号；
  播出去则是拿一次读失败冒充"团队闲了"，消费方据此投输入的代价高得多。

## 与 `TEAM_COMPLETED` 的关系没有变

新增的这一条恰好是 `is_team_completed` 三条件里的第一条，但两个信号仍在回答不同问题，
不要合并：

| | `team.completed` | `team.idle` |
|---|---|---|
| 任务板 | 至少一个任务且全终态 | 全终态**或空板** |
| 成员 | `MEMBER_SETTLED_STATUSES`（排除 `UNSTARTED` / `ERROR`） | `MEMBER_QUIESCENT_STATUSES`（含二者） |
| 未读消息 | 必须为零 | 不看 |
| 流 | 发完关流 | 不关流 |

差异集中在"没有任务的团队"和"有成员崩在 ERROR 上的团队"——这两种 `team.completed`
永远不发，而它们恰恰是调用方最需要知道"没人在动了"的时刻。F_74 立这个信号就是为了
覆盖它们，本次收紧不能把它们重新排除掉，所以空板必须算静止。

## 拒绝的方案

- **判定塞进 `MemberActivityRegistry.record()`**。那个类的全部价值是"一个可以一次调用
  一次推理读懂的纯数据结构"，塞进一次 await DB 就同时毁掉纯度和同步性——`record()` 在
  每次状态观测上都被调用，而板只需要在扛过去抖之后问一次。
- **`StreamController` 直接持 `TeamBackend`**。流适配器学会读任务表，就是把
  `resources` / `infra` 的分层揉烂；注入一个返回 bool 的窄探针，被测性还更好。
- **改判据为"必须至少有一个任务"**（对齐 `is_team_completed`）。会让没派过任务的团队
  永远发不出 idle——那正是 F_74 要覆盖的场景之一。
- **复用 `TEAM_COMPLETED`，把 idle 取消掉**。见上表，两者的成员集、消息条件、关流行为
  全不同，不是同一个问题的两种精度。
- **先问板再复查成员**。省不掉任何一次查询（成员不静止时本来就该早退），反而在最常见的
  "成员还在动"路径上多打一次 DB。

## 验证

`tests/unit_tests/agent_teams/agent/test_member_activity.py` 新增 7 例：板有非终态任务时
marker 被压住、板静止时放行、成员在窗口内又动了则**根本不查板**（探针零调用）、无探针时
退回纯成员判据、`is_task_board_settled` 的三档取值（`(3,1)` → False / `(3,0)` 与 `(0,0)` →
True）、无 backend 判静止、读库抛错判未静止。去抖窗口沿用既有 `fast_debounce` fixture
压到 20ms。

`tests/unit_tests/agent_teams` 全量 2477 passed / 39 skipped / 3 xfailed。

## 已知遗留

- **板静止但邮箱里还压着未读消息**时仍会报 idle。这是刻意的：`team.idle` 不看消息
  （看消息的是 `team.completed`），而未读消息会很快把某个成员唤醒成 BUSY，届时
  marker 已经被 cancel 掉。真要收紧到"连消息都排空"，那就是在把 idle 往 completed 上
  并，回到上面已经拒绝的方案。
- **探针失败会静默压住 marker**，只有 warning 日志。与 F_74 里"崩溃成员永久压住 idle"
  同一类问题，同样交给可靠性框架的检测面处置。
