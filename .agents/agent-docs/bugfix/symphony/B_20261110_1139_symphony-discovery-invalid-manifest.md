---
id: B_20261110_1139_symphony-discovery-invalid-manifest
title: Symphony 隔离无效 Skill 清单
type: B
author: lin-xiaoyu
author_email: xiaoyu.lin.work@outlook.com
created: 2026-11-10
updated: 2026-11-10
modules: [openjiuwen/symphony]
files: [openjiuwen/symphony/inventory.py, openjiuwen/symphony/tree_engine.py, tests/unit_tests/symphony/test_runtime_config_integration.py]
designs: [D-20261109-1745-shared-capability-fingerprints]
---

# Symphony 隔离无效 Skill 清单

## 现象与根因

检索已开启时，一个安装的 `SKILL.md` 因描述里含未引用的冒号空格而出现 `invalid_frontmatter`。扫描器已隔离坏记录并返回有效条目，`DirectoryInventory` 却在构建与会话读取时都拒绝不完整扫描。Swarm 在创建会话工具时收到异常，跳过了 `capability_search` 注册。

## 修复

根据共享指纹设计的会话范围约定，发现和只读状态使用通过校验的记录。状态保留扫描错误并标记索引过期，包含错误路径和代码。构建要求完整扫描，失败时保留已发布分类树和指纹。该边界已同步到 [Runtime 契约](../../specs/symphony/S_01_configuration-contract.md)。

## 验证

新增 Runtime 回归在已发布索引后损坏一条清单。基线 `2575e3370` 在创建会话时重现注册失败。修复后通过模型工具查询剩余 Skill，读取状态诊断，核对失败构建前后的索引版本和指纹字节，并验证修复清单后同一会话重新发现条目。

Core Symphony 904 项和 Swarm 检索、Code Adapter、专家目录、只读权限共 479 项回归通过。用实际安装目录和临时产物目录验证了 `capability_search` 进入模型工具清单并能查询有效 Skill。
