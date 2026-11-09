---
modules:
- openjiuwen/harness
designs:
- jiuwenswarm:D-20261028-1704-agent-leader-context-handoff
author: null
author_note: 从恢复补丁导入；原始作者无法从 Git 确认
title: DeepAgent 对话归属保护与提交
id: F_32_conversation-ownership
type: F
files: []
---

# DeepAgent 对话归属保护与提交

日期：2026-10-29。范围：`openjiuwen/harness/deep_agent.py`。

## 背景与决策

Host 接管对话后，旧的缓存 Agent 仍可能收到输入；仅替换目标消息不能防止旧实例继续执行。`set_conversation_guard` 安装同步 host guard，在输入接纳与运行边界检查归属；未安装 guard 的普通 Core 用户保持原行为。

交互式轮次和 invoke/stream 完成时，受管理 Agent 通过 `commit_conversation` 保存原生 context 并提交 Session，使空闲驱逐后的冷恢复能读取完整消息。`conversation_is_idle` 同时检查活动调用、交互轮次、事件队列和待完成 session_spawn。Host 还必须检查任务、goal、人机交互等可恢复状态。

## 拒绝的方案

不将 Swarm 的文件 metadata 或 UI mode 引入 Core，也不替换现有调度器、权限流程或 checkpoint codec。guard 不自动取消工具或暂停轮次。

## 验证与边界

原生调度循环、提交与 guard 的配套行为测试位于 `tests/unit_tests/agent_teams/harness/test_conversation.py`；快照 codec 测试位于 `tests/unit_tests/core/context_engine/test_conversation.py`。未进行外部模型验收。

Core 相关回归共 194 passed，包含本模块的 interaction、terminal_stream、session_state、stream_aclose 测试。

共同的消息交接决策和跨仓交付条件见 [F_146_host-conversation-handoff.md](F_146_host-conversation-handoff.md)，不在本记录复制。关联契约：[S_02_deep-agent-architecture.md](../specs/harness/S_02_deep-agent-architecture.md)。

## 2026-11-09 交互轮次提交失败隔离

PR #2589 检视确认：`run_one_round` 已输出结果、计算 next_work 并保存 Session 状态后，
conversation commit 的异常会被外层执行异常处理改写为 `round_execution_error`。
这会向宿主追加错误事件、丢失返回的 next_work，并将 proposal 的成功执行标记为失败。
`clear_state` 只清运行时缓存，并不会默认删除 Session 中已保存的 deepagent 状态。

本次沿用既有对话归属方案，在交互轮次的 commit 边界单独捕获 `Exception` 并记录明确的
`interaction conversation commit failed` 堆栈；保留已输出的结果、成功 outcome、next_work
和 proposal 完成状态。不自动重放输入或重试不确定写入；执行成功不等于 checkpoint 成功。
取消仍传播，真正的执行错误保留原错误码。未修改共享 commit helper，直接 invoke/stream
仍将 commit 异常交给调用方；交互模式沿用长驻 supervisor 的错误隔离边界。

候选为 `7d4f2c73a` 加本地 NativeHarness P1 和本次 DeepAgent P2 工作区修复。
新增 `test_run_one_round_separates_commit_failure_from_execution` 验证 context 保存、Session
commit、真实执行异常和取消四种情况；另以真实 interaction supervisor / task-loop 和模拟内层
模型验证两类 checkpoint 失败后答案保留、不输出 execution.error、下一个输入仍完成。
修复前四个持久化用例均复现预期缺陷；执行错误及取消负向对照通过。修复后下列文件共
**106 passed**：

- `tests/unit_tests/harness/test_deep_agent_interaction.py`
- `tests/unit_tests/harness/test_deep_agent_session_state.py`
- `tests/unit_tests/harness/test_deep_agent_terminal_stream.py`
- `tests/unit_tests/agent_teams/harness/test_conversation.py`
- `tests/unit_tests/agent_teams/harness/test_critical_regressions.py`
- `tests/unit_tests/agent_teams/harness/test_state_transitions.py`
- `tests/unit_tests/agent_teams/harness/test_failure_retry.py`

沿用 F_146 中记载的 Python 3.12 与已有本地 pytest / pytest-asyncio 环境，通过 pytest.main
运行；禁用自动插件、cacheprovider 和默认 HTML addopts，无依赖安装或真实模型调用。
验证不覆盖真实存储故障、全量测试或锁定依赖环境；进程恢复仍依赖已持久化的 checkpoint。

## 2026-11-10 直接 invoke / stream 提交失败隔离

PR #2589 对 `663bfc1a` 的复查确认，直接调用在模型和外层 rail 已成功完成后仍可能被
conversation commit 异常打断：invoke 不返回最终结果，stream 只交付中间 chunk 而不交付
最终 answer，proposal 的 succeeded 为 False。此处接续并替代上一节「直接调用继续传播
commit 异常」的局部选择，按本次用户确认将受管理调用的提交失败与执行成功分开。

invoke / stream 各自在状态保存后的 conversation commit 边界捕获 `Exception`，用带入口名
的异常堆栈报告失败，保留外层 rail 最终确定的结果及 proposal 成功状态。不改变共享的
commit_conversation 原语；宿主直接调用它时仍可观察提交异常。

DeepAgent 自建的 Session.post_run 也会 commit，单独保护前一处调用不足以处理持续存储
故障。新增 `_post_run_owned_session` 保留输出流关闭和 Session 收尾，在安装 guard 时
记录并隔离普通收尾异常；未受管理时仍传播异常。stream 在受管理收尾返回后标记已完成，
避免已记录的普通异常导致 finally 再次调用 post_run。若收尾被取消，finally 仍完成清理，
取消不被 Exception 捕获，实际执行错误也继续传播。
执行成功不保证持久化成功，进程恢复仍以存储中的 checkpoint 为准。

回归位于 `tests/unit_tests/harness/test_deep_agent_terminal_stream.py`。八个参数组合覆盖
两个入口、两种 Session 所有权、context 保存或持续 commit 故障，验证最终 rail 结果、
唯一 answer、proposal 成功、清理一次及实际输出 emitter 关闭。修复前八项均失败。
另四项验证执行异常及 checkpoint 取消继续传播，两项验证非受管理的自建 Session
保留持久化异常。另验证自建 Session 在收尾中被任务取消时，finally 仍完成清理并传播取消。
所有测试使用真实 DeepAgent/Session/Rail 和模拟内层模型。

候选为 `663bfc1a` 加本次工作区差异；沿用前述 Python 3.12 和已有本地测试依赖，
下列九个文件共 **132 passed**，无依赖安装、外部模型或真实存储故障验证：

- `tests/unit_tests/harness/test_deep_agent_terminal_stream.py`
- `tests/unit_tests/harness/test_deep_agent_stream_aclose.py`
- `tests/unit_tests/harness/test_deep_agent_interaction.py`
- `tests/unit_tests/harness/test_deep_agent_session_state.py`
- `tests/unit_tests/core/context_engine/test_conversation.py`
- `tests/unit_tests/agent_teams/harness/test_conversation.py`
- `tests/unit_tests/agent_teams/harness/test_critical_regressions.py`
- `tests/unit_tests/agent_teams/harness/test_state_transitions.py`
- `tests/unit_tests/agent_teams/harness/test_failure_retry.py`
