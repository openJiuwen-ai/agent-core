# S_18 Harness 交互契约（HarnessProtocol / MemberRuntime）

最近一次修订日期：2026-06-16

本 spec 定义 agent_teams harness 层的对外交互契约。阶段 1（NativeHarness 接管 task
loop）的实现细节见 [[F_27_native-harness-task-loop]]；阶段 B（NativeHarness 收编
TeamHarness/StreamController）的决策见 [[F_28_native-harness-team-adoption]]；关停竞态守卫与
`add_rail` 委派见 [[F_39_swarmflow-e2e-hardening]]。

## 两层契约

阶段 B 把交互契约分成两层，调用方按层编程而非绑定具体类：

- **`HarnessProtocol`**（`harness/protocol.py`，`@runtime_checkable`）：底层并发安全
  交互表面，**独立于 harness 如何驱动底层 agent**。`NativeHarness` 直接实现；
  `TeamHarness` 组合单个 `NativeHarness` 并转发同一表面。
- **`MemberRuntime`**（`agent/member_runtime.py`，`@runtime_checkable`）：team
  coordination/configurator/StreamController 驱动的 **brain seam**——superset
  `HarnessProtocol`（`start` 形参为 `team_session`），再加 team 专属的
  rail/memory/customizer hook。默认实现是 `TeamHarness`（DeepAgent-backed）；外部
  CLI 成员由 `ExternalCliRuntime` / `ReinvokeCliRuntime` 实现同一表面，把单轮
  `_drive` 适配成多轮 surface（rail/memory hook 为 no-op，rollback/pause 降级）。

## HarnessProtocol 成员

| 成员 | 签名 | 语义 |
|---|---|---|
| `state` | `-> HarnessState`（property） | 当前生命周期阶段 |
| `session_id` | `-> str \| None`（property） | 拥有/注入的 session id，start 前为 None |
| `start` | `async (*, session: Session \| None = None)` | 初始化并启动 supervisor；可注入外部 session |
| `stop` | `async ()` | 取消在途工作、关闭输出、转 TERMINATED |
| `outputs` | `() -> AsyncIterator[OutputSchema]` | queue-backed 输出迭代器（单消费者，`_END` sentinel 终止） |
| `send` | `async (content, *, immediate=False) -> str` | 提交输入，immediate=True 注入当前 round；返回 seq id |
| `abort` | `async (*, immediate=False)` | 中止当前 round：graceful（False）/ 硬取消+回滚（True） |
| `pause` | `async ()` | 暂停当前 round；下次 send 拼接并重启 |

`MemberRuntime` 额外声明：`subscribe(*, on_state=None, on_round=None)`（单一订阅入口，
两回调 keyword-only 且可选，只注册非 None 的，kwargs 按回调声明参数 narrow）、
`has_pending_interrupt` /
`is_pending_interrupt_resume_valid` / `init_cwd_for_round`、`find_rails` /
`register_rail` / `unregister_rail` / `register_member_tools` /
`inject_member_memory` / `run_agent_customizer`、`workspace` / `sys_operation`。

**并发安全契约**：所有方法可从任意协程并发调用，无需外部加锁。NativeHarness 侧以单
supervisor 协程 + control channel 串行化所有状态转换（唯一 state writer）；CLI runtime
侧单轮 turn 任务串行驱动。

**关停竞态不变量**：control 命令的 ack future 解析必须**幂等于被遗弃**——supervisor 在
`set_result` 前经 `NativeHarness._ack`（`if fut is not None and not fut.done()`）守卫。
`stop()` 先 `async_tool_runtime.cancel_all()`，会取消正 `await send-ack` 的异步工具完成注入
任务，使其 ack future 变 cancelled；supervisor 随后 dispatch 那条已入队的 `_CmdSend` 时若
裸 `set_result` 就抛 `InvalidStateError` 打崩 supervisor。守卫后"无人再等的 ack"直接跳过
（与错误路径早有的 `not ack.done()` 守卫一致）。详见 [[F_39_swarmflow-e2e-hardening]]。

## HarnessState

