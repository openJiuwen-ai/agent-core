---
id: S_02_orchestration-plans
title: Symphony 规划结果与宿主输入处理
type: S
author: lin-xiaoyu
author_email: xiaoyu.lin.work@outlook.com
created: 2026-11-20
updated: 2026-11-21
modules: [openjiuwen/symphony]
files: [openjiuwen/symphony/graph_engine.py, openjiuwen/symphony/orchestration/service.py, openjiuwen/symphony/orchestration/routing.py, openjiuwen/symphony/orchestration/planned_graph.py, openjiuwen/symphony/orchestration/planning/expert_tasks.py]
---

# Symphony 规划结果与宿主输入处理

宿主使用 `runtime.orchestration.plan(query, type="auto" | "skill" | "expert", ...)` 统一规划，类型缺省为 `auto`。`OrchestrationService.plan()` 管理内部判级与规划选择，`SymphonyGraphEngine.plan()` 管理运行态、取消和技能动态图上下文。非法类型在模型调用和图读取前被拒绝；没有对宿主公开的独立路由方法或工具。

自动路由选择最低充分层级：T0 上下文内回答；T1 单 Skill 或单个已有工具；T2 多 Skill、工具或混合工作流；T3 单专家；T4 多专家任务图。多步骤或多个独立事项本身不构成委派依据，单 Skill 内部使用多个工具仍为 T1。T3/T4 依据专业工作规则、隔离、互补、交接、并行或独立验证收益选择，也允许没有安装专家的纯内联计划。

`PlanningContext` 包含请求身份、已读事实、资料完整性标志、宿主当前可见摘要及执行约束。模型前应用禁用 Skill 和专家 manifest 范围。选中 ID 必须唯一且可见，必选 ID 不可静默遗漏，禁止委派时不能返回 T3/T4。候选 ID 是排序/规划起点提示，不是完整授权目录；完整可见列表是规划的边界。工具摘要不进入图检索和工具 DAG。

自动结果包含 `execution_route`，仅纯 Skill 的 T2 和 T3/T4 必需规划图。T3 专家规划最多一个任务，任务数超限或首次 `no_plan` 可触发一次重新判级。调用通常一次路由模型请求，无效结构或决定最多修复一次。`context.reconsiderations=1` 表示宿主已使用同一请求的唯一重判预算，Core 不再重判；结果记录已用次数。异常和取消传播。单节点技能图降为 T1，单节点专家图降为 T3；最终技能 ID 始终取实际图节点，包含关系扩展节点。

Core 使用有界事实、目录和具名资料预览路由，并标记裁剪，不假定未读文件或被裁掉内容已知。完整 `inputs` 以及事实/约束的具名文本传入专用规划器；自动图结果的 `planning_inputs` 供宿主向执行节点保留同一资料。缺项是提示，不代表结构有效的图不能执行。

Engine 在判级前只检查运行态；仅纯 Skill 的 T2 请求 `prepare_skill_graph`，再检查技能图并读取动态快照。其他层级不触发技能图可用性检查。显式图修订/快照、动态图覆盖和任务簇只适用于 `type=skill`，自动接口拒绝，避免丢弃固定图约束。显式类型和专用入口不触发判级模型。

`plan_skill_graph()` 固定生成技能工作流，与 `plan(type="skill")` 共用技能规划实现；`plan_subagent_graph()` 固定生成由已安装专家和临时角色承担的任务图，与 `plan(type="expert")` 一致。两个专用入口不接受 `type`，Engine 的专用入口以固定类型进入统一入口。三个入口都支持具名 `inputs`、`mode`、`language`、进度。专用入口返回 `planned_graph.graph`，统一入口的图可选。

`plan(type="expert")` 替代旧版返回专家能力链的行为，统一输出 `plan_kind=expert_tasks`。专家关系图保留为候选证据，已安装专家与实时 manifest 可见范围取交集；没有技能图、专家图或可用专家时允许纯内联任务。专家规划不读取技能动态图，不要求技能图来源可用。技能专属修订、动态图覆盖和禁用技能参数不能传给专家规划，非空禁用列表或显式图上下文会被拒绝，避免静默丢弃约束。

有效 DAG 的 `metadata.status` 为 `ready`。无法形成可用计划时返回 `no_plan`。图级 `missing_inputs` 描述规划时发现的资料缺项。宿主结合会话资料和 DAG 决定哪些节点可以运行、哪些前置资料需要获取或向用户确认。

Skill 节点的 `metadata.inputs` 和 `metadata.outputs` 提供已声明的端口名、类型和可用的必填标志。该节点已知的缺项通过 `metadata.missing_inputs` 提示。子智能体图沿用 `plan_kind=expert_tasks`，任务节点的 `external_inputs` 引用具名资料，依赖边以 `source_output` 和 `target_input` 表达交接。

子智能体规划可沿已发布的关系补充候选专家。模型接收的每位候选只有 `id`、`name` 和 `description`。一个交付物可由单个节点完成。多个任务可复用同一专家，候选也可闲置，缺少匹配专家的任务可分给内联角色。节点的 `outputs` 由本轮任务定义，Core 根据 `handoffs` 生成下游 `inputs`。

子智能体模型草稿可省略成功状态和节点标题。Core 在任务和交接通过校验后输出 `ready`，并优先用显式 `title` 生成节点标签，省略时取 `task` 的首句或首行。节点引用的外部资料名若未在 `inputs` 提供，由 Core 汇总为图级缺项。若节点把自己的入边产物名或接收端口名也列为外部资料，Core 通过该入边交接。宿主已提供或草稿显式列为缺项的同名外部资料仍单独保留，其他节点的入边不参与判定。显式缺项可以补充类型和用途。

调用方式见 [Symphony SDK 说明](../../../../openjiuwen/symphony/README.md)。
