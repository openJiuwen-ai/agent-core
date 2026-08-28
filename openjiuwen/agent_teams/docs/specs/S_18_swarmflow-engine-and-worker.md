# Swarmflow 引擎与 Worker 运行时规约

## 元信息

| 项 | 值 |
|---|---|
| 类型 | spec |
| 关联模块 | `workflow/`（engine / backends / observer / schema / runner / tool_swarmflow）、`schema/team.py`、`schema/events.py`、`schema/blueprint.py`、`agent/team_agent.py`、`agent/coordination/handlers/workflow.py`、`rails/team_policy_rail.py`、`prompts/sections.py` |
| 最近一次修订日期 | 2026-08-28 |
| 关联 feature | `F_27_swarmflow-workflow-orchestration.md`、`F_31_swarmflow-per-call-model-routing.md`、`F_35_native-harness-async-tool-framework.md`、`F_37_swarmflow-stateful-sessions-and-human.md`、`F_38_swarmflow-journal-persistence.md`、`F_39_swarmflow-agent-worktree-isolation.md`、`F_39_swarmflow-e2e-hardening.md`、`F_40_swarmflow-journal-wal-and-program-order.md`、`F_42_swarmflow-tool-claude-code-alignment.md`、`F_43_swarmflow-pause-resume.md`、`F_47_swarmflow-concurrency-governor.md`、`F_66_swarmflow-real-token-budget-enforcement.md`、`F_69_cwd-workspace-project-root-separation.md`、`F_81_swarmflow-session-fork.md`、`F_87_swarmflow-run-id-isolation-and-dual-budget.md`、`F_88_swarmflow-relaunch-kind-and-seal-pause-semantics.md` |

## 范围 / 边界

**管：**

- swarmflow 引擎的分层契约（facade / seam / provider / primitives / backend）与移植边界。
- `TeamRole.WORKER` 的不变量与单轮执行契约。
- 结构化输出工具（`StructuredOutputTool`）协议。
- 进度事件分类（`WORKFLOW_PROGRESS`）与 leader 旁观播报路径。
- 4 层 `WorkflowRun` 数据模型。
- resume journal 的落盘路径与 `run_swarmflow` 接线契约。
- Swarmflow 并发治理与多 run `run_id` 身份（摘要见下节；细则 `S_21`）。
- 错误边界（引擎错误 vs 仓库 `StatusCode`）。

**不管：**

- 单 agent DeepAgent / ReAct 内部执行（见 harness）。
- engine 原语内部并发调度细节（`parallel`/`pipeline` 分支合并，与 `dw/wf` 移植层一致）；**不含** Leader 侧 L1–L3 `ConcurrencyGovernor`（见 `S_21`）。
- skill 全局安装（下一个 PR）。

## 引擎契约（`workflow/engine`）

- **业务无关**：engine 子包不得 import 任何 `openjiuwen.agent_teams` 业务模块（schema/agent/tools/...），以保证可用 `MockBackend` 独立单测、并与上游 `dw/wf` 同步。约束的是**业务耦合**，不是依赖面：engine 可用通用第三方库（`aiofiles` 异步文件 I/O、可选 `pydantic`/`jsonschema`）。"仅 stdlib" 是 **swarmflow 脚本**(外部用户代码)的约束,不是引擎的。
- **脚本格式**：合法 Python 模块，顶层 `META={...}`(纯字面量，`ast.literal_eval` 强制) + `async def run(args)`(或 `run()`)。脚本用 `from swarmflow import agent, parallel, ...`（facade 模块导入时一次性把唯一包名 `swarmflow` 注册进 `sys.modules` 指向 facade；进程内固定映射、无 per-run 安装/卸载，故顶层 import 与 `run` 体内延迟 import 同样生效）。
- **接缝**：`agent()`/`parallel()`/`pipeline()`/`agent_session()`/`human_session()`/`human()`/... 经 contextvar provider 转发；`Provider` 实现可整体替换。IO 接缝是 `AgentBackend`：单轮 `run(prompt, opts, schema_json) -> AgentResult`，加可选的有状态会话四方法 `open_session` / `send_turn` / `close_session` / `aclose`（默认 `NotImplementedError` / no-op，单轮 backend 不实现也不受影响）+ `KNOWN_OPTIONS`（backend 自声明的 options 白名单扩展）。
  `agent()` 的 option 集合包含 `label` / `phase` / `schema` / `model` /
  `timeout` / `isolation`。`isolation` 当前只允许 `None` 或 `"worktree"`；
  engine 只校验与透传，具体隔离语义由 backend 实现。
