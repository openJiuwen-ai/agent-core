# F_28 NativeHarness 收编 TeamHarness / StreamController

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-06-03 |
| 范围 | `harness/team_harness.py`、`harness/protocol.py`、`agent/stream_controller.py`、`agent/member_runtime.py`、`agent/coordination/kernel.py`、`agent/agent_configurator.py`、`agent/resources.py`、`agent/team_agent.py`、`external/runtime.py`、删 `rails/first_iteration_gate.py` |
| 测试基线 | `tests/unit_tests/agent_teams/` 1326 passed / 16 skipped / 0 failed |
| Refs | #984 |

## 背景

阶段 A（[[F_27_native-harness-task-loop]]）让 `NativeHarness` 接管 DeepAgent 的整个
task loop，并定义了并发安全交互面（`start` / `stop` / `outputs` / `send` /
`abort` / `pause` + `on_state_changed` / `on_round` 事件）。但 team 侧仍有一套**平行
的旧交互机制**：

- `TeamHarness.run_streaming(inputs, session_id)` 把单轮 `Runner.run_agent_streaming`
  generator 暴露给 `StreamController`；
- `StreamController` 自己驱动 round（`_run_one_round` / `_execute_round`）、排
  `pending_inputs` / `pending_interrupt_resumes`、两阶段 `cooperative_cancel`、retry
  循环、`_run_retrying_stream`；
- `FirstIterationGate` rail 用异步信号挡住 steer / follow_up，直到 agent 真正进入
  task loop。

这套机制与 NativeHarness 的单 supervisor 模型重复且语义冲突（谁驱动 round？谁是 state
writer？steer 窗口何时打开？）。阶段 B 的诉求：**让 NativeHarness 的新交互面成为
TeamHarness 唯一的交互抽象**，StreamController 退化为纯转发 + 状态映射层，并让外部
CLI 成员也实现同一抽象。

## 数据结构 / 状态机

- **两层契约**：底层 `HarnessProtocol`（`harness/protocol.py`，NativeHarness 直接实现，
  TeamHarness 组合转发）+ 上层 `MemberRuntime`（`agent/member_runtime.py`，superset
  `HarnessProtocol` 的 `start(team_session=)` + team 专属 rail/memory/customizer hook）。
  coordination / configurator / StreamController 一律按 `MemberRuntime` 编程。
- **HarnessState**：`IDLE` / `RUNNING` / `PAUSED` / `TERMINATED`（CLI runtime 用无 PAUSED
  的子集）。NativeHarness 仅 supervisor 协程 mutate。
- **事件**：`harness.state`（old/new/session_id）+ `harness.round`（kind ∈
  started/finished/aborted/paused/failed，round_id，result），StreamController 经
  `on_state_changed`/`on_round` 订阅映射到 MemberStatus / ExecutionStatus（kwargs 按
  回调声明参数 narrow，复用 `AsyncCallbackFramework`）。

## 决策

1. **NativeHarness `configure/run` 分离**（additive，阶段 A `start()` 行为不变）：
   `prepare_config()`（sync：card/config/rails，供 build 时 customizer / workspace 读）+
   `prepare()`（async：+ ensure_initialized）+ `start(session)`（仅 supervisor + 绑 session）。
   让 TeamHarness 能在 sync 上下文（build 时）跑 customizer / 读 workspace，无需 async。

2. **TeamHarness 组合单个 NativeHarness（per-cycle）**：build 时构造 native +
   `prepare_config()`；configurator 在 sync 上下文跑 customizer / 读 workspace。
   `start(team_session)` 建 child agent_session（`create_agent_session` 复用 team
   session_id → DeepAgentState 按 session_id 持久化，`share_stream_writer=False`）+
   native.start + 首迭代 plan-mode seeding；`stop()`→native TERMINATED（保留实例供
   `deep_config` 读），下个 cycle `start` 检测 TERMINATED 重建（重跑缓存的 customizer）。
   跨 cycle 状态靠 session_id 恢复，不需 native rebind。

3. **StreamController 瘦身**：删 round 驱动 / 三标记 / pending / retry-loop。`start()` 挂
   `_forward_outputs`（消费 `runtime.outputs()`→`_tag_chunk`→stream_queue+observers）+
   注册 `on_state_changed(_map_state)`/`on_round(_map_round)`；`stop()` 停 forwarder。
   `_map_state` RUNNING→BUSY、IDLE→READY+`_on_idle_settled`（team_cleaned/shutdown close、
   wake mailbox、completion poll）；`_map_round` 走 EXECUTION_TRANSITIONS 合法多步边。
   cancel/cooperative/drain 转发 `abort(immediate=)`。

