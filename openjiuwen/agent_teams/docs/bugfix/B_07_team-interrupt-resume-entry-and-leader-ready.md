# 团队中断关闭流后无恢复入口：裸文本恢复静默失效，recovery 流伪造 "None" 轮次

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-10-20 |
| 严重程度 | 中：中断（ask-user / 审批类）后团队无法程序化恢复；裸文本投递是**静默失败**（无错误、无输出，中断永远挂起）；`inputs=None` 伪造一轮 `"None"` 垃圾输入 |
| 范围 | `core/runner/team_runner.py`（新增 `resume_agent_team_interrupt_streaming` 实例方法 + 类门面）、`agent_teams/runtime/manager.py`（新增 `LeaderReadyWaiter` / `watch_next_leader_ready` / `_bind_leader_ready_callback`）、`agent_teams/agent/coordination/kernel.py`（`_started_callback` + `set_started_callback`，`start()` 尾部触发）、`agent_teams/agent/team_agent.py`（`set_leader_started_callback` 透传 + `_prepare_initial_input` None 防护）、`core/common/exception/codes.py`（新增 `AGENT_TEAM_LEADER_START_TIMEOUT`）、`tests/unit_tests/agent_teams/runtime/test_leader_ready.py`（新增）、`tests/unit_tests/agent_teams/test_runner_team_runtime.py` |
| 测试基线 | `pytest tests/unit_tests/agent_teams/test_runner_team_runtime.py tests/unit_tests/agent_teams/runtime/` → 199 passed（含本修复新增 11 例）；coordination / 错误码 / core runner 定向 → 123 passed, 20 skipped（skip 均为既有条件跳过） |
| 关联 spec | `S_01_public-api-and-spec-flow.md`（不变量 17 + 入口签名与错误语义）、`S_04_session-and-recovery.md`（I-9 中断恢复流）；`agent_teams/AGENTS.md` / `runtime/AGENTS.md` / `coordination/AGENTS.md` 表面同步 |
| Refs | #4910 |

## 现象

中断（ask-user / 审批类）把 `run_agent_team_streaming` 的流关闭之后（进程重启、pool 清空时更甚），
SDK 侧没有把"重新拉起团队 + 恢复中断"一次完成的入口，调用方只能手工拼装且处处踩坑：

| 尝试 | 结果 |
|---|---|
| 直接 `interact_agent_team` | pool 空 → `DeliverResult.failure("not_active")`；pool 有 entry 但 kernel 未 `start` → 消息投进 bus 无人消费，静默丢失 |
| `run_agent_team_streaming(inputs=None)` 拉起团队 | 团队起来了，但没有通道把中断答案送进去；且 `inputs=None` 在 `_prepare_initial_input` 被转成字符串 `"None"` 当 query，**伪造一轮垃圾输入** |
| 把中断答案（裸 `str`）交给 `interact_agent_team` | 走 GodView → `deliver_input` → `harness.send(immediate=True)` 被当普通 steer；pending interrupt 保持挂起，调用方**拿不到任何错误或输出**——静默失败 |

初版实现评审另发现三处违规：裸 `RuntimeError` 抛出（违反 error-codes 规则）、kernel 就绪回调异常会打断
`kernel.start()` 整个启动、`waiter.wait(timeout=)` 参数形式触发 ASYNC109。

## 根因

1. **就绪信号缺失。** `kernel.start()` 完成前（事件总线订阅、harness start、`resume_paused_round`）
   投递必丢或 not_active，而系统没有任何"leader coordination runtime 已就绪"的可观察信号——
   调用方只能 sleep / 轮询去猜时序，慢初始化（workspace + 模型加载）下必然误判。
2. **恢复载荷的类型契约不可见。** `manager.interact` 只有 `InteractiveInput` 走
   `agent.resume_interrupt` 分支；裸 `str` 的语义完全不同（god-view steer）。但入口签名是
   `Any`、无边界校验、无文档——两类载荷天壤之别却对调用方不可见，失败还无法从返回值区分。
3. **`_prepare_initial_input` 对 `inputs=None` 执行 `str(inputs)`。** recovery / resume 场景
   "无输入"是合法形态（契约：spawn/recover/resume 不伪造轮次），但 `None` 被转成 `"None"`
   文本绕过了空 query 判定，污染成员上下文与记忆管线。

## 决策

**原则：恢复 = recovery 流 + 就绪门 + 类型化投递，三步一个入口，时序靠信号不靠猜。**

1. **`Runner.resume_agent_team_interrupt_streaming`**：后台 pump `run_agent_team_streaming(inputs=None)`
   的 recovery 流，leader 就绪后经 `interact_agent_team` 提交 `InteractiveInput`，转发缓冲与后续
   chunk 直到 run cycle 结束。
