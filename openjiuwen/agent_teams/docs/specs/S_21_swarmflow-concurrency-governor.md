# Swarmflow 并发治理运行时规约

## 元信息

| 项 | 值 |
|---|---|
| 类型 | spec |
| 关联模块 | `workflow/concurrency.py`、`workflow/engine/admission.py`、`workflow/engine/cap.py`、`workflow/engine/runtime.py`、`workflow/engine/primitives.py`、`workflow/engine/runner.py`、`workflow/tool_swarmflow.py`、`workflow/runner.py`、`workflow/backends/team_worker_backend.py`、`harness/async_tools.py`、`harness/native_harness.py`、`schema/blueprint.py`、`agent/agent_configurator.py`、`rails/team_context.py`、`rails/team_tool_rail.py`、`tools/tool_factory.py`、`agent/coordination/handlers/workflow.py`、`i18n.py` |
| 最近一次修订日期 | 2026-07-01 |
| 关联 feature | `F_47_swarmflow-concurrency-governor.md`、`F_48_swarmflow-inline-script-execution.md` |

## 范围 / 边界

**管：**

- L1/L2/L3 三层 cap 定义、`ConcurrencyGovernor` admit/release 语义。
- engine `AgentAdmission` 协议与默认 `SemaphoreAdmission`。
- `TeamAgentSpec.swarmflow_concurrency` 配置与 build 校验。
- Swarmflow 多 run `run_id` 生成与透传、worker 命名前缀。
- Leader i18n 与 async 框架 `format_*` 终态注入分工。

**不管：**

- 跨 Team / 跨进程全局限制。
- workflow 脚本内 cap 配置（engine 铁律：脚本不感知）。
- Token 预算（`Runtime.budget_total`，独立机制）。
- journal 路径选择（仍 `(team, session, workflow_name)`，`run_id` 不参与路径）。

## 三层限制（不变量）

三层 cap 由 `ConcurrencyLimits`（`@dataclass(frozen=True)`，`workflow/concurrency.py`）承载：

| 层 | 字段 | 类型 | 默认 | 机制 | 命中行为 |
|----|------|------|------|------|----------|
| L1 | `max_workflows` | `int` | 16 | Governor 在飞 ticket 集合 | **拒绝**：`Swarmflow concurrent limit reached (n/m)` |
| L2 | `agents_per_run` | `int \| None` | `None` | 每 run 私有 `asyncio.Semaphore` | **阻塞** |
| L3 | `max_agents_total` | `int` | 64 | Governor 共享 global sem | **阻塞** |

**L2 有效 cap** 由 `resolve_agents_per_run_cap(agents_per_run: int | None, *, cap_override: int | None = None) -> int`
（`engine/cap.py`，build 期与运行期共享单一来源）解析，优先级 `cap_override > agents_per_run > min(16, cpu_count-2)`，
结果 `max(1, …)` clamp≥1：

- `cap_override` 非空 → `max(1, cap_override)`（单测 / engine `run_workflow(cap=N)` 走此路，绕过 CPU 启发）。
- 显式 `agents_per_run: int` → `max(1, agents_per_run)`。
- `agents_per_run: null` → `max(1, min(16, cpu_count - 2))`（`os.cpu_count()` 缺省按 4）。

build 期校验由 `validate_swarmflow_concurrency(limits: ConcurrencyLimits) -> int`（`workflow/concurrency.py`）
承担：校验通过则**返回 L2 有效 cap**（供 Governor 构造的 `agents_per_run_cap`）；违反任一约束经
`raise_error(StatusCode.AGENT_TEAM_CONFIG_INVALID, reason=...)` 抛 `ValidationError`（与 `blueprint.py`
同层配置校验一致），**不抬升**配置。约束共 5 条：`max_workflows≥1`、`max_agents_total≥1`、
`agents_per_run≥1`（若显式设置）、有效 L2≤L3、`max_agents_total≥max_workflows`。触发点两处，均仅
`enable_swarmflow=True` 时生效：① `TeamAgentSpec.build()` 内显式调用（fail-fast，与其它
`@model_validator(mode="after")` 校验并行）；② `agent_configurator` 在构造 Governor 前再次调用取 `l2_cap`。
正常路径只运行 build() 一次；agent_configurator 复用其语义并取返回值。

`RunAgentAdmission.acquire()`：**先 L2 后 L3**，退出逆序释放。

## engine 准入协议（`engine/admission.py`）

