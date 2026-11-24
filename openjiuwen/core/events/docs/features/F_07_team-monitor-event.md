# team.monitor_event 团队监控事件契约设计方案

## 1. 元信息

| 项 | 值 |
| --- | --- |
| 类型、编号 | feature，`F_07 / team-monitor-event` |
| 建档日期 | 2026-10-31 |
| 范围 | Events 的 `team.monitor_event` 事件契约、监控通知桥接的行为边界与宿主接入面 |
| 方案成熟度 | 已确认；实现与端到端验证完成 |
| 计划条目 | 无对应 EV 条目；team 扩展整体接线与进度见 [F_100](../../../../agent_teams/docs/features/F_100_team-extension-wiring.md) |
| 设计依据 | [S_01 Events 公共契约](../specs/S_01_events-contract.md)（注册、匹配、分发与容错语义均沿用）；[S_14 Monitor 模型](../../../../agent_teams/docs/specs/S_14_monitor-and-observability.md)（`MonitorEvent` / `MonitorEventType` 的主责规约） |
| 关联 feature | [F_100 Team 扩展机制接入](../../../../agent_teams/docs/features/F_100_team-extension-wiring.md)（契约层实现与成员接线）；[F_08 team.build_policy](F_08_team-build-policy.md)（同批事件的策略构建侧） |
| 验证摘要 | 事件契约、Pattern 筛选与桥接全生命周期经单元及端到端验证，见第 7 节 |
| Refs | #2163 |

## 2. 背景、目标与范围

### 2.1 背景与问题

团队运行态（建队、成员生命周期、任务流转、消息）此前只在 Team 子系统的内部事件与监控查询里可见：
扩展插件可以通过 `ops.team` 查询团队数据库快照，却没有任何途径在状态变化发生的时刻得到通知，只能轮询。
Team 需要一个把公开监控通知投递给扩展 Handler 的事件入口。

本事件没有 Claude Code 对应物——Claude Code 无多 Agent 团队协作模型，`team.monitor_event`
是 openjiuwen Team 子系统的原生扩展点。

### 2.2 目标与关键场景

| 场景 / 触发条件 | 此前行为 | 目标行为 |
| --- | --- | --- |
| 任务完成 | 插件无感知，只能轮询查询 | Handler 收到 `task_completed`，可按需经 `ops.team` 查任务详情 |
| 团队创建 | 同上 | Handler 收到 `team_created`，可读取展示名与队长并渲染团队卡片 |
| 成员状态变化 | 同上 | Handler 收到 `member_status_changed`，刷新成员名册 |
| 高频通知 | — | Handler 用 Pattern 按类型 / 团队 / 成员收窄订阅，避免全量处理 |

### 2.3 本次范围与非目标

- 本次包含：`team.monitor_event` 的事件契约（触发位置、输入 / 输出、Pattern 字段）、
  桥接的行为边界（队列、丢弃、顺序）、宿主接入面。
- 本次不包含：桥接与成员接线的实现细节（[F_100](../../../../agent_teams/docs/features/F_100_team-extension-wiring.md) 管辖）；
  任何写入或干预团队状态的能力——通知是事后只读投递，不参与原操作决策。

## 3. 设计约束与已核实前提

- 事件注册、Handler 链（USER → BUILTIN → CORE）、Pattern 匹配、输入输出校验与容错
  全部沿用 [S_01](../specs/S_01_events-contract.md) 公共契约，本事件不新增框架机制。
- 通知源是 Team 子系统监控的公开事件：`MonitorEventType` 共 25 个值（见 5.2）。
  内部协调信令（计划 / 工具审批、worktree、workspace 锁）被有意排除，见
  [S_14](../../../../agent_teams/docs/specs/S_14_monitor-and-observability.md)。
- 桥接挂在 leader 的 coordination 事件回调路径上——该路径同时服务团队主链路，
  扩展 Handler 的执行不能阻塞它。
- 无未决问题。

## 4. 总体方案与设计取舍

### 4.1 方案概览

```mermaid
flowchart LR
    A["团队状态变更（已落库、状态机已流转）"] --> B["messager 内部事件"]
    B --> C["TeamMonitorEventBridge<br/>有界队列 · 非阻塞入队"]
    C --> D["独立消费任务逐条出队"]
    D --> E["trigger(team.monitor_event)"]
    E --> F["USER / BUILTIN Handler 链"]
    F --> G["CORE：空标记返回"]
```

团队状态变更完成后，桥接把公开通知非阻塞入队；独立消费任务逐条触发事件，
Handler 链执行完继续取下一条。通知链的成败不影响已完成的团队操作。

