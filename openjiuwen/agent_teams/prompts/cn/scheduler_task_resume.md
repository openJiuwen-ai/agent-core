[任务继续] 你拆出的子任务都已结束（含被取消的），任务 [{{task.task_id}}]「{{task.title}}」已由调度框架重新开始，请接着完成它。

## 目标与验收标准

{{task.content}}

## 子任务与上游结果

{{task.upstream_brief}}

上面列出了子任务和本任务其他直接上游的完成摘要；没有摘要的子任务可能已被取消，用 `view_task(action=get, task_id=...)` 查看状态。

## 交付方式

- 把子任务的结果汇总进本任务的交付，完成后调用 `member_complete_task(task_id='{{task.task_id}}', summary=..., note=...)` 提交——子任务的结果不会单独报给 Leader，你的 `summary` / `note` 就是整体交接。
- 下游（等待你交付的任务）：
{{task.downstream_brief}}
- 该任务的验证者：{{task.reviewer}}。提交后由调度框架转交验收，你不需要联系验证者。
