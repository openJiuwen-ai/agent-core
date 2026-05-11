把指派给当前成员的任务标记为完成。

## 何时使用

- Leader 通过 `update_task(assignee=<你的 member_name>)` 把任务派给你（任务进入 `claimed` 状态、assignee 指向你）之后，你完成了实际工作，调用本工具把状态推进到 `completed`。
- 仅适用于「assignee 等于你自己」的任务；其他任务调用会报错。

## 输入

- `task_id`（必填）：要完成的任务 ID。
- `note`（可选）：完成说明，写明产出物路径、关键决策或团队需要注意的事项。

## 与其它工具的边界

- 不要用本工具去 *领取* 任务 —— 它只负责「完成」，不负责「认领」。任务由 leader 指派；你不应该自主认领。
- 与 leader 的 `update_task` 不同：`update_task` 管理团队层面的任务图（取消、改派、修改内容、添加依赖），不属于成员能力。
- 与 teammate 的 `claim_task` 不同：`claim_task` 是「认领后再完成」的复合工具；本工具是「只完成」的成员工具。

## 失败码

- `Task '<id>' not found`：任务不存在。
- `Task '<id>' is assigned to '<other>', not '<you>'; you can only complete tasks assigned to yourself`：任务不是指派给你的。
- 其它（来自 task_manager.complete）：当前任务状态不允许完成（例如已 cancelled、未 claimed 等）。
