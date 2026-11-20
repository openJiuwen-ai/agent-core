---
id: S_02_orchestration-plans
title: Symphony 规划结果与宿主输入处理
type: S
author: lin-xiaoyu
author_email: xiaoyu.lin.work@outlook.com
created: 2026-11-20
updated: 2026-11-20
modules: [openjiuwen/symphony]
files: [openjiuwen/symphony/graph_engine.py, openjiuwen/symphony/orchestration/service.py, openjiuwen/symphony/orchestration/planned_graph.py, openjiuwen/symphony/orchestration/planning/expert_tasks.py]
---

# Symphony 规划结果与宿主输入处理

宿主用 `runtime.orchestration.plan(...)` 在已发布的能力层规划，用 `runtime.orchestration.plan_subagent_graph(...)` 生成由已安装专家和临时角色承担的任务图。两者都支持具名 `inputs`，返回 `planned_graph.graph`。

有效 DAG 的 `metadata.status` 为 `ready`。无法形成可用计划时返回 `no_plan`。图级 `missing_inputs` 描述规划时发现的资料缺项。宿主结合会话资料和 DAG 决定哪些节点可以运行、哪些前置资料需要获取或向用户确认。

Skill 节点的 `metadata.inputs` 和 `metadata.outputs` 提供已声明的端口名、类型和可用的必填标志。该节点已知的缺项通过 `metadata.missing_inputs` 提示。子智能体图沿用 `plan_kind=expert_tasks`，任务节点的 `external_inputs` 引用具名资料，依赖边以 `source_output` 和 `target_input` 表达交接。

调用方式见 [Symphony SDK 说明](../../../../openjiuwen/symphony/README.md)。
