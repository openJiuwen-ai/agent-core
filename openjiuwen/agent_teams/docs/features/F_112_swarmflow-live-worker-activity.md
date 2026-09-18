# Swarmflow 单轮 worker 实时活动（agent_activity）

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-09-17 |
| 范围 | `workflow/engine/progress.py`（新增 `ProgressKind.AGENT_ACTIVITY`）、`workflow/engine/backends/base.py`（`progress_sink` / `bind_progress_sink` 接缝）、`workflow/engine/runner.py`（`run_workflow` 绑定）、`workflow/engine/primitives.py`（`_BackendCallSpec` 携带 `agent_id` + `call_key`）、`workflow/backends/team_worker_backend.py`（`SwarmflowActivityRail` + `_make_activity_emitter`）、`workflow/schema.py`（`AgentActivity.agent_id` + 活动归位）、`workflow/AGENTS.md`、`docs/specs/S_18_swarmflow-engine-and-worker.md`、测试 `test_worker_backend.py` / `test_observer.py` |
| 测试基线 | `tests/unit_tests/agent_teams/workflow/` 全量通过（299 passed），含新增 4 层归位用例 4 例（同 label 消歧、无 id 回退、多活动累积） |
| Refs | #773 |

## 背景

Swarmflow 的单轮 `agent()` 在引擎眼里是**一次不透明调用**：发 `AGENT_STARTED`，等 `backend.run` 返回，发 `AGENT_COMPLETED`。一个 worker 实际可能跑几十秒、调十几次工具，而观战方（Swarm Map / Monitor）在这段时间里只看到"开始…结束"两个点，无法展示"它正在写文件 / 跑命令"。

引擎已有的进度接缝 `Runtime.progress_sink` 只对 engine 自己的起止钩子开放（`phase()` / `log()` / `agent()` 起止），backend 中途**没有**发事件的手段。本特性补上这条接缝，并让 worker 的工具活动经它上抛。

## 决策

1. **新增 `ProgressKind.AGENT_ACTIVITY`**，字段沿用既有事件模型：`agent_id`（节点身份）/ `phase` / `label`，`message="tool: <name>"` 承载一句话叙述。事件仍是**业务无关、无时间戳**的——消费方在 agent_teams 层补时，保持引擎 resume 确定性。

2. **给 `AgentBackend` 增一条 backend → engine 的进度接缝**：`progress_sink`（只读）+ `bind_progress_sink(...)`，`run_workflow` 建好 `Runtime` 后一次性绑定为 `rt.progress_sink`。未绑定时 `progress_sink is None`，backend 静默（测试后端零成本）。这条接缝是通用的——任何 backend 都能在单次调用中途上抛进度，不绑定具体业务。

3. **`_BackendCallSpec` 统一携带 per-call 身份**（本分支把 `_call_backend` 的位置参数收进 spec，见 `1eb59e6`）：`agent_id` 与 `call_key` 都来自 `agent()` 的 journal call-path key `ks`。`agent_id` 被注入 **opts 的副本**（`{**opts, "agent_id": ks}`）——journal 记录的仍是原始 opts，不污染白名单校验面与持久化内容；`call_key` 则作为 `backend.run` 的关键字参数原样透传（F_96）。

4. **worker 侧用 rail 钩工具调用，每 worker 节流**：`SwarmflowActivityRail.before_tool_call` 读 `ToolCallInputs.tool_name`，按 `min_interval_s`（默认 1.5s）节流后调 `_make_activity_emitter(agent_id, phase, label)`；emitter 把 `message="tool: <name>"` 的事件投给 `backend.progress_sink`。rail 仅在 `opts` 带 `agent_id` 时挂载（即经引擎的 `agent()` 调用），直接调用 backend 的测试场景不挂。

5. **活动事件归 4 层 `WorkflowRun`，不进 leader 播报**：`AgentActivity` 增 `agent_id` 字段；`build_workflow_run_from_events` 把 `AGENT_ACTIVITY` 追加到对应节点的 `activity` 列表——**优先按 `agent_id` 匹配**（同 label 的循环 / parallel 分支唯一可判），`agent_id` 缺席时回退到"该 phase 下最近的 running 同 label 节点"。leader 侧 `WorkflowHandler` 对 per-agent 事件一律 `None`（太吵），与既有"per-agent 事件归 4 层表示"的边界一致。

事件同时经 `WorkflowObserver.on_event → SwarmflowTool._publish` 上图到 `WORKFLOW_PROGRESS` 团队事件（`text` 字段携带 `message`），因此**原始事件流**与**4 层快照**两条消费路径都能拿到活动。

## 拒绝的方案

- **让 leader 逐条播报工具活动**：leader 是旁观者，per-agent、高频（每 worker 秒级）的叙述会淹没真正的中途里程碑（phase / human 等待）。活动属 4 层表示，由前端按需渲染。
- **只按 label 归位活动**：同 label 的节点在 for 循环 / `parallel` 里并行时无法区分，活动会串到错误节点。`agent_id` 是唯一可判的键，label 只作无 id 时的降级回退。
- **不节流、每个 tool call 都发**：worker 一个回合可能连续调多个工具，逐条上抛让事件流膨胀且观感抖动；每 worker 1.5s 节流保证"正在做什么"始终新鲜，又不刷屏。
- **把 `agent_id` 写进 journal 化的 opts**：opts 是用户可见的白名单 + journal 持久化内容，塞引擎内部键会污染校验面与缓存签名；注入副本即可。
- **改 `AgentBackend.run` 的位置参数列表再塞两个键**：参数列表继续膨胀且易错；本分支已把 per-call 参数收进 `_BackendCallSpec`，新键随 spec 走。

## 验证

- `test_activity_rail_emits_tool_name` / `test_activity_rail_throttles_bursts`：rail 在 `before_tool_call` 上抛工具名；非 `ToolCallInputs` / 空名忽略；窗口内多次只发一次。
- `test_activity_emitter_builds_agent_activity_event`：emitter 产出 `kind=AGENT_ACTIVITY` + `agent_id`/`phase`/`label`/`message`；未绑定 sink 时 no-op。
- `test_agent_activity_is_folded_into_its_node`：活动按 `agent_id` 落到 `AgentActivity.activity`，多条累积。
- `test_agent_activity_disambiguates_same_label_by_agent_id`：同 label 两节点，活动只落到指定 `agent_id` 的那个。
- `test_agent_activity_falls_back_to_label_without_agent_id`：无 `agent_id` 时按 label 回退归位。
- 回归：`tests/unit_tests/agent_teams/workflow/` 全量 299 passed。

## 已知遗留

- **缺引擎级 e2e**：现有用例覆盖 rail / emitter / 4 层归位的单元行为，但没有"跑一个真实 `agent()` 脚本、断言 backend 侧发出 `AGENT_ACTIVITY`"的端到端用例。补一个以脚本驱动、经 fake backend 的用例即可闭合。
- **活动是 live-only**：缓存命中的 `agent()` 根本不进 backend，resume 重放不会重发活动；`AGENT_ACTIVITY` 也不落 journal。观战方在 resume 期间看不到历史活动，只能看到新的实时活动。
- **仅单轮 `agent()` 有活动**：`agent_session` / `human_session` 的 `send_turn` 走 `_attempt_calls`（非 `_call_backend`），未挂 activity rail。多轮会话的实时活动是后续可选扩展。
- **原始事件的上图是 fire-and-forget**：`_publish` 用 `asyncio.create_task`，活动事件在 run 结束瞬间可能来不及投递（与既有中途进度事件同样的取舍，终态另有 awaited 路径）。
