---
id: F_20261125_1828_team-prompt-event-identity
title: Team 成员 Prompt 事件的身份隔离
type: F
author: yangzequ
author_email: yangzequ1@huawei.com
modules: [core/events, core/single_agent, harness, agent_teams]
files:
  - openjiuwen/core/events/builtins/event_types/prompt_compose.py
  - openjiuwen/core/single_agent/agents/react_agent.py
  - openjiuwen/harness/prompts/prompt_attachment_manager.py
  - openjiuwen/agent_teams/harness/native_harness.py
  - openjiuwen/agent_teams/harness/team_harness.py
designs: [D-20261125-1725-team-prompt-event-identity]
---

# Team 成员 Prompt 事件的身份隔离

归档日期为 2026-11-25。方案见 [D-20261125-1725-team-prompt-event-identity](../designs/active/D-20261125-1725-team-prompt-event-identity/design.md)，当前行为见 [Events S_01](../../../openjiuwen/core/events/docs/specs/S_01_events-contract.md)、[Harness S_06](../specs/harness/S_06_prompts-and-l10n.md)和 [Agent Teams S_09](../specs/agent_teams/S_09_prompts-and-rails.md)。

## 实施与取舍

旧版 Runtime 仅按会话查找 Agent，团队成员共用会话时可能取到另一个成员的 builder，因此 Team 曾绕过 Prompt 事件。现在 ReActAgent 传递当前 Agent Session。触发前检查和 `prompt.compose` CORE 都按 `(session_id, agent_id)` 查找当前 DeepAgent，`prompt.section` 继承同一上下文。保留 session-only 查找会在多个成员同时绑定时重新引入串用，因此仅对缺省 `agent_id` 的旧调用保留该行为。

附件管理器用 Agent Loop 传入的身份构建批次的 `SessionContext`，并从同一个上下文填充 `PromptAttachmentInput.agent_id`。缺省身份的直接调用仍可继续。Agent Loop 将空卡片 ID 按缺省身份处理，以满足 EventBus 对身份字段的校验。有 Bus 时，Team 成员的附件改写在写入历史前执行。附件的 hash、快照和增量仍基于原始附件。

移除 `is_team_agent` 和 `trigger_events` 旁路。NativeHarness 继续给内层 ReActAgent 传递 usage attribution。保留旁路开关会让 Team 构造路径继续承担事件分流责任。新的回归由真实 `TeamHarness.run_once` 创建 child session，覆盖设计中拟用两个 DeepAgent 模拟的验证场景。

## 验证

在 Windows 的仓库虚拟环境运行以下定向回归。用例在模型边界使用模拟 LLM，事件总线、Runtime、Harness 和 Session 都使用实际实现。

```powershell
& ./.venv/Scripts/python.exe -m pytest -o addopts= -q --tb=short --show-capture=no --disable-warnings tests/unit_tests/core/events tests/unit_tests/core/single_agent/test_agent_runtime.py tests/unit_tests/agent/react_agent tests/unit_tests/harness/test_prompt_attachment_event_integration.py tests/unit_tests/harness/test_prompt_attachment_manager.py tests/unit_tests/harness/test_deep_agent_runtime_binding.py tests/unit_tests/agent_teams/harness tests/unit_tests/agent_teams/extensions tests/unit_tests/agent_teams/test_harness.py
```

- 基线定向用例复现 Team 跳过 compose，以及附件输入和上下文丢失已知身份。无身份的附件用例基线通过。
- 实现后定向回归为 877 项通过。同一团队的两个成员均触发三类 Prompt 事件。测试在 compose 续执行前将另一成员设为仅按 session 查找时的最后绑定，两个模型请求仍只包含各自的改写 section 和附件。
- 检视期间补充了空卡片 ID 的回归。修正前总线拒绝事件，修正后仍触发附件 handler，输入和上下文的 `agent_id` 都是 `None`。
- Team 构建测试使用 pytest 临时目录隔离工作目录。回归覆盖已有附件省略、异常回退、取消和无 Bus 行为，以及内层 Agent 更换后的 wiring。

## 验证边界

验证范围为 agent-core 的定向测试。JiuwenSwarm 宿主渠道、真实模型服务和全仓测试不在这次运行范围内。

## 2026-11-26 流式入口修正

`run_once` 由 `DeepAgent.invoke` 登记成员。流式 supervisor 通过 task-loop executor 直接驱动
内层 ReActAgent，原先没有登记步骤，导致已启用 Bus 的请求仍用原生 builder。
现在 supervisor 在接收首轮输入前登记自身，并在退出时释放自身的复合键绑定。
解绑按实例校验，先停止的成员不会移除同会话的其他成员。

回归用真实 `TeamPolicyRail` 和共享 child session 分别驱动 `start/send` 与 `run_once`。
模拟模型捕获到各自的 leader 开场段、teammate 角色段和独立附件，事件改写进入对应请求。
流式用例检查成员独立解绑，崩溃回归检查 supervisor 异常退出时也会解绑。
另对中英文、两种建队方式和两种入口做了模型请求对照，事件透传时首条系统消息与原生渲染逐字一致。

在原定向测试范围基础上增加 Harness 关闭、Team Policy Rail、角色模板和 leader 恢复开场段用例。
首轮只有 ReAct 流式延迟断言得到零值，整组复跑结果为 1083 项通过。
