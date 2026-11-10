---
id: B_20261110_2013_symphony-catalog-cache-binding
title: Symphony 首次构图兼容宿主目录指纹
type: B
author: lin-xiaoyu
author_email: xiaoyu.lin.work@outlook.com
created: 2026-11-10
updated: 2026-11-10
modules: [openjiuwen/symphony]
files: [openjiuwen/symphony/runtime_storage.py, tests/unit_tests/symphony/test_runtime_config_integration.py]
designs: []
design_note: 基于现有 Runtime 产物契约修复首次绑定，无独立设计。
---

# Symphony 首次构图兼容宿主目录指纹

## 现象与根因

混合分类树先发布到独立集合。Swarm 在检索构建后将连接器和 MCP 的指纹写入产物根的 `cache/fingerprints/<type>/`。此时单 Skill 根尚未创建 `runtime.json`。首次构图会将这些缓存以及 Finder 的普通 `.DS_Store` 文件识别为未绑定产物，报 `Artifact directory was populated before collection binding`。

## 修复

绑定前检查识别普通 Finder 元数据、独立分类树集合和宿主指纹目录。宿主缓存只在单 Skill 根按已知类型共享，Core 管理的产物仍需要匹配集合身份。读取和首次写入采用同一规则，写入继续使用文件锁与原子发布。详见 [Runtime 契约](../../specs/symphony/S_01_configuration-contract.md)。

## 回归证据

在 `c7e047a97` 的构图入口分别用 Finder 元数据和宿主指纹复现报错。候选通过读状态、首次发布混合图、保留宿主指纹，以及拒绝未绑定 Skill 指纹的回归。Core Symphony 912 项与 Swarm Symphony 294 项离线测试通过。Core 离线套件排除需要绑定本地端口的模型事件循环测试，真实模型构图需在宿主重试时验证。
