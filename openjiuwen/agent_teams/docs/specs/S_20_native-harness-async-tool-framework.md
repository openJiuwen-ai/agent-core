# NativeHarness 异步工具框架运行时规约

## 元信息

| 项 | 值 |
|---|---|
| 类型 | spec |
| 关联模块 | `harness/async_tools.py`、`harness/native_harness.py`、`id_generator.py`、`tools/tool_async.py`、`tools/tool_factory.py`、`tools/tool_permissions.py`、`paths.py`、`rails/team_tool_rail.py`、`workflow/tool_swarmflow.py`、`workflow/observer.py` |
| 最近一次修订日期 | 2026-07-01 |
| 关联 feature | `F_35_native-harness-async-tool-framework.md`、`F_41_async-tool-control-and-spill.md`、`F_47_swarmflow-concurrency-governor.md`、`F_48_swarmflow-inline-script-execution.md` |

## 范围 / 边界

**管：**
- 异步后台工具的两段式协议（启动即闭合 + 完成注入）。
- `AsyncTool` / `AsyncToolRuntime` / `AsyncToolRecord` 契约与生命周期。
- NativeHarness 的注入入口语义。
- 统一 task_id 生成（`id_generator.generate_id`，前缀体系）。
- 管控工具（`async_tasks_list` / `async_task_output` / `async_task_cancel`）契约与装配。
- 大输出磁盘溢写（阈值内联 vs 超阈值写盘 + 摘要回灌）。
- swarmflow 作为第一个实现的接入约束。

**不管：**
- swarmflow 引擎分层 / worker 单轮契约（见 `S_18`）。
- 作用域通知队列 / 工具边界 drain / 多任务合并（C 阶段，未实现）。
- 下沉到通用 `openjiuwen/harness/` 层、普通 DeepAgent 复用（E 阶段，未实现）。

## 两段式协议（不变量）

openjiuwen 工具循环与 Anthropic API 一样**强配对**：每个 `tool_call` 必产生紧随的
`ToolMessage`，`tool.invoke` 同步阻塞、不可挂起 `tool_result`。异步工具因此必须：

1. **启动段**：`invoke` 立即返回 `ToolOutput(success=True, data={"status":"launched",
   "task_id": ...})`，当场闭合 `tool_use`/`ToolMessage`，不阻塞当前轮。
2. **完成段**：后台任务结束后，结果（或错误）**绕过工具协议**，经 NativeHarness 的
   `send(text, immediate=False)` 作为独立注入式消息进入下一轮——**绝不**回到原
   `tool_use_id`。

## 统一 ID 体系（`id_generator.py`）

- `generate_id(kind, *, length=8) -> str`：返回 `{prefix}{base36*length}`。前缀表
  `{"async_tool":"x", "swarmflow":"w", "session_spawn":"s"}`，未知 kind → 默认 `t`。
  `secrets`-backed 随机 body；前缀参照 Claude Code（b/a/w …）便于人/LLM 从 id 识别任务类型。
- 放在 **agent_teams 层**（非 `core/common`），因其索引的 kind 都是 team-scoped。
- id 是**不透明字符串**，无下游解析（已 grep 确认无 substring / startswith / UUID 解析依赖）。

## `AsyncTool`（基类）

- 继承 `TeamTool`；持 `parent_agent`（NativeHarness）引用，构造期由 `TeamToolRail.init(agent)`
  的 `agent` 注入（此时 harness 实例已存在，invoke 时早已 start）。
- 子类实现 `async def run_background(self, task_id, inputs) -> Any`（返回**完整**结果）；
  可选覆写 `launched_description(inputs)`。
- `invoke`：生成 `task_id`（`generate_id(self.card.name)`，swarmflow→`w` 前缀，其它工具名未
  注册→`t`）→ `parent_agent.launch_async_tool(...)` → 立即返回 launched。`map_result` 统一返回
  `async_tool.launched` 文案（含 task_id）。

## `AsyncToolRecord`

- 字段：`task_id` / `tool_name` / `description` / `status`（running|completed|error）/
  `result` / `error` / `output_file`（溢写时为磁盘路径字符串，否则 `None`）/
  `format_completed` / `format_failed`（可选回调，见下）。
