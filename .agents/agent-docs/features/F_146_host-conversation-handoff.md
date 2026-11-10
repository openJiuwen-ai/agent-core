---
modules:
- openjiuwen/agent_teams/harness
- openjiuwen/core/context_engine
designs:
- jiuwenswarm:D-20261028-1704-agent-leader-context-handoff
author: null
author_note: 从恢复补丁导入；原始作者无法从 Git 确认
title: Host 管理的 Leader 对话交接
id: F_146_host-conversation-handoff
type: F
files: []
---

# Host 管理的 Leader 对话交接

日期：2026-10-29。
范围：`agent_teams/harness`、`core/context_engine/conversation.py`，与 `harness/deep_agent.py` 配套。

## 背景与决策

Host 在同一产品 Session 的单智能体和实际 Leader 之间交接对话，需要定位真实 child Session，并在任何自动恢复输入启动之前完成导入。外层 adapter 的 Agent 不代表团队 Leader。

- `bind_leader_preparation(callback)` 绑定异步 host callback。`TeamHarness.start` 在 Leader child.pre_run 与模板/rail 初始化后、native.start 前传递实际 NativeHarness、child Session 和父 Team Session；teammate 不调用。异常阻止 supervisor 启动。绑定方只在驱动内部迭代器时持有 ContextVar，不跨公开 yield；目标 context 创建必须带上目标配置的 processor 链。
- NativeHarness 的 admission guard 同时保护 send 入队和 supervisor dispatch。被拒绝命令只拒绝自身 ack，不能使 supervisor 崩溃。`RunContext.extra.conversation_request_id` 仅在显式输入入队时传给 guard，背景输入不代为确认用户请求。
- `conversation_is_idle` 包含活动轮次、控制队列、后续输入、后台工具和 session_spawn；`conversation_peers_are_idle(session_id=...)` 检查团队任务、成员及未读消息，显式绑定查询 Session。PAUSED 不作为可交接边界。
- `persisted_team_conversation_is_idle(team_session)` 从已保存的 TeamRuntimeContext 定位数据库，通过现有公共 DAO 检查冷团队工作，不启动 Runtime。缺少身份、pending resume 或未完成工作均不视为 idle。
- ContextEngine 的 `export_conversation` / `replace_conversation` 只深复制消息及内存 offload，保持消息 ID 和工具配对，更新目标 session context 与 receipt 一起 commit。提交抛错时回滚内存；持久化已成功但响应丢失由 host 通过 receipt 收敛。
- 替换不复制源任务、配置、模型、工具、workspace 或 turn 状态。host 可指定重置 `team_prompt_context`，使 Leader 重新注入当前身份/名册。

## 拒绝的方案

不复制整个 checkpoint，不按成员名猜 agent_id，不启动整个团队只为读取历史，不从公共 JSONL 重建 Leader 的完整上下文，不自动取消运行中的工具。文件 offload 缺少可证明的跨 workspace 映射时显式失败，不复制失效路径。

## 验证

`tests/unit_tests/core/context_engine/test_conversation.py` 验证实际 Session + PersistenceCheckpointer codec、消息/状态隔离、提交失败回滚和 offload 完整性。
`tests/unit_tests/agent_teams/harness/test_conversation.py` 验证启动 callback、错误 ack、冷团队状态/Session 隔离，以及真实 NativeHarness 任务循环接收已导入消息并提交新回答。
Core 相关回归 194 passed，Swarm 配套回归 296 passed。命令、范围和限制见配套 Swarm 的 `F_20261029_0001_agent-leader-context-handoff.md`；使用本地两个仓库源码，未调用真实外部模型。

## 交付边界

本接口不拥有产品 Session 的 owner/journal，也不提供通用输入去重。Host 必须在静止检查、冻结、导入、提交、归属更新之间维持互斥；跨崩溃工具副作用恰好一次不在本变更范围。Swarm 发布前须更新到包含本接口的 Core 版本。

关联契约：[S_18_harness-interaction-contract.md](../specs/agent_teams/S_18_harness-interaction-contract.md)。

## 2026-11-09 补丁迁入

恢复至 `feat/agent-leader-context-handoff`，基线为 `b412128ab89362de0afe95682b113d880813c97a`。
DeepAgent 合并时保留上游 invocation 日志、proposal 生命周期及 host iteration limit；归属 guard 在
proposal 建立前检查，conversation commit 在状态保存后完成。spec 与 feature 使用当前 `.agents/agent-docs/`
布局。原文测试结果是历史记录；本次尚未重跑行为测试，当前仓库无 `.venv`。

## 2026-11-09 NativeHarness 提交失败隔离

PR #2589 检视发现，`_settle_round_done` 在结果已输出后调用 `commit_conversation`，
context 保存或 Session commit 抛错会触发 supervisor 的终止分支，关闭输出流并丢弃排队工作。
本次是既有托管会话实现的局部修正，继续维护本记录及 S_18，不改变交接凭据或存储格式。

仅在此提交边界捕获 `Exception`，记录带 round_id 的异常堆栈，然后继续原有调度。
不重放已完成输入，不自动重试不确定的写入，不把持久化成功作为日志之外的承诺；
取消异常继续传播。未捕获整个 dispatch，以保留其它 handler 崩溃的终止语义。
进程仍可能在下一次成功 checkpoint 前丢失尚未持久化的状态，本修复保证运行时可用性。

