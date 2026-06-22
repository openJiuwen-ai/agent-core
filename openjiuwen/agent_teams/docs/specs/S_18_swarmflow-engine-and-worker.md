# Swarmflow 引擎与 Worker 运行时规约

## 元信息

| 项 | 值 |
|---|---|
| 类型 | spec |
| 关联模块 | `workflow/`（engine / backends / observer / schema / runner / tool_swarmflow）、`schema/team.py`、`schema/events.py`、`schema/blueprint.py`、`agent/team_agent.py`、`agent/coordination/handlers/workflow.py`、`rails/team_policy_rail.py`、`prompts/sections.py` |
| 最近一次修订日期 | 2026-06-22 |
| 关联 feature | `F_27_swarmflow-workflow-orchestration.md`、`F_31_swarmflow-per-call-model-routing.md`、`F_35_native-harness-async-tool-framework.md`、`F_37_swarmflow-stateful-sessions-and-human.md`、`F_38_swarmflow-journal-persistence.md`、`F_39_swarmflow-agent-worktree-isolation.md`、`F_39_swarmflow-e2e-hardening.md`、`F_40_swarmflow-journal-wal-and-program-order.md`、`F_42_swarmflow-tool-claude-code-alignment.md` |

## 范围 / 边界

**管：**

- swarmflow 引擎的分层契约（facade / seam / provider / primitives / backend）与移植边界。
- `TeamRole.WORKER` 的不变量与单轮执行契约。
- 结构化输出工具（`StructuredOutputTool`）协议。
- 进度事件分类（`WORKFLOW_PROGRESS`）与 leader 旁观播报路径。
- 4 层 `WorkflowRun` 数据模型。
- resume journal 的落盘路径与 `run_swarmflow` 接线契约。
- 错误边界（引擎错误 vs 仓库 `StatusCode`）。

**不管：**

- 单 agent DeepAgent / ReAct 内部执行（见 harness）。
- 引擎原语的并发/resume 实现细节（与 `dw/wf` 一致，见该项目设计文档）。
- skill 全局安装（下一个 PR）。

## 引擎契约（`workflow/engine`）

- **业务无关**：engine 子包不得 import 任何 `openjiuwen.agent_teams` 业务模块（schema/agent/tools/...），以保证可用 `MockBackend` 独立单测、并与上游 `dw/wf` 同步。约束的是**业务耦合**，不是依赖面：engine 可用通用第三方库（`aiofiles` 异步文件 I/O、可选 `pydantic`/`jsonschema`）。"仅 stdlib" 是 **swarmflow 脚本**(外部用户代码)的约束,不是引擎的。
- **脚本格式**：合法 Python 模块，顶层 `META={...}`(纯字面量，`ast.literal_eval` 强制) + `async def run(args)`(或 `run()`)。脚本用 `from swarmflow import agent, parallel, ...`（facade 模块导入时一次性把唯一包名 `swarmflow` 注册进 `sys.modules` 指向 facade；进程内固定映射、无 per-run 安装/卸载，故顶层 import 与 `run` 体内延迟 import 同样生效）。
- **接缝**：`agent()`/`parallel()`/`pipeline()`/`agent_session()`/`human_session()`/`human()`/... 经 contextvar provider 转发；`Provider` 实现可整体替换。IO 接缝是 `AgentBackend`：单轮 `run(prompt, opts, schema_json) -> AgentResult`，加可选的有状态会话四方法 `open_session` / `send_turn` / `close_session` / `aclose`（默认 `NotImplementedError` / no-op，单轮 backend 不实现也不受影响）+ `KNOWN_OPTIONS`（backend 自声明的 options 白名单扩展）。
  `agent()` 的 option 集合包含 `label` / `phase` / `schema` / `model` /
  `timeout` / `isolation`。`isolation` 当前只允许 `None` 或 `"worktree"`；
  engine 只校验与透传，具体隔离语义由 backend 实现。
