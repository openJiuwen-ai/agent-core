# Swarmflow 单轮 worker 实时活动（agent_activity）

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-10-08（2026-09-17 初版，本修订扩展结构化字段与 session / 三方覆盖） |
| 范围 | `workflow/engine/progress.py`（`ProgressKind.AGENT_ACTIVITY` 结构化字段）、`workflow/engine/backends/base.py`（`progress_sink` / `bind_progress_sink` 接缝）、`workflow/engine/runner.py`（`run_workflow` 绑定）、`workflow/engine/primitives.py`（`_BackendCallSpec` 携带 `agent_id` + `call_key`；`AgentSession.send` 同款 opts 副本注入）、`workflow/backends/activity_emitter.py`（共享 emitter 工厂）、`workflow/backends/team_worker_backend.py`（`SwarmflowActivityRail` 双钩不节流）、`workflow/backends/avatar_session_backend.py`（avatar 挂同一 rail + per-turn `correlation_id`）、`workflow/backends/external_turns.py`（`bind_activity_sink` passthrough）、`external/member_runtime.py`（`bind_activity_sink` + `_on_event` 三方映射）、`workflow/schema.py`（`AgentActivity.agent_id` + 活动归位）、`workflow/AGENTS.md`、`docs/specs/S_18_swarmflow-engine-and-worker.md`、测试 `test_worker_backend.py` / `test_observer.py` / `test_avatar_session_backend.py` / `test_session_primitives.py` / `test_member_runtime.py` |
| 测试基线 | `tests/unit_tests/agent_teams/workflow/` 全量通过（405 passed）；含 `external/` + `tools/` 全量 797 passed |
| Refs | #773 |

## 背景

Swarmflow 的单轮 `agent()` 在引擎眼里是**一次不透明调用**：发 `AGENT_STARTED`，等 `backend.run` 返回，发 `AGENT_COMPLETED`。一个 worker 实际可能跑几十秒、调十几次工具，而观战方（Swarm Map / Monitor）在这段时间里只看到"开始…结束"两个点，无法展示"它正在写文件 / 跑命令"。

引擎已有的进度接缝 `Runtime.progress_sink` 只对 engine 自己的起止钩子开放（`phase()` / `log()` / `agent()` 起止），backend 中途**没有**发事件的手段。本特性补上这条接缝，并让 worker 的工具活动经它上抛。初版只覆盖单轮 `agent()` 且每 worker 节流；本修订扩展为**结构化字段 + 三类执行体全覆盖（单轮 worker / session avatar / 三方 worker 与三方会话）+ 不节流双钩**。

## 决策

1. **新增 `ProgressKind.AGENT_ACTIVITY`，结构化载荷**：`activity_type`（`"tool_call"` = 工具执行前 / `"tool_result"` = 工具执行后）/ `tool_call_id`（配对键）/ `tool_name` / `content`（完整入参 / 结果 / 异常文本，仅 64KB 病态载荷防护上限，越界 `…(truncated)` 标记——与团队成员 `chat.tool_call` 帧的完整参数对齐）。`message="tool: <name>"` 保留给旧消费方。事件仍是**业务无关、无时间戳**的——消费方在 agent_teams 层补时，保持引擎 resume 确定性。

2. **给 `AgentBackend` 增一条 backend → engine 的进度接缝**：`progress_sink`（只读）+ `bind_progress_sink(...)`，`run_workflow` 建好 `Runtime` 后一次性绑定为 `rt.progress_sink`。未绑定时 `progress_sink is None`，backend 静默（测试后端零成本）。这条接缝是通用的——任何 backend 都能在单次调用中途上抛进度，不绑定具体业务。

