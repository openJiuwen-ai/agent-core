# 未开工的任务也能改派

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-08-15 |
| 范围 | `openjiuwen/agent_teams/schema/status.py`、`openjiuwen/agent_teams/tools/database/task_dao.py`、`openjiuwen/agent_teams/tools/task_manager.py`、`openjiuwen/agent_teams/tools/tool_task.py`、`docs/specs/S_03`、`docs/specs/S_08` |
| 测试基线 | `pytest tests/unit_tests/agent_teams/` → 2627 passed / 39 skipped / 3 xfailed |
| Refs | #984 |

## 背景

用户报告：调度模式下改不了 `PENDING` 任务的 assignee。

调度模式的任务生命周期是 `create_task(assignee=X)` → 任务停在 `PENDING(assignee=X)` →
调度器 `start_task` 推进到 `IN_PROGRESS`。`TaskStatus` 的 docstring 把这个状态写成一等
公民：

> "Assigned but not yet started" (scheduled) stays representable as `PENDING`
> with an assignee.

而 `update_task(task_id=..., assignee=Y)` 在任务已有 owner 时走
`TeamTaskManager.reassign` → `TaskDao.reassign_task`，后者的 CAS 是：

```sql
WHERE task_id = ? AND assignee = ? AND status = 'in_progress'
```

于是调度模式下的**常驻状态恰好是唯一改不了派的状态**：任务开工前无法改派，开工后反而
可以。这正好反了——开工前没有任何在途工作，交接零成本；开工后才要付出原 owner 半轮推理
的代价。

## 根因

**不是调度模式的问题，是状态集写窄了。** `reassign` 是 F_54 / F_56 为"把正在干的活从 A
换到 B"引入的，CAS 顺手钉死了当时唯一见过的状态。它的设计意图写在 DAO 注释里：

> so the task never bounces through PENDING (no spurious TASK_RELEASED, no
> claimable-pool window an idle teammate could race into)

这句话约束的是**状态不变**（`SET` 只动 assignee），不是**状态必须是 IN_PROGRESS**。把
"不改变状态"实现成"只允许一种状态"，是把不变量和它的一个实例搞混了。

autonomous 模式同样受害：leader `create_task(assignee=X)` 预指派出来的任务也是
`PENDING(assignee)`，一样改不了派。所以修法不能是加 `dispatch_mode` 分支。

## 决策

1. **`schema/status.py` 新增 `TASK_REASSIGNABLE_STATUSES`** = {`PENDING`, `BLOCKED`,
   `IN_PROGRESS`}，并在注释里写清判据——这是关于**归属**的问题，不是关于进度的，所以
   刻意不是终态的补集：
   - `PENDING` / `BLOCKED`：有主但没开工。交接不丢在途工作，是最安全的改派时机。
   - `IN_PROGRESS`：有主且在执行。代价是原 owner 的在途推理，由 `TASK_REVOKED` 让它停手。
   - `PLANNING` / `IN_REVIEW` **排除**：两个 gate 各有一份绑在**当前** owner 身上的产物
     （已提交的计划、正在被评判的成果），换人会让产物归属到没做过它的人头上。
   - 终态排除：没有 owner 可以移动。
2. **DAO CAS 改用该状态集**（`status.in_(...)`），`SET` 仍只动 assignee，因此**任务保持
   原状态**——`IN_PROGRESS` 的不经 PENDING（原有保证不变），`PENDING` 的也不会被顺手启动。
3. **`reassign` 的失败文案说清两种原因**（不再持有 / 状态不允许交接）并带上实际状态，
   否则 leader 拿到"it is no longer claimed by X"会去查一个并不存在的并发问题。
4. **`BLOCKED` 一并纳入**，虽然用户只提了 `PENDING`。它与 `PENDING` 是同一类（有主、
   没开工、无产物），安全论证逐字相同；只放 `PENDING` 等于留着同一个 bug 等下一次报告。

## 拒绝的方案

### A. 在 `UpdateTaskTool` 里按 `dispatch_mode` 分支，调度模式走另一条改派路径

否决理由：模式不是根因。autonomous 的预指派任务同样是 `PENDING(assignee)`，同样改不了派。
按模式分支既修不全，又违反"形态选择只发生在装配期、`invoke` 内零模式分支"（`S_08`
不变量 18）。

### B. 改派前先 `reset` 回 PENDING、再 `assign` 给新 owner

否决理由：这正是 F_56 删掉的旧实现。reset 会发 `TASK_RELEASED` spurious 唤醒所有空闲
teammate，并开出一个 claimable-pool 窗口让别人抢先认领 —— 而且对一个本来就 `PENDING` 的
任务，"先 reset 再 assign"还会把它推进 `IN_PROGRESS`（`assign` 带 entry gate），等于用改派
顺手把任务启动了，在调度模式下直接绕过 `start_task`。

### C. 放开全部非终态（含 `PLANNING` / `IN_REVIEW`）

否决理由：那两个状态各有一份绑在当前 owner 身上的产物。`IN_REVIEW` 尤其糟——reviewer 正在
判的成果会突然记到新 owner 名下，verify 闸的票据（`team_review_vote_*`）却仍指向旧 author。
需要改派一个卡在 gate 里的任务时，正确做法是先退出 gate（拒绝计划 / 打回返工），再改派。

## 已知遗留

- **新 owner 收到的 `TASK_CLAIMED` 通知文案未按状态分化。**
  `dispatcher.task_assigned_to_self` 说的是"请通过 view_task 查看任务详情并执行"，对一个
  仍是 `PENDING` 的任务，调度模式下的新 owner 并不能自己开工（它没有 `claim_task`，要等
  调度器 `start_task`）。成员调 `view_task` 会看到真实状态，所以不会做错事，但文案确实
  多余地催了一句。要治的话是按任务状态选文案，不是按 dispatch_mode——本次未做。
- `UpdateTaskTool` 对**无 owner** 的 `PENDING` 任务仍走 `assign()`，而 `assign()` 带 entry
  gate，会把任务直接推进 `IN_PROGRESS` / `PLANNING`。调度模式下这绕过了 `start_task`。
  调度模式的 `create_task` 强制 `assignee`，所以无主任务在该模式下基本不出现，本次未动；
  真要修属于 `assign` 的语义问题，与本次的改派路径正交。
