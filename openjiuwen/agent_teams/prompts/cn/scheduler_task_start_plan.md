[任务开工·计划阶段] 任务 [{{task.task_id}}]「{{task.title}}」已由调度框架启动并指派给你。你处于计划模式：先提交执行计划，待 leader 批准后再开始执行。

## 目标与验收标准

{{task.content}}

## 上下游

- 上游（已完成、你可依赖的交付）：
{{task.upstream_brief}}
- 下游（等待你交付的任务）：
{{task.downstream_brief}}

## 交付方式

- 先用 `submit_plan` 提交执行计划，不要直接动手执行。
- leader 批准后调度框架会通知你进入执行阶段。
