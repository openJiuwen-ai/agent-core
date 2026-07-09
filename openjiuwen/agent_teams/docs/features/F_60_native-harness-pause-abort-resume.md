# F_60 NativeHarness pause / abort / resume：iteration 级语义与原地续跑

日期：2026-07-09

## 背景

排查团队 leader 的 react round 被意外 cancel（日志 `task_scheduler ... cancelled
(likely by pause_task/cancel_task)`）时，定位到 pause 语义从上到下都是错的：

- **协调层**：`kernel.pause` 的 round teardown 走 `drain_agent_task` →
  `cancel_agent` → `harness.abort(immediate=True)`。真正的 `harness.pause()` 是**零调用方
  的死代码**。
- **harness 层**：`pause` 与 `immediate abort` 几乎同构——都 hard-cancel + 回滚到
  `pre_round_snapshot`（**整个 outer round** = 一次完整 `react_agent.invoke`）。`pause` 的
  docstring 明说 "discards the whole round"，靠下次 send 拼接 query 重启。
- **粒度**：回滚目标 `last_safe_snapshot` 由 `SnapshotRail` 在 `AFTER_TASK_ITERATION`
  抓，而它经 `_ACTIVE_ROUND` ContextVar 定位 round —— inner loop 跑在 TaskScheduler 的
  exec task 里，ContextVar 读到 `None`。**该快照从未被填充，是 dead code**，immediate
  abort 实际总是退到 round 起点。
- **无 resume**：`HarnessProtocol` 只有 start/stop/outputs/send/abort/pause。

实际后果：leader 刚 `build_team`（DB 副作用已提交）、正要 spawn/派活时被外部 pause 打断，
整轮回滚、27 万 input token 白烧，且「世界已改、对话记忆没了」。

## 决策

**pause 与 abort 是两个正交动词**，都精确到 **inner ReAct iteration**（一次 LLM 调用 +
该轮 toolcall；日志 `ReAct iteration N`），而非 outer round：

| 触发时机 | PAUSE | ABORT immediate=true | ABORT immediate=false |
|---|---|---|---|
| model 阶段（未提交 toolcall） | 打断 LLM，回退上一 iteration 边界 | 中断，回退上一边界 | LLM 未开始→立即退；已开始→等本 iteration 完成 |
| tool 阶段（副作用在飞） | 等本 iteration 完成再停 | 中断回退 | 等本 iteration 完成 |
| 停后状态 | **PAUSED，可 resume 原地续** | **IDLE，靠 send 重启** | **IDLE，靠 send 重启** |

### 让问题坍缩的两个结构性事实

1. **强 tool_call/result 配对**：`react_agent` 写下带 tool_calls 的 AssistantMessage 后，
   必须执行完所有 tool 并写 ToolMessages。「已提交 tool_calls」与「所有 tool 完成」之间
   **没有合法停止点**。所以一次 iteration 的 pause 只有两个合法结局：**丢弃整个 iteration**
   （回退上一边界）或 **完成整个 iteration**（前进下一边界）—— 这正是上表。

2. **正确性权威在 inner loop 的 rail 检查点，不在 supervisor 读 phase**：
   - `before_model_call` 里 `request_force_finish` → `@rail` 跳过 LLM body；
   - `after_model_call` 里设 → `react_agent` 在**写 AssistantMessage 之前** break。

   两者都把 context 停在干净的 iteration 边界，**绝不触碰运行中的 tool**。

⇒ inner loop 总能在 model-call 边界协作式干净停止。supervisor 的 hard-cancel 因此退化为
**纯及时性优化**（打断慢的 in-flight LLM），且 **gated 到只在 `model_call_in_flight` 时执行**
—— 它只能落在 parked 的 LLM `await` 上。

### 落地

- **`PhaseSnapshotRail`**（取代 `SnapshotRail`）：持 **harness back-ref**（`harness._st.active`）
  而非死的 ContextVar。维护 `iter_phase` / `model_call_in_flight` / `tool_started`；在
  `AFTER_REACT_ITERATION` 抓 `last_iter_snapshot`（inner 边界，pause/abort 的主回滚目标），在
  `AFTER_TASK_ITERATION` 抓 `last_safe_snapshot`（顺带复活 outer 快照）；在两个 model-call
  钩子执行 cooperative force_finish。`before_tool_call` **故意不 force_finish**。
  `after_model_call` **只对 pause** 生效——graceful abort 的契约是「LLM 已开始就跑完整个
  iteration」。
- **`HarnessState.PAUSING`**：tool 阶段 pause 的过渡态（等边界），`_on_round_done` 结算为
  PAUSED。ack 延迟到那时 resolve（`ActiveRound.pause_ack`），且由 `_on_round_done` 的
  `finally` 兜底，任何路径（paused/graceful/crashed）都不会让 `pause()` 调用方永久阻塞。
- **`resume()`（warm，R-clean）**：起一个 **continuation round**，在既有 context 上继续 loop、
  **不追加新的 user turn**。`_resume_continuation` 沿 `submit_round → event.metadata →
  task_metadata → executor.effective → ctx.extra` 透传（与 `run_kind`/`run_context` 同一模式），
  `_inner_invoke` 据此跳过 UserMessage append 并放宽空 query 守卫。paused round 的
  `original_query` 存进 `paused_query`，交给 continuation 作 `original_query`，使 task-plan 续轮仍可复用。