- **可观测性**：`Runtime` 有两个 sink。`log_sink: Callable[[str], None]`（诊断文本，默认 no-op）；`progress_sink: Callable[[WorkflowProgressEvent], None]`（结构化进度，默认 no-op）。`phase()`/`log()`/`agent()` 起止发 `WorkflowProgressEvent`；引擎不读 wall-clock（保持 resume 确定性），事件**无时间戳**——消费方在 agent_teams 层补时。
- **嵌套 workflow 的深度守卫是 per-task，不是全局**：`workflow()` 递归封顶用 `primitives._wf_depth`（contextvar，`_MAX_WORKFLOW_DEPTH=1`），非共享 `Runtime` 计数器。
  - contextvar 随 asyncio Task 拷贝：`parallel`/`pipeline` 各分支继承父深度 → **同层并发 `workflow()` 全部放行**
  - 真递归（子流 `run()` 内再调 `workflow()`，同一 Task）→ 返回 `None` + progress `LOG`（`[wf] nested workflow depth > 1 not allowed; skipping`）
  - 详见 `F_39`
- **嵌套 workflow 可观测性（child phase 声明）**：`workflow()` 入口在 push `("wf", k, name)` 之前发一条 `ProgressKind.PHASE` 声明，供下游 Monitor 建独立 child 卡（**不**改 author `phase()` 的 seal 语义）。
  - `phase_type="child"`；`phase` = 子脚本 `META["name"]`（原始名）
  - `nested_phase` = 展示名 `▸ {name}` 或 `▸ {name} #{N}`（`N` 取 `_path` 最深 branch 段 `i`；无 branch 时无 `#N`）
  - `parent_phase` = 进入子流前的 author phase（`_parent_phase` contextvar）
  - 子流内 agent/human 事件携带 `nested_phase`；消费方**优先**用它挂卡，而非 author `phase`
  - child 声明复用 `PHASE`；`log_sink` 另打 `[wf] child phase declared: …`（默认 no-op）
- **单次 fan-out 上限**：`parallel(thunks)` / `pipeline(items, ...)` 入口校验长度 ≤ `_MAX_FANOUT`（4096），超出抛 `WorkflowError`——显式报错而非静默截断（对齐参考工具的单次上限）。
- **`agent()` 的 CC 对齐参数（接口就位、执行留空）**：`agent(..., isolation='worktree', agent_type=...)` 经 `_ENGINE_OPTIONS` 接受并透传至 backend，但参考引擎暂不据其改变执行（不起 worktree 隔离、不解析具名专家 agent），`call_signature` 暂不纳入二者。对齐 Claude Code Workflow 工具表面，便于脚本针对完整 API 编写。详见 `F_42`。

## Token 预算（`BudgetLedger`，`F_66`）

- **账本是共享对象，不是计数字段**：`Runtime.budget: BudgetLedger`（`total` / `spent` / `remaining()` / `exhausted`）取代了旧的 `budget_total: int | None` + `tokens_spent: int`。`int` 不可变、传不进 backend 装的 rail，天花板也就无从执行——形状换成可共享引用才有下文。`BudgetLedger` 住 `engine/budget.py`（纯计数器 + 天花板，零业务耦合，与 `admission.py` 同性质，不破铁律 1）。
- **单写者：backend 记账，引擎只读**。`run_workflow` 在跑之前调 `AgentBackend.bind_budget(rt.budget)` 一次性注入；此后**只有 backend 写账本**。引擎**不再**累加 `AgentResult.tokens`（那行已删）——一次 `agent()` 是一整圈 agent 循环，引擎只看得见首尾，累加它等于把 backend 已记的账再记一遍。`AgentResult.tokens` 因此退化为**单次调用成本的如实上报**（无人累加）。
- **数字必须来自模型返回值**：`SwarmflowBudgetRail`（`workflow/backends/budget_rail.py`，业务层）读 `AssistantMessage.usage_metadata`（`total_tokens`，缺失时回落 `input_tokens + output_tokens`）。provider 不报 usage 就记 **0，不猜**——按长度估算会让天花板的含义随 provider 而变。`MockBackend` 无模型可问，用自己的估算喂账本（离线确定性）。
- **两级执法，缺一不可**：
  - **rail（主力）**：backend 给每个 worker / avatar harness 挂一个 `SwarmflowBudgetRail`。`after_model_call` 记账、超了 `ctx.request_force_finish` **就地终止该 round**；`before_model_call` 挡下付不起的调用（专治并发——账本共享，兄弟 worker 烧干预算时本 worker 立刻被挡）。用 force-finish 而非抛异常：超预算是钱花完了，不是坏了，已做的工作照常返回。
  - **引擎（兜底）**：`_check_budget(rt)` 紧挨 `_check_abort(rt)`，只在 `agent()` / `AgentSession.send()` **入口**，`raise BudgetExhausted`。**不做 pre-journal 检查**（与 `_check_abort` 不同）：钱已经花了的调用必须落 journal，否则 resume 会重跑并再付一次。
