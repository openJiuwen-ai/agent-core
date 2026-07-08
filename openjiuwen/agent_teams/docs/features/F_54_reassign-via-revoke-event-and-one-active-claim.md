# 基于 TASK_REVOKED 事件的任务改派 + 一成员一活跃认领

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-07-07 |
| 范围 | `schema/events.py` · `tools/task_manager.py` · `tools/tool_task.py` · `agent/coordination/handlers/task_board.py` · `i18n.py` |
| 测试基线 | agent_teams level0：745 passed, 2 skipped |
| Refs | #751 |

## 背景

线上日志复盘出一次「任务改派误杀整个 worker」的事故：leader 先把 `KS-1`、`KS-2` 两个任务都指派给
`lit-searcher-1`，9 秒后改主意，调 `update_task(task_id="KS-2", assignee="lit-searcher-2")` 想把
`KS-2` 挪给另一个成员。`UpdateTaskTool` 的 reassignment 分支为此调用了 `cancel_member(lit-searcher-1)`，
而 `cancel_member` 的语义是**取消整个成员的执行**：它把 `lit-searcher-1` 名下**所有** CLAIMED 任务
（`KS-1` + `KS-2`）全部 reset 回 PENDING，并取消它正在运行的 deep_agent_task（已跑了约 10 分钟、
烧掉约 78 万 input token）。为了挪走一个任务，把整个 worker 连同它正在做的另一个任务一起牺牲了。

同一段代码还藏着一个必现 bug：reassignment 分支先 `cancel_member` 再 `reset(task_id)`，而
`cancel_member` 内部已经把该任务 reset 成 PENDING 了，外层紧跟的 `reset` 必然撞上「只有 CLAIMED 能
reset」的状态校验而失败，导致 `update_task` 整体返回 `success=False`——**改派操作本身失败，却已经
造成了误杀的既成事实**，只能靠自愈机制兜底。

根因是两层：（1）粒度错配——task 级的改派用了 member 级的取消；（2）一个成员能同时持有多个 CLAIMED
任务，放大了「动一个牵连一片」的破坏面。

## 数据结构 / 状态机

任务状态机不变（`PENDING ⇄ CLAIMED → COMPLETED/CANCELLED`，`reset` 把 CLAIMED 拉回 PENDING 并清
`assignee`）。本次新增两样东西：

- **`TeamEvent.TASK_REVOKED` + `TaskRevokedEvent`**：`member_name` 携带**被撤任务的原 owner**。语义与
  `TASK_RELEASED` 正交——`TASK_RELEASED` 面向 idle 池「有活可抢」，`TASK_REVOKED` 是精准投给失去任务
  的那个成员的「停手」通知。反向映射 `_EVENT_CLASS_MAP` 由 `_EVENT_TYPE_MAP` 自动推导，无需手工登记。
- **「一成员一活跃 CLAIMED」不变量**：由工具边界强制，靠 `TeamTaskManager.get_other_claimed_task_id(
  member, exclude_task_id)` 查询支撑——DB 层单列 `task_id` 投影 + `LIMIT 1` 的存在性探测，只回一个
  task_id（拼错误消息用）或 `None`，**不物化该成员的全部 claimed 行**；`exclude_task_id` 保证幂等
  re-claim / re-assign 同一任务不被误判为冲突。

改派后的完整时序（`TeamTaskManager.reassign`）：先校验新成员存在 → `reset`（发 `TASK_RELEASED`）→ 若
有原 owner 则发 `TASK_REVOKED` → `assign`（发 `TASK_CLAIMED`）。

## 决策

1. **`update_task` 改派不再 `cancel_member`，收敛为 `TeamTaskManager.reassign`。** 只动目标任务：
   reset 该任务、发 `TASK_REVOKED` 通知原 owner、assign 给新 owner。原 owner 的其它 CLAIMED 任务和
   正在跑的 round 全部保住。顺带根除了 `cancel_member + reset` 的 double-reset 必现失败。

