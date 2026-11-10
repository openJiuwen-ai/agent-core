---
id: D-20261109-1603-symphony-orchestration-experience
title: Symphony 编排经验配置收敛
slug: symphony-orchestration-experience
type: D
status: in-development
stage: active
author: lin-xiaoyu
author_email: xiaoyu.lin.work@outlook.com
reviewers: []
created: 2026-11-09
updated: 2026-11-09
approved-date: 2026-11-09
domain: core
modules: [symphony, jiuwenswarm/runtime/symphony]
files: [openjiuwen/symphony/config.py, openjiuwen/symphony/runtime.py, openjiuwen/symphony/graph_engine.py, jiuwenswarm/common/symphony_config.py, jiuwenswarm/runtime/symphony/service.py]
related: [D-20261031-1614-symphony-config-contract, D-20261103-1438-symphony-capability-retirement]
tags: [symphony, configuration, orchestration]
scope: Core 经验配置与写入门控，Swarm 执行轨迹、Flow 恢复及设置接入
---

# Symphony 编排经验配置收敛

## 决策与确认

2026-11-09，用户确认单层 `orchestration.experience`、共用一个经验开关、面板只保存经验开关、Core 与 Swarm 统一默认值，并明确要求实施。该决定局部替代关联设计中顶层 Flow 配置和独立启用方式。开发态按全新配置安装，沿用统一模型校验。既有独立经验库的退役决定及 `symphony.experience_recipe.v1` 产物契约继续适用。

## 配置与运行

- `OrchestrationExperienceConfig` 直接包含 `enabled`、`acceptance` 和 `narrative`，替代 `FlowConfig`。Core 定义经验默认关闭，最少成功 3 次，最低成功率 0.8，叙述示例最多 5 条。Swarm 继承该组默认值，显式配置优先。
- `orchestration.enabled` 和 `experience.enabled` 同时开启才提交新证据。`submit_evolution()` 先提交完整执行图，再按已有规则将合格的成功子图交给 Flow。运行时禁用时统一提交返回空结果，直接观测写入在创建工件前拒绝。`runtime.flow` 继续承担候选和作用域接口。
- `orchestration.plan.dynamic_graph_enabled` 独立控制规划是否采用已存在的动态观测。经验关闭时仍可读取既有观测。单 Agent、团队 Rail、启动恢复和能力包候选操作使用统一的经验开关。常驻 Flow 的配置快照在有序重启后更新。
- Swarm 设置字段 `symphony_evolution_enabled` 仅对应 `orchestration.experience.enabled`。本地和元戎模板使用 `null` 继承 Core 默认值。

## 验收

- Core 覆盖默认关闭、显式启用、三次合格成功生成候选、父子门控、已有观测读取与配置导出。
- Swarm 验证模板继承、单 Agent 和团队触发、启动恢复、候选处理以及面板保留独立的规划开关。两仓使用同一 Core 候选进行安装包联测。
