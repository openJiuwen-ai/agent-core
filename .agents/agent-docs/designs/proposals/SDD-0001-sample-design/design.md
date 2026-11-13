---
id: SDD-0001
title: 示例设计文档（脚手架自带，可删除）
slug: sample-design
status: draft
stage: proposals
reviewers: []
created: 2026-06-15
updated: 2026-06-15
version: 1
domain: core
tags: [example, scaffold]
related: []
author: '@your-handle'
author_note: 由历史 owner 字段原值迁移
type: D
modules: []
files: []
author_email: null
---

# 示例设计文档（脚手架自带，可删除）

> 这是仓库自带的一篇 **示范** 设计文档：既演示目录 / frontmatter / 章节规范，也让校验与索引脚本"有米下锅"。
> 真正开始写第一篇设计前，可以直接删除本文件夹（删除后 `SDD-0001` 可被复用）。

## 1. 背景与问题 (Why)

团队的特性设计此前散落在聊天记录与个人文档中，开发前缺少统一评审、开发后无法沉淀为可检索的设计资产，AI 编码代理也无权威设计可参考。本仓库即为解决这一问题而建。

## 2. 目标与非目标

**目标**
- 演示一篇合规 design.md 的完整形态。
- 作为 `validate_frontmatter.py` / `generate_index.py` 的可运行样例。

**非目标**
- 不描述任何真实特性。
- 不进入真实的开发 / 归档流程。

## 3. 术语与上下文（可选）

- **SDD**：Swarm Design Doc，本仓库中一篇特性设计文档的编号前缀。

## 4. 方案设计 (What / How)

### 4.1 总体架构
以「分阶段目录 + frontmatter」承载生命周期：`proposals/ → active/ → archive/`，文档随状态 `git mv` 迁移。

### 4.2 数据流与时序
作者复制模板 → 取号 → 开 PR → 多轮评审（每轮 `version`+1）→ 批准后移入 `active/` → 交付后移入 `archive/` 冻结。

### 4.3 接口与数据模型
> **Requirement:** 系统 SHALL 保证每篇 design.md 的 `stage` 字段等于其所在顶层目录。
> **Scenario:** WHEN 文档位于 `archive/` 而 `stage` 仍为 `active` THEN 校验脚本报 ERROR。

### 4.4 关键流程
见 4.2；异常分支（否决 / 暂缓 / 取代 / 废弃）见 CONTRIBUTING.md 的状态机。

### 4.5 配置与开关
本示例无运行期配置；真实设计在此列出配置项与特性开关。

## 5. 关键决策与权衡 (ADR)

### 5.1 用顺序编号而非纯 slug
- **决策：** 文件夹采用 `SDD-NNNN-<slug>`。
- **背景：** 设计资产需要稳定、可跨文档引用的标识。
- **备选与理由：** 纯 slug 改名即断引用；日期前缀引用串过长。顺序号稳定且简短。

### 5.2 被否决的备选与取舍理由
曾考虑「纯 frontmatter、路径永不变」方案：对 AI 引用最稳定，但人无法靠 `ls` 看出进度，且归档资产与在研文档混在一处。故否决，改用分阶段目录——人机皆可一眼区分在研与已归档。

## 6. 影响面与兼容性

新建仓库，无存量兼容问题。后续若调整 frontmatter Schema，需同步更新 `scripts/lib_frontmatter.py` 与本模板。

## 7. 测试与验证

`python scripts/validate_frontmatter.py` 对本文件应零 ERROR；`python scripts/generate_index.py` 应在 INDEX.md 的「开发前」分组列出 SDD-0001。

## 8. 可观测性

文档仓无运行期可观测性需求。真实设计在此描述日志 / 指标 / 告警 / SLI。

## 9. 风险与回滚

风险：目录迁移可能断开按路径的交叉链接。缓解：一律按 `id` 引用；INDEX 自动生成。

## 10. 开放问题 (Open Questions)

- [ ] （示例）domain 受控词表是否需要新增条目？

## 11. 评审记录 (Iteration Log)

| 轮次 | 日期 | 版本 | 评审人 | 结论 | 主要修改/讨论 |
|---|---|---|---|---|---|
| 1 | 2026-06-15 | 1 | @your-handle | draft | 初稿，作为脚手架示例 |

## 12. 实现记录与偏差

### 实际实现
不适用（示例文档，永不进入开发）。

### 与设计的偏差
无。

### 链接
无。

### 遗留与后续
真实使用时删除本文件夹。

## 13. 参考与关联设计

- 仓库根 `README.md`、`CONTRIBUTING.md`、`AGENTS.md`。