- **可观测性**：`Runtime` 有两个 sink。`log_sink: Callable[[str], None]`（诊断文本，默认 no-op）；`progress_sink: Callable[[WorkflowProgressEvent], None]`（结构化进度，默认 no-op）。`phase()`/`log()`/`agent()` 起止发 `WorkflowProgressEvent`；引擎不读 wall-clock（保持 resume 确定性），事件**无时间戳**——消费方在 agent_teams 层补时。
- **嵌套 workflow 的深度守卫是 per-task，不是全局**：`workflow()` 的递归封顶用 `primitives._wf_depth`（**contextvar**，`_MAX_WORKFLOW_DEPTH=1`），不是共享 `Runtime` 计数器。contextvar 随 asyncio Task 按值拷贝，故 `parallel`/`pipeline` 各分支继承父深度、推进自己的副本——**同层并发的多个 `workflow()` 全部放行**（并发子工作流），而真正的递归（子工作流 `run()` 内再调 `workflow()`，同一 Task）看到已自增的深度被拦（返回 `None` + 日志）。曾用共享 int 会把"嵌套深度"与"并发数"混为一谈，导致并发兄弟 `workflow()` 被静默跳过。详见 `F_39`。
- **单次 fan-out 上限**：`parallel(thunks)` / `pipeline(items, ...)` 入口校验长度 ≤ `_MAX_FANOUT`（4096），超出抛 `WorkflowError`——显式报错而非静默截断（对齐参考工具的单次上限）。
- **`agent()` 的 CC 对齐参数（接口就位、执行留空）**：`agent(..., isolation='worktree', agent_type=...)` 经 `_ENGINE_OPTIONS` 接受并透传至 backend，但参考引擎暂不据其改变执行（不起 worktree 隔离、不解析具名专家 agent），`call_signature` 暂不纳入二者。对齐 Claude Code Workflow 工具表面，便于脚本针对完整 API 编写。详见 `F_42`。

## WORKER 不变量（`TeamRole.WORKER`）

1. **单轮、无状态、用完即弃**：一个 `agent()` 调用对应一个 worker；worker 跑一次即销毁，上下文每次全新。
2. **worker = 没有团队工具的 teammate**：`TeamWorkerBackend` 从 team 的 **teammate spec**（缺失则 leader spec，经 `agent_configurator` → `inject_team_handles` 的 `SWARMFLOW_WORKER_BASE_SPEC` 注入）`model_copy` 派生 worker `DeepAgentSpec`——保留 teammate 能力（model / tools / skills / workspace / sys_operation / **todo 规划 `enable_task_planning` / `enable_task_loop`**），但因 team rail 是装配期注入、原始 spec 不含，worker 天然无团队协作工具。每个 worker 是一个 `TeamHarness(role=WORKER)`。
3. **不进 coordination 协作循环**：worker 不订阅消息总线、不认领任务、不被 dispatcher 唤醒。它经 **`TeamHarness.run_once`** 执行——`run_once` = `DeepAgent.invoke`（按 spec 的 `enable_task_loop` 自动单轮或自驱 task-loop），**不开 supervisor → 无 steer / 无 outputs 流**，返回值与 `Runner.run_agent` 一致；**不经** `TeamAgent.invoke` / `CoordinationKernel.start`。结束 `harness.dispose()` 释放 sys_operation（工具由 `run_once` 的 `teardown_tools` 自动清理）。
4. **有 roster 身份**：`TeamWorkerBackend` 经 `spawn_member(role=WORKER, status=BUSY)` 开 DB row（member_name 形如 `wf-<label-slug>-<n>`，满足 `_MEMBER_NAME_PATTERN`，也用作 worker card / owner id），完成标 `SHUTDOWN`。row 操作 best-effort，失败不阻断执行。
5. **model**：worker 默认继承 base spec（teammate/leader）的 `model`（`TeamModelConfig`）。`agent(model="X")` 的 per-call hint 经注入的 `model_resolver` 回调解析为**配置而非实例**——`agent_configurator` 在 leader+`enable_swarmflow` 时构造闭包，用 `resolve_member_model(ctx.team_spec, model_name="X", model_index=None)` 对 team model pool 做**纯位置查找**（无 allocator 轮转、无状态），返回 `TeamModelConfig`；命中则覆盖 worker spec.model，未命中（pool 未配 / 名字缺失 / 无 hint）返回 `None` → 继承 base spec model。resolver 经 `BuildContext.extras` 的 `SWARMFLOW_MODEL_RESOLVER` 注入，`TeamWorkerBackend` 只持 `(name) -> TeamModelConfig | None` 回调，engine 对接层不耦合 pool/allocator 结构。
6. **worktree isolation**：`agent(isolation="worktree")` 给该 worker 创建
   owner-scoped worktree。`TeamWorkerBackend` 调
   `WorktreeManager.create_owner_worktree(slug)`，slug 固定为
   `agent-{team_name}-{worker_member_name}-{hash8}`；worker 的
   `WorkspaceSpec.root_path` 覆盖为 `worktree_path`，`stable_base=False`，且不
   注册到 `cleanup_path`。worker 完成后检查变更：干净则
   `remove_worktree`，有修改 / 有提交 / 状态不可确认则保留 worktree 给 leader
   后续集成。`agent()` 返回值保持 worker 原始输出，不附加 worktree path / branch；
   后续集成阶段由明确的 merge agent 基于真实仓库的 git 状态、`git worktree list`、
   分支和提交信息完成提交、合并和冲突处理。

