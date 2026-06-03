# Agent Teams Runtime

`TeamAgent` 对象池 + 派发 + 并发门禁子系统。`Runner` 通过这一层把 `TeamAgentSpec`（或已激活的 team_name 字符串）映射成可被 SDK facade（`interact_agent_team` / `register_human_agent_inbound` / `pause_agent_team` / `stop_team` / `release_session` / `delete_team`）操纵的 pool entry。

## 模块构成

| 文件 | 职责 |
|---|---|
| `pool.py` | `TeamRuntimePool` / `ActiveTeam` / `RuntimeState`。pool key 是 `team_name`，同一 team 在内存中至多一个 `ActiveTeam` 实例；同 session 多 team 通过 pool 多 entry 自然支持；切 session 由 `manager.activate` 在 dispatch 前 `stop_team` 拆掉 stale entry。`RuntimeState.RUNNING / PAUSED` 是运行时状态（与 `schema.team.TeamLifecycle` "temporary/persistent" 不同） |
| `gate.py` | `InteractGate` / `AdmissionTicket`。run / interact 并发门禁：`admit / consume_done / close_and_drain / reset`。每个 `ActiveTeam` 自带一个 gate |
| `dispatch.py` | `decide_run_action(...)` 纯函数 + `RunAction` / `RunActionKind`。7 路 truth table：`CREATE / NEW_TEAM_IN_SESSION / COLD_RECOVER / RESUME_FROM_PAUSE / REJECT_RUNNING / REJECT_ORPHANED / REJECT_INCONSISTENT`，其中 `DB=False + bucket=True` 再由 `db_state` 区分 pending/cleaned 可重建与真实 orphan。前置契约：调到这里时 `pool_entry` 要么是 None，要么 `current_session_id == target_session_id`——跨 session entry 由 `manager.activate` 提前 tear-down |
| `metadata.py` | session checkpoint 的 per-team namespace 读写：`read_team_namespace / write_team_namespace / merge_team_namespace / read_team_names_in_session / read_team_db_state / merge_team_db_state`。状态结构 `state["teams"][team_name] = {spec, context, model_allocator_state, lifecycle, db_state}`，按 team 分桶；`db_state` 取值为 `pending_create / created / cleaned` |
| `manager.py` | `TeamRuntimeManager`：持有 `TeamRuntimePool`；`activate` 先 tear-down stale cross-session entry，再用 `decide_run_action` 派发后调用 `_apply_action` 执行副作用；`finalize` / `finalize_member` 在 Runner finally 上决定 pause vs stop（kernel 的 `finalize_round` 不再决策）；`pause / interact / stop_team / release_session / delete_team` 通过 pool 查 entry。`interact` 接 `InteractPayload`，走 gate 的 `admit / consume_done`，分发到 `UserInbox`（GodView / Operator）或 `HumanAgentInbox`（HumanAgent） |

## 行为铁律

- **派发决策是纯函数**：`decide_run_action` 只看 `(team_in_db, team_in_session, team_db_state, pool_entry, target_session_id, target_team_name)`，无副作用。manager 把 IO 收集进派发输入，然后 `_apply_action` 才动 TeamAgent / pool / session 这三类副作用源。改派发时改 `dispatch.py`，不要把决策逻辑塞回 manager。
- **lifecycle 直达 TeamAgent**：`_apply_action` 直接调 `TeamAgent.recover_from_session` / `agent.recover_team` / `agent.resume_for_new_session`，没有任何 `factory.*` 中间层（已删除）——避免 wrapper 上沉淀 runtime 透传参数。
- **同一 team 在内存中只有一个实例，跨 session 必走 stop+rebuild**：`activate` 在 dispatch 前检查 `pool_entry.current_session_id`，与目标 session 不一致就 `await stop_team(...)` + `pool.remove`、再走 cold 路径重建；不要让 pool 出现多个相同 `team_name` 的 entry，也不要让同一个 `TeamAgent` 实例跨 session 复用（曾经的 `WARM_RECOVER` / `NEW_TEAM_IN_SESSION_WARM` 已删，见 F_05）。
- **InteractGate 的生命周期与 run cycle 对齐**：`run_agent_team[_streaming]` 退出 `finally` 先调 `manager.finalize`（leader 路径，决定 pause/stop + 更新 pool entry），再调 `_close_team_interact_gate`（Runner 内 helper）；`RESUME_FROM_PAUSE` 在 activate 时调 `gate.reset()` 让下一个 cycle 重新放行。`member=True` 路径在 finally 调 `manager.finalize_member` 收 teammate / human-agent 的 kernel。
- **finalize 决策权归 manager，不归 kernel**：`CoordinationKernel.finalize_round` 只做 memory extract + 释放 stream_queue；pause vs stop（`shutdown_requested or lifecycle != "persistent"` → stop+remove；否则 pause+state=PAUSED）的判定与 leader pool entry / teammate `team_member` 持久状态的写入都属 `manager.finalize` / `finalize_member`。不要让 kernel 反向决定团队去向——那条路径会被外部 `stop_team` 撞穿。
- **静止前置**：`release_session` / `delete_team` 在 pool 仍持有相关 entry 时报 `AGENT_TEAM_BUSY_INVALID`（ValidationError），调用方必须先 `stop_team`。
- `Runner` 在 `_get_team_runtime_manager()` 里 lazy import 它，避免子进程 bootstrap 时拉链。