- **`AgentAdmission`**（`@runtime_checkable` Protocol）：仅要求 `acquire()` 返回 async 上下文
  管理器（阻塞占槽、退出释放）。engine `primitives.agent()` / `AgentSession._run` 唯一并发入口；
  脚本与 workflow 配置不感知。
- **`SemaphoreAdmission(cap: int)`**（`admission.py`）：单 sem，构造 `asyncio.Semaphore(max(1, cap))`，
  等价旧 `Runtime.sem`；无 Swarmflow 注入时的默认路径。
- **`RunAgentAdmission`**（`concurrency.py`，**不**进入 engine 包）：双 sem（per-run + global），
  由 Governor 在 `admit_workflow` 时 mint。

`Runtime.agent_gate: AgentAdmission | None`（`repr=False`，**替代** `Runtime.sem`）。
`Runtime.make_cap() -> int` 委托 `resolve_agents_per_run_cap(None, cap_override=self.cap_override)`（单一来源）。
`run_workflow(..., agent_gate: AgentAdmission | None = None)` 透传给 Runtime 构造，**不再**在 runner 内创建 sem。
`cap=` 仍映射 `cap_override`（仅 fallback）。

未注入时 lazy 构造：模块级 `_resolve_agent_gate(rt)`（`primitives.py`）在 `rt.agent_gate is None` 时
构造 `SemaphoreAdmission(rt.make_cap())` **并赋回 `rt.agent_gate`**（safety net，保留旧默认语义）。
`agent()` 与 `AgentSession._run` 均改为 `gate = _resolve_agent_gate(rt); async with gate.acquire():`——
默认路径行为与旧 `async with rt.sem` 完全一致（back-compat，`MockBackend` / `preprocess_swarmflow` /
旧测试不受影响）。

## `ConcurrencyGovernor` 契约

`ConcurrencyGovernor(limits: ConcurrencyLimits, *, agents_per_run_cap: int)`（`workflow/concurrency.py`，
per-Leader 单例）。`agents_per_run_cap` 由 `validate_swarmflow_concurrency` 返回值传入，每次 `admit_workflow`
用它 mint 一个新的 per-run `Semaphore(agents_per_run_cap)`，与共享 `_global_sem(Semaphore(max_agents_total))`
组成两层闸门。

- `admit_workflow()` → `WorkflowAdmission(ticket, agent_gate)` 或 `None`（L1 满）。
- `release_workflow(ticket)` → 从在飞 ticket 集合移除该 ticket。
  - 正常：`run_background.finally`。
  - 异常：`invoke` 在 `launch_async_tool` 抛错时 release；与 finally **互斥**（成功 launch 只走 finally）。
  - **幂等**：`discard` 语义——重复 release 同一 ticket、或 release 一个从未 admit 的 ticket 均为
    no-op，不会多放 L1 槽位。L1 计数恒等于在飞 ticket 集合大小，不会为负、不会与 ticket 状态失步
    （机制级防双 release，不依赖调用约定）。集合大小被 `max_workflows` 严格限制（有界）。
- `snapshot()` → `ConcurrencySnapshot`（`active_workflows`、`max_workflows`、`max_agents_total`、
  `agents_per_run`）；调试只读，无对外 API。

## `SwarmflowTool` 契约

### `invoke`

0. 校验 script source（`script_path` / `script` / `name` / `resume_id` 四源 one-of，**`script_path`（磁盘）
   与 `script`（内联源码）当下可执行**，`name` / `resume_id` 返回 "not supported yet"）；四源全缺返回
   `ToolOutput(success=False)`。内联 `script` 在 `invoke`（admit 之后）落盘到 `workflows/{META.name}/script.py`、
   把绝对 `script_path` 回填 enriched + 进 launched 回执后复用 path-based 加载；materialize 失败先 release ticket 再返回错误。
1. `governor is None` → `ToolOutput(success=False, error="Swarmflow concurrency governor is not configured")`。
2. `admit_workflow()` → `None` → 拒绝，**不生成** `run_id`。
3. `new_swarmflow_run_id()` → `wf_{12hex}`（`tool_swarmflow.py` 模块级函数，**仅在 invoke 铸造**；
   `run_background` 只读 inputs 中的 run_id，不重新生成——并行 run 各自独立）。
