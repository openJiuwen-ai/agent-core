---
id: F_20261110_1938_symphony-expert-orchestration
title: Symphony 专家分层编排
type: F
author: lin-xiaoyu
author_email: xiaoyu.lin.work@outlook.com
created: 2026-11-10
updated: 2026-11-11
modules: [openjiuwen/symphony, openjiuwen/harness/rails/evolution]
files: [openjiuwen/symphony/runtime.py, openjiuwen/symphony/graph_engine.py, openjiuwen/symphony/orchestration/artifacts.py, openjiuwen/symphony/orchestration/service.py, openjiuwen/symphony/orchestration/mutation_service.py, openjiuwen/symphony/orchestration/planning/expert_tasks.py, openjiuwen/symphony/orchestration/planning/fast.py, openjiuwen/symphony/orchestration/planning/beam.py, openjiuwen/symphony/orchestration/graph/matcher/protocol.py, openjiuwen/harness/rails/evolution/symphony_graph_evolution_rail.py, tests/unit_tests/symphony/test_mixed_taxonomy.py, tests/unit_tests/symphony/test_expert_task_planning.py]
designs: []
design_note: 依据对话中确认的一张图和两层独立规划方案。
---

# Symphony 专家分层编排

在共用分类树的专家指纹之上新增专家关系匹配与规划。该决定接续 [混合分类树实施记录](F_20261110_1614_symphony-mixed-taxonomy.md)。构图和规划契约见 [Symphony 配置契约](../specs/symphony/S_01_configuration-contract.md)。

混合图每个版本发布一份 2.0 产物。关系缓存和索引按层隔离，类型前缀区分同名节点。规划器接收单层投影，专家 Fast 使用已发布的交接边。Skill 增量更新通过同一发布入口复核并携带专家子图，观测沿用 Skill 投影。共用指纹目录在写缓存前绑定集合身份，树与图可取消地等待共享的专家缓存锁。

本地候选为 Core `2459b2cfa` 加本次差异，使用 Python 3.12.13。Symphony 与相关 Rail 回归 1175 项通过，另行运行的本地 HTTP 回环用例通过。混合图用例覆盖两层同名边、专家指纹复用、并发构树和构图、两种规划模式、增量发布、来源变化、取消及 Skill 经验。

## 专家与内联角色的混合任务计划

`runtime.orchestration.plan_expert_tasks` 复用两种模式的候选和邻居扩展，在可见专家层上生成任务实例。任务可以并列，也可以把有输入输出依据的产物交给下游。专家之间的交接必须有发布图支持，涉及内联角色的交接校验声明端口与专家指纹。缺少已发布专家图时可以生成纯内联计划。

`expert_tasks` 计划图的任务 ID 独立于能力 ID。内联角色规格和临时交接留在本轮 `planned_graph`，供后续执行消费。以 Core `c7e047a97` 为基线叠加本次差异，Python 3.11.15 下 Symphony 单元测试 925 项通过，另有 1 项本地 HTTP 回环测试通过。用例覆盖同一专家的多个任务、纯内联、无边并行、已发布端口映射与字段证据、缺失或不兼容端口、循环以及澄清状态。
