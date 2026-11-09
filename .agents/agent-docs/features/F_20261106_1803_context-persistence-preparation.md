---
id: F_20261106_1803_context-persistence-preparation
title: 原生 context 保存准备 hook 与延迟原文版本
type: F
author: 刘瑜
author_email: liuyu524@huawei.com
created: "2026-11-06 18:03"
designs: []
design_note: 用户在 issue6134 会话中确认第二批方案并直接授权实施；本次按该方案提供通用 Core 扩展点，不新增独立 D。
modules:
  - openjiuwen/core/context_engine
  - openjiuwen/core/single_agent
  - openjiuwen/harness
files:
  - openjiuwen/core/context_engine/context_engine.py
  - openjiuwen/core/context_engine/base.py
  - openjiuwen/core/context_engine/context/file_offload_store.py
  - openjiuwen/core/context_engine/context/message_buffer.py
  - openjiuwen/core/context_engine/context/context.py
  - openjiuwen/core/single_agent/rail/base.py
  - openjiuwen/core/single_agent/agents/react_agent.py
  - openjiuwen/harness/deep_agent.py
  - tests/unit_tests/core/context_engine/test_durable_context_persistence.py
---

# 原生 context 保存准备 hook 与延迟原文版本

宿主若只在外层 after_invoke 写快照，会漏过原生中断、异常和内部提交边界。本次在 `save_contexts()` 收集 state 与安装 Session state 之间加入可选、单所有者的异步准备 hook。宿主在 hook 中准备不可变文件与 Session 引用，原生 Session 保持唯一提交责任；不用替换 checkpointer，也不 monkey patch 原生方法。

原 `offload_messages` 可包含所有卸载原文，直接恢复会重新占用内存。新增可选原生目录存储，按内容版本而非可变句柄文件保存；原文转存后只保存 handle→version 索引，恢复不读原文，更新同一句柄也保留旧检查点版本。大字符串按块编码，避免制造整份转义副本。

输入元数据随实际入窗一起安装，Core 保留 origin/source 的所有权。`ensure_initialized()` 明确成为宿主恢复前可用的幂等初始化屏障；没有改变默认处理器配置、模型或压缩尾部策略。

有效契约见 `specs/core/context_engine/S_01_durable-context-persistence.md`。本地配套源码基于 `f3f8f576164cc405c046e43079c8ad00ee17f0c4`；定向 Core 回归 132 项已通过。真实宿主验收与完整版本指纹在 JiuwenSwarm issue6134 第二批本地报告中；未提交、未发布 wheel、未运行远端 CI。

## 2026-11-09 分支交付适配

以上保留首次实现的基线与证据。本次交付将同一通用能力适配到 Core develop，最终基线为 `f3260e9b38351587c10a6b0aeafbbde9fa1098e4`，供 Swarm develop 与 releases/0.2.9 配套依赖。原生 context 的消息 ID、取消状态和既有压缩处理器行为保持当前基线实现；新增 hook 和持久卸载为可选能力。

定向 ContextEngine/SessionModelContext/持久化回归 132 项通过。最初基于 `2889d59233ac93e8c072e2c5f2ca404b47585b45` 扩展到全部 context_engine 及 DeepAgent rail/终态流回归时，590 项通过；5 项因 Windows 符号链接权限和 epoch 时间转换失败，未修改的同 SHA 基线也复现相同失败。后续基线合入团队审批恢复，未改变此次 context/rail 改动及其测试文件，保留该上游更新后重验配套链路。未为消除这些既存环境差异扩展本次代码范围。

提交按代码、测试、文档三个连续提交组织。对应 Swarm 分支分别以其目标基线适配并保留自身 Protocol/Gateway 固定版本；新的 Core 固定 SHA 由实际发布的配套提交决定。真实 Worker 验证使用本地确定性 OpenAI 服务与隔离数据，不声明原现场或真模型长稳已经通过。
