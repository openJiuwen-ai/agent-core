---
id: F_20261110_1614_symphony-mixed-taxonomy
title: Symphony 共用分类树与独立专家读取
type: F
author: lin-xiaoyu
author_email: xiaoyu.lin.work@outlook.com
created: 2026-11-10
updated: 2026-11-10
modules: [openjiuwen/symphony, openjiuwen/agent_teams/tools]
files: [openjiuwen/symphony/runtime.py, openjiuwen/symphony/runtime_storage.py, openjiuwen/symphony/expert_manifest.py, openjiuwen/symphony/tree_engine.py, openjiuwen/symphony/discovery/directory_toolkit.py, openjiuwen/agent_teams/tools/tool_expert.py, tests/unit_tests/symphony/test_mixed_taxonomy.py]
designs: [D-20261110-1614-symphony-mixed-taxonomy]
---

# Symphony 共用分类树与独立专家读取

## 实施与决策

在配置来源列表的基础上实现运行接入。共用树以类型和 ID 区分叶子，按来源集合隔离产物。构树、读树与发布版本使用同一份提交工件。会话只投影实时可见的类型分支，原单 Skill 布局和 Graph Scope 保留。专家扫描仅依赖 manifest 中的身份与描述，包内资源无需可加载。

## 验证

候选为 Core dffae8e12 加本地差异，Python 3.12.13。Symphony 与专家工具回归 982 项通过，另行运行的本地回环模型用例通过。验证覆盖跨类型同名、双 Skill 目录、专家独立来源、错误诊断、取消和发布前复核。
