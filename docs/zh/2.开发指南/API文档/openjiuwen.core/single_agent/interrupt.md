# openjiuwen.core.single_agent.interrupt

中断请求数据结构模块。本模块定义了工具执行中断时使用的数据结构，主要在两个场景下使用：解析Agent返回的中断信息，以及在自定义Rail时创建中断请求。

## class InterruptRequest

中断请求数据结构，定义了中断时需要用户输入的数据格式。

**参数**：

* **message**(str)：提示消息，用于向用户说明需要输入的内容。默认值：`""`。
* **payload_schema**(dict)：用户输入的数据结构定义，遵循JSON Schema规范。默认值：`{}`。
* **auto_confirm_key**(str)：自动确认的键值，通常为工具名称，用于自动确认配置。默认值：`""`。

## class ToolCallInterruptRequest

带工具调用上下文的中断请求数据结构，继承自[InterruptRequest](#class-interruptrequest)，用于向用户展示中断的详细信息。

**参数**：

* **message**(str)：提示消息，用于向用户说明需要输入的内容。默认值：`""`。
* **payload_schema**(dict)：用户输入的数据结构定义，遵循JSON Schema规范。默认值：`{}`。
* **auto_confirm_key**(str)：自动确认的键值，通常为工具名称。默认值：`""`。
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
                    index=0
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