## Stream 生命周期 ≠ OuterLoop 单轮

`Runner.run_agent_team_streaming` 的 stream 不会因为 leader DeepAgent 单轮 OuterLoop "all tasks completed, controller cleaned up" 就结束。stream 的真正终止由 team 层显式动作触发：`pause_agent_team` / `stop_agent_team` / `clean_team`（或退出时 `_close_team_interact_gate` 的 finally 收尾，但那也得 `agent.stream(...)` 自身先返回）。临时团队 leader 调用 `clean_team` 工具时，stream 的结束机制是：`TeamBackend.clean_team` 成功回调同步置位 `TeamAgentState.team_cleaned`，`StreamController._run_one_round` 在 round-end 读到后 `close_stream()` 入队 `None`——不依赖会与 round-end 竞态的 `TeamCleanedEvent` 总线事件（leader 故意忽略自己的 CLEANED 事件，见 F_10 / S_02）。leader OuterLoop 跑完一轮后会 idle 等下一个唤醒事件（worker 回报、用户 interact 等），dispatcher 仍在调度，pool entry 仍 `RUNNING`。基于"OuterLoop 完成 = stream 结束 = entry 失活"做的判断都是错的——遇到 `interact_agent_team` 返回 `not_active` 时，先查 entry 是否在 pool 里，而不是怀疑 stream 已收尾。

## 公共入口：spec / team_name + base 分流（leader-only pool）

`Runner.run_agent_team*` 公共表面只有这一对方法，按 keyword-only flag 分流（互斥）：

- **`base=False, member=False`（默认，agent_teams 路径）**：接 `str | TeamAgentSpec`。
  - **Spec 路径**：`manager.activate(spec, ...)` 走 dispatch truth table 拿 / 建 entry。这是 leader 进 pool 的唯一公共路径。
  - **str 路径**：把 `team_name` 当 shorthand，复用已被 spec 激活过的 pool entry（拿 `entry.agent.spec` 反推后再次 `activate`）。pool 里没 entry 直接 `AGENT_TEAM_CONFIG_INVALID`——首次必须传 spec。
- **`base=True`（multi_agent 路径）**：接 `str | BaseTeam`。Facade 直接转到 instance method `_RunnerImpl._run_base_team*`（`str` 走 `resource_mgr.get_agent_team`，`BaseTeam` 直通），跟 pool / `manager.activate` 没有关系。该 instance method 是实现细节（`_` 前缀），**不在 `Runner` 上有 facade**。
- **`member=True`（spawn 路径）**：接已构建的 `BaseAgent` 实例（teammate / human-agent）。跳过 activate/dispatch，**不入 pool**，直接 `agent.invoke` / `agent.stream`，专供 `inprocess_spawn` / `child_process` 使用。

`Runner.interact_agent_team` / `register_human_agent_inbound` 通过 `_resolve_entry` 找 entry，entry 永远来自 `base=False, member=False` 的 spec 路径。

约束：

1. **Pool 只持 leader**。leader 由 spec 路径（`manager.activate` → `_apply_action` 写入）入 pool；teammate / human-agent 走 `Runner.run_agent_team*(member=True)` 入口（spawn 调用），完全不碰 pool。pool key 是 `team_name`，一个 team 只有一个 leader 占位。
2. **`manager` 没有"已 build 实例 → pool"的快捷入口**。早期版本提供过 `register_instance` 作为这种快捷，已删除——pool 写入语义只剩一种（spec 经 `activate`），避免双入口下的 stale state 与漂移。
3. **stream 退出 finally 关 gate**：默认 spec 路径的 finally 调 `_close_team_interact_gate`，run cycle 结束后 `interact_agent_team` 拿到的是 `gate_closed` 而非 `not_active`。`base=True` BaseTeam 路径与 `member=True` spawn 路径因为不入 pool，不参与 gate 生命周期。
