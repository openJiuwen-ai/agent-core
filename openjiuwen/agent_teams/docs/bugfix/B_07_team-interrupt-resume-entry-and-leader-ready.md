# 团队中断关闭流后无恢复入口：裸文本恢复静默失效，recovery 流伪造 "None" 轮次

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-10-20 |
| 严重程度 | 中：中断（ask-user / 审批类）后团队无法程序化恢复；裸文本投递是**静默失败**（无错误、无输出，中断永远挂起）；`inputs=None` 伪造一轮 `"None"` 垃圾输入 |
| 范围 | `core/runner/team_runner.py`（新增 `resume_agent_team_interrupt_streaming` 实例方法 + 类门面）、`agent_teams/schema/stream.py`（新增 `is_leader_ready_marker`）、`agent_teams/agent/stream_controller.py`（新增 `emit_leader_ready`）、`agent_teams/agent/coordination/kernel.py`（`start()` 尾部 leader-only 发射标记帧）、`agent_teams/agent/team_agent.py`（`_prepare_initial_input` None 防护）、`core/common/exception/codes.py`（新增 `AGENT_TEAM_LEADER_START_TIMEOUT`）、`tests/unit_tests/agent_teams/test_team_agent_coordination.py`、`tests/unit_tests/agent_teams/test_runner_team_runtime.py` |
| 测试基线 | `pytest tests/unit_tests/agent_teams/test_runner_team_runtime.py tests/unit_tests/agent_teams/runtime/ tests/unit_tests/agent_teams/test_team_agent_coordination.py tests/unit_tests/agent_teams/test_coordination_lifecycle.py tests/unit_tests/agent_teams/agent/test_member_activity.py tests/unit_tests/agent_teams/test_stream_controller.py` → 376 passed, 19 skipped（skip 均为既有条件跳过；含本修复新增 9 例） |
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

1. **`Runner.resume_agent_team_interrupt_streaming`**：驱动 `run_agent_team_streaming(inputs=None)`
   的 recovery 流并转发其 chunk，读到就绪标记后经 `interact_agent_team` 提交
   `InteractiveInput`，继续转发直到 run cycle 结束。
2. **就绪信号是流上的 `team.leader_ready` 标记帧**：`kernel.start()` 尾部
   （`resume_paused_round` 之后、kernel 全活）leader-only 经
   `StreamController.emit_leader_ready` 入队；`is_leader_ready_marker`
   （`schema/stream.py`）是专用判定，`is_team_event_marker` 将其归入框架标记。
   复用既有的标记 chunk 模式（`team.idle` / `team.completed` / `team.interact.failed`），
   任何流消费者都可用，无需预注册。**初版实现是 manager 侧的一次性 waiter**
   （`watch_next_leader_ready` 注册 + `activate` 尾部挂发布回调 + 池身份校验），
   评审后因跨组件状态多（manager 字典 + kernel 回调装卸 + runner 双 task 收敛）
   而简化为标记帧——机制从"manager 里的一次性注册"变成"流上多一种 chunk"。
3. **契约收紧并边界校验**：`user_input: InteractiveInput` 必填、`session` 必填，违规抛
   `AGENT_TEAM_CONFIG_INVALID`——堵住裸 `str` 静默不恢复的失败模式（中断状态与 spec bucket
   都在原 session 的 checkpoint 里，新 session 无法恢复）。
4. **错误统一 StatusCode**：流先于标记失败 / 结束 / interact 被拒（reason token 透传）→
   `AGENT_TEAM_STATE_INVALID`；标记静默超时 → 新增 `AGENT_TEAM_LEADER_START_TIMEOUT`（132009，
   模板带 `{timeout}`，自动映射 `ExecutionError` 可重试）。超时按**单次 `__anext__`** 计
   （`asyncio.timeout` 包住一次取帧），慢消费者不误伤，只有流真静默才触发。
5. **`str(None)` 防护**：`inputs is None` → 空 query，空即不伪造首轮（回归既有契约）。

## 拒绝的方案

- **轮询 pool entry 状态或固定 sleep 后 interact**：时序不精确，慢初始化下误判；标记帧零竞态。
- **允许 `str` 输入自动包一层 `InteractiveInput`**：中断恢复载荷按 interaction id 对位
  （`user_inputs`），裸文本无法可靠映射，自动包装只是把失败推迟到 harness 内部。
- **把就绪等待并入 `run_agent_team_streaming`**：改变既有入口语义（普通 run 无需等 ready 即返回
  chunk），影响所有调用方。
- **manager 侧一次性 waiter（初版方案）**：`watch_next_leader_ready` 注册 +
  `activate` 尾部绑定回调 + kernel `set_started_callback` 挂点 + runner 侧 pump task /
  输出队列 / 双 task 收敛——四组件联动约 250 行，只为传一个一次性信号；标记帧方案
  删掉全部跨组件状态，任何消费者直接读流即可。
- **全局超时预算（整个标记等待共享一个 30s 窗口）**：慢消费者处理 chunk 期间窗口仍在走，
  会误伤；按单次 `__anext__` 计只在流真静默时触发。

## 验证

`tests/unit_tests/agent_teams/test_team_agent_coordination.py`（新增）：

| 用例 | 覆盖 |
|---|---|
| `test_leader_start_emits_leader_ready_marker` | leader 的 `kernel.start()` 尾部入队 `team.leader_ready`，`is_leader_ready_marker` / `is_team_event_marker` 判定 |
| `test_teammate_start_does_not_emit_leader_ready_marker` | 非 leader kernel 不发射 |

`tests/unit_tests/agent_teams/test_runner_team_runtime.py`（新增）：

| 用例 | 覆盖 |
|---|---|
| `..._resumes_and_forwards`（level0） | chunk 顺序（runtime_ready → leader_ready → 前置 chunk → 恢复后 chunk）、`resume_interrupt` 收到原 `InteractiveInput`、recovery 流传 `inputs=None` |
| `test_runner_resume_rejects_non_interactive_input` | 裸 `str` 边界拒绝（`AGENT_TEAM_CONFIG_INVALID`） |
| `test_runner_resume_rejects_missing_session` | session 缺失拒绝 |
| `..._propagates_stream_failure_before_ready` | 流先失败：原始异常传播 |
| `..._times_out_when_leader_never_ready` | 标记永不出现：`AGENT_TEAM_LEADER_START_TIMEOUT` |
| `..._raises_when_stream_ends_before_marker` | 流静默结束无标记：`AGENT_TEAM_STATE_INVALID` |
| `..._raises_when_interact_rejected` | interact 被拒（`unsupported_interactive_input` → `AGENT_TEAM_STATE_INVALID`，reason 透传） |

## 已知遗留

- REJECT 派发（如 `REJECT_RUNNING`）时 recovery 流静默结束，真实 reason 仍在
  `run_agent_team_streaming` 的日志里；异常信息是泛化描述。透传需要该入口暴露 action 信息，改动面大，暂不做。
- 30s 就绪预算硬编码，未做 per-call 可配。
- `TeamAgent.resume_interrupt` 的 stale-resume 校验失败是**静默 drop**（只记 info 日志），
  interact 仍返回 success——调用方拿到空流时需自行排查；要闭合需让
  `is_valid_interrupt_resume` 的结果回到 `DeliverResult`。
