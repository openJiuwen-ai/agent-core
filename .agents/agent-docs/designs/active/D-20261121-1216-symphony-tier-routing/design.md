---
id: D-20261121-1216-symphony-tier-routing
title: Core 内部 T0–T4 分级规划与宿主自动接入
slug: symphony-tier-routing
type: D
status: in-development
stage: active
author: lin-xiaoyu
author_email: xiaoyu.lin.work@outlook.com
reviewers: []
approved-date: 2026-11-21
created: 2026-11-21
updated: 2026-11-21
domain: core
modules: [openjiuwen/symphony, /Users/xiaoyulin/Documents/Huawei/code/jiuwenswarm/jiuwenswarm/runtime/symphony, /Users/xiaoyulin/Documents/Huawei/code/jiuwenswarm/jiuwenswarm/agents/harness/common/rails/symphony]
tags: [symphony, planning, routing]
scope: 在统一 plan 内选择最低充分执行层级，宿主绑定当前请求上下文并执行。
files: []
---

# Core 内部 T0–T4 分级规划与宿主自动接入

## 背景与确认

来源是用户提供的《智能体能力拓扑编排》第八页分级路由及本次会话确认的调整：路由属于 Core，由 `plan()` 内部调用，Swarm 不直接调用；`auto` 是默认值。用户随后明确要求按方案实现。本文整理已确认的方案，未补造独立人员评审或交付批准。

本方案扩展统一规划入口；显式 `skill/expert` 及专用技能/专家入口继续有效。原省略类型的调用需适配默认值与可选图。

## 目标与边界

Core 决定如何完成任务。Swarm 提供会话模型、已读事实、资料、可见 Skill/专家/工具摘要、权限与工作模式；主智能体执行 Core 的决定。工具仅作为宿主能力摘要，不建设工具检索、工具关系图或工具 DAG。本期不接入经验命中的自动快速通道，用户明确选择 SkillPack 的既有授权路径保留。

## 层级与选择

| 层级 | 最低充分策略 | 规划产物 |
| --- | --- | --- |
| T0 | 现有信息直接回答 | 无图 |
| T1 | 单个 Skill 或已有工具 | 无图 |
| T2 | 主智能体协调多 Skill/工具 | 纯 Skill 生成技能图，含工具留在宿主工作流 |
| T3 | 单个已安装专家或内联角色 | 最多一个专家任务节点 |
| T4 | 多专家互补、交接、隔离或并行 | 专家任务 DAG |

多步骤或多个事项本身不足以选择 T4；单 Skill 内部多个工具仍为 T1。尊重明确的用户约束、必选能力和委派限制。资料缺项不自动取消可执行策略，未读文件不作为已知信息。T3/T4 不要求安装专家或存在技能图。

## Core 接口

`plan(query, candidate_ids=None, *, type="auto", context=..., inputs=..., mode=..., language=...)` 为统一入口。路由不是独立宿主 API。显式类型和两个专用入口不增加路由调用。

结果包含 `execution_route` 与可选 `planned_graph`。图沿用既有技能/专家契约。Engine 管理生命周期、取消与上下文，Service 负责路由和规划分流。只在纯 Skill T2 后调用宿主图准备回调，保留动态图、修订和过滤行为；固定图修订需显式技能类型。

路由使用当前请求模型，通常一次调用，结构或约束错误最多修复一次。规划不可行或执行前发现必要能力变化时，同一请求最多重新判级一次。最终单节点图按实际层级降级；最终能力摘要与实际图一致。模型异常和取消向上传播。

## Swarm 接入与执行

根主智能体在第一次模型执行前自动调用 Core `plan`，按请求缓存。主智能体不再自行决定调用哪种 Core 规划函数。显式 compose 复用同一请求的结果；新任务清除缓存，匹配交互 ID 的追问恢复复用原计划并保留确认答复；专家子智能体不递归自动判级。

Rail 按返回策略准入。T0 不执行实质工具；T1 使用选定能力，Skill 在工具步骤前加载；低层级不额外委派；T3/T4 由现有 `task_tool(plan_id,node_id)` 消费图。规划失败/no_plan 不得放行待执行动作。Plan 模式保留既有权限控制并禁止专家派发。最多一次重新判级的预算由 Core 与宿主共享。

自动规划发真实 `chat.symphony_status`，不伪造 compose 工具调用。图展示和历史恢复复用现有 Mermaid 转换。用户要求实际完成任务时按依赖派发专家节点，默认信赖其有效产物，由主智能体协调验收和整合。

## 验证计划与验收

- 默认 auto、显式类型、专用入口和 fast/beam 保持所述语义。
- 覆盖五级、可见/禁用候选、具名资料、ready/no_plan、修订/动态图、结构修复、重判上限、异常和取消。
- 覆盖真实根智能体入口、请求隔离、追问恢复、专家派发和子智能体不递归，以及自动状态的实时/历史投影。
- 提供至少 25 条带预期分级的样例；契约回放与真实模型分类准确率分别报告。
- 验证 Core 后锁定 Swarm 同一新提交，核对安装包入口。提交/推送按单独授权执行。