## Resume Journal 持久化（`run_swarmflow`）

引擎 `run_workflow` 暴露 `resume`（读旧）/ `journal_path`（写新）两个入参（content-addressed
journal，`engine/journal.py`，JSONL 格式）。集成层 `run_swarmflow` 把两者指向**同一**文件，
使同一 `(team, session, workflow)` 的再次运行命中缓存、跳过未变的 agent 调用。

- **落盘路径**（单一真相源 `paths.py`）：
  `{team_home}/sessions/{session_id}/workflows/{workflow_name}/journal.jsonl`。
  `session_id` / `workflow_name` 经 `_safe_segment` sanitize（`[^A-Za-z0-9_.-]` 折成 `_`、
  strip 首尾分隔符）防目录穿越；`session_id` 为空回退 `"default"`。`Journal.save` 不建父目录，
  故 `run_swarmflow` 先 `mkdir(parents=True)`。
- **workflow_name 必填**：由脚本 `META["name"]` 提供，经 `load_workflow_meta`（纯 AST 取 META，
  **不** importlib 导入脚本）在调 `run_workflow` 前读取；缺失 →
  `raise_error(StatusCode.AGENT_TEAM_CONFIG_INVALID)`。
- **resume = journal_path 同路径**：首跑文件不存在 → 空 prior（冷启动）；跑完 `finalize` 写入；
  次跑命中 → cache-hit 短路。`preprocess_swarmflow`（MockBackend 预演）**不**落 journal。

### 落盘顺序、WAL 与异步 I/O（`F_40`）

- **program order 落盘**：`save` 按**结构序号**(`_program_order`：每段 call-path 的序号 + 子索引
  数值元组)排序写出,文件逐行即脚本执行顺序(构思→征询嘉宾→…),且因序号确定而**字节稳定**
  (与并发完成时序无关)。不再按 key 字符串字典序。
- **WAL 崩溃恢复**：journal 有 sidecar WAL `<journal>.wal`(引擎内由 `journal_path + ".wal"`
  派生)。`use` 对**新鲜**记录(cache-miss)立即 append 写 WAL(cache-hit 复用 prior 对象、
  不重写);`load` 先读 journal、再用 WAL **覆盖/补全**(WAL 较新,last-wins),并**容忍尾部
  半行**(崩溃中途 append);故进程中途崩溃(没机会 commit)仍可恢复,journal 缺失/不完整时
  可纯靠 WAL 恢复。
