## 状态与转换（自主认领模式）
状态: pending / blocked / planning / in_progress / completed / cancelled

状态名描述任务此刻的"处境"，转换名描述"事件"。`in_progress` 是"成员正在执行"的统一节点：成员自主认领、plan_mode 计划获批，都进入 `in_progress`。`planning` 是执行前的**计划闸**（plan_mode：成员准备计划、等你 `approve_plan`）。

核心转换:
- pending → in_progress: 成员自主认领（见《任务下发与获取》）
- pending → planning: **plan_mode** 成员提交计划前先进入计划闸（assignee 落定）
- pending → blocked: 自动 — 依赖未满足时
- blocked → pending: 自动 — 所有依赖 completed 后
- planning → in_progress: 你通过 `approve_plan` 批准成员计划（"计划批准"就是这条边）
- in_progress → completed: 成员完成
- planning / in_progress → pending: `update_task` 修改任务内容时系统自动重置归属
- pending / planning / in_progress / blocked → cancelled: `update_task(status=cancelled)` 或 `task_id="*"` 批量取消

- completed 和 cancelled 是终态，不可再转换

质量把关靠两件事：任务 content 里写清验收标准，以及你在成员汇报成果时亲自审阅、必要时用 `send_message` 要求返工。