- **`BudgetExhausted` 是 `BaseException`**（与 `WorkflowAborted` 同理由：能被 `except Exception` 吞掉的天花板不叫天花板，须穿透 `parallel` / `pipeline` 分支体）。但语义相反——abort 可恢复（resume 重跑），exhausted 是**终态**（重跑只会撞同一个 gate），故 `SwarmflowTool.run_background` 单独捕获它转成 `BackendError`，让 async-tool runtime 注入 leader 读得到的失败；直接放 `BaseException` 上去会静默杀掉 task。
- **允许小幅越界**：一次调用的用量只有返回后才入账，最后那次可以把 `spent` 顶过 `total`；`remaining()` 因此 clamp 到 0，不返回负数。要不越界就得预知成本——不可能。
- **作用域是 leader，不是 run**：账本由 `agent_configurator` 在 `role==LEADER and enable_swarmflow` 时建一个，经 `inject_team_handles(SWARMFLOW_BUDGET)` → `TeamToolRail` → `create_team_tools` → `SwarmflowTool` → `run_swarmflow(budget=)` 下发（与 `swarmflow_concurrency` 完全平行的链路）。并发 run 抽同一个池，对齐工具描述里 `spent()`「跨主循环 + 所有工作流共享」的语义。配置入口是 `TeamAgentSpec.swarmflow_budget: int | None`（build 期校验 `>= 1`，与 `validate_swarmflow_concurrency` 同层）；**不是 `swarmflow()` 工具入参**——花钱上限是部署方的决定，不该由 leader 每次现编。

### 双层 Budget 与 run_id 隔离（`F_87`）

- **两层账本分立**：`Runtime.budget`（session/leader 级，跨 run 共享）之外，run 启动时按脚本
  `META["workflow_token_limit"]` 建 per-run `BudgetLedger` 存 `Runtime.workflow_budget`。
  `SwarmflowBudgetRail` 在 `after_model_call` 记账时，`workflow_budget` 非 None 则同时往
  `self._budget.add(tokens)` 与 `self._wf_budget.add(tokens)` 各记一笔；`workflow_budget=None`
  时退化为纯 session 级（向后兼容未配 `workflow_token_limit` 的脚本）。
- **leader / TinyAgent 的 token 只算 session，不算 workflow**：leader 与判断意图的 TinyAgent
  不属于某次 run 的 worker 群，其消耗进 session 账本但不进 per-run 账本——避免一次 run 的撞顶
  被 leader 自身开销提前触发。`agent_configurator` 在 `enable_swarmflow` 时给 leader 挂
  `SwarmflowBudgetRail(swarmflow_budget, workflow_budget=None)`（per-run 账本由引擎在 run 启动时注入）。
- **`BudgetExhausted.scope`**：`scope="workflow"`（per-run 账本撞顶）= 可重试，改脚本或调高
  `workflow_token_limit` 后同 run_id relaunch；`scope="session"`（leader/session 账本撞顶）=
  终端，需新建 session 或调高 `swarmflow_budget`。
- **缓存命中必须重建花费**：`journal.get_cached(ks, sig, run_id)` 命中后，用记录里的 `tokens`
  字段调 `rt.workflow_budget.add(cached_tokens)` 把当初那笔消耗加回本次 run 的 per-run 账本——
  否则撞顶检测会把命中缓存当"免费"而失灵。
- **`run_id` 进 journal 查询，不进 journal 路径**：journal 落盘路径仍由
  `(team, session, workflow_name)` 决定（不变量），多 run 同名脚本共享同一 journal 文件；
  `run_id` 只参与记录级的命中判定（`get_cached` 第三参数）。旧 run（无 run_id 字段）自然失效。
- **Progress 可观测（结果回路与终态）**：
  - per-agent `tokens`：`AgentResult.tokens` → `_BackendCallResult.tokens` → `_emit_agent_completed` / `_emit_agent_failed`（cache-hit 时 `tokens=None`，budget 快照仍带）
  - `budget`：`_budget_snapshot(rt.budget)` → `{total, spent, remaining, scope="leader", exhausted}`
  - rail 撞顶 force-finish **不走** `_emit_agent_completed`；`BudgetExhausted` 由 `_exec_loaded` 补发 `WORKFLOW_FAILED`（`budget.exhausted=true`）后 re-raise
  - `SwarmflowTool._publish` 显式拷贝 `tokens`/`budget`/`phase_type`/`nested_phase`/`parent_phase`

## WORKER 不变量（`TeamRole.WORKER`）

