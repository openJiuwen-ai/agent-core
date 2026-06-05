# Agent Teams Workflow（Swarmflow 编排）

用一个 swarmflow 脚本（普通 Python 模块，`META={...}` + `async def run(args)`，用 `agent()`/`parallel()`/`pipeline()`/`phase()` 等原语）声明式编排多 agent 工作流，底层复用 agent_teams 基础设施。设计来龙去脉见 `docs/features/F_27`，运行时契约见 `docs/specs/S_18`。

## 模块地图

```
workflow/
├── engine/              # dw/wf 引擎的忠实移植——业务无关，可独立单测
│   ├── facade.py        # 脚本 import 的稳定原语面（agent/parallel/pipeline/phase/log/...）
│   ├── seam.py          # Provider 协议 + contextvar（引擎可整体替换的接缝）
│   ├── provider.py      # EngineProvider（把原语转发到 primitives）
│   ├── primitives.py    # 原语实现：call-path keys / 并发信号量 / journal；phase/log/agent 起止发 progress 事件
│   ├── journal.py       # content-addressed resume（结构化 call-path 键 + sig）
│   ├── loader.py        # AST 提取 META + 确定性 lint + importlib 导入
│   ├── schema.py        # agent(schema=) 解析/校验（dict / pydantic / None）
│   ├── progress.py      # WorkflowProgressEvent（业务无关、无时间戳）+ ProgressSink
│   ├── runtime.py       # Runtime：backend / journal / log_sink / progress_sink / 并发 cap
│   ├── runner.py        # run_workflow：装 provider、建 Runtime、发 workflow 起止事件
│   └── backends/{base,mock}.py  # AgentBackend 抽象 + 离线确定性 MockBackend
├── backends/
│   ├── team_worker_backend.py   # TeamWorkerBackend：把每个 agent() 映射成单轮 WORKER（核心对接）
│   └── submit_result_tool.py    # SubmitResultTool：worker 调它产出结构化结果
├── schema.py            # 4 层 WorkflowRun → PhaseRecord → AgentActivity{prompt,activity,outcome}
├── observer.py          # WorkflowObserver：累积 4 层 + 可选 on_event 回调（republish team 事件）；to_frontend stub
├── runner.py            # run_swarmflow（真实 worker）/ preprocess_swarmflow（MockBackend 预演）
└── tool_swarmflow.py    # SwarmflowTool：leader 工具，异步启动 + 立即返回
```

## 三条铁律

**铁律 1：`engine/` 保持业务无关。** engine 子包**不得** import 任何 `openjiuwen.agent_teams` 业务模块。只依赖 stdlib + 可选 pydantic/jsonschema。这是它能用 `MockBackend` 独立单测、并能与上游 `dw/wf` 同步的前提。业务对接（worker backend、observer→team 事件、swarmflow 工具）全部放 `workflow/` 顶层。

**铁律 2：结构化输出走工具，不靠提示词解析。** harness 无原生 `response_format`。worker 装 `SubmitResultTool`（`input_params=schema_json`），单轮完成后调它提交结果，backend 读 `captured`。引擎的 `retries` 兜底未调用/不合规的情况。

**铁律 3：worker 单轮、无协作、用完即弃。** WORKER 不进 coordination 循环（不订阅消息、不认领任务、不多轮）。`TeamWorkerBackend` 直接 `create_deep_agent` + `Runner.run_agent` 跑一轮，**不经** `TeamAgent.invoke`。leader 旁观靠「无任务→不 auto-complete→stream 保持」+ `WORKFLOW_PROGRESS` 事件唤醒播报。

## 跟其它子目录的边界

- 进度事件类型 `WORKFLOW_PROGRESS` / `WorkflowProgressTeamEvent` 在 `schema/events.py`；leader 播报由 `agent/coordination/handlers/workflow.py:WorkflowHandler` 消费。
- `TeamRole.WORKER` 在 `schema/team.py`；`enable_swarmflow` 在 `schema/blueprint.py`；leader 子模式提示词在 `prompts/{cn,en}/leader_swarmflow.md`（经 `sections.build_team_swarmflow_section` 注入）。
- swarmflow 工具的运行时依赖（leader model/messager/state）由 `TeamAgent.run_swarmflow_background` 封装，经 `_launch_swarmflow` 回调 thread 到 `SwarmflowTool`（不让工具层耦合 TeamAgent）。
