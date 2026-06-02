# Swarmflow 多 Agent 工作流编排

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-06-02 |
| 范围 | 新增 `workflow/`（引擎移植 + worker backend + observer + 4 层 schema + swarmflow 工具）；`schema/team.py`（`TeamRole.WORKER`）、`schema/events.py`（`WORKFLOW_PROGRESS` + `WorkflowProgressTeamEvent`）、`schema/blueprint.py`（`enable_swarmflow`）、`agent/state.py`、`agent/team_agent.py`、`agent/agent_configurator.py`、`agent/coordination/handlers/workflow.py` + `dispatcher.py`、`rails/team_policy_rail.py`、`prompts/sections.py` + `prompts/{cn,en}/leader_swarmflow.md`、`tools/tool_factory.py` + `tools/tool_permissions.py`、`i18n.py` |
| 测试基线 | `tests/unit_tests/agent_teams/workflow/` → 16 passed；联合 `workflow/ + test_team_tools + test_coordination_loop + prompts/rails` → 179 passed, 14 skipped |
| Refs | `#751` |

## 背景

参考独立项目 `dw/wf` —— 一套成熟的确定性多 agent 工作流编排引擎（facade → seam(contextvar) → provider → primitives(call-path keys / 并发信号量 / journal resume) → `AgentBackend` 唯一 IO 接缝）。目标：让用户用一个 swarmflow 脚本（普通 Python 模块，`META={...}` + `async def run(args)`，用 `agent()`/`parallel()`/`pipeline()`/`phase()` 等原语）声明式编排多 agent 工作流，底层复用 agent_teams 基础设施。

核心洞察：dw 引擎是**业务无关**的（dw 团队特意做 facade/seam 分层就是为了被替换后端）。因此本特性 = **移植引擎核心 + 只改造两个对接点**：① 真实 backend 接到 worker 执行；② 引擎进度回调改成发 team event 给 leader 旁观播报。

## 架构与关键决策

### 分层

- `workflow/engine/`：dw/wf 的忠实移植（seam / provider / primitives / journal / loader / schema / aliases / errors / runtime / runner / facade + backends/{base,mock}）。**完全不依赖 agent_teams**，可用 `MockBackend` 独立单测。仅两处偏离原版：引擎内新增 `progress.py`（业务无关的 `WorkflowProgressEvent`，无时间戳以保持确定性），`primitives.phase/log/agent` 起止经 `runtime.progress_sink` 发结构化事件；`runtime.log_sink` 默认 no-op（不 `print`）。
- `workflow/backends/team_worker_backend.py`：`TeamWorkerBackend(AgentBackend)`，把每个 `agent()` 调用映射成单轮 worker。
- `workflow/{schema,observer,runner,tool_swarmflow}.py`：4 层 `WorkflowRun` 模型、进度 observer、`run_swarmflow`/`preprocess_swarmflow` 入口、leader 工具。

### 决策 1 — Worker = 简化成员，但走**方案 Y**（backend 直接构造轻量 DeepAgent）

`TeamRole.WORKER` 是单轮、无状态、用完即弃的执行角色（有 member_name/roster 身份、占 DB row，但**不进 coordination 协作循环**）。实现上，`TeamWorkerBackend.run()` 每次直接 `create_deep_agent(...)` 跑一轮（`Runner.run_agent`），并通过 `spawn_member(role=WORKER)` 开一个 roster row（执行期 BUSY、完成标 SHUTDOWN，best-effort）。worker 执行抽到可 override 的 `_execute_worker` 以便脱离 LLM 单测。

### 决策 2 — 结构化输出走**工具**（用户校正）

harness **无原生** `response_format`/`tool_choice`/结构化输出机制。故 worker 装一个动态 `SubmitResultTool`，其 `ToolCard.input_params` = 引擎传来的 `schema_json`；worker 单轮完成后**必须调用** `submit_result` 提交结果，工具 `invoke` 捕获入参作为 `AgentResult.structured`。这是 Claude 强制工具调用做结构化输出的标准模式，schema 由工具入参强制，可靠且隔离。

### 决策 3 — leader 旁观播报：事件唤醒 + 静态子模式 section

