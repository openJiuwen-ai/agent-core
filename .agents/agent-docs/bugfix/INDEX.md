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
| [core](#module-core) |
| [core.extension](#module-core-extension) |
| [core/context_engine](#module-core-context_engine) |
| [core/controller](#module-core-controller) |
| [core/foundation/llm](#module-core-foundation-llm) |
| [core/foundation/llm/model_clients](#module-core-foundation-llm-model_clients) |
| [core/runner/drunner/dmessage_queue](#module-core-runner-drunner-dmessage_queue) |
| [core/session](#module-core-session) |
| [core/session/checkpointer](#module-core-session-checkpointer) |
| [core/session/tracer](#module-core-session-tracer) |
| [core/single_agent](#module-core-single_agent) |
| [core/sys_operation](#module-core-sys_operation) |
| [core/workflow](#module-core-workflow) |
| [extensions](#module-extensions) |
| [extensions/a2a](#module-extensions-a2a) |
| [extensions/observability](#module-extensions-observability) |
| [extensions/tracer_otel](#module-extensions-tracer_otel) |
| [harness](#module-harness) |
| [harness/moa](#module-harness-moa) |
| [harness/subagents](#module-harness-subagents) |
| [harness/tools/browser_move/decision](#module-harness-tools-browser_move-decision) |
| [harness/tools/subagent](#module-harness-tools-subagent) |
| [symphony/shared/fingerprint](#module-symphony-shared-fingerprint) |
| [tests/unit_tests/agent/react_agent](#module-tests-unit_tests-agent-react_agent) |

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
| [B_20261117_1240_swarmflow-fork-round-boundary-attachment](agent_teams/B_20261117_1240_swarmflow-fork-round-boundary-attachment.md) | B | Swarmflow fork 轮数边界被运行时附件消息整体前移一轮 | chenchunzhou | [SDD-0014](../designs/active/SDD-0014-swarmflow-session-fork/design.md) |
| [B_20261117_1626_directed-assignment-notice-dropped](agent_teams/B_20261117_1626_directed-assignment-notice-dropped.md) | B | 定向指派/改派的通知被接收端 pending 判据丢弃，新 owner 永不唤醒 | xiajing | 无独立 D：缺陷修复只改消费端投递判据，不新增契约与机制；依据见 S_03（TASK_CLAIMED 定向投递）与 jiuwenswarm 问题 |
| [B_20261118_1142_shared-templates-name-leader-only-tools](agent_teams/B_20261118_1142_shared-templates-name-leader-only-tools.md) | B | 全员共享提示词模板点名 leader 专属工具误导 teammate | zhanglongmin | 无独立 D：日常文案修复，无独立设计文档；行为依据 F_76（提示词分层与渐进式披露）、F_122（framework 建队的 <team-policy> 投递）与 prompts/AGENTS.md 编辑规则 2。 |
| [B_20261125_1733_team_stopped_approval_mail](agent_teams/B_20261125_1733_team_stopped_approval_mail.md) | B | 停止成员的审批回执阻塞持久化 Team 完成 | MaxMadMax | [D-20261125-1414-team-stopped-approval-recovery](../designs/active/D-20261125-1414-team-stopped-approval-recovery/design.md)<br>[D-20261126-1704-team-lifecycle-boundaries](../designs/active/D-20261126-1704-team-lifecycle-boundaries/design.md) |

<a id="module-agent_teams-workflow"></a>

## agent_teams/workflow

| ID / 文档 | 类型 | 标题 | 作者 | 关联 D |
|---|---|---|---|---|
| [B_20261102_1120_avatar-child-session-trace-root](agent_teams/B_20261102_1120_avatar-child-session-trace-root.md) | B | Swarmflow avatar child session 找不到 Team 轨迹 root | wangcan103 | 无独立 D：普通缺陷修复；沿用 S_18 的 session 隔离契约，无独立设计 |

<a id="module-core"></a>

## core

| ID / 文档 | 类型 | 标题 | 作者 | 关联 D |
|---|---|---|---|---|
| [B_20261126_2215_log-rollover-truncate-guard](core_runtime/B_20261126_2215_log-rollover-truncate-guard.md) | B | 日志滚动备份复制失败仍截断活动文件造成日志段丢失 | linghb | 无独立 D：单点数据安全修复,不改变契约与公开接口,无需独立设计。 |

<a id="module-core-extension"></a>

## core.extension

| ID / 文档 | 类型 | 标题 | 作者 | 关联 D |
|---|---|---|---|---|
| [B_20261125_1733_team_stopped_approval_mail](agent_teams/B_20261125_1733_team_stopped_approval_mail.md) | B | 停止成员的审批回执阻塞持久化 Team 完成 | MaxMadMax | [D-20261125-1414-team-stopped-approval-recovery](../designs/active/D-20261125-1414-team-stopped-approval-recovery/design.md)<br>[D-20261126-1704-team-lifecycle-boundaries](../designs/active/D-20261126-1704-team-lifecycle-boundaries/design.md) |

<a id="module-core-context_engine"></a>

## core/context_engine

| ID / 文档 | 类型 | 标题 | 作者 | 关联 D |
|---|---|---|---|---|
| [B_20261103_1050_grandocean-review-feedback](core_runtime/B_20261103_1050_grandocean-review-feedback.md) | B | grandocean 七项检视意见的兼容性修复 | alan_cheng | 无独立 D：用户明确要求按已有七条检视意见直接开发并补测试，无独立设计文档。 |
| [B_20261104_2030_cancellation-cause-lifecycle](core_runtime/B_20261104_2030_cancellation-cause-lifecycle.md) | B | 压缩取消一律误报 failed 及标记生命周期残留窗口缺陷 | CharlesWang2030 | 无独立 D：c4e52b7c 引入 cancellation cause 注册表属于常规缺陷修复，未归档独立设计；本次为对该提交检视发现的标记生命周期缺陷的后续修复，沿用注册表现有契约（TTL、非消费式 resolve、前缀继承）。 |
| [B_20261111_2314_clear-messages-window-truncation](core_runtime/B_20261111_2314_clear-messages-window-truncation.md) | B | clear_messages 因窗口截断无法清空底层缓冲导致旧消息重回模型上下文 | CharlesWang2030 | 无独立 D：契约违背的常规缺陷修复，未归档独立设计；清空语义以 context_engine/base.py 抽象方法 docstring 为唯一契约依据，修复方向为使实现回归既有契约，无新增契约或方案取舍。 |

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
| [B_20261110_2112_anthropic-disabled-thinking-fallback](core_runtime/B_20261110_2112_anthropic-disabled-thinking-fallback.md) | B | Anthropic Messages 支持图谱关闭思考失败后的回退 | lin-xiaoyu | 无独立 D：补齐已有请求级关闭思考回退在 Anthropic 非流式调用中的接入。 |
| [B_20261116_1636_glm53-flash-reasoning-levels](core_runtime/B_20261116_1636_glm53-flash-reasoning-levels.md) | B | GLM-5.3-Flash 推理强度与禁用编码修复 | lizhiyuan | 无独立 D：用户已确认继续采用端点/模型族静态能力表；本次仅补齐既有 Zhipu 配置的一个模型族，无独立设计。 |
| [B_20261116_1708_symphony-temperature-fallback](core_runtime/B_20261116_1708_symphony-temperature-fallback.md) | B | Symphony 构图在模型拒绝温度参数后恢复 | lin-xiaoyu | 无独立 D：按已确认的发布线补丁方案扩展已有请求参数回退。 |

<a id="module-core-foundation-llm-model_clients"></a>

## core/foundation/llm/model_clients

| ID / 文档 | 类型 | 标题 | 作者 | 关联 D |
|---|---|---|---|---|
| [B_20261117_1727_anthropic-output-limit-errors](openjiuwen/core/B_20261117_1727_anthropic-output-limit-errors.md) | B | 模型输出上限截断可见错误修复 | lizhiyuan | 无独立 D：用户在本轮会话确认的 Anthropic 输出上限错误处理范围，未形成独立设计文档。 |

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

<a id="module-core-session-checkpointer"></a>

## core/session/checkpointer

| ID / 文档 | 类型 | 标题 | 作者 | 关联 D |
|---|---|---|---|---|
| [B_20261127_1537_pymilvus-dead-import](core_runtime/B_20261127_1537_pymilvus-dead-import.md) | B | 删除 inmemory checkpointer 的未使用 pymilvus 导入 | Huazijiayou | 无独立 D：单行未使用导入删除，无独立设计文档。 |

<a id="module-core-session-tracer"></a>

## core/session/tracer

| ID / 文档 | 类型 | 标题 | 作者 | 关联 D |
|---|---|---|---|---|
| [B_20261108_2028_llm-span-model-display-name](core_runtime/B_20261108_2028_llm-span-model-display-name.md) | B | llm span 的 gen_ai.request.model 与 span 名显示网关服务 ID 而非模型名缺陷 | yidaaichixia | 无独立 D：常规缺陷修复（遥测展示层纯增量可选字段），无方案取舍与公开契约变更，未归档独立设计。 |

<a id="module-core-single_agent"></a>

## core/single_agent

| ID / 文档 | 类型 | 标题 | 作者 | 关联 D |
|---|---|---|---|---|
| [B_20261117_1727_anthropic-output-limit-errors](openjiuwen/core/B_20261117_1727_anthropic-output-limit-errors.md) | B | 模型输出上限截断可见错误修复 | lizhiyuan | 无独立 D：用户在本轮会话确认的 Anthropic 输出上限错误处理范围，未形成独立设计文档。 |

<a id="module-core-sys_operation"></a>

## core/sys_operation

| ID / 文档 | 类型 | 标题 | 作者 | 关联 D |
|---|---|---|---|---|
| [B_20261104_2054_recovered-context-seed-and-execution-environment](agent_teams/B_20261104_2054_recovered-context-seed-and-execution-environment.md) | B | 冷恢复后成员策略 seed 与执行环境传递修复 | alan_cheng | 无独立 D：无独立 D；修复 F_20261104_1720_tool-execution-environment / F_20261104_1721_session-execution-settings 在冷恢复与远程调用路径上的遗漏。 |

<a id="module-core-workflow"></a>

## core/workflow

| ID / 文档 | 类型 | 标题 | 作者 | 关联 D |
|---|---|---|---|---|
| [B_20261116_2246_subworkflow-stream-batch-mode](core_runtime/B_20261116_2246_subworkflow-stream-batch-mode.md) | B | 子工作流默认批式配置被流式边调用时 _sub_stream 必崩的缩进缺陷 | CharlesWang2030 | 无独立 D：常规缺陷修复，未归档独立设计；修复方案在 CORE-02 问题分析归档中确认（缩进修正 + 批式回退），沿用 _sub_invoke 既有批式返回契约，无新增公开 API。 |

<a id="module-extensions"></a>

## extensions

| ID / 文档 | 类型 | 标题 | 作者 | 关联 D |
|---|---|---|---|---|
| [B_20261126_2165_jiuwenbox-windows-killpg](openjiuwen/B_20261126_2165_jiuwenbox-windows-killpg.md) | B | jiuwenbox 本地超时清理在 Windows 上因 AttributeError 失效留下孤儿进程 | linghb | 无独立 D：单点异常类型修正,不改变任何契约,无需独立设计。 |

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

<a id="module-extensions-tracer_otel"></a>

## extensions/tracer_otel

| ID / 文档 | 类型 | 标题 | 作者 | 关联 D |
|---|---|---|---|---|
| [B_20261108_2028_llm-span-model-display-name](core_runtime/B_20261108_2028_llm-span-model-display-name.md) | B | llm span 的 gen_ai.request.model 与 span 名显示网关服务 ID 而非模型名缺陷 | yidaaichixia | 无独立 D：常规缺陷修复（遥测展示层纯增量可选字段），无方案取舍与公开契约变更，未归档独立设计。 |

<a id="module-harness"></a>

## harness

| ID / 文档 | 类型 | 标题 | 作者 | 关联 D |
|---|---|---|---|---|
| [B_20261104_2030_cancellation-cause-lifecycle](core_runtime/B_20261104_2030_cancellation-cause-lifecycle.md) | B | 压缩取消一律误报 failed 及标记生命周期残留窗口缺陷 | CharlesWang2030 | 无独立 D：c4e52b7c 引入 cancellation cause 注册表属于常规缺陷修复，未归档独立设计；本次为对该提交检视发现的标记生命周期缺陷的后续修复，沿用注册表现有契约（TTL、非消费式 resolve、前缀继承）。 |
| [B_20261111_2006_sandbox-shell-output](harness/B_20261111_2006_sandbox-shell-output.md) | B | 本地沙箱内回读 Shell 超长输出 | 羿寒 王 | 无独立 D：按已确认的沙箱内存储方案修复现有工具结果回读契约。 |
| [B_20261114_1107_interactive-memory-turn](harness/B_20261114_1107_interactive-memory-turn.md) | B | 交互式中断恢复后记忆丢失修复 | robin1217 | 无独立 D：本次按对话中确认的 D3 收敛方案修复，未建立独立 D 文档；外部 F08 的 D3 是决策编号，不是设计资产标识。 |
| [B_20261117_2130_subagent-type-name-variants](harness/B_20261117_2130_subagent-type-name-variants.md) | B | subagent_type 名称机械变体导致子代理委派失败 | mulderlover | 无独立 D：确定性名称归一化缺陷修复，无独立设计；只映射到已注册/已允许名称，不做模糊匹配 |
| [B_20261126_2055_lsp-client-crash-reporting](harness/B_20261126_2055_lsp-client-crash-reporting.md) | B | LSP 客户端 EOF 崩溃不上报、超时请求条目泄漏与 Windows 文件 URI 未编码 | linghb | 无独立 D：对 LSP 客户端通信层的三处确定性缺陷修复，不改变对外契约，无需独立设计。 |

<a id="module-harness-moa"></a>

## harness/moa

| ID / 文档 | 类型 | 标题 | 作者 | 关联 D |
|---|---|---|---|---|
| [B_20261117_1727_anthropic-output-limit-errors](openjiuwen/core/B_20261117_1727_anthropic-output-limit-errors.md) | B | 模型输出上限截断可见错误修复 | lizhiyuan | 无独立 D：用户在本轮会话确认的 Anthropic 输出上限错误处理范围，未形成独立设计文档。 |

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

<a id="module-symphony-shared-fingerprint"></a>

## symphony/shared/fingerprint

| ID / 文档 | 类型 | 标题 | 作者 | 关联 D |
|---|---|---|---|---|
| [B_20261109_1721_symphony-hidden-skill-scan](openjiuwen/symphony/B_20261109_1721_symphony-hidden-skill-scan.md) | B | Symphony 忽略技能归档和隐藏资产 | lin-xiaoyu | 无独立 D：技能文件夹扫描的局部修复。 |

<a id="module-tests-unit_tests-agent-react_agent"></a>

## tests/unit_tests/agent/react_agent

| ID / 文档 | 类型 | 标题 | 作者 | 关联 D |
|---|---|---|---|---|
| [B_20261118_1219_turn-step-runtime-cleanup](core_runtime/B_20261118_1219_turn-step-runtime-cleanup.md) | B | turn.step 测试会话绑定泄漏导致 PR 门禁偶发失败 | liaojunzhuo-sefonsoft | 无独立 D：修复既有测试的资源清理与隔离，不改变生产代码或公开契约，无需独立设计。 |
