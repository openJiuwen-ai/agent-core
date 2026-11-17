---
id: F_20261116_1529_abort-stop-and-clear
title: Leader abort stops execution and clears follow-ups
type: F
author: yzdnh123
author_email: yzdnh123@gmail.com
designs: []
design_note: 用户明确要求直接停止、清空 follow_up，只有 Leader 新消息能恢复，按确认范围直接实施。
modules:
  - openjiuwen/agent_teams
files:
  - openjiuwen/agent_teams/agent/coordination/handlers/message.py
  - openjiuwen/agent_teams/harness/native_harness.py
---

# 实施决定

替代原先“取消当前执行后执行 abort 正文”的私信行为。普通集群的 Leader abort
先建立停止屏障，再硬取消当前轮次；清空运行时 follow_up 与中止之前的邮箱输入，
保留历史但不重放，不启动模型汇报。停止屏障写入成员子 Session，位于回滚的
DeepAgent 状态之外。新显式 Leader 私信解除屏障，广播、队友与调度模板不能解除。
任务板状态不变；已发生的文件副作用不回滚。同一任务的更新时间变化不会使中止失效。

## 验证

定向运行消息处理、NativeHarness 中止、消息工具和 TeamAgent coordination 测试：
198 passed、19 skipped，包含群聊兼容性和 TeamHarness 测试。包含真实 NativeHarness supervisor
取消运行、清空排队消息、屏蔽异步输入、显式恢复后不重放旧消息的测试。
原手工会话是问题证据，不作为新实现通过证据。本次尚未重复真实模型业务情景。

## 边界

这次不引入任务暂停枚举或全局契约同步机制。NativeHarness 的屏障和队列清理有行为覆盖；
第三方外部 Harness 未进行此停止协议的端到端验证。
