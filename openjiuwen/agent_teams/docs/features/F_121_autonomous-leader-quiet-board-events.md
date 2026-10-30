# 自主模式 leader 只为需要它决策的看板事件醒来

## 元信息
| 项 | 值 |
|---|---|
| 日期 | 2026-10-14 |
| 状态 | 已实施 |
| 范围 | `agent/coordination/handlers/task_board.py`（`TaskBoardHandler`）；spec：`S_03_coordination-protocol.md` |
| 测试基线 | `tests/unit_tests/agent_teams`：3620 passed（实施时既有用例全部通过，说明没有用例依赖 leader 被纯进度事件唤醒）+ 本 feature 新增 4 例；排除既有不稳定用例 `test_member_activity.py::test_observe_member_status_emits_after_the_debounce` |
| 关联 | F_120（完成唤醒附"刚完成"） |

## 背景

自主模式下 `TaskBoardHandler` 让 leader **每个看板事件都巡视一次全部未完成任务**（S_03：
"leader 每个 board 事件都巡视全部未完成任务"，理由是它掌管看板级决策、必须看见每次跃迁）。
teammate 侧早已按"会不会扩大可认领池"做了过滤（`_TEAMMATE_NUDGE_EVENTS`），leader 侧没有。

会话 `web_1a1393d66c5_0d8d9d966482` 第二轮：leader 建 5 个串行任务、全部指派给同一名成员。
5 个任务期间 leader 被唤醒约 18 次，几乎每次只回一句"进度正常，无需调整"。每个任务的唤醒来源：

| 触发 | leader 收到 | 需要 leader 决策吗 |
|---|---|---|
| 成员 `send_message` "bs-x 已完成" | 汇报 | 否（已由 `1cb037106` 在 leader 提示词侧收敛） |
| `TASK_COMPLETED` | "刚完成" + 看板 | **是**：审阅结果、决定下一步 |
| 下一个任务 `TASK_UNBLOCKED` | 看板（该任务 `pending`） | 否：它已有 assignee，承担者自己会被唤醒去认领 |
| 承担者认领 → `TASK_CLAIMED` 非自身 fall-through | 看板（该任务 `in_progress`） | 否：纯进度变化 |

后两类每次都是一轮完整的模型调用（看板正文 + 系统提示），却不产生任何决策。

## 目标

- 自主模式 leader 只在**需要它做决定**的看板事件上被唤醒；纯进度类事件只续上轮询，不唤醒。
- 不丢信息：leader 下一次被唤醒时看到的仍是**完整的当前看板**，被跳过的中间态只是不再单独播报。
- teammate 行为、调度模式（`ScheduledTaskBoardHandler`）、自身定向通知（被指派 / 撤回 / 取消 /
  改内容的 self 分支）一律不变。

## 设计

### D1 leader 事件分类

| 事件 | 现状 | 方案 | 理由 |
|---|---|---|---|
| `TASK_COMPLETED` / `TASK_VERIFIED` | 唤醒 | **唤醒**（附"刚完成"，F_120） | 审阅结果、决定下一步 |
| `TASK_CREATED`（他人创建） | 唤醒 | 唤醒 | 看板出现 leader 不知道的新工作 |
| `TASK_PLAN_REQUEST` | 唤醒 | 唤醒 | plan 闸要 leader 审批 |
| `TASK_RELEASED` | 唤醒 | 唤醒 | 任务回到公共池，可能需要改派 |
| `TASK_CANCELLED` / `TASK_UPDATED`（非自身 fall-through） | 唤醒 | 唤醒 | 罕见，且可能影响规划 |
| `TASK_UNBLOCKED`，**任务无 assignee** | 唤醒 | 唤醒 | 进入公共池；若只有 `human_agent` 能做，leader 须显式指派（`dispatch_autonomous_leader` 已有此要求） |
| `TASK_UNBLOCKED`，**任务已有 assignee** | 唤醒 | **不唤醒** | 承担者自己会被唤醒去认领；leader 无事可做 |
| `TASK_CLAIMED`（非自身 fall-through） | 唤醒 | **不唤醒** | 认领 / 指派生效只是进度变化 |
| `TASK_STARTED`（自主模式下仅真人承担者的自动开工） | 唤醒 | **不唤醒** | 同上 |
| `TASK_SUBMITTED_FOR_REVIEW` / `TASK_REVISION_REQUESTED`（非目标成员 fall-through） | 唤醒 | **不唤醒** | 验收往返由作者与验证者处理；结论（通过）走 `TASK_VERIFIED` 唤醒 |

