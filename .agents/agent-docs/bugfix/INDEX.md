---
title: 故障修复 B索引
author: sync_documents.py
generated: true
---

# 故障修复 B索引

由 sync_documents.py 从文档 frontmatter 自动生成；请修改源文档后重新生成，不手工维护。

按模块分组；跨模块文档在各相关分组展示。

## 模块导航

| 模块 |
|---|
| [agent_teams](#module-agent_teams) |
| [agent_teams/workflow](#module-agent_teams-workflow) |
| [core/context_engine](#module-core-context_engine) |
| [core/controller](#module-core-controller) |
| [core/foundation/llm](#module-core-foundation-llm) |
| [core/runner/drunner/dmessage_queue](#module-core-runner-drunner-dmessage_queue) |
| [core/session](#module-core-session) |
| [core/sys_operation](#module-core-sys_operation) |
| [extensions/a2a](#module-extensions-a2a) |
| [extensions/observability](#module-extensions-observability) |
| [harness](#module-harness) |
| [harness/subagents](#module-harness-subagents) |
| [harness/tools/browser_move/decision](#module-harness-tools-browser_move-decision) |
| [harness/tools/subagent](#module-harness-tools-subagent) |

<a id="module-agent_teams"></a>

## agent_teams

| ID / 文档 | 类型 | 标题 | 作者 | 关联 D |
|---|---|---|---|---|
| [B_01_team-root-adopts-foreign-session-root](agent_teams/B_01_team-root-adopts-foreign-session-root.md) | B | Team 根 span 误认并发会话的 root，导致整条团队轨迹丢失 | alan_cheng | 待核实 |
| [B_02_native-harness-interrupt-checkpoint-before-output](agent_teams/B_02_native-harness-interrupt-checkpoint-before-output.md) | B | NativeHarness 中断结果输出前未持久化恢复状态 | wangcan103 | 待核实 |
| [B_03_task-updated-event-stale-payload](agent_teams/B_03_task-updated-event-stale-payload.md) | B | TASK_UPDATED 事件载荷取自写前快照且不携带任务状态 | wangcan103 | 待核实 |
| [B_04_autonomous-create-task-inline-member-leak](agent_teams/B_04_autonomous-create-task-inline-member-leak.md) | B | autonomous 团队的 leader 照着共享文案向 create_task 传 `member`，字段被静默丢弃、任务无主落板 | alan_cheng | 待核实 |
| [B_04_inbound-relative-time-and-mailbox-order](agent_teams/B_04_inbound-relative-time-and-mailbox-order.md) | B | 被唤醒成员倒序读邮箱，入站消息的相对时间写死在历史里 | alan_cheng | 待核实 |
| [B_05_paused-team-rebuilt-on-every-request](agent_teams/B_05_paused-team-rebuilt-on-every-request.md) | B | 同一模型选择被判成"换模型"，paused 团队每次追问都被拆掉重建 | alan_cheng | 待核实 |
| [B_06_scheduled-reassign-active-task-handoff-gap](agent_teams/B_06_scheduled-reassign-active-task-handoff-gap.md) | B | 调度模式：改派一个已开工的任务给尚未启动的成员后，新 owner 永远收不到交接 | alan_cheng | 待核实 |
| [B_07_team-interrupt-resume-entry-and-leader-ready](agent_teams/B_07_team-interrupt-resume-entry-and-leader-ready.md) | B | 团队中断关闭流后无恢复入口：裸文本恢复静默失效，recovery 流伪造 "None" 轮次 | wangcan103 | 待核实 |
| [B_08_single-branch-worktree-startup-failure](agent_teams/B_08_single-branch-worktree-startup-failure.md) | B | 单分支仓库的 worktree 基线失效导致调查成员未启动 | MengHan18 | 待核实 |
| [B_09_all-done-prompt-stalls-remaining-work](agent_teams/B_09_all-done-prompt-stalls-remaining-work.md) | B | 看板清空的收尾提示让 leader 停止建任务，且之后再清空不再提示 | alan_cheng | 待核实 |
| [B_09_session-db-bind-delete-race](agent_teams/B_09_session-db-bind-delete-race.md) | B | Concurrent session cleanup can break another session's bind | 待确认 | 待核实 |
| [B_09_summary-source-waiting-feedback](agent_teams/B_09_summary-source-waiting-feedback.md) | B | Summary 来源待验收时误提示 Owner 结束回合等待 | 待确认 | 待核实 |
| [B_10_task-assigned-to-retired-member-stalls](agent_teams/B_10_task-assigned-to-retired-member-stalls.md) | B | 任务派给已退役的任务级成员后整夜无人执行 | alan_cheng | 待核实 |
| [B_20261031_1134_org-ddl-bypasses-write-lock](agent_teams/B_20261031_1134_org-ddl-bypasses-write-lock.md) | B | 组织表 DDL 绕过写锁导致团队激活写池耗尽 | alan_cheng | 无独立 D：普通缺陷修复，沿用 F_83（DDL 走写锁）与 tools/AGENTS.md「数据库并发」的既有契约，无独立设计 |
| [B_20261104_2030_cancellation-cause-lifecycle](core_runtime/B_20261104_2030_cancellation-cause-lifecycle.md) | B | 压缩取消一律误报 failed 及标记生命周期残留窗口缺陷 | CharlesWang2030 | 无独立 D：c4e52b7c 引入 cancellation cause 注册表属于常规缺陷修复，未归档独立设计；本次为对该提交检视发现的标记生命周期缺陷的后续修复，沿用注册表现有契约（TTL、非消费式 resolve、前缀继承）。 |
| [B_20261104_2054_recovered-context-seed-and-execution-environment](agent_teams/B_20261104_2054_recovered-context-seed-and-execution-environment.md) | B | 冷恢复后成员策略 seed 与执行环境传递修复 | alan_cheng | 无独立 D：无独立 D；修复 F_20261104_1720_tool-execution-environment / F_20261104_1721_session-execution-settings 在冷恢复与远程调用路径上的遗漏。 |
| [B_20261105_0911_swarmflow-thirdparty-trajectory](agent_teams/B_20261105_0911_swarmflow-thirdparty-trajectory.md) | B | Swarmflow 三方 Agent 轨迹未绑定成员身份 | wangcan103 | 无独立 D：小范围补齐既有 HarnessTrajectoryRecorder 的 Swarmflow 接线，不新增协议或公开 API。 |
| [B_20261117_1626_directed-assignment-notice-dropped](agent_teams/B_20261117_1626_directed-assignment-notice-dropped.md) | B | 定向指派/改派的通知被接收端 pending 判据丢弃，新 owner 永不唤醒 | xiajing | openjiuwen/agent_teams | openjiuwen/agent_teams/agent/coordination/handlers/task_board.py, tests/unit_tests/agent_teams/agent/test_directed_assignment_notice.py | 无独立 D：只改消费端投递判据，沿用 S_03 的 TASK_CLAIMED 定向投递契约 |

<a id="module-agent_teams-workflow"></a>

## agent_teams/workflow

| ID / 文档 | 类型 | 标题 | 作者 | 关联 D |
|---|---|---|---|---|
| [B_20261102_1120_avatar-child-session-trace-root](agent_teams/B_20261102_1120_avatar-child-session-trace-root.md) | B | Swarmflow avatar child session 找不到 Team 轨迹 root | wangcan103 | 无独立 D：普通缺陷修复；沿用 S_18 的 session 隔离契约，无独立设计 |

<a id="module-core-context_engine"></a>

## core/context_engine

| ID / 文档 | 类型 | 标题 | 作者 | 关联 D |
|---|---|---|---|---|
| [B_20261103_1050_grandocean-review-feedback](core_runtime/B_20261103_1050_grandocean-review-feedback.md) | B | grandocean 七项检视意见的兼容性修复 | alan_cheng | 无独立 D：用户明确要求按已有七条检视意见直接开发并补测试，无独立设计文档。 |
| [B_20261104_2030_cancellation-cause-lifecycle](core_runtime/B_20261104_2030_cancellation-cause-lifecycle.md) | B | 压缩取消一律误报 failed 及标记生命周期残留窗口缺陷 | CharlesWang2030 | 无独立 D：c4e52b7c 引入 cancellation cause 注册表属于常规缺陷修复，未归档独立设计；本次为对该提交检视发现的标记生命周期缺陷的后续修复，沿用注册表现有契约（TTL、非消费式 resolve、前缀继承）。 |

<a id="module-core-controller"></a>

## core/controller

| ID / 文档 | 类型 | 标题 | 作者 | 关联 D |
|---|---|---|---|---|
| [B_20261103_1050_grandocean-review-feedback](core_runtime/B_20261103_1050_grandocean-review-feedback.md) | B | grandocean 七项检视意见的兼容性修复 | alan_cheng | 无独立 D：用户明确要求按已有七条检视意见直接开发并补测试，无独立设计文档。 |

<a id="module-core-foundation-llm"></a>

## core/foundation/llm

| ID / 文档 | 类型 | 标题 | 作者 | 关联 D |
|---|---|---|---|---|
| [B_20261105_1628_llm-usage-model-name-response-first](core_runtime/B_20261105_1628_llm-usage-model-name-response-first.md) | B | LLM usage.model_name 改为响应体 model 优先 | yidaaichixia | 无独立 D：语义纠错类修复；依据 GenAI 语义约定对 gen_ai.response.model 的定义（生成响应的模型名）与同文件 response_model 既有响应体优先取法，无独立方案取舍需要设计文档。 |

<a id="module-core-runner-drunner-dmessage_queue"></a>

## core/runner/drunner/dmessage_queue

| ID / 文档 | 类型 | 标题 | 作者 | 关联 D |
|---|---|---|---|---|
| [B_20261103_1050_grandocean-review-feedback](core_runtime/B_20261103_1050_grandocean-review-feedback.md) | B | grandocean 七项检视意见的兼容性修复 | alan_cheng | 无独立 D：用户明确要求按已有七条检视意见直接开发并补测试，无独立设计文档。 |

<a id="module-core-session"></a>

## core/session

| ID / 文档 | 类型 | 标题 | 作者 | 关联 D |
|---|---|---|---|---|
| [B_20261103_1050_grandocean-review-feedback](core_runtime/B_20261103_1050_grandocean-review-feedback.md) | B | grandocean 七项检视意见的兼容性修复 | alan_cheng | 无独立 D：用户明确要求按已有七条检视意见直接开发并补测试，无独立设计文档。 |

<a id="module-core-sys_operation"></a>

## core/sys_operation

| ID / 文档 | 类型 | 标题 | 作者 | 关联 D |
|---|---|---|---|---|
| [B_20261104_2054_recovered-context-seed-and-execution-environment](agent_teams/B_20261104_2054_recovered-context-seed-and-execution-environment.md) | B | 冷恢复后成员策略 seed 与执行环境传递修复 | alan_cheng | 无独立 D：无独立 D；修复 F_20261104_1720_tool-execution-environment / F_20261104_1721_session-execution-settings 在冷恢复与远程调用路径上的遗漏。 |

<a id="module-extensions-a2a"></a>

## extensions/a2a

| ID / 文档 | 类型 | 标题 | 作者 | 关联 D |
|---|---|---|---|---|
| [B_20261103_1050_grandocean-review-feedback](core_runtime/B_20261103_1050_grandocean-review-feedback.md) | B | grandocean 七项检视意见的兼容性修复 | alan_cheng | 无独立 D：用户明确要求按已有七条检视意见直接开发并补测试，无独立设计文档。 |

<a id="module-extensions-observability"></a>

## extensions/observability

| ID / 文档 | 类型 | 标题 | 作者 | 关联 D |
|---|---|---|---|---|
| [B_20261102_1120_avatar-child-session-trace-root](agent_teams/B_20261102_1120_avatar-child-session-trace-root.md) | B | Swarmflow avatar child session 找不到 Team 轨迹 root | wangcan103 | 无独立 D：普通缺陷修复；沿用 S_18 的 session 隔离契约，无独立设计 |

<a id="module-harness"></a>

## harness

| ID / 文档 | 类型 | 标题 | 作者 | 关联 D |
|---|---|---|---|---|
| [B_20261104_2030_cancellation-cause-lifecycle](core_runtime/B_20261104_2030_cancellation-cause-lifecycle.md) | B | 压缩取消一律误报 failed 及标记生命周期残留窗口缺陷 | CharlesWang2030 | 无独立 D：c4e52b7c 引入 cancellation cause 注册表属于常规缺陷修复，未归档独立设计；本次为对该提交检视发现的标记生命周期缺陷的后续修复，沿用注册表现有契约（TTL、非消费式 resolve、前缀继承）。 |
| [B_20261114_1107_interactive-memory-turn](harness/B_20261114_1107_interactive-memory-turn.md) | B | 交互式中断恢复后记忆丢失修复 | robin1217 | 无独立 D：本次按对话中确认的 D3 收敛方案修复，未建立独立 D 文档；外部 F08 的 D3 是决策编号，不是设计资产标识。 |

<a id="module-harness-subagents"></a>

## harness/subagents

| ID / 文档 | 类型 | 标题 | 作者 | 关联 D |
|---|---|---|---|---|
| [B_20261102_1733_browser-pr-contract-review](harness/B_20261102_1733_browser-pr-contract-review.md) | B | Browser PR budget and exception contract corrections | ShowHsiang | 无独立 D：Local corrections to the existing explicit-budget and fact-retention contracts; no new architecture or feature. |

<a id="module-harness-tools-browser_move-decision"></a>

## harness/tools/browser_move/decision

| ID / 文档 | 类型 | 标题 | 作者 | 关联 D |
|---|---|---|---|---|
| [B_20261102_1733_browser-pr-contract-review](harness/B_20261102_1733_browser-pr-contract-review.md) | B | Browser PR budget and exception contract corrections | ShowHsiang | 无独立 D：Local corrections to the existing explicit-budget and fact-retention contracts; no new architecture or feature. |

<a id="module-harness-tools-subagent"></a>

## harness/tools/subagent

| ID / 文档 | 类型 | 标题 | 作者 | 关联 D |
|---|---|---|---|---|
| [B_20261102_1733_browser-pr-contract-review](harness/B_20261102_1733_browser-pr-contract-review.md) | B | Browser PR budget and exception contract corrections | ShowHsiang | 无独立 D：Local corrections to the existing explicit-budget and fact-retention contracts; no new architecture or feature. |
