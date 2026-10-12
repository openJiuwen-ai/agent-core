# Swarmflow 三方 Agent 接入（`options.agent_type` → Claude Code / Codex）

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-09-21 |
| 范围 | `workflow/backends/{team_worker_backend,avatar_session_backend,external_turns,budget_rail}.py`、`workflow/engine/{journal,primitives,progress}.py`、`workflow/engine/backends/base.py`、`workflow/{runner,tool_swarmflow}.py`、`external/cli_agent/spawn.py`、`external/member_runtime.py`、`rails/team_context.py`、`agent/agent_configurator.py`、`schema/events.py` |
| 测试基线 | `test_worker_backend.py::test_agent_type_*`、`test_avatar_session_backend.py::test_external_session_*` / `test_session_member_names_*`、`test_external_turns.py`、`test_cli_workflow_runtime.py`、`test_member_runtime.py::test_require_checkpoint_*`、`test_journal.py::test_call_signature_folds_agent_type_when_set`、`test_session_primitives.py::test_agent_started_carries_agent_type_and_described_model`；`agent_teams/workflow` + `agent_teams/external` 全套通过 |
| 关联 | `S_18`（引擎契约 / WORKER 不变量 8 / 有状态会话契约）、`S_27`（`require_checkpoint`、工作流运行时构造）、`F_22`（团队侧三方成员）、`F_96`（成员名稳定性）、`F_113` |

## 背景

团队对话场景已能拉起 Claude Code / Codex 作为一等成员（`F_22`），但 swarmflow 脚本的
`agent()` / `agent_session()` 只能派 native worker——同一团队两套用人渠道，编排侧只能用一半。
`_ENGINE_OPTIONS` 里的 `agent_type` 键此前是占位，无任何消费者。

## 决策

1. **在 `TeamWorkerBackend` 内按 `opts["agent_type"]` 分叉，`AgentBackend` 协议不变**。空走原路径零改动；
   非空先过双闸——引擎会驱动的后端（本版仅 SDK 族 claude / codex）∩ 团队声明集
   `TeamAgentSpec.external_cli_agents`（声明携带启动配置，也是部署方对该 CLI 花钱的许可）。任一不满足
   在发射前抛 `BackendError`，文案同时给出两个名单与改法，让 leader 改参数而不是 worker 重试。
   声明集经 `inject_team_handles(SWARMFLOW_EXTERNAL_CLI_AGENTS)` → `run_swarmflow` → backend 下发，
   与 `swarmflow_model_resolver` 同一条链。
2. **`build_cli_workflow_runtime()` 是 `build_cli_runtime()` 的兄弟，不是开关**。工作流成员只被脚本单向
   派活：`HarnessContext` 直构、`inject_mcp=False`、env 不含 `OPENJIUWEN_TEAM_JOIN`、系统提示词是
   swarmflow worker / session 提示词。启动件（cli_path / skills / model / env / idle 超时）仍取自声明条目；
   两函数共享 `_claude_process_env` / `_codex_process_env`。`cwd`：`isolation="worktree"` 时取
   `SwarmflowWorkerWorktrees` 路径，否则声明 `cwd`，否则 `build_context.project_dir`。
3. **`ExternalMemberTurns` 把运行时的 turn 事件流折成"一个 prompt → 一个 `TurnResult`"**，一次性 worker 与
   会话共用。终态 `on_round`（finished / failed / aborted）解析本轮 future；`CancelledError`（引擎
   `options.timeout` 或暂停）先 `abort(immediate=True)` 再上抛——被打断的轮次不写 journal，续跑重做。
