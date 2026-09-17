# 创建任务即拉起成员，round-idle 再对账一次

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-08-17 |
| 范围 | `openjiuwen/agent_teams/tools/team.py`、`openjiuwen/agent_teams/tools/tool_task.py`、`openjiuwen/agent_teams/tools/tool_message.py`、`openjiuwen/agent_teams/tools/tool_factory.py`、`openjiuwen/agent_teams/agent/team_agent.py`、`openjiuwen/agent_teams/agent/stream_controller.py`、`openjiuwen/agent_teams/agent/agent_configurator.py`、`openjiuwen/agent_teams/rails/`、`openjiuwen/agent_teams/external/cli_agent/claude/`、`openjiuwen/agent_teams/prompts/{cn,en}/dispatch_autonomous_leader.md`、`docs/specs/S_05`、`docs/specs/S_08` |
| 测试基线 | `pytest tests/unit_tests/agent_teams/` → 2651 passed / 39 skipped / 3 xfailed |
| Refs | #984 |

## 背景

用户报告：autonomous 模式下 teammate 的启动依赖 leader 调 `send_message`；模型偶尔在
`create_task` 之后不再调它，于是全员停滞，只能等 leader 自己轮询兜底。

这不是模型的偶发失误，是设计把一个必要环节交给了模型。当时的
`prompts/cn/dispatch_autonomous_leader.md` 原文：

> 任务创建完成后，用 `send_message(to="*")` 广播启动——系统据此自动拉起所有未启动的成员

**兜底也救不回来。** `StaleTaskHandler._check_stale_pending_tasks` 是 leader 侧唯一的看板
兜底，它的三个前置条件之一是「roster 里至少一个非 leader 成员处于 `READY`」。全员
`UNSTARTED` 时这条不成立——**恰恰在最需要它的场景下它不触发**。真正能救场的只剩 leader
下一轮自己想起来发消息，也就是刚刚失效的那一环。

对照组是 scheduled 模式：`TeamScheduler._reconcile_starts` 扫看板 → `_send_as_leader` 先落
邮箱行再 `host.auto_start_member`（`S_22` 不变量 2：**投递即启动**，成员在线与否不是开工
前置条件）。autonomous 缺的正是这个等价物——`kernel.setup` 只在 scheduled 的 leader 上构造
调度器，autonomous 下 `self._scheduler is None`。

## 根因

**拉起漏斗的触发点集合漏了一条交付路径。** 注册与拉起分离（`S_05` 不变量 1）本身是对的：
`spawn_teammate` 只写 DB 行，让 leader 能先规划名册再统一启动。问题在于漏斗的三个触发点
全是"消息要投给谁"，而 autonomous 有**两条**把活交给成员的路径——消息，和看板上的任务。
第二条从来没有接进漏斗。

`create_task` 走 `add_graph` 写库后往 `TeamTopic.TASK` 发 `TaskCreatedEvent`，但
`UNSTARTED` 成员的进程没起来、没订阅那个 topic，事件是发给空气的。

## 方案

### 1. `TeamBackend.autostart_unstarted()` 作为共享入口

新增 keyword-only 构造回调 `on_member_started`（由 `AgentConfigurator` 注入
`TeamAgent._on_teammate_created`）+ 一个自带 leader 门与回调门的方法。调用方只表达
"确保名册起来了"的意图，不再各自捎带一份 spawn 回调。并发闸仍是下层
`startup_member` 的 `UNSTARTED→STARTING` CAS，所以重复调用天然幂等。

**顺带收敛了一条重复的注入链**：`on_teammate_created` 原本经
`create_team_tools` → `_SendMessageBase` 这一路单独下发，且在 rails 侧还有一整条
`TeamHandleKey.ON_TEAMMATE_CREATED` → `inject_team_handles` → `get_on_teammate_created`
→ `TeamToolRail` 的传递链，外加 external CLI 的两跳转发。回调收进 `TeamBackend` 之后，
这条链的唯一终点消失，整条一并删除——两个注入点必然漂移，留着只会让下一个人猜哪个才算数。

