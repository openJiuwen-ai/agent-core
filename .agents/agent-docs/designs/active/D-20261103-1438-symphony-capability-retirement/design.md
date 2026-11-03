---
id: D-20261103-1438-symphony-capability-retirement
title: Symphony 旧检索与独立经验库退役
slug: symphony-capability-retirement
status: in-development
stage: active
author: lin-xiaoyu
author_email: xiaoyu.lin.work@outlook.com
reviewers: []
created: 2026-11-03
updated: 2026-11-03
approved-date: 2026-11-03
domain: core
modules: [symphony]
tags: [configuration, retrieval, retirement]
scope: Core 旧检索、独立经验库及 Swarm develop 配套配置引用清理
related: [D-20261031-1614-symphony-config-contract]
type: D
files: [openjiuwen/symphony/config.py, openjiuwen/symphony/agent/retrieval_toolkit.py, openjiuwen/symphony/retrieval, openjiuwen/symphony/experience, tests/unit_tests/symphony]
---

# Symphony 旧检索与独立经验库退役

## 背景与确认

以 JiuwenSwarm develop 主链路为保留边界，删除闲置能力的代码、配置、导出和对应测试。Core 基线 99fa1600d；Swarm 基线 01c69908f3156efb57648df9b78cc79f92dec4c8。
用户确认允许最小跨仓适配、整体退役旧模型检索链和独立 experience 包，并于 2026-11-03 要求执行计划。未进行独立代码评审，不预填评审人。
本方案局部替代 D-20261031-1614-symphony-config-contract 中旧检索、工具浏览和独立经验库的保留决定；嵌套 Pydantic、中文元数据、默认值保持及运行链路尚未接入等约定继续有效。历史实施与测试事实不重写。

## 方案与取舍

不采用仅内化配置的方式：删除旧行为实现，不留兼容别名或空壳。删除按符号及依赖执行，不按 search 等目录名称整包删除。

- 删除 Retriever / ProgressiveRetriever / FlatRetriever、检索服务、结果合并、紧凑编码、logit/trie 选择及 retrieval/llm 后端。
- AgenticSkillRetrievalToolkit 保留构建、更新、状态、取消、树读取和发布恢复；删除分支浏览、提示渲染、可见列表及浏览缓存。
- 删除独立 experience 包，保留 flow、observation、Swarm runtime/symphony/experience.py 和 symphony.experience_recipe.v1 协议字符串。
- 删除 SkillIndexRuntimeConfig、ScoreTruncationConfig、SearchStep、MultiLevelSearchResult。构建统一使用 config，移除 BuildMethod、method、IndexBuildRuntimeConfig 及 runtime_config 入参别名。
- 保留 search/artifacts/loading.py、search/runtime/lexical.py、共用树类型及 indexing 提示资源的现有路径。
- 保留一次性、递归与增量构树、回退与旧索引保护、指纹缓存、Fast/Beam、dynamic_graph_enabled、Discovery/SkillFS 的快照和分页。
- 配置入口保留 capability、storage、retrieval、orchestration、flow；retrieval 仅保留 enabled、build、discovery。预计 36 个模型：公共 33，内部 3。
- Swarm 清理废弃 retrieve 模板与文档示例，配置更新测试使用 Discovery 字段；旧会话的工具结果渲染和上下文压缩保留。

## 不变量与边界

保留配置默认值、约束、路径及构建指纹、工件格式不变。SymphonyConfig 尚不接入 Runtime。不迁移或删除用户历史工件。不修改全局依赖及 Swarm Core 锁定版本，不提交或推送。

## 验证设计

1. 配置往返、中文元数据、完整错误路径、独立容器、登记覆盖与基线默认值/约束对比；重新生成 config.tree.txt。
2. 真实临时文件验证构建、增量、失败保护、恢复及 Discovery 的索引读取和回退、可见范围、搜索与分页；模型使用确定性替身。
3. 验证删除后的公共导出和保留的共享加载、构建、编排、观测及 Flow。
4. 运行 Core Symphony 与 Swarm develop 的相关回归，明确验证实际导入候选 Core。
5. 不运行格式化或静态检查；真实在线模型、发布及依赖升级不在验收范围。

## 交接

配置契约由 S_01_configuration-contract 维护；实际实施、偏差、测试与遗留由对应 feature 记录维护。
