# F_29 Reliability framework (member health monitoring + remediation)

日期：2026-06-02

## 背景

`agent_teams` 之前只有**被动**的健康面：`monitor/`（`TeamMonitor` 只读视图）、
`observability/`（OTel 导出）、`agent/recovery_manager.py`（进程心跳重启）、
`stream_controller`（LLM 重试）。缺一个**主动识别成员"逻辑性不健康"并据此决策处置
的闭环**——成员可能陷入工具反复报错、重复/循环工具调用、输出/思考爆长、上下文频繁
压缩、两成员无限 pingpong 来回。这些都不是进程崩溃，心跳看不到，但会烧 token、卡死
任务、产生无效输出。

诉求：在成员执行生命周期采集状态（经 deepagent rail）→ 检测异常 → 按分级策略路由到
三条处置路径（喂 leader LLM 决策 / 非 LLM 自动化兜底 / 上报用户），全部可配置。

规约见 [[S_19_reliability-framework]]。

## 数据结构 / 状态机

- **统一信号流** `Signal(kind, member_name, tool_name?, tool_args?, error?, text_len?,
  thinking_len?, message_count?, peer_member?)`：rail 每个 hook 构造一个 Signal，广播给
  所有 detector，detector 按 `SignalKind` 自取所需。采集层与检测层解耦。
- **Anomaly**（Pydantic，可跨进程）`{detector, kind, severity, member_name, summary,
  evidence, peer_member}`。`Severity` LOW/MEDIUM/HIGH/CRITICAL（str enum + `rank`
  property）；`AnomalyKind` 8 种。
- **两个信号源**：成员内（工具/模型/输出/压缩）走 per-member `ReliabilityRail`；团队级
  （pingpong）走 leader 进程的 `ReliabilityHandler` 监听 MESSAGE/BROADCAST。
- **跨进程上报**：anomaly → `AnomalyDetectedEvent`（新 TeamEvent）→ messager TEAM topic
  → leader coordination 消费。复用现有事件通道，零新增传输机制。

## 决策

1. **命名 `reliability` 而非 `safety`**：`security` 在本仓库已被占用且语义不同
   （`harness/security/` 工具权限引擎、`harness/rails/security/` prompt 安全）。本模块解
   决"成员健康度 + 编排可靠性"，业界对应词即 reliability framework。
2. **单一 `ReliabilityRail` 采集全部成员内信号**，不为每类信号建独立 rail——监听相邻
   hook 的职责合并到一个 rail（对齐"职责相近的 rail 倾向合并"）。
3. **统一 `Signal` 流 + `Detector` Protocol（`observe(signal) -> Anomaly | None`）**：检测
   器是纯逻辑、无 I/O、易单测；直接触发型与滑窗采样型统一在 `observe` 内（窗口状态私有）。
4. **`ErrorBurstDetector` 基类复用**：工具错误率与模型错误率结构同构（滑窗速率 + 连续失
   败 + 边沿触发防刷屏），抽一个基类参数化，tool/model 各薄包装。
5. **重复/循环检测用稳定哈希 + N=30 滑窗 + 四级阈值**（同调用≥10 LOW / 交替≥10 MEDIUM /
   连续同 outcome≥20 HIGH / ≥30 CRITICAL）。稳定哈希递归排序 args，避免参数顺序漏检。
6. **anomaly 统一经 `AnomalyDetectedEvent` 上报到 leader 进程**，leader 的
   `ReliabilityHandler`（仿 `StaleTaskHandler` 的 coordination handler）统一消费——无论来自
   teammate 子进程的 rail 还是 leader 本进程。
7. **分级处置，默认上报 leader**：LOW 仅记录；MEDIUM 喂 leader（LLM 用现有工具决策）；
   HIGH 本地可逆自纠 + 上报；CRITICAL 自纠 + 升级用户。映射可配
   （`RemediationPolicyConfig`）。
8. **自动化只做可逆动作**：本地自纠用 rail 的 `ctx.push_steering()` 注入非破坏纠正指令
   （不终止 round、不改工具），受 restart-intensity 上限约束（period 内超额则停止自纠、
   交 leader/用户）。停成员/取消任务/spawn 等破坏性动作**一律不在本模块执行**，交 leader
   LLM 用现有 team 工具——契合 coordination「不做决策」铁律。