3. **`_BackendCallSpec` 统一携带 per-call 身份**（本分支把 `_call_backend` 的位置参数收进 spec，见 `1eb59e6`）：`agent_id` 与 `call_key` 都来自 `agent()` 的 journal call-path key `ks`。`agent_id` 被注入 **opts 的副本**（`{**opts, "agent_id": ks}`）——journal 记录的仍是原始 opts，不污染白名单校验面与持久化内容；`call_key` 则作为 `backend.run` 的关键字参数原样透传（F_96）。**`AgentSession.send` 同款注入**（修订新增）：session turn 的 `_TurnRequest.opts` 同样是 `{**opts, "agent_id": ks}` 副本，`_make_record` / emits 仍用原始 opts。

4. **worker rail 双钩、不节流**：`SwarmflowActivityRail.before_tool_call` / `after_tool_call` 各发一次，一次工具执行产生一对事件（按 `tool_call_id` 配对）。`after_tool_call` 的 `callback_priority` 加偏移（1,000,000）排在所有 rail 之后，读到其它 rail（如 `StructuredOutputFinishRail`）改写过后的 tool message；无 tool message 时回退异常文本 / 原始 result repr。rail 仅在 `opts` 带 `agent_id` 时挂载（即经引擎的 `agent()` / `send` 调用），直接调用 backend 的测试场景不挂。

5. **共享 emitter 工厂**：`workflow/backends/activity_emitter.py` 统一 `truncate_activity_content`（64KB 防护上限）与 `build_activity_event`（事件形状 + `message` 兼容），worker rail / session avatar rail / external runtime 三处发射端共用——上限与事件形状只此一份，第四处发射端不会漂移。

6. **session avatar 挂同一 rail + per-turn `correlation_id`**：`AvatarSessionManager` 给每个带 `agent_id` 的 avatar harness 挂 `SwarmflowActivityRail`；emitter 发射时从 `_SessionState.current_correlation_id`（每次 `send_turn` 置当前轮）读 id，把活动事件对齐到该轮 turn 的 started/completed 事件——观战方能按轮匹配活动。

7. **三方 worker / 三方会话覆盖**：`ExternalHarnessMemberRuntime.bind_activity_sink` 绑定 sink（`None` 静默），`_on_event` 处理 `ItemLifecycleEvent`（`item_type=="tool"`）——Claude `tool_use` / `tool_result` 与 Codex 工具类 item started/completed（command_execution / file_change / mcp_tool_call）统一映射为同一结构化事件，envelope `item_id` 作配对键。单轮三方 worker 在发射前绑定（带节点身份 `agent_id` / `phase` / `label`）；三方 session 在**每轮 `send_turn` 前重绑**——`correlation_id` 每轮变化，bind-once 会让后续轮的活动骑在轮 1 的 id 上。映射不可解析（无 data / 无 item_id / 非生命周期端点）debug 丢弃，不 raise。

8. **活动事件归 4 层 `WorkflowRun`，不进 leader 播报**：`AgentActivity` 增 `agent_id` 字段；`build_workflow_run_from_events` 把 `AGENT_ACTIVITY` 追加到对应节点的 `activity` 列表——**优先按 `agent_id` 匹配**（同 label 的循环 / parallel 分支唯一可判），`agent_id` 缺席时回退到"该 phase 下最近的 running 同 label 节点"。leader 侧 `WorkflowHandler` 对 per-agent 事件一律 `None`（太吵），与既有"per-agent 事件归 4 层表示"的边界一致。

事件同时经 `WorkflowObserver.on_event → SwarmflowTool._publish` 上图到 `WORKFLOW_PROGRESS` 团队事件（`text` 字段携带 `message`），因此**原始事件流**与**4 层快照**两条消费路径都能拿到活动。

## 拒绝的方案

