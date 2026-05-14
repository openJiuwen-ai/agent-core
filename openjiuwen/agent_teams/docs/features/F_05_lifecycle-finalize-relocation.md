# Lifecycle Finalize Relocation: kernel → runtime manager + warm-path removal

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-05-12 |
| 范围 | `openjiuwen/agent_teams/agent/coordination/kernel.py`、`openjiuwen/agent_teams/agent/recovery_manager.py`、`openjiuwen/agent_teams/agent/team_agent.py`、`openjiuwen/agent_teams/runtime/dispatch.py`、`openjiuwen/agent_teams/runtime/manager.py`、`openjiuwen/agent_teams/runtime/pool.py`、`openjiuwen/agent_teams/schema/status.py`、`openjiuwen/core/runner/team_runner.py`；`tests/unit_tests/agent_teams/runtime/test_dispatch.py`、`tests/unit_tests/agent_teams/test_runner_team_runtime.py`；同步 `docs/specs/S_03` / `S_04` / `S_06` / `S_12` |
| 测试基线 | `tests/unit_tests/agent_teams/runtime/test_dispatch.py`、`tests/unit_tests/agent_teams/test_runner_team_runtime.py`（提交时跑 `make test TESTFLAGS=...` 验证） |
| Refs | `#751` |

## 背景

`CoordinationKernel.finalize_round` 长期同时承担两件不该耦合的事：

1. round-end 的纯 cleanup：memory extract、释放 stream_queue。
2. 团队去向决策：读 `team_member.status == SHUTDOWN_REQUESTED` 与 `host.lifecycle`，
   决定调 `pause()` 还是 `stop()`，并写下 `team_member.status = READY | SHUTDOWN`。

这个耦合在 `stop_team` SDK 入口上出过具体的 bug——leader 路径里：

```
外部 caller             stream 协程
   stop_team()  ─── stop_coordination ──►
                  pool.remove(team_name)
                                       │
                                       │  stream finally
                                       ▼
                              finalize_round()
                              └─ persistent + 没有 shutdown_requested
                                 → await self.pause()
                                   重新激活 kernel？？
                                   持久化 lifecycle="paused"
                                   写 team_member READY
```

`stop_team` 把 pool entry 拆掉后，stream finally 里的 `finalize_round` 仍按
"persistent + 未 shutdown_requested → pause" 这条隐式决策跑了一遍 `pause()`。
表现是：DB 里成员状态卡在 `READY`、`lifecycle="paused"`，而 pool 里 entry 早没
了——外部"我已经叫你停了"被悄悄推回 paused。

第二个相关问题：dispatch table 里 `WARM_RECOVER` / `NEW_TEAM_IN_SESSION_WARM`
两条路径**跨 session 复用同一个 `TeamAgent` 实例**。leader 进程内的 contextvars
（特别是 `_session_id_context`）、模型客户端的 stateful 缓存、`recovery_manager`
持有的 spawn handle 在 session 间产生隐式漂移。一旦 `recover_for_existing_session`
/ `resume_for_new_session` 漏清某个字段，bug 就长成"切回原 session 拿到的是另一
个 session 的状态"。

第三个：`MemberStatus` 把"runtime 已停"的语义全压在 `PAUSED` 上——pause 路径里
leader 调 `_mark_live_teammates_paused` 把 teammate 写成 PAUSED，stop 路径里
什么都不写。结果是外部 stop_team 之后看 DB，分不出 "teammate 还在 pause" 与
"team 被外部 stop 了"。

## 决策

### 1. lifecycle 决策权下沉到 `TeamRuntimeManager`

把"pause vs stop"从 `CoordinationKernel.finalize_round` 抽出来，放到
`TeamRuntimeManager` 上：

- `manager.finalize(team_name, session_id)` —— leader 路径。规则：
  `agent.is_shutdown_requested() or agent.lifecycle != "persistent"` →
  `stop_coordination` + `pool.remove`；否则 `pause_coordination` +
  `entry.state = PAUSED`。在 Runner finally 路径上调用，pool entry 已被外部
  stop_team 拆走时是 no-op。
- `manager.finalize_member(agent)` —— teammate / human-agent 路径。先读
  `team_member.status`，若已属 `{STOPPED, PAUSED, SHUTDOWN_REQUESTED, SHUTDOWN}`
  就只关 kernel、不写状态（外部已经写过）；否则按 lifecycle 决定 stop+SHUTDOWN
  还是 pause+READY。

`CoordinationKernel.finalize_round` 缩成纯 round-end cleanup hook：memory
extract + `stream_queue = None`。不再读 `lifecycle` / `shutdown_requested`、不再
调 `pause()` / `stop()`、不再写 `team_member.status`。

### 2. kernel 引入显式 lifecycle state machine

