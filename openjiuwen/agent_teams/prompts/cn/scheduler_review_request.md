[验收指派] 任务 [{{task.task_id}}]「{{task.title}}」的承担者 {{task.assignee}} 已提交交付物（第 {{task.review_round}} 轮验收）。你是该任务的验证者之一，请现在验收。

## 任务目标与验收标准

{{task.content}}

## 验收步骤

1. 对照上面的验收标准检查交付产物。
2. 调用 `verify_task(task_id='{{task.task_id}}', decision='pass'|'fail')` 投票；`fail` 时在 `feedback` 中写明具体返工要求。
3. 投票后无需跟进——调度框架按票数判定并推进（本任务验证者：{{task.reviewer}}）。
