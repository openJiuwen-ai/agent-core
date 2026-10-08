# F_07 Session 级 checkpointer 注入

| 项 | 内容 |
| --- | --- |
| 日期 | 2026-10-07 |
| 状态 | 已实现 |
| 负责人 | Song Haoran |
| 测试基线 | 新增 5 项单测；回归 6609 passed、44 skipped（harness、core single_agent / runner / session、agent 全量单测） |
| 范围 | `openjiuwen/core/session/agent.py`（`Session`、`create_agent_session`）、`openjiuwen/core/session/checkpointer/`（新增 `NoopCheckpointer` 并导出） |
| 关联 | [Persistent Agent 详细架构设计](../../persistent_agent/docs/designs/persistent-agent-architecture.md) §7.8；Persistent [F_05](../../persistent_agent/docs/features/F_05_execution-entries.md)（PA-05） |

## 背景与决策

Persistent Agent 的跨 Execution 状态只能来自 `WindowStore`。但原生 ReAct 流式路径对 AgentSession 会自行调用 `session.pre_run()` 和 `session.commit()`：

- `pre_run` 通过全局 `CheckpointerFactory.get_checkpointer()` 按 session_id 把旧状态整体写回 Session state；
- `commit` 通过 Session 自带的 checkpointer 保存整份 Session state。

在 AgentServer 进程内，这会让默认的内存 checkpointer 覆盖 `WindowStore` 刚加载的状态，并按 session_id 长期留存快照。公开的 `Session` 构造函数又没有暴露内部 `AgentSession` 已经支持的 `checkpointer` 参数，调用方无法按 Session 隔离。

决策：

- `Session.__init__` 和 `create_agent_session` 增加可选关键字参数 `checkpointer: Checkpointer | None = None`，传给内部 `AgentSession`。
- 显式传入时，`pre_run` 的状态加载和 `commit` 的状态保存都使用注入的实现。
- 未传入时保留现有行为，包括选择 checkpointer 的时机：内部 Session 在构造时取得默认实现供 `commit` 使用，`pre_run` 在执行时取得全局实现。个人版和其他调用方的行为不变。
- `Session` 增加只读方法 `checkpointer()`，返回本 Session 实际用于保存的 checkpointer，供调用方检查隔离配置，不必读取私有属性。
- 新增 `NoopCheckpointer`：所有恢复、保存、中断和清理方法都不读写存储，`session_exists` 返回 False；`graph_store()` 返回该实例自带的内存 Store，只供同一 Session 内的工作流组件使用，不跨 Session 共享。从 `openjiuwen.core.session.checkpointer` 导出。
- 原生生命周期照常运行：`pre_run` 仍触发 Session 创建回调，`close_stream`、取消收敛和资源清理不受影响；只是注入空实现后，其中的 checkpointer 恢复和保存不读写存储。执行过程中仍正常读写当前 Session state。

Persistent 的用法：AgentServer 为每次 Execution 创建新的空 Session 并显式注入 `NoopCheckpointer`；PA-05 在执行前检查 `session.checkpointer()` 是不是 `NoopCheckpointer`，不是就拒绝开始。隔离按 Session 生效，不修改全局 checkpointer 配置。

## 拒绝的方案

| 方案 | 不采用的原因 |
| --- | --- |
| 在 Persistent 里改写 `session._inner._checkpointer`、标记 `_pre_run_done` | 依赖私有属性，原生实现一改就失效 |
| 修改全局 `CheckpointerFactory` 默认实现 | 影响同进程的其他 Agent 和个人版 |
| 未注入时也让 `pre_run` 改用 Session 自带的 checkpointer | 会改变默认路径选择 checkpointer 的时机；当前调用方可能在构造 Session 后才替换全局默认实现 |
| 用 `InMemoryCheckpointer` 代替空实现 | 它仍会按 session_id 恢复和保存状态，不能提供隔离 |
| 改走原生 invoke 路径避开 `pre_run` | 只规避流式路径，取消和异常路径仍会提交；PA-05 需要流式输出 |

## 已核实的原生行为

- 单 Agent 与 harness 路径不读取 `pre_run` 写入的 `INTERACTIVE_INPUT`；空实现跳过这次写入不影响工具中断恢复。
- 涉及 checkpointer 的调用只有 `pre_run`、`commit` 和 `session.interact` 的交互路径（后者调用 `interrupt_agent_execute`，工具中断恢复不走这条路径）。

## 验证

验证计划：

1. 显式注入时，`pre_run` 调用注入实现的 `pre_agent_execute`、不调用全局实现；`commit` 调用注入实现的 `post_agent_execute`；`checkpointer()` 返回注入的实例。
2. 未注入时，`pre_run` 仍调用全局实现、`commit` 仍使用构造时取得的实现，与改动前一致。
3. 注入 `NoopCheckpointer` 后：全局内存 checkpointer 中同 session_id 的旧状态不会被恢复；执行后全局 checkpointer 中没有该 Session 的新快照；Session state 在执行中照常读写。
4. `NoopCheckpointer` 的各方法不抛异常、不读写存储。
5. 回归：core session 相关单测、ReAct 与 DeepAgent 定向回归、Persistent 包测试。

实际结果：

| 验证 | 结果 |
| --- | --- |
| `tests/unit_tests/core/session/checkpointer/test_session_checkpointer_injection.py` | 5 passed；撤掉 `agent.py` 改动后 5 项全部失败，恢复后通过 |
| 回归：`tests/unit_tests/harness`、`core/single_agent`、`core/runner`、`core/session`、`tests/unit_tests/agent` | 6609 passed、44 skipped，无失败（运行环境补装了 `observability`、`cli` 可选依赖，否则部分 harness 测试在收集阶段缺 `opentelemetry.exporter`） |
| Ruff（改动文件） | 新增文件通过；`agent.py`、`checkpointer/__init__.py` 的 I001 为改动前已有，未处理 |

## 已知遗留

- AgentServer 侧的 Session 创建与注入在 jiuwenswarm 仓实现。
- 子代理、task-loop 等路径的隔离随 Persistent PA-24～PA-26 验证。
