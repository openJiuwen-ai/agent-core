---
title: 故障修复 B索引
author: lin-xiaoyu
author_email: xiaoyu.lin.work@outlook.com
generated: true
---

# 故障修复 B索引

由 sync_documents.py 从文档 frontmatter 自动生成；请修改源文档后重新生成，不手工维护。

按模块分组；跨模块文档在各相关分组展示。

## 模块导航

| 模块 |
|---|
| [agent_teams](#module-1) |
| [agent_teams/workflow](#module-2) |
| [core/context_engine](#module-3) |
| [core/controller](#module-4) |
| [core/runner/drunner/dmessage_queue](#module-5) |
| [core/session](#module-6) |
| [extensions/a2a](#module-7) |
| [extensions/observability](#module-8) |
| [harness](#module-9) |
| [harness/subagents](#module-10) |
| [harness/tools/browser_move/decision](#module-11) |
| [harness/tools/subagent](#module-12) |
| [symphony](#module-13) |
| [symphony/discovery](#module-14) |

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
| [B_08_single-branch-worktree-startup-failure](agent_teams/B_08_single-branch-worktree-startup-failure.md) | B | 单分支仓库的 worktree 基线失效导致调查成员未启动 | MengHan18 | openjiuwen/agent_teams | — | 待核实 |
| [B_09_all-done-prompt-stalls-remaining-work](agent_teams/B_09_all-done-prompt-stalls-remaining-work.md) | B | 看板清空的收尾提示让 leader 停止建任务，且之后再清空不再提示 | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [B_09_session-db-bind-delete-race](agent_teams/B_09_session-db-bind-delete-race.md) | B | Concurrent session cleanup can break another session's bind | 待确认 | openjiuwen/agent_teams | — | 待核实 |
| [B_09_summary-source-waiting-feedback](agent_teams/B_09_summary-source-waiting-feedback.md) | B | Summary 来源待验收时误提示 Owner 结束回合等待 | 待确认 | openjiuwen/agent_teams | — | 待核实 |
| [B_10_task-assigned-to-retired-member-stalls](agent_teams/B_10_task-assigned-to-retired-member-stalls.md) | B | 任务派给已退役的任务级成员后整夜无人执行 | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [B_20261031_1134_org-ddl-bypasses-write-lock](agent_teams/B_20261031_1134_org-ddl-bypasses-write-lock.md) | B | 组织表 DDL 绕过写锁导致团队激活写池耗尽 | alan_cheng | openjiuwen/agent_teams | openjiuwen/agent_teams/organization/db.py, openjiuwen/agent_teams/tools/database/__init__.py, openjiuwen/agent_teams/tools/database/engine.py, openjiuwen/agent_teams/spawn/shared_resources.py, tests/unit_tests/agent_teams/organization/test_org_db.py, tests/unit_tests/agent_teams/test_database_concurrency.py, tests/unit_tests/agent_teams/test_shared_resources.py | 无独立 D：普通缺陷修复，沿用 F_83（DDL 走写锁）与 tools/AGENTS.md「数据库并发」的既有契约，无独立设计 |
| [B_20261104_2030_cancellation-cause-lifecycle](core_runtime/B_20261104_2030_cancellation-cause-lifecycle.md) | B | 压缩取消一律误报 failed 及标记生命周期残留窗口缺陷 | CharlesWang2030 | openjiuwen/core/context_engine, openjiuwen/harness, openjiuwen/agent_teams | openjiuwen/core/context_engine/context/cancellation.py, openjiuwen/core/context_engine/context/context.py, openjiuwen/core/context_engine/context/processor_state_recorder.py, openjiuwen/core/context_engine/context/context_state.py, openjiuwen/core/context_engine/context_engine/__init__.py, openjiuwen/harness/deep_agent.py, openjiuwen/agent_teams/harness/native_harness.py, openjiuwen/agent_teams/runtime/manager.py, openjiuwen/extensions/observability/runtime.py, tests/unit_tests/core/context_engine/test_cancellation_cause.py, tests/unit_tests/core/context_engine/test_context_compression_state_stream.py, tests/unit_tests/harness/test_deep_agent_abort_cancellation_cause.py, tests/unit_tests/agent_teams/runtime/test_manager.py, tests/unit_tests/agent_teams/harness/test_abort.py | 无独立 D：c4e52b7c 引入 cancellation cause 注册表属于常规缺陷修复，未归档独立设计；本次为对该提交检视发现的标记生命周期缺陷的后续修复，沿用注册表现有契约（TTL、非消费式 resolve、前缀继承）。 |
| [B_20261105_0911_swarmflow-thirdparty-trajectory](agent_teams/B_20261105_0911_swarmflow-thirdparty-trajectory.md) | B | Swarmflow 三方 Agent 轨迹未绑定成员身份 | wangcan103 | openjiuwen/agent_teams | openjiuwen/agent_teams/external/member_runtime.py, openjiuwen/agent_teams/spawn/external_cli_spawn.py, openjiuwen/agent_teams/workflow/backends/team_worker_backend.py, openjiuwen/agent_teams/workflow/backends/avatar_session_backend.py, tests/unit_tests/agent_teams/external/test_member_runtime.py, tests/unit_tests/agent_teams/workflow/test_worker_backend.py, tests/unit_tests/agent_teams/workflow/test_avatar_session_backend.py | 无独立 D：小范围补齐既有 HarnessTrajectoryRecorder 的 Swarmflow 接线，不新增协议或公开 API。 |

<a id="module-2"></a>

## agent_teams/workflow

| ID / 文档 | 类型 | 标题 | 作者 | 模块 | 代码文件 | 关联 D |
|---|---|---|---|---|---|---|
| [B_20261102_1120_avatar-child-session-trace-root](agent_teams/B_20261102_1120_avatar-child-session-trace-root.md) | B | Swarmflow avatar child session 找不到 Team 轨迹 root | wangcan103 | openjiuwen/agent_teams/workflow, openjiuwen/extensions/observability | openjiuwen/agent_teams/workflow/backends/avatar_session_backend.py, openjiuwen/extensions/observability/span_context.py, tests/unit_tests/agent_teams/workflow/test_avatar_session_backend.py, tests/unit_tests/extensions/observability/test_span_context.py | 无独立 D：普通缺陷修复；沿用 S_18 的 session 隔离契约，无独立设计 |

<a id="module-3"></a>

## core/context_engine

| ID / 文档 | 类型 | 标题 | 作者 | 模块 | 代码文件 | 关联 D |
|---|---|---|---|---|---|---|
| [B_20261103_1050_grandocean-review-feedback](core_runtime/B_20261103_1050_grandocean-review-feedback.md) | B | grandocean 七项检视意见的兼容性修复 | alan_cheng | openjiuwen/core/session, openjiuwen/core/context_engine, openjiuwen/core/controller, openjiuwen/core/runner/drunner/dmessage_queue, openjiuwen/extensions/a2a | openjiuwen/core/session/session_controller/scope.py, openjiuwen/core/session/session_controller/chain_session.py, openjiuwen/core/session/vcs/models.py, openjiuwen/core/session/vcs/delta.py, openjiuwen/core/session/vcs/adapter.py, openjiuwen/core/context_engine/context_engine.py, openjiuwen/core/controller/modules/intent_recognizer.py, openjiuwen/core/runner/drunner/dmessage_queue/message_serializer.py, openjiuwen/core/runner/runner_config.py, openjiuwen/core/runner/drunner/dmessage_queue/message_queue_factory.py, openjiuwen/core/runner/drunner/dmessage_queue/message_queue_fake.py, openjiuwen/extensions/message_queue/message_queue_pulsar.py, openjiuwen/extensions/a2a/a2a_agent_executor.py | 无独立 D：用户明确要求按已有七条检视意见直接开发并补测试，无独立设计文档。 |
| [B_20261104_2030_cancellation-cause-lifecycle](core_runtime/B_20261104_2030_cancellation-cause-lifecycle.md) | B | 压缩取消一律误报 failed 及标记生命周期残留窗口缺陷 | CharlesWang2030 | openjiuwen/core/context_engine, openjiuwen/harness, openjiuwen/agent_teams | openjiuwen/core/context_engine/context/cancellation.py, openjiuwen/core/context_engine/context/context.py, openjiuwen/core/context_engine/context/processor_state_recorder.py, openjiuwen/core/context_engine/context/context_state.py, openjiuwen/core/context_engine/context_engine/__init__.py, openjiuwen/harness/deep_agent.py, openjiuwen/agent_teams/harness/native_harness.py, openjiuwen/agent_teams/runtime/manager.py, openjiuwen/extensions/observability/runtime.py, tests/unit_tests/core/context_engine/test_cancellation_cause.py, tests/unit_tests/core/context_engine/test_context_compression_state_stream.py, tests/unit_tests/harness/test_deep_agent_abort_cancellation_cause.py, tests/unit_tests/agent_teams/runtime/test_manager.py, tests/unit_tests/agent_teams/harness/test_abort.py | 无独立 D：c4e52b7c 引入 cancellation cause 注册表属于常规缺陷修复，未归档独立设计；本次为对该提交检视发现的标记生命周期缺陷的后续修复，沿用注册表现有契约（TTL、非消费式 resolve、前缀继承）。 |

<a id="module-4"></a>

## core/controller

| ID / 文档 | 类型 | 标题 | 作者 | 模块 | 代码文件 | 关联 D |
|---|---|---|---|---|---|---|
| [B_20261103_1050_grandocean-review-feedback](core_runtime/B_20261103_1050_grandocean-review-feedback.md) | B | grandocean 七项检视意见的兼容性修复 | alan_cheng | openjiuwen/core/session, openjiuwen/core/context_engine, openjiuwen/core/controller, openjiuwen/core/runner/drunner/dmessage_queue, openjiuwen/extensions/a2a | openjiuwen/core/session/session_controller/scope.py, openjiuwen/core/session/session_controller/chain_session.py, openjiuwen/core/session/vcs/models.py, openjiuwen/core/session/vcs/delta.py, openjiuwen/core/session/vcs/adapter.py, openjiuwen/core/context_engine/context_engine.py, openjiuwen/core/controller/modules/intent_recognizer.py, openjiuwen/core/runner/drunner/dmessage_queue/message_serializer.py, openjiuwen/core/runner/runner_config.py, openjiuwen/core/runner/drunner/dmessage_queue/message_queue_factory.py, openjiuwen/core/runner/drunner/dmessage_queue/message_queue_fake.py, openjiuwen/extensions/message_queue/message_queue_pulsar.py, openjiuwen/extensions/a2a/a2a_agent_executor.py | 无独立 D：用户明确要求按已有七条检视意见直接开发并补测试，无独立设计文档。 |

<a id="module-5"></a>

## core/runner/drunner/dmessage_queue

| ID / 文档 | 类型 | 标题 | 作者 | 模块 | 代码文件 | 关联 D |
|---|---|---|---|---|---|---|
| [B_20261103_1050_grandocean-review-feedback](core_runtime/B_20261103_1050_grandocean-review-feedback.md) | B | grandocean 七项检视意见的兼容性修复 | alan_cheng | openjiuwen/core/session, openjiuwen/core/context_engine, openjiuwen/core/controller, openjiuwen/core/runner/drunner/dmessage_queue, openjiuwen/extensions/a2a | openjiuwen/core/session/session_controller/scope.py, openjiuwen/core/session/session_controller/chain_session.py, openjiuwen/core/session/vcs/models.py, openjiuwen/core/session/vcs/delta.py, openjiuwen/core/session/vcs/adapter.py, openjiuwen/core/context_engine/context_engine.py, openjiuwen/core/controller/modules/intent_recognizer.py, openjiuwen/core/runner/drunner/dmessage_queue/message_serializer.py, openjiuwen/core/runner/runner_config.py, openjiuwen/core/runner/drunner/dmessage_queue/message_queue_factory.py, openjiuwen/core/runner/drunner/dmessage_queue/message_queue_fake.py, openjiuwen/extensions/message_queue/message_queue_pulsar.py, openjiuwen/extensions/a2a/a2a_agent_executor.py | 无独立 D：用户明确要求按已有七条检视意见直接开发并补测试，无独立设计文档。 |

<a id="module-6"></a>

## core/session

| ID / 文档 | 类型 | 标题 | 作者 | 模块 | 代码文件 | 关联 D |
|---|---|---|---|---|---|---|
| [B_20261103_1050_grandocean-review-feedback](core_runtime/B_20261103_1050_grandocean-review-feedback.md) | B | grandocean 七项检视意见的兼容性修复 | alan_cheng | openjiuwen/core/session, openjiuwen/core/context_engine, openjiuwen/core/controller, openjiuwen/core/runner/drunner/dmessage_queue, openjiuwen/extensions/a2a | openjiuwen/core/session/session_controller/scope.py, openjiuwen/core/session/session_controller/chain_session.py, openjiuwen/core/session/vcs/models.py, openjiuwen/core/session/vcs/delta.py, openjiuwen/core/session/vcs/adapter.py, openjiuwen/core/context_engine/context_engine.py, openjiuwen/core/controller/modules/intent_recognizer.py, openjiuwen/core/runner/drunner/dmessage_queue/message_serializer.py, openjiuwen/core/runner/runner_config.py, openjiuwen/core/runner/drunner/dmessage_queue/message_queue_factory.py, openjiuwen/core/runner/drunner/dmessage_queue/message_queue_fake.py, openjiuwen/extensions/message_queue/message_queue_pulsar.py, openjiuwen/extensions/a2a/a2a_agent_executor.py | 无独立 D：用户明确要求按已有七条检视意见直接开发并补测试，无独立设计文档。 |

<a id="module-7"></a>

## extensions/a2a

| ID / 文档 | 类型 | 标题 | 作者 | 模块 | 代码文件 | 关联 D |
|---|---|---|---|---|---|---|
| [B_20261103_1050_grandocean-review-feedback](core_runtime/B_20261103_1050_grandocean-review-feedback.md) | B | grandocean 七项检视意见的兼容性修复 | alan_cheng | openjiuwen/core/session, openjiuwen/core/context_engine, openjiuwen/core/controller, openjiuwen/core/runner/drunner/dmessage_queue, openjiuwen/extensions/a2a | openjiuwen/core/session/session_controller/scope.py, openjiuwen/core/session/session_controller/chain_session.py, openjiuwen/core/session/vcs/models.py, openjiuwen/core/session/vcs/delta.py, openjiuwen/core/session/vcs/adapter.py, openjiuwen/core/context_engine/context_engine.py, openjiuwen/core/controller/modules/intent_recognizer.py, openjiuwen/core/runner/drunner/dmessage_queue/message_serializer.py, openjiuwen/core/runner/runner_config.py, openjiuwen/core/runner/drunner/dmessage_queue/message_queue_factory.py, openjiuwen/core/runner/drunner/dmessage_queue/message_queue_fake.py, openjiuwen/extensions/message_queue/message_queue_pulsar.py, openjiuwen/extensions/a2a/a2a_agent_executor.py | 无独立 D：用户明确要求按已有七条检视意见直接开发并补测试，无独立设计文档。 |

<a id="module-8"></a>

## extensions/observability

| ID / 文档 | 类型 | 标题 | 作者 | 模块 | 代码文件 | 关联 D |
|---|---|---|---|---|---|---|
| [B_20261102_1120_avatar-child-session-trace-root](agent_teams/B_20261102_1120_avatar-child-session-trace-root.md) | B | Swarmflow avatar child session 找不到 Team 轨迹 root | wangcan103 | openjiuwen/agent_teams/workflow, openjiuwen/extensions/observability | openjiuwen/agent_teams/workflow/backends/avatar_session_backend.py, openjiuwen/extensions/observability/span_context.py, tests/unit_tests/agent_teams/workflow/test_avatar_session_backend.py, tests/unit_tests/extensions/observability/test_span_context.py | 无独立 D：普通缺陷修复；沿用 S_18 的 session 隔离契约，无独立设计 |

<a id="module-9"></a>

## harness

| ID / 文档 | 类型 | 标题 | 作者 | 模块 | 代码文件 | 关联 D |
|---|---|---|---|---|---|---|
| [B_20261104_2030_cancellation-cause-lifecycle](core_runtime/B_20261104_2030_cancellation-cause-lifecycle.md) | B | 压缩取消一律误报 failed 及标记生命周期残留窗口缺陷 | CharlesWang2030 | openjiuwen/core/context_engine, openjiuwen/harness, openjiuwen/agent_teams | openjiuwen/core/context_engine/context/cancellation.py, openjiuwen/core/context_engine/context/context.py, openjiuwen/core/context_engine/context/processor_state_recorder.py, openjiuwen/core/context_engine/context/context_state.py, openjiuwen/core/context_engine/context_engine/__init__.py, openjiuwen/harness/deep_agent.py, openjiuwen/agent_teams/harness/native_harness.py, openjiuwen/agent_teams/runtime/manager.py, openjiuwen/extensions/observability/runtime.py, tests/unit_tests/core/context_engine/test_cancellation_cause.py, tests/unit_tests/core/context_engine/test_context_compression_state_stream.py, tests/unit_tests/harness/test_deep_agent_abort_cancellation_cause.py, tests/unit_tests/agent_teams/runtime/test_manager.py, tests/unit_tests/agent_teams/harness/test_abort.py | 无独立 D：c4e52b7c 引入 cancellation cause 注册表属于常规缺陷修复，未归档独立设计；本次为对该提交检视发现的标记生命周期缺陷的后续修复，沿用注册表现有契约（TTL、非消费式 resolve、前缀继承）。 |

<a id="module-10"></a>

## harness/subagents

| ID / 文档 | 类型 | 标题 | 作者 | 模块 | 代码文件 | 关联 D |
|---|---|---|---|---|---|---|
| [B_20261102_1733_browser-pr-contract-review](harness/B_20261102_1733_browser-pr-contract-review.md) | B | Browser PR budget and exception contract corrections | ShowHsiang | openjiuwen/harness/subagents, openjiuwen/harness/tools/subagent, openjiuwen/harness/tools/browser_move/decision | openjiuwen/harness/subagents/browser_agent.py, openjiuwen/harness/tools/subagent/task_tool.py, tests/unit_tests/harness/test_deep_agent_terminal_stream.py, tests/unit_tests/harness/tools/browser_move/test_browser_fact_handoff.py, tests/unit_tests/harness/tools/browser_move/test_browser_shared_state_delivery.py, tests/unit_tests/harness/tools/browser_move/test_browser_local_intent_readers.py | 无独立 D：Local corrections to the existing explicit-budget and fact-retention contracts; no new architecture or feature. |

<a id="module-11"></a>

## harness/tools/browser_move/decision

| ID / 文档 | 类型 | 标题 | 作者 | 模块 | 代码文件 | 关联 D |
|---|---|---|---|---|---|---|
| [B_20261102_1733_browser-pr-contract-review](harness/B_20261102_1733_browser-pr-contract-review.md) | B | Browser PR budget and exception contract corrections | ShowHsiang | openjiuwen/harness/subagents, openjiuwen/harness/tools/subagent, openjiuwen/harness/tools/browser_move/decision | openjiuwen/harness/subagents/browser_agent.py, openjiuwen/harness/tools/subagent/task_tool.py, tests/unit_tests/harness/test_deep_agent_terminal_stream.py, tests/unit_tests/harness/tools/browser_move/test_browser_fact_handoff.py, tests/unit_tests/harness/tools/browser_move/test_browser_shared_state_delivery.py, tests/unit_tests/harness/tools/browser_move/test_browser_local_intent_readers.py | 无独立 D：Local corrections to the existing explicit-budget and fact-retention contracts; no new architecture or feature. |

<a id="module-12"></a>

## harness/tools/subagent

| ID / 文档 | 类型 | 标题 | 作者 | 模块 | 代码文件 | 关联 D |
|---|---|---|---|---|---|---|
| [B_20261102_1733_browser-pr-contract-review](harness/B_20261102_1733_browser-pr-contract-review.md) | B | Browser PR budget and exception contract corrections | ShowHsiang | openjiuwen/harness/subagents, openjiuwen/harness/tools/subagent, openjiuwen/harness/tools/browser_move/decision | openjiuwen/harness/subagents/browser_agent.py, openjiuwen/harness/tools/subagent/task_tool.py, tests/unit_tests/harness/test_deep_agent_terminal_stream.py, tests/unit_tests/harness/tools/browser_move/test_browser_fact_handoff.py, tests/unit_tests/harness/tools/browser_move/test_browser_shared_state_delivery.py, tests/unit_tests/harness/tools/browser_move/test_browser_local_intent_readers.py | 无独立 D：Local corrections to the existing explicit-budget and fact-retention contracts; no new architecture or feature. |

<a id="module-13"></a>

## symphony

| ID / 文档 | 类型 | 标题 | 作者 | 模块 | 代码文件 | 关联 D |
|---|---|---|---|---|---|---|
| [B_20261102_1507_symphony-build-method-strict-integer](symphony/B_20261102_1507_symphony-build-method-strict-integer.md) | B | Symphony 构建方式严格整数校验 | lin-xiaoyu | symphony | openjiuwen/symphony/config.py, tests/unit_tests/symphony/test_config.py | [D-20261031-1614-symphony-config-contract](../designs/active/D-20261031-1614-symphony-config-contract/design.md) |
| [B_20261110_1139_symphony-discovery-invalid-manifest](symphony/B_20261110_1139_symphony-discovery-invalid-manifest.md) | B | Symphony 隔离无效 Skill 清单 | lin-xiaoyu | openjiuwen/symphony | openjiuwen/symphony/inventory.py, openjiuwen/symphony/tree_engine.py, tests/unit_tests/symphony/test_runtime_config_integration.py | [D-20261109-1745-shared-capability-fingerprints](../designs/active/D-20261109-1745-shared-capability-fingerprints/design.md) |

<a id="module-14"></a>

## symphony/discovery

| ID / 文档 | 类型 | 标题 | 作者 | 模块 | 代码文件 | 关联 D |
|---|---|---|---|---|---|---|
| [B_20261110_1651_symphony-expert-batch-search](symphony/B_20261110_1651_symphony-expert-batch-search.md) | B | Symphony 专家目录支持批量查询 | lin-xiaoyu | openjiuwen/symphony/discovery | openjiuwen/symphony/discovery/directory_toolkit.py, tests/unit_tests/symphony/test_mixed_taxonomy.py | [D-20261110-1614-symphony-mixed-taxonomy](../designs/active/D-20261110-1614-symphony-mixed-taxonomy/design.md) |
