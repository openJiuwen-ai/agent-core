---
id: D-20261110-1614-symphony-mixed-taxonomy
title: Symphony 混合来源与共用分类树
slug: symphony-mixed-taxonomy
status: in-development
stage: active
author: lin-xiaoyu
author_email: xiaoyu.lin.work@outlook.com
reviewers: []
created: 2026-11-10
updated: 2026-11-10
approved-date: 2026-11-10
domain: core
modules: [symphony, agent_teams/tools, jiuwenswarm/runtime/symphony]
tags: [symphony, discovery]
scope: Skill 与独立专家共用分类树，按类型检索
related: [D-20261031-1614-symphony-config-contract, D-20261109-1745-shared-capability-fingerprints]
type: D
files: []
---

# Symphony 混合来源与共用分类树

现有来源列表先于多目录运行实现落地。本设计承接配置方案第 17 节，定义目录读取、共用树与发现会话。关系图和编排仍消费恰好一个 Skill 来源，专家参与关系图另行设计。

## 方案

Runtime 归一化来源列表。技能继续读取 SKILL.md，独立专家从 manifest.json 投影 name、description 与 package_type。构树器用带类型的叶子身份合并数据。共用树和分类型指纹写入集合专属目录，单 Skill 产物保留原布局。扫描不完整、发布前变化或取消时保留上一版树。

会话按查询类型和实时可见范围投影已发布树。专家列表、分类内搜索和续页都使用同一视图。Swarm 的显式专家来源与当前可调用清单按 manifest 路径取交集，执行仍使用宿主专家引用。省略专家来源时使用现有会话目录。

## 验收

验证多目录、同名跨类型叶子、授权排除、分类计数、游标失效及无索引回退。验证错误或取消时工件版本稳定，以及安装包环境中的 Swarm 注册、构建和 v2 快照恢复。

## 2026-11-10 确认

用户确认共用一棵树、专家分类浏览与搜索，以及配置目录和会话授权取交集，并明确本轮实施分类树。