4. **token 事后记账**：外部成员无模型调用钩子，`SwarmflowBudgetRail.bill()` 在轮末把归一后的量记进
   session / workflow 两本账；入口闸在下一次调用前生效，最坏超支一轮。归一：Codex 的
   `TurnResult.usage` 已是本轮 `usage.last` 之和；Claude（`claude-code`）为会话累计量，逐轮差分，
   总量回落即视为对话重置、整段归本轮。输入/输出/缓存拆分随总量同行：`ExternalTurnOutcome` 携带
   `TurnUsage` 的拆分（累计 provider 逐计数器差分），`bill()` 一并记入 rail 的 `call_input/output/cache_tokens`，
   `AGENT_COMPLETED` 据此上报、与 native 口径对齐；CLI 未报拆分时保持 `None`，不猜。SDK 侧预算不透传。
5. **结构化输出走提示词 + JSON 解析**：外部 CLI 不挂 `StructuredOutputTool`，schema 追加到 prompt 尾部，
   轮末从 `final_output` 取最后一个 JSON 对象；解析失败 = native "未提交"，`BackendError` → 引擎重试 → `None`。
6. **`REQUIRE_RESUME` 两级语义落在 backend + `require_checkpoint`**：外部会话的运行时在**首轮 `send_turn`** 才
   启动，因为只有那时引擎的 history 镜像能区分"首轮发射"（history 空 → 派生 id 首启）与"冷续跑重建"
   （history 非空 → `resume_external_backend=True, require_checkpoint=True`）。
   `ExternalHarnessMemberRuntime(require_checkpoint=True)` 在指针缺失时抛 `HarnessProtocolError`，
   不再静默新开会话；默认 `False` 保持团队对话场景原语义。provider harness 三行未动。
7. **`agent_session` 成员名掺 run 前缀**：`{run_prefix}-sess-{slug}-{n}`，对齐一次性 worker。avatar 子会话 id
   `{team}/{wf}/{member}` 因而按 run 隔离——同 run 续跑找回记忆，新 run 从零，并发同名 run 互不干扰。
   三方成员的会话指针存在同一成员子会话 checkpoint，同样按 run 隔离。**声明式 BREAKING**：旧行为
   "新 run 读回上一场记忆"是副作用而非契约。
8. **事件**：`call_signature` 仅在 `agent_type` 为真时并入 identity（存量 journal 字节不变）；
   `AGENT_STARTED.agent_type` 取 `opts`，`model` 经新钩子 `AgentBackend.describe_model(opts)`——引擎在调
   backend 之前发事件，只有 backend 知道 CLI 实际会跑哪个模型（hint → 声明条目 → 后端名）。

## 拒绝的方案

- 引擎层按 `agent_type` 选不同 backend：引擎持单 backend，`parallel` 内混编要求逐调用路由。
- 给 `build_cli_runtime()` 加"不加盟"开关：已有 20+ 参数，语义开关组合爆炸。
- 引擎镜像降级 fork 外部会话：残缺上下文伪装成功；本版外部会话 fork 一律 `BackendError`，provider 原生 fork 另期。
- 在 provider harness 里改 `REQUIRE_RESUME` 回退：harness 不知道 swarmflow 历史；宿主侧标记更小且不伤团队对话。
- 透传 SDK 预算（Claude USD / Codex goal）：单位与截断语义不同构，会双重记账。

## 已知遗留

- 冷续跑指针缺失抛出的 `HarnessProtocolError` 由引擎 `_attempt_calls` 按 `rt.retries` 重试后转 `AGENT_FAILED`
  （确定性失败被多试几次，代价有界；错误文案完整到达 leader）。
- 外部会话 fork 见 `F_115`。
- Claude 的 `TurnResult.usage` 按 provider mapping 标记为会话累计量（`CUMULATIVE`）；若实测确认为跨续跑累计，
  冷续跑后的首轮会把续跑前的累计量重复计入两本账（`ExternalMemberTurns` 每次重建从 0 起算）。修法是随会话
  指针一起保存上次累计基线并在 `resume=True` 时喂给驱动；待 token 口径实测（含 Codex 跨进程 thread 可寻址性）
  一并定案。
- `outputs()` 队列无界：长会话的投影 chunk 只增不减，需要时改为轮末 drain。
- `_execute_external_worker` 的 `session_id` 为空字符串时（直接构造 backend 的测试路径）成员子会话不可用。
