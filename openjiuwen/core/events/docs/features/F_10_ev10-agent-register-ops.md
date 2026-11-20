# `ops.agent.register` / `agent.register` 特性设计

## 1. 范围、状态与依据

| 项 | 内容 |
| --- | --- |
| 类型、编号 | feature，`F_10 / agent-register-ops` |
| 建档日期 | 2026-11-02 |
| 方案状态 | 已实现；定义、预置登记、真实 `DeepAgent` 绑定和定向回归已验证 |
| 计划条目 | [Events 开发计划 EV-07](../designs/events-development-plan.md#ev-07-业务-eventtype-与-agentruntime-集成) |
| 总体方案 | [F_07 AgentRuntime 与业务事件](F_07_ev07-agent-runtime-business-events.md) |
| 公共契约 | [S_01 Events 公共契约](../specs/S_01_events-contract.md) |
| 关联特性 | [F_11 `ops.tool.register`](F_11_ev11-tool-register-ops.md)、Harness 的 Extension Binder |
| 实现位置 | `builtins/event_types/agent_register.py`、`builtins/handler_ops/agent_register.py`、`harness/deep_agent.py` |

本文维护 `ops.agent.register` 方法及其同名事件的完整契约。F_07 保留 AgentRuntime、嵌套事件和整体边界说明；本文说明开发者如何调用、扩展和验收这项能力。

## 2. 能力、范围与当前状态

`await ops.agent.register(data)` 在当前 Handler 的会话中申请登记一个子 Agent 类型。方法把调用参数规范化为 `AgentRegisterInput`，以同一个 `SessionContext` 触发一次新的 `agent.register` 事件链；USER / BUILTIN Handler 可以检查、改写或短路，CORE 将定义绑定到当前会话已登记的真实 `DeepAgent`。成功返回 `AgentRegisterValue`，其中 `agent` 与 `agent_card.name` 相同。

当前 CORE 只把 `AgentCard + prompt` 转换为 `SubAgentConfig`，通过 `DeepAgent.load_subagent_config()` 复用既有 Extension Binder、`DeepAgentConfig.subagents` 和 `AbilityManager`。它不创建模型调用、不启动子 Agent 实例，也不改变父 Agent 身份。声明了当前 `SubAgentConfig` 无法表达的可选字段时，CORE 返回 `deny`，Ops 将其转换为 `RuntimeError`；不会静默丢弃字段。

这项能力是 Events 和 Harness 的进程内能力。当前没有 JiuwenSwarm 业务入口、生产授权宿主或单独的卸载 Ops；真实业务调用方仍需在后续接入时负责来源、权限和生命周期。

## 3. 事件与方法契约

### 3.1 调用转换

```text
AgentRegisterInput
  → ops.agent.register(data)
  → prepare_agent_register_input(data)
  → Event("agent.register", prepared), 当前 SessionContext
  → USER → BUILTIN → CORE
  → AgentRegisterOutput
  → 校验并解包 value
  → AgentRegisterValue
```

方法首参数由 Builder 注入，调用者只传一个 `AgentRegisterInput`：

```python
from openjiuwen.core.events.builtins.event_types.agent_register import AgentRegisterInput
from openjiuwen.core.single_agent.schema.agent_card import AgentCard

registered = await ops.agent.register(
    AgentRegisterInput(
        agent_card=AgentCard(
            id="reviewer",
            name="reviewer",
            description="检查代码并说明依据",
        ),
        prompt="检查代码，列出结论和证据。",
    )
)
assert registered.agent == "reviewer"
```

`ops.agent.register()` 只推进新建的 `agent.register` 事件链，不会自动推进当前 Handler 正在处理的事件；当前 Handler 仍须自行决定是否调用自己的 `event_next(event)`。

### 3.2 输入字段

| 字段 | 类型、默认值与含义 | 当前行为 |
| --- | --- | --- |
| `agent_card` | `AgentCard`，必填；`id` 和 `name` 必须是非空字符串。卡片包含 `description`、`input_params`、`output_params`、`interface_url` 等既有字段。 | CORE 使用深复制后的卡片构造 `SubAgentConfig`，不会直接保存调用方传入的卡片对象。 |
| `prompt` | `str`，必填且非空。 | 作为 `SubAgentConfig.system_prompt`。 |
| `tools`、`disallowed_tools`、`skills` | `tuple[str, ...]` 或 `None`。 | 非空时拒绝；空元组可作为无操作声明。 |
| `model`、`permission_mode`、`initial_prompt` | `str` 或 `None`。 | 非 `None` 时拒绝。 |
| `effort` | `str 或 int` 或 `None`；布尔值不接受。 | 非 `None` 时拒绝。 |
| `mcp_servers` | `tuple[str 或 Mapping, ...]` 或 `None`。 | 非空时拒绝；空元组可作为无操作声明。 |
| `hooks` | `Mapping[str, object]` 或 `None`。 | 非空时拒绝；空映射可作为无操作声明。 |
| `max_turns`、`auto_compact_window` | 正整数或 `None`。 | 非 `None` 时拒绝。 |
| `memory` | `"user"`、`"project"`、`"local"` 或 `None`。 | 非 `None` 时拒绝。 |
| `background`、`omit_claude_md` | `True` 或 `None`。 | `True` 仍属于当前运行时不支持的声明，会被拒绝。 |
| `isolation` | `"worktree"`、`"remote"` 或 `None`。 | 非 `None` 时拒绝。 |

输入对象是冻结 dataclass，但 `AgentCard` 本身是可变的 Pydantic 模型；方法和 CORE 各自执行校验与快照。未知字段不属于 `AgentRegisterInput`，不能借此绕过当前运行时的支持检查。

### 3.3 输出、匹配与失败

事件常量为 `AGENT_REGISTER = "agent.register"`。事件输入 Validator 要求 `AgentRegisterInput`；Matcher 只接受空模式 `{}`，并要求输入类型正确。触发时先固定匹配集合，Handler 改写输入不会重新匹配。

`AgentRegisterOutput` 必须且只能包含一个分支：

| 分支 | 含义 | Ops 行为 |
| --- | --- | --- |
| `value=AgentRegisterValue` | CORE 或下游 Handler 表示登记成功。`agent` 必须等于 `agent_card.name`。 | 返回 `AgentRegisterValue`。 |
| `deny=str` | 业务拒绝，例如当前 `DeepAgent` 不支持声明字段。 | 抛出 `RuntimeError(deny)`。 |

没有有效 `SessionContext`、会话未绑定或已释放、绑定对象不是 `DeepAgent`、输入非法、重复子 Agent 名称或 Binder 失败时，调用失败。取消沿事件链传播，不转换为成功值；框架不会为注册副作用自动重试或回滚已经成功的外层业务操作。

## 4. Handler 用法与登记

普通 Handler 可以继续、改写或短路。改写必须创建同一事件类型的新输入；短路必须返回合法的 `AgentRegisterOutput`，短路后 CORE 不执行。

```python
from dataclasses import replace

from openjiuwen.core.events import Event, EventHandlerOps, EventNext, HandlerLevel
from openjiuwen.core.events.builtins.event_types.agent_register import (
    AGENT_REGISTER,
    AgentRegisterOutput,
    AgentRegisterValue,
)


async def add_evidence_rule(
    event: Event,
    ops: EventHandlerOps | None,
    event_next: EventNext,
) -> object:
    del ops
    data = event.event_input
    if data.agent_card.name == "reviewer":
        data = replace(data, prompt=data.prompt + "\n请列出检查依据。")
    return await event_next(Event(event.event_type, data))


async def reject_unsafe_agent(
    event: Event,
    ops: EventHandlerOps | None,
    event_next: EventNext,
) -> AgentRegisterOutput:
    del ops, event_next
    del event
    return AgentRegisterOutput(deny="registration is not allowed for this request")


bus.register_event_handler(AGENT_REGISTER, add_evidence_rule, {}, HandlerLevel.USER)
```

上例中的短路函数只展示返回形状；生产代码应在允许短路前完成自己的授权判断。CORE 不调用 `ops.agent.register` 或其它会再次触发 `agent.register` 的公开路径，以避免递归。

新建 `ExtensionEventBus` 时，bootstrap 会扫描 `builtins/event_types` 和 `builtins/handler_ops` 并完成预置登记；宿主仍需显式 Build 一次：

```python
from openjiuwen.core.events import Event, ExtensionEventBus, OPS_BUILD_EVENT_TYPE

bus = ExtensionEventBus()
await bus.trigger(Event(OPS_BUILD_EVENT_TYPE, None))
```

重复触发 Build 会按公共契约失败；使用已有 Registry 初始化流程时不要再次手工 Build。

## 5. 实现、上下文与生命周期

| 层 | 实现与责任 |
| --- | --- |
| EventType / CORE | `builtins/event_types/agent_register.py` 定义输入、输出、Validator、Matcher 和 `core_handler`。CORE 从 `ops.session_context.session_id` 查询 `get_agent_runtime()`，只接受绑定的 `DeepAgent`。 |
| Ops 方法 | `builtins/handler_ops/agent_register.py` 的 `_AgentRegistrationOps.register(ops, data)` 规范化输入，触发同名事件，校验输出并解包；`build()` 把方法登记为 `agent.register`。 |
| Harness 适配 | `DeepAgent.load_subagent_config()` 调用 `_apply_extension_parts(ExtensionParts(subagents=[config]))`，复用 Binder 的重复检查、子 Agent 能力卡和 `SubagentRail` 准备。 |
| 上下文 | `SessionContext.session_id` 是查找运行实例的唯一键；AgentRuntime 是进程级弱引用表，不承担授权或跨进程身份认证。 |

成功绑定后，子 Agent 配置进入 `DeepAgent.deep_config.subagents`，其 `AgentCard` 进入当前 `AbilityManager`；存在子 Agent 时 Binder 会确保 `SubagentRail` 可用。`load_subagent_config()` 还会在 DeepAgent 的 `_load_records` 中记录本次资源，但当前 Ops 返回值不包含 `LoadRecord`，也没有对应的 `ops.agent.unload` 方法。宿主若需要撤销动态注册，必须另行持有并调用受支持的 Harness 生命周期接口，不能把返回的 `AgentRegisterValue` 当作卸载句柄。

本特性不接入真实业务入口。页面、渠道、Swarm 或插件何时调用该方法，以及注册结果由谁消费，属于后续业务接线；隔离 Bus 测试只证明定义、登记和能力调用。

## 6. 验收与验证记录

| 验证场景 | 输入与操作 | 预期结果 | 证明方式 |
| --- | --- | --- | --- |
| 正常登记 | 已绑定并配置的 `DeepAgent`，调用 `ops.agent.register()`。 | 返回 `AgentRegisterValue`；`SubAgentConfig`、AgentCard 和 AbilityManager 状态出现一次。 | `test_agent_register_uses_real_deep_agent_binding`。 |
| USER 短路 | 在 `agent.register` 前登记 USER Handler，直接返回合法 Output。 | CORE 不执行，DeepAgent 不新增子 Agent。 | `test_agent_register_short_circuit_does_not_change_real_agent`。 |
| 不支持字段 | 传入非空 `tools` 等字段。 | CORE 返回 `deny`，Ops 抛出拒绝错误或被容错链按公共规则处理；无绑定副作用。 | `test_agent_register_rejects_unsupported_fields_without_side_effect`。 |
| 会话缺失 | 直接触发事件或调用 Ops 时没有有效 SessionContext / Runtime 绑定。 | 失败并明确说明会话或绑定缺失。 | `test_agent_register_core_requires_the_bound_runtime_agent`。 |
| 输入和输出边界 | 非法 Matcher、非法嵌套 Output。 | 按 EventBus Validator / 类型契约拒绝。 | `test_registration_matchers_ignore_nonempty_patterns_without_raising`、`test_registration_outputs_reject_invalid_nested_values`。 |

2026-11-02 在 Windows / Python 3.11.9 目标 worktree 执行：

```text
uv run pytest tests/unit_tests/core/events/test_registration_events_rework.py -q
```

结果为 **14 passed**；出现 2 条既有 Pydantic 弃用提示。该命令覆盖 Agent 与 Tool 两项注册能力；没有执行真实业务入口、跨进程或生产授权验证。

## 7. 已知限制与参考资料

- 当前实现只支持把 `AgentCard + prompt` 绑定为 `SubAgentConfig`；可选声明字段的类型检查存在，但非空字段会明确拒绝。
- Runtime 只接受绑定的 `DeepAgent`，不自动适配任意 `BaseAgent`、TeamAgent 或远端 Agent。
- 注册成功后的动态资源没有专门的 Ops 卸载能力；生产宿主需要补齐授权、生命周期排空、幂等、冲突和撤销策略。
- 本文的测试证明“定义、登记与绑定通过验证”，不代表 JiuwenSwarm 业务入口已经接入。

参考：[F_07 总体方案](F_07_ev07-agent-runtime-business-events.md)、[S_01 公共契约](../specs/S_01_events-contract.md)、[Events 模块规则](../../AGENTS.md)、[DeepAgent Extension Binder](../../../../harness/extension_binder.py)。