4. `task_id = generate_id(self.card.name)`（AsyncTool 统一 ID，与 run_id 正交——resume 会换新 task_id）。
5. enrich inputs：复制为可变 `enriched`，注入 4 个内部键（见下）。
6. `launch_async_tool(..., format_completed=..., format_failed=...)` 闭包捕获 `run_id` + `completion_ctx`；
   launch 抛异常 → `release_workflow(ticket)`（与 finally 互斥）+ `ToolOutput(error="Internal error: {exc}")`。
7. `map_result` → `swarmflow.launched`（`run_id` + `task_id`）。

Leader 并行局数只认 `run_id`，不认 `task_id`（resume 会换新 `task_id`）。

**四个对外方法**（`tool_swarmflow.py`）：

- `map_result(output) -> str`：启动期同步回执，成功调 `format_launched_message`，失败回退 `output.error`。
- `format_launched_message(run_id, task_id) -> str`：`swarmflow.launched`（显式区分 run_id 与 task_id 用途）。
- `format_completed_injection(result, *, run_id, completion_ctx=None) -> str`：终态成功文本，`swarmflow.completed`。
- `format_failed_injection(error, *, run_id) -> str`：终态失败文本，`swarmflow.failed`。

`run_background` 现返回 `await run_swarmflow(...)` 的**原始脚本结果**（不再内部拼接字符串）；
格式化职责移到 `format_completed_injection`，由框架经 `format_completed` 回调调起。

### 内部键常量（enrich inputs + completion_ctx 管线）

`tool_swarmflow.py` 模块级常量，进/出 inputs 的键名：

| 常量 | 值 | 写入处 | 读取处 |
|---|---|---|---|
| `_RUN_ID_INPUT_KEY` | `"_run_id"` | invoke | `run_background` |
| `_WORKFLOW_TICKET_KEY` | `"_workflow_ticket"` | invoke | `run_background`（finally release） |
| `_AGENT_GATE_KEY` | `"_agent_gate"` | invoke | `run_background`（透传 `run_swarmflow`） |
| `_COMPLETION_CTX_KEY` | `"_completion_ctx"` | invoke | `run_background` |
| `_OBSERVER_CTX_KEY` | `"observer"` | `run_background` | `format_completed_injection` |

**completion_ctx 管线**（per-run summary 隔离）：invoke 创建空 `dict` 写入 `enriched[_COMPLETION_CTX_KEY]`；
`run_background` 在构造 `WorkflowObserver` 后写 `completion_ctx[_OBSERVER_CTX_KEY] = observer`；
invoke 构造的 `_format_completed` 闭包**按引用捕获该 dict**，故后台 run 结束时 `format_completed_injection`
经 `completion_ctx.get(_OBSERVER_CTX_KEY)` 取到的是**该 run 自己的 observer**，`summarize_run(observer.run)`
只反映该 run——多 run 并行互不污染。

### `run_background` / `_relaunch`

- `run_swarmflow(..., run_id=, agent_gate=)`；`finally` 里 `release_workflow(ticket)`（governor 非 None 时），
  覆盖正常 / 异常 / `CancelledError`（`WorkflowAborted` 被 except 转为 `CancelledError` 重抛，故 pause 退出也触发 finally）。
- **`_relaunch`（resume，`F_43`）**：`launch_async_tool(同一 inputs)`，**不**二次 `admit_workflow`；
  复用 inputs 内 `_workflow_ticket` / `_agent_gate`。因 pause 退出时 `run_background.finally` 已 `discard`
  该 ticket（幂等），resume 运行期间**实际不再持有 L1 槽**——resume 的 finally 再 release 同一 ticket 仅为幂等 no-op
  （ticket 已不在集合内）。这是设计如此：resume 是原 run 的延续，不是新 admit。

### Worker 命名（取代 `F_44` 的 `wf-{label}-` 默认）

`TeamWorkerBackend.__init__(..., run_id: str | None = None)`；`_run_id_prefix(run_id)` staticmethod 对完整 run_id
做 `_SLUG_RE.sub("-", run_id.lower()).strip("-")`（**不截断**），空则返回 `None`。`_next_member_name` 两分支
都先 `slug = _SLUG_RE.sub("-", label.lower()).strip("-") or "worker"`：

- 有 `run_id`：`{run_prefix}-{slug}-{n}`（run_prefix = 完整 run_id slug 化，不截断；slug 为 label slug 化）。
- 无 `run_id` 回退：`wf-{slug}-{n}`。

不同 run_id 因前缀不同故成员名不碰撞；`n` 仍为 per-backend 同步自增计数器。