1. **单轮、无状态、用完即弃**：一个 `agent()` 调用对应一个 worker；worker 跑一次即销毁，上下文每次全新。
2. **worker = 没有团队工具的 teammate**：`TeamWorkerBackend` 从 team 的 **teammate spec**（缺失则 leader spec，经 `agent_configurator` → `inject_team_handles` 的 `SWARMFLOW_WORKER_BASE_SPEC` 注入）`model_copy` 派生 worker `DeepAgentSpec`——保留 teammate 能力（model / tools / skills / workspace / sys_operation / **todo 规划 `enable_task_planning` / `enable_task_loop`**），但因 team rail 是装配期注入、原始 spec 不含，worker 天然无团队协作工具。每个 worker 是一个 `TeamHarness(role=WORKER)`。
3. **不进 coordination 协作循环**：worker 不订阅消息总线、不认领任务、不被 dispatcher 唤醒。它经 **`TeamHarness.run_once`** 执行——`run_once` = `DeepAgent.invoke`（按 spec 的 `enable_task_loop` 自动单轮或自驱 task-loop），**不开 supervisor → 无 steer / 无 outputs 流**，返回值与 `Runner.run_agent` 一致；**不经** `TeamAgent.invoke` / `CoordinationKernel.start`。结束 `harness.dispose()` 释放 sys_operation（工具由 `run_once` 的 `teardown_tools` 自动清理）。
4. **无 DB roster 身份、不持 `team_backend`**：swarmflow worker **不是 teammate**，`TeamWorkerBackend` **不**经 `spawn_member` 往 team DB 写 member row。每个 worker mint 成员名：有 `run_id` 时为 `{run_prefix}-{label_slug}-{n}`，否则 `wf-{slug}-{n}`（`F_47` / `S_21`），满足 `_MEMBER_NAME_PATTERN`，只用作 worker card / owner id / 工作区目录名——纯进程内身份，用完即弃，不污染团队成员表。worker 工作区落在 `team_home/workspaces/{member}_workspace`——**开 worktree 隔离时也是它**，隔离只把 worker 的 cwd（`DeepAgentSpec.cwd`）指向 worktree，工作区不跟着走（[[F_69]]）；`agent_configurator` 已把整个 `team_home` 登记进团队 cleanup，故 worker 工作区随 `clean_team` 一并删除，**无需** backend 单独 `register_cleanup_path`。`TeamWorkerBackend` / `run_swarmflow` / `SwarmflowTool` 整条链**不注入 `team_backend`**（`F_44`）——worker 路径与 team DB 解耦。
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

### Run 级记录与可恢复性（`F_87` / `F_88`）

journal 在 call-path 记录（`__call__:` 前缀）之外，新增 run 级记录，key 形如
`__run__:{type}:{run_id}`，复用同一 journal 文件 + WAL 机制：

- **`get_cached(ks, sig, run_id)`**：命中条件从 `(ks, sig)` 升级为 `(ks, sig, run_id)` 三元组。
  旧 run（无 run_id 字段）自然失效——`run_id=None` 与新 run 的非空 run_id 比对为不等。命中后
  用记录里的 `tokens` 字段重建 per-run 花费（见双层 Budget 节）。
- **`find_run_record(run_id, record_type)`**：查某 run 是否有某类记录（当前 `seal` / `pause`）。
- **`write_run_record(run_id, record_type, payload)`**：写 run 级记录，落 WAL + 刷进 `prior`
  使同 run 查询可见。
- **seal 记录**：run 终态时 `_write_seal_record(rt, terminal_status)` 写
  `__run__:seal:{run_id}`（`terminal_status` = `completed` / `stopped`）。sealed run 不可 resume。
- **pause 记录**：可恢复中断时 `_write_pause_record(rt, pause_reason)` 写
  `__run__:pause:{run_id}`（`pause_reason` = `paused` / `early_return` /
  `workflow_budget_exhausted`）。pause 记录的 run 可同 run_id resume。

事件语义对齐（`F_88`）：

| 场景 | 事件 | status | 记录 | 可恢复 |
|---|---|---|---|---|
| 正常完成 | `WORKFLOW_COMPLETED` | completed | seal(completed) | 否（终态） |
| workflow 级 budget 撞顶 | `WORKFLOW_FAILED` | failed | pause(workflow_budget_exhausted) | 是（改脚本重试） |
| session 级 budget 撞顶 | `WORKFLOW_STOPPED` | stopped | seal(stopped) | 否（终端） |
| early_return（改脚本重跑） | `WORKFLOW_PAUSED` | paused | pause(early_return) | 是 |
| pause/resume | `WORKFLOW_PAUSED` | paused | pause(paused) | 是 |
| stop | `WORKFLOW_STOPPED` | stopped | seal(stopped) | 否 |

- **CancelledError seal 兜底**：stop 落在 LLM 调用中途时 `WorkflowAborted` checkpoint 来不及触发，
  `_exec_loaded` 的 `except asyncio.CancelledError` 检查 `rt.abort_event.reason == "stop"`，若然则
  `task.uncancel()` 后补写 seal 再 raise——保证 stop 永远落 seal。