### 4.2 关键决策

#### D-01 桥接非阻塞：有界队列 + 满丢弃计数

- 选择与依据：coordination 回调不能等待扩展 Handler。队列容量 1024，满时丢弃并计数，
  stop 时取消消费任务并清点积压；通知链失败不回滚已完成的操作。
- 代价：不保证恰好一次交付、全局有序（跨进程场景）、所有订阅者都执行（链可短路）——
  这些是明示的行为边界而非缺陷。
- 实现体现：`monitor_event_bridge.py`；验证场景 F_07-V-02 / F_07-V-04。

#### D-02 Output 为空标记类型

- 选择与依据：通知是单向消费，没有可组合的返回值语义。`TeamMonitorEventOutput`
  仅表示通知链完成，CORE 无副作用。
- 代价：Handler 返回 `None` 是合法短路——后续 Handler 与 CORE 不执行，订阅方须知。
- 实现体现：`events.py` 的 CORE 处理程序；验证场景 F_07-V-03。

#### D-03 Pattern 三字段覆盖三个筛选维度

| 字段 | 匹配来源 | 说明 |
| --- | --- | --- |
| `monitor_event_type` | `monitor_event.event_type.value` | 通知类型，如 `"task_created"` |
| `team_name` | Input 的 `team_name` | 团队名称 |
| `member_name` | Input 的 `member_name` | 通知所属成员；`None` 时指定该字段不匹配 |

多字段 AND 组合、精确值比较、不支持通配符或正则；省略某字段即不限该维度。
三个字段分别回答"什么类型 / 哪个团队 / 关于谁"。

#### D-04 输入不可改写

通知是事后投递，输入模型不提供改写通道；`monitor_event` 虽是可变 Pydantic 模型，
对它的修改不回写团队状态。

### 4.3 替代方案与排除理由

| 候选 | 与采用方案的差异 | 排除原因 | 重新评估条件 |
| --- | --- | --- | --- |
| 背压等待（队列满时阻塞入队） | 保证不丢通知 | 阻塞 coordination 回调，团队主链路被扩展拖累 | 出现必须不丢通知的业务场景时，另行引入分级通道 |
| 每成员信箱式点对点推送 | 精确投递到单个订阅方 | 重复建设消息通道；Pattern 的 `member_name` 已覆盖成员维度筛选 | 无 |
| 恰好一次 + 全局有序的强投递语义 | 语义更强 | 跨进程场景不可实现，插件监控场景不需要 | 无 |

## 5. 事件契约

### 5.1 触发位置与粒度

- **粒度**：一次处理一条通知。
- **频率**：与团队活动正相关——建队后每条任务 / 成员 / 消息事件各触发一次。
- **触发前**：团队状态变更已完成（DB 已写入、状态机已流转）。返回结果不参与原操作的决策。
- **返回后**：桥接消费任务继续取下一条。通知链失败不回滚已完成的操作。
- **不保证**（见 D-01）：恰好一次交付（队列满丢弃并计数）、全局有序（跨进程场景）、
  所有订阅者都执行（可短路）。

### 5.2 通知类型

`MonitorEventType` 枚举共 25 个公开值，四类：

| 类别 | 事件类型 |
| --- | --- |
| 团队生命周期 | `team_created`、`team_cleaned`、`team_standby` |
| 成员生命周期 | `member_spawned`、`member_restarted`、`member_status_changed`、`member_execution_changed`、`member_shutdown`、`member_canceled` |
| 任务流转 | `task_created`、`task_plan_request`、`task_plan_response`、`task_updated`、`task_claimed`、`task_started`、`task_completed`、`task_cancelled`、`task_unblocked`、`task_released`、`task_revoked`、`task_submitted_for_review`、`task_verified`、`task_revision_requested` |
| 消息 | `message`、`broadcast` |

### 5.3 输入

| 字段 | 类型 | 可改写 | 说明 |
| --- | --- | --- | --- |
| `team_name` | `str` | 否 | 通知所属团队标识 |
| `member_name` | `str \| None` | 否 | 通知"关于谁"，取自 `MonitorEvent.member_name`；非"谁收到"。团队级事件为 `None` |
| `monitor_event` | `MonitorEvent` | 是（可变模型，但修改不回写团队状态） | 公开监控事件，含 `event_type`、`timestamp` 和各类型载荷字段（如 `task_id`、`old_status` / `new_status`、`message_id` 等），完整字段见 [S_14](../../../../agent_teams/docs/specs/S_14_monitor-and-observability.md) |

### 5.4 输出

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| （空） | `TeamMonitorEventOutput` | 空标记类型；仅表示通知链完成 |

