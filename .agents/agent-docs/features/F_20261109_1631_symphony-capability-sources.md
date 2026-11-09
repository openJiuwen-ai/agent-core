---
id: F_20261109_1631_symphony-capability-sources
title: Symphony 多类型来源配置
type: F
author: lin-xiaoyu
author_email: xiaoyu.lin.work@outlook.com
created: 2026-11-09
updated: 2026-11-09
modules: [openjiuwen/symphony]
files: [openjiuwen/symphony/config.py, openjiuwen/symphony/inventory.py, openjiuwen/symphony/runtime.py, openjiuwen/symphony/runtime_storage.py, openjiuwen/symphony/config.tree.txt]
designs: [D-20261031-1614-symphony-config-contract]
---

# Symphony 多类型来源配置

## 实施

依据 [D-20261031-1614 §17](../designs/active/D-20261031-1614-symphony-config-contract/design.md#17-2026-11-09-多类型来源配置) 将集合级 `root/type` 替换为非空的 `capability.sources`。每个目录项沿用严格的格式枚举及路径校验。Schema 和配置树展示完整子模型。当前契约见 [S_01](../specs/symphony/S_01_configuration-contract.md)。

单来源消费方通过 `require_single_source()` 获取目录和格式。多来源在 Runtime 构造时被拒绝，因而现有扫描、存储和会话入口只操作完整的单来源集合。单来源产物继续按绝对目录及格式绑定。混合来源的扫描、身份与发布规则留给运行接入设计。

## 验证

2026-11-09 在 `e180606ce` 的候选工作树上使用 Python 3.11.15 运行 `tests/unit_tests/symphony`。常规 898 项通过，使用本地回环 HTTP 的用例单独通过，共 899 项。证据覆盖混合列表往返、索引化错误路径、旧字段拒绝、生成文档及单来源运行回归。多来源运行入口测试在没有创建产物的情况下返回明确错误。

合入候选基于 `326b129b0`，结合多来源配置与统一检索模型。配置、导出、Runtime、Discovery 生命周期和回环模型调用用例共 224 项通过。
