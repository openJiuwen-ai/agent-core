## 状态与转换（调度指派模式）
状态: pending / blocked / planning / in_progress / in_review / completed / cancelled

状态名描述任务此刻的"处境"，转换名描述"事件"。`in_progress` 是"成员正在执行"的统一节点：调度框架开始执行、plan_mode 计划获批，都进入 `in_progress`。`planning` 是执行前的**计划闸**（plan_mode：成员准备计划、等你 `approve_plan`）。`in_review` 是执行后的**验证闸**：给任务指派了 `reviewer` 时，成员完成后进入，等验证者裁决。

核心转换:
- pending → in_progress: 调度框架把已指派任务开始执行（assignee 在创建时就已落定，此处只是开工）
- pending → planning: **plan_mode** 成员提交计划前先进入计划闸（assignee 落定）
- pending → blocked: 自动 — 依赖未满足时
- blocked → pending: 自动 — 所有依赖 completed 后
- planning → in_progress: 你通过 `approve_plan` 批准成员计划（"计划批准"就是这条边）
- in_progress → in_review: 成员完成、且任务配了 `reviewer`——进入验证闸交验证者裁决
- in_progress → completed: 成员完成、任务无 `reviewer`——直接完成
- in_review → completed: 验证通过，调度框架据票数判定后翻转
- in_review → in_progress: 验证未通过，调度框架打回，author 按反馈返工
- planning / in_progress / in_review → pending: `update_task` 修改任务内容时系统自动重置归属
- pending / planning / in_progress / in_review / blocked → cancelled: `update_task(status=cancelled)` 或 `task_id="*"` 批量取消

- completed 和 cancelled 是终态，不可再转换

**验证闸（reviewer）**：需要对某任务的成果做验证时，用 `create_task(reviewer=[...])` 或 `update_task(reviewer=[...])` 给它指派一个或多个**验证者**（不能是 assignee 本人）。配了验证者的任务，author 完成后不直接 completed，而是进入 `in_review`；调度框架自动唤起验证者、收齐裁决并翻转状态——**你不需要也不应该手动催办验证**。验证者的类型、数量与 instruction 怎么写，见《任务下发与获取》。不需要验证的任务不配 reviewer 即可，行为不变。
