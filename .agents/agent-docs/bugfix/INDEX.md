---
title: 故障修复 B索引
author: 霆龙(吴建宇)
author_email: wujianyu@xiaohongshu.com
generated: true
---

# 故障修复 B索引

由 sync_documents.py 从文档 frontmatter 自动生成；请修改源文档后重新生成，不手工维护。

共 9 篇文档，按模块分组；跨模块文档在各相关分组展示，总数按文档去重。

## 模块导航

| 模块 | 文档数 |
|---|---|
| [agent_teams](#module-1) | 8 |
| [symphony/flow](#module-2) | 1 |

<a id="module-1"></a>

## agent_teams

| ID / 文档 | 类型 | 标题 | 作者 | 模块 | 代码文件 | 关联 D |
|---|---|---|---|---|---|---|
| [B_01_team-root-adopts-foreign-session-root](agent_teams/B_01_team-root-adopts-foreign-session-root.md) | B | Team 根 span 误认并发会话的 root，导致整条团队轨迹丢失 | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [B_02_native-harness-interrupt-checkpoint-before-output](agent_teams/B_02_native-harness-interrupt-checkpoint-before-output.md) | B | NativeHarness 中断结果输出前未持久化恢复状态 | wangcan103 | openjiuwen/agent_teams | — | 待核实 |
| [B_03_task-updated-event-stale-payload](agent_teams/B_03_task-updated-event-stale-payload.md) | B | TASK_UPDATED 事件载荷取自写前快照且不携带任务状态 | wangcan103 | openjiuwen/agent_teams | — | 待核实 |
| [B_04_autonomous-create-task-inline-member-leak](agent_teams/B_04_autonomous-create-task-inline-member-leak.md) | B | autonomous 团队的 leader 照着共享文案向 create_task 传 `member`，字段被静默丢弃、任务无主落板 | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [B_04_inbound-relative-time-and-mailbox-order](agent_teams/B_04_inbound-relative-time-and-mailbox-order.md) | B | 被唤醒成员倒序读邮箱，入站消息的相对时间写死在历史里 | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [B_05_paused-team-rebuilt-on-every-request](agent_teams/B_05_paused-team-rebuilt-on-every-request.md) | B | 同一模型选择被判成"换模型"，paused 团队每次追问都被拆掉重建 | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [B_06_scheduled-reassign-active-task-handoff-gap](agent_teams/B_06_scheduled-reassign-active-task-handoff-gap.md) | B | 调度模式：改派一个已开工的任务给尚未启动的成员后，新 owner 永远收不到交接 | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [B_07_team-interrupt-resume-entry-and-leader-ready](agent_teams/B_07_team-interrupt-resume-entry-and-leader-ready.md) | B | 团队中断关闭流后无恢复入口：裸文本恢复静默失效，recovery 流伪造 "None" 轮次 | wangcan103 | openjiuwen/agent_teams | — | 待核实 |

<a id="module-2"></a>

## symphony/flow

| ID / 文档 | 类型 | 标题 | 作者 | 模块 | 代码文件 | 关联 D |
|---|---|---|---|---|---|---|
| [B_20261028_1727_skillpack-legacy-redistillation](symphony/B_20261028_1727_skillpack-legacy-redistillation.md) | B | 升级普通成员校验后，旧配方阻止合格新证据生成候选 | 霆龙(吴建宇) | openjiuwen/symphony/flow | openjiuwen/symphony/flow/engine.py, tests/unit_tests/symphony/test_skillpack_member_types.py | symphony-design-docs:SDD-0013（非本仓或待定位） |