## 配置（`TeamAgentSpec.swarmflow_concurrency`）

```yaml
# team spec（agent-core blueprint / jiuwenswarm config_loader 透传）
enable_swarmflow: true
swarmflow_concurrency:
  max_workflows: 16      # L1，同时后台 workflow 数
  agents_per_run: null   # L2；null = min(16, cpu_count-2)
  max_agents_total: 64   # L3，该 Leader 下全局 agent 槽
```

`enable_swarmflow: false` 时不构造 Governor、不校验、不注入 swarmflow 工具。

## i18n 与 Leader 旁观分工

| Key | 阶段 | 含 `{run_id}` | 通道 |
|-----|------|---|------|
| `swarmflow.launched` | 启动 | ✅ `{run_id}` + `{task_id}` | `map_result`（当前 tool 轮闭合文案） |
| `workflow.started` | 中途 | ✅ `{run_id}`（置于 `{name}` 前） | `WorkflowHandler` ← `WORKFLOW_PROGRESS` |
| `workflow.phase` | 中途 | ✅ `{run_id}`（置于 `{phase}` 前） | 同上 |
| `swarmflow.completed` | 终态 | ✅ `{run_id}` + `{result}` | async `_run` → `format_completed` 闭包 → `harness.send` |
| `swarmflow.failed` | 终态 | ✅ `{run_id}` + `{error}` | async `_run` → `format_failed` 闭包 → `harness.send` |
| `workflow.human_prompt` | 中途（人工等待） | ❌ 仅 `{label}`/`{prompt}`/`{corr}` | `WorkflowHandler` ← `WORKFLOW_PROGRESS` |
| `workflow.human_replied` | 中途（人工回复） | ❌ 仅 `{label}` | 同上 |
| `async_tool.completed`/`failed`/`launched` | 终态（无回调时） | ❌ 仅 `tool` + `result`/`error` | async `_run` 默认回退 |

中途 milestone 与终态完成 **分离**：handler **不** narrate `workflow_completed` / 失败；
终态不经 handler（`S_20` 两段式）。

`format_completed` 内组装 `summarize_run(observer.run)` + `render_result_text(脚本返回值)`。

**已知 run_id 覆盖缺口**：`workflow.human_prompt` / `workflow.human_replied`（人工交互节点）与
`async_tool.*`（无回调时的默认回退）**不含** `{run_id}` 占位符。`WorkflowHandler` 对 started/phase
传 `run_id=payload.run_id or "?"`（缺失回退 `"?"` 保 format 不报 KeyError），对 human_prompt /
human_replied **故意不传 run_id**——人工交互靠 `correlation_id` 路由回执（corr 跨 resume 稳定、与人一一对应），
run_id 在此无叙事价值。后果：多 run 并行时，这几类消息无法从文案本身区分来源 run（started/phase/终态可区分）。
见「已知遗留」。

## async 框架衔接（`S_20`）

- 新增导出类型别名（`harness/async_tools.py` `__all__`）：`CompletionFormatter = Callable[[Any], str | None]`、
  `FailureFormatter = Callable[[str], str | None]`。
- `AsyncToolRecord.format_completed` / `format_failed` 可选回调字段；`AsyncToolRuntime.launch(...)` 与
  `NativeHarness.launch_async_tool(...)` 同名 kwargs 透传。
- `AsyncToolRuntime._run`：**回调存在且回调返回非 None** 则用回调文本 inject；回调返回 `None`（或未设回调）
  回退 `async_tool.completed` / `async_tool.failed`（**默认文案不含 run_id**，仅 `tool` + `result`/`error`）。
  Swarmflow 经闭包注入 `swarmflow.completed` / `swarmflow.failed`（含 run_id）；其它 AsyncTool 不提供回调，
  故其终态文案无 run_id。
- `has_running(tool_name)`：registry 是否有 running 记录；**不作** L1 门禁。

## 错误语义

