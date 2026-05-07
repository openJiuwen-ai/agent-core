# Agent Teams Runtime

`TeamAgent` 对象池 + 派发 + 并发门禁子系统。`Runner` 通过这一层把 spec / 已 build 的 TeamAgent 映射成可被 SDK facade（`interact_agent_team` / `register_human_agent_inbound` / `pause_agent_team` / `stop_team` / `release_session` / `delete_team`）操纵的 pool entry。

## 模块构成

| 文件 | 职责 |
|---|---|
| `pool.py` | `TeamRuntimePool` / `ActiveTeam` / `RuntimeState`。pool key 是 `team_name`，同一 team 在内存中至多一个 `ActiveTeam` 实例；同 session 多 team 通过 pool 多 entry 自然支持。`RuntimeState.RUNNING / PAUSED` 是运行时状态（与 `schema.team.TeamLifecycle` "temporary/persistent" 不同） |
| `gate.py` | `InteractGate` / `AdmissionTicket`。run / interact 并发门禁：`admit / consume_done / close_and_drain / reset`。每个 `ActiveTeam` 自带一个 gate |
| `dispatch.py` | `decide_run_action(...)` 纯函数 + `RunAction` / `RunActionKind`。9 路 truth table：`CREATE / NEW_TEAM_IN_SESSION / NEW_TEAM_IN_SESSION_WARM / COLD_RECOVER / WARM_RECOVER / RESUME_FROM_PAUSE / REJECT_RUNNING / REJECT_ORPHANED / REJECT_INCONSISTENT` |
| `metadata.py` | session checkpoint 的 per-team namespace 读写：`read_team_namespace / write_team_namespace / merge_team_namespace / read_team_names_in_session`。状态结构 `state["teams"][team_name] = {spec, context, model_allocator_state, lifecycle}`，按 team 分桶 |
| `manager.py` | `TeamRuntimeManager`：持有 `TeamRuntimePool`；`activate` 用 `decide_run_action` 派发后调用 `_apply_action` 执行副作用；`register_instance` 把已 build 的 `TeamAgent` leader 注入 pool；`pause / interact / stop_team / release_session / delete_team` 通过 pool 查 entry。`interact` 接 `InteractPayload`，走 gate 的 `admit / consume_done`，分发到 `UserInbox`（GodView / Operator）或 `HumanAgentInbox`（HumanAgent） |

## 行为铁律

- **派发决策是纯函数**：`decide_run_action` 只看 `(team_in_db, team_in_session, pool_entry, target_session_id, target_team_name)`，无副作用。manager 把 IO 收集进派发输入，然后 `_apply_action` 才动 factory / pool / session。改派发时改 `dispatch.py`，不要把决策逻辑塞回 manager。
- **同一 team 在内存中只有一个实例**：切 session 走 `recover_for_existing_session`（warm），不要让 pool 出现多个相同 `team_name` 的 entry。
- **InteractGate 的生命周期与 run cycle 对齐**：`run_agent_team[_streaming]` 退出 `finally` 调 `_close_team_interact_gate`（Runner 内 helper）；warm 路径 activate 时调 `gate.reset()` 让下一个 cycle 重新放行。
- **静止前置**：`release_session` / `delete_team` 在 pool 仍持有相关 entry 时报 `AGENT_TEAM_BUSY_INVALID`（ValidationError），调用方必须先 `stop_team`。
- `Runner` 在 `_get_team_runtime_manager()` 里 lazy import 它，避免子进程 bootstrap 时拉链。

## Stream 生命周期 ≠ OuterLoop 单轮

`Runner.run_agent_team_streaming` 的 stream 不会因为 leader DeepAgent 单轮 OuterLoop "all tasks completed, controller cleaned up" 就结束。stream 的真正终止由 team 层显式动作触发：`pause_agent_team` / `stop_agent_team` / `clean_team`（或退出时 `_close_team_interact_gate` 的 finally 收尾，但那也得 `agent.stream(...)` 自身先返回）。leader OuterLoop 跑完一轮后会 idle 等下一个唤醒事件（worker 回报、用户 interact 等），dispatcher 仍在调度，pool entry 仍 `RUNNING`。基于"OuterLoop 完成 = stream 结束 = entry 失活"做的判断都是错的——遇到 `interact_agent_team` 返回 `not_active` 时，先查 entry 是否在 pool 里，而不是怀疑 stream 已收尾。

## Spec 路径 vs 实例路径：pool 入口统一（leader-only）

`Runner.run_agent_team*` 同时接 `TeamAgentSpec` 和 `TeamAgent` 实例：

- **Spec 路径**：`manager.activate(spec, ...)` 走 dispatch truth table 拿 / 建 entry。
- **实例路径**：`_register_team_instance_if_eligible` 调 `manager.register_instance(agent, session)` 把 leader 实例放进 pool。

两条路径殊途同归——`Runner.interact_agent_team` / `register_human_agent_inbound` 通过 `_resolve_entry` 都能找到 entry。SDK 用户外面 `spec.build()` 后传 leader 给 streaming（典型 e2e 写法）跟传 spec 给 streaming 在 pool 行为上一致。

约束：

1. **Pool 只持 leader**。`_register_team_instance_if_eligible` 用 `agent.role == TeamRole.LEADER` 过滤——`inprocess_spawn` 路径下 teammate / human-agent 的 TeamAgent 实例也会路过 `Runner.run_agent_team(agent_team=teammate, ...)`，但它们不入 pool。pool key 是 `team_name`，一个 team 只有一个 leader 占位。
2. **同实例 re-entry 是 reuse**：`register_instance` 检查 pool 已存在 entry 时，若 `entry.agent is agent` 就 reuse 并刷新 session_id；不同实例占同 team_name 抛 `RuntimeError` 防 stale state。
3. **stream 退出 finally 关 gate**：实例路径的 finally 跟 spec 路径对齐调 `_close_team_interact_gate`，所以 run cycle 结束后 `interact_agent_team` 会得 `gate_closed` 而非 `not_active`——错误码语义跟 spec 路径一致。
