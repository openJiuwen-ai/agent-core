[验收未过·返工] 任务 [{{task.task_id}}]「{{task.title}}」第 {{task.review_round}} 轮验收未通过（轮数上限 {{param.max_rounds}}），已打回给你返工。

## 验证者反馈

{{param.feedback}}

## 任务目标与验收标准

{{task.content}}

按反馈修复后，再次调用 `member_complete_task(task_id='{{task.task_id}}')` 提交验收。