`CoordinationKernel` 加 `_lifecycle_state: Literal["idle", "running", "paused", "stopped"]`：

- `start()` → `running`
- `pause()` 仅当 `state == "running"`，否则 no-op；末尾 `state = "paused"`
- `stop()` 仅当 `state ∈ {"running", "paused"}`，否则 no-op；末尾 `state = "stopped"`

效果：外部 `stop_coordination` 把状态机推到 `stopped` 之后，Runner finally 里
再调 `pause_coordination` / `stop_coordination`（manager.finalize 的某些分支
仍会这么做）都是空转。kernel 不会被"finally 再次 pause"硬倒回 paused 态。

### 3. `MemberStatus.STOPPED` 新枚举

新增 `MemberStatus.STOPPED`，与 `PAUSED` / `SHUTDOWN` 三者各管各的语义：

| 状态 | 含义 |
|---|---|
| `PAUSED` | 自然 round-end 的 persistent team idle |
| `STOPPED` | 外部 stop_team 把 runtime 拆掉，但 team 仍 live |
| `SHUTDOWN` | 永久退场（`shutdown_self` / 累积失败） |

`MEMBER_TRANSITIONS` 扩张：所有"非 BUSY 的活态"可转 `STOPPED`，`STOPPED` 与
`PAUSED` 对称地可转 `READY` / `RESTARTING` / `SHUTDOWN_REQUESTED` / `SHUTDOWN`
/ `ERROR`，让 `recover_team` 能直接 `STOPPED → RESTARTING`（与原 `PAUSED →
RESTARTING` 同语义）。

`_mark_live_teammates_paused` 重命名为 `_mark_live_teammates(target_status)`，
leader 的 `pause()` 路径传 `PAUSED`，leader 的 `stop()` 路径传 `STOPPED`——两
条路径对称写下"为什么 teammate runtime 不在了"。

`RecoveryManager` 把"directly restartable"集合从 `{ERROR, SHUTDOWN}` 扩到
`{PAUSED, STOPPED, ERROR, SHUTDOWN}`，避免对这些显然可直接复活的状态强行先
归一到 ERROR；`collect_live_teammates_for_session_switch` 把 `STOPPED` 加入
"not live"过滤（与 `UNSTARTED` / `SHUTDOWN` 同列）。

### 4. dispatch truth table 砍 2 路：`WARM_RECOVER` / `NEW_TEAM_IN_SESSION_WARM`

跨 session 复用同一个 `TeamAgent` 实例的两条路径下线。新规则：
`TeamRuntimeManager.activate` 在 dispatch 之前主动检查 stale pool entry——

```python
if pool_entry is not None and pool_entry.current_session_id != target_session_id:
    await self.stop_team(team_name, pool_entry.current_session_id)
    pool_entry = None
```

之后 dispatch 看到的 `pool_entry` 要么是 None，要么 session 已对齐。原来的 9
路 truth table 自然收敛成 7 路：`CREATE` / `NEW_TEAM_IN_SESSION` / `COLD_RECOVER`
/ `RESUME_FROM_PAUSE` / `REJECT_RUNNING` / `REJECT_ORPHANED` / `REJECT_INCONSISTENT`。

`decide_run_action` 收到 cross-session pool_entry（手动构造测试） →
`raise RuntimeError("dispatch invariant violated: ...")`，把"manager.activate
没做 tear-down 就来 dispatch"作为契约违反硬暴露，而不是默默回到 WARM 路径。

### 5. `TeamAgent.is_shutdown_requested`

新增的小工具，给 `manager.finalize` 用：读 teammate `team_member.status` ∈
`{SHUTDOWN_REQUESTED, SHUTDOWN}`。leader 没有 `team_member` 句柄，永远返回 False。
`SHUTDOWN` 也算"already heading out"，避免 `shutdown_self` 已经把状态写成
SHUTDOWN 之后，finalize 再把它通过 pause 分支翻回 READY。

### 6. `team_runner.py` 在 finally 调 `finalize` / `finalize_member`

`_run_team_via_runtime` / `_stream_team_via_runtime` / `_run_member_*` 的
`finally` 都加上对应调用，确保不管 stream 正常结束、异常中止、还是 cancellation
都走 finalize 路径——manager 的 idempotency 让这条路径安全可叠加。

### 7. `destroy_team` 也遵循 "stop_coordination ⇒ pool.remove" 不变量

