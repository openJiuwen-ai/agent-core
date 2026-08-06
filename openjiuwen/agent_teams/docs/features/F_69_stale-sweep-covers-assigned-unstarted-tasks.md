# 已指派未开工的任务：自催扫描面覆盖 + 首启板巡视

## 元信息
| 项 | 值 |
|---|---|
| 日期 | 2026-07-28 |
| 范围 | `openjiuwen/agent_teams/agent/coordination/handlers/stale_task.py`、`.../handlers/task_board.py`、`.../coordination/event_bus.py`、`.../coordination/dispatcher.py`、`.../coordination/kernel.py`、`.../agent/team_agent.py`、`openjiuwen/agent_teams/i18n.py` |
| 测试基线 | `pytest tests/unit_tests/agent_teams/` → 2182 passed / 16 skipped |
| Refs | #751 |

## 背景

自主模式下发现一个死角：团队有一个 `PENDING` 任务，静止一小时无人推进。

考古后确认这不是偶发，而是两条 sweep 之间的结构性缺口。F_65 建立的两个 sweep 按 assignee
把任务板划成两半：

| sweep | 覆盖 | 前置 |
|---|---|---|
| `_check_stale_claimed_tasks`（每个成员自催） | `assignee == self` 且状态 ∈ `{PLANNING, IN_PROGRESS}` | 本成员 idle 超阈值 |
| `_check_stale_pending_tasks`（仅 leader 自催） | **`assignee` 为空**的 `PENDING`（`stale_task.py`：`if not tk.assignee`） | leader idle 超阈值 + roster 有 READY 成员 |

**`PENDING(assignee=X)` 两边都不属于**：leader 那条被"无 assignee"前置过滤掉，X 自己那条
只扫已开工的两个条件。而这个状态恰恰是 leader 定向指派后的落点（F_59：`PENDING` 可带
assignee，表示"已指派未开始"）。

指派的唯一通知是 `TaskBoardHandler.on_task_claimed` 消费的 `TASK_CLAIMED`——**一个瞬时
transport 事件，不落邮箱、不持久化**。成员当时没起来（`UNSTARTED` 是 `spawn_member` 的默认
落点，只写 DB 行不启动进程）、或正忙于别的事而事件在投递路径上丢了，就再没有第二次提醒。
任务从此躺在板上，没有任何周期机制会再看它一眼。

需要说清楚的是**成员不会周期巡视任务板**——这是排查时最容易误判的一点。teammate 的
`POLL_TASK` 扫描只看自己已持有的任务；`TaskBoardHandler._nudge_idle_agent` 是纯事件驱动，
且 teammate 只被 `_TEAMMATE_NUDGE_EVENTS`（`TASK_CREATED` / `TASK_UNBLOCKED` /
`TASK_RELEASED`）唤醒。板上有活但没有新事件时，无人扫描。所以定向指派丢了就是永久丢了。

## 数据结构

无新增字段、无新增表。改的是"该扫哪些任务"这条规则本身，抽成一个方法：

```python
# agent/coordination/handlers/stale_task.py — StaleTaskHandler
async def _own_stalled_tasks(self, task_manager) -> list[Any]:
    """活跃优先，待开工兜底。"""
    owned_active = [PLANNING / IN_PROGRESS 中 assignee == self 的]
    if owned_active:
        return owned_active
    assigned = [PENDING 中 assignee == self 的]
    return [min(assigned, key=(updated_at, task_id))] if assigned else []
```

一句话规则：**我该推的任务 = 手上的活跃任务；手上一个活跃任务都没有时，才是被指派给我的
最早那个待开工任务。**

`_check_stale_claimed_tasks` 其余部分（idle 阈值、节流、streak、升级、GC）一行未动——它们
本就写成"对给定任务集做记账"，换掉集合的来源即可。

两个补救层，一快一慢，覆盖同一个缺口的两端：

| 层 | 触发 | 覆盖 |
|---|---|---|
| **首启板巡视**（`INITIAL_POLL_TASK`） | 成员 runtime 起来时，一次 | "指派时我根本不在"——最常见的情形，秒级生效 |
| **停滞自催扫描面**（`_own_stalled_tasks`） | 每个 `POLL_TASK` tick，idle 超阈值时 | "我在、但事件丢了 / 我读了没动"——`stale_claim_idle_timeout` 后兜住 |

## 决策

