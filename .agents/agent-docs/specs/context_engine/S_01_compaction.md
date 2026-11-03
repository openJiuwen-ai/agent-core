---
id: S_context_engine_01_compaction
title: 原生上下文压缩拦截与候选提交规约
type: S
author: 刘瑜
author_email: liuyu524@huawei.com
modules: [core/context_engine]
files: [openjiuwen/core/context_engine/compaction.py, openjiuwen/core/context_engine/context_engine.py, openjiuwen/core/context_engine/context/context.py]
updated: 2026-11-03
---

# 原生上下文压缩拦截与候选提交规约

## 1. 范围与职责

适用于 ContextEngine 的原生会话消息。Core 管理快照、处理器候选、原生消息校验与正式安装，宿主负责触发来源、会话空闲准入和领域事件。Core 不依赖 Server session.compact、JSONL 或界面渲染。

设计依据：D-20261103-1156-session-compaction-events，当前位于 designs/active；实施和证据见 [F_20261103_1515_session-compaction-events](../../features/F_20261103_1515_session-compaction-events.md)。

## 2. 公开入口与输入输出

`ContextEngine.register_compaction_hook(hook: CompactionHook) -> Callable[[], None]` 注册单个宿主 Hook，影响该引擎已有及后续原生 Context。重复注册失败，返回的移除函数幂等，移除时清理缓存；旧注册准备的结果不得继续安装。

`await ContextEngine.compact_context(context_id="default_context_id", session=None, *, session_id=None, trigger="manual", instructions=None, processor_types=None) -> CompactionDecision` 面向宿主使用。Session 优先解析真实会话；context_id 是该 Session 内真实上下文键。用户领域事件不得通过修改输入切换这两个目标。processor_types 是宿主选择的原生算法白名单，不从插件公开参数取值。

| 对象/字段 | 含义与填写规则 |
| --- | --- |
| CompactionRequest.session_id/context_id | 引擎填入的真实目标；宿主 next 不得修改 |
| trigger | manual、auto、plugin、precompute；宿主确认来源，next 不得修改 |
| instructions | 本次摘要要求，字符串或 None；空白视为 None；通过独立模型代理进入实际摘要调用，不改共享 prompt |
| messages | 按旧到新排列的原生 BaseMessage 快照 tuple；ADD 自动压缩包含本次待追加消息。宿主可返回改写后的请求 |
| CompactionCandidate.messages | 最终可压缩对话内容的完整原生消息 tuple；不能只返回摘要增量并期待引擎自动保留其它消息 |
| tokens_before/tokens_after | 引擎按本地 TokenCounter 测量；缺失用 None，不虚构零值；precompute 不报告 tokens_after；不信任 Hook 自填计数 |
| CompactionSkipped.skip | 去空白后 1～512 字符的原因；与 messages 分支互斥 |

CompactionHook 接收 `(request, default_continuation)` 并异步返回 CompactionDecision。default_continuation 接受改写请求并只准备候选。同内容重复 next 复用候选；改变内容后再次 next 报 COMPACT_CONTINUATION_REUSED。Hook 可以不 next，或在 next 后改变/跳过结果。

## 3. 不变量

- CP-01：整条宿主 Hook 返回前不替换 live 消息，不发布候选卸载文件或推进 live Session memory 状态。
- CP-02：默认压缩在独立缓冲、处理器状态副本、会话状态快照和暂存目录内运行。原算法不改为另一个“测试摘要器”；原生和 forked 内置处理器均提供候选 fork。第三方被选中处理器必须显式提供安全 fork，否则失败。
- CP-03：只 fork 本次选中压缩器；未参与的旁路处理器不执行、不获得额外候选副作用。
- CP-04：最终消息必须通过原生类型、工具调用/结果配对和固定目标校验。不能伪造未完成工具调用或孤立结果。
- CP-05：最终 skip 不安装本次结果。ADD 仍正常追加原 pending，一次且不丢失；GET 重新形成同一次最终窗口并保留系统提示、工具和窗口约束。
- CP-06：同一 live 目标的正式安装串行。嵌套压缩在等待自身锁前拒绝。消息、Hook 代次、模型/处理器/记忆依赖变化使旧候选失效。
- CP-07：正式结果最多安装一次；有有效 Session 时必须完成 save_contexts 和 Session.commit，之后才能报告 committed。
- CP-08：安装前取消丢弃候选；安装中取消等待有界保存收尾，不把取消解释为已经回滚。保存结果不确定时冻结后续 ADD/GET，只有成功加载权威状态才解除。
- CP-09：precompute 不占用 live 处理器锁等待模型，不安装，也不生成正式压缩历史。缓存每个上下文一份，可丢弃；原前缀、策略和记忆依赖有效时才复用，合法追加尾部原样接回。
- CP-10：传统 compress_context 在没有 Hook 时保持旧路径；注册 Hook 后主动压缩、Context 直接调用、自动摘要和溢出恢复进入统一边界。普通 micro/offload 或宿主旁路检索维护不冒充摘要事件。

## 4. 生效、持久化与恢复

通过最终校验后发布最终引用的暂存文件，安装消息及必要的 offload 数据；保留默认候选时应用其处理器/Session 状态，改写候选则失效相应派生状态。原 save_contexts/commit 保存实际消息。模型费用、trace 和插件自行实施的外部操作不回滚。

文件与 Session 没有跨存储原子事务。文件发布后保存前可能留下未引用文件；保存未知不能恢复旧内存后声称 skip。COMPACT_COMMIT_UNCERTAIN 会冻结目标，成功 reload 后恢复；正式提交后的通知错误为 COMPACT_NOTIFICATION_FAILED，不宣称保存未发生。

clear_context 在压缩在途时拒绝，并在可清理时释放候选缓存。宿主结束会话/重建模型/卸载策略时移除注册或调用 invalidate_compaction_candidates；不持久化 Hook、临时句柄或预计算任务。

## 5. 错误与验证入口

CompactionError.code 使用 COMPACT_BUSY、COMPACT_REENTRANT、COMPACT_UNSUPPORTED、COMPACT_SESSION_UNAVAILABLE、COMPACT_INVALID_RESULT、COMPACT_STALE、COMPACT_CONTINUATION_REUSED、COMPACT_COMMIT_UNCERTAIN、COMPACT_NOTIFICATION_FAILED 等稳定符号。模型和文件错误保留真实异常，不伪装成 skip。新增数值状态码未分配。

行为测试：`tests/unit_tests/core/context_engine/test_compaction_hook.py`；扩展 Runtime 调用作用域测试：`tests/unit_tests/core/extension/test_invocation_scope.py`。实际执行范围和真实 Server 消费证据在实施 F 文档唯一维护。
