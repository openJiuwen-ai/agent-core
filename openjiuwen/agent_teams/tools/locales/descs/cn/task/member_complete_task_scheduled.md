把指派给你的任务标记为完成。

## 何时使用

- Leader 通过 `create_task(assignee=<你的 member_name>)` 或 `update_task(assignee=...)` 把任务派给你、调度框架为你开始（任务进入 `in_progress` 状态、assignee 指向你）之后，你完成了实际工作，调用本工具把状态推进到 `completed`。
- 仅适用于「assignee 等于你自己」的任务；其他任务调用会报错。

## 输入

- `task_id`（必填）：要完成的任务 ID。
- `note`（可选）：完成说明，写明产出物路径、关键决策或团队需要注意的事项。

## 与其它工具的边界

- 本工具只负责「完成」。本团队运行在**调度指派模式**，任务由 Leader 指派，你没有也不需要认领任务的能力。
- 与 leader 的 `update_task` 不同：`update_task` 管理团队层面的任务图（取消、改派、修改内容、添加依赖），不属于成员能力。
- 任务范围不合理、或你判断自己不适合承担时，用 `send_message` 请 Leader 改派——不要默默不做，也不要去动别人的任务。

## 失败码

- `Task '<id>' not found`：任务不存在。
- `Task '<id>' is assigned to '<other>', not '<you>'; you can only complete tasks assigned to yourself`：任务不是指派给你的。
- 其它（来自 task_manager.complete）：当前任务状态不允许完成（例如已 cancelled、尚未进入 in_progress 等）。