- **写/删分离 + 终态删 WAL**(不变量):`save` 是**纯写**(原子,见下),**绝不删 WAL**,可重复
  调(供未来 mid-run checkpoint);只有 `finalize`(workflow 完全跑完后,`run_workflow` 在
  `_exec_loaded` 正常返回**之后**调,任何异常/取消都会跳过)写 journal 后**校验 `used ⊆ 已落盘
  journal`(key+sig)** 才删 WAL,不一致则保留兜底。
- **原子写**:`save` 写 `<journal>.tmp` 后 `os.replace` 原子改名,崩溃中途不产生半截 journal。
- **异步 I/O**:journal/WAL 的读写经 `aiofiles`(`load`/`use`/`save`/`finalize` 均 async),不阻塞
  共享事件循环(swarmflow 在 leader 进程内与其它团队协程同 loop);WAL append 由 `asyncio.Lock`
  串行化防并发交错。(引擎可用通用三方库,见「引擎契约」业务无关条;`os.replace`/`unlink` 是快元
  数据 syscall,保持同步。)

详见 `F_38`(路径接线)、`F_40`(落盘顺序 / WAL / 原子 / 异步)。

## 有状态会话契约（`agent_session` / `human_session` / `human`）

与单轮 worker 正交的多轮执行单位（见 `F_37`）。引擎层业务无关，会话实现落 `workflow/backends/avatar_session_backend.py`。