0. **首启投一次任务板巡视，对称于首启邮箱 sweep。** 邮箱 sweep 收的是停机期间发来的
   **消息**，这条收的是停机期间指派来的**活**。落点是新的 inner event
   `InnerEventType.INITIAL_POLL_TASK` + `kernel.enqueue_initial_task_poll()`，紧跟
   `enqueue_initial_mailbox_poll()` 之后投（先读消息、再看板），由
   `TaskBoardHandler.on_initial_task_poll` → `_nudge_idle_agent` 消费。
   **它复用既有的板巡视，没有新渲染逻辑**——`_nudge_idle_agent` 的 teammate 分支本就渲染
   "claimable（pending 且无 assignee）+ assignee == 自己的未完成任务"，且空片直接 return。
   这段代码的注释里早就写着「Assigned pending tasks may have been created before this
   member started; feeding them here makes **the startup poll** actionable」——设计意图
   一直在，只是那个 startup poll 从来没有被接上：唯一能触发它的
   `_nudge_idle_agent(from_poll=True)` 那行在 `stale_task.py` 里是注释掉的死代码，
   `from_poll` 参数至今没有 `True` 的调用者。
1. **待开工是 fallback，不是活跃集的增补。** `claim_task(status="claimed")` 有一活跃限额
   （`ClaimTaskTool.invoke` 查 `get_other_active_task_id`，有活跃任务直接拒），所以成员手上
   有活时，排队中的指派**本就开不了工**。把它并进活跃集会让每个窗口多发一条催促，而成员唯一
   能做的回应是撞上"你已有活跃任务"的拒绝。fallback 写法同时消掉了这个特殊情况——不需要
   任何"如果有活跃任务就跳过 pending"的分支，早返回即是。
2. **只取最早的一个。** 同理：成员一次只能开工一个，催整条队列只会产生 N-1 条注定被拒的
   催促。排序键 `(updated_at, task_id)` 与调度器 `_reconcile_starts` 的选取逻辑逐字一致
   ——两个模式对"下一个该开工的是哪个"的定义没有理由不同。
3. **任务当前处境进 nudge body（`{status}` 参数），而不是按状态分派两套文案。** 未开工要
   `claim_task(status='claimed')` 认领，已开工要继续推进——下一步动作确实不同，但这个差异
   LLM 从状态串就能读出来，不必让 handler 先判一次再选 key。一条文案覆盖三个条件，代码里零
   分支。`dispatcher.stale_idle_claim_self` 与 `..._escalate` 同步加参数（cn/en 成对）。
4. **升级路径不区分**：`PENDING` 任务连续 3 个窗口催不动，同样由成员自己 `send_message`
   上报 leader。对 leader 而言"指派出去但没开工"和"领了没推进"是同一类信号——都该问询 /
   改派 / 换人，没有理由分成两种上报。
5. **调度模式不动。** `ScheduledStaleTaskHandler` 覆写了整个 `_check_stale_claimed_tasks`，
   本次改的是自主基类，天然隔离。语义上也不该动：调度模式的 `PENDING(assignee)` 是**正常
   排队态**（等 owner 的一活跃限额释放），调度器 `_reconcile_starts` 会主动 `start_task`，
   把它当停滞是错的。
6. **`BLOCKED` 不纳入。** 依赖未满足是等待，不是停滞；依赖清掉后 `TASK_UNBLOCKED` 会把它
   翻回 `PENDING`，届时自然进入本 sweep。
7. **首启巡视对 leader 与 human-agent 都不投。** leader 与邮箱 poll 同理直接 return——它的
   板巡视渲染的是全板，空板时还会发 all-done 提示，不是一个刚起来的 leader 该被递到手里的
   东西。human-agent 则加进 `dispatch` 的 inner-event 静音元组：它是一次自主任务板巡视，
   正是 S_03 不变量 10 禁止 avatar 做的事。
8. **调度模式覆写为 no-op。** `ScheduledTaskBoardHandler.on_initial_task_poll` 只记一行
   debug。调度模式下成员不需要自己发现活：调度器的 start scan 每次 wake 都跑，交接是落进
   邮箱的**持久行**，首启邮箱 sweep 会 drain 它。再巡视一次板等于把同一个任务在调度器的
   邮件旁边又投一遍。

## 拒绝的方案

- **放宽 leader 的 stale-pending 前置，让它连 `assignee` 非空的一起催**——方向错了。leader
  收到催促后能做的是"指派给谁"，而这个任务已经指派过了；真正要被推动的是 assignee 自己。
  这还会退回 F_53 砍掉的 leader→成员跨进程催（leader 拿到提示后只能去 message 那个成员）。
- **让 `TASK_CLAIMED` 走邮箱持久化，从根上不丢指派**——治的是投递可靠性，值得单独做，但
  救不了"成员进程当时根本不存在"和"成员读了但没动"两种情形。停滞兜底是独立于投递可靠性的
  一层，两者不互相替代。