- **seal guard**：`SwarmflowTool._seal_guard(script_path, resume_id)` 在 invoke 时若 `resume_id`
  指向已 seal 的 run（`find_run_record(resume_id, "seal")` 命中），清空 `resume_id` → 强制
  `new_swarmflow_run_id()`。best-effort，journal 读取失败只 debug log，不阻塞。
- **`relaunch_kind`**：`WorkflowProgressTeamEvent.relaunch_kind: "relaunch" | "resume" | None`，
  由 `SwarmflowTool._publish` 从 inputs 透传。`"relaunch"`（脚本编辑重跑，存在 resume_id 时
  invoke 设置）= 整体替换 phase/agent 树；`"resume"`（pause→resume，`_relaunch` 设置）= 增量合并；
  `None` = 全新 launch。
- **`swarmflow_human_reply_topic(session_id, team_name, run_id)`**：human session 真人回复走专用
  topic（run-scoped，避免与 leader team-event 订阅竞态）。

## 并发治理（`F_47` / `S_21`）

单 Leader 实例内一个 `ConcurrencyGovernor`（与 `AsyncToolRuntime` 同作用域），三层 cap：

| 层 | 语义 | 命中 |
|----|------|------|
| L1 `max_workflows` | 同时后台 swarmflow run 数 | `invoke` **拒绝** |
| L2 `agents_per_run` | 单 run 内并行 `agent()` | sem **阻塞** |
| L3 `max_agents_total` | 该 Leader 全局 agent 槽 | sem **阻塞** |

- `SwarmflowTool.invoke`：`admit_workflow()` → `new_swarmflow_run_id()` → `launch_async_tool`；
  `run_background.finally` 或 launch 失败路径 `release_workflow`。
- engine：`Runtime.agent_gate`（`AgentAdmission` 协议）；Swarmflow 注入 `RunAgentAdmission`（先 L2 后 L3）。
  **未注入时 back-compat**：`primitives._resolve_agent_gate(rt)` 惰性构造 `SemaphoreAdmission(rt.make_cap())`
  赋回 `rt.agent_gate`，等价旧 `Runtime.sem`（`MockBackend` / `preprocess_swarmflow` / 旧测试不受影响）。
- **resume（`F_43`）**：`_relaunch` 复用 inputs 内 ticket/gate，**不**二次 admit。注：`run_background.finally`
  对 `WorkflowAborted→CancelledError` 也会 release（pause 退出即释 L1）；resume 复用同 ticket 但不重新 admit，
  故 resume 期间不占 L1 槽（详见 `S_21` 错误语义）。
- **`run_id`**：进程内身份 + Leader 播报 + worker 命名前缀；**不改变** journal 路径（仍
  `(team, session, workflow_name)`）。

配置：`TeamAgentSpec.swarmflow_concurrency`；详见 `S_21_swarmflow-concurrency-governor.md`。

## pause/resume 中断恢复（`F_43`）

外部经 `Runner.run_agent_team_streaming(background_task_controller=...)` 传入一个
`BackgroundTaskController`，attach 到 leader harness；leader 调起的 swarmflow 后台任务可被
`controller.pause()` / `controller.resume()` 中断恢复。参照 Claude Code：pause = abort + 进程级
停止，resume 靠 journal 重放，被中断的 agent/turn 不入 journal、resume 重跑。

**engine 中断契约**：
- `Runtime.abort_event: asyncio.Event | None`（`run_workflow(abort_event=...)` 注入，None=不可暂停）。
- `primitives.agent()` / `AgentSession.send()` 各有两个 checkpoint：入口 gate（cache-hit 后、起
  backend 前）挡新调用；pre-journal guard（backend 成功后、`journal.use` 前）确保在途调用不写 WAL。
- checkpoint 命中 raise `WorkflowAborted`（`engine/errors.py`，**`BaseException`** 子类，穿透
  parallel/pipeline 的 `except Exception`，不被吞成 None 再 journal 成 null）。
- `run_workflow` 中断时解栈不到 `journal.finalize`，WAL 保留；resume 时 `Journal.load` 重放前缀。

**pause 停止机制（三步，顺序是正确性关键）**：`set abort_event → backend.abort_sessions() →
async_tool_runtime.cancel(task_id)`。
1. 单轮 `agent()` worker 走 run_once 不可 abort → 靠顶层 task cancel（CancelledError，finally 清理）。
2. `agent_session`/`human_session` 的 session harness 是独立 supervisor → 顶层 cancel 够不到，必须
   `AvatarSessionManager.abort_all()` 单独 abort（agent+human 两类 `harness.abort(immediate=True)`；
   human 还 cancel `_pending_human` 在等真人的 future）。abort_all 在 controller 协程内**完整**执行，
   故必须排在 cancel 之前，否则顶层 cancel 解栈时 session supervisor 泄漏。