`TeamAgent.destroy_team`（leader 自销毁路径，绕过 `Runner` 直接拆团队的低层入口）
在 `stop_coordination` 之后调一次新增的 `_remove_self_from_pool` —— best-effort
反查 `GLOBAL_RUNNER._team_runtime_manager.pool`，把自身条目摘掉。
理由与 `manager.stop_team` 一致：本特性把"runtime tear-down ⇒ pool 失活"
固化为不变量（`manager.finalize` / `stop_team` / `delete_team(force=True)` 均
遵循），`destroy_team` 作为同语义的低层入口必须对齐。失败容忍：pool 不存在、
manager 未懒构造、session 不匹配都退化成 no-op + warning，不影响
`force_clean_team` 自身的返回。

## 拒绝的方案

### A. 在 `finalize_round` 里加 `if pool_entry is None: return` 兜底

最小改动：让 finalize_round 检查 pool 状态，pool 没了就别再 pause/stop。

否决理由：
1. **决策权仍在错的层**。kernel 还是要 import / 反查 pool，违反"kernel 不感知 runtime/pool"的边界（S_03 vs S_06 的分层）。
2. **掩盖了真问题**——issue 不是"pool 已经没了所以别 pause"，而是"finalize_round 根本不应该决定 pause 还是 stop"。pause 写 lifecycle 到 session、写 PAUSED 到 DB，这些都是 round-end cleanup 不该有的副作用。
3. 解决了 leader 路径的一种竞态，仍解决不了 teammate 路径的另一种——`shutdown_self` 写 SHUTDOWN 后 finally 又跑去 pause 写 READY 的同类问题。

### B. 让 `CoordinationKernel.stop()` 也写 `team_member.status = SHUTDOWN`

让 kernel 在 stop 路径里把所有 teammate 直接标 SHUTDOWN，与 `shutdown_self` 对齐。

否决理由：
1. teammate 持久态从此一并被 kernel 写——但 `recover_team` 会被 kernel.start 的"全员 SHUTDOWN → clean_team"自保撤销整个团队。外部 `stop_team` 应当"team 仍 live、可以再 recover"，写 SHUTDOWN 等于把团队解散。
2. 与 `stop_team` 真实语义不符。`SHUTDOWN` 是逻辑退场，不是"runtime 暂停"。这就是 `STOPPED` 状态存在的全部理由。

### C. 给 dispatch 加 `pool_entry.current_session_id == target_session_id` 的早 reject

让 dispatch 直接拒绝跨 session 的复用请求，调用方收到 reject 后自己手动 stop+rerun。

否决理由：
1. 把"切 session"这种正常用例变成两步操作，加重了 SDK 调用方的心智负担——`Runner.run_agent_team(agent_team=spec, session=new_id)` 这个最常见入口本就应自动处理 session 切换。
2. 早期 7 路 truth table 设计就是要让 dispatch 完备分类、调用方只看 action；让 dispatch 反过来要求"调用方先做 X 才能再 dispatch"是反向。
3. stop+rebuild 决定本就属于"team lifecycle"层的 manager，dispatch 这一层是 pure decision、应该看完整快照后给出动作；把动作藏到 reject reason 里是错。

### D. 保留 `WARM_RECOVER`，把"清干净 contextvars / 客户端缓存"做成 `recover_for_existing_session` 的责任

让 warm 路径继续存在，但要求 `recover_for_existing_session` 显式清掉所有跨 session
漂移的 in-memory 状态。

否决理由：
1. 复用同一个 leader Python 实例换 session 是个"得做完所有清理才安全"的脆弱设计——任何下游字段（新加的 cache / 新加的 contextvar）默认都会破。这种"必须知道所有间接状态才能安全运行"的契约是 bug 温床。
2. 实际收益是省一次 `spec.build()` + leader 进程 boot。但 leader 是单进程内的内存对象，构造成本与一次 round 内的 LLM 调用比可以忽略；teammate 进程通过 spawn handle 重起反正不复用。
3. 砍掉之后 `_apply_action` 的 cold 路径只剩三种 + same-session 复用，控制流明显简化。这是用一次性的"少跑一次 build" 换日常的复杂度——不划算。

### E. 把 `manager.finalize` 写在 `Runner._TeamRunnerMixin` 里、不暴露在 manager 上

让 Runner 直接读 `pool_entry`，决策代码就近放 Runner。

否决理由：
1. Runner 是 facade 层，本来就尽量薄；把 pool/lifecycle 决策塞进去会让 Runner 摸到 `RuntimeState` / `agent.is_shutdown_requested` / `lifecycle` 这些细节。
2. `manager.finalize` 是 SDK facade 的一部分（与 `pause` / `stop_team` 同列）——后续 CLI / 测试也可能直接调，便于外部 hook 进 round-end 决策。
3. 与 `finalize_member` 对称：teammate 路径没有 pool，但 manager 仍然是合适的归属（lifecycle 决策、`_MEMBER_FINALIZED_STATUSES` 这套规则属于"team runtime lifecycle"层）。

