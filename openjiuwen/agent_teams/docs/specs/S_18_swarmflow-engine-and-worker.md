# Swarmflow 引擎与 Worker 运行时规约

## 元信息

| 项 | 值 |
|---|---|
| 类型 | spec |
| 关联模块 | `workflow/`（engine / backends / observer / schema / runner / tool_swarmflow）、`schema/team.py`、`schema/events.py`、`schema/blueprint.py`、`agent/team_agent.py`、`agent/coordination/handlers/workflow.py`、`rails/team_policy_rail.py`、`prompts/sections.py` |
| 最近一次修订日期 | 2026-06-03 |
| 关联 feature | `F_27_swarmflow-workflow-orchestration.md`、`F_31_swarmflow-per-call-model-routing.md` |

## 范围 / 边界

**管：**

- swarmflow 引擎的分层契约（facade / seam / provider / primitives / backend）与移植边界。
- `TeamRole.WORKER` 的不变量与单轮执行契约。
- 结构化输出工具（`SubmitResultTool`）协议。
- 进度事件分类（`WORKFLOW_PROGRESS`）与 leader 旁观播报路径。
- 4 层 `WorkflowRun` 数据模型。
- 错误边界（引擎错误 vs 仓库 `StatusCode`）。

**不管：**

- 单 agent DeepAgent / ReAct 内部执行（见 harness）。
- 引擎原语的并发/resume 实现细节（与 `dw/wf` 一致，见该项目设计文档）。
- skill 全局安装（下一个 PR）。

## 引擎契约（`workflow/engine`）

- **业务无关**：engine 子包不得 import 任何 `openjiuwen.agent_teams` 业务模块（schema/agent/tools/...）。它只依赖 stdlib + 可选 pydantic/jsonschema。这保证可用 `MockBackend` 独立单测，也保证未来可与上游 `dw/wf` 同步。
- **脚本格式**：合法 Python 模块，顶层 `META={...}`(纯字面量，`ast.literal_eval` 强制) + `async def run(args)`(或 `run()`)。脚本用 `from swarmflow import agent, parallel, ...`（loader 运行时把 `swarmflow` 映射到 facade；`run_swarmflow` 额外映射 `jiuwenswarm.swarmflow`）。
- **接缝**：`agent()`/`parallel()`/`pipeline()`/... 经 contextvar provider 转发；`Provider` 实现可整体替换。唯一 IO 接缝是 `AgentBackend.run(prompt, opts, schema_json) -> AgentResult`。
- **可观测性**：`Runtime` 有两个 sink。`log_sink: Callable[[str], None]`（诊断文本，默认 no-op）；`progress_sink: Callable[[WorkflowProgressEvent], None]`（结构化进度，默认 no-op）。`phase()`/`log()`/`agent()` 起止发 `WorkflowProgressEvent`；引擎不读 wall-clock（保持 resume 确定性），事件**无时间戳**——消费方在 agent_teams 层补时。

## WORKER 不变量（`TeamRole.WORKER`）

1. **单轮、无状态、用完即弃**：一个 `agent()` 调用对应一个 worker；worker 跑一轮 DeepAgent 即销毁，上下文每次全新。
2. **不进 coordination 协作循环**：worker 不订阅消息总线、不认领任务、不多轮、不被 dispatcher 唤醒。它由 `TeamWorkerBackend` 直接 `create_deep_agent` + `Runner.run_agent` 执行，**不经** `TeamAgent.invoke` / `CoordinationKernel.start`。
3. **有 roster 身份**：`TeamWorkerBackend` 经 `spawn_member(role=WORKER, status=BUSY)` 开 DB row（member_name 形如 `wf-<label-slug>-<n>`，满足 `_MEMBER_NAME_PATTERN`），完成标 `SHUTDOWN`。row 操作 best-effort，失败不阻断执行。
4. **model**：worker 默认复用 leader 的 model（`TeamWorkerBackend(model=leader_agent.harness.model)`）。`agent(model="X")` 的 per-call hint 经注入的 `model_resolver` 回调解析——`run_swarmflow_background` 用 `resolve_member_model(team_spec, model_name="X", model_index=None)` 对 team model pool 做**纯位置查找**（无 allocator 轮转、无状态），命中则该 worker 用 pool 条目的 model。pool 未配 / 名字缺失 / 无 hint 一律回退 leader model。解析逻辑留在 team 层（`run_swarmflow_background`），`TeamWorkerBackend` 只持 `(name) -> Model | None` 回调，engine 对接层不耦合 pool/allocator 结构。