**resume 契约**：`controller.resume()` → `SwarmflowTool._relaunch(inputs, session_id)`（新 task_id +
`launch_async_tool(同一 inputs)`，绕过 `invoke`）。journal 路径由 `(team,session,name)` 唯一决定 →
命中 pause 前完成的 agent、断点后 live。SwarmflowTool 把 engine 抛的 `WorkflowAborted` 转
`CancelledError`，让 async-tool runtime 静默取消（不注入完成）。human turn 的 `correlation_id` 跨
resume 稳定，真人回复仍能匹配重跑的那轮。**resume 必须恢复 `session_id` contextvar**：relaunch 由
外部协程（controller）驱动、不在 leader round 上下文里，而 `launch_async_tool` 的新 task 在
`create_task` 时继承当前 context；故 `run_background` 捕获 `session_id` 一次（贯穿 `_publish` topic
/ `run_swarmflow` / relaunch 闭包），`_relaunch` 在 launch 前 `set_session_id(原 session)`、`finally`
复位。缺这一步 resume 会解析到空 session → 用错 journal 路径（不命中缓存、全部重跑）+ 进度事件发到
错 topic（外部 monitor/drain 收不到）。

**接线**：`team_runner.run_agent_team_streaming(background_task_controller=)` →
`TeamAgent.set_background_task_controller` → `TeamHarness`（存 `_bg_controller`，`start` 跨 native
rebuild 回灌）→ `NativeHarness.background_task_controller`；SwarmflowTool 经 `parent_agent` 读、launch
时 `controller.register(SwarmflowRunHandle)`、finally `deregister`。controller 落
`runtime/background_task_controller.py`。

## 有状态会话契约（`agent_session` / `human_session` / `human`）

与单轮 worker 正交的多轮执行单位（见 `F_37`）。引擎层业务无关，会话实现落 `workflow/backends/avatar_session_backend.py`。

1. **DSL**：`agent_session(...)` / `human_session(...)` 返回 `AgentSession`；`send()` 推进一轮；`human(...)` 是单次 human 语法糖（临时 `AgentSession(_human=True)`，问一次即关）。`label`/`phase` 与 `agent()` 对称。
   **corr 格式**：
   - 串行：`{phase}:{label}:{turn}`
   - 有 branch：`{phase}#{disambig}:{label}:{turn}`（`disambig` = `_branch_disambig(_path)`，`par`→`p{i}`，`pipe`→`i{i}s{s}`，`.` 连接）
   - `HumanSession` 是 `AgentSession` 别名。详见 `F_39`。
2. **句柄 + 懒开 + history 镜像**：`AgentSession` 在引擎层维护轻量 `_history`（`(user, assistant)` 对，**不进 journal**，靠脚本重放重建，仅供 resume 签名 / 未来 fork）。首个 cache-miss 的 `send` 才调 `backend.open_session` 建会话；前序 cache hit 全程不开、不驱动后端。
3. **journal 兼容**：`call_signature(prompt, opts, schema_json, history=None)` **仅 history 非空时**把 history 折入哈希——`agent()`（history 恒空）签名逐字节不变，worker resume 零回归；会话 turn 折入 history，使上游 turn 变更级联重跑下游。
4. **options bag**：会话原语经 `options` dict 传调优参数，`_build_opts` 校验键 ∈ `_ENGINE_OPTIONS{label,phase,schema,model,timeout,isolation,agent_type} | backend.KNOWN_OPTIONS`，未知键 fail-fast；`agent()` 保持显式 kwargs（含新增的 `isolation` / `agent_type`，CC 对齐、执行留空，见引擎契约段）。
5. **phase 动态绑定**：会话 `send` 未显式传 phase 时取 `rt.current_phase`，一个会话可跨多个 phase；同一会话被并发 `send` 一次性告警（`_in_flight`）。
6. **后端 = 有状态 avatar harness（`AvatarSessionManager`）**：从 base spec 派生（agent → `worker_base_spec`；human → `human_base_spec`）经 `_member_spec.derive_member_spec`（与 worker 共享）建唯一 card + 多轮 role prompt → `TeamHarness.build(role=WORKER)` → `start()` **一次** → 多轮 `send`。`role=WORKER` 隔离级别同 worker（不进 coordination），但**保活多轮**、`dispose` 于 `close_session`/`aclose`。
7. **send-等-收**：`harness.send(prompt, immediate=False)` 起一轮；`subscribe(on_round, on_state)` 的回调（跑在 supervisor 协程，仅 set future / cache result）在 `RUNNING→IDLE` settle 时 resolve 本轮 future，取**最后一轮 finished 的 `output`**（一次 send 可能驱动多轮 task-loop continuation）。`result_type=="interrupt"`（avatar 内部 HITL）→ 抛 `BackendError` + error 日志（后续特性），不返回半截。
8. **schema 多轮注入**：会话 IDLE 间隙 per-turn `harness.add_tool(StructuredOutputTool)` + user prompt 追加 nudge，轮末 `remove_tool`（ability_manager 按 owner re-qualify，并发会话不撞）。`TeamHarness.add_tool/remove_tool` 是转 `ability_manager.add_ability/remove_ability` 的 passthrough。
9. **human 输入源**：`send_turn` 推问题 → `_pending_human[corr]` 等真人回复 → avatar 格式化输出。`submit_human_reply(corr, answer)` 为入向口；超时 → `AgentResult(skipped=True)` → `send` 返 `None`。**等真人不占 LLM permit、不计 spawn 预算**。
   - corr 由引擎确定性生成（见上条格式），跨 resume 稳定；非法 corr 被 `submit_human_reply` 拒绝
   - 出向 `HUMAN_PROMPT`/`HUMAN_REPLIED` 带 `correlation_id`（仅 backend 等待路径；cache-hit 不重放）
   - 入向：`interact_agent_team(HumanAgentMessage(target="swarmflow:<corr>"))` → `swarmflow_human_reply_topic` → `submit_human_reply`
   - human base spec 经 `SWARMFLOW_HUMAN_BASE_SPEC` 注入。详见 `F_37`。