- **`_on_send` 在 PAUSED**：改为「resume + 注入」（continuation round + `_push_steer`），取代
  旧的「merge query 重启」——后者会丢弃 paused round 已完成的每一个 iteration。
- **协调层**：`kernel.pause` 改走 `pause_agent_round()` → `harness.pause()`；`stop`/`destroy`
  仍走 `drain_agent_task()`（硬取消）。`kernel.start` 尾部（session/stream/event bus 都已就绪后）
  调 `resume_paused_round()` 续跑，否则成员会空等新消息、静默丢掉被暂停的工作。
- **cooperative-stop 结果不入流**：`_is_control_result` 让 `{"result_type": "cooperative_stop"}`
  与 `{"error": ...}` 一样不写 output stream，也不喂给 AFTER_INVOKE rails。

## 命门验证（race 闭合）

`_on_pause` 先同步 arm `pause_requested`，再看 `model_call_in_flight` 决定是否 hard-cancel。
残余 race：`cancel_task` 内部有 await，LLM 可能恰好在这期间完成。此时 `after_model_call`
（`pause_requested` 已置）force_finish，**在写 AssistantMessage 之前 break，没有任何 tool 会启动**；
迟到的 cancel 落在正在 unwind 的协程上，无害。**关掉 race 的是 cooperative force_finish，
hard-cancel 只是叠加的及时性。**

`@rail` 在 `CancelledError` 时跳过 after 钩子，故 hard-cancel 不会发出虚假的 AFTER_MODEL_CALL。

## 拒绝的方案

- **supervisor 单方读 `iter_phase` 后硬取消**：读与 inner loop 的阶段转换存在 race，且把
  正确性押在时序上。改为「决策点落在 rail 检查处」。
- **无条件保护 tool（pause 一律等 iteration 完成）**：model 阶段没有不可逆副作用，等下去只是
  让 pause 不及时。按阶段分化。
- **graceful abort 也在 `after_model_call` 停**：那会跳过已提交的 tool，违反其「LLM 已开始
  就跑完整个 iteration」的契约。只 pause 在此停。
- **R-minimal resume（合成一条 "continue" user turn）**：零 core 改动，但每次 pause/resume 都
  往对话历史塞垃圾，且不是真正的「原地」。选 R-clean（flag-guarded、有 tool-interruption 先例）。
- **`_ACTIVE_ROUND` ContextVar 桥接**：inner loop 在 exec task 中读到 None，本就是死路。改用
  harness back-ref。
- **本期新增 `kernel.abort(*, immediate)` 控制面动词**：目前无调用方，属投机抽象。留到第二期
  与 `Runner` facade 一起接线。

## 已知遗留

- **孤儿 chunk（窄窗口）**：pause 恰好落在「LLM 已完成、AssistantMessage 未写」时，
  `after_model_call` force_finish 丢弃该结果，但其流式 chunk 已发给消费者。消费者看到过一段
  输出而 context 没有它；resume 后会重新生成。cooperative pause 不发 `round_aborted`（已完成的
  iteration 的 chunk 是有效的），故这段孤儿不被撤回。
- **工具外部副作用不可回滚**：沿袭 F_27 的固有代价。这恰恰是「tool 阶段必须等 iteration 完成」
  的理由。
- **PAUSING 期间再次 `pause()`**：幂等立即返回，不等待边界。
- **第二期（不在本次范围）**：checkpoint 持久化——`paused_query` / round 进度落盘、把死字段
  `lifecycle:"paused"` 接进 dispatch、实现 pause→stop→start = resume 的冷恢复续跑；以及
  `Runner`/runtime 的 `abort_agent_team` / `resume_agent_team` facade。

  > 已由 [[F_61_cold-resume-across-stop-start]] 落地：只持久化一个 `pending_resume` marker
  > （context 本就随 teardown 的 `commit()` 进 checkpoint），**不**接 dispatch（`COLD_RECOVER`
  > 已是正确派发，续跑归 kernel），facade 经评估后拒绝（无调用方）。

## 验证基线

```
tests/unit_tests/agent_teams/harness/                70 passed   (含 5 个新 pause/resume 用例)
tests/unit_tests/agent_teams/                      1828 passed   (含 2 个新 kernel 根因守卫)
tests/unit_tests/harness/                          2050 passed   (task_loop + deep_agent 向后兼容)
tests/unit_tests/core/single_agent/                  43 passed
tests/unit_tests/agent/react_agent/…continuation      3 passed   (core 的 continuation 分支)
```

关键用例：`test_pause_in_model_phase_interrupts_and_rewinds_to_boundary`（hard-cancel +
回退到 iteration 边界）、`test_pause_in_tool_phase_never_interrupts_the_tool`
（`cancelled_count == 0`，tool 跑完）、`test_resume_continues_in_place_without_a_new_user_turn`、
`test_send_while_paused_resumes_and_injects_the_new_content`、
`test_pause_stops_at_boundary_and_never_hard_cancels`（kernel.pause 不再 abort）。

测试侧另修复 fixture 的接线缺口：`set_react_agent` 不重新注册 rail，故注入 fake 后必须
`rebind_bridge_rails`，否则 BRIDGE 钩子（model/tool/react-iteration）全部丢失、phase 追踪是死的。
`FakeReactAgent.invoke` 现在跑一个迷你 ReAct loop，按真实顺序 fire 钩子并在真实的三个点
消费 force_finish。