1. **DSL**：`agent_session(*, label, phase, instructions, options)` / `human_session(...)` 返回 `AgentSession`；`AgentSession.send(prompt, *, schema=None, notify=False, options=None)` 推进一轮；`human(prompt, *, schema=None, label=None, phase=None, options=None)` 是单次 human 问答的语法糖（开一个临时 `AgentSession(_human=True, label=, phase=)`、问一次、关）。`label`/`phase` 与 `agent()`/`*_session()` 对称——转发给临时会话,使一次性 human turn 也带确定性 corr `{phase}:{label}:{turn}` 与可读进度标签（缺省时 corr 退回 `{member}:{turn}`）。`HumanSession` 是 `AgentSession` 的类型别名（`_human=True`）。详见 `F_39`。
2. **句柄 + 懒开 + history 镜像**：`AgentSession` 在引擎层维护轻量 `_history`（`(user, assistant)` 对，**不进 journal**，靠脚本重放重建，仅供 resume 签名 / 未来 fork）。首个 cache-miss 的 `send` 才调 `backend.open_session` 建会话；前序 cache hit 全程不开、不驱动后端。
3. **journal 兼容**：`call_signature(prompt, opts, schema_json, history=None)` **仅 history 非空时**把 history 折入哈希——`agent()`（history 恒空）签名逐字节不变，worker resume 零回归；会话 turn 折入 history，使上游 turn 变更级联重跑下游。
4. **options bag**：会话原语经 `options` dict 传调优参数，`_build_opts` 校验键 ∈ `_ENGINE_OPTIONS{label,phase,schema,model,timeout,isolation,agent_type} | backend.KNOWN_OPTIONS`，未知键 fail-fast；`agent()` 保持显式 kwargs（含新增的 `isolation` / `agent_type`，CC 对齐、执行留空，见引擎契约段）。
5. **phase 动态绑定**：会话 `send` 未显式传 phase 时取 `rt.current_phase`，一个会话可跨多个 phase；同一会话被并发 `send` 一次性告警（`_in_flight`）。
6. **后端 = 有状态 avatar harness（`AvatarSessionManager`）**：从 base spec 派生（agent → `worker_base_spec`；human → `human_base_spec`）经 `_member_spec.derive_member_spec`（与 worker 共享）建唯一 card + 多轮 persona → `TeamHarness.build(role=WORKER)` → `start()` **一次** → 多轮 `send`。`role=WORKER` 隔离级别同 worker（不进 coordination），但**保活多轮**、`dispose` 于 `close_session`/`aclose`。
7. **send-等-收**：`harness.send(prompt, immediate=False)` 起一轮；`subscribe(on_round, on_state)` 的回调（跑在 supervisor 协程，仅 set future / cache result）在 `RUNNING→IDLE` settle 时 resolve 本轮 future，取**最后一轮 finished 的 `output`**（一次 send 可能驱动多轮 task-loop continuation）。`result_type=="interrupt"`（avatar 内部 HITL）→ 抛 `BackendError` + error 日志（后续特性），不返回半截。
8. **schema 多轮注入**：会话 IDLE 间隙 per-turn `harness.add_tool(StructuredOutputTool)` + user prompt 追加 nudge，轮末 `remove_tool`（ability_manager 按 owner re-qualify，并发会话不撞）。`TeamHarness.add_tool/remove_tool` 是转 `ability_manager.add_ability/remove_ability` 的 passthrough。
9. **human 输入源**：`human` 会话 `send_turn` 推问题（`on_human_prompt(member, corr, prompt)` 回调 → `observer.emit(HUMAN_PROMPT)` → leader 播报）→ `_pending_human[corr]`（**实例字段，非全局 registry**）等真人 raw 回复 → avatar 用 LLM 把"问题+回复"格式化（schema 时结构化）。`submit_human_reply(corr, answer)` 是入向口；`opts["timeout"]`（默认 `_DEFAULT_HUMAN_TIMEOUT`）超时 → `AgentResult(skipped=True)` → `send` 返回 `None`；`aclose` 取消所有未决 future。**等真人不占 LLM permit、不计 spawn 预算**（agent 会话 turn 则占）。**外部链路（已接线，seam B）**：corr 由**引擎确定性生成** `{phase}:{label}:{turn}`（`turn = len(history)//2`，hit/miss 都推进），跨 resume 稳定——"等真人期间中断 → resume"后同一交互点 corr 不变，真人回复仍有效；非法 corr（不匹配 pending）被 `submit_human_reply` 拒绝。出向 `HUMAN_PROMPT`/`HUMAN_REPLIED` progress 事件带 `correlation_id`（只从 backend 等待路径发，cache-hit 重放不出现，progress 不进 journal）；human avatar base spec 经 `SWARMFLOW_HUMAN_BASE_SPEC` handle 链注入（`agent_configurator` 取 `human_agent` spec 缺省回退 worker spec）；入向真人回复经 `interact_agent_team(HumanAgentMessage(target="swarmflow:<corr>"))` → `TeamRuntimeManager.interact` 在 `resolve_targets` 前薄路由 publish 到专用 `swarmflow_human_reply_topic`（`TeamEvent.WORKFLOW_HUMAN_REPLY`，独立于 `TeamTopic.TEAM`）→ `AvatarSessionManager` 订阅过滤 → `submit_human_reply`。详见 `F_37`。
10. **run 收口**：`run_workflow` finally 调 `backend.aclose()` 释放本 run 开过的所有会话。

## 结构化输出工具协议（`StructuredOutputTool`）

- harness 无原生结构化输出。当 `agent(prompt, schema=...)` 的 `schema_json` 非空：backend 构造一个 `StructuredOutputTool` **对象**，其 `ToolCard.input_params == schema_json`，描述经 `tools/locales` i18n 解析（`descs/<lang>/structured_output.md`），**作为实例追加进 worker spec.tools**（`DeepAgentSpec._resolve_tools` 原样透传实例）。挂上 harness 时 ability_manager 把 id re-qualify 为 `structured_output_{owner_id}`（owner = worker card id，并发 worker 不撞），无需 backend 指定 per-call id。
- worker system prompt 要求「完成后必须调用 `structured_output` 提交结构化结果」。工具 `invoke(inputs)` 捕获 `inputs` 到 `self.captured` 并标 `called`。
- **捕获即终止本轮**：`StructuredOutputFinishRail`（`AgentRail`，`after_tool_call` hook）在 `tool_name == "structured_output"` 时 `ctx.request_force_finish(...)`，经 ability_manager 冒泡到 ReAct 主 ctx → 本轮 `break`、不再发起下一次 LLM 调用。worker（`has_schema` 时，`team_worker_backend`）与 avatar session（`avatar_session_backend._start_avatar`，`start` 前）各经 `TeamHarness.add_rail`（委派 `native.add_rail`，对称于 `add_tool`）挂一份。这从机制上消除小模型"调用后无停止信号 → 反复重发 `structured_output`"的循环（force-finish 的 payload 无关紧要——backend 仍读 `captured`）。详见 `F_39`。
- `TeamWorkerBackend.run` 读 `submit_tool.captured` → `AgentResult(structured=...)`；若 worker 未调用工具则抛 `BackendError` → 引擎按 `retries` 重试 → 耗尽后 `agent()` 返回 `None`（dw 控制流容忍）。工具的注册/清理全由 harness 的 ability_manager + `run_once` 的 `teardown_tools` 负责，backend **不手动** `resource_mgr.add/remove`。
- `schema_json` 为空时：worker 不装该工具，取最终自由文本 → `AgentResult(text=...)`。

