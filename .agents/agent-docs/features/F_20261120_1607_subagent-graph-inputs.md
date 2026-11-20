---
id: F_20261120_1607_subagent-graph-inputs
title: 子智能体编排接口改名与 DAG 缺项提示
type: F
author: lin-xiaoyu
author_email: xiaoyu.lin.work@outlook.com
created: 2026-11-20
updated: 2026-11-21
designs: []
design_note: 用户已明确指定 SDK 方法名、规划状态和宿主对输入的决策职责。
modules: [openjiuwen/symphony]
files: [openjiuwen/symphony/graph_engine.py, openjiuwen/symphony/orchestration/service.py, openjiuwen/symphony/orchestration/planned_graph.py, openjiuwen/symphony/orchestration/planning/fast.py, openjiuwen/symphony/orchestration/planning/beam.py, openjiuwen/symphony/orchestration/planning/plan_builder.py, openjiuwen/symphony/orchestration/planning/expert_tasks.py]
---

# 子智能体编排接口改名与 DAG 缺项提示

Runtime 和编排服务的 `plan_expert_tasks` 改为 `plan_subagent_graph`。已安装专家与内联角色共用入口。宿主和已返回图通过现有 `plan_kind` 识别同一种任务图。[当前编排契约](../specs/symphony/S_02_orchestration-plans.md)

fast 和 beam 都把具备有效节点的计划返回为 `ready`。已有的缺项仍参与候选排序，节点端口和受影响步骤随图提供给宿主。模型草稿若沿用旧缺参状态且含可用的任务，校验后仍能生成有效 DAG。空草稿和无计划场景保留原有的失败与纠错路径。

本次运行 Core 的 Symphony 单元测试 963 项，以及 Harness 图演化与边观测回归 201 项，全部通过。覆盖两种规划模式下的已有资料和缺项、具名资料的节点归属、子智能体草稿纠错、空计划及图观测消费。规划使用本地模型替身。回环 HTTP 用例单独运行。

## 子智能体草稿精简

成功草稿给出任务及必要的交接边。Core 在校验非空 DAG 后补上 `ready`，用显式标题或任务正文首句生成节点标签。模型按节点列出所需外部资料，宿主结合现有资料决定何时执行或询问。两节点 JSON 示例用于明确角色、外部资料及交接边的字段位置。节点把上游产物重复列入外部资料时，Core 核对该节点的入边。宿主已提供或模型单独标明缺失的同名外部资料仍可分别传递。

以 Core `1edb94a54` 为基线，固定系统提示词正文从 4,215 字符缩短至 1,405 字符（不计运行时语言指令）。本机 Qwen3-8B 使用同一份合成产品事实和同一条三平台撰稿、独立审查请求，省略已核准价格。基线一次纠错后因交接字段无效返回 `no_plan`。精简版一次纠错后返回 `ready`，生成三个并行稿件节点和三个产物汇入的审稿节点，缺项仅有 `approved_price`。这是一次同输入的本地模型对照。

Core 的专家编排、编排服务和公共接口定向回归 87 项通过。Swarm 使用同一 Core 源码的直接服务和专家派发定向回归 59 项通过，覆盖资料齐全、缺价、缺附件、节点等待及产物交接。

## 候选专家摘要

fast 和 beam 仍可从起始专家沿关系图补充候选。发送给模型的专家摘要只含 `id`、`name` 和 `description`。指纹端口和已发布的端口映射均已移除。示例同时展示用 `expert_id` 分派已安装专家和用 `role` 定义临时角色的写法。

在合成访谈案例中，两位专家的指纹以 CSV 表交接。通过 Swarm 配置的 `deepseek-flash` 调用实际规划接口，fast 和 beam 均从调研专家补充找到撰稿专家并返回 `ready`。两份计划都把 Markdown 写入任务正文，交接类型为 `text`。空候选案例由两个临时角色交接。模型调用次数依次为 1、2、1。Core 定向回归 101 项通过，Swarm 直接服务与派发定向回归 59 项通过。

## 按交付物划分子智能体

生成任务图时，每个节点承担用户要求的交付物或下游需要的中间产物。可选参考放在实际使用它的节点内。匹配的已有专家可复用，未覆盖的任务可分给临时角色。提示词用两行分工展示单人交付与并行撰稿后独立审查，返回字段单独说明。

以 Core `19788cc84` 为基线，通过 Swarm 配置的 `deepseek-flash` 调用 Fast 规划接口。两次对照均传入相同的用电、用水候选专家和合成园区台账。仅需用电小结且可参考用水时，旧提示词生成两个节点，新提示词只分派用电专家。需要用电、用水、固废三份小结和独立核验时，两版均生成四个节点和三条交接边。新版中两位已有专家和一位临时角色并行撰写，独立核验员取得三份小结及原台账。

在空专家名单且缺少停机时段、名单有专家且缺少明确命名的电价表两种情况下，均返回 `ready` 任务图，缺项分别为 `approved_window` 和 `tariff`。强制指定不可用的专家时返回 `no_plan`。以上五个合成案例均调用一次规划模型。

另一次用自然语言写作「已核准的电价 tariff」，没有明确它是资料标识。该次返回 `ready`，缺项名称包含整段「已核准的电价 tariff」。将资料明确命名为 `tariff` 的对照只返回该键。

## 独立调研与订单证据交接

2026-11-21 将分工示例替换为两种场景，各用一行。亚洲和欧洲市场简报各自取材，两位分析员可同时交付。排查订单延误时，临时调研员从多年日志中提取该订单的时间线并保留记录编号，已有客服专家据此诊断。区域调研与订单检索取自 Anthropic 的文章，订单案例的下游诊断在 Symphony 中分给客服专家。[Anthropic 原文](https://claude.com/resources/articles/building-multi-agent-systems-when-and-how-to-use-them)

通过 Fast 入口用 `deepseek-flash` 规划三个合成案例，每例调用一次模型。区域任务返回两个无依赖节点，各自只取本区材料。订单案例提供一份七千条虚构事件的文件引用，返回提取和诊断两个节点，一条边传递含原始记录编号的时间线，原始文件只分给提取节点。只要求亚洲简报、允许参考欧洲资料的对照返回单个亚洲专家节点。规划、编排服务及公共接口定向回归 87 项通过。
