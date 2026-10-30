[任务开工] 任务 [{{task.task_id}}]「{{task.title}}」已由调度框架启动并指派给你，现在开始执行。

## 目标与验收标准

{{task.content}}

## 上下游

- 上游（已完成、你可依赖的交付）：
{{task.upstream_brief}}
- 下游（等待你交付的任务）：
{{task.downstream_brief}}

## 交付方式

- 完成后调用 `member_complete_task(task_id='{{task.task_id}}', summary=..., note=...)` 提交：`summary` 一句话说明交付了什么、主产物在哪，`note` 写完整的产物路径与关键决策——它们会交给 Leader 与下游，提交后不需要另行汇报。
- 该任务的验证者：{{task.reviewer}}。提交后由调度框架转交验收，你不需要联系验证者。
- 执行中需要澄清或协调时，用 `send_message` 联系 leader。
