# S_18 Harness 交互契约（HarnessProtocol / MemberRuntime）

最近一次修订日期：2026-07-21

本 spec 定义 agent_teams harness 层的对外交互契约。阶段 1（NativeHarness 接管 task
loop）的实现细节见 [[F_27_native-harness-task-loop]]；阶段 B（NativeHarness 收编
TeamHarness/StreamController）的决策见 [[F_28_native-harness-team-adoption]]；关停竞态守卫与
`add_rail` 委派见 [[F_39_swarmflow-e2e-hardening]]；pause / abort / resume 的 inner-iteration
级语义与原地续跑见 [[F_60_native-harness-pause-abort-resume]]；跨 stop/start 的冷恢复续跑见
[[F_61_cold-resume-across-stop-start]]。

## 两层契约

阶段 B 把交互契约分成两层，调用方按层编程而非绑定具体类：

- **`HarnessProtocol`**（`harness/protocol.py`，`@runtime_checkable`）：底层并发安全
  交互表面，**独立于 harness 如何驱动底层 agent**。`NativeHarness` 直接实现；
  `TeamHarness` 组合单个 `NativeHarness` 并转发同一表面。
- **`MemberRuntime`**（`agent/member_runtime.py`，`@runtime_checkable`）：team
  coordination/configurator/StreamController 驱动的 **brain seam**——superset
  `HarnessProtocol`（`start` 形参为 `team_session`），再加 team 专属的
  rail/memory/customizer hook。默认实现是 `TeamHarness`（DeepAgent-backed）；外部
  CLI 成员由 `ExternalCliRuntime` / `ReinvokeCliRuntime` /
  `CodexSdkRuntime` / `ClaudeSdkRuntime` 实现同一表面，把各自的 turn
  协议适配成多轮 surface（rail/memory hook 为 no-op，rollback/pause 降级）。

## HarnessProtocol 成员

| 成员 | 签名 | 语义 |
|---|---|---|
| `state` | `-> HarnessState`（property） | 当前生命周期阶段 |
| `session_id` | `-> str \| None`（property） | 拥有/注入的 session id，start 前为 None |
| `start` | `async (*, session: Session \| None = None)` | 初始化并启动 supervisor；可注入外部 session |
| `stop` | `async ()` | 取消在途工作、关闭输出、转 TERMINATED |
| `outputs` | `() -> AsyncIterator[OutputSchema]` | queue-backed 输出迭代器（单消费者，`_END` sentinel 终止） |
| `send` | `async (content, *, immediate=False) -> str` | 提交输入，immediate=True 注入当前 round；返回 seq id |
| `abort` | `async (*, immediate=False)` | 中止当前 round → IDLE。graceful（False）在 iteration 边界停；immediate（True）硬取消 + 回退最近边界 |
| `pause` | `async ()` | 在最近的 inner ReAct iteration 边界暂停 → PAUSED，round 保留可续 |
| `resume` | `async (*, query=None)` | 从保留的 context 原地续跑 paused round（不追加新的 user turn）。`query` 驱动**冷恢复**（harness 已重建、context 来自 checkpoint）；warm 恢复忽略它 |

`abort` 与 `pause` 是**两个正交动词**：abort 丢弃当前 round（下次 `send` 起全新 round），
pause 停在干净边界并保留 round（`resume` 原地续）。

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

`IDLE` / `RUNNING` / `PAUSING` / `PAUSED` / `TERMINATED`。NativeHarness 仅 supervisor 协程 mutate。

转换：

- IDLE →(send) RUNNING
- RUNNING →(round 完成无后继) IDLE / (有 follow_up·pending·remaining) RUNNING
- RUNNING →(pause，model 阶段：硬取消 + 回退上一 iteration 边界) PAUSED
- RUNNING →(pause，tool/boundary 阶段：cooperative) PAUSING →(到达 iteration 边界) PAUSED
- RUNNING →(abort immediate=True：硬取消 + 回退边界) IDLE / (abort immediate=False：边界停) IDLE
- PAUSED →(resume) RUNNING（continuation round）/ (send) RUNNING（resume + 注入）/ (abort) IDLE
- 任意 →(stop) TERMINATED

`PAUSING` 是 tool 阶段 pause 的过渡态：一个已开始的 iteration 必须跑完（副作用不可撤销），
inner loop 在下一个 model-call 边界协作式停止，`_on_round_done` 结算为 PAUSED 并 resolve 延迟的
pause ack。CLI runtime 用同一枚举的子集（无 PAUSING / PAUSED）。

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

停止点一律落在 **inner ReAct iteration 边界**（一次 LLM 调用 + 该轮 toolcall）。由于 tool_call 与
ToolMessage 强配对，一次 iteration 只有两个合法结局：**整个丢弃**（回退上一边界）或 **整个完成**
（前进下一边界）。正确性权威是 `PhaseSnapshotRail` 在 model-call 钩子上的 cooperative
`force_finish`；supervisor 的 hard-cancel 只是**及时性优化**，且 gated 到
`model_call_in_flight`（只能落在 parked 的 LLM `await`，绝不落在运行中的 tool）。

- **pause**：model 阶段 → 打断 LLM，回退 `last_iter_snapshot`（无则 pre-round baseline）→ PAUSED；
  tool/boundary 阶段 → 不硬取消，arm `pause_requested`，inner loop 跑完当前 iteration 后在下一个
  `before_model_call` 停 → PAUSING → PAUSED。round 保留，`resume` 原地续。