### 2. 快路径：`create_task` 成功后拉起

`TaskCreateTool.invoke` 在 `add_graph` 成功之后调 `autostart_unstarted()`。

**为什么必须在工具侧同步做，而不是让 `TaskBoardHandler` 收 `TASK_CREATED` 事件**：leader
自己发的事件会被 `kernel._filter_self` 丢弃，而 autonomous 下没有 scheduler，也就没有
`SCHEDULER_SCAN` 回声降级。leader 根本收不到自己创建任务的事件——事件驱动路径在这里**物理
不可达**。

**为什么拉起全部而不只是 assignee**：无 assignee 的任务进公共认领池，任何成员都可能是认领
者。这与 `send_message` 的 `_auto_start_members` 语义一致（它本来就是无差别 `startup()`）。

**为什么拉起失败不改变工具的成败**：任务已经落库了。报失败只会诱使模型把同一批任务再建
一遍。补救交给下面的慢路径。

**`ScheduledTaskCreateTool` 不动**：scheduled 的交接归 `TeamScheduler` 全权负责，工具再插
一手就是双投递。两个形态本来就是独立的类，差异由类的选择吸收，不是 `invoke` 里的模式分支。

### 3. 慢路径：leader round-idle 对账

`TeamAgent._reconcile_member_startup` 经新的 `member_startup_reconciler` 回调挂在
`StreamController._on_idle_settled` 上：leader-only，先一次聚合 COUNT 确认看板有非终态任务，
再调 `autostart_unstarted()`。

**为什么是 round-idle 而不是周期 `POLL_TASK`**：leader 一轮结束意味着这一轮的意图已经完整
表达——该建的任务建了、该拉的人可能忘了拉——这是"该起的人起没起"最准的检查点。挂周期 poll
则要多等最多一个 30s 窗口，且那个信号和 leader 做了什么无关。

**顺序是正确性的一部分**：对账必须排在同一条边上的 `_request_completion_poll` **之前**。
拉起成员会让团队重新动起来，先做完成判定会把"成员正在起来"的团队判成已完成。这与
`POLL_TASK` 上 `stale_task → team_completion` 的既有顺序契约同源。

**不借道 `_request_completion_poll`**：后者是 leader + persistent only。temporary 团队既没有
scheduler 也没有 operator 介入，恰恰是最容易卡死的那类，必须覆盖。

**不需要节流，也不需要 idle 阈值**：拉起后 `UNSTARTED` 集合为空，触发条件自然消失；成员没
起来这件事与 leader 忙不忙无关，越早拉起越好。

### 4. Prompt 纠偏

`dispatch_autonomous_leader.md`（cn / en）里把广播写成启动因果的两处改掉——`send_message`
不再是启动开关，只用于传达任务正文之外的额外上下文。描述是行为契约，留着错的因果，模型就
会照错的做。

## 拒绝的方案

- **让 `TaskBoardHandler` 收 `TASK_CREATED` 时拉起**：leader 收不到自己发的事件（见上），
  路径不可达。
- **在 `MemberHandler` 上挂周期 `POLL_TASK` 对账**：可行，且 fan-out 顺序天然正确
  （`member` 注册在 `stale_task` / `team_completion` 之前）。但比 round-idle 慢一个 poll
  窗口，且触发信号与 leader 的行为无关；round-idle 是更精确的边。
- **在 `StaleTaskHandler` 里加**：领域错位——那是停滞任务域，不是成员生命周期域。
- **`spawn_teammate` 直接拉起（取消注册/拉起分离）**：推翻 `S_05` 不变量 1，影响面最大，
  且会拿掉"先规划名册再统一启动"这个能力。
- **只拉 assignee**：无主任务是 autonomous 的主要玩法之一，不覆盖等于没解决。

## 已知遗留

leader 若先 `create_task` 再 `spawn_teammate`，快路径拉不到还不存在的成员——由 round-idle
对账在这一轮结束时补上。这个顺序在 `build_team` 的工具描述里本来就不是推荐写法
（build → create_task → spawn_teammate），且延迟上限是一轮，不额外处理。
