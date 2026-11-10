---
author: Wang Tao
author_email: zzyyww0808@gmail.com
designs: []
design_note: 依据本会话已确认的跨仓 Coding 文档记忆方案及跳过旧 AutoMemory 的补充决定。
id: F-20261107-1700-coding-document-memory
title: Coding 文档记忆 Rail
type: F
modules: [openjiuwen/harness/rails]
files: [openjiuwen/harness/rails/memory/jiuwen_memory_coding_rail.py]
---

# Coding 文档记忆 Rail

日期：2026-11-07。

## 决策

新增 `JiuwenMemoryCodingRail`，通过 `MemoryProvider` 接受宿主构造的 Code Provider；core 不依赖 agent-memory 包。before_invoke 初始化、读取记忆；before_model_call 向当前 Agent 注入记忆上下文；after_invoke 对非后台、非空问答异步串行 sync_turn。

主 Agent 与 code_agent 可共用同一 Rail/Provider，但工具归属、提示词和召回缓存按 Agent 分开。模型回调通过内层 ReActAgent 找回对应外层状态。初始化锁防止并发启动，写入锁防止轮次重叠；每次写入上限 130 秒。

uninit 立即撤销所属工具与静态提示词，异步清理动态上下文；最后一位使用者退出后排空写入再关闭 Provider。`drain()` 等待写入，`wait_closed()` 等待已安排的清理，宿主淘汰整个共享实例时用 `aclose()`。同一事件循环中不做同步 Future.result 等待。

工具注册跳过缺失或空白名称；注册或静态提示词装配异常时撤回本 owner 的部分注册，保留 Agent 可用。工具、静态提示词或动态上下文的单项清理失败只记录告警，不阻断其余清理和 Provider 关闭。通过 JiuwenMemoryRail 的公共上下文辅助入口复用文本解析与后台判断，保留旧私有名称兼容原 Work 调用。

## 拒绝的方案

不复用工作 Rail 的单 Agent 可变状态，不增加新回调事件，不让 core 决定 Swarm 的项目键或配置开关。新旧 Coding 路由互斥和跳过旧 AutoMemory 由宿主负责。Dreaming 不变。

## 验证

测试覆盖主/子缓存和工具隔离、串行写入、后台请求、初始化失败、注册回滚、清理异常与注册后工具调用，以及 `create_code_agent → Runner.run_agent` 的真实回调链；检查模拟模型实际请求中含记忆、调用后有 sync_turn。

## 交付边界

离线模拟模型验证接线，不替代真实模型抽取质量验收；Web/TUI/IM 由下游共用适配器接入，本仓测试不宣称所有渠道已逐一通过。