"不唤醒"仍执行 `resume_polls()`（与 teammate 过滤一致：过滤只砍 nudge、不停轮询）。

### D2 实现形态

- `TaskBoardHandler` 新增 `_LEADER_QUIET_EVENTS: ClassVar[frozenset[str]]`
  = `{TASK_CLAIMED, TASK_STARTED, TASK_SUBMITTED_FOR_REVIEW, TASK_REVISION_REQUESTED}`，与
  `_TEAMMATE_NUDGE_EVENTS` 并列声明、同样写清每类为什么不唤醒。
- `on_task_board_event` 的 leader 分支在 `resume_polls()` 之后、`_nudge_idle_agent` 之前判定：
  - 事件属于 `_LEADER_QUIET_EVENTS` → 记 debug 日志后返回；
  - 事件是 `TASK_UNBLOCKED` → 现查该任务行，`assignee` 非空则返回（任务行已删也返回）。
- 判定收在 `_leader_needs_no_decision(event)`；只多一次 `TASK_UNBLOCKED` 时的单行查询，其余判定是纯集合查询。
- `ScheduledTaskBoardHandler` 覆写了 `on_task_board_event`，不受影响。

### D3 兜底：被跳过的情况由谁接住

| 风险 | 兜底 |
|---|---|
| 已指派任务解锁后，承担者迟迟不认领 | `StaleTaskHandler` 的停滞检测（stale pending / stale claimed，按 `stale_*_idle_timeout`）照常提示 leader |
| leader 想看中间进度 | 下一次被唤醒（完成 / 新建 / 释放 / 停滞 / 用户消息）时看板是完整快照；也可随时 `view_task` |
| 验收往返卡住 | 作者与验证者各自被定向唤醒；卡住时同样落入停滞检测 |

### D4 预期效果

同一会话：每个任务的 leader 唤醒从 3 次（完成 + 解锁 + 认领，不计已修的成员汇报）降到 1 次（完成），
5 个串行任务约 15 次 → 5 次。并行任务、无 assignee 的公共池任务的唤醒次数不变。

## 决策

1. **按"是否需要 leader 决策"过滤 leader 的看板唤醒**，与 teammate 侧"是否扩大可认领池"的过滤对称。
2. **`TASK_UNBLOCKED` 按 assignee 分流**：只有进入公共池的才可能需要 leader 介入。
3. **不改投递方式**：被保留的唤醒仍走 `use_steer=False` 追加，leader 看板仍不做 superseded 裁剪
   （它读的是跃迁序列，F_71 的豁免理由不变）。

## 拒绝的方案

- **对 leader 的看板唤醒做时间窗合并（debounce）**：引入定时器与延迟，"刚完成"会被推迟；
  事件顺序与合并边界都要额外定义。分类过滤已能去掉绝大多数无效唤醒。
- **把 leader 也纳入 `_drop_superseded`**：只能在 leader 忙时合并排队的看板，leader 空闲时每个事件
  仍各开一轮；且 leader 看板豁免裁剪是有意为之（它要看跃迁序列）。
- **只保留完成类事件**：会丢掉 plan 审批、任务释放、无主任务解锁等真正需要 leader 的时刻。

## 验证

`agent/test_mode_handlers.py`：

| 用例 | 覆盖 |
|---|---|
| `test_leader_is_not_woken_by_progress_only_events` | 他人认领（`on_task_claimed` 兜底）、`TASK_STARTED`、验收提交 / 打回兜底都不投递，`resume_polls` 每次仍被调用 |
| `test_leader_unblock_wakes_only_for_an_unowned_task` | 已指派任务解锁不投递；任务行不存在不投递；无主任务解锁投递 |
| `test_leader_still_wakes_for_decision_events` | `TASK_CREATED` / `TASK_RELEASED` / `TASK_COMPLETED` 照常投递 |
| `test_teammate_still_wakes_on_an_assigned_unblock` | teammate 侧 `TASK_UNBLOCKED` 唤醒不变 |

## spec 修订

| spec | 修订 |
|---|---|
| `S_03` | `TaskBoardHandler` 一行："leader 每个 board 事件都巡视"改为"leader 只在需要决策的事件上巡视（`_LEADER_QUIET_EVENTS` 与已指派任务的 `TASK_UNBLOCKED` 不唤醒，只续轮询）"；`agent/coordination/handlers/AGENTS.md`"看板巡视"一节同步 |

## 已知遗留

- 停滞检测的阈值（默认 600s）决定了"已指派但没人认领"的发现时延；若需要更快发现，可单独调阈值，
  不在本 feature 范围。
