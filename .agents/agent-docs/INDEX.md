---
title: D/F/B/S 文档总索引
author: alan_cheng
author_email: chengshuo5@huawei.com
generated: true
---

# D/F/B/S 文档总索引

由 sync_documents.py 从文档 frontmatter 自动生成；请修改源文档后重新生成，不手工维护。

共 272 篇文档，按模块分组；跨模块文档在各相关分组展示，总数按文档去重。

## 模块导航

| 模块 | 文档数 |
|---|---|
| [agent_evolving](#module-1) | 1 |
| [agent_teams](#module-2) | 199 |
| [agent_teams/workflow](#module-3) | 15 |
| [extensions/observability](#module-4) | 2 |
| [harness](#module-5) | 54 |
| [未分类](#module-6) | 6 |

<a id="module-1"></a>

## agent_evolving

| ID / 文档 | 类型 | 标题 | 作者 | 模块 | 代码文件 | 关联 D |
|---|---|---|---|---|---|---|
| [SDD-0024](designs/proposals/SDD-0024-trajectory-projection-evolution-rl/design.md) | D | 轨迹投影：把 OTel 标准轨迹投影为自演进与在线 AgentRL 输入 | @alan_cheng | agent_evolving, harness | — | — |

<a id="module-2"></a>

## agent_teams

| ID / 文档 | 类型 | 标题 | 作者 | 模块 | 代码文件 | 关联 D |
|---|---|---|---|---|---|---|
| [SDD-0011](designs/archive/SDD-0011-evolvable-team-workspace/design.md) | D | 可演进 Team 工作区 | @xiajing | agent_teams | — | — |
| [SDD-0015](designs/proposals/SDD-0015-agent-team-scheduled-dispatch/design.md) | D | AgentTeam 调度模式：任务图驱动的低通信协同与验证闭环 | @alan_cheng | agent_teams | — | — |
| [SDD-0016](designs/archive/SDD-0016-third-party-agent-reliability/design.md) | D | 三方 Agent 可靠性设计 | @zhanglongmin | agent_teams | — | — |
| [SDD-0018](designs/archive/SDD-0018-swarmflow-team-runtime-binding/design.md) | D | Swarmflow 启停与 Team 生命周期联动（小飞机控灯 / 恢复裁决 / 冷启动续跑） | @chenchunzhou | agent_teams/workflow, agent_teams | — | — |
| [SDD-0019](designs/proposals/SDD-0019-team-model-group/design.md) | D | Team 模式模型组接入与成员模型分配 | @<huanghao> | agent_teams | — | — |
| [SDD-0022](designs/proposals/SDD-0022-trajectory-observability-framework/design.md) | D | 轨迹观测框架：基于 OTel GenAI 语义约定的 Harness 与 Team 轨迹采集和处理 | @alan_cheng | extensions/observability, harness, agent_teams | — | — |
| [SDD-0030](designs/proposals/SDD-0030-swarmflow-agent-group-expert-worker/design.md) | D | Swarmflow 专家 worker：agent_template 算子参数复用专家团成员 | @chenchunzhou | agent_teams/workflow, agent_teams | — | — |
| [agent_swarm_overview](designs/agent_teams/agent_swarm_overview.md) | D | Agent Swarm（Agent Team）特性文档 | alan_cheng | openjiuwen/agent_teams | — | — |
| [architecture_cn](designs/agent_teams/architecture_cn.md) | D | AgentTeam 架构文档 | alan_cheng | openjiuwen/agent_teams | — | — |
| [F_01_coordination-protocol-cleanup](features/agent_teams/F_01_coordination-protocol-cleanup.md) | F | Coordination Protocol Cleanup | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_02_member-attributed-streaming](features/agent_teams/F_02_member-attributed-streaming.md) | F | Member-Attributed Streaming | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_03_drop-factory-lifecycle-wrappers](features/agent_teams/F_03_drop-factory-lifecycle-wrappers.md) | F | Drop factory.py: lifecycle wrappers fold into runtime/ | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_04_hitt-human-in-the-team](features/agent_teams/F_04_hitt-human-in-the-team.md) | F | HITT — Human in the Team | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_05_lifecycle-finalize-relocation](features/agent_teams/F_05_lifecycle-finalize-relocation.md) | F | Lifecycle Finalize Relocation: kernel → runtime manager + warm-path removal | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_06_name-old-session-recover](features/agent_teams/F_06_name-old-session-recover.md) | F | run_agent_team* input contract collapse to `spec+new_session` / `name+old_session` | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_07_bridge-agent](features/agent_teams/F_07_bridge-agent.md) | F | Bridge Agent — 桥接外部独立 Agent | 待确认 | openjiuwen/agent_teams | — | 待核实 |
| [F_07_team-completion-events](features/agent_teams/F_07_team-completion-events.md) | F | Team Completion Events | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_08_default-team-mode-hybrid](features/agent_teams/F_08_default-team-mode-hybrid.md) | F | 默认 team_mode 从 predefined 改为 hybrid | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_09_team-stream-logging](features/agent_teams/F_09_team-stream-logging.md) | F | Team Stream Logging | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_100_harness-system-prompt-modes](features/agent_teams/F_100_harness-system-prompt-modes.md) | F | Codex / DSH 系统提示词保留与替换模式 | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_100_swarmflow-verify-fork-progress-events](features/agent_teams/F_100_swarmflow-verify-fork-progress-events.md) | F | Swarmflow verify/fork 结构化进度事件（VERIFY_STARTED/COMPLETED + fork 父标识） | hwxiajing | openjiuwen/agent_teams | — | 待核实 |
| [F_101_portable-skills-for-external-harnesses](features/agent_teams/F_101_portable-skills-for-external-harnesses.md) | F | 三方 Harness 的 Portable Skills | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_109_send-message-recipient-parameter-split](features/agent_teams/F_109_send-message-recipient-parameter-split.md) | F | `send_message` 收件参数分离 | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_10_temporary-leader-clean-team-stream-end](features/agent_teams/F_10_temporary-leader-clean-team-stream-end.md) | F | 临时团队 leader clean_team 后确定性结束自身 stream | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_110_genai-semconv-canonicalization](features/agent_teams/F_110_genai-semconv-canonicalization.md) | F | GenAI Semantic Conventions 全链路统一 | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_110_swarmflow-team-runtime-binding](features/agent_teams/F_110_swarmflow-team-runtime-binding.md) | F | Swarmflow stop_all 与 progress script_path（team runtime binding 的 agent-core 侧） | chenchunzhou | openjiuwen/agent_teams | — | 待核实 |
| [F_111_passive-human-member](features/agent_teams/F_111_passive-human-member.md) | F | F_111 — 被动人类成员与 tool call 透传协议 | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_112_group-conversation-and-delegation](features/agent_teams/F_112_group-conversation-and-delegation.md) | F | 群消息文件归档与成员通知 | openJiuwen-bot | openjiuwen/agent_teams | — | 待核实 |
| [F_112_harness-protocol-trajectory-observation](features/agent_teams/F_112_harness-protocol-trajectory-observation.md) | F | F_112 三方 harness 成员的轨迹观测：协议事件驱动 | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_112_swarmflow-live-worker-activity](features/agent_teams/F_112_swarmflow-live-worker-activity.md) | F | Swarmflow 单轮 worker 实时活动（agent_activity） | openJiuwen-bot | openjiuwen/agent_teams | — | 待核实 |
| [F_113_external-harness-builtin-model-selection](features/agent_teams/F_113_external-harness-builtin-model-selection.md) | F | F_113 三方 harness 成员的内置模型切换与 effort 调整 | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_113_swarmflow-worker-progressive-tool](features/agent_teams/F_113_swarmflow-worker-progressive-tool.md) | F | Swarmflow worker 渐进式工具暴露（team 级 opt-in） | chenchunzhou | openjiuwen/agent_teams | — | 待核实 |
| [F_114_swarmflow-thirdparty-agent](features/agent_teams/F_114_swarmflow-thirdparty-agent.md) | F | Swarmflow 三方 Agent 接入（`options.agent_type` → Claude Code / Codex） | chenchunzhou | openjiuwen/agent_teams | — | 待核实 |
| [F_114_team-tool-namespace-declaration](features/agent_teams/F_114_team-tool-namespace-declaration.md) | F | F_114 团队工具的命名空间声明：裸名归谁 | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_115_external-session-fork](features/agent_teams/F_115_external-session-fork.md) | F | 外部 swarmflow 会话的 fork（provider 原生分支） | chenchunzhou | openjiuwen/agent_teams | — | 待核实 |
| [F_115_swarmflow-model-hint-fail-fast](features/agent_teams/F_115_swarmflow-model-hint-fail-fast.md) | F | Swarmflow Model Hint Fail-Fast（options-bag 值治理） | wangcan103 | openjiuwen/agent_teams | — | 待核实 |
| [F_115_team-organization-develop-integration](features/agent_teams/F_115_team-organization-develop-integration.md) | F | Team Organization 合入 develop | ZezeChang | openjiuwen/agent_teams | — | 待核实 |
| [F_116_summary-submission-closeout](features/agent_teams/F_116_summary-submission-closeout.md) | F | Summary Submission Closeout | ZezeChang | openjiuwen/agent_teams | — | 待核实 |
| [F_116_swarmflow-thirdparty-model-source](features/agent_teams/F_116_swarmflow-thirdparty-model-source.md) | F | Swarmflow 三方节点的模型来源解析（`model` / `builtin_model` / `effort`，无回退） | chenchunzhou | openjiuwen/agent_teams | — | 待核实 |
| [F_117_third-party-harness-provider-members](features/agent_teams/F_117_third-party-harness-provider-members.md) | F | F_117 三方 harness provider 作为团队外部成员 | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_118_swarmflow-agent-template-expert-worker](features/agent_teams/F_118_swarmflow-agent-template-expert-worker.md) | F | Swarmflow 专家 worker：`agent_template` 复用专家团成员 | chenchunzhou | openjiuwen/agent_teams | — | 待核实 |
| [F_118_task-scoped-member-lifetime](features/agent_teams/F_118_task-scoped-member-lifetime.md) | F | 调度模式：任务内联成员与成员退场策略（短生命周期成员） | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_119_scheduled-completion-handoff-via-note](features/agent_teams/F_119_scheduled-completion-handoff-via-note.md) | F | 调度模式完成交接改走任务行：成员不再汇报完成，完成说明拆为摘要与详情 | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_11_leader-member-status-tracking](features/agent_teams/F_11_leader-member-status-tracking.md) | F | Leader Member Status Tracking | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_120_autonomous-completion-summary-and-note](features/agent_teams/F_120_autonomous-completion-summary-and-note.md) | F | 自主模式接入完成摘要与详情，两种模式统一取消完成汇报 | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_121_autonomous-leader-quiet-board-events](features/agent_teams/F_121_autonomous-leader-quiet-board-events.md) | F | 自主模式 leader 只为需要它决策的看板事件醒来 | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_122_framework-bootstrapped-generic-team](features/agent_teams/F_122_framework-bootstrapped-generic-team.md) | F | 调度模式：会话启动即由框架建队（通用团队，任务驱动组员） | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_123_team-tools-direct-under-progressive-exposure](features/agent_teams/F_123_team-tools-direct-under-progressive-exposure.md) | F | 团队协作工具在渐进式工具暴露下保持直达 | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_124_monitor-message-template-meta](features/agent_teams/F_124_monitor-message-template-meta.md) | F | Monitor 透出框架模板消息的 meta | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_125_team-expert-catalog-and-spawn](features/agent_teams/F_125_team-expert-catalog-and-spawn.md) | F | Team 专家目录与按模板拉起专家成员 | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_126_scheduled-member-peer-messaging](features/agent_teams/F_126_scheduled-member-peer-messaging.md) | F | 调度模式：可配置的成员间通信 | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_127_merge-spawn-external-cli-into-spawn-teammate](features/agent_teams/F_127_merge-spawn-external-cli-into-spawn-teammate.md) | F | `spawn_external_cli` 并入 `spawn_teammate` | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_128_scheduled-member-subtasks](features/agent_teams/F_128_scheduled-member-subtasks.md) | F | 调度模式：成员拆分子任务（父任务挂起 / 恢复） | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_129_task-revision-and-staleness](features/agent_teams/F_129_task-revision-and-staleness.md) | F | 任务版本与过期感知：任务被修改后，执行者一定能知道 | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_12_multicast-full-roster-rejection](features/agent_teams/F_12_multicast-full-roster-rejection.md) | F | 禁止覆盖全员的组播，强制改用广播 | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_13_human-agent-send-message](features/agent_teams/F_13_human-agent-send-message.md) | F | Human Agent — User-Driven `send_message` Relay | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_14_human-agent-team-event-rendering](features/agent_teams/F_14_human-agent-team-event-rendering.md) | F | Human Agent — Role-Aware Team Event Rendering | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_15_human-agent-role-persistence](features/agent_teams/F_15_human-agent-role-persistence.md) | F | Human Agent — Persist Role on Member Row | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_16_by-model-name-allocator-list-serialisation](features/agent_teams/F_16_by-model-name-allocator-list-serialisation.md) | F | ByModelNameAllocator List Serialisation | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_17_spawn-member-visibility-contract](features/agent_teams/F_17_spawn-member-visibility-contract.md) | F | spawn_member — Public / Private Visibility Contract in Tool Description | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_18_hide-human-agent-role-from-teammate](features/agent_teams/F_18_hide-human-agent-role-from-teammate.md) | F | Hide Human-Agent Role from Teammate Prompts (Switchable) | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_19_monitor-hide-dm-switch](features/agent_teams/F_19_monitor-hide-dm-switch.md) | F | Monitor `hide_dm` Switch | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_20261022_2319_inline-member-worktree-isolation](features/F_20261022_2319_inline-member-worktree-isolation.md) | F | 调度模式内联成员支持 worktree 隔离 | alan_cheng | openjiuwen/agent_teams | openjiuwen/agent_teams/tools/tool_task.py, openjiuwen/agent_teams/tools/locales/cn.py, openjiuwen/agent_teams/tools/locales/en.py, openjiuwen/agent_teams/tools/locales/descs/cn/fragments/create_task_inline_member.md, openjiuwen/agent_teams/tools/locales/descs/en/fragments/create_task_inline_member.md | 无独立 D：对 F_118 内联成员参数遗漏的小范围补齐，复用既有 `isolation` 契约，无独立设计 |
| [F_20_human-agent-mailbox-unread-flip](features/agent_teams/F_20_human-agent-mailbox-unread-flip.md) | F | Human-Agent Mailbox: Flip Auto-Read Off | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_21_external-agent-access](features/agent_teams/F_21_external-agent-access.md) | F | External Agent Access: skill+cli / mcp Wrappers | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_21_team-completion-auto-pause](features/agent_teams/F_21_team-completion-auto-pause.md) | F | Team Completion Auto-Pause | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_21_team-plan](features/agent_teams/F_21_team-plan.md) | F | Team.plan 团队计划模式 | weichenhao | openjiuwen/agent_teams | — | 待核实 |
| [F_22_external-cli-spawn-member-and-mcp-injection](features/agent_teams/F_22_external-cli-spawn-member-and-mcp-injection.md) | F | External-CLI as a spawn_member Role + Static Spec Config + MCP Auto-Injection | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_23_strict-recipient-match-fold-unknown-mentions](features/agent_teams/F_23_strict-recipient-match-fold-unknown-mentions.md) | F | Strict ``@<member>`` Recipient Matching + Fold Unknown Mentions Back to Text | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_24_agent-time-awareness](features/agent_teams/F_24_agent-time-awareness.md) | F | Agent Time Awareness — Inject Absolute + Relative Time into Agent-Facing Rendering | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_25_external-cli-hardening-and-gemini](features/agent_teams/F_25_external-cli-hardening-and-gemini.md) | F | External CLI Member Hardening, Team-Rail System Prompt, Gemini, and Live Output Surfacing | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_26_external-access-operator-member-scopes](features/agent_teams/F_26_external-access-operator-member-scopes.md) | F | External Access Splits Into operator / member Scopes; member Reuses Real TeamTools | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_26_spawn-tools-split-by-role](features/agent_teams/F_26_spawn-tools-split-by-role.md) | F | Split `spawn_member` into Four Role-Specific Tools | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_27_native-harness-task-loop](features/agent_teams/F_27_native-harness-task-loop.md) | F | F_27 NativeHarness over the full DeepAgent task loop | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_27_swarmflow-workflow-orchestration](features/agent_teams/F_27_swarmflow-workflow-orchestration.md) | F | Swarmflow 多 Agent 工作流编排 | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_28_native-harness-team-adoption](features/agent_teams/F_28_native-harness-team-adoption.md) | F | F_28 NativeHarness 收编 TeamHarness / StreamController | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_29_reliability-framework](features/agent_teams/F_29_reliability-framework.md) | F | F_29 Reliability framework (member health monitoring + remediation) | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_30_reliability-self-monitor-and-result-aware-repeat](features/agent_teams/F_30_reliability-self-monitor-and-result-aware-repeat.md) | F | F_30 Reliability follow-ups: leader self-monitor + result-aware repeat | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_31_swarmflow-per-call-model-routing](features/agent_teams/F_31_swarmflow-per-call-model-routing.md) | F | Swarmflow Per-Call Model 路由 | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_32_declarative-harness-assembly](features/agent_teams/F_32_declarative-harness-assembly.md) | F | DeepAgent 装配体系归一到纯声明式 manifest | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_33_spawn-first-start-message-injection](features/agent_teams/F_33_spawn-first-start-message-injection.md) | F | spawn 只在首启注入初始消息，recover/resume 不再自动 send | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_34_swarmflow-import-alias-simplification](features/agent_teams/F_34_swarmflow-import-alias-simplification.md) | F | Swarmflow Import 别名机制简化 | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_35_native-harness-async-tool-framework](features/agent_teams/F_35_native-harness-async-tool-framework.md) | F | NativeHarness 异步工具框架（起步版）+ swarmflow 接入 | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_36_swarmflow-worker-teamharness-and-run-once](features/agent_teams/F_36_swarmflow-worker-teamharness-and-run-once.md) | F | F_36 swarmflow worker 改用 TeamHarness + TeamHarness.run_once 非流式接口 | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_37_observability-otel-trace](features/agent_teams/F_37_observability-otel-trace.md) | F | F_37: Observability OTel Trace | hwxiajing | openjiuwen/agent_teams | — | 待核实 |
| [F_37_swarmflow-stateful-sessions-and-human](features/agent_teams/F_37_swarmflow-stateful-sessions-and-human.md) | F | Swarmflow 有状态会话（agent_session / human_session）与 human 参与点 | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_38_swarmflow-journal-persistence](features/agent_teams/F_38_swarmflow-journal-persistence.md) | F | Swarmflow Resume Journal 落盘接线与 sessions/workflows 目录布局 | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_38_team-permission-system](features/agent_teams/F_38_team-permission-system.md) | F | Team Permission System — leader-mediated ASK resolution | zhanglongmin | openjiuwen/agent_teams | — | 待核实 |
| [F_38_team-teammate-worktree-isolation-agenttool](features/agent_teams/F_38_team-teammate-worktree-isolation-agenttool.md) | F | Team teammate worktree 隔离对齐 AgentTool isolation | openJiuwen-bot | openjiuwen/agent_teams | — | 待核实 |
| [F_39_swarmflow-agent-worktree-isolation](features/agent_teams/F_39_swarmflow-agent-worktree-isolation.md) | F | SwarmFlow agent 支持 worktree isolation | openJiuwen-bot | openjiuwen/agent_teams | — | 待核实 |
| [F_39_swarmflow-e2e-hardening](features/agent_teams/F_39_swarmflow-e2e-hardening.md) | F | Swarmflow 端到端验证与四项健壮性修复 | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_40_swarmflow-journal-wal-and-program-order](features/agent_teams/F_40_swarmflow-journal-wal-and-program-order.md) | F | Swarmflow Journal:program-order 落盘 + 崩溃durable WAL + 原子/异步 I/O | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_41_async-tool-control-and-spill](features/agent_teams/F_41_async-tool-control-and-spill.md) | F | 异步工具框架路线一：统一 ID + 管控工具 + 磁盘溢写（DAB） | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_42_swarmflow-tool-claude-code-alignment](features/agent_teams/F_42_swarmflow-tool-claude-code-alignment.md) | F | Swarmflow 工具对齐 Claude Code:提示词集中化 + 接口扩展 | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_43_swarmflow-pause-resume](features/agent_teams/F_43_swarmflow-pause-resume.md) | F | Swarmflow 真实 pause/resume 中断恢复 | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_44_swarmflow-worker-not-teammate-no-db](features/agent_teams/F_44_swarmflow-worker-not-teammate-no-db.md) | F | Swarmflow Worker 不是 Teammate：移除 DB roster 写入与 team_backend 注入 | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_45_tiny-agent](features/agent_teams/F_45_tiny-agent.md) | F | Tiny Agent：随时唤起的极简 native harness | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_46_info-layering-attachment-and-inbound-xml](features/agent_teams/F_46_info-layering-attachment-and-inbound-xml.md) | F | 信息分层优化：动态 section 改挂 attachment + 入站消息 XML 化 | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_47_swarmflow-concurrency-governor](features/agent_teams/F_47_swarmflow-concurrency-governor.md) | F | Swarmflow 三层并发治理（ConcurrencyGovernor） | chenchunzhou | openjiuwen/agent_teams | — | 待核实 |
| [F_48_swarmflow-inline-script-execution](features/agent_teams/F_48_swarmflow-inline-script-execution.md) | F | Swarmflow 内联 script 执行 | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_49_member-desc-prompt-split-and-spec-unification](features/agent_teams/F_49_member-desc-prompt-split-and-spec-unification.md) | F | 成员 desc/prompt 二分与 spec 归一 | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_50_hitt-contract-roster-split-and-finish-md-externalization](features/agent_teams/F_50_hitt-contract-roster-split-and-finish-md-externalization.md) | F | HITT 契约/名册按生命周期拆分 + 补完 HITT/Bridge 外置化 | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_51_external-cli-inbound-xml-and-tag-notice-relocation](features/agent_teams/F_51_external-cli-inbound-xml-and-tag-notice-relocation.md) | F | 外部 CLI 入站渲染统一为 XML + tag 说明就近归属 build_team_static_sections | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_52_unify-member-roster-and-static-sections](features/agent_teams/F_52_unify-member-roster-and-static-sections.md) | F | 统一成员名册 + 团队 section 全静态化 | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_53_self-only-stale-nudge-minimal-and-append](features/agent_teams/F_53_self-only-stale-nudge-minimal-and-append.md) | F | Self-Only Stale-Task Nudge：收敛渲染、精简载荷、接续投递 | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_54_reassign-via-revoke-event-and-one-active-claim](features/agent_teams/F_54_reassign-via-revoke-event-and-one-active-claim.md) | F | 基于 TASK_REVOKED 事件的任务改派 + 一成员一活跃认领 | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_55_create-task-atomic-graph-and-depended-by-contract](features/agent_teams/F_55_create-task-atomic-graph-and-depended-by-contract.md) | F | create_task 原子图创建 + depended_by 仅指已有任务的契约 | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_56_task-level-signals-replace-member-cancel](features/agent_teams/F_56_task-level-signals-replace-member-cancel.md) | F | 任务级信号取代 member 级 cancel（改派原子化 + 取消/编辑轻量化） | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_57_tool-variants-and-templated-descriptions](features/agent_teams/F_57_tool-variants-and-templated-descriptions.md) | F | Tool Variants + Templated Descriptions（dispatch_mode 进入工具装配） | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_59_condition-named-task-state-machine-with-verify-gate](features/agent_teams/F_59_condition-named-task-state-machine-with-verify-gate.md) | F | 条件命名任务状态机 + 验证闸（设计稿，待评审） | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_60_native-harness-pause-abort-resume](features/agent_teams/F_60_native-harness-pause-abort-resume.md) | F | F_60 NativeHarness pause / abort / resume：iteration 级语义与原地续跑 | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_61_cold-resume-across-stop-start](features/agent_teams/F_61_cold-resume-across-stop-start.md) | F | F_61 pause → stop → start 的冷恢复续跑 | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_62_scheduled-dispatch-runtime-and-review-voting](features/agent_teams/F_62_scheduled-dispatch-runtime-and-review-voting.md) | F | 调度模式 runtime + 评审投票 | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_63_scheduler-message-templating-and-delivery-render](features/agent_teams/F_63_scheduler-message-templating-and-delivery-render.md) | F | 调度器消息模板 + 投递时渲染 | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_64_message-channel-policy-and-content-size-guard](features/agent_teams/F_64_message-channel-policy-and-content-size-guard.md) | F | 消息通道纪律：按内容形态分流 + `content` 硬上限 | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_65_runtime-idle-clock-stall-nudge](features/agent_teams/F_65_runtime-idle-clock-stall-nudge.md) | F | 运行时 idle 计时驱动的停滞唤醒（autonomous） | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_66_swarmflow-real-token-budget-enforcement](features/agent_teams/F_66_swarmflow-real-token-budget-enforcement.md) | F | Swarmflow token budget：真实用量 + 硬约束落地 | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_67_intelli-router-allocation-strategy](features/agent_teams/F_67_intelli-router-allocation-strategy.md) | F | IntelliRouter Allocation Strategy | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_68_member-identity-out-of-prompt-prefix](features/agent_teams/F_68_member-identity-out-of-prompt-prefix.md) | F | per-member 内容移出系统提示词前缀（team_identity attachment） | 待确认 | openjiuwen/agent_teams | — | 待核实 |
| [F_69_cwd-workspace-project-root-separation](features/agent_teams/F_69_cwd-workspace-project-root-separation.md) | F | cwd / workspace / project_root 三层解绑 | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_69_stale-sweep-covers-assigned-unstarted-tasks](features/agent_teams/F_69_stale-sweep-covers-assigned-unstarted-tasks.md) | F | 已指派未开工的任务：自催扫描面覆盖 + 首启板巡视 | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_70_team-context-into-history](features/agent_teams/F_70_team-context-into-history.md) | F | 团队上下文改走对话历史：数据产生时插入，只插不删，基线持久化 | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_71_input-batch-hook-and-superseded-board-drop](features/agent_teams/F_71_input-batch-hook-and-superseded-board-drop.md) | F | F_71 输入批次钩子与被覆盖看板的剔除 | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_72_nested-team-note-inside-annotated-block](features/agent_teams/F_72_nested-team-note-inside-annotated-block.md) | F | 入站 XML 层级化：`<team-note>` 嵌进它所修饰的块 | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_73_avatar-controller-channel-separation](features/agent_teams/F_73_avatar-controller-channel-separation.md) | F | Avatar 的控制者通道与团队 `user` 分离 | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_73_reviewer-feedback-skill-evolution-boundaries](features/agent_teams/F_73_reviewer-feedback-skill-evolution-boundaries.md) | F | Reviewer Feedback 驱动的 Skill 演进边界 | 2401_87256159 | openjiuwen/agent_teams | — | 待核实 |
| [F_73_reviewer-role-split-and-flexible-verification](features/agent_teams/F_73_reviewer-role-split-and-flexible-verification.md) | F | 判断者角色拆分与弹性验证 | openJiuwen-bot | openjiuwen/agent_teams | — | 待核实 |
| [F_74_leader-member-activity-and-team-idle](features/agent_teams/F_74_leader-member-activity-and-team-idle.md) | F | F_74 Leader 侧成员活动登记与 team-idle 信号 | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_75_fork-context-inheritance](features/agent_teams/F_75_fork-context-inheritance.md) | F | 团队 Fork 上下文继承 | dunphy1 | openjiuwen/agent_teams | — | 待核实 |
| [F_76_fork-checkpoint-persistence](features/agent_teams/F_76_fork-checkpoint-persistence.md) | F | Checkpoint 持久化 | dunphy1 | openjiuwen/agent_teams | — | 待核实 |
| [F_76_leader-progressive-policy-disclosure](features/agent_teams/F_76_leader-progressive-policy-disclosure.md) | F | Leader 协同准则的渐进式披露 | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_77_team-idle-requires-a-settled-task-board](features/agent_teams/F_77_team-idle-requires-a-settled-task-board.md) | F | F_77 team.idle 追加任务板条件 | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_78_steering-batch-quota-hook](features/agent_teams/F_78_steering-batch-quota-hook.md) | F | F_78 steering 消费配额钩子：每次 drain 取多少由 rail 现场决定 | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_79_team-scoped-skill-library-and-visibility](features/agent_teams/F_79_team-scoped-skill-library-and-visibility.md) | F | Team 场景 Skill 单点化：一份库 + 可见性声明 | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_80_fork-identity-conversion](features/agent_teams/F_80_fork-identity-conversion.md) | F | Fork 身份转换：继承上下文后显式声明当前身份 | sodawlh | openjiuwen/agent_teams | — | 待核实 |
| [F_80_task-loop-timeout-contract](features/agent_teams/F_80_task-loop-timeout-contract.md) | F | F_80 Task-loop timeout contract | ldstyle8 | openjiuwen/agent_teams | — | 待核实 |
| [F_81_agent-template-snapshot-loading](features/agent_teams/F_81_agent-template-snapshot-loading.md) | F | F_81 AgentTemplate 快照加载 | openJiuwen-bot | openjiuwen/agent_teams | — | 待核实 |
| [F_81_disband-requires-explicit-intent](features/agent_teams/F_81_disband-requires-explicit-intent.md) | F | 解散团队只认显式意图 | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_81_session-file-data-store](features/agent_teams/F_81_session-file-data-store.md) | F | Session 文件数据存储（message/task 正文外置） | hwxiajing | openjiuwen/agent_teams | — | 待核实 |
| [F_81_swarmflow-session-fork](features/agent_teams/F_81_swarmflow-session-fork.md) | F | SwarmFlow 有状态会话 fork（`agent_session` 上下文继承） | sodawlh | openjiuwen/agent_teams | — | 待核实 |
| [F_82_evolvable-workspace](features/agent_teams/F_82_evolvable-workspace.md) | F | 可演进 workspace（A 提示词 / B DB 值 / C tool 描述 三类文本演进） | hwxiajing | openjiuwen/agent_teams | — | 待核实 |
| [F_82_reassign-before-a-task-starts](features/agent_teams/F_82_reassign-before-a-task-starts.md) | F | 未开工的任务也能改派 | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_83_agent-tier-rail-split](features/agent_teams/F_83_agent-tier-rail-split.md) | F | agent 层 span 与 team 增量拆成两个独立 rail | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_83_session-ddl-under-write-lock](features/agent_teams/F_83_session-ddl-under-write-lock.md) | F | Session 动态表 DDL 收编进 DbSessions 写锁 | hwxiajing | openjiuwen/agent_teams | — | 待核实 |
| [F_84_task-creation-starts-members](features/agent_teams/F_84_task-creation-starts-members.md) | F | 创建任务即拉起成员，round-idle 再对账一次 | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_85_block-c-member-directory-linker](features/agent_teams/F_85_block-c-member-directory-linker.md) | F | Block C：成员目录链接器（member-directory linker） | hwxiajing | openjiuwen/agent_teams | — | 待核实 |
| [F_86_swarmflow-verify-primitive](features/agent_teams/F_86_swarmflow-verify-primitive.md) | F | Swarmflow 验证原语 verify()：把任务验收机制抽象为可脚本调用的原语 | sodawlh | openjiuwen/agent_teams | — | 待核实 |
| [F_87_swarmflow-run-id-isolation-and-dual-budget](features/agent_teams/F_87_swarmflow-run-id-isolation-and-dual-budget.md) | F | Swarmflow run_id 隔离、缓存重建与双层 Budget | chenchunzhou | openjiuwen/agent_teams | — | 待核实 |
| [F_88_swarmflow-relaunch-kind-and-seal-pause-semantics](features/agent_teams/F_88_swarmflow-relaunch-kind-and-seal-pause-semantics.md) | F | Swarmflow relaunch_kind 信号与 seal/pause 终态语义 | chenchunzhou | openjiuwen/agent_teams | — | 待核实 |
| [F_89_team-shared-deliverables-outputs](features/agent_teams/F_89_team-shared-deliverables-outputs.md) | F | F_89：团队共享产物目录（outputs）与 .team 路由废止 | hwxiajing | openjiuwen/agent_teams | — | 待核实 |
| [F_94_external-harness-protocol](features/agent_teams/F_94_external-harness-protocol.md) | F | Third-party Agent Harness Protocol | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_95_dsh-external-harness-adapter](features/agent_teams/F_95_dsh-external-harness-adapter.md) | F | DSH External Harness Adapter | 待确认 | openjiuwen/agent_teams | — | 待核实 |
| [F_96_protocol-harness-providers-and-member-migration](features/agent_teams/F_96_protocol-harness-providers-and-member-migration.md) | F | Protocol Harness Providers 与 Claude Code / Codex 成员迁移 | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_96_swarmflow-worker-name-stability-and-isolation-sig](features/agent_teams/F_96_swarmflow-worker-name-stability-and-isolation-sig.md) | F | Swarmflow worker 成员名稳定化与 isolation 入签名 | hwxiajing | openjiuwen/agent_teams | — | 待核实 |
| [F_97_native-harness-protocol-adapter](features/agent_teams/F_97_native-harness-protocol-adapter.md) | F | NativeHarness 公共协议适配 | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_98_native-checkpoint-and-endpoint-recovery](features/agent_teams/F_98_native-checkpoint-and-endpoint-recovery.md) | F | NativeHarness 冷恢复与原端点重连恢复 | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [F_99_dsh-composition-capabilities](features/agent_teams/F_99_dsh-composition-capabilities.md) | F | DSH 启动能力适配与 SDK 升级 | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [B_01_team-root-adopts-foreign-session-root](bugfix/agent_teams/B_01_team-root-adopts-foreign-session-root.md) | B | Team 根 span 误认并发会话的 root，导致整条团队轨迹丢失 | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [B_02_native-harness-interrupt-checkpoint-before-output](bugfix/agent_teams/B_02_native-harness-interrupt-checkpoint-before-output.md) | B | NativeHarness 中断结果输出前未持久化恢复状态 | wangcan103 | openjiuwen/agent_teams | — | 待核实 |
| [B_03_task-updated-event-stale-payload](bugfix/agent_teams/B_03_task-updated-event-stale-payload.md) | B | TASK_UPDATED 事件载荷取自写前快照且不携带任务状态 | wangcan103 | openjiuwen/agent_teams | — | 待核实 |
| [B_04_autonomous-create-task-inline-member-leak](bugfix/agent_teams/B_04_autonomous-create-task-inline-member-leak.md) | B | autonomous 团队的 leader 照着共享文案向 create_task 传 `member`，字段被静默丢弃、任务无主落板 | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [B_04_inbound-relative-time-and-mailbox-order](bugfix/agent_teams/B_04_inbound-relative-time-and-mailbox-order.md) | B | 被唤醒成员倒序读邮箱，入站消息的相对时间写死在历史里 | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [B_05_paused-team-rebuilt-on-every-request](bugfix/agent_teams/B_05_paused-team-rebuilt-on-every-request.md) | B | 同一模型选择被判成"换模型"，paused 团队每次追问都被拆掉重建 | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [B_06_scheduled-reassign-active-task-handoff-gap](bugfix/agent_teams/B_06_scheduled-reassign-active-task-handoff-gap.md) | B | 调度模式：改派一个已开工的任务给尚未启动的成员后，新 owner 永远收不到交接 | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [B_07_team-interrupt-resume-entry-and-leader-ready](bugfix/agent_teams/B_07_team-interrupt-resume-entry-and-leader-ready.md) | B | 团队中断关闭流后无恢复入口：裸文本恢复静默失效，recovery 流伪造 "None" 轮次 | wangcan103 | openjiuwen/agent_teams | — | 待核实 |
| [B_08_single-branch-worktree-startup-failure](bugfix/agent_teams/B_08_single-branch-worktree-startup-failure.md) | B | 单分支仓库的 worktree 基线失效导致调查成员未启动 | MengHan18 | openjiuwen/agent_teams | — | 待核实 |
| [B_09_all-done-prompt-stalls-remaining-work](bugfix/agent_teams/B_09_all-done-prompt-stalls-remaining-work.md) | B | 看板清空的收尾提示让 leader 停止建任务，且之后再清空不再提示 | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [B_10_task-assigned-to-retired-member-stalls](bugfix/agent_teams/B_10_task-assigned-to-retired-member-stalls.md) | B | 任务派给已退役的任务级成员后整夜无人执行 | alan_cheng | openjiuwen/agent_teams | — | 待核实 |
| [S_01_public-api-and-spec-flow](specs/agent_teams/S_01_public-api-and-spec-flow.md) | S | S_01 公开 API 表面与 Spec 流 | alan_cheng | openjiuwen/agent_teams | — | — |
| [S_02_team-agent-architecture](specs/agent_teams/S_02_team-agent-architecture.md) | S | TeamAgent 架构规约 | alan_cheng | openjiuwen/agent_teams | — | — |
| [S_03_coordination-protocol](specs/agent_teams/S_03_coordination-protocol.md) | S | Coordination Protocol | alan_cheng | openjiuwen/agent_teams | — | — |
| [S_04_session-and-recovery](specs/agent_teams/S_04_session-and-recovery.md) | S | Session and Recovery | alan_cheng | openjiuwen/agent_teams | — | — |
| [S_05_member-spawn-and-stream](specs/agent_teams/S_05_member-spawn-and-stream.md) | S | Member Spawn 与 Stream 控制 | alan_cheng | openjiuwen/agent_teams | — | — |
| [S_06_runtime-pool-dispatch](specs/agent_teams/S_06_runtime-pool-dispatch.md) | S | Runtime Pool & Dispatch | alan_cheng | openjiuwen/agent_teams | — | — |
| [S_07_interaction-views-and-hitt](specs/agent_teams/S_07_interaction-views-and-hitt.md) | S | Interaction Views and HITT Runtime Surface | alan_cheng | openjiuwen/agent_teams | — | — |
| [S_08_team-tools-contract](specs/agent_teams/S_08_team-tools-contract.md) | S | Team Tools Contract | alan_cheng | openjiuwen/agent_teams | — | — |
| [S_09_prompts-and-rails](specs/agent_teams/S_09_prompts-and-rails.md) | S | Prompts 与 Team Rails | alan_cheng | openjiuwen/agent_teams | — | — |
| [S_10_messager-transport](specs/agent_teams/S_10_messager-transport.md) | S | S_10 Messager 传输层与注册表 | alan_cheng | openjiuwen/agent_teams | — | — |
| [S_11_models-pool-and-allocation](specs/agent_teams/S_11_models-pool-and-allocation.md) | S | Models Pool and Allocation | alan_cheng | openjiuwen/agent_teams | — | — |
| [S_12_schema-data-models](specs/agent_teams/S_12_schema-data-models.md) | S | Schema Data Models | alan_cheng | openjiuwen/agent_teams | — | — |
| [S_13_team-workspace](specs/agent_teams/S_13_team-workspace.md) | S | Team Workspace | alan_cheng | openjiuwen/agent_teams | — | — |
| [S_14_monitor-and-observability](specs/agent_teams/S_14_monitor-and-observability.md) | S | Monitor and Observability | alan_cheng | openjiuwen/agent_teams | — | — |
| [S_15_cli-tui](specs/agent_teams/S_15_cli-tui.md) | S | Agent Teams CLI / TUI Spec | alan_cheng | openjiuwen/agent_teams | — | — |
| [S_16_top-level-utilities](specs/agent_teams/S_16_top-level-utilities.md) | S | Top-level Utilities | alan_cheng | openjiuwen/agent_teams | — | — |
| [S_17_team-plan](specs/agent_teams/S_17_team-plan.md) | S | Team.plan 运行时规约 | weichenhao | openjiuwen/agent_teams | — | — |
| [S_18_harness-interaction-contract](specs/agent_teams/S_18_harness-interaction-contract.md) | S | S_18 Harness 交互契约（HarnessProtocol / MemberRuntime） | alan_cheng | openjiuwen/agent_teams | — | — |
| [S_18_swarmflow-engine-and-worker](specs/agent_teams/S_18_swarmflow-engine-and-worker.md) | S | Swarmflow 引擎与 Worker 运行时规约 | alan_cheng | openjiuwen/agent_teams | — | — |
| [S_19_reliability-framework](specs/agent_teams/S_19_reliability-framework.md) | S | S_19 Reliability framework | alan_cheng | openjiuwen/agent_teams | — | — |
| [S_20_native-harness-async-tool-framework](specs/agent_teams/S_20_native-harness-async-tool-framework.md) | S | NativeHarness 异步工具框架运行时规约 | alan_cheng | openjiuwen/agent_teams | — | — |
| [S_21_swarmflow-concurrency-governor](specs/agent_teams/S_21_swarmflow-concurrency-governor.md) | S | Swarmflow 并发治理运行时规约 | chenchunzhou | openjiuwen/agent_teams | — | — |
| [S_22_scheduling-runtime](specs/agent_teams/S_22_scheduling-runtime.md) | S | Scheduling Runtime（调度模式运行时） | alan_cheng | openjiuwen/agent_teams | — | — |
| [S_23_agent-template-snapshot-loading](specs/agent_teams/S_23_agent-template-snapshot-loading.md) | S | S_23 AgentTemplate 快照加载 | openJiuwen-bot | openjiuwen/agent_teams | — | — |
| [S_23_session-file-data-store](specs/agent_teams/S_23_session-file-data-store.md) | S | Session 文件数据存储（message/task 正文外置） | hwxiajing | openjiuwen/agent_teams | — | — |
| [S_24_evolvable-workspace](specs/agent_teams/S_24_evolvable-workspace.md) | S | 可演进 workspace（A/B/C 三类文本演进机制） | hwxiajing | openjiuwen/agent_teams | — | — |
| [S_25_swarmflow-verify](specs/agent_teams/S_25_swarmflow-verify.md) | S | Swarmflow verify 原语规约 | sodawlh | openjiuwen/agent_teams | — | — |
| [S_26_team-shared-deliverables](specs/agent_teams/S_26_team-shared-deliverables.md) | S | 团队共享产物目录（Team Shared Deliverables） | hwxiajing | openjiuwen/agent_teams | — | — |
| [S_27_external-harness-member-runtime](specs/agent_teams/S_27_external-harness-member-runtime.md) | S | S_27 External Harness Member Runtime | alan_cheng | openjiuwen/agent_teams | — | — |
| [S_28_group-conversation-and-delegation](specs/agent_teams/S_28_group-conversation-and-delegation.md) | S | S_28 群聊输入与文件归档 | openJiuwen-bot | openjiuwen/agent_teams | — | — |
| [S_29_team-organization](specs/agent_teams/S_29_team-organization.md) | S | Team Organization | ZezeChang | openjiuwen/agent_teams | — | — |