`swarmflow()` 工具异步启动后台运行（`TeamAgent.run_swarmflow_background`），引擎进度经 observer republish 成 `WORKFLOW_PROGRESS` team event，`sender_id="swarmflow"`（≠ leader member_name，绕过 `kernel` 的 self-filter），leader 自己的 coordination 循环收到 → `WorkflowHandler` 渲染 phase/起止里程碑 → `deliver_input` → leader 流式播报。**leader 不建任务 → `is_team_completed` 第一条「无任务返回 None」→ 永不 auto-complete → stream 自然保持 idle 等 event**（无需改 completion gating）。leader 子模式用 `enable_swarmflow` 静态注入 `leader_swarmflow.md` section（含关键字触发 + 旁观语义 + 不建任务约束）。

### 决策 4 / 5

`swarmflow(script_path, args)` 显式传脚本路径（复用引擎 `load_workflow_source`，为下期 skill 全局安装留接口）；本期端到端 MVP 真做（worker 真实 LLM 对接、事件播报、预处理生成 4 层 JSON），仅「4 层 JSON 传给前端 TUI 的通道」按需求留 `WorkflowObserver.to_frontend` stub。

## 拒绝的方案

- **方案 X：worker 走完整 `TeamAgent.run_worker_once` + `Runner.run_agent_team(worker=True)` + configurator WORKER 分支** —— 要在 configure/Runner/TeamAgent 三处加 WORKER 特殊分支，每次 `agent()` 走完整 messager/backend/workspace 装配（重），且 per-call `SubmitResultTool` 注入侵入装配链。方案 Y（backend 直接 `create_deep_agent`）更简单、隔离、天然「用完即弃」。
- **提示词要求返回 JSON + 解析重试** 做结构化输出 —— 不可靠（LLM 包 prose/fences）；被决策 2（工具方案）取代。
- **新增静态 `team_mode="swarmflow"`** —— `team_mode` 是 build-time 静态选择；改用 `enable_swarmflow` 门控静态 section + 关键字让 LLM 自行触发（与 `build_team` 触发一致，不硬编码正则）。
- **in-process 直接回调播报** —— 绕过 coordination，但 leader stream 生命周期保持困难；事件方案复用 coordination 的「event 唤醒 idle leader」，stream 由 coordination 维持。
- **新增 `WorkerAgent` 类** —— 违反铁律「TeamAgent 单一实现」。
- **LLM 可见的 `SpawnWorkerTool`** —— worker 由引擎执行脚本时 spawn，非 LLM 决策；引擎直接用 `spawn_member(role=WORKER)`，LLM 面只暴露 `swarmflow()`。

## 已知遗留

- **真实 LLM 端到端未自动验证**：单测覆盖到「链路接通」（引擎 MockBackend、worker backend override `_execute_worker`、handler/tool spy、section 注入）。真实 leader + worker LLM 的端到端跑通需手动/系统测试验证（`os.getenv(..., "mock-api-key")` 兜底）。
- **worker roster 膨胀**：每个 `agent()` 开一个 WORKER row（完成标 SHUTDOWN，不删）。大工作流会累积 row。后续可加 worker pool 或完成即删 row。
- **进度播报 sync→async 用 `asyncio.create_task` fire-and-forget**：`progress_sink` 是同步的，republish 经 `create_task` 调度，phase 事件不频繁故乱序风险低，但非严格有序。后续可改 queue + 消费协程保证顺序。
- **worker system prompt 内联英文**（`team_worker_backend.py`），未走 i18n / prompts；worker 复用 leader model，未支持 per-worker model spec（`TeamAgentSpec.worker` 字段留待需要时加）。
- **leader 子模式用静态 section 而非 dynamic probe**：`swarmflow_active` 仅用于 `_launch` 防重入，未驱动运行时提示词切换（静态 section 已够用）。

## 验证

```bash
source .venv/bin/activate && export PYTHONPATH=.:$PYTHONPATH
make test TESTFLAGS="tests/unit_tests/agent_teams/workflow/"
```

端到端手动验证：构造 `TeamAgentSpec(enable_swarmflow=True, spawn_mode="inprocess", ...)`，leader 收到含 `swarmflow <script>` 的消息 → 调 `swarmflow()` 工具 → 后台跑（worker 真实 LLM）→ 观察 leader 流式播报各 phase 进展 + 产出 4 层 `WorkflowRun`。离线预演：`preprocess_swarmflow(script)` 用 `MockBackend` 零网络生成 4 层结构。
