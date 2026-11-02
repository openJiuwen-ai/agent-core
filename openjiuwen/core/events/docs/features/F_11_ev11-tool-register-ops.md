# `ops.tool.register` / `tool.register` 特性设计

## 1. 范围、状态与依据

| 项目 | 内容 |
| --- | --- |
| 方案状态 | 已实现；可执行工具登记、预置登记、事件链和输入边界已验证 |
| 适用范围 | agent-core `rsi-native-agent-os-rework` 的 Events 模块 |
| 关联特性 | [F_10 `ops.agent.register`](F_10_ev10-agent-register-ops.md)、[F_07 EV-07 总体方案](F_07_ev07-agent-runtime-business-events.md) |
| 实现位置 | `builtins/event_types/tool_register.py`、`builtins/handler_ops/tool_register.py` |

本文维护 `ops.tool.register` 方法及其同名事件的可执行工具登记契约。当前能力支持已有 `Tool`、`RestfulApiCard` 和 Python callable；其中 `LocalFunction`、`RestfulApi` 以及未来的 `Tool` 子类都通过同一个来源字段进入事件链。Ops 只传递来源，CORE 在确认当前 `DeepAgent` 后创建需要的运行时对象并通过现有扩展绑定路径完成登记。`tool.call`、工具搜索和独立卸载不在本特性范围内。

## 2. 能力与边界

`await ops.tool.register(spec)` 在当前会话登记一份可执行工具。Ops 把 `ToolRegisterSpec` 转换为 `ToolRegisterInput(source=..., card=...)`，不创建运行时 `Tool`，并以当前 Handler 的 `SessionContext` 触发一次新的 `tool.register` 事件链；USER / BUILTIN Handler 可以审查、替换或短路，CORE 确认绑定的 `DeepAgent` 后再创建并登记工具，最后返回 `ToolRegisterValue`。

事件在当前进程内携带来源对象，不使用 `ContextVar` 保存本次注册状态。已有 `LocalFunction` / `RestfulApi` 或其它 `Tool` 子类直接复用；`RestfulApiCard` 由 CORE 构造 `RestfulApi`；Python callable 由 CORE 使用可选的 `ToolCard` 包装为 `LocalFunction`，未提供卡片时由 callable 的名称、文档字符串和签名自动生成卡片。通用 `ToolCard` 单独出现时会被拒绝，因为它没有执行实现。工具卡片负责保存 input schema 和后续可扩展的元数据。CORE 通过 `DeepAgent.load_plugin_ability()` 复用 `extension_binder`，由它维护 `AbilityManager`、`Runner.resource_mgr` 和配置登记。

```text
ToolRegisterSpec
  → ops.tool.register(spec)
  → source: Tool / RestfulApiCard / callable
  → 构造 ToolRegisterInput(source=source, card=card)，不创建 Tool
  → Event("tool.register", prepared), 当前 SessionContext
  → USER → BUILTIN → CORE
  → CORE: RestfulApiCard → RestfulApi；callable → LocalFunction；Tool → 原实例
  → DeepAgent.load_plugin_ability()
  → ToolRegisterOutput(value=ToolRegisterValue(tool=name)) / deny
```

## 3. 方法与事件契约

### 3.1 方法签名与字段

签名：

```python
await ops.tool.register(spec: ToolRegisterSpec) -> ToolRegisterValue
```

方法首参数由 Builder 注入；调用者只传一个 `ToolRegisterSpec`。Ops 只组装 `ToolRegisterInput` 并触发事件，事件 Validator 和 CORE 负责校验来源及卡片元数据。Handler 改写必须使用同一事件类型的新输入对象，并通过 `source` / `card` 明确保留或替换来源。

| 字段 | 类型、默认值与含义 | 校验与生效 |
| --- | --- | --- |
| `source` | `Tool | RestfulApiCard | Callable[..., Any]`，必填。 | 已有 `Tool` 在 CORE 原样复用；`RestfulApiCard` 只作为声明进入事件，并由 CORE 转换为 `RestfulApi`；callable 由 CORE 转换为 `LocalFunction`。未来 `Tool` 子类无需新增字段。 |
| `card` | `ToolCard | None`，默认 `None`。 | 仅用于 callable 的显式卡片；也可省略并由 callable 自动推导。若 `source` 已是 `Tool` 或 `RestfulApiCard`，则必须省略或传入同一个卡片对象。 |