10. **run 收口**：`run_workflow` finally 调 `backend.aclose()` 释放本 run 开过的所有会话。
11. **fork 原语（`F_81`）**：`AgentSession.fork(fork_mode=, keep_rounds=, label=, phase=, instructions=, options=)` 派生独立分支，五种 `fork_mode`（`full` / `before` / `after` / `keep_before_compact_after` / `keep_after_compact_before`）逐字对齐团队 fork。`keep_rounds` 为**轮数**（每次 `send()` 计一轮）：**`full` 之外必填**（缺省抛 `WorkflowError`）；**越界**（> 实际轮数）告警 + 回退全量。eager 捕获——`fork()` 调用时刻冻结父上下文，之后父自由 `send` 不影响子；子可链式再 `fork`。`human_session` 拒绝。fork turn 的 `node_type="agent_session_fork"`。
    - 引擎层只做镜像继承 + `backend.capture_fork` 委托 + 存不透明 `fork_data`（铁律 1：engine 不碰业务符号）；backend 负责轮数→消息索引换算、捕获/注入/双向压缩、child session_id 派生。
    - **`_member_name` + `ensure_member_name`（D9）**：`AgentSession` 在首次 `send`（无论 cache-hit/miss）经 `backend.ensure_member_name(kind, opts)` 预留 member name（不建 avatar / 不调 LLM / 不计 spawn）；`_sid` 保持"已建 avatar"语义（只 miss 时设）。`fork()` 用 `_member_name` 定位父恢复路径（而非 `_sid`），故 fully-hit resume 也能 fork。cache-hit 首轮也命名使 `_counter` 由 session 访问顺序决定、跨 resume 稳定（修复 counter 漂移）。
    - 后端捕获路径（`AvatarSessionManager.capture_fork`）：父 live → `ForkContext.from_agent`（含 ToolMessage）；父未重建（fully-hit resume）→ 构造一个**独立** Session（`create_agent_session(session_id=固定id, card=AgentCard(id=f"{team_name}_{member_name}"))`，card.id 带 team 前缀以匹配真实 agent_id；非"绑定"父 live session，仅复用其分桶键）+ `pre_run` 从 checkpointer 恢复 `state["context"]`（含 ToolMessage，无需重建父 avatar，见 D6）；两者都无 → 返 `None`，引擎镜像兜底（缺 ToolMessage）。
    - **链式 fork 约束（D10）**：不要 fork 一个未 send 过的 fork 子会话（其 `_member_name` 为 None，会镜像兜底）；想基于父早期状态派生，直接用父的 `fork_mode` + `keep_rounds`。
12. **前置底座：avatar 固定 session_id（`F_81` / F_37 决策 3）**：avatar child session 绑定稳定派生 id `{team}/{workflow}/{member}`（`_derive_avatar_session_id`），经 `harness.start(team_session=...)` → `create_agent_session(session_id=...)` 继承。这让已在代码里的 `pre_run` + checkpoint 恢复机制生效——同进程 resume 能按稳定 id 恢复 `state["context"]`（含 ToolMessage），是 fork 捕获完整上下文与部分-hit 续跑的共同底座。**仅同进程恢复（InMemory store）**；跨进程/重启的持久化 checkpointer 装配不做（`set_default_checkpointer` 进程级全局副作用大），列为遗留。

## 结构化输出工具协议（`StructuredOutputTool`）

