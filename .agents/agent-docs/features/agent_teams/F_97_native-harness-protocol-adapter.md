# NativeHarness 公共协议适配

- 日期：2026-09-10
- Refs: #751
- 范围：`agent_teams/harness/protocol_adapter.py`

## 行为与构造

新增 `NativeHarnessProtocolAdapter`，在 team 层组合真正的 `NativeHarness`，对外实现根级
`openjiuwen.harness_protocol.HarnessProtocol`。构造不启动 runtime；每个 start/stop cycle
从保留的 `DeepAgentSpec` 构造一个新 NativeHarness。

`create_native_harness_protocol(manifest, agent_spec=..., build_context=...)` 复用
`harness_providers.factory.load_manifest` 读取 AgentTemplate，补齐未显式指定的 card/model，
把模板保存到现有 `DeepAgentSpec.agent_template_spec`。仍由 NativeHarness.start 的
`_prepare()` 初始化基础 rails，再调用已有 `load_agent_template_spec()` 装配模板，且每个实例只装配一次。
不复制模板工具、rail、subagent、MCP、skill 的解析与装配规则。

根级 `create_harness(..., provider="native")` 仍返回裸 DeepAgent 的适配器。
`TeamHarness` 与 team spawn 原调用链保持原状。统一入口
`create_harness(manifest, provider="native_v2")` 经 NativeV2HarnessProvider 复用本工厂；
card 和 checkpoint provider 名均为 `native_v2`，独立构造入口仍可用于传入 BuildContext。

## 协议边界

- 输入排队由 `SerializedTurnHarness` 管理，一次只派发一个输入给 NativeHarness，保留独立 receipt/Turn ID。
- 复用 DeepAgentHarness 的文本、思考、工具、中断映射；ask-user 应答仍通过 protocol interactions。
- native 内部 round、任务计划续跑与 pause/resume continuation 均不创建新的外部 Turn。
- 原生 state/round 回调向 session stream 写入内部控制标记，经同一个输出 FIFO 转发，保证最后的输出
  先于终态。直到 NativeHarness 整条链 IDLE 才结束外部 Turn，内部失败重试不会提前终结 Turn。
- pause 到达 PAUSED 后发布 PAUSED；resume 发布同一 Turn 的 RESUMED。有限 `turn_events()` 跨暂停保持打开。
- GRACEFUL_ABORT / FORCE_ABORT 分别转发 `abort(immediate=False/True)`，边界停止与回滚仍由原生实现负责。
- 适配器独占 native 输入和输出通道，调用方不得同时直接驱动 `native_harness.send()` 或消费其 outputs。
- CHECKPOINT/PERSISTENT_SESSION 和跨实例冷恢复由 [F_98](F_98_native-checkpoint-and-endpoint-recovery.md) 补齐；warm resume 保持原行为。

## 验证

`tests/unit_tests/agent_teams/harness/test_protocol_adapter.py` 使用真实 NativeHarness supervisor、
任务循环、session 流和暂停快照，仅替换模型执行：验证独立排队、终态输出排空、模型/工具阶段暂停、
同 Turn 恢复、两种 abort、暂停时 stop、早期 abort、ask-user 与工具/思考输出、模板装配和 cycle 重建。
