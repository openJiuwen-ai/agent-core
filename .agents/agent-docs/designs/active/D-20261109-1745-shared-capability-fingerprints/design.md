---
id: D-20261109-1745-shared-capability-fingerprints
title: Symphony 检索与编排共用能力指纹
slug: shared-capability-fingerprints
status: in-development
stage: active
author: lin-xiaoyu
author_email: xiaoyu.lin.work@outlook.com
created: 2026-11-09
updated: 2026-11-09
approved-date: 2026-11-09
domain: core
type: D
modules: [symphony, jiuwenswarm/jiuwenswarm/runtime/symphony]
files: [openjiuwen/symphony/tree_engine.py, openjiuwen/symphony/orchestration/service.py, jiuwenswarm/jiuwenswarm/runtime/symphony/capabilities.py]
related: [D-20261029-1619-skill-directory-graph-api, D-20261031-1614-symphony-config-contract]
---

# Symphony 检索与编排共用能力指纹

## 目标

为 Agent 提供一个统一入口，按任务发现当前会话可用的 Skill、专家、连接器和 MCP。由能力指纹提供描述与输入输出，会话目录提供可用性和动作引用。

## 方案

- 来源适配器继续提供 `CapabilityDescriptor`，`FingerprintService` 按来源集合增量发布 `CapabilityFingerprint`。Skill 检索构建和构图共用现有产物及缓存。构图绑定本次使用的指纹快照。
- Swarm 的检索构建读取安装的专家与连接器卡片和已配置的 MCP 服务。连接器与 MCP 的指纹粒度为服务，具体动作来自会话实时目录。首期构图使用 Skill 集合的指纹。
- 模型通过单个 `capability_search` 选择 `type=skill|expert|connector|mcp`，默认检索 Skill。Skill 分支复用原有目录搜索、分类和游标，其他类型交给宿主的会话目录。各分支先按会话可用范围筛选，再匹配来源、类型、ID 和内容哈希。描述、摘要、场景和关键词进入词法召回，目录信息和 Skill 正文参与匹配，指纹缺失或过期时使用当前目录描述。
- Skill、专家和工具引用由各自的宿主入口消费。连接器可通过可用的下属动作被召回。详情与执行沿用当前会话的权限与注册校验。

## 验收设计

- 禁用编排时可由检索构建生成指纹，重新开启编排后在同内容、同策略下复用提取结果。
- 指纹里的场景词能参与召回。修改能力定义使旧指纹失效并回退到当前目录，下一次构建更新描述。指纹构建失败保留上一份已发布产物。
- 同名跨类型能力可区分。禁用 Skill、MCP 断开或撤权后，下一次查询剔除相关候选。
- 正常模型工具注册只暴露一个检索入口。Skill 分类浏览和翻页复用现有状态，SDK 的 `skill_index` 调用继续可用。

## 确认记录

2026-11-09，用户在会话中确认按此方案实现，选择仅检索会话可用能力，由检索构建增量生成指纹，并让检索和编排按来源集合共用产物。

同日，用户要求合并 Skill 与其他能力的检索入口，用单个 `type` 选择类型。该选择替代最初并列的模型工具与 `types` 多类型输入。
