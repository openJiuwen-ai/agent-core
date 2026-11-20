---
id: F_20261120_1607_subagent-graph-inputs
title: 子智能体编排接口改名与 DAG 缺项提示
type: F
author: lin-xiaoyu
author_email: xiaoyu.lin.work@outlook.com
created: 2026-11-20
updated: 2026-11-20
designs: []
design_note: 用户已明确指定 SDK 方法名、规划状态和宿主对输入的决策职责。
modules: [openjiuwen/symphony]
files: [openjiuwen/symphony/graph_engine.py, openjiuwen/symphony/orchestration/service.py, openjiuwen/symphony/orchestration/planned_graph.py, openjiuwen/symphony/orchestration/planning/fast.py, openjiuwen/symphony/orchestration/planning/beam.py, openjiuwen/symphony/orchestration/planning/plan_builder.py, openjiuwen/symphony/orchestration/planning/expert_tasks.py]
---

# 子智能体编排接口改名与 DAG 缺项提示

Runtime 和编排服务的 `plan_expert_tasks` 改为 `plan_subagent_graph`。已安装专家与内联角色共用入口。宿主和已返回图通过现有 `plan_kind` 识别同一种任务图。[当前编排契约](../specs/symphony/S_02_orchestration-plans.md)

fast 和 beam 都把具备有效节点的计划返回为 `ready`。已有的缺项仍参与候选排序，节点端口和受影响步骤随图提供给宿主。模型草稿若沿用旧缺参状态且含可用的任务，校验后仍能生成有效 DAG。空草稿和无计划场景保留原有的失败与纠错路径。

本次运行 Core 的 Symphony 单元测试 963 项，以及 Harness 图演化与边观测回归 201 项，全部通过。覆盖两种规划模式下的已有资料和缺项、具名资料的节点归属、子智能体草稿纠错、空计划及图观测消费。规划使用本地模型替身。回环 HTTP 用例单独运行。
