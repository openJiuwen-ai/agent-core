---
id: F_20261121_1216_symphony-tier-routing
title: Core 内部自动分级规划
type: F
author: lin-xiaoyu
author_email: xiaoyu.lin.work@outlook.com
created: 2026-11-21
updated: 2026-11-21
modules: [openjiuwen/symphony]
files: [openjiuwen/symphony/graph_engine.py, openjiuwen/symphony/orchestration/service.py, openjiuwen/symphony/orchestration/routing.py, openjiuwen/symphony/orchestration/planning/expert_tasks.py, tests/unit_tests/symphony/test_task_tier_routing.py]
designs: [D-20261121-1216-symphony-tier-routing]
---

# Core 内部自动分级规划

承接已确认的 [分级路由设计](../designs/active/D-20261121-1216-symphony-tier-routing/design.md)，当前契约见 [S_02_orchestration-plans](../specs/symphony/S_02_orchestration-plans.md)。在基线 `034523cb729597805a41c5ee8cf70e5fe39fe8e5` 上引入默认 auto 规划。

## 实施与取舍

统一入口默认 auto，Service 内部路由，Engine 按实际层级延迟准备技能图；显式技能/专家规划保持固定类型。新增 PlanningContext、可见摘要和 execution_route。工具只描述已有宿主能力，T0/T1/工具流程不构图。T3 约束任务数，T4 沿用专家任务图；支持纯内联计划。

路由仅发送有界摘要与资料预览，完整具名资料和约束继续进入专用规划及执行输入。显式图修订与簇参数在 auto 中拒绝，避免被路由旁路。路由修复和唯一重判有明确预算；宿主通过 context.reconsiderations 共享重判次数。专家必选 ID 在最终计划核验，Skill 必选 ID 在技能图核验；专家任务中所用 Skill 属于任务约束，具体工具使用仍由执行节点遵循。

独立自动检视指出并修复：SDK 默认目录协议适配、manifest 前置过滤、禁用能力、显式图参数、约束/资料传递，以及技能图扩展节点必须同步至 route IDs。后者有最小复现与回归；避免 Swarm 拒绝加载合法扩展节点。

## 验证

激活 Core .venv 并设置 PYTHONPATH=.：
`pytest -q -o addopts='' -o log_cli=false tests/unit_tests/symphony`：1056 项通过。覆盖技能 fast/beam、固定修订和动态图、专家/内联图、结构失败修复、T3 任务超限升级、一次重判、约束、资料完整传递及取消传播。运行涉及本地 HTTP 测试服务，未调用外部模型。

`fixtures/task_tier_cases.json` 提供 25 条预期分级及原因，覆盖上下文已知/未读文件、Skill 内部工具、混合流程、多步骤、专业隔离和专家交接。测试回放预期决策验证契约，不声称测得真实模型分类准确率。

README、公开导出、示例和规划 spec 同步默认值与可选图；原技能测试补显式类型。

暂存区 hw-codecheck 通过。修复前检查已命中导入位置 E402、行长 G.FMT.02 和条件复杂度 G.CTL.03，修复后复查通过，没有确认的共享规则漏检。公开导出整理曾触发 shared/fingerprint 与 orchestration/model 的循环导入，保留服务先于路由的加载顺序后，全量回归通过。

## 交付与限制

代码、规约及验证记录随本次提交交付；Swarm 安装包验收与依赖锁定在对应宿主 feature 记录。未运行真实模型语义评估，模型如何理解任意自然语言约束仍属于模型行为；本期不加入工具 DAG 或经验自动快速通道。
