---
id: F_20261116_1615_message-delivery-repair
title: Repair message feedback, broadcast blocking, and withdrawal
type: F
author: yzdnh123
author_email: yzdnh123@gmail.com
designs: []
design_note: 用户授权按已分析问题合理修复，保留私信模式和接收方 Jev 策略。
modules:
  - openjiuwen/agent_teams
files:
  - openjiuwen/agent_teams/agent/coordination/handlers/message.py
  - openjiuwen/agent_teams/tools/database/message_dao.py
  - openjiuwen/agent_teams/tools/tool_message.py
---

# 问题与修复

1. 时间水位让延后广播挡住新的紧急广播。普通广播改为逐消息、逐接收方 handled 状态；群聊保留时间游标。
2. Jev 异常被误记为主动丢弃。依用户要求，未配置或失败时退回原投递机制；反馈标明 legacy_fallback。
3. 私信缺反馈且旧要求无法撤回。增加私信 message_id/status 和 withdraw；只允许发送者撤回尚未交给运行时的接收方输入。写锁串行化撤回与投递认领。
4. abort 错误清除普通输入、受单任务限制及时间戳碰撞影响。清除 follow_up，其他旧输入 held 留在历史，Leader 当前成员中止不依赖任务数量。时间戳按团队递增。
5. 团队契约分叉、共享文件验收漂移、交接冒充完成和回执循环。中英文 Leader/Teammate 提示词要求单一契约维护者、递增版本、任务写入版本、提交/摘要快照验收、区分交接与完成、只在有实质变更时回复。

```mermaid
flowchart TD
  A[普通广播逐接收方判断] --> B{Jev 结果}
  B -->|steer| C[认领后交给运行时]
  B -->|follow_up| D[记录绑定任务并等待]
  D --> E[后续广播可继续判断]
  B -->|drop| F[记录主动丢弃]
  B -->|失败| G[原机制直接投递或 steer]
  H[发送者撤回] --> I{是否已认领或提交}
  I -->|否| J[withdrawn 禁止旧输入投递]
  I -->|是| K[返回实际状态 另发纠正]
```

## 验证与边界

最终回归：379 passed、19 skipped（13 项依赖弃用警告）。测试日志在工作区 work/routing-fix-final.out。覆盖消息 DAO、消息工具、消息处理、群聊、TeamAgent、NativeHarness 中止和 TeamHarness。
新增回归覆盖广播越过等待消息且不丢失旧消息、私信状态、撤回权限及认领竞争、同 tick 的停止恢复、Jev 缺配置和失败时的原机制回退和 abort 分类处理。
本轮未重跑真实模型业务情景；提示词协作约束不等同于程序强制的契约隔离。外部 Harness 及模型是否遵守协作要求仍需情景验证。
