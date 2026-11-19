---
author: Wang Tao
author_email: zzyyww0808@gmail.com
designs: []
design_note: 依据本会话已确认的跨仓 Coding 文档记忆方案及后续交互轮次复用决定；AutoMemory 保留宿主原有开关。
id: F-20261107-1700-coding-document-memory
title: Coding 文档记忆 Rail
type: F
modules: [openjiuwen/harness/rails]
files: [openjiuwen/harness/rails/memory/jiuwen_memory_coding_rail.py, openjiuwen/harness/rails/memory/jiuwen_memory_rail.py, openjiuwen/harness/rails/memory/memory_turn.py]
---

# Coding 文档记忆 Rail

日期：2026-11-07。

## 决策

新增 `JiuwenMemoryCodingRail`，通过 `MemoryProvider` 接受宿主构造的 Code Provider；core 不依赖 agent-memory 包。before_invoke 初始化、读取记忆；before_model_call 向当前 Agent 注入记忆上下文；after_tool_call 收集有效 ask_user 回答；after_invoke 对正常完成、非后台、非空的逻辑轮次异步串行 sync_turn。

主 Agent 与 code_agent 可共用同一 Rail/Provider，但工具归属、提示词和召回缓存按 Agent 分开。模型回调通过内层 ReActAgent 找回对应外层状态。初始化锁防止并发启动，写入锁防止轮次重叠；每次写入上限 130 秒。

uninit 立即撤销所属工具与静态提示词，异步清理动态上下文；最后一位使用者退出后排空写入再关闭 Provider。`drain()` 等待写入，`wait_closed()` 等待已安排的清理，宿主淘汰整个共享实例时用 `aclose()`。同一事件循环中不做同步 Future.result 等待。

工具注册跳过缺失或空白名称；注册或静态提示词装配异常时撤回本 owner 的部分注册，保留 Agent 可用。工具、静态提示词或动态上下文的单项清理失败只记录告警，不阻断其余清理和 Provider 关闭。通过 JiuwenMemoryRail 的公共上下文辅助入口复用文本解析与后台判断，保留旧私有名称兼容原 Work 调用。

## 拒绝的方案

不复用工作 Rail 的单 Agent 可变状态，不增加新回调事件，不让 core 决定 Swarm 的项目键或配置开关。新旧 Coding 记忆路由互斥由宿主负责。后续已确认保留 AutoMemory 原有开关，不再因接入 Jiuwen Provider 自动跳过；早期跳过决定不再适用。Dreaming 不变。

## 交互轮次复用（2026-11-18）

将 Work 既有中断恢复聚合提取到 `MemoryTurnBuffer`，两种 Rail 共用算法，各自保留 Provider 生命周期和写入控制。连续追问时保留原始请求及已接受的新回答，最终正常回复后提交一次；中断不提交，异常和取消清理，不把权限批准、过期或重复回答写入用户材料。

Coding 缓冲按挂载 Agent 分开，再按实际 Session 隔离，即使共享 Provider、Session 标识或回调 extra 也不混入彼此的问答；卸载仅清理该 Agent。沿用每缓冲 128 个会话、每轮 16,000 字符和 30 分钟闲置淘汰，不新增持久化恢复能力。拒绝复制两份聚合算法，也不继承 Work 的单 Agent 生命周期状态。

## 验证

测试覆盖主/子缓存和工具隔离、串行写入、后台请求、初始化失败、注册回滚、清理异常与注册后工具调用，以及 `create_code_agent → Runner.run_agent` 的真实回调链；检查模拟模型实际请求中含记忆、调用后有 sync_turn。

2026-11-18 定向回归 110 项通过：复用 `test_jiuwen_memory_interrupts.py` 的真实 DeepAgent 中断流程分别验证 Work/Coding 的普通与流式调用、连续追问、不同回答格式、过期回答与权限批准过滤；补充 Coding 同 Session 多 Agent 隔离、卸载重挂和失败续接回归，同时验证 Work CLI 装配与事件路由。使用本地 Python 3.12 环境、模拟 LLM/Provider，未调用外部模型服务。

## 交付边界

离线模拟模型验证接线，不替代真实模型抽取质量验收；Web/TUI/IM 由下游共用适配器接入，本仓测试不宣称所有渠道已逐一通过。
