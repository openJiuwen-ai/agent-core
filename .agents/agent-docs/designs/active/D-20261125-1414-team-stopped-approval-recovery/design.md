---
id: D-20261125-1414-team-stopped-approval-recovery
title: 恢复阻塞持久化团队完成的审批邮箱
type: D
slug: team-stopped-approval-recovery
status: in-development
stage: active
author: MaxMadMax
author_email: chengkunck@gmail.com
reviewers: [independent-reviewer]
created: 2026-11-25
updated: 2026-11-25
approved-date: 2026-11-25
domain: protocol
modules: [agent_teams]
tags: [approval, team-completion]
scope: 持久化团队的 Native 成员停止后遗留审批回执阻塞完成
related: []
files: []
---

# 恢复阻塞持久化团队完成的审批邮箱

PR !3893 的完成轮询可能被发给 `STOPPED` 成员的未读审批卡住。缺少 harness 或等待超时无法证明回执已经失效，因此保留未读门禁，由收件人从可核实的原 checkpoint 恢复后判断。

- 复用 leader 完成轮询与成员恢复。只有任务均为终态且成员均处于 settled 状态，目标是无活跃 runtime 且只有合法审批未读的同进程 Native 成员时，才尝试恢复。
- 限定当前 leader 会话持有的原成员身份、精确快照及内置 InMemory checkpointer 实例。确认原成员已退出，并在新实例校验装载成功后恢复邮箱处理。
- 复用原子状态迁移和现有任务清理。失败或取消时仅撤销本次尝试拥有的状态，避免覆盖并发变更。消费规则见 S_03。

验收覆盖过期回执清除后完成、匹配回执只执行一次，以及身份或快照不明、启动失败和停机竞争时保留回执。

2026-11-25 独立初审要求补足原 checkpoint 的身份凭据及启动成功判定。修订后独立复审通过。
