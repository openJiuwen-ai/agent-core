# F_30 Reliability follow-ups: leader self-monitor + result-aware repeat

日期：2026-06-03

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-06-03 |
| 范围 | openjiuwen/agent_teams/reliability/ + agent/agent_configurator.py + agent/team_agent.py |
| 测试基线 | 160 passed（reliability 全量 + harness/coordination/team_agent_coordination 回归） |
| Refs | #751 |

## 背景

可靠性框架 MVP（[[F_29_reliability-framework]]）落地后留了两个「已知遗留」，本次推进
（用户选定）：

1. **leader 自监控**：MVP 的 `monitor_roles` 默认只含 `teammate`，因为 leader 自己发的
   anomaly 事件会被它自己的 messager self-filter（kernel 丢弃 `sender_id == 本成员` 的事件）
   过滤掉，leader 收不到自身异常。
2. **result 采样提精度**：`RepeatToolCallDetector` 的 outcome 只区分 `"ok"`/error，无法分辨
   "成功但结果每次不同的合理迭代"vs"成功但结果相同的死循环"，工具层 alternation/no-progress
   会误判正常迭代。

## 数据结构 / 状态机

- `Signal` 新增 `tool_result: Any | None`——`ReliabilityRail.after_tool_call` 从
  `ctx.inputs.tool_result` 填入。
- `window.stable_result_hash(result)`：`None→"none"`、`str→hash`、pydantic `model_dump`
  （ToolOutput 等）→ sorted-json hash、其余 `str` fallback（`default=str` 容错）。
- `RepeatToolCallDetector` 的 AFTER outcome 从固定 `"ok"` 改为 `stable_result_hash(tool_result)`
  ——相同结果同 outcome（计入循环），不同结果不同 outcome（不计）。
- `LocalAnomalyReporter`（实现 `AnomalyReporter` Protocol）：持可后绑的 `_sink`；`bind(sink)`
  设置，`report(anomaly)` 直投 sink（未绑前 no-op + warning）。

## 决策

1. **leader 自监控用本地直投，不碰 kernel self-filter**。leader 自己的 anomaly 在 leader
   进程，直投本地 `ReliabilityHandler.handle_local_anomaly`（复用 `_route`），既绕过
   self-filter，又省一次 messager round-trip、不污染其他成员的事件流。
2. **复刻 `_register_team_completion_callbacks` 的回填模式**解决时序：rail 在
   `configurator.setup_agent` 先建（handler 尚不存在），`coordination.setup` 后建 handler；
   新增 `TeamAgent._register_reliability_local_sink` 在 dispatcher 建好后用
   `harness.find_rails(ReliabilityRail)` + `dispatcher.reliability` 把 handler 的
   `handle_local_anomaly` 回填进 rail 的 `LocalAnomalyReporter`。
3. **factory 按 role 选 reporter**：leader 用 `LocalAnomalyReporter`（同时作为 rail 的
   `local_reporter`），其余用 `EventAnomalyReporter`。configurator 条件放宽——leader 豁免
   `self.messager` gate（LocalReporter 不需 messager），teammate 仍需（EventReporter 跨进程）。
4. **`monitor_roles` 默认改为 `["leader", "teammate"]`**：能力可用后，enabled 时默认也监控
   leader。
5. **result hash 向后兼容**：`tool_result` 为 None 时 `stable_result_hash` 返回固定 `"none"`，
   等价 MVP 的固定 `"ok"`（循环检测语义不变）；generic-repeat（同 call_key 计数）不受影响。

## 拒绝的方案

1. **改 kernel self-filter 豁免 ANOMALY_DETECTED 事件**：可让 leader 自己的 anomaly 不被
   过滤，但触及 coordination 核心、且会让所有成员收到自己的 anomaly（多余 dispatch）。改用
   本地直投，影响面局限在 reliability + team_agent 一个回填方法。
2. **leader reporter 用特殊 `sender_id` 常量绕过 self-filter**：最小改动，但污染
   "sender_id = 发布者 node id" 语义，且 anomaly 仍跨进程广播给所有成员。本地直投更干净。
3. **result 摘要取 `tool_msg`（渲染文本）而非 `tool_result`**：tool_msg 是面向 LLM 的渲染，
   可能含易变前缀；`tool_result`（ToolOutput / 原始值）经 `model_dump` 更稳定。

## 验证

- `tests/unit_tests/agent_teams/reliability/` 增补并全绿：
  - `test_window`：`stable_result_hash` None/str/dict/pydantic(ToolOutput)/非序列化分支 + 顺序无关。
  - `test_detectors`：result 变化不判循环、result 相同判循环（`_call_cycle` 加 `tool_result`）。
  - `test_reporter`：`LocalAnomalyReporter` 绑前 no-op、绑后直投。
  - `test_handler`：`handle_local_anomaly` leader-only + 按 policy `deliver_input`。
  - `test_rail`：`after_tool_call` 捕获 `tool_result`；leader rail `bind_local_sink` 后经
    detector→LocalReporter→sink；非 leader rail bind 为 no-op。
  - `test_integration`：leader 自监控端到端——leader 自己的 anomaly 经本地 sink → handler →
    `deliver_input`，**不经 messager**。
- 回归：160 passed（含 `test_team_agent_coordination` / `test_harness` / `test_coordination_*`，
  确认 `_register_reliability_local_sink` 回填未破坏现有 configure）。
- 按 CLAUDE.local.md 未运行 ruff/mypy/make check。

## 已知遗留

- result hash 对非确定性内容（时间戳、随机 id）仍可能漏判循环，故工具层 alternation 仍封顶
  MEDIUM 交 leader 判断。
- F_28 其余遗留项未动：per-member 阈值覆盖、anomaly 历史 + leader 查询工具、流式 chunk /
  压缩事件的 core hook、熔断/restart-intensity 持久化。