2. **新旧两侧通知走两个分开的事件，不走 `send_message`。** 成员通知的既有通道是「事件 → dispatcher →
   `deliver_input`」；`send_message` 是 mailbox，混用会破坏通道统一性。新 owner 复用既有
   `TASK_CLAIMED → on_task_claimed`（「任务已指派给你」）；原 owner 走新增
   `TASK_REVOKED → on_task_revoked`（`deliver_input(use_steer=True)` 打断它当前 round，提示停手并
   `view_task` 找下一个可认领任务）。

3. **新成员校验前置到 reset 之前。** `reassign` 先确认新成员存在再动状态，避免 reset 之后 assign 失败
   把任务留成无主 PENDING。

4. **「一成员一活跃 CLAIMED」在工具边界强制。** `ClaimTaskTool`（teammate 自认领）和 `UpdateTaskTool`
   （leader 指派）在真正写状态前各查一次 `get_other_claimed_task_id`；`UpdateTaskTool` 的检查放在
   reassignment reset **之前**，拒绝时原任务与当前 owner 均不受扰动。校验落在工具边界符合模块既有
   原则（不可信的 LLM 输入在进入处校验一次），且外部 CLI 成员复用同一套 `create_team_tools` 也自动
   受约束。

## 拒绝的方案

- **用 `send_message` 给原/新 assignee 发 mailbox 通知。** 最初的提案。被否——成员通知的统一通道是事件
  驱动的 `deliver_input`，`send_message` 往 mailbox 里塞会分裂通道；且新 owner 的通知本就由既有
  `TASK_CLAIMED` 覆盖，再发一条 mailbox 消息是内容重复。

- **给新 assignee 在工具层也显式发一条通知。** 被否，同上——`assign` 内部已发 `TASK_CLAIMED`，
  `on_task_claimed` 已渲染「任务已指派给你」，工具层再发即重复。新 owner 复用既有机制，工具层只负责补
  原 owner 缺失的那半。

- **`on_task_revoked` 里保留 human-agent 分支。** 被否，是死代码——human-agent 持有的 CLAIMED 任务被
  `UpdateTaskTool` 的 `_is_human_agent_locked` 挡在改派之外，`TASK_REVOKED` 的 `member_name` 永远不会
  是 human avatar，无需 HITT 渲染分支，也无需把 `TASK_REVOKED` 加进 dispatcher 的 human-agent 白名单。

- **把「一 claimed」下推到 DB 做原子约束。** 过度设计。成员的 agent 是单 event loop 顺序调用，leader
  也是顺序调 `update_task`，工具边界「先查后写」的窗口在本运行模型下不构成真实竞态；DB 级唯一约束是
  YAGNI。

## 验证

- `test_task_manager.py`：`get_other_claimed_task`（命中其它 claimed / 排除自身 / 无 claim 返回 None）、
  `reassign`（转移 + 发 `TASK_REVOKED(member=旧 owner)` + 新 owner 收 `TASK_CLAIMED`）、新成员不存在时
  任务原封不动且不发 revoke。
- `test_team_tools.py`：`update_task` 拒绝指派给已有 CLAIMED 的成员（两任务均不受扰动）、`claim_task`
  拒绝第二次并发认领；更新旧的 `test_assign_reassigns_to_new_member` 断言 `cancel_member` 未被调用 +
  发出 `TASK_REVOKED`。
- `test_team_agent_coordination.py`：`on_task_revoked` 对自身撤回 `deliver_input`（含 `[任务撤回]` /
  task_id / `view_task`）、对他人撤回忽略。
- 全量 agent_teams level0 冒烟 745 passed，零回归。

## 已知遗留

- 「一成员一活跃 CLAIMED」只按字面 `CLAIMED` 计数，不含 `PLAN_APPROVED`。plan-mode 成员若持有一个
  `PLAN_APPROVED` 任务仍可认领新任务。当前按需求字面实现；若要「一次只干一个活」的更强语义，再扩计数
  口径。