`ToolRegisterInput.source` 是本次进程内的来源对象，不属于可序列化的数据；EventBus 不跨进程传输它。`ToolRegisterInput.card` 和 `RestfulApiCard.input_params` 保存工具输入 Schema；如果是 Mapping，入口会校验其为对象型 JSON Schema，并拒绝嵌入 callable 等运行时对象。未来的卡片字段随 `ToolCard` 子类扩展，不需要修改事件输入字段。

### 3.2 输出、匹配与失败

事件常量为 `TOOL_REGISTER = "tool.register"`。Matcher 只接受空模式 `{}` 和正确的 `ToolRegisterInput`。`ToolRegisterOutput` 必须且只能包含一个分支：

| 分支 | 含义 | Ops 行为 |
| --- | --- | --- |
| `value=ToolRegisterValue(tool=str)` | CORE 已将工具绑定到当前 `DeepAgent` 的能力和资源登记。 | 返回 `ToolRegisterValue`；工具可从当前 Agent 的 `AbilityManager` 获取。 |
| `deny=str` | Handler 拒绝本次声明。 | 抛出 `RuntimeError(deny)`。 |

没有有效 `SessionContext`、来源类型非法、通用 `ToolCard` 缺少执行实现、名称或 Schema 非法、Runtime 未绑定 `DeepAgent` 或扩展绑定失败时，调用失败。取消沿事件链传播；EventBus 不自动重试。

## 4. Handler 与登记

普通 Handler 可以审查、替换或短路。改写使用同一事件类型的新 `ToolRegisterInput`，并通过 `source` / `card` 明确保留或替换来源；短路必须返回合法的 `ToolRegisterOutput`，短路后 CORE 不执行。

```python
from openjiuwen.core.events import Event, EventHandlerOps, EventNext, HandlerLevel
from openjiuwen.core.events.builtins.event_types.tool_register import TOOL_REGISTER


async def normalize_description(
    event: Event,
    ops: EventHandlerOps | None,
    event_next: EventNext,
) -> object:
    del ops
    data = event.event_input
    # Handler 可以基于 data.source / data.card 做审查；替换时传入完整来源描述。
    return await event_next(Event(event.event_type, data))


bus.register_event_handler(TOOL_REGISTER, normalize_description, {}, HandlerLevel.USER)
```

新建 `ExtensionEventBus` 时，bootstrap 会扫描 `builtins/event_types` 和 `builtins/handler_ops` 并完成预置登记；宿主完成全部注册后显式 Build 一次：

```python
from openjiuwen.core.events import Event, ExtensionEventBus, OPS_BUILD_EVENT_TYPE

bus = ExtensionEventBus()
await bus.trigger(Event(OPS_BUILD_EVENT_TYPE, None))
```

`ops.tool.register()` 开启新事件链，不会代替当前 Handler 调用自己的 `event_next(event)`。USER / BUILTIN 短路时 CORE 不会调用 `load_plugin_ability()`，因此不会产生工具登记副作用。

## 5. 实现与责任

| 层 | 实现与责任 |
| --- | --- |
| EventType / CORE | `builtins/event_types/tool_register.py` 定义 `ToolRegisterSpec/Input/Value/Output`、来源校验、卡片 JSON Schema 校验、Matcher、输入/输出 Validator 和 CORE。CORE 通过 `AgentRuntime` 的 session ID 确认绑定 `DeepAgent`；已有 `Tool` 原样复用，callable 转为 `LocalFunction`，`RestfulApiCard` 在此处创建 `RestfulApi`，再调用 `load_plugin_ability()`。 |
| Ops 方法 | `builtins/handler_ops/tool_register.py` 的 `_ToolRegistrationOps.register(ops, spec)` 只构造 `ToolRegisterInput(source=..., card=...)`，不创建运行时 `Tool`，触发同名事件，校验输出并解包；`build()` 把方法登记为 `tool.register`。 |
| Harness 绑定层 | `DeepAgent.load_plugin_ability()` 复用 `extension_binder.apply_extension_hot()`，负责能力卡、具体资源、`DeepAgentConfig.tools` 和失败回滚；本特性不另建工具注册表。 |