## 进度事件与 leader 旁观（`WORKFLOW_PROGRESS`）

- 单一事件类型 `TeamEvent.WORKFLOW_PROGRESS` + `WorkflowProgressTeamEvent(kind, workflow_name, phase, label, prompt, model, outcome, text, phases, correlation_id)`，`kind` 取引擎 `ProgressKind` 字符串值（一个 handler 方法渲染全部）。`correlation_id` 仅 `human_prompt` / `human_replied` 携带。
- swarmflow 在 leader 进程内后台跑（NativeHarness 异步工具框架的后台任务，见 `S_20`）；`SwarmflowTool.run_background` 的 observer 把进度 republish 成该事件，`sender_id="swarmflow"`（≠ leader member_name），故 `kernel` 的 self-filter 不拦截，leader 自己的 coordination 循环收到。
- `WorkflowHandler`（coordination 第 7 个 handler，仅监听 `WORKFLOW_PROGRESS`，leader-only）渲染**中途里程碑**（`workflow_started` / `phase`）+ **human 等待**（`human_prompt` 带 question+corr / `human_replied`）→ `deliver_input(use_steer=True)`；per-agent 事件不播报（太频繁，归 4 层结构）。符合 coordination 铁律：事件只作为 leader 输入，不做决策。**完成 / 失败结果不在此叙述**——`SwarmflowTool.run_background` 返回 `summarize_run(observer.run) + render_result_text(脚本返回值)`，由异步工具框架经 `harness.send(immediate=False)` 回灌 leader（完整、不截断；失败回灌错误文本，见 `S_20`）。
- **leader stream 生命周期**：swarmflow 场景 leader 不建任务 → `is_team_completed` 首条「无任务返回 None」→ team 永不 auto-complete → leader stream 保持 idle 等 event，直到外部停止。

## 4 层数据模型（`WorkflowRun`）

`WorkflowRun(name, status, phases)` → `PhaseRecord(title, agents)` → `AgentActivity(label, prompt, activity, outcome, status)`。即 TUI 的 Phase ▸ agents ▸ {prompt, activity, outcome}。由 `build_workflow_run_from_events(events)` 折叠引擎进度事件流构建（对 `parallel`/`pipeline` 的交错事件健壮：按 `phase` 归组、按 label 匹配最近未完成 activity）。`preprocess_swarmflow`（`MockBackend`）零网络生成同结构供前端预览；`WorkflowObserver.to_frontend()` 是传输 stub（按需求留空）。

## 错误边界

引擎内部抛 `workflow.engine.errors.WorkflowError` 子类（`MetaError`/`LintError`/`SchemaError`/`BackendError`）。这些**限制在 `workflow/` 内部**；不得散播到 agent_teams 调用点。`swarmflow()` 工具边界捕获异常并转成 `ToolOutput(success=False, error=...)`（公共 seam）。后续若需对外抛错，在工具边界转 `StatusCode`/`raise_error`，不在引擎内引入仓库错误体系（保持引擎业务无关）。