## 结构化输出工具协议（`SubmitResultTool`）

- harness 无原生结构化输出。当 `agent(prompt, schema=...)` 的 `schema_json` 非空：worker 装一个 `SubmitResultTool`，其 `ToolCard.input_params == schema_json`，`id = swarmflow.submit_result.<member_name>`（唯一，避免 `resource_mgr` 冲突）。
- worker system prompt 要求「完成后必须调用 `submit_result` 提交结构化结果」。工具 `invoke(inputs)` 捕获 `inputs` 到 `self.captured` 并标 `called`。
- `TeamWorkerBackend.run` 读 `submit_tool.captured` → `AgentResult(structured=...)`；若 worker 未调用工具则抛 `BackendError` → 引擎按 `retries` 重试 → 耗尽后 `agent()` 返回 `None`（dw 控制流容忍）。
- `schema_json` 为空时：worker 不装该工具，取最终自由文本 → `AgentResult(text=...)`。

## 进度事件与 leader 旁观（`WORKFLOW_PROGRESS`）

- 单一事件类型 `TeamEvent.WORKFLOW_PROGRESS` + `WorkflowProgressTeamEvent(kind, workflow_name, phase, label, outcome, text)`，`kind` 取引擎 `ProgressKind` 字符串值（一个 handler 方法渲染全部）。
- swarmflow 在 leader 进程内后台跑；`run_swarmflow_background` 的 observer 把进度 republish 成该事件，`sender_id="swarmflow"`（≠ leader member_name），故 `kernel` 的 self-filter 不拦截，leader 自己的 coordination 循环收到。
- `WorkflowHandler`（coordination 第 7 个 handler，仅监听 `WORKFLOW_PROGRESS`，leader-only）只渲染**里程碑**（`workflow_started` / `phase` / `workflow_completed`）→ `deliver_input(use_steer=True)`；per-agent 事件不播报（太频繁，归 4 层结构）。符合 coordination 铁律：事件只作为 leader 输入，不做决策。
- **leader stream 生命周期**：swarmflow 场景 leader 不建任务 → `is_team_completed` 首条「无任务返回 None」→ team 永不 auto-complete → leader stream 保持 idle 等 event，直到外部停止。

## 4 层数据模型（`WorkflowRun`）

`WorkflowRun(name, status, phases)` → `PhaseRecord(title, agents)` → `AgentActivity(label, prompt, activity, outcome, status)`。即 TUI 的 Phase ▸ agents ▸ {prompt, activity, outcome}。由 `build_workflow_run_from_events(events)` 折叠引擎进度事件流构建（对 `parallel`/`pipeline` 的交错事件健壮：按 `phase` 归组、按 label 匹配最近未完成 activity）。`preprocess_swarmflow`（`MockBackend`）零网络生成同结构供前端预览；`WorkflowObserver.to_frontend()` 是传输 stub（按需求留空）。

## 错误边界

引擎内部抛 `workflow.engine.errors.WorkflowError` 子类（`MetaError`/`LintError`/`SchemaError`/`BackendError`）。这些**限制在 `workflow/` 内部**；不得散播到 agent_teams 调用点。`swarmflow()` 工具边界捕获异常并转成 `ToolOutput(success=False, error=...)`（公共 seam）。后续若需对外抛错，在工具边界转 `StatusCode`/`raise_error`，不在引擎内引入仓库错误体系（保持引擎业务无关）。