- **graceful abort**（`immediate=False`）：arm `graceful_abort`。LLM 未开始 → 下一个
  `before_model_call` 立即退；已开始 → 跑完整个 iteration（含 tool）后在随后的 model-call 边界退。
  无回滚 → IDLE。
- **immediate abort**（`immediate=True`）：`task_scheduler.cancel_task(task_id)` 硬停 executor +
  回滚 DeepAgentState+context 到 `last_iter_snapshot`（无则 pre-round baseline）→ IDLE。
- **工具外部副作用不撤销**（沿袭 F_27）。这正是 tool 阶段必须等 iteration 完成的理由。
- **CLI runtime**：abort 信号单轮 `_drive` 自然返回并 fire `aborted` round 事件；
  immediate rollback / pause / resume 无对应快照，降级 no-op。Codex SDK runtime 具备 turn 级
  steer / interrupt：正常消息在队列中作为后续 turn，`immediate=True` 走
  `AsyncTurnHandle.steer()`，shutdown 走 `AsyncTurnHandle.interrupt()` 并关闭 SDK client。

### Codex SDK backend 的会话边界

`cli_agent="codex"` 在 backend registry 中归类为 `sdk`，不走单次 `codex exec`。当前
每个成员构建一个 `AsyncCodex` client，调用
`thread_start()` 建立独立 thread，再通过 `thread.turn()` 和 `AsyncTurnHandle.stream()`
执行与消费每一轮。后续任务复用该成员的同一 thread；不同成员的
client、thread id 和模型上下文均隔离。SDK 内部仍可启动 `codex app-server`，但其
传输、JSON-RPC 和子进程生命周期不再由 Jiuwen 维护。

当前持久性边界是“同一成员 runtime 实例内跨 turn”；runtime 冷重建后尚未把
`thread_id` 回写到 team checkpoint，因此 spawn 路径仍会启动新 thread。runtime 已支持传入
已保存 id 并调用 `thread_resume(thread_id)`，只差 checkpoint 读写接线。`openai-codex`
是可选依赖，经 `codex.options.load_codex_sdk()` 在构建 Codex runtime 时延迟导入；
模块导入和其它 backend 不要求安装 SDK。线程启动不显式设置 `approval_mode`
或 `sandbox`，使用 SDK 自身的审批默认值并保持 sandbox 未设置。旧的单次路径以显式名称
`cli_agent="codex-exec"` 保留为兼容兜底。详见 [[F_58_codex-app-server-runtime]]、
[[F_66_codex-python-sdk-runtime]]。

快照由 `PhaseSnapshotRail` 经 **harness back-ref**（公共只读属性 `harness.active_round`）维护：
`AFTER_REACT_ITERATION` → `last_iter_snapshot`（主回滚目标），`AFTER_TASK_ITERATION` →
`last_safe_snapshot`。旧的 `_ACTIVE_ROUND` ContextVar 在 exec task 中恒为 None，已删除。

## resume（两种，互不相干）

**1. resume（`resume(*, query=None)`，pause 的逆操作）**：起一个 **continuation round**，在 paused
round 保留的 context 上继续 loop，**不追加新的 user turn**。`_resume_continuation` 沿
`submit_round → event.metadata → task_metadata → executor.effective → ctx.extra` 透传（与
`run_kind` / `run_context` 同一模式），`_inner_invoke` 据此跳过 UserMessage append 并放宽空 query
守卫。该 query 只作 continuation round 的 `original_query`，使 task-plan 续轮仍可复用。

两条路径，都由 `kernel.start` 尾部（session / stream / event bus 就绪后）的 `resume_paused_round()`
驱动，续跑成功后清 marker：

- **warm**（同进程）：harness 仍 PAUSED —— `kernel.pause` 从不 stop 它 —— 直接 `resume()`，
  用 `paused_query` 的内存值。
- **cold**（`pause → stop → start`）：harness 被重建、状态 IDLE，context 由 teardown 时的
  `child.commit()` 写入 checkpoint 后经共享 session id 恢复。`kernel.pause` 落盘的
  `pending_resume = {"query": ...}`（per-team bucket，leader-only）提供 query →
  `resume(query=...)`。**dispatch 不读这个 marker**：`COLD_RECOVER` 本就是正确的派发结果，续跑
  发生在 kernel 层。详见 [[F_61_cold-resume-across-stop-start]]。

PAUSED 收到 `send` 等价于「resume + 把新内容 steer 进去」——**不再** merge query 重启整轮。
`kernel.pause` 走 `pause_agent_round()`；只有 stop / destroy 才 `drain_agent_task()` 硬取消。

**2. interrupt resume（`send(InteractiveInput)`，HITL 中断恢复）**：经 `submit_round` 透传
（executor `_extract_interactive_input` 原生 resume）；resume round 单轮语义，完成后 settle 到
IDLE 不续 task_plan。StreamController 仅校验 `is_pending_interrupt_resume_valid` 后转发，不再
client 侧排队。此路径与 warm resume 正交：一个 InteractiveInput round 被 pause 时不缓存其 query
（不可 replay），PAUSED 收到 InteractiveInput 则直接起它自己的单轮 round。
