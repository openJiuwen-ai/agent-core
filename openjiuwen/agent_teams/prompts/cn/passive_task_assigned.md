[任务指派] 任务 [{{task.task_id}}]「{{task.title}}」已指派给你。

## 任务内容

{{task.content}}

## 如何完成

你是被动人类成员：请通过你的外部通道实际完成这项工作，然后透传 `member_complete_task(task_id='{{task.task_id}}')` 标记完成。
- 需要查看详情时，透传 `view_task`。
- 需要澄清或协调时，透传 `send_message` 联系团队。
- 该任务的验证者：{{task.reviewer}}（如有）。