返回 `None` 是合法的短路——后续 Handler 不执行，CORE 不执行（见 D-02）。

### 5.5 Handler 示例

```python
import logging

logger = logging.getLogger("my_plugin.team_events")


async def on_task_completed(event, ops, next_handler):
    """Log completed tasks with their details fetched via Ops."""
    input_ = event.event_input
    monitor = input_.monitor_event
    # Pattern 已筛出 task_completed，此处直接处理。
    logger.info("task done: %s in %s", monitor.task_id, input_.team_name)
    if ops is not None and ops.session_context is not None:
        # press / ready 等非 monitor 事件链无当前团队绑定，须显式传 team_name。
        task = await ops.team.task(monitor.task_id, team_name=input_.team_name)
        logger.info("  title=%s status=%s assignee=%s", task.title, task.status, task.assignee)
    return await next_handler(event)


event_bus.register_event_handler(
    "team.monitor_event",
    on_task_completed,
    match_pattern={"monitor_event_type": "task_completed"},
    handler_level=HandlerLevel.USER,
)
```

## 6. 方法契约与使用注意

本事件无专属 Ops 方法。Handler 可调用 `ops.team` 命名空间的只读查询方法
（`info` / `members` / `member` / `tasks` / `task` / `task_dependencies` / `messages` /
`message`），详见 `openjiuwen/extensions/ops_build_handlers/team.py`。

使用注意：

- **事件量大**：空 Pattern 订阅全部通知在繁忙团队下每分钟可达数十条。建议按业务需要
  用 `match_pattern` 收窄。
- **非 monitor 链无团队绑定**：`ui.press` 等前端事件链中调用 `ops.team` 查询须显式传
  `team_name`。

宿主接入面：`register_team_event_types(bus)` 注册事件 + CORE + 校验器，
`Runner.set_team_extension_bus(bus)` 注入桥接所需总线——接线细节与生命周期见
[F_100](../../../../agent_teams/docs/features/F_100_team-extension-wiring.md)。

## 7. 验证设计、执行记录与结论

### 7.1 验证设计与通过条件

| 验证 ID | 场景 / 前置状态与输入 | 预期结果 |
| --- | --- | --- |
| F_07-V-01 | 无插件时团队正常运行 | 提示词与基线逐字一致（无扩展零变化） |
| F_07-V-02 | 插件订阅 `task_created`，建队并建任务 | Handler 收到事件；桥接全程不阻塞团队操作 |
| F_07-V-03 | Pattern 筛选 `team_created` | 仅建队事件到达 Handler，其余不进入 |
| F_07-V-04 | 团队启动 / 停止 / 暂停 | 桥接随冷构建挂载、stop 路径拆除、pause 保留；stop 日志出现 |
| F_07-V-05 | 宿主接线后 bus 到达 TeamRuntimeManager | manager 持有同一 bus 实例（宿主接线单测） |

### 7.2 实际执行与证据

| 日期、范围 | 环境与入口 | 实际结果 |
| --- | --- | --- |
| 2026-10-30，契约与桥接单测 | `tests/unit_tests/agent_teams/extensions/`（真 `ExtensionEventBus` 端到端：注册 → USER handler → trigger → 链 / 短路 / 校验器 / 容错；桥接全生命周期含取消传播） | 全部通过 |
| 2026-10-30，Ops 查询单测 | `tests/unit_tests/extensions/ops_build_handlers/`（内存 DB + 真 pool 的 resolver、任务详情查询） | 全部通过 |
| 2026-10-30，端到端运行 | 真实宿主进程 + 验证插件（订阅多类通知） | 一次团队运行收到 41 条事件、覆盖 8 种类型；任务详情经 `ops.team.task` 查询成功 |

### 7.3 验证结论

- 已证明范围：事件契约（输入 / 输出 / Pattern 三字段）、桥接生命周期、无插件零变化、
  宿主接线与端到端通知投递。
- 未覆盖项：跨进程部署下的投递顺序（行为边界本就不保证，见 D-01）。
- 可支持的交付判断：本事件契约满足"插件可订阅团队公开监控通知"的承诺。

## 8. 已知遗留

无。

## 9. 参考资料

- 事件契约实现：`openjiuwen/agent_teams/extensions/events.py`、`models.py`、`matching.py`
- 桥接实现：`openjiuwen/agent_teams/extensions/monitor_event_bridge.py`
- 通知模型：`openjiuwen/agent_teams/monitor/models.py`（规约见 S_14）
- 整体接线：[F_100 Team 扩展机制接入](../../../../agent_teams/docs/features/F_100_team-extension-wiring.md)