`IDLE` / `RUNNING` / `PAUSED` / `TERMINATED`。NativeHarness 仅 supervisor 协程 mutate。
转换：IDLE →(send) RUNNING；RUNNING →(round 完成无后继) IDLE / (有 follow_up·pending·
remaining) RUNNING / (pause) PAUSED / (immediate abort) IDLE；PAUSED →(send)
RUNNING；任意 →(stop) TERMINATED。CLI runtime 用同一枚举的子集（无 PAUSED）。

## 事件契约（StreamController 消费）

NativeHarness/CLI runtime 在 phase/round 转换时 fire 两个 harness-private 事件，
StreamController 经 `subscribe(on_state=_map_state, on_round=_map_round)` 一次性订阅，
映射到 MemberStatus / ExecutionStatus：

- `harness.state` → kwargs `old` / `new`（`HarnessState`）/ `session_id`；
  `_map_state`：RUNNING→BUSY、IDLE→READY + idle-settled（team_cleaned close /
  wake mailbox / completion poll）。
- `harness.round` → kwargs `kind`（started/finished/aborted/paused/failed）/
  `round_id` / `result`；`_map_round` 走 EXECUTION_TRANSITIONS 合法多步边。

## 实现

- **NativeHarness**：`class NativeHarness(DeepAgent)`，继承复用 DeepAgent 的
  task_loop 内核（controller / coordinator / handler / executor / LoopQueues /
  task_plan / session_spawn），单协程 supervisor 接管 outer round 驱动。能力与
  DeepAgent 完全一致。构造即配置：`__init__` 末尾同步跑 `_prepare_config()`（forward
  construction：resolve_parts + apply_deep_agent_parts），异步初始化在
  `_prepare()`（ensure_initialized）+ `start(session)`，二者均私有/内部，外部不再可见。
- **TeamHarness**：组合单个 `NativeHarness`（per-cycle）。build 时只构造 native——native
  在构造函数内已同步配好（configurator 可立即读 workspace / sys_operation）；
  `start(team_session)` 建 child agent_session（复用 team session_id，
  share_stream_writer=False）+ native.start + 首迭代 plan-mode seeding；
  `stop()`→native TERMINATED（保留实例供 deep_config 读），下个 cycle start 检测
  TERMINATED 重建。`abort`/`pause` 仅在 cycle 活跃（`_active_agent_session` 非空）
  时转发 native，否则 no-op——避免 teardown 路径在未 start 的 native 上触发
  `_require_alive`。`add_rail(rail)` 把额外 rail 委派给 `native.add_rail`（对称于既有
  `add_tool`/`remove_tool` passthrough；供 swarmflow 挂 `StructuredOutputFinishRail`，须在该
  round/turn 的 invoke/start 前调用，rail 入 native pending 列表、init 时注册）。
- **StreamController**：瘦身为输出转发 + 状态映射层。`start()` 挂 `_forward_outputs`
  （消费 `runtime.outputs()`→`_tag_chunk`→stream_queue+observers）+ 经
  `subscribe(on_state=, on_round=)` 注册 phase/round 回调；不再驱动 round / 排 pending /
  自重启。transient
  retry（181001 可重试）在 `_forward_outputs` 的 `_handle_retry`：swallow 失败轮
  + `send(_RETRY_QUERY)` 重驱；exhausted/non-retryable 转发 task_failed 给 consumer
  （不再 raise）。

## 中断语义

- **graceful**（`abort(immediate=False)`）：复用 `coordinator.request_abort`，当前
  round 收尾后停，不续轮。无回滚。
- **immediate**（`abort(immediate=True)`）：`task_scheduler.cancel_task(task_id)`
  硬停 executor + 回滚 DeepAgentState+context 到 round 边界 snapshot。工具已产生
  的外部副作用不撤销。
- **pause**：cancel + 回滚到 pre-round baseline + 缓存 query，下次 send 拼接重启。
- **CLI runtime**：abort 信号单轮 `_drive` 自然返回并 fire `aborted` round 事件；
  immediate rollback / pause 无对应快照，降级 no-op。

## resume（中断恢复）

`send(InteractiveInput)` 经 `submit_round` 透传（executor `_extract_interactive_input`
原生 resume）；resume round 单轮语义，完成后 settle 到 IDLE 不续 task_plan。
StreamController 仅校验 `is_pending_interrupt_resume_valid` 后转发，不再 client 侧排队。