<a id="module-3"></a>

## agent_teams/workflow

| ID / 文档 | 类型 | 标题 | 作者 | 模块 | 代码文件 | 关联 D |
|---|---|---|---|---|---|---|
| [SDD-0002](designs/archive/SDD-0002-swarmflow-stateful-sessions-and-human/design.md) | D | SwarmFlow 有状态会话与人机交互编排 | @alan_cheng | agent_teams/workflow | — | — |
| [SDD-0003](designs/archive/SDD-0003-swarmflow-interruption-recovery-and-durable-state/design.md) | D | SwarmFlow 中断恢复与崩溃可恢复状态持久化 | @alan_cheng | agent_teams/workflow | — | — |
| [SDD-0004](designs/archive/SDD-0004-swarmflow-nested-workflow-and-hardening/design.md) | D | SwarmFlow 嵌套工作流调用与执行加固 | @alan_cheng | agent_teams/workflow | — | — |
| [SDD-0005](designs/archive/SDD-0005-swarmflow-script-enhancement-and-cc-alignment/design.md) | D | SwarmFlow 脚本定义增强与 Claude Code 工具对齐 | @alan_cheng | agent_teams/workflow | — | — |
| [SDD-0007](designs/archive/SDD-0007-swarmflow-worker-decouple-from-team-db/design.md) | D | SwarmFlow Worker 去团队成员化与会话存储解耦 | @alan_cheng | agent_teams/workflow | — | — |
| [SDD-0008](designs/archive/SDD-0008-swarmflow-concurrency-governor/design.md) | D | Swarmflow 并发治理模块（ConcurrencyGovernor） | @chenchunzhou | agent_teams/workflow | — | — |
| [SDD-0012](designs/proposals/SDD-0012-swarmflow-dual-layer-budget/design.md) | D | Swarmflow 双层 Budget（workflow 级 + session 级）撞顶逻辑 | @wangcan103 | agent_teams/workflow | — | — |
| [SDD-0013](designs/archive/SDD-0013-swarmflow-runtime-control-intents/design.md) | D | Swarmflow 运行态控制意图（Early-Return / Pause-Resume / Stop） | @chenchunzhou | agent_teams/workflow | — | — |
| [SDD-0014](designs/active/SDD-0014-swarmflow-session-fork/design.md) | D | SwarmFlow 有状态会话 fork（agent_session 上下文继承） | @sodawlh | agent_teams/workflow | — | — |
| [SDD-0017](designs/proposals/SDD-0017-swarmflow-verify-primitive/design.md) | D | Swarmflow 验证原语 verify()：把任务验收机制抽象为可脚本调用的原语 | @sodawlh | agent_teams/workflow | — | — |
| [SDD-0018](designs/archive/SDD-0018-swarmflow-team-runtime-binding/design.md) | D | Swarmflow 启停与 Team 生命周期联动（小飞机控灯 / 恢复裁决 / 冷启动续跑） | @chenchunzhou | agent_teams/workflow, agent_teams | — | — |
| [SDD-0020](designs/proposals/SDD-0020-swarmflow-operator-options/design.md) | D | Swarmflow 算子 options 参数治理（model 前置校验 + timeout 可观测性） | @wangcan103 | agent_teams/workflow | — | — |
| [SDD-0021](designs/proposals/SDD-0021-swarmflow-toolsearch-and-thirdparty-agent/design.md) | D | Swarmflow 渐进式工具暴露开关与三方 Agent 接入（tool_search + agent_type） | @chenchunzhou | agent_teams/workflow | — | — |
| [SDD-0027](designs/archive/SDD-0027-swarmflow-per-run-journal-wal-split/design.md) | D | Swarmflow Journal/WAL 按 run 拆分与 WAL 日志化治理 | @xiajing | agent_teams/workflow | — | — |
| [SDD-0030](designs/proposals/SDD-0030-swarmflow-agent-group-expert-worker/design.md) | D | Swarmflow 专家 worker：agent_template 算子参数复用专家团成员 | @chenchunzhou | agent_teams/workflow, agent_teams | — | — |

