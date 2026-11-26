---
id: F_20261126_1427_expert-task-handoffs
title: 专家任务的交接与最终验收
type: F
author: lin-xiaoyu
modules: [openjiuwen/symphony/orchestration]
files:
  - openjiuwen/symphony/orchestration/planning/expert_tasks.py
designs: []
design_note: 本轮沿用专家任务图和派发接口，调整已有规划指令。
author_email: xiaoyu.lin.work@outlook.com
---

# 实施与验证

规划器按可独立验收的结果确定负责人。拆分支撑节点时，要求明确下游会依据其产物做什么决定或操作。同一状态的连续修改尽量归同一节点，以减少重复操作和交接时的上下文丢失。

交付节点需要从原始要求和相关资料中提取验收条件，检查最终文件或回读实际状态。上游遗漏的要求也在验收范围内。发现问题时局部修正并复核，重试有副作用的操作前先确认已生效的变更。

规划器在节点说明中写明强制交付约束的依据，保留用户或适用资料明确规定的范围和排除项。拆分验收检查时保留原意和格式，让执行者在建议的方法与原要求冲突时调整方法。

回归用例位于 `tests/unit_tests/symphony/test_expert_task_planning.py`，覆盖规划请求中的指令、校验补试时保留的请求上下文，以及交付任务和交接产物在最终图中的投影。模型使用测试替身，真实任务得分需通过对照实验验证。