4. **transient retry 移到 forward 层**：`_forward_outputs` 的 `_handle_retry` 检测
   task_failed（181001 可重试且未超 `_MAX_RETRY_ATTEMPTS=10`）→ swallow 失败轮 +
   `send(_RETRY_QUERY)` 重驱（follow-up round）；exhausted / non-retryable → error log +
   **转发 task_failed 给 consumer（不再 raise）**——单 supervisor 模型移除了旧
   `_execute_round` 的 raise-based exhaustion。

5. **resume 透传**：`send(InteractiveInput)` 经 `submit_round` 透传，executor
   `_extract_interactive_input` 原生 resume；resume round 单轮语义，完成 settle 到 IDLE
   不续 task_plan。StreamController 仅校验 `is_pending_interrupt_resume_valid` 后转发，
   不再 client 侧排队。

6. **删 FirstIterationGate**：`kernel.enqueue_mailbox_after_first_iteration` 改直接
   enqueue POLL_MAILBOX（native start 完成即 ready）；kernel `start` 在 bind_session 后
   `harness.start(team_session)`+`stream_controller.start()`，`finalize_round` 加
   `stream_controller.stop()`+`harness.stop()`。

7. **外部 CLI 成员实现同一 MemberRuntime 面**：`external/runtime.py` 的 `_CliRuntimeBase`
   升级为 adapter——复用 `harness/outputs.py` 的 `_OutputIterator`/`_END` +
   `AsyncCallbackFramework`，把每个 flavour 的单轮 `_drive` async generator 适配成
   `outputs()`（内部 queue）+ `on_round`（turn started/finished/aborted）+
   `start/stop/send(immediate)/abort(immediate)/state/session_id`。`send` IDLE 起后台
   turn 任务（非阻塞，对齐 native）；abort 不 cancel 任务而是 set flag + 信号 `_abort_turn`
   让 `_drive` 自然返回；rail/memory hook 为 no-op，pause / immediate-rollback 降级
   no-op（子进程 turn 无中段快照）。删除已死的 `run_streaming`（不在协议里）。

## 拒绝的方案

- **StreamController-backed harness**（阶段 A S_18 设想的"阶段 2"）：让 StreamController
  自身实现 HarnessProtocol，与 NativeHarness 并列两个 harness 实现。拒绝——会保留两套
  round 驱动逻辑，正是要消除的重复；改为 TeamHarness 组合单个 NativeHarness，
  StreamController 退化为转发层。
- **TeamHarness 跨 cycle rebind 同一 native**：保留 native 实例、stop 后 rebind 新
  session。拒绝——stopped native 是 terminal（supervisor 已退出），rebind 要重建内核且
  状态边界含糊；改为 stop→TERMINATED + 下 cycle 重建，跨 cycle 状态全靠 session_id 恢复，
  更简单且与 DeepAgentState 持久化天然一致。
- **CLI runtime abort 直接 cancel turn 任务**：拒绝——cancel 会让 `_drive` 抛
  CancelledError、丢失 round 事件、phase 卡 RUNNING；改为 set flag + 信号子类
  `_abort_turn`，`_drive` 检查 flag 自然返回，`_drive_turn` 据此 fire `aborted`。
- **保留 `run_streaming` 作便利单轮入口**：拒绝——不在 MemberRuntime 协议、生产已不调用，
  保留即死代码；机制测试改直接驱动 `_drive`，集成测试改走 `send`/`outputs`。

## 验证

- `tests/unit_tests/agent_teams/`：1326 passed / 16 skipped / 0 failed（阶段 B 测试起点
  89 failed，全部为旧接口过时测试）。
- 重写 / 修正测试文件：`test_cli_agent`、`test_external_cli_spawn`（机制测试驱动 `_drive`、
  abort 走真实面、新增 surface 行为测试）、`test_stream_controller`（全文件重写，
  `_FakeRuntime` 替代旧 TeamHarness）、`test_team_agent_retry`（forward 层 retry 语义）、
  `test_harness`（build 用 fake native、runtime/state 转发）、`test_team_agent_coordination`
  （nudge 测试 `_start_agent`/`steer`→`deliver_input`、删 first_iter_gate 测试）、
  `test_runner_team_runtime`（plan-mode 测试驱动 `_seed_initial_plan_mode`）、
  `test_human_agent_setup`（去 gate）。

## 已知遗留

- **修了一个伴生生产 bug**：`TeamHarness.abort/pause` 原盲目转发到 `native`，未 start 时
  触发 `_require_alive` 抛错（session=None 协调启动后 teardown 会崩）。加 `_is_cycle_active()`
  守卫（`_active_agent_session` 非空 = cycle 活跃）降级为 no-op。
- **重型 inprocess E2E 待补**：leader+teammate 实进程 chunk fan-out / deliver / cancel /
  resume / 状态转移的端到端验证目前靠单测层覆盖接线，未补一条 live 进程 E2E。
- **subprocess 模式 teammate chunk 转发**仍是 unimplemented（messager-driven observer），
  与本次收编正交，沿用 S_05 既有遗留。
