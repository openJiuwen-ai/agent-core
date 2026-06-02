# S_18 Harness 交互契约（HarnessProtocol）

最近一次修订日期：2026-06-02

本 spec 定义 agent_teams harness 层的对外交互契约。实现细节与决策见
[[F_27_native-harness-task-loop]]。

## HarnessProtocol

`openjiuwen/agent_teams/harness/protocol.py` 的 `@runtime_checkable` Protocol，
定义 harness 对外的并发安全交互表面，**独立于 harness 如何驱动底层 agent**。两个
harness 实现（当前 NativeHarness，阶段 2 的 StreamController-backed harness）对外
表面一致，调用方按 Protocol 编程而非绑定具体类。

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

**并发安全契约**：所有方法可从任意协程并发调用，无需外部加锁。实现侧以单
supervisor 协程 + control channel 串行化所有状态转换（唯一 state writer）。

## HarnessState

`IDLE` / `RUNNING` / `PAUSED` / `TERMINATED`。仅 supervisor 协程 mutate。转换：
IDLE →(send) RUNNING；RUNNING →(round 完成无后继) IDLE / (有 follow_up·pending·
remaining) RUNNING / (pause) PAUSED / (immediate abort) IDLE；PAUSED →(send)
RUNNING；任意 →(stop) TERMINATED。

## 实现

- **NativeHarness**：`class NativeHarness(DeepAgent)`，继承复用 DeepAgent 的
  task_loop 内核（controller / coordinator / handler / executor / LoopQueues /
  task_plan / session_spawn），单协程 supervisor 接管 outer round 驱动。能力与
  DeepAgent 完全一致。
- **阶段 2**：StreamController-backed harness 实现同一 Protocol，统一 team 与
  native 对外表面（待落地）。

## 中断语义

- **graceful**（`abort(immediate=False)`）：复用 `coordinator.request_abort`，当前
  round 收尾后停，不续轮。无回滚。
- **immediate**（`abort(immediate=True)`）：`task_scheduler.cancel_task(task_id)`
  硬停 executor + 回滚 DeepAgentState+context 到 round 边界 snapshot。工具已产生
  的外部副作用不撤销。
- **pause**：cancel + 回滚到 pre-round baseline + 缓存 query，下次 send 拼接重启。