## 数据结构 / 状态机

### `MemberStatus`

新增枚举值 `STOPPED = "stopped"`。`MEMBER_TRANSITIONS` 表扩张：

```
READY      -> ... | STOPPED | ...
BUSY       -> ... | STOPPED | ...
PAUSED     -> ... | STOPPED | ...
STOPPED    -> READY | RESTARTING | SHUTDOWN_REQUESTED | SHUTDOWN | ERROR
RESTARTING -> ... | STOPPED | ...
ERROR      -> ... | STOPPED | ...
```

### `RunActionKind`

砍：

- `WARM_RECOVER`
- `NEW_TEAM_IN_SESSION_WARM`

保留 7 个：`CREATE` / `NEW_TEAM_IN_SESSION` / `COLD_RECOVER` / `RESUME_FROM_PAUSE`
/ `REJECT_RUNNING` / `REJECT_ORPHANED` / `REJECT_INCONSISTENT`。

### `CoordinationKernel`

新字段：`_lifecycle_state: Literal["idle", "running", "paused", "stopped"]`。

`finalize_round` 缩容：

```python
async def finalize_round(self) -> None:
    if self._host.resources.memory_manager:
        await self._host.resources.memory_manager.extract_after_round()
    self._host.stream_controller.stream_queue = None
```

不再调 `pause()` / `stop()`、不再写 `team_member.status`、不再读 `lifecycle`
/ `shutdown_requested`。

### `TeamRuntimeManager`

新方法：

- `async def finalize(self, *, team_name: str, session_id: str) -> None`
- `@staticmethod async def finalize_member(agent: TeamAgent) -> None`

`_MEMBER_FINALIZED_STATUSES = frozenset({STOPPED, PAUSED, SHUTDOWN_REQUESTED, SHUTDOWN})`
作为 `finalize_member` 跳过状态写的判定集。

`activate` 多了 stale pool entry tear-down 的前置：当 `pool_entry.current_session_id
!= target_session_id`，先 `await stop_team(...)`、把 `pool_entry = None` 再走 dispatch。

## 验证

- `tests/unit_tests/agent_teams/runtime/test_dispatch.py`：
  - 新增 `test_raises_when_pool_entry_on_other_session`：dispatch 收到 cross-session
    pool entry 时抛 `RuntimeError("dispatch invariant violated: ...")`。
  - 删除 `test_warm_recover_when_pool_on_other_session_and_target_bucket_exists`
    与 `test_new_team_in_session_warm_when_pool_on_other_session_and_target_bucket_missing`。
  - truth table 参数化用例不再生成 cross-session 行；保留断言
    `assert session_match, "cross-session pool entries are torn down before dispatch"`
    防止未来回归。
- `tests/unit_tests/agent_teams/test_runner_team_runtime.py`：
  - `test_runner_team_runtime_manager_resumes_new_session_and_recovers_history` 改名
    `test_runner_session_switch_stops_and_rebuilds`，断言 round-two 走
    `new_team_in_session`（cold path）而非 `new_team_in_session_warm`；
    `active_agent.resume_calls == [session_two]`；至少触发一次 `stop_coordination`。

提交前跑：

```
make test TESTFLAGS="tests/unit_tests/agent_teams/runtime/test_dispatch.py tests/unit_tests/agent_teams/test_runner_team_runtime.py"
```

并加跑 `tests/unit_tests/agent_teams/test_team_agent.py` / `test_team_agent_tools.py`
以确保 `MemberStatus.STOPPED` 加入 transitions / RecoveryManager 调整未引入回归。

## 已知遗留

1. `team_runner.py` 的 `finalize` / `finalize_member` 调用对 base team（`base=True`）
   路径不适用——base team 不入 pool、不持 `team_member`。当前仅在 team-agent 分支
   的 finally 里调，base 分支保持原状。未来若 base team 也需要 round-end hook，应
   设计独立的 finalize 入口、不要复用本套语义。
2. `MemberStatus.STOPPED` 已对 `recovery_manager` 与 `_mark_live_teammates` 接入，
   但 SDK 面向用户的 monitor / status 报表仍按"PAUSED + SHUTDOWN"两态显示。下次
   monitor 改版时把 `STOPPED` 暴露出来（与 `PAUSED` 同一显示组、附"externally
   stopped"标记）。
3. dispatch 拆掉 WARM 路径后，session 切换会重新构造 `TeamAgent` + 重新 spawn
   teammate 子进程，相比原 warm 复用增加了一次 build + spawn 成本。同 session
   内的多轮 `pause → resume` 仍走 `RESUME_FROM_PAUSE`，零成本。如果未来观测到
   "频繁跨 session 切换"成为热点，再评估"build 缓存"独立机制——不要回到 WARM 复用。