- harness 无原生结构化输出。当 `agent(prompt, schema=...)` 的 `schema_json` 非空：backend 构造一个 `StructuredOutputTool` **对象**，其 `ToolCard.input_params == schema_json`，描述经 `tools/locales` i18n 解析（`descs/<lang>/common/structured_output.md`），**作为实例追加进 worker spec.tools**（`DeepAgentSpec._resolve_tools` 原样透传实例）。挂上 harness 时 ability_manager 把 id re-qualify 为 `structured_output_{owner_id}`（owner = worker card id，并发 worker 不撞），无需 backend 指定 per-call id。
- worker system prompt 要求「完成后必须调用 `structured_output` 提交结构化结果」。工具 `invoke(inputs)` 捕获 `inputs` 到 `self.captured` 并标 `called`。
- **成功捕获即终止本轮**：`StructuredOutputFinishRail`（`AgentRail`，`after_tool_call` hook）在 `tool_name == "structured_output"` 且 `ctx.exception is None` 时 `ctx.request_force_finish(...)`，经 ability_manager 冒泡到 ReAct 主 ctx → 本轮 `break`、不再发起下一次 LLM 调用。worker（`has_schema` 时，`team_worker_backend`）与 avatar session（`avatar_session_backend._start_avatar`，`start` 前）各经 `TeamHarness.add_rail`（委派 `native.add_rail`，对称于 `add_tool`）挂一份。这从机制上消除小模型"调用成功后无停止信号 → 反复重发 `structured_output`"的循环（force-finish 的 payload 无关紧要——backend 仍读 `captured`）。若工具调用失败，rail 不 force-finish，错误 `ToolMessage` 会进入下一轮模型请求，让模型有机会自我修正并重新提交合法的 `structured_output`。详见 `F_39`。
- `TeamWorkerBackend.run` 读 `submit_tool.captured` → `AgentResult(structured=...)`；若 worker 未调用工具则抛 `BackendError` → 引擎按 `retries` 重试 → 耗尽后 `agent()` 返回 `None`（dw 控制流容忍）。工具的注册/清理全由 harness 的 ability_manager + `run_once` 的 `teardown_tools` 负责，backend **不手动** `resource_mgr.add/remove`。
- `schema_json` 为空时：worker 不装该工具，取最终自由文本 → `AgentResult(text=...)`。

## 进度事件与 leader 旁观（`WORKFLOW_PROGRESS`）

- 单一事件：`TeamEvent.WORKFLOW_PROGRESS` + `WorkflowProgressTeamEvent`（镜像 `WorkflowProgressEvent`），`kind` 取 `ProgressKind` 字符串值。
- **核心字段**（按 kind 填充）：
  - workflow：`name` / `description` / `phases`
  - agent/phase/log：`phase` / `label` / `prompt` / `model` / `outcome` / `message`
  - human：`correlation_id` / `answer`（格式见有状态会话段）
  - 节点匹配：`node_type` / `agent_id`
  - 并行 run：`run_id`（`wf_{12hex}`，`F_47`）；`workflow_started` / `phase` 播报含 `{run_id}`
- **可观测扩展**（`SwarmflowTool._publish` 显式透传）：
  - `tokens`：`agent_completed` / `agent_failed`（cache-hit 为 `None`）
  - `budget`：`_budget_snapshot` 冻结 dict（完成/失败帧与 `BudgetExhausted` 终态均可带）
  - `phase_type` / `nested_phase` / `parent_phase`：嵌套 child 声明与子流内挂卡
- republish：`SwarmflowTool.run_background` observer → `sender_id="swarmflow"`（见 `S_20`）；kernel self-filter 不拦截。
- `WorkflowHandler`（leader-only）：narrate 中途 milestone + human 等待。
- per-agent 归 Monitor / 4 层结构；终态走 async 闭包（`S_20`/`S_21`）。
- `BudgetExhausted` 另经 `_exec_loaded` 发 `workflow_failed`（`budget.exhausted=true`）。
- **leader stream 生命周期**：无任务时不 auto-complete，保持 idle 等 event，直到外部停止。

## 4 层数据模型（`WorkflowRun`）

`WorkflowRun(name, status, phases)` → `PhaseRecord(title, agents)` → `AgentActivity(label, prompt, activity, outcome, status)`。即 TUI 的 Phase ▸ agents ▸ {prompt, activity, outcome}。由 `build_workflow_run_from_events(events)` 折叠引擎进度事件流构建（对 `parallel`/`pipeline` 的交错事件健壮：按 `phase` 归组、按 label 匹配最近未完成 activity）。`preprocess_swarmflow`（`MockBackend`）零网络生成同结构供前端预览；`WorkflowObserver.to_frontend()` 是传输 stub（按需求留空）。

## 错误边界

引擎内部抛 `workflow.engine.errors.WorkflowError` 子类（`MetaError`/`LintError`/`SchemaError`/`BackendError`）。这些**限制在 `workflow/` 内部**；不得散播到 agent_teams 调用点。`swarmflow()` 工具边界捕获异常并转成 `ToolOutput(success=False, error=...)`（公共 seam）。后续若需对外抛错，在工具边界转 `StatusCode`/`raise_error`，不在引擎内引入仓库错误体系（保持引擎业务无关）。
