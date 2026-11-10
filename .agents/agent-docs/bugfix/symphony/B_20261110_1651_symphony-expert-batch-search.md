---
id: B_20261110_1651_symphony-expert-batch-search
title: Symphony 专家目录支持批量查询
type: B
author: lin-xiaoyu
author_email: xiaoyu.lin.work@outlook.com
created: 2026-11-10
updated: 2026-11-10
modules: [openjiuwen/symphony/discovery]
files: [openjiuwen/symphony/discovery/directory_toolkit.py, tests/unit_tests/symphony/test_mixed_taxonomy.py]
designs: [D-20261110-1614-symphony-mixed-taxonomy]
---

# Symphony 专家目录支持批量查询

## 现象与根因

模型按 `capability_search` 的 schema 为 `type=expert` 传入多条查询，工具返回 `Search one query at a time for expert`。共用分类树接入时，专家查询沿用了旧目录只处理单条查询的逻辑，与同一工具允许 1–8 条查询的声明冲突。

## 修复

对会话授权范围内的专家独立执行每条查询，复用 Skill 按排名轮流合并并去重的逻辑。分类内的游标从合并结果续页，沿用原查询列表。旧版专家回调保留单条入参，Core 逐条调用并合并命中。详见 [Runtime 契约](../../specs/symphony/S_01_configuration-contract.md)。

## 回归证据

基线 `dd3ad7670` 在模型工具入口复现专家多查询拒绝。修复后的 Core Symphony 无网络回归通过 906 项，覆盖平面和混合树检索、分类、续页、去重及旧回调。Swarm 使用本地 Core 候选源码通过 479 项 Symphony 和组装回归，另用隔离测试通过注册后的批量专家检索和授权变化使游标失效的验证。