9. **opt-in**：`ReliabilityConfig.enabled` 默认 `False`，对现有团队零影响；handler 仅
   enabled 时注册到 dispatcher，rail 仅 enabled 且角色命中时挂载。
10. **`monitor_roles` 默认 `["teammate"]`**：leader 自监控暂不做——leader 自己发的
    anomaly 事件会被其 messager self-filter 过滤，需独立通道，留后续。

## 拒绝的方案

1. **每类信号一个 rail**（StreamErrorRail / ToolErrorRail / ...）：拒绝。监听同/相邻 hook
   的逻辑碎片化，违背 rail 合并约定。改为单 `ReliabilityRail` + 多 detector。
2. **leader 下发跨进程 action 事件硬控成员（停 poll / 拒绝工具）**：评估后拒绝。需新增
   action 事件 + 所有成员进程消费路径 + resume 机制，重；且 `pause_polls` 对 round 内的工
   具循环无效（循环不靠 poll 驱动）。关键洞察：**工具循环的检测（rail）与最自然的可逆干预
   （push_steering 自纠）在同一成员进程**，本地闭环即可，无需跨进程。
3. **破坏性自动化（自动停成员 / 取消任务 / spawn 替换）**：拒绝（用户拍板"仅可逆"）。
   agent restart 丢上下文 + 烧 token，且易误杀正常的长输出 / 合理重试。破坏性决策交 leader。
4. **改 `dispatch()` 粗筛门放行 `ANOMALY_DETECTED`**：不需要。dispatch 对 LEADER/TEAMMATE
   默认放行所有 transport 事件（仅 HUMAN_AGENT 有白名单），handler 注册 EVENT_METHOD_MAP
   即可被 framework 路由。
5. **`Severity` 用 `IntEnum`**：拒绝。用 str enum + `rank` property——跨进程 JSON 序列化
   友好（event 里是 "low"/"medium"/...），同时保留可比较性。

## 验证

- `tests/unit_tests/agent_teams/reliability/` 50 用例全绿：window（稳定哈希顺序无关 +
  滑窗淘汰）、7 detectors（喂 signal 序列断言阈值前后 `observe` 返回与 severity）、
  rail（各 hook→Signal + monitor fan-out + 本地 steer）、reporter（事件发布 +
  round-trip）、remediation（policy 分级 + restart-intensity 预算）、handler（leader 路由
  + 非 leader 忽略 + pingpong）。
- dispatch 端到端（`test_integration.py`）：enabled 时 `AnomalyDetectedEvent` 经真实
  `dispatch()` 路由到 leader 并 `deliver_input`；disabled 时无 handler、事件 no-op。
- 回归：`test_harness.py` / `test_coordination_loop.py` / `test_coordination_lifecycle.py`
  全绿，接线未破坏现有行为（合计 83 passed）。
- 按 CLAUDE.local.md 约定未运行 ruff / mypy / make check。

## 已知遗留

- **流式 chunk 级错误**与**上下文压缩事件**无专用 core hook：`ModelStreamErrorDetector`
  只到模型调用级粒度，`FrequentCompactionDetector` 靠相邻 `message_count` 骤降推断（封顶
  MEDIUM、不进自动化）。更高精度需在 core 补 hook，超出本模块范围。
- **工具层 alternation（A-B-A-B）pingpong**：未采集 result 内容，正常 "ok" 迭代可能被误
  判，故封顶 MEDIUM 交 leader 判断、不自动处置。
- **leader 自监控**：受 messager self-filter 限制，待独立通道。
- **per-member 阈值覆盖**、**熔断/restart-intensity 跨 leader 重启持久化**（可落 session
  team namespace）、**更激进的处置动作矩阵**（OTP one_for_one / one_for_all /
  rest_for_one restart 策略）、**leader 主动查询告警历史工具**——均为后续扩展点，骨架已留。
- reliability 单测的 `@pytest.mark.level0/1` 分级 marker 仅 `test_integration` 标注，其余
  待统一补全。