- **让 leader 逐条播报工具活动**：leader 是旁观者，per-agent、高频（每 worker 秒级）的叙述会淹没真正的中途里程碑（phase / human 等待）。活动属 4 层表示，由前端按需渲染。
- **只按 label 归位活动**：同 label 的节点在 for 循环 / `parallel` 里并行时无法区分，活动会串到错误节点。`agent_id` 是唯一可判的键，label 只作无 id 时的降级回退。
- **初版的每 worker 节流 / 只发 before 单钩**：节流（默认 1.5s）与单钩只能给"正在做什么"的粗粒度叙述，观战方丢失具体步骤、也拿不到工具结果。修订版改双钩不节流——每步一对 `tool_call` / `tool_result`，按 `tool_call_id` 配对后能看到"调了什么 → 得到什么"，事件形状结构化后前端按需渲染、不依赖刷屏频率。
- **把 `agent_id` 写进 journal 化的 opts**：opts 是用户可见的白名单 + journal 持久化内容，塞引擎内部键会污染校验面与缓存签名；注入副本即可（session turn 同款）。
- **改 `AgentBackend.run` 的位置参数列表再塞两个键**：参数列表继续膨胀且易错；本分支已把 per-call 参数收进 `_BackendCallSpec`，新键随 spec 走。

## 验证

- `test_activity_rail_emits_paired_tool_call_and_result`（初版 `test_activity_rail_emits_tool_name` / `test_activity_rail_throttles_bursts` 合并重写）：rail 在 `before_tool_call` / `after_tool_call` 各发一次；窗口内多次工具执行逐对上抛、不节流；`tool_call` 入参压缩预览；无 tool message 时回退异常 / result repr；非 `ToolCallInputs` / 空名忽略。
- `test_activity_emitter_truncates_content_at_the_guard_bound` / `test_activity_emitter_builds_agent_activity_event`：`content` 完整透传 + 防护上限截断标记；emitter 产出 `kind=AGENT_ACTIVITY` + 结构化字段 + `message`；未绑定 sink 时 no-op。
- `test_session_avatar_rail_emits_paired_activity_with_agent_id_and_correlation`：avatar 挂 rail，事件带 `agent_id` + 当前轮 `correlation_id`。
- `test_session_backend_opts_carry_node_agent_id_and_journal_stays_raw`：session turn 的 opts 副本带 `agent_id`，journal 记录保持原始 opts。
- `test_external_worker_binds_activity_sink_with_node_identity`：三方 worker 发射前绑定（带节点身份）。
- `test_external_session_rebinds_activity_sink_per_turn`：三方 session 每轮重绑 `correlation_id`。
- `test_agent_activity_is_folded_into_its_node`：活动按 `agent_id` 落到 `AgentActivity.activity`，多条累积。
- `test_agent_activity_disambiguates_same_label_by_agent_id`：同 label 两节点，活动只落到指定 `agent_id` 的那个。
- `test_agent_activity_falls_back_to_label_without_agent_id`：无 `agent_id` 时按 label 回退归位。
- 回归：`tests/unit_tests/agent_teams/workflow/` 全量 405 passed；含 `external/` + `tools/` 三目录 797 passed。

## 已知遗留

- **缺引擎级 e2e**：现有用例覆盖 rail / emitter / 4 层归位 / session 与三方的单元行为，但没有"跑一个真实 `agent()` 脚本、断言 backend 侧发出 `AGENT_ACTIVITY`"的端到端用例。补一个以脚本驱动、经 fake backend 的用例即可闭合。
- **活动是 live-only**：缓存命中的 `agent()` 根本不进 backend，resume 重放不会重发活动；`AGENT_ACTIVITY` 也不落 journal。观战方在 resume 期间看不到历史活动，只能看到新的实时活动。
- **「仅单轮 `agent()` 有活动」——已解决**：初版遗留"`agent_session` / `human_session` 的 `send_turn` 未挂 activity rail"；本修订给 avatar 挂同一 rail（per-turn `correlation_id` 对齐），三方 worker / 三方会话经 `bind_activity_sink` 映射为同一结构化事件（见决策 6/7）。
- **原始事件的上图是 fire-and-forget**：`_publish` 用 `asyncio.create_task`，活动事件在 run 结束瞬间可能来不及投递（与既有中途进度事件同样的取舍，终态另有 awaited 路径）。