| 场景 | 层 | 行为 |
|------|-----|------|
| build 期并发配置非法（`max_workflows<1` / `max_agents_total<1` / `agents_per_run<1` / L2>L3 / L3<L1） | — | `raise_error(StatusCode.AGENT_TEAM_CONFIG_INVALID)` → `ValidationError` |
| L1 满 | L1 | `ToolOutput(success=False)`，文案 `(n/m)`，无 `run_id` |
| governor 未装配 | — | `ToolOutput(success=False)` |
| `launch_async_tool` 抛错 | L1 | invoke 内 `release_workflow`（幂等）+ `Internal error: ...` |
| L2/L3 满 | L2/L3 | `agent()` 内 `acquire()` 阻塞，不上抛到工具层 |
| worker backend 失败 | engine | `BackendError`（engine 内部异常）→ `_attempt_calls` retry → 耗尽 `agent()` 返 `None`；**不**穿透到工具层，不转 StatusCode |
| 后台 run 失败 | — | `format_failed` → inject；L1 在 `finally` release |
| pause（`F_43`） | L1 | pause 停 task 但 `WorkflowAborted → CancelledError` 触发 `run_background.finally`，**立即** release ticket；resume（`_relaunch`）复用同 ticket 但不重新 admit，故 resume 期间**不**占 L1 槽（resume 的 finally 再 release 为幂等 no-op） |

引擎内部 `WorkflowError` / `BackendError` 等仍限制在 `workflow/` 内（`S_18` 错误边界、
engine 业务无关铁律）；工具边界转 `ToolOutput` 或 async 失败注入。**仅 build 期配置校验**
走统一 `StatusCode` 体系（`concurrency.py` 在 `workflow/` 顶层，非 engine 子包）。

## 装配链

```
TeamAgentSpec.swarmflow_concurrency  (ConcurrencyLimits，default_factory)
  → build() 内 validate_swarmflow_concurrency(...)（enable_swarmflow=True，fail-fast）
  → agent_configurator（仅 role=LEADER 且 enable_swarmflow）
       → l2_cap = validate_swarmflow_concurrency(spec.swarmflow_concurrency)  # 复用语义并取返回值
       → ConcurrencyGovernor(spec.swarmflow_concurrency, agents_per_run_cap=l2_cap)
       → + swarmflow_model_resolver
  → inject_team_handles(SWARMFLOW_CONCURRENCY_GOVERNOR, SWARMFLOW_MODEL_RESOLVER)
  → build_team_tool_rail → get_swarmflow_concurrency_governor(context) → TeamToolRail(swarmflow_concurrency_governor=)
  → TeamToolRail._build_tools → create_team_tools(concurrency_governor=)
       → SwarmflowTool(concurrency_governor=..., model_resolver=...)
       → gate: model_resolver is not None（governor 与 resolver 同批注入，未单独 gate governor）
```

Governor 为 **per-Leader 单例**，与 `AsyncToolRuntime` 同作用域，不跨 Leader 共享。
`governor is None` 时 invoke 返回 `Swarmflow concurrency governor is not configured`。

jiuwenswarm：`jiuwenswarm/agents/harness/team/config_loader.py` 可选透传 YAML 块。

## 与其它 spec / feature 的关系

- 引擎分层 / worker 单轮 / journal：`S_18_swarmflow-engine-and-worker.md`（本规约补充 L1–L3、
  `agent_gate`、`run_id` 命名；journal 路径不变）。
- 异步工具两段式 / `format_*`：`S_20_native-harness-async-tool-framework.md`。
- pause/resume 不二次 admit：`F_43_swarmflow-pause-resume.md`。注：F_43 原设计意图为「pause 期间 L1 槽仍被占用」，
  但现行 `run_background.finally` 对 `WorkflowAborted→CancelledError` 也会 release（见「错误语义」pause 行），
  故实际 pause 退出即释放 L1，resume 期间不占槽——二者在「resume 不重新 admit」上一致。
- worker 非 teammate、去 `team_backend`：`F_44_swarmflow-worker-not-teammate-no-db.md`。

## 已知遗留

- `ConcurrencyGovernor.snapshot()` 未暴露 Monitor / TUI HTTP API。
- L3 长等待无 debug 日志（阻塞在 sem 上时无可观测排队深度）。
- pause 多 run 并发场景 e2e 仅 controller 逻辑覆盖，未专门压测 L1 与 pause 交错。
- **i18n run_id 覆盖缺口**（见「i18n 与 Leader 旁观分工」）：`workflow.human_prompt` /
  `workflow.human_replied` / `async_tool.*` 不含 `{run_id}`，多 run 并行时这几类消息无法从文案区分来源 run。
  human_* 靠 `correlation_id` 路由（设计如此，不计划补 run_id）；`async_tool.*` 是无回调时的默认回退，
  swarmflow 已用 `swarmflow.*` 覆盖，其它 AsyncTool 暂无 run_id 概念。
