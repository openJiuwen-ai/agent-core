---
title: tool.register 启动工具登记桥接
author: null
author_note: 历史作者待确认；缺少原 owner 和可靠创建署名
id: F_27_tool-registration-event-bridge
type: F
modules:
- openjiuwen/harness
files: []
designs: null
design_note: 历史设计关联待核实，不等同于没有关联设计
---

# tool.register 启动工具登记桥接

## 元信息

| 项 | 值 |
|---|---|
| 类型 | feature |
| 日期 | 2026-11-11 |
| 范围 | `DeepAgentConfig`、factory、`DeepAgent`、`SysOperationRail`、`DeepAgentSpec` |
| 关联 spec | [S_01 公开 API 与构造流](../specs/S_01_public-api-and-construction.md)、[S_05 工具契约](../specs/S_05_tools-contract.md) |
| Core 契约 | [F_11 `tool.register`](../../core/events/docs/features/F_11_ev11-tool-register-ops.md) |
| 设计依据 | `2026-11-11-JiuWenSwarm tool.register 工具注册重构方案与实施计划` |

## 背景

JiuWenSwarm 的扩展需要在工具进入 Agent 时观察或改写统一的 `tool.register` 事件。
旧路径由 factory、`SysOperationRail` 等入口直接调用 `AbilityManager.add_ability`，使同一
工具注册事实存在多个入口；同时，Agent 首次初始化时还没有用户 `SessionContext`，不能把
启动登记伪装成公共 `ops.tool.register` 调用。

## 设计决策

`DeepAgentConfig.tool_registration_mode` 是显式的兼容开关：

| 模式 | 具体启动 `Tool` | 纯 `ToolCard` | bus 要求 |
|---|---|---|---|
| `legacy`（默认） | 维持原直接登记 | 维持原卡片登记 | 无 |
| `event` | 进入 FIFO 队列，经 `tool.register` 事件登记 | 仍走卡片登记 | 必须有 live `ExtensionEventBus` |

event 模式只覆盖一期 live 工具入口：factory 产生的、被宿主明确选中的具体 `Tool`，以及
`SysOperationRail` 产生的具体 `Tool`。`DeepAgentSpec.event_tool_types` 用 provider type
allowlist 表达宿主边界；未被选中的 concrete tool 继续走 legacy。`swarm.*` 业务 rail、代码
工具 bundle 的旧手工注册以及动态插件 `load_plugin_ability` 保持各自既有边界，不在本期扩大范围。

事件调用链如下：

```text
DeepAgentConfig / DeepAgentSpec
  → apply_deep_agent_parts()：具体 Tool 入 FIFO
  → _ensure_initialized()：先 flush factory 工具
  → rail.init()
  → flush 新增工具
  → Event("tool.register", ToolRegisterInput(source=tool))
  → ToolRegisterTriggerContext(agent=DeepAgent)
  → USER → BUILTIN → CORE
  → DeepAgent.load_plugin_ability()
```

`ToolRegisterTriggerContext` 是 Events CORE 的内部调用上下文。CORE 优先使用其中的 live
`DeepAgent`；公共 `ops.tool.register` 没有该上下文时仍要求有效 `SessionContext`，因此旧的
公共接口和安全边界不变。

## 生命周期与失败语义

- 队列按进入顺序串行 flush；成功后才移除队首。
- CORE、USER / BUILTIN Handler 或扩展绑定失败时立即抛出，失败项及其后续项保留；再次初始化
  从失败项重试，不重复已成功项。
- event 模式没有 bus 时明确失败，不回退到 legacy 直接登记。
- 初始 factory 工具在任何 pending rail 之前 flush；每个 rail 的 `init()` 完成后立即 flush，
  再执行该 rail 的 callback 注册，从而保持 legacy 初始化可见性和 rail priority 顺序。
- 队列只在已有事件循环的异步初始化路径中消费，不使用 `asyncio.run()`、嵌套事件循环或
  `create_task()` 伪造同步初始化。

## JiuWenSwarm 适配

JiuWenSwarm 的 `config_specs` 在同时检测到 `DeepAgentSpec` 的事件字段、内部 Agent 上下文、
DeepAgent 队列和 `ExtensionEventBus.trigger(trigger_context=...)` 能力时，显式选择 `event`，
并只传入一期 core provider allowlist；旧版 agent-core 能力不完整时不传入，自然保持 `legacy`。
真实 Team runtime 通过既有 `Runner` wiring 将 extension bus 放进
`BuildContext.extras["team.extension_bus"]`，`DeepAgentSpec.resolve_parts()` 读取同一句柄，
不新增全局隐式依赖。

因此兼容关系为：旧 Swarm + 新 Core 默认 legacy；新 Swarm + 新 Core 使用 event；新 Swarm
运行在不支持该字段的旧 Core 上自动走 legacy。新 Core 但 event 模式缺少 bus 时按失败语义
暴露 wiring 问题，不静默绕过事件链。

## 验证

- `tests/unit_tests/core/events/test_tool_register_ops.py`：内部上下文无 session 的 CORE 登记，
  以及既有公共 Session 路径回归。
- `tests/unit_tests/harness/test_tool_registration_event_mode.py`：factory FIFO、重复 flush
  幂等、真实 `ExtensionEventBus` 绑定、失败保留与重试、`SysOperationRail` 事件登记。
- JiuwenSwarm `tests/agents/swarm/test_swarm_assembly.py`：各 team/code mode 与 leader/
  teammate 均选择 event，并断言 allowlist 不包含 `swarm.*`；旧 Core 能力缺失时预期为 legacy。

## 已知限制

本特性不改变 `load_plugin_ability` 的重复检测、资源回滚与动态插件语义，也不把 ToolCard
升级为可执行事件来源。热重配由 factory 在 event 阶段暂缓 concrete card 的直接装载，待
事件成功后再绑定；纯 ToolCard 仍按卡片路径装载。跨进程恢复只携带可序列化 Spec；live bus
仍由接收侧 runtime 重新注入，未注入时不应开启 event 模式。
