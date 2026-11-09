---
id: F_20261109_1135_symphony-runtime-config-integration
title: SymphonyConfig 接入 Core Runtime 与模型调用
type: F
author: lin-xiaoyu
author_email: xiaoyu.lin.work@outlook.com
created: 2026-11-09
updated: 2026-11-09
modules: [openjiuwen/symphony]
files: [openjiuwen/symphony/runtime.py, openjiuwen/symphony/config.py, openjiuwen/symphony/tree_engine.py, openjiuwen/symphony/graph_engine.py, openjiuwen/symphony/runtime_flow.py, openjiuwen/symphony/runtime_storage.py, openjiuwen/symphony/inventory.py, openjiuwen/symphony/model_bridge.py, openjiuwen/symphony/orchestration, openjiuwen/symphony/retrieval/build, openjiuwen/symphony/discovery, tests/unit_tests/symphony, examples/symphony/runtime.py]
designs: [D-20261029-1619-skill-directory-graph-api, D-20261031-1614-symphony-config-contract]
---

# SymphonyConfig 运行接入

用户批准的2026-11-09方案覆盖Core与Swarm；本记录只保存Core实施事实，Swarm宿主改动在其仓库记录。Core基线dcdcef3ef02c3d1169db3a7375f9d45a317f207c，工作树feature/symphony-config-contract。

## 实施

Runtime改为二参数构造，统一retrieval/orchestration/flow入口。扫描、解析、指纹、库存快照、树和图构建、Discovery及默认Flow由Core组装。构造惰性，路径和配置快照隔离。保留Rail观测入口，未实现读取器显式拒绝，旧构造/配置/模型连接包装删除。

模型桥保持同步算法调度，原生Model绑定宿主循环；模型凭证、SDK连接和响应计量留在宿主。重复配置与RetrievalModelConfig删除，公共默认值由基线JSON快照验证，内部模型只余协议与一次性构树预算。

新版布局和集合标识隔离旧产物。树/图发布前重验源快照，取消文件写入等待实际线程结束。Runtime关闭为可重复等待的单次任务；关闭等待者取消不会遗留图/Flow工作。Discovery无现有索引时不写布局，来源按ID，输出限制和冻结会话语义由真实入口验证。

## 检视修复

- 独立构树契约检视与质量检视：修复严格后处理失败吞并、策略变更增量复用、模型身份缺字段、FrameworkError错误链瞬态分类、发布窗口取消和大小写ID。
- 原生HTTP持久连接复现：禁止未绑定的同步原生模型调用，实际同一连接跨两次构建及Runtime关闭后宿主调用继续使用。
- 取消清理：等待异步finally结束再重试，5秒超界终止全构建；永久HTTP状态优先于外层timeout文本。
- Runtime独立检视：修复空候选扩大范围、树/图/增量发布前源变更、首次Discovery目录污染、source_by_id显示名误用、输出上限绕过、关闭遗漏mutation/staging线程、同Task嵌套操作丢追踪、取消aclose提前退出、复用统计沿用旧版本及构建ID串用。

## 验证记录

开发过程中已记录：构树全分块121 passed；独立质量针对性22 passed；初版Core全量856 passed；后续新增生命周期覆盖后885 passed/1 failed（旧测试快照读取次数期望，已修）。这些是各当时版本的历史事实，不替代最终候选验证。

最终 Core 候选：Python 3.11.15、Pydantic 2.12.5、当前声明的 OpenTelemetry 1.45.1，Symphony 全量与六组相关 Harness Rail 测试合并运行，1276 passed、4 warnings（2026-11-09）。涵盖真实本机 HTTP 连接复用，不调用远端模型。配置导出一致性随 Symphony 测试通过，config.tree.txt 已重新生成。

跨仓固定依赖验收由 Swarm 开发记录维护，待以本次 Core 提交构建安装后验证。此记录不宣称未完成的 Swarm 交付或用户验收。未运行格式化或静态检查。