- `format_completed(result) -> str | None` / `format_failed(error) -> str | None`（类型别名
  `CompletionFormatter` / `FailureFormatter`，`async_tools.__all__` 导出）：per-task 自定义终态注入文案。
  `AsyncToolRuntime._run` 在回调存在且**返回非 `None`** 时用回调文本 inject；回调返回 `None`（或未设回调）
  视同未设，回退 `async_tool.completed` / `async_tool.failed`（默认文案**不含 run_id**，仅 `tool` + `result`/`error`）。
  swarmflow 用闭包捕获 `run_id` 注入 `swarmflow.completed` / `swarmflow.failed`（含 `run_id`，`S_21`）。

## `AsyncToolRuntime`（挂在 NativeHarness）

- 字段：`registry: dict[task_id, AsyncToolRecord]`、`inject`、
  `output_dir_resolver: Callable[[], Path|None]|None`（溢写目录惰性解析，`None`=不溢写）、
  `spill_threshold: int = 32768`、`_tasks: dict[task_id, asyncio.Task]`（id 映射，支持按 id
  取消，取代起步版的无标识 `set`）、`_events: dict[task_id, asyncio.Event]`（per-task 完成
  信号，供 `wait` 阻塞唤醒）。
- `launch(task_id, coro_factory, *, tool_name, description, format_completed=None,
  format_failed=None)`：建 `running` 记录（含 format 回调）+ per-task event →
  `asyncio.create_task` 入 `_tasks[task_id]`（防 GC）+ done callback `pop(task_id)` → `_run`。
- `_run`：`await coro_factory()`；成功 → 记录 `completed` + 注入文本 =
  `format_completed(result)` 若存在，否则 `_maybe_spill` + `async_tool.completed`；异常 →
  记录 `error` + `team_logger.error` → `format_failed(str(exc))` 若存在，否则
  `async_tool.failed`；`CancelledError` → 记录 `error="cancelled"` → `_signal` → 重抛（**不注入**）。
- `_signal(task_id)`：set per-task event，唤醒阻塞的 `wait`。
- `_maybe_spill(task_id, record, text) -> str`：`len(text) <= spill_threshold` 或 resolver 为
  空 / 返回 `None` → 完整内联（保持"完整回灌"）；否则 `asyncio.to_thread` 写盘 +
  `record.output_file` 置位，返回"摘要（前 1024 字符）+ `async_tool.spilled_notice`（路径 +
  取回提示）"。写盘失败降级内联（不丢结果）。
- `get(task_id)` / `list_all()`：registry 读取（管控工具用）。
- `cancel(task_id) -> bool`：按 id 取消 task + 标记 record `error`/`"cancelled"` + `_signal`；
  未知 id 返 `False`；已完成幂等（返 `True` 不改终态）。
- `wait(task_id, timeout) -> AsyncToolRecord|None`：终态 / 未知立即返回；否则
  `asyncio.wait_for(event.wait(), timeout)`（秒），超时返回 running record（**不 raise**）。
- `has_running(tool_name)`：registry 中是否有该工具的 running 记录（观测 / 管控工具用；
  **不作** swarmflow L1 门禁，见 `S_21` / `F_47`）。
- `cancel_all()`：teardown 取消 `_tasks` 全部未完成。

## 管控工具（`tools/tool_async.py`）

三个普通 `TeamTool`（非 `AsyncTool` —— 同步查询 / 操作立即返回，无两段式），持
`parent_agent`，经 `parent_agent.async_tool_runtime` 操作；与启动工具（swarmflow 等）共享
**同一** runtime 实例，故能查到对方启动的任务。

| 工具 | 入参 | 行为 |
|---|---|---|
| `async_tasks_list` | 无 | `list_all()` → 每行 `task_id / tool / status / description` |
| `async_task_output` | `task_id`，`block:bool=false`，`timeout:int=30000`(ms) | block=false 取当前 record；block=true `wait(task_id, min(timeout,600000)/1000)`；`output_file` 非空读盘，否则内存 result。返回 `{task_id, status, result, error}` |
| `async_task_cancel` | `task_id` | `cancel(task_id)` |

- 装配：均在 `LEADER_ONLY_TOOLS`（`tool_permissions.py`），`create_team_tools` 始终随 leader
  注入（**不 gate** —— registry 空时 list 返回 "No async tasks."，无害）。`timeout` 上限封顶
  600000ms 防死等；`block=true` 占用调用方当前轮，工具描述明确"不要用它轮询"。

## 磁盘输出溢写（>`spill_threshold`，默认 32KB）