验证候选为 `7d4f2c73a` 加本次工作区差异。新增参数化用例
`test_conversation_commit_failure_preserves_supervisor_and_queued_input`，分别在 context 保存和
Session commit 注入一次 OSError。修复前两项均因 supervisor 终止、无法回到 IDLE 而失败；
修复后验证已排队输入和后续新输入均执行一次、三个答案完整输出、输出流保持打开，
且错误日志包含 round_id 与原始异常。

Python 3.12 使用 `/home/yanqingshi/jiuwenswarm/.venv/bin/python` 和已有本地 pytest / pytest-asyncio
包运行，无依赖安装；测试入口为 `pytest.main`，显式加载 `pytest_asyncio.plugin`，
关闭插件自动发现和 cacheprovider，并以 `-o addopts=` 禁用 HTML 报告生成。
以下四个文件共 **39 passed**：

- `tests/unit_tests/agent_teams/harness/test_conversation.py`
- `tests/unit_tests/agent_teams/harness/test_critical_regressions.py`
- `tests/unit_tests/agent_teams/harness/test_state_transitions.py`
- `tests/unit_tests/agent_teams/harness/test_failure_retry.py`

使用真实 supervisor / task-loop 和模拟内层模型，验证范围限于本次故障隔离及相关生命周期。
未运行真实外部模型、全量测试或锁定依赖环境。DeepAgent 对持久化错误的分类问题留作下一项修复。

后续 DeepAgent 修复与验证记录见 [F_32_conversation-ownership.md](F_32_conversation-ownership.md)
的「2026-11-09 交互轮次提交失败隔离」。

## 2026-11-09 回滚证据与公开导出补全

复核 PR 建议后，保留 ContextEngine 现有状态形状及单次 Session commit：`save_contexts`
只暂存 Session 状态，未发现需要额外补偿存储写入的证据。本次补充的是可独立恢复的行为证据。

- 先提交旧状态，再注入 commit 异常；原 context、独立新 engine 从内存 Session 物化的 context，
  以及全新 Session 从 PersistenceCheckpointer 恢复的 context 均保持旧消息，全部 Session 状态恢复。
- 模拟 commit 已完成后抛错或取消，内存回到旧状态，而全新 Session 同时恢复新消息、receipt
  和 reset_state_keys 的结果；宿主据此判断交接是否已持久化。两种时序均覆盖 OSError 和 CancelledError。
- 带 engine 的 live export 覆盖未提交消息和快照隔离；独立 Session 仍只读到已提交消息。
- 将既有 `persisted_team_conversation_is_idle` 加入 `openjiuwen.agent_teams.harness` 的导入和
  `__all__`，现有七种冷团队场景改用该公开路径；修复前均复现 ImportError。启动分发用的
  `prepare_leader_conversation` 继续作为内部实现，不额外扩展公共回调入口。

在前述 Python 3.12 本地环境，候选为 `7d4f2c73a` 加本轮累积修复，F_32 列出的七个测试文件
加 `tests/unit_tests/core/context_engine/test_conversation.py` 共 **116 passed**。
新增恢复测试使用真实 Session 和 PersistenceCheckpointer codec、MockKVStore；未验证真实存储
后端的部分写入或网络故障，也未运行全量测试。以上完善不改变既有交接语义或存储格式。

## 2026-11-10 受保护成员访问修正

按 G.CLS.11 修正两处跨对象受保护成员访问。`conversation_is_idle` 改用
`AsyncToolRuntime.has_pending_tasks()`，不再读取 `_tasks`；该查询仍以 Task 是否结束为准，
包含任务记录已 completed/error、结果仍在注入的阶段。未复用 `has_running(tool_name)`，
因为后者只检查业务记录的 running 状态，无法覆盖结果注入窗口。

新增幂等的 `NativeHarness.prepare()` 公开准备入口，委托原有 `_prepare()` 完成 rails
与模板初始化；TeamHarness 在 child.pre_run 后通过此入口准备，再调用 Leader callback，
最后启动 supervisor。保留内部准备实现和其它生命周期调用的行为。

模板加载测试改从公开入口验证幂等和加载顺序；Leader callback 用例验证回调前已初始化
并应用模板，且 supervisor 尚未启动。新增用例用 Event 控制结果注入：后台任务刚启动、
业务记录完成但注入未结束时均不可交接，注入结束后恢复为空闲。

候选为 `4ea6d2588` 加本次工作区差异，沿用前述 Python 3.12 本地依赖环境。下列七个文件
共 **65 passed、1 deselected**，其中新增/调整的行为测试均通过：

- `tests/unit_tests/agent_teams/harness/test_async_tools.py`
- `tests/unit_tests/agent_teams/harness/test_agent_template_prepare.py`
- `tests/unit_tests/agent_teams/harness/test_conversation.py`
- `tests/unit_tests/agent_teams/harness/test_run_once.py`
- `tests/unit_tests/agent_teams/harness/test_protocol_and_lifecycle.py`
- `tests/unit_tests/agent_teams/harness/test_critical_regressions.py`
- `tests/unit_tests/agent_teams/harness/test_state_transitions.py`

整目录验证在 `test_runtime_large_result_spills_to_disk` 的 asyncio.run 关闭阶段挂起，已中断。
该项单独执行也在 30 秒超时，未计入通过数量；完整目录验证未完成，未运行静态检查。
同环境将 async_tools 模块替换为 `4ea6d2588` 的原始源码后单独执行该项，仍在 30 秒超时，
说明这一溢写验证限制在未加入本次查询接口的基线实现中也存在；本次未修改溢写逻辑。