`ToolRegisterValue.tool` 是已绑定工具的逻辑名称，不是 Tool 实例、资源句柄或卸载凭据。工具的 `ToolCard` / 专用卡片保存名称、描述、input schema、exposure 及后续扩展字段；事件不再复制这些字段，也不在 CORE 中重新拼装已有 `Tool` 的元数据。

## 6. 验收与验证记录

| 验证场景 | 输入与操作 | 预期结果 | 证明方式 |
| --- | --- | --- | --- |
| callable 登记 | 已绑定 `DeepAgent`，传入 callable 和可选 `ToolCard` 并调用 `ops.tool.register()`。 | USER / BUILTIN 看到 callable，CORE 构造 `LocalFunction`，工具进入 `AbilityManager` / `Runner.resource_mgr` 且可执行。 | `test_tool_register_binds_and_executes_a_real_local_function`。 |
| 已有 Tool 登记 | 传入 `LocalFunction`、`RestfulApi` 或其它 `Tool` 子类。 | USER / BUILTIN 看到原 Tool，CORE 复用同一实例并按其卡片 Schema 登记。 | `test_tool_register_accepts_existing_tool_with_default_schema`、`test_tool_register_reuses_existing_restful_api`。 |
| RestfulApiCard 登记 | 传入 `RestfulApiCard`。 | USER / BUILTIN 看到原 card，CORE 构造 `RestfulApi` 并复用同一卡片完成登记。 | `test_tool_register_materializes_restful_api_card`。 |
| callable 自动卡片 | 只传入命名 callable，不提供 `card`。 | USER / BUILTIN 看到 callable，CORE 根据名称、文档字符串和签名生成 `LocalFunction` 卡片。 | `test_tool_register_normalizes_default_schema_in_event`。 |
| 未绑定 Agent | 直接触发事件且 Session 未绑定 Runtime Agent。 | CORE 明确失败，不静默成功。 | `test_tool_register_event_requires_the_bound_runtime_agent`。 |
| USER 短路 | 在 `tool.register` 前返回合法 Output。 | CORE 不执行。 | `test_tool_register_short_circuit_skips_core`。 |
| 非法 Schema | 顶层 Schema 不是 object。 | 在 CORE 前失败。 | `test_tool_register_rejects_invalid_schema_before_core`。 |
| 实现边界 | 只传通用 `ToolCard`，或卡片 Schema 内放 callable。 | 通用卡片因缺少执行实现被拒绝；Schema 内 callable 在事件入口拒绝。 | `test_tool_register_requires_a_real_implementation`、`test_tool_register_event_rejects_callable_nested_in_schema`。 |

2026-11-02 在 Windows / Python 3.11.9 目标 worktree 执行：

```text
uv run --frozen pytest tests/unit_tests/core/events/test_registration_events_rework.py -q
```

结果为 **17 passed**；出现 2 条既有 Pydantic 弃用提示。测试证明 callable、已有 `LocalFunction`、已有 `RestfulApi`、`RestfulApiCard` 的真实登记、短路、Agent 绑定和输入边界；没有证明跨进程传输、`tool.call` 或独立卸载接口。

## 7. 已知限制与参考资料

- `tool.register` 是当前进程内事件；`Tool` / callable 不应跨进程或通过序列化协议传递。
- `tool.call`、工具搜索和独立卸载仍未实现；返回成功不提供独立卸载凭据。
- 具体权限策略、跨进程实现、退役排空和真实业务入口接入不在本特性测试范围内。

参考：[F_07 总体方案](F_07_ev07-agent-runtime-business-events.md)、[F_10 Agent 注册](F_10_ev10-agent-register-ops.md)、[S_01 公共契约](../specs/S_01_events-contract.md)、[Events 模块规则](../../AGENTS.md)。
