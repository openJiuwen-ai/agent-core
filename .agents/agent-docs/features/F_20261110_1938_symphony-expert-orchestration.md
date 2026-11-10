---
id: F_20261110_1938_symphony-expert-orchestration
title: Symphony 专家分层编排
type: F
author: lin-xiaoyu
author_email: xiaoyu.lin.work@outlook.com
created: 2026-11-10
updated: 2026-11-10
modules: [openjiuwen/symphony, openjiuwen/harness/rails/evolution]
files: [openjiuwen/symphony/runtime.py, openjiuwen/symphony/graph_engine.py, openjiuwen/symphony/orchestration/artifacts.py, openjiuwen/symphony/orchestration/service.py, openjiuwen/symphony/orchestration/mutation_service.py, openjiuwen/harness/rails/evolution/symphony_graph_evolution_rail.py, tests/unit_tests/symphony/test_mixed_taxonomy.py]
designs: []
design_note: 依据对话中确认的一张图和两层独立规划方案。
---

# Symphony 专家分层编排

在共用分类树的专家指纹之上新增专家关系匹配与规划。该决定接续 [混合分类树实施记录](F_20261110_1614_symphony-mixed-taxonomy.md)。构图和规划契约见 [Symphony 配置契约](../specs/symphony/S_01_configuration-contract.md)。

混合图每个版本发布一份 2.0 产物。关系缓存和索引按层隔离，类型前缀区分同名节点。规划器接收单层投影，专家 Fast 使用已发布的交接边。Skill 增量更新通过同一发布入口复核并携带专家子图，观测沿用 Skill 投影。共用指纹目录在写缓存前绑定集合身份，树与图可取消地等待共享的专家缓存锁。

本地候选为 Core `2459b2cfa` 加本次差异，使用 Python 3.12.13。Symphony 与相关 Rail 回归 1175 项通过，另行运行的本地 HTTP 回环用例通过。混合图用例覆盖两层同名边、专家指纹复用、并发构树和构图、两种规划模式、增量发布、来源变化、取消及 Skill 经验。
