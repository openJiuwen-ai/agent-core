# 任务级信号取代 member 级 cancel（改派原子化 + 取消/编辑轻量化）

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-07-08 |
| 范围 | `tools/database/task_dao.py` · `tools/task_manager.py` · `tools/tool_task.py` · `agent/coordination/handlers/task_board.py` · `i18n.py` · `tools/locales/`（descs + cn/en） |
| 测试基线 | agent_teams level0：749 passed, 2 skipped |
| Refs | #751 |

## 背景

[[F_54_reassign-via-revoke-event-and-one-active-claim]] 把 reassign 从 `cancel_member`（member 级重锤）
改成了 `TASK_REVOKED` 事件，但留了三个尾巴，复盘时暴露：

1. **改派仍会 spurious 唤醒所有空闲 teammate**。F_54 的 `reassign` 用 `reset` + `assign` 实现，而 `reset`
   无条件发 `TASK_RELEASED`——它在 `_TEAMMATE_NUDGE_EVENTS` 里，会唤醒**所有**空闲 teammate 去看板。但任务
   下一步立刻被 `assign` 给了新成员，根本不该进可认领池。于是每次改派都白白唤醒 N 个空闲成员（浪费 N 轮
   token），还留了一个 reset→assign 之间的抢占窗口。
2. **cancel 和 content-edit 两条路仍走 `cancel_member`**。cancel 一个已认领任务、或改它的标题/内容，都会
   `_cancel_member_if_claimed` → `cancel_member`，把整个成员连同其它工作一起端掉。其中 content-edit 更糟：
   DB 层 `update_task` 只允许编辑 pending/blocked 任务，所以编辑已认领任务时**先 reset 成 pending（解除认领）
   再 cancel_member（杀成员）**——等于「改个任务描述就把它踢回可认领池、还杀掉正在做它的 worker」，是个隐藏
   缺陷。
3. **LLM 面向的工具描述过时甚至错误**。`update_task` 描述说 assign「仅当任务当前无 assignee 时生效」，与
   reassign 特性直接矛盾；说 content-edit / cancel「自动取消执行中的成员」。LLM 据此形成的心智模型是错的。

## 数据结构 / 状态机

统一到一个模式：**当某成员正在做的任务被从它手里动了（改派 / 取消 / 改内容），给它发一个精准的任务级事件，
由它自己的 dispatcher steer 它**，而不是 member 级 `cancel_member`。

- **改派**：新增 DAO 层 `reassign_task(task_id, from, to)` —— 一条 CAS `UPDATE SET assignee=to WHERE
  assignee=from AND status='claimed'`。任务全程保持 CLAIMED、assignee 直接 A→B，不经 PENDING。`reassign` 只发
  `TASK_REVOKED`（旧 owner，停手）+ `TASK_CLAIMED`（新 owner，接手），**不发 `TASK_RELEASED`**，抢占窗口也随之
  消失。
- **取消 / 编辑**：复用现有 `TASK_CANCELLED` / `TASK_UPDATED`（**不新增事件类型**），补上 `member_name`（受影响
  成员）。`TaskBoardHandler` 加两个 self 分支：`on_task_cancelled`（自身→「已取消，停手，view_task」steer）/
  `on_task_updated`（自身→「内容已更新，view_task 重看后继续，任务仍归你」steer）；非自身 fall-through 到
  `on_task_board_event`（leader 巡视兜底，与 `on_task_claimed` 一致）。
- **编辑放开 CLAIMED**：DAO `update_task` 原本挡 CLAIMED / PLAN_APPROVED，现在放开 CLAIMED（PLAN_APPROVED 仍锁），
  这样编辑已认领任务无需再 reset。

## 决策

1. **改派 = DAO 原子 CAS 交换**，消除 spurious `TASK_RELEASED` 与 reset→assign 抢占窗口；改派只影响这一个任务。
2. **cancel/edit 复用现有事件 + `member_name`**，不新增事件类型；`TaskBoardHandler` 加 self 分支精准 steer，
   非自身 fall-through 到 board。
3. **编辑保持认领、通知重看**（不再 reset+杀成员）；DAO 放开 CLAIMED 可编辑。修掉「改描述踢回池子」的隐藏缺陷。
4. **content-edit 纳入 HITT 锁**：人类成员认领的任务不可改标题/内容（与 cancel / reassign 同锁）。因此
   `TASK_REVOKED` / `TASK_CANCELLED` / `TASK_UPDATED` 的 `member_name` 永远不会是 human avatar，两个新 self
   分支都无需 HITT 渲染分支——保持干净。
5. **删除死代码** `_cancel_member_if_claimed` / `_cancel_claimed_members` / `_is_cancellable_assignee`
   （去掉调用后全无引用）。`cancel_member`（backend）保留给 `shutdown_member`。
6. **重写工具描述**（`update_task.md` / `claim_task.md`，cn+en）：删掉「仅当无 assignee 生效」「自动取消成员」
   这些错的/过时的，补 reassign 语义、one-claim 约束、cancel/edit 的新信号语义、编辑纳入 HITT 锁。
7. **集成测试走真实 dispatch 路由**：`dispatcher.dispatch(event)` 路由 `TASK_REVOKED` / `TASK_CANCELLED` /
   `TASK_UPDATED` 到各自 self 分支，补齐 F_54 时只手调 handler、没验证 `EVENT_METHOD_MAP` + framework 路由的盲区。

## 拒绝的方案

- **为 cancel/edit 新增独立事件类型**：复用现有 `TASK_CANCELLED` / `TASK_UPDATED` + `member_name` 更省，语义也
  贴合（这些事件本就代表这件事发生了，只是过去没带受影响成员）。
- **content-edit 保留「解除认领」语义**：那是隐藏缺陷——编辑任务内容不该把它踢回可认领池。改为保持认领、通知重看。
- **content-edit 对 human 任务保留可编辑 + 在 `on_task_updated` 加 human 渲染分支**：不如直接把 edit 纳入 HITT
  锁——与 S_08 既有「人类认领任务 leader-immutable」不变量一致，且让两个新 handler 免于 human 分支。
- **保留 `reset` 复用**（F_54 的 reset+assign 改派）：图省事复用现成原语，但把 `reset` 的副作用（`TASK_RELEASED`）
  带进了不该有的场景。原子 CAS 更贴合改派语义。

## 验证

- 单测：`reassign` 原子交换 + 不发 `TASK_RELEASED`；`cancel` 发 `TASK_CANCELLED(member)`；编辑 CLAIMED 任务成功 +
  保持认领 + 发 `TASK_UPDATED(member)`；DAO `update_task` 放开 CLAIMED / 仍锁 PLAN_APPROVED；`on_task_cancelled` /
  `on_task_updated` self 分支 steer；工具层 cancel/edit 不再调 `cancel_member`；human 锁定任务拒绝编辑。
- 集成（③）：`dispatcher.dispatch` 真实路由 `TASK_REVOKED` / `TASK_CANCELLED` / `TASK_UPDATED` 到 self 分支。
- 全量 agent_teams level0 冒烟 749 passed，零回归。

## 已知遗留

- `cancel_all`（`task_id="*"`）逐任务发 `TASK_CANCELLED` 精准通知各成员——团队规模大时事件条数多，但每条都是精准
  通知、无空转唤醒。
- PLAN_APPROVED 状态的任务仍不可编辑 / 改派（DAO 编辑锁 + reassign CAS 都要求 CLAIMED）。plan-mode 是特殊流程，
  暂按此约束，需要时再放开。