- resolver 注入：`TeamToolRail._wire_async_spill(agent)` 设 `runtime.output_dir_resolver` 为
  惰性闭包 —— 运行时取 `agent.session_id`，拼 `paths.async_tool_output_dir(team_name,
  session_id)`（`{team_home}/sessions/{session_id}/async_tools/`），并在首次解析成功时
  `team_backend.register_cleanup_path`（幂等，`clean_team` 经 `_remove_cleanup_paths` rmtree）。
- session_id 在 rail init 时可能为 `None`（早于 round），故 resolver 惰性——溢写发生在 `_run`
  完成（round 运行中），此时 session 必已 start，闭包能解析出目录。
- 阈值默认 32KB，保守倾向内联，尊重起步版"完整回灌"决策；仅真正超大结果才溢写。
  `output_dir_resolver=None`（非 NativeHarness 宿主或未注入）时永不溢写（向后兼容）。
- 溢写文件名 `{task_id}.output`（task_id 由 `generate_id` 生成，字符安全）。

## NativeHarness 注入入口

- `async_tool_runtime`（lazy property）：首次访问建 `AsyncToolRuntime(inject=
  self._inject_async_completion)`。
- `_inject_async_completion(text)`：`await self.send(text, immediate=False)`——IDLE 起新轮、
  RUNNING 排 follow-up。**统一 immediate=False，不分 active/idle。**
- `launch_async_tool(...)`：转发到 `async_tool_runtime.launch`（透传 `format_completed` /
  `format_failed`）。
- `stop()`：teardown 前先 `cancel_all()`，避免完成注入打到正在停止的 harness。

## 结果渲染（不截断）

- `render_result_text(result)`（`harness/async_tools.py`）：None→空串；str→原样；
  dict|list→`json.dumps(ensure_ascii=False, indent=2)`；其它→`str()`。**完整、不截断**——
  是否内联 vs 溢写由 `_maybe_spill` 按长度决定，渲染本身不截断。
- `summarize_run(run)`（`workflow/observer.py`，swarmflow 专用）：`"N phases, M agents"`。

## swarmflow 接入约束

- `SwarmflowTool(AsyncTool)` 构造注入：`parent_agent`(NativeHarness)、`messager`、
  `team_name`、`model_resolver`（worker model 解析）、`concurrency_governor`（`F_47`）。
  worker 不是 teammate、不写 team DB，**不**注入 `team_backend`（`F_44`）。model 取
  `parent_agent.model`。
- `invoke` 前置校验：`script_path`（磁盘）或 `script`（内联源码）至少一个，`name` / `resume_id` 返回
  "not supported yet"；`ConcurrencyGovernor.admit_workflow()` 满则拒绝（L1，
  文案 `Swarmflow concurrent limit reached (n/m)`）。`has_running` 仅作 registry 观测，
  **不作** L1 门禁（见 `S_21`）。内联 `script` 由 `run_background` 落盘到
  `workflows/{META.name}/script.py`（idempotent，resume 重写同路径）后复用 path-based 加载。
- `run_background` 返回脚本原始结果；终态经 `format_completed` / `format_failed` 闭包注入
  `swarmflow.completed` / `swarmflow.failed`（含 `run_id` + `summarize_run` 摘要）。
  phase 进度仍发 `WORKFLOW_PROGRESS`（中途叙述，`WorkflowHandler` 只渲染 `workflow_started`
  / `phase`（含 `run_id`），**不再渲染** `workflow_completed`）。
- 装配：`agent_configurator` 在 leader+`enable_swarmflow` 时注入
  `SWARMFLOW_MODEL_RESOLVER` 与 `SWARMFLOW_CONCURRENCY_GOVERNOR`；`create_team_tools` 以
  `model_resolver is not None` 作 swarmflow 工具 gate。

## 边界 / 错误语义

- 框架零 `TeamAgent` 依赖（仅依赖 NativeHarness 公共表面）。
- 后台任务失败不上抛到主循环：无 `format_failed` 时统一经 `async_tool.failed` 回灌；有自定义
  回调时走回调文本（swarmflow → `swarmflow.failed`）。
- 不走 TaskScheduler 的 `SESSION_SPAWN` 路径，不写 DeepAgent `pending_follow_ups`
  checkpoint 状态。
- 溢写输出文件生命周期随 team —— `register_cleanup_path` 注册后由 `clean_team` 清理，不单独
  泄漏。
