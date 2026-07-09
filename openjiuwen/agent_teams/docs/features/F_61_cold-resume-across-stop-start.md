# F_61 pause → stop → start 的冷恢复续跑

日期：2026-07-09

## 背景

[[F_60_native-harness-pause-abort-resume]]（第一期）把 pause / abort 语义改成 inner-iteration
级的两个正交动词，但 `resume` 只做了**进程内 warm 恢复**：harness 没被 stop 时 round 还在内存，
`kernel.start` 尾部的 `resume_paused_round()` 原地续跑。

目标语义还剩一条没兑现：**「pause 后紧接 stop，下次 start 等价于直接 resume」**。第一期做不到：

- `paused_query` 是纯内存态，`_on_stop` 会清掉；
- `TeamHarness.start` 在 native TERMINATED 时**重建一个全新 native**（状态 IDLE），而
  `resume_paused_round()` 只认 PAUSED；
- session 里写着的 `lifecycle: "paused"` 是**无人读取的死字段**。

## 决策

**只持久化一个 marker，不持久化 round 快照。**

关键观察：pause 停在干净的 inner-iteration 边界，而 run cycle 的 teardown
（`finalize_round` → `TeamHarness.stop()` → `native.stop()` + `child.commit()`）**本来就会**把那个
边界的 context 提交进 session checkpoint；冷启动时 `TeamHarness.start` 重建 native，
context / task_plan / history 由共享的 session id 恢复。

⇒ 冷恢复唯一缺的信息是：**「有一轮活干到一半」以及它的 originating query**。

- `state["teams"][team_name]["pending_resume"] = {"query": ...}`，由 `kernel.pause` 写入
  （leader-only，且仅当 harness 真的进入 PAUSED —— 没有 in-flight round 就没什么可续）。
- `kernel.start` 尾部的 `resume_paused_round()` 分两路，续跑成功后都清掉 marker：
  - **warm**：harness 仍是 PAUSED（同进程，`pause` 从不 stop 它）→ `resume()`，读内存里的 query。
  - **cold**：harness 是 IDLE（已重建）且 marker 存在 → `resume(query=marker["query"])`。
- `NativeHarness.resume(*, query=None)`：PAUSED 用内存 query（warm）；IDLE + `query` 起
  continuation round（cold，context 来自 checkpoint）。其余状态 no-op。
- `NativeHarness.paused_query` property 把该 query 暴露给协调层落盘。

### 这个 query 到底喂给谁

值得写清楚，因为它**不喂 continuation round**：`resume_continuation` 让 `_inner_invoke` 跳过
UserMessage append，context 已从 checkpoint 恢复。该 query 的唯一身份是
`ActiveRound.original_query`，供 continuation round **结束之后**的两条既有分支消费：

- `_has_remaining_tasks(...)` → `_start_round(query)`：task-plan 续轮（一个**普通** round，
  那时 query 才被 append）
- `died_abnormally` → `_start_round(query, failure_retry=True)`：一次性重试

实测（`test_cold_resume_query_drives_the_task_plan_continuation` /
`test_cold_resume_without_query_cannot_drive_the_task_plan`）：带 query 时冷恢复跑
「continuation + task-plan 续轮」两轮；query 为空时只跑 continuation 一轮，task_plan 的剩余任务
**永远推不动**。所以它不是可有可无的元数据，而是「冷恢复后 leader 还能不能继续干活」的开关。

顺带看清一件**既有**事实：task-plan 续轮会把原始 query 再 append 一次 user turn，尽管它早就在
context 里。这是 task_loop 的固有设计（每个 task iteration 让 LLM 重看目标），冷恢复只是沿用，
没有引入新的重复。

## 拒绝的方案

- **把死字段 `lifecycle:"paused"` 接进 dispatch**（F_60 当初的设想）：`decide_run_action` 根本
  不需要知道。pool entry 没了就是 `COLD_RECOVER`，这**已经是正确的派发结果**；缺的只是「恢复之后
  要续跑」，那是 kernel 层的事。让 dispatch 读 lifecycle 只会无谓扩大一个纯函数的输入面。
  `lifecycle` 保留为诊断字段。
- **持久化整个 round 快照（context messages + DeepAgentState）**：重复。teardown 的 `commit()`
  已经把它们写进 checkpoint，再存一份既冗余、又会与真实 context 漂移。
- **per-member 的 `pending_resume` map**：目前只有 leader 会走 `kernel.pause`（teammate 的 run
  cycle 走 `finalize_member`），且 pause 会 `shutdown_all_handles()` 停掉 teammate 进程、由
  `recover_team` 从 roster 重启。给一个只有单个写者的 key 做 map 是投机。
- **`Runner.resume_agent_team` / `abort_agent_team` facade**（F_60 列为第二期候选）：`resume` 已由
  `RESUME_FROM_PAUSE` / `COLD_RECOVER` + `kernel.start` 自动完成，显式 facade 冗余；`abort` 至今
  没有任何调用方。不做没人要的抽象——真需要时再加。

## 已知遗留

- **marker 与 context commit 不是原子的**：marker 在 `kernel.pause` 写进 session state，context 在
  其后的 teardown `commit()` 落盘。若进程恰好在两者之间崩溃，marker 可能先于 context 被保存，
  冷恢复就会在**较旧的 context** 上续跑。当前 checkpoint 机制没有跨二者的事务边界。
- **teammate 的 in-flight round 不跨重启**：pause 时 `shutdown_all_handles()` 停掉 teammate 进程，
  其 round 丢失；冷恢复由 `recover_team` 从 DB roster 重启它们，从头开始。既有行为，本次未改。
- **冷恢复的 context 完整性未做 E2E 验证**：单测用 mock context engine，验证的是「continuation
  round 被正确驱动、不追加 user turn、marker 被消费」。真实 checkpoint → 新 native 的 context 恢复
  依赖 `TeamHarness.start` 的 docstring 契约（"task plan / history / plan mode recover from the
  persisted session id"），值得补一条 system test。

## 验证基线

```
tests/unit_tests/agent_teams/                          1842 passed  (含 5 个 kernel warm/cold 用例)
tests/unit_tests/agent_teams/runtime/test_metadata.py    15 passed  (含 4 个 pending_resume 用例)
tests/unit_tests/agent_teams/harness/test_pause.py        9 passed  (含 cold resume + query 价值验证)
tests/unit_tests/harness/ + tests/unit_tests/agent/react_agent/   2149 passed
```

关键用例：`test_pause_persists_pending_resume_for_a_later_cold_start`、
`test_pause_records_nothing_when_no_round_was_suspended`、
`test_resume_paused_round_cold_path_consumes_the_marker`、
`test_resume_paused_round_warm_path_ignores_the_marker`、
`test_cold_resume_continues_over_restored_context`、
`test_cold_resume_query_drives_the_task_plan_continuation`、
`test_cold_resume_without_query_cannot_drive_the_task_plan`、
`test_paused_query_is_exposed_for_persistence`、
`test_clear_pending_resume_drops_only_that_key`。