2. **就绪信号走 kernel 尾回调而非轮询/延时**：`set_started_callback` 挂点在 `kernel.start()` 尾部
   （`resume_paused_round` 之后、kernel 全活）触发；waiter 在启动流**之前同步注册**
   （`watch_next_leader_ready`，同键先 cancel 旧 waiter），`manager.activate` 尾部绑定发布回调
   （池身份校验后 `set_ready` 并自清）——`kernel.start` 只从 `agent.stream` / `invoke` 进入，
   绑定必然先于触发，不存在错过。
3. **契约收紧并边界校验**：`user_input: InteractiveInput` 必填、`session` 必填，违规抛
   `AGENT_TEAM_CONFIG_INVALID`——堵住裸 `str` 静默不恢复的失败模式（中断状态与 spec bucket
   都在原 session 的 checkpoint 里，新 session 无法恢复）。
4. **错误统一 StatusCode**：流先于就绪结束 / interact 被拒（reason token 透传）→
   `AGENT_TEAM_STATE_INVALID`；就绪超时 → 新增 `AGENT_TEAM_LEADER_START_TIMEOUT`（132009，
   模板带 `{timeout}`，自动映射 `ExecutionError` 可重试）。
5. **kernel 回调异常只记日志不打断启动**（就绪信号是辅助机制）；waiter 注册/替换/发布/身份
   不匹配四处补 `team_logger` 日志；`finally` 收敛路径捕 `TimeoutError` 防止掩盖原始异常。
6. **`str(None)` 防护**：`inputs is None` → 空 query，空即不伪造首轮（回归既有契约）。

## 拒绝的方案

- **轮询 pool entry 状态或固定 sleep 后 interact**：时序不精确，慢初始化下误判；就绪回调零竞态。
- **允许 `str` 输入自动包一层 `InteractiveInput`**：中断恢复载荷按 interaction id 对位
  （`user_inputs`），裸文本无法可靠映射，自动包装只是把失败推迟到 harness 内部。
- **把就绪等待并入 `run_agent_team_streaming`**：改变既有入口语义（普通 run 无需等 ready 即返回
  chunk），影响所有调用方。
- **在 runner 层直接维护等待器、不经 manager**：绕过 `activate` 的统一绑定时机，两处状态各自对账。

## 验证

`tests/unit_tests/agent_teams/runtime/test_leader_ready.py`（新增）：

| 用例 | 覆盖 |
|---|---|
| `test_set_ready_resolves_wait` | 一次性事件正常唤醒 |
| `test_cancel_discards_waiter_and_blocks_wait` | cancel 幂等；cancel 后 `set_ready` 不复活 |
| `test_watch_replaces_previous_waiter` | 同键重复注册：旧 waiter 被 cancel，新 waiter 收到就绪 |
| `test_matching_activation_publishes_ready_and_clears_callback` | 身份匹配：发布就绪 + 回调自清 |
| `test_identity_mismatch_does_not_publish_ready` | 池内 agent 不一致：不发布 |

`tests/unit_tests/agent_teams/test_runner_team_runtime.py`（新增）：

| 用例 | 覆盖 |
|---|---|
| `..._resumes_and_forwards`（level0） | chunk 顺序（runtime_ready → 前置 chunk → 恢复后 chunk）、`resume_interrupt` 收到原 `InteractiveInput`、recovery 流传 `inputs=None` |
| `test_runner_resume_rejects_non_interactive_input` | 裸 `str` 边界拒绝（`AGENT_TEAM_CONFIG_INVALID`） |
| `test_runner_resume_rejects_missing_session` | session 缺失拒绝 |
| `..._propagates_stream_failure_before_ready` | 流先失败：原始异常传播 |
| `..._times_out_when_leader_never_ready` | 永不就绪：`AGENT_TEAM_LEADER_START_TIMEOUT` |
| `..._raises_when_interact_rejected` | interact 被拒（`unsupported_interactive_input` → `AGENT_TEAM_STATE_INVALID`，reason 透传） |

## 已知遗留

- REJECT 派发（如 `REJECT_RUNNING`）时 recovery 流静默结束，真实 reason 仍在
  `run_agent_team_streaming` 的日志里；异常信息是泛化描述。透传需要该入口暴露 action 信息，改动面大，暂不做。
- 30s 就绪预算硬编码，未做 per-call 可配。
- `TeamAgent.resume_interrupt` 的 stale-resume 校验失败是**静默 drop**（只记 info 日志），
  interact 仍返回 success——调用方拿到空流时需自行排查；要闭合需让
  `is_valid_interrupt_resume` 的结果回到 `DeliverResult`。
