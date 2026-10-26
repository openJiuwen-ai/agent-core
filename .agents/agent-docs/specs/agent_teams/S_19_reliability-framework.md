# S_19 Reliability framework

最近一次修订日期：2026-06-03

本 spec 定义 `openjiuwen/agent_teams/reliability/` 的契约。实现来龙去脉与拒绝的方案见
[[F_29_reliability-framework]] 与 [[F_30_reliability-self-monitor-and-result-aware-repeat]]。

## 元信息

| 项 | 值 |
|---|---|
| 类型 | spec |
| 关联模块 | openjiuwen/agent_teams/reliability/ |
| 最近一次修订日期 | 2026-06-03 |
| 关联 feature | F_29_reliability-framework.md / F_30_reliability-self-monitor-and-result-aware-repeat.md |

## 范围 / 边界

**管**：成员执行期的健康信号采集（经 deepagent rail）、异常检测（监控策略）、异常的分级
处置（处理策略：本地可逆自纠 / 上报 leader / 上报用户）。

**不管**：破坏性团队操作（停成员 / 取消任务 / spawn）——那是 leader LLM 用现有 team 工具
的决策，本模块只产出告警与可逆 nudge。也不替代 `monitor/`（只读观测）与
`observability/`（OTel 导出），与二者正交。

opt-in：`TeamAgentSpec.reliability.enabled` 默认 `False`。

## 不变量

1. **自动化只做可逆动作**。`RemediationAction` 中唯一的自动动作是 `LOCAL_STEER`
   （`ctx.push_steering` 注入纠正指令，不终止 round、不改工具参数）。`OBSERVE_ONLY` /
   `REPORT_LEADER` / `ESCALATE_USER` 不改变团队状态。破坏性动作不在枚举内。
2. **自动化受 restart-intensity 上限约束**：`LocalAutoRemediator` 在 `period_seconds` 内最多
   发 `intensity` 次 steer，超额返回 None（停止自纠，交 leader/用户），防自愈风暴。
3. **anomaly 路由到 leader**：teammate 经 `AnomalyDetectedEvent` 跨进程上报；leader 自身经
   `LocalAnomalyReporter` 本地直投 `ReliabilityHandler.handle_local_anomaly`（绕过 messager
   self-filter）。两路都走 `ReliabilityHandler` 的 policy 路由；`on_anomaly_detected` /
   `on_message` / `handle_local_anomaly` 均以 `role == LEADER` 为前置门。
4. **检测器是纯逻辑**：`Detector.observe(signal) -> Anomaly | None`，无 I/O、无副作用；窗口/
   计数状态私有，`reset()` 清空。一个 detector 抛异常被 monitor 记录并跳过，不影响其它。
5. **`ReliabilityRail` 是 observe-only**：唯一对外副作用是 `push_steering`（非破坏）。priority
   低于业务 rail（=5），不抢在 prompt/tool rail 前。
6. **handler 仅在 `enabled` 时注册**到 dispatcher；rail 仅在 `enabled` 且
   `role.value in monitor_roles` 时挂载——leader 用 `LocalAnomalyReporter`（不需 messager），
   其余角色需 messager（`EventAnomalyReporter` 跨进程发布）。
7. **检测分级边沿触发**：同一异常 severity 不上升时不重复产出，避免告警风暴。

## 接口契约

- **配置入口** `ReliabilityConfig`（`TeamAgentSpec.reliability`，公开导出）：
  `enabled` / `monitor_roles: list[str]`（TeamRole values，默认 `["leader", "teammate"]`）/
  `detectors: DetectorsConfig`（每检测器 enabled + 阈值）/ `policy: RemediationPolicyConfig`
  （`severity_actions: dict[Severity, list[RemediationAction]]`）/
  `restart_intensity: RestartIntensityConfig`（intensity / period_seconds）。
- **`Detector` Protocol**：`name`（property）、`observe(signal) -> Anomaly | None`、`reset()`。
- **`AnomalyReporter` Protocol**（`report(anomaly)`）两实现：`EventAnomalyReporter`（teammate，
  发 `AnomalyDetectedEvent` 跨进程）、`LocalAnomalyReporter`（leader，`bind(sink)` 后直投本地
  handler，绕过 self-filter）。
