[任务修改] 你正在执行的任务 [{{task.task_id}}]「{{task.title}}」已被修改（当前版本 r{{task.revision}}）。请以下方的完整内容为准，按新要求调整后继续；与新要求无关、已经完成的部分不必返工。

{{task.change_notes}}

## 最新的目标与验收标准

{{task.content}}

{{task.revision_diff}}

完成后照常调用 `member_complete_task(task_id='{{task.task_id}}', summary=..., note=...)` 提交，不需要另外回复本消息。
