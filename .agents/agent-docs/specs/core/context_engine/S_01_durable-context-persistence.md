---
id: S_01_durable-context-persistence
title: 原生上下文持久化准备与延迟卸载恢复
type: S
author: 刘瑜
author_email: liuyu524@huawei.com
updated: "2026-11-09"
modules:
  - openjiuwen/core/context_engine
  - openjiuwen/core/single_agent
files:
  - openjiuwen/core/context_engine/context_engine.py
  - openjiuwen/core/context_engine/base.py
  - openjiuwen/core/context_engine/context/context.py
  - openjiuwen/core/context_engine/context/message_buffer.py
  - openjiuwen/core/context_engine/context/file_offload_store.py
  - openjiuwen/core/single_agent/rail/base.py
  - openjiuwen/core/single_agent/agents/react_agent.py
---

# 原生上下文持久化准备与延迟卸载恢复

## 1. 范围与职责

Core 提供通用的保存准备 hook、输入元数据和延迟原文存储。宿主负责快照格式、路径、读取预算、审计消费边界、生命周期代次和保留策略；Core 不依赖 JiuwenSwarm，不创建第二套执行或恢复循环。

## 2. 不变量

- `ContextEngine.save_contexts()` 先收集 state，再等待可选宿主 hook，最后一次性把返回的 context mapping 安装到 Session。
- hook 不提交 Session，不递归调用 `save_contexts()`；异常使本次 context 保存失败。实际持久化、审批中断状态和执行状态仍由原生 Session 的提交边界决定。
- 默认没有 hook，既有调用行为不变。一个 engine 只接受一个所有者的 hook；只有相同函数对象能清理自己的注册。
- 卸载原文不在恢复时整体解码；同一句柄更新不会改写较旧检查点引用的内容版本。

## 3. 公开接口

```python
async def prepare(session, states):
    # Publish immutable artifacts and add their reference to this Session.
    # Return the complete context mapping, without committing.
    return states

engine.set_persistence_hook(prepare)
await engine.save_contexts(session)
await session.commit()
engine.clear_persistence_hook(prepare)
```

`engine.persistence_hook` 用于宿主幂等装配/卸载。重复注册同一对象允许；不同对象覆盖已注册 hook 报错。返回非字典报错，不能把空值伪装成有效保存。

`ModelContext.persist_offloaded_messages(directory) -> bool` 是可选能力；基类返回 False，原生实现返回 True。directory 必须是绝对目录，已启用的原生存储不能在活跃上下文中迁移到另一目录。宿主需要维护目录的生命周期。

## 4. 状态结构与原文版本

默认 state 仍保留 `messages/offload_messages/last_context_window_access_at`。启用持久卸载后新增 `offload_directory`、`offload_versions: {handle: sha256}`，已转存的原文从 `offload_messages` 移出。

原文按 JSON 流式写临时文件，flush、fsync 后按内容 SHA-256 原子发布。写入全部成功后才切换内存索引；旧 state 的字典不被清空。相同 handle 的新值生成新版本，旧检查点继续指向原版本。clear 移除当前索引，不删除仍可能被旧检查点引用的不可变文件。

`load_state()` 只装载索引，保持 User/Assistant/Tool/System 类型。显式 reload 一次最多读取 8 MiB；超限、损坏或版本文件缺失报错，不逐条跳过。旧式纯内存 state 继续可用；既有 filesystem 卸载路径保持原实现。

## 5. 输入身份与调用链

`UserMessageInputs.metadata` 是宿主可填的附加字典。原生 `_admit_user_message()` 只在输入实际加入窗口时复制这些字段；所有 parts 被丢弃时不安装元数据。Core 的 origin/source 字段覆盖同名输入，应用字段不能伪造原生来源。

```text
ON_USER_MESSAGE(parts, source, metadata)
  → 原生 UserMessage 入窗
  → 模型/工具/压缩及中断处理
  → save_contexts 收集有效窗口
  → 宿主 persistence_hook 准备工件和引用
  → context 与引用写入同一 Session
  → Session.commit 同时持久化 context、引用和中断状态
```

## 6. Harness 初始化与兼容

宿主在第一轮前恢复上下文，应先 `await deep_agent.ensure_initialized()`，确保 rails 和处理器/模型绑定完成；该方法幂等，不消费输入，也不调用模型。`start()` 绑定交互内核与“已完成 rails 初始化”是不同状态。

新字段均为可选；不启用持久卸载时保留既有状态形态。外部 ModelContext 可不实现持久卸载，宿主必须根据能力和原文状态决定是否允许自己的快照策略。Core 不替宿主决定丢弃历史、自动重试业务工具或清理原始文件。

## 7. 依据与验证

`test_durable_context_persistence.py` 验证保存顺序、失败不安装半份 context、注册所有权、惰性恢复、句柄版本隔离、大原文写入内存预算和输入来源字段。已有 ContextEngine/SessionModelContext 回归一起执行。下游真实进程、审计迁移及线协议验证归属宿主，不能由这些 SDK 单测替代。