- **`RemediationPolicy.actions_for(severity) -> list[RemediationAction]`**。默认分级：
  LOW→[OBSERVE_ONLY]、MEDIUM→[REPORT_LEADER]、HIGH→[LOCAL_STEER, REPORT_LEADER]、
  CRITICAL→[LOCAL_STEER, ESCALATE_USER]。
- **`AnomalyDetectedEvent(BaseEventMessage)`**（`schema/events.py`，`TeamEvent.ANOMALY_DETECTED`）：
  `detector / kind / severity / summary / evidence / peer_member`。`kind` / `severity` 存
  str（`AnomalyKind` / `Severity` 的 value），使 schema 不依赖 reliability 包。
- **装配 helper**（`reliability/factory.py`，供 configurator / dispatcher 直接 import）：
  `build_reliability_rail(...)` / `build_remediation_policy(config)` /
  `build_pingpong_detector(config)` / `build_member_detectors(detectors_config)`。

## 数据结构

- `Signal` / `SignalKind`：统一信号流（见 F_29）。`AFTER_TOOL_CALL` 携带 `tool_result`，喂
  result-aware repeat 检测。
- `Anomaly` / `Severity`（含 `rank`）/ `AnomalyKind`。
- 内置检测器（成员内 5 + 团队级 1）：
  | Detector | 信号源 hook | kind | 默认阈值 |
  |---|---|---|---|
  | `ToolErrorRateDetector` | tool_exception / after_tool_call | TOOL_ERROR_RATE | 窗口60s≥5 或 连续≥3 |
  | `RepeatToolCallDetector` | before/after_tool_call + tool_exception | REPEAT_TOOL_CALL / TOOL_CALL_LOOP | 重复10 / 交替10 / 连续20 / 全局30；outcome 用 `stable_result_hash`（相同结果计循环、不同结果不计）|
  | `ModelStreamErrorDetector` | model_exception / after_model_call | MODEL_ERROR | 窗口120s≥3 或 连续≥2 |
  | `OutputLengthDetector` | after_model_call | OUTPUT_TOO_LONG / THINKING_TOO_LONG | 文本>32k / 思考>16k 字符 |
  | `FrequentCompactionDetector` | before_model_call | FREQUENT_COMPACTION | 窗口300s≥3 次推断压缩 |
  | `PingPongDetector` | leader MESSAGE / BROADCAST | PING_PONG | 连续来回≥6 volleys |

## 数据流

```
成员进程：rail hook → Signal → ReliabilityMonitor.feed
            → detector.observe → Anomaly
            → (policy: REPORT_LEADER/ESCALATE_USER) reporter.report
                 ├ teammate: EventAnomalyReporter → AnomalyDetectedEvent → messager
                 └ leader:   LocalAnomalyReporter → handle_local_anomaly（本地直投，不经 messager）
            → (policy: LOCAL_STEER, within budget) rail.ctx.push_steering  ← 可逆自纠
leader 进程：AnomalyDetectedEvent → dispatch() → ReliabilityHandler.on_anomaly_detected
            → policy.actions_for → round.deliver_input(report / escalate 文案)  ← leader LLM 决策
团队级：    MESSAGE/BROADCAST → dispatch() → ReliabilityHandler.on_message
            → PingPongDetector.observe → (同上路由)
```

## 与其它 spec 的关系

- **S_03 协调协议**：`ReliabilityHandler` 是新增的（可选）coordination handler，遵循
  `BaseCoordinationHandler` 约定；条件注册于 `EventDispatcher`，在 MESSAGE 上于
  `MessageHandler` 之后 fan-out，不监听 POLL_TASK（不影响完成判定排序）。
- **S_09 Prompts and rails**：`ReliabilityRail(DeepAgentRail)` 是新增的 observe-only rail，
  经 `TeamHarness.build(reliability_rail=...)` 挂载。
- **S_12 Schema 数据模型**：新增 `AnomalyDetectedEvent` + `TeamEvent.ANOMALY_DETECTED`。
- **S_14 Monitor and observability**：正交——本模块主动检测 + 处置，monitor/observability
  被动观测；不互相依赖。
