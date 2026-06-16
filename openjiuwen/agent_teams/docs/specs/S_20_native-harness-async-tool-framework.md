# NativeHarness 异步工具框架运行时规约

## 元信息

| 项 | 值 |
|---|---|
| 类型 | spec |
| 关联模块 | `harness/async_tools.py`、`harness/native_harness.py`、`workflow/tool_swarmflow.py`、`workflow/observer.py`、`tools/tool_factory.py`、`rails/team_tool_rail.py`、`rails/elements.py`、`rails/team_context.py`、`agent/agent_configurator.py` |
| 最近一次修订日期 | 2026-06-05 |
| 关联 feature | `F_35_native-harness-async-tool-framework.md` |

## 范围 / 边界

**管：**
- 异步后台工具的两段式协议（启动即闭合 + 完成注入）。
- `AsyncTool` / `AsyncToolRuntime` / `AsyncToolRecord` 契约与生命周期。
- NativeHarness 的注入入口语义。
- swarmflow 作为第一个实现的接入约束。

**不管：**
- swarmflow 引擎分层 / worker 单轮契约（见 `S_18`）。
- 取回工具 / list / cancel / 磁盘输出 / 全局通知队列（后续阶段，未实现）。

## 两段式协议（不变量）

openjiuwen 工具循环与 Anthropic API 一样**强配对**：每个 `tool_call` 必产生紧随的
`ToolMessage`，`tool.invoke` 同步阻塞、不可挂起 `tool_result`。异步工具因此必须：

1. **启动段**：`invoke` 立即返回 `ToolOutput(success=True, data={"status":"launched",
   "task_id": ...})`，当场闭合 `tool_use`/`ToolMessage`，不阻塞当前轮。
2. **完成段**：后台任务结束后，结果（或错误）**绕过工具协议**，经 NativeHarness 的
   `send(text, immediate=False)` 作为独立注入式消息进入下一轮——**绝不**回到原
   `tool_use_id`。

## `AsyncTool`（基类）

- 继承 `TeamTool`；持 `parent_agent`（NativeHarness）引用，构造期由 `TeamToolRail.init(agent)`
  的 `agent` 注入（此时 harness 实例已存在，invoke 时早已 start）。
- 子类实现 `async def run_background(self, task_id, inputs) -> Any`（返回**完整**结果）；
  可选覆写 `launched_description(inputs)`。
- `invoke`：生成 `task_id`（`uuid.uuid4().hex`）→ `parent_agent.launch_async_tool(...)` →
  立即返回 launched。`map_result` 统一返回 `async_tool.launched` 文案（含 task_id）。

## `AsyncToolRuntime`（挂在 NativeHarness）

- 字段：`registry: dict[task_id, AsyncToolRecord]`、`tasks: set[asyncio.Task]`、`inject`。
- `launch(task_id, coro_factory, *, tool_name, description)`：建 `running` 记录 →
  `asyncio.create_task` 跟踪（防 GC）+ done callback discard → `_run`。
- `_run`：`await coro_factory()`；成功 → 记录 `completed` + `inject(t("async_tool.completed",
  ...))`；异常 → 记录 `error` + `team_logger.error` + `inject(t("async_tool.failed", ...))`；
  `CancelledError` → 记录 `error="cancelled"` 并重抛（**不注入**）。
- `_inject` best-effort：`inject` 抛错（如 harness 已 stop）只 debug 记录，不上抛。
- `has_running(tool_name)`：是否有该工具的 running 记录（供单实例约束）。
- `cancel_all()`：teardown 取消未完成 tasks。

## NativeHarness 注入入口

- `async_tool_runtime`（lazy property）：首次访问建 `AsyncToolRuntime(inject=
  self._inject_async_completion)`。
- `_inject_async_completion(text)`：`await self.send(text, immediate=False)`——IDLE 起新轮、
  RUNNING 排 follow-up。**统一 immediate=False，不分 active/idle。**
- `launch_async_tool(...)`：转发到 `async_tool_runtime.launch`。
- `stop()`：teardown 前先 `cancel_all()`，避免完成注入打到正在停止的 harness。

## 结果渲染（不截断）

- `render_result_text(result)`（`harness/async_tools.py`）：None→空串；str→原样；
  dict|list→`json.dumps(ensure_ascii=False, indent=2)`；其它→`str()`。**完整、不截断。**
- `summarize_run(run)`（`workflow/observer.py`，swarmflow 专用）：`"N phases, M agents"`。

## swarmflow 接入约束

- `SwarmflowTool(AsyncTool)` 构造注入：`parent_agent`(NativeHarness)、`messager`、
  `team_backend`、`team_name`、`model_resolver`（worker model 解析）。model 取
  `parent_agent.model`。
- `invoke` 前置校验：`script_path` 必填；`has_running("swarmflow")` 为真则拒绝（单实例）。
- `run_background` 返回 `summarize_run(observer.run)` + `render_result_text(脚本返回值)`；
  phase 进度仍发 `WORKFLOW_PROGRESS`（中途叙述，`WorkflowHandler` 只渲染 `workflow_started`
  / `phase`，**不再渲染** `workflow_completed`）。
- 装配 gate：`agent_configurator` 仅 leader+`enable_swarmflow` 时注入
  `TeamHandleKey.SWARMFLOW_MODEL_RESOLVER`，`create_team_tools` 以其非空作 swarmflow gate。

## 边界 / 错误语义

- 框架零 `TeamAgent` 依赖（仅依赖 NativeHarness 公共表面）。
- 后台任务失败不上抛到主循环，统一经完成段以 `async_tool.failed` 文本回灌 leader。
- 不走 TaskScheduler 的 `SESSION_SPAWN` 路径，不写 DeepAgent `pending_follow_ups`
  checkpoint 状态。
