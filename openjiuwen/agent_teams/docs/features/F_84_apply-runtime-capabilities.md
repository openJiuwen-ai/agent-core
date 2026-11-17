# Hot-update model / skills via apply_runtime_capabilities

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-11-10 |
| 范围 | `openjiuwen/agent_teams/schema/runtime_capabilities.py`、`openjiuwen/agent_teams/agent/team_agent.py`、`openjiuwen/agent_teams/runtime/manager.py`、`docs/specs/S_04`、`docs/specs/S_06`、`docs/specs/S_12` |
| 测试基线 | `pytest tests/unit_tests/agent_teams/test_runtime_capabilities.py` → 3 passed |
| Refs | tip→live capability reconcile (cold recover + resume-from-pause) |

## 背景

平台侧每次请求都会重新 enrich 一份 tip `TeamAgentSpec`（例如 swarm 的
`get_swarm_enriched_team_spec`），但进程内挂载的 mounted spec 仍是 session
权威：`recover_team` / `spawn` 走 `configure(leader.spec, …)`。

两条路径因此会把 tip 上已刷新的 `model` / `skills` / pool 字段丢掉：

1. **COLD_RECOVER**：`recover_from_session` 从 checkpoint 反序列化旧 spec，
   原先只 reinject `build_context` / `embedding_config`，能力字段仍是旧值。
2. **RESUME_FROM_PAUSE**：pool 复用同一 `TeamAgent`，故意跳过 rebuild，
   mounted spec 一直停留在首次挂载时的能力快照。

## 决策

1. **抽出纯函数 `apply_runtime_capabilities(base, runtime)`**：identity /
   layout 留在 `base`；`agents[*].model` / `agents[*].skills` 与团队级
   `model_pool` / `model_pool_strategy` / `model_router` /
   `model_intelli_router` 整组取自 `runtime`。返回新 spec，不改入参。
2. **`resolve_effective_model_pool`**：与 `TeamAgentSpec.build` 同规则，把
   router / intelli-router 展开成 flat pool，供 allocator / `TeamSpec` 使用。
3. **`apply_runtime_capabilities_inplace`**：写入同一 mounted 对象引用
   （`TeamAgentBlueprint.spec`），保证 in-process teammate 共享的 tip 引用可见。
4. **COLD_RECOVER**：`recover_from_session(..., runtime_spec=tip)` 在
   `configure` 前 overlay + reinject exclude 字段，并用
   `inherit_pool_ids(checkpoint_pool, tip_pool)` 同步 `context.team_spec`。
   同步后必须刷新或清空 `context.member_model`（按旧 `model_name` 对 tip
   pool 做 `resolve_member_model`；无 pool 则置 `None`），否则
   `configure` 仍优先用 checkpoint 凭证，leader 与 teammate 会各用一套端点。
5. **RESUME_FROM_PAUSE**：`_apply_action` 在 `gate.reset()` 前调用
   `TeamAgent.apply_runtime_capabilities(tip)`；先 expand tip，再
   `update_model_pool`（保留 live `model_id`），并刷新
   `runtime_context.member_model`。禁止先清空再赋值。
6. **本轮不做 live harness / skills-visibility / TeamSkillUseRail 热重绑**：
   已在跑的 DeepAgent 实例不强制 rebuild；resume overlay 只保证 mounted
   spec / allocator / `member_model` 与**后续** spawn·configure 读到 tip。
   pause 复用路径上，live leader 的模型 client、已挂载 skill rail、以及
   已装入的 skills-visibility 仍停留在首挂载实例，直到下一次
   `configure` / spawn。原因：热换等于半套 rebuild，易与进行中的 tool
   loop 打架，并丢掉 pause 复用（contextvars / 活 handle / gate）的意义。

## 拒绝的方案

1. **每次 resume 都 stop+cold rebuild**：能力能刷新，但丢掉 pause 复用的意义
   （contextvars、活 handle、gate 状态）。
2. **只在 swarm 侧改 tip、不改 core**：checkpoint / pool 仍是 mounted 权威，
   平台层无法单方面让 `configure(leader.spec)` 看到新 skills。
3. **把整个 tip 替换成 mounted spec**：会冲掉动态成员模板、workspace、
   spawn_mode 等 layout 字段，越权。

## 验证

- 单元：`test_runtime_capabilities.py` 覆盖 overlay / 保留 base-only agent /
  inplace 同对象突变。
- 行为契约见 `S_04`（recover overlay）与 `S_06`（resume overlay）。
