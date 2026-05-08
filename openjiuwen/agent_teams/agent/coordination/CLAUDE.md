# Coordination — 唤醒循环

`TeamAgent` 的事件驱动唤醒层。把传输层来的 `EventMessage` 与内部 poll 计时器产生的 `InnerEventMessage` 收成统一的 `CoordinationEvent`，按粗筛规则放行后由 `AsyncCallbackFramework` 分发到具体的场景 handler。**自身不做业务决策**——所有真正的行为都来自 handler 触发的 `DispatcherHost` 回调（`deliver_input` / `cancel_agent` / `shutdown_self` / `pause_polls` 等），最终落到 DeepAgent + team tools。

## 文件地图

| 文件 | 类 | 职责 |
|---|---|---|
| `event_bus.py` | `EventBus` / `InnerEventType` / `InnerEventMessage` | 事件入队 + 周期 poll timer + lifecycle（start / stop） |
| `dispatcher.py` | `EventDispatcher` / `DispatcherHost` | 触发规则（agent_ready / inner-vs-transport / 角色级粗筛）+ 持有私有 `AsyncCallbackFramework` 实例 + 把 5 个场景 handler 的 `get_callbacks()` 注册到 framework；handler 实例作为字段直接暴露（`dispatcher.lifecycle / .member / .message / .task_board / .stale_task`） |
| `handlers/` | `BaseCoordinationHandler` + 5 个场景 handler | 见下文"handler 拆分" |
| `kernel.py` | `CoordinationKernel` | 整体协调 facade：把 event_bus + dispatcher 挂在 host 上，pause / resume / drain |

## handler 拆分

`handlers/` 下一个 handler 一个业务域，每个类自己声明 `EVENT_METHOD_MAP: ClassVar[dict[str, str]]` 与对应 `async` 方法，从 `BaseCoordinationHandler.get_callbacks()` 输出 `event_key → bound method` 注册给 framework。沿用 `core/single_agent/rail/base.py:AgentRail` 的 rails 约定。

| handler | 监听 event | 状态 | 关键方法 |
|---|---|---|---|
| `AgentLifecycleHandler` | `USER_INPUT` / `STANDBY` / `CLEANED` / `TOOL_APPROVAL_RESULT` | 无 | `on_user_input` / `on_standby` / `on_cleaned` / `on_tool_approval_result` |
| `MemberHandler` | 6 个 `MEMBER_*` | 共享 `stale_claim_throttle` | `on_member_event` 分流到 `_handle_leader_member_event` / `_handle_teammate_member_event`；`MEMBER_STATUS_CHANGED → READY/ERROR` 时 `_nudge_idle_member_with_stale_claims` |
| `MessageHandler` | `MESSAGE` / `BROADCAST` / `POLL_MAILBOX` + `MEMBER_SHUTDOWN`（fan-out） | 无 | `on_message_or_broadcast`（leader 额外 ack user-bound + 通知 human-agent inbound）/ `on_poll_mailbox` / `on_member_shutdown_drain`（仅 teammate 给自己 drain） |
| `TaskBoardHandler` | `TASK_CLAIMED` / 5 个 `TASK_*` | 无 | `on_task_claimed`（targeted assignment）/ `on_task_board_event` → `_nudge_idle_agent` |
| `StaleTaskHandler` | `POLL_TASK` | 共享 `stale_claim_throttle` + 独占 `_last_pending_nudge` | `on_poll_task` → `_check_stale_claimed_tasks` + `_check_stale_pending_tasks` |

`MemberHandler` 与 `StaleTaskHandler` 在 `EventDispatcher.__init__` 拿到同一个 `dict[str, float]` 引用（`stale_claim_throttle`），保证"成员状态变化 nudge"和"poll 周期 nudge"两条路径不会在同一 stale 窗口内对同一 task 重复 nudge。`_last_pending_nudge` 仅 leader 自己用，留在 `StaleTaskHandler` 私有字段。

## 三条铁律

**铁律 1：coordination 不做决策。** loop 只管 wake-up，所有业务行为由内部 DeepAgent + team tools 驱动。新功能想塞进 dispatcher.py / handler 之前先问：是不是应该用一个新工具实现，让 LLM 自己决定调？

**铁律 2：每个 handler 一个业务域，跨域协作走 framework fan-out。** 新事件类型 = 在对应场景 handler 的 `EVENT_METHOD_MAP` 加一行 + 写方法，不需要改 `dispatcher.dispatch()`。`dispatch()` 只承担"是否该 trigger"的粗筛，**不做** event_type → handler 的具体路由——这是 framework 的职责。如果一个事件需要跨域响应（例如 `MEMBER_SHUTDOWN` 既要更新成员状态也要 drain 邮箱），让两个 handler 各自注册同一 event_key，framework 按注册顺序串行 fan-out —— **不要在一个 handler 内部调另一个 handler 的方法**。

**铁律 3：异常语义。** `AsyncCallbackFramework.trigger()` 吞普通 `Exception`（log + continue），仅 `AbortError` 上抛。和原 fail-fast 语义不同——handler 内部的失败不会让 dispatcher 中断、也不会阻断同一 event 上的其它 fan-out callback。handler 必须自己用 `team_logger.error("...", exc_info=True)` 记录关键失败，不依赖 framework swallow 当作隐式错误处理。

## 跨域协作要点

- **fan-out 顺序由注册顺序决定**：`EventDispatcher.__init__` 中 `(lifecycle, member, message, task_board, stale_task)` 元组顺序就是 framework 注册顺序。同 priority（默认 0）下 Python `list.sort` 稳定，故 `MEMBER_SHUTDOWN` 上 `MemberHandler.on_member_event` 先于 `MessageHandler.on_member_shutdown_drain`。改这个元组顺序前先评估 fan-out 影响。
- **`kernel.pause` 会等 in-flight round drain**：相关 `contextlib.suppress(asyncio.CancelledError, Exception)` 在 `stream_controller.drain_agent_task` —— 改清理路径时检查 `import contextlib` 是否还在（之前漏过一次）。
- **`DispatcherHost` 是公共契约**：`dispatcher.py` 顶部的 `Protocol` 定义了 dispatcher / handler 反向调 host 的所有方法；扩展前先确认改动不会让现有 handler 行为漂移。

## 跟其它子目录的边界

- `interaction/` 把三视角入口（GodView / Operator / HumanAgent）解析成 `EventMessage` 后才进 dispatcher，不要让 dispatcher / handler 自己解析 mention 字符串——那段已搬到 `interaction/router.py`。
- 真正干活的 LLM 在 `harness/deep_agent.py`，本目录只装配 + 调度。
- 跨 team 的对象池 / 派发 / 并发门禁在 `runtime/`，本目录不感知。
