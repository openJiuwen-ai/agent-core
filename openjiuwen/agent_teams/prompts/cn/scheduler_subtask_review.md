[子任务复核] 你挂起的任务 [{{task.task_id}}]「{{task.title}}」已被修改（当前版本 r{{task.revision}}），它的子任务是按旧版本拆出来的。请对照下方的新内容逐个复核子任务：

- 需要调整：`update_task(task_id=<子任务>, title=..., content=..., change_note=...)`，修改会推送给它的执行者
- 不受影响：`update_task(task_id=<子任务>, confirm=true)`，确认它仍然有效
- 不再需要：`update_task(task_id=<子任务>, status="cancelled")`，它下面的子任务会一起取消

**不要继续做 [{{task.task_id}}] 本身**，复核完就结束本轮；子任务全部结束后它会照常恢复给你。

{{task.change_notes}}

## 最新的目标与验收标准

{{task.content}}

{{task.revision_diff}}

## 子任务

{{task.subtask_brief}}