<a id="module-4"></a>

## extensions/observability

| ID / 文档 | 类型 | 标题 | 作者 | 模块 | 代码文件 | 关联 D |
|---|---|---|---|---|---|---|
| [SDD-0022](designs/proposals/SDD-0022-trajectory-observability-framework/design.md) | D | 轨迹观测框架：基于 OTel GenAI 语义约定的 Harness 与 Team 轨迹采集和处理 | @alan_cheng | extensions/observability, harness, agent_teams | — | — |
| [SDD-0023](designs/proposals/SDD-0023-trajectory-export-langfuse-otel/design.md) | D | 轨迹导出：Langfuse 适配与 OTel 兼容平台上报（含文件 WAL 与离线导入） | @alan_cheng | extensions/observability | — | — |

<a id="module-5"></a>

## harness

| ID / 文档 | 类型 | 标题 | 作者 | 模块 | 代码文件 | 关联 D |
|---|---|---|---|---|---|---|
| [SDD-0006](designs/archive/SDD-0006-native-harness-async-tool-control-and-spill/design.md) | D | 原生 Harness 异步工具管控框架与磁盘溢写 | @alan_cheng | harness | — | — |
| [SDD-0022](designs/proposals/SDD-0022-trajectory-observability-framework/design.md) | D | 轨迹观测框架：基于 OTel GenAI 语义约定的 Harness 与 Team 轨迹采集和处理 | @alan_cheng | extensions/observability, harness, agent_teams | — | — |
| [SDD-0024](designs/proposals/SDD-0024-trajectory-projection-evolution-rl/design.md) | D | 轨迹投影：把 OTel 标准轨迹投影为自演进与在线 AgentRL 输入 | @alan_cheng | agent_evolving, harness | — | — |
| [F_01_ask-user-otel-events](features/harness/F_01_ask-user-otel-events.md) | F | F_01 ask_user 跨中断 OTel 事件 | alan_cheng | openjiuwen/harness | — | 待核实 |
| [F_01_task-scoped-web-research](features/harness/F_01_task-scoped-web-research.md) | F | F_01 任务级 Web 检索配置 | l_x_d | openjiuwen/harness | — | 待核实 |
| [F_02_configured-paid-search](features/harness/F_02_configured-paid-search.md) | F | F_02 Configured Paid Search | m056838700 | openjiuwen/harness | — | 待核实 |
| [F_02_genai-semconv-tool-spans](features/harness/F_02_genai-semconv-tool-spans.md) | F | GenAI 工具 Span 标准化 | alan_cheng | openjiuwen/harness | — | 待核实 |
| [F_03_harness-providers-and-manifest-factory](features/harness/F_03_harness-providers-and-manifest-factory.md) | F | Harness Providers、DeepAgent IO Adapter 与 Manifest 工厂 | alan_cheng | openjiuwen/harness | — | 待核实 |
| [F_04_authoritative-terminal-stream](features/harness/F_04_authoritative-terminal-stream.md) | F | F_04 Authoritative Terminal Stream | m056838700 | openjiuwen/harness | — | 待核实 |
| [F_04_tool-result-llm-rendering](features/harness/F_04_tool-result-llm-rendering.md) | F | 工具结果面向模型的纯文本渲染 | alan_cheng | openjiuwen/harness | — | 待核实 |
| [F_05_browser-task-integrity](features/harness/F_05_browser-task-integrity.md) | F | F_05 Browser Task Integrity | m056838700 | openjiuwen/harness | — | 待核实 |
| [F_05_claude-turn-boundary-from-delivery-receipts](features/harness/F_05_claude-turn-boundary-from-delivery-receipts.md) | F | F_05 Claude Turn Boundaries from Delivery Receipts | alan_cheng | openjiuwen/harness | — | 待核实 |
| [F_06_browser-execution-and-completion](features/harness/F_06_browser-execution-and-completion.md) | F | Browser Execution and Completion Boundaries | m056838700 | openjiuwen/harness | — | 待核实 |
| [F_07_browser-jev-policy](features/harness/F_07_browser-jev-policy.md) | F | Browser Jev Policy with LLM Fallback | 待确认 | openjiuwen/harness | — | 待核实 |
| [F_07_third-party-harness-provider-packages](features/harness/F_07_third-party-harness-provider-packages.md) | F | 第三方 Harness Provider 包：entry point 发现与生成 skill | alan_cheng | openjiuwen/harness | — | 待核实 |
| [F_08_browser-jev-handover](features/harness/F_08_browser-jev-handover.md) | F | Browser Jev Segmented Handover | 待确认 | openjiuwen/harness | — | 待核实 |
| [F_08_provider-owned-model-config](features/harness/F_08_provider-owned-model-config.md) | F | Provider 自有的模型映射：`ModelChoice` + `model_config` | alan_cheng | openjiuwen/harness | — | 待核实 |
| [F_09_auth-fallback-as-a-protocol-capability](features/harness/F_09_auth-fallback-as-a-protocol-capability.md) | F | 认证回退做成协议能力：`AUTH_FALLBACK` + 骨架统一流程 | alan_cheng | openjiuwen/harness | — | 待核实 |
| [F_09_browser-runtime-phase-contract](features/harness/F_09_browser-runtime-phase-contract.md) | F | Browser runtime phase contracts and verified handover | 待确认 | openjiuwen/harness | — | 待核实 |
| [F_10_browser-runtime-simplification](features/harness/F_10_browser-runtime-simplification.md) | F | Browser runtime simplification and shared execution facts | 待确认 | openjiuwen/harness | — | 待核实 |
| [F_10_hot-replacing-installed-providers](features/harness/F_10_hot-replacing-installed-providers.md) | F | 已安装 provider 的热替换：按 RECORD 指纹重新导入 | alan_cheng | openjiuwen/harness | — | 待核实 |
| [F_11_browser-p0-closure](features/harness/F_11_browser-p0-closure.md) | F | Browser P0 closure after runtime simplification | 待确认 | openjiuwen/harness | — | 待核实 |
| [F_12_browser-jev-shared-observation](features/harness/F_12_browser-jev-shared-observation.md) | F | Shared observation and bounded Jev coverage after September 24 replay | 待确认 | openjiuwen/harness | — | 待核实 |
| [F_13_browser-local-decision-loop](features/harness/F_13_browser-local-decision-loop.md) | F | Browser local decision loop and shared observation lifecycle | 待确认 | openjiuwen/harness | — | 待核实 |
| [F_14_browser-model-usage-summary](features/harness/F_14_browser-model-usage-summary.md) | F | Browser model usage at task completion | 待确认 | openjiuwen/harness | — | 待核实 |
| [F_15_browser-jev-decision-context](features/harness/F_15_browser-jev-decision-context.md) | F | Browser Jev decision context and handover repairs | 待确认 | openjiuwen/harness | — | 待核实 |
| [F_16_browser-lean-guards](features/harness/F_16_browser-lean-guards.md) | F | Browser lean guards: general guarantees instead of special cases | 待确认 | openjiuwen/harness | — | 待核实 |
| [F_17_browser-runtime-reduction](features/harness/F_17_browser-runtime-reduction.md) | F | Browser runtime reduction | 待确认 | openjiuwen/harness | — | 待核实 |
| [F_18_browser-receipts-and-local-continuity](features/harness/F_18_browser-receipts-and-local-continuity.md) | F | Browser receipts, observation reuse and local continuity | 待确认 | openjiuwen/harness | — | 待核实 |
| [F_19_browser-observation-first-results](features/harness/F_19_browser-observation-first-results.md) | F | Browser observation-first results | 待确认 | openjiuwen/harness | — | 待核实 |
| [F_20_react-iteration-limit](features/harness/F_20_react-iteration-limit.md) | F | F_20 ReAct 迭代耗尽后的结构化结果 | openJiuwen-bot | openjiuwen/harness | — | 待核实 |
| [F_21_cua-desktop-subagent](features/harness/F_21_cua-desktop-subagent.md) | F | cua_agent desktop subagent | kwangyyinc | openjiuwen/harness | — | 待核实 |
| [F_22_agent-template-card-and-async-loader](features/harness/F_22_agent-template-card-and-async-loader.md) | F | AgentTemplate 卡片与异步加载器 | alan_cheng | openjiuwen/harness | — | 待核实 |
| [F_22_cli-jiuwen-memory-rail-mount-and-switch](features/harness/F_22_cli-jiuwen-memory-rail-mount-and-switch.md) | F | F_22 CLI 挂载 JiuwenMemoryRail 与启用开关 | huangyuzhe | openjiuwen/harness | — | 待核实 |
| [F_23_progressive-tool-discovery-metadata](features/harness/F_23_progressive-tool-discovery-metadata.md) | F | 渐进式工具发现元数据 | MengHan18 | openjiuwen/harness | openjiuwen/harness/tools/tool_discovery/bm25.py, openjiuwen/harness/rails/progressive_tool_rail.py | 无独立 D：复用现有渐进式工具发现架构，仅增加向后兼容的卡片元数据扩展点 |
| [S_01_public-api-and-construction](specs/harness/S_01_public-api-and-construction.md) | S | S_01 公开 API 表面与构造流 | yangzequ | openjiuwen/harness | — | — |
| [S_02_deep-agent-architecture](specs/harness/S_02_deep-agent-architecture.md) | S | S_02 DeepAgent 架构 | yangzequ | openjiuwen/harness | — | — |
| [S_03_task-loop](specs/harness/S_03_task-loop.md) | S | S_03 TaskLoop 事件体系 | yangzequ | openjiuwen/harness | — | — |
| [S_04_rails-contract](specs/harness/S_04_rails-contract.md) | S | S_04 Rails 契约 | yangzequ | openjiuwen/harness | — | — |
| [S_05_tools-contract](specs/harness/S_05_tools-contract.md) | S | S_05 工具契约 | yangzequ | openjiuwen/harness | — | — |
| [S_06_prompts-and-l10n](specs/harness/S_06_prompts-and-l10n.md) | S | S_06 Prompts 与 i18n | yangzequ | openjiuwen/harness | — | — |
| [S_07_skill-library-state](specs/harness/S_07_skill-library-state.md) | S | S_07 Skill 库开关状态 | yangzequ | openjiuwen/harness | — | — |
| [S_08_security-engine](specs/harness/S_08_security-engine.md) | S | S_08 安全引擎 | yangzequ | openjiuwen/harness | — | — |
| [S_09_workspace](specs/harness/S_09_workspace.md) | S | S_09 Workspace | yangzequ | openjiuwen/harness | — | — |
| [S_10_subagent-runtime](specs/harness/S_10_subagent-runtime.md) | S | S_10 SubAgent 运行时 | yangzequ | openjiuwen/harness | — | — |
| [S_11_goal-and-evaluation](specs/harness/S_11_goal-and-evaluation.md) | S | S_11 Goal 与评估 | yangzequ | openjiuwen/harness | — | — |
| [S_12_manifest-declarative-assembly](specs/harness/S_12_manifest-declarative-assembly.md) | S | S_12 Manifest 声明式装配 | yangzequ | openjiuwen/harness | — | — |
| [S_13_resources-packages](specs/harness/S_13_resources-packages.md) | S | S_13 资源包加载（Plugin / AgentTemplate） | yangzequ | openjiuwen/harness | — | — |
| [S_14_lsp-integration](specs/harness/S_14_lsp-integration.md) | S | S_14 LSP 集成 | yangzequ | openjiuwen/harness | — | — |
| [S_15_cli-and-config](specs/harness/S_15_cli-and-config.md) | S | S_15 CLI 与配置 | yangzequ | openjiuwen/harness | — | — |
| [S_16_kv-cache-subagent-lifecycle](specs/harness/S_16_kv-cache-subagent-lifecycle.md) | S | S_16 KV Cache 子代理生命周期 | MengHan18 | openjiuwen/harness | — | — |
| [S_17_personal-context](specs/harness/S_17_personal-context.md) | S | S_17 Personal Context | yangzequ | openjiuwen/harness | — | — |
| [S_18_subagents-and-lifecycle](specs/harness/S_18_subagents-and-lifecycle.md) | S | S_18 SubAgent 预设与生命周期 | yangzequ | openjiuwen/harness | — | — |
| [S_19_harness-providers](specs/harness/S_19_harness-providers.md) | S | S_19 Harness Providers（协议实现、IO Adapter 与 Manifest 工厂） | alan_cheng | openjiuwen/harness | — | — |

<a id="module-6"></a>

## 未分类

| ID / 文档 | 类型 | 标题 | 作者 | 模块 | 代码文件 | 关联 D |
|---|---|---|---|---|---|---|
| [README](designs/README.md) | D | 设计资产 | 待确认 | — | — | — |
| [REGISTRY](designs/REGISTRY.md) | D | SDD 编号台账 (REGISTRY) | 待确认 | — | — | — |
| [SDD-0001](designs/proposals/SDD-0001-sample-design/design.md) | D | 示例设计文档（脚手架自带，可删除） | @your-handle | — | — | — |
| [legacy-documents](designs/legacy-documents.md) | D | 旧格式设计资料 | 待确认 | — | — | — |
| [migration-20261026](designs/migration-20261026.md) | D | 设计文档迁移与跨仓引用 | 待确认 | — | — | — |
| [swarmchat_design](designs/archive/SSD-0012-swarmchat-design/swarmchat_design.md) | D | 群聊设计 | 待确认 | — | — | — |