- **给 teammate 加周期任务板巡视**——正是 `_TEAMMATE_NUDGE_EVENTS` 和 F_65 给 `view_task`
  补反轮询抑制语所刻意消除的行为，每个窗口烧一轮 token 去重扫没变化的板。本方案只在成员
  **已经 idle 超阈值**且**名下确实有该推的任务**时才出声。
- **按状态分派两套 nudge 文案**——见决策 3，一个浅分支换不来任何 LLM 拿不到的信息。
- **把首启巡视挂到周期 `POLL_TASK` 上**（即取消注释 `stale_task.py` 里那行
  `_nudge_idle_agent(from_poll=True)`）——那样 teammate **每 30 秒**重扫一次没有变化的板，
  每次都可能烧一轮 token。这正是当初把那行注释掉的原因。板巡视要的是"起来时看一次"，
  语义上就是一次性事件，配一个独立的 inner event 才对，不该寄生在周期 tick 上。
- **只做首启巡视、不改停滞扫描面**——首启覆盖不了"我在线但事件丢了"和"我看到了但没动"。
  反过来只改扫描面则要等满一个 10 分钟窗口。两层都要。
- **PENDING 的停滞时长按"指派后过了多久"（`task.updated_at`）单独计时**——F_65 已经论证过
  这个数据源在 pause/resume 下从根上是错的，不该为新场景把它请回来。用成员自己的 idle 时钟
  的副作用是：成员已 idle 很久时新指派一丢，下一个 poll tick 就会催——这是想要的快速兜底，
  不是缺陷。

## 验证

- `pytest tests/unit_tests/agent_teams/` → **2182 passed / 16 skipped**（无 failed；F_65
  记录的那个 HEAD 既有失败 `monitor/test_models.py` 已在此前被修复）。
- 新增 3 个用例（`agent/test_mode_handlers.py`）覆盖首启巡视：
  - `test_startup_survey_surfaces_work_assigned_while_member_was_down` — 起来即看到指派给
    自己的任务
  - `test_startup_survey_is_silent_when_nothing_is_for_this_member` — 板上只有别人的活时
    不烧轮次
  - `test_scheduled_startup_survey_defers_to_the_scheduler` — 同一块板，自主投递、调度静默
- 新增 4 个用例（`test_team_agent_coordination.py`）覆盖扫描面：
  - `test_stale_claim_nudges_an_unstarted_assignment` — 无活跃任务 + `PENDING(assignee=self)`
    → 催，body 带处境串、不含任务正文
  - `test_stale_claim_backlog_yields_to_an_active_task` — 有活跃任务时排队的指派不被催
  - `test_stale_claim_backlog_nudges_only_the_earliest_assignment` — 多个排队指派只催最早的
  - `test_stale_claim_ignores_unassigned_pending` — 无主 `PENDING` 仍归 leader 那条 sweep
- 既有 `test_stale_claim_leader_ignores_other_members_claim` 的扫描面断言从
  `{"planning", "in_progress"}` 更新为含 `"pending"`——该用例验的是 self-only 语义，扫描面
  是顺带断言，行为变更符合预期。

## 已知遗留

- **`TASK_CLAIMED` 仍是瞬时事件**，指派通知本身的可靠性未改善；本特性只补了停滞兜底。让
  定向指派走邮箱持久化（对齐 F_63 调度器交接的做法）是值得单独评估的后续项。
- **第二层的兜底延迟等于 `stale_claim_idle_timeout`（默认 10 分钟）**。成员在线、刚进入
  idle 时被指派的任务（首启巡视覆盖不到这一路），最坏要等一个完整窗口才被催。缩短窗口会
  加重所有停滞检测的噪音，故未针对此场景单独调参。
- **`_nudge_idle_agent` 的 `from_poll` 参数仍然没有 `True` 的调用者**。本次没有顺手删它
  ——参数与那行注释掉的周期调用是同一段历史，是否连同删除属于独立的清理判断，不在本次
  范围内。
- **首启巡视每个 run cycle 都投一次，不只"首次启动"**（与 `enqueue_initial_mailbox_poll`
  同语义，两者都挂在 `invoke` / `stream` 的同一处）。pause → resume 频繁的团队会在每次
  resume 时重看一眼板；空片静默使这在多数情况下无成本，但板上确实有自己的活时会重复注入。
  若日后成为噪音，正确的修法是在 kernel 侧按 run cycle 去重，而不是把它挪回周期 tick。
- 调度模式的 `PENDING(assignee)` 仍完全依赖调度器 `_reconcile_starts`；调度器不在（leader
  崩溃且未恢复）时没有成员侧兜底。与 F_65 记录的调度模式 `updated_at` 遗留是同一片区域。
