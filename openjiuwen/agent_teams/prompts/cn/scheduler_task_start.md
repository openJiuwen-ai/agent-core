[任务开工] 任务 [{{task.task_id}}]「{{task.title}}」已由调度框架启动并指派给你，现在开始执行。

## 目标与验收标准

{{task.content}}

## 交付方式

- 完成后调用 `member_complete_task(task_id='{{task.task_id}}')` 提交。
- 该任务的验证者：{{task.reviewer}}。提交后由调度框架转交验收，你不需要联系验证者。
- 执行中需要澄清或协调时，用 `send_message` 联系 leader。
