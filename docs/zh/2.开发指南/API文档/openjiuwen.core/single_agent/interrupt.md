# openjiuwen.core.single_agent.interrupt

中断请求数据结构模块。本模块定义了工具执行中断时使用的数据结构，主要在两个场景下使用：解析Agent返回的中断信息，以及在自定义Rail时创建中断请求。

## class InterruptRequest

中断请求数据结构，定义了中断时需要用户输入的数据格式。

**参数**：

* **message**(str)：提示消息，用于向用户说明需要输入的内容。默认值：`""`。
* **payload_schema**(dict)：用户输入的数据结构定义，遵循JSON Schema规范。默认值：`{}`。
* **auto_confirm_key**(str)：自动确认的键值，通常为工具名称，用于自动确认配置。默认值：`""`。
* **questions**(List[dict], 可选)：结构化问题列表，用于向用户展示多问题模式下的完整问题信息（包含 header、question、options、multi_select、preview 等字段）。默认值：`None`。
* **interrupt_id**(str)：本次中断的唯一标识，由中断处理器在收集暂停的工具调用时分配（`after_tool_call` 中还读不到 id，已能读到 `interrupt_kind` 和 `interrupt_owner`）。同一工具调用上的后一次中断会得到新的 id；只因同一 Rail 的上一个中断未被回答而再次发起的中断沿用原 id。升级前持久化、没有 id 的中断在加载时得到 `intr_<tool_call_id>`。默认值：`""`。
* **interrupt_kind**(str)：中断类型，取值见 [InterruptKind](#class-interruptkind)；供宿主决定如何展示、以什么形状答复。空字符串表示发起中断的 Rail 未声明类型。默认值：`""`。
* **interrupt_owner**(str)：发起中断的 Rail 的键（默认为 Rail 的类路径）。答复只交给该 Rail。默认值：`""`。

## class InterruptKind

中断类型常量（普通字符串，第三方 Rail 可自定义取值）：`PERMISSION="permission"`、`CONFIRM="confirm"`、`ASK_USER="ask_user"`、`SKILL_EVOLUTION_APPROVAL="skill_evolution_approval"`、`TEAM_TOOL_APPROVAL="team_tool_approval"`、`UNDECLARED=""`。

`interrupt_kind_of(request)` 返回请求的类型；升级前持久化的技能进化审批只在 `metadata["interrupt_kind"]` 中带类型，也能读出。

## 回答中断

* `InteractiveInput.answer(interrupt_id, value)`：回答指定中断，只有发起该中断的 Rail 收到答复。`interrupt_id` 为空时报错。
* `InteractiveInput.update(tool_call_id, value)`：兼容旧方式。Agent 恢复时把它换算成对该工具调用当前等待的中断的答复（只换算这一次，此后 Agent 内部只按 `interrupt_id` 认答复）。
* `pending_interrupts(state)`：列出会话中 `ToolInterruptionState` 里暂停的中断，每项为 `PendingInterrupt(interrupt_id, tool_call_id, kind)`（含子 Agent 内的中断）。
* `match_answers(user_input, pending)`：按 Agent 恢复时的同一规则换算一次恢复输入，返回 `MatchedAnswers(answers, unmatched)`：`answers` 为 `{interrupt_id: 答复}`（按 interrupt_id 或 tool_call_id 回答都计入），`unmatched` 为不对应任何暂停中断的键（过期答复）。

## class ToolCallInterruptRequest

带工具调用上下文的中断请求数据结构，继承自[InterruptRequest](#class-interruptrequest)，用于向用户展示中断的详细信息。

**参数**：

* **message**(str)：提示消息，用于向用户说明需要输入的内容。默认值：`""`。
* **payload_schema**(dict)：用户输入的数据结构定义，遵循JSON Schema规范。默认值：`{}`。
* **auto_confirm_key**(str)：自动确认的键值，通常为工具名称。默认值：`""`。
* **questions**(List[dict], 可选)：结构化问题列表（继承自[InterruptRequest](#class-interruptrequest)）。默认值：`None`。
* **tool_name**(str)：触发中断的工具名称。默认值：`""`。
* **tool_call_id**(str)：工具调用的唯一标识符，由LLM生成。默认值：`""`。
* **tool_args**(Any)：工具调用的参数。默认值：`None`。
* **index**(int, 可选)：并发调用时的索引。默认值：`None`。

### classmethod from_tool_call

```python
classmethod from_tool_call(request: InterruptRequest, tool_call: Any) -> ToolCallInterruptRequest
```

从InterruptRequest和ToolCall创建ToolCallInterruptRequest实例。

**参数**：

* **request**([InterruptRequest](#class-interruptrequest))：中断请求数据结构。
* **tool_call**(Any)：工具调用对象，需包含`name`、`id`、`arguments`和`index`属性。

**返回**：

**ToolCallInterruptRequest**，包含工具调用上下文的中断请求数据结构。

**样例**：

当Agent执行过程中触发中断时，返回的结果结构如下：

```python
{
    'result_type': 'interrupt',
    'state': [
        OutputSchema(
            type='__interaction__',
            index=0,
            payload=InteractionOutput(
                id='call_dd26eaa14529440c81b54eab',
                value=ToolCallInterruptRequest(
                    message='Please approve or reject?',
                    payload_schema={
                        'description': 'Payload for user confirmation response.',
                        'properties': {
                            'approved': {'title': 'Approved', 'type': 'boolean'},
                            'feedback': {'default': '', 'title': 'Feedback', 'type': 'string'},
                            'auto_confirm': {'default': False, 'title': 'Auto Confirm', 'type': 'boolean'}
                        },
                        'required': ['approved'],
                        'title': 'ConfirmPayload',
                        'type': 'object'
                    },
                    auto_confirm_key='read',
                    tool_name='read',
                    tool_call_id='call_dd26eaa14529440c81b54eab',
                    tool_args='{"filepath": "/tmp/test1.txt"}',
                    index=0,
                    interrupt_id='intr_5f1c0e8b6b0d4c64a6f3a1d2e9b7c410',
                    interrupt_kind='confirm',
                    interrupt_owner='openjiuwen.harness.rails.interrupt.confirm_rail.ConfirmInterruptRail'
                )
            )
        )
    ],
    'interrupt_ids': ['call_dd26eaa14529440c81b54eab']
}
```

开发者可以通过以下方式获取中断信息：

```python
# 获取ToolCallInterruptRequest对象
interrupt_output = result['state'][0]  # OutputSchema
request = interrupt_output.payload.value  # ToolCallInterruptRequest

# 获取中断详细信息
print(f"工具名称: {request.tool_name}")
print(f"工具调用ID: {request.tool_call_id}")
print(f"工具参数: {request.tool_args}")
print(f"提示消息: {request.message}")
print(f"payload_schema: {request.payload_schema}")
```

> **使用场景**
>
> 本模块的类在以下两个场景中使用：
>
> 1. **解析中断输出**：当Agent执行过程中触发中断时，返回的结果中会包含`ToolCallInterruptRequest`对象，开发者可以从中获取中断的详细信息（如工具名称、工具参数等）。
>
> 2. **自定义Rail**：开发者在实现自定义Rail时，需要创建`InterruptRequest`对象来定义中断请求的数据结构，包括提示消息和用户输入的数据格式。
>
> 完整的使用方法请参考[Interrupt Rail文档](../../openjiuwen.harness/rails/interrupt.md)。
