# openjiuwen.core.context_engine.base

## class openjiuwen.core.context_engine.base.ModelContext
```python
class openjiuwen.core.context_engine.base.ModelContext()
```

`ModelContext`模型上下文，定义消息增删、窗口构建、统计等接口。支持 BaseMessage 类型的内容格式。

### abstract add_message
```python
abstract add_message(message: BaseMessage | List[BaseMessage]) -> List[BaseMessage]
```
向模型上下文中添加*一条*或*多条*消息。

**参数**：

- **message**([BaseMessage](../../openjiuwen.core/foundation/llm/llm.md#class-openjiuwencorefoundationllmschemamessagebasemessage)| List[BaseMessage])：要添加的单个或多条消息对象，必须是BaseMessage或者BaseMessage组成的列表类型。

**返回**：

**List[[BaseMessage](../../openjiuwen.core/foundation/llm/llm.md#class-openjiuwencorefoundationllmschemamessagebasemessage)]**，追加后的消息列表（Processor 处理后的结果）。

**样例**：

```python
>>> import asyncio
>>> from openjiuwen.core.context_engine import ContextEngine, ContextEngineConfig
>>> from openjiuwen.core.foundation.llm import UserMessage
>>>
>>> async def main():
...     engine = ContextEngine(ContextEngineConfig())
...     ctx = await engine.create_context(session=None, history_messages=[])
...     added = await ctx.add_messages(UserMessage(content="你好"))
...     return len(added), ctx.get_messages()[0].content
>>>
>>> asyncio.run(main())
(1, '你好')
```

### abstract get_messages
```python
abstract get_messages(size: Optional[int] = None, with_history: bool = True) -> List[BaseMessage]
```
从对话上下文中获取消息（不执行移除操作）。

**参数**：

- **size**(int, 可选)：待获取的消息数量。默认值：`None`，表示获取所有消息。
- **with_history**(bool, 可选)：是否包含历史。若为`True`，返回包含历史记录的消息；若为`False`，仅返回最新的消息。默认值：`True`。

**返回**：

**List[[BaseMessage](../../openjiuwen.core/foundation/llm/llm.md#class-openjiuwencorefoundationllmschemamessagebasemessage)]**，按原始顺序返回的检索到的消息，最近的消息在最后。

**样例**：

```python
>>> import asyncio
>>> from openjiuwen.core.context_engine import ContextEngine, ContextEngineConfig
>>> from openjiuwen.core.foundation.llm import UserMessage, AssistantMessage
>>>
>>> async def main():
...     engine = ContextEngine(ContextEngineConfig())
...     ctx = await engine.create_context(session=None, history_messages=[])
...     await ctx.add_messages([UserMessage(content="hi"), AssistantMessage(content="hello")])
...     msgs = ctx.get_messages(size=1, with_history=True)
...     return len(msgs)
>>>
>>> asyncio.run(main())
1
```

### abstract set_message
```python
abstract set_message(messages: List[BaseMessage], with_history: bool = True)
```
将当前消息列表替换为传入的消息列表。

**参数**：

- **messages**(List[[BaseMessage](../../openjiuwen.core/foundation/llm/llm.md#class-openjiuwencorefoundationllmschemamessagebasemessage)])：要置入模型上下文的新消息序列。
- **with_history**(bool, 可选)：替换范围。为`True`表示替换拼接后的消息集（由「上下文消息 + 历史消息」组成）；为`False`表示仅替换历史消息，保留上下文消息不变。默认值：`True`。

**样例**：

```python
>>> import asyncio
>>> from openjiuwen.core.context_engine import ContextEngine, ContextEngineConfig
>>> from openjiuwen.core.foundation.llm import UserMessage
>>>
>>> async def main():
...     engine = ContextEngine(ContextEngineConfig())
...     ctx = await engine.create_context(session=None, history_messages=[])
...     await ctx.add_messages([UserMessage(content="old")])
...     ctx.set_messages([UserMessage(content="new")], with_history=True)
...     return ctx.get_messages()[0].content
>>>
>>> asyncio.run(main())
'new'

### abstract pop_message
```python
abstract pop_message(size: int = 1, with_history: bool = True) -> List[BaseMessage]
```
移除并返回当前请求消息列表中最新的`size`条消息。

**参数**：

- **size**(int, 可选)：要弹出的消息数量。默认值：`1`。
- **with_history**(bool, 可选)：是否同步更新历史。为`True`表示会同时从底层持久化历史中移除对应消息；为`False`表示仅对当前请求的消息列表产生影响。默认值：`True`。

**返回**：

**List[[BaseMessage](../../openjiuwen.core/foundation/llm/llm.md#class-openjiuwencorefoundationllmschemamessagebasemessage)]**：被移除的消息。

**样例**：

```python
>>> import asyncio
>>> from openjiuwen.core.context_engine import ContextEngine, ContextEngineConfig
>>> from openjiuwen.core.foundation.llm import UserMessage
>>>
>>> async def main():
...     engine = ContextEngine(ContextEngineConfig())
...     ctx = await engine.create_context(session=None, history_messages=[])
...     await ctx.add_messages([UserMessage(content="a"), UserMessage(content="b")])
...     popped = ctx.pop_messages(size=1, with_history=True)
...     return len(popped), popped[0].content, len(ctx)
>>>
>>> asyncio.run(main())
(1, 'b', 1)
```

### abstract clear_message
```python
abstract clear_message(with_history: bool = True)
```
移除当前对话轮次中添加的所有消息。

**参数**：

- **with_history**(bool, 可选)：是否清空历史。为`True`表示会同时清空底层的持久化历史消息，即从内存加载或上下文创建时传入的初始消息；为`False`表示仅丢弃本次请求过程中累积的消息，持久化历史消息保持不变。默认值：`True`。

**样例**：

```python
>>> import asyncio
>>> from openjiuwen.core.context_engine import ContextEngine, ContextEngineConfig
>>> from openjiuwen.core.foundation.llm import UserMessage
>>>
>>> async def main():
...     engine = ContextEngine(ContextEngineConfig())
...     ctx = await engine.create_context(session=None, history_messages=[])
...     await ctx.add_messages([UserMessage(content="x")])
...     ctx.clear_messages(with_history=True)
...     return len(ctx)
>>>
>>> asyncio.run(main())
0
```

### abstract get_context_window
```python
abstract get_context_window(
    system_messages: List[BaseMessage] = None,
    tools: List[ToolInfo] = None,
    window_size: Optional[int] = None,
    dialogue_round: Optional[int] = None,
    **kwargs
) -> "ContextWindow"
```
构建并获取适用于模型推理的上下文窗口。

**参数**：

- **system_messages**(List[[BaseMessage](../../openjiuwen.core/foundation/llm/llm.md#class-openjiuwencorefoundationllmschemamessagebasemessage)], 可选)：要前置到消息窗口的系统级消息。
- **tools**(List[[ToolInfo](../../openjiuwen.core/foundation/tool/tool.md)], 可选)：要纳入消息窗口的工具定义信息。
- **window_size**(int, 可选)：要包含的历史消息最大数量；若省略，默认包含全部历史消息。
- **dialogue_round**(int, 可选)：要保留的最新对话轮次数量。一个轮次的定义为：从用户消息开始，至下一条不含工具调用的助手消息结束（即工具调用轮次中的最终回复）。未完成轮次（仅有用户消息、无后续助手消息）仍计为一个轮次。当该参数与`window_size`同时指定时，本参数优先级更高。若显式设置，值必须大于 0；默认值`None`表示禁用基于轮次的消息截断逻辑。
- **kwargs**(dict, 可选)：额外的、与上下文相关的参数。

**返回**：

**ContextWindow**，包含构建完成的消息列表及元数据的上下文窗口。

**样例**：

```python
>>> import asyncio
>>> from openjiuwen.core.context_engine import ContextEngine, ContextEngineConfig
>>> from openjiuwen.core.foundation.llm import UserMessage, AssistantMessage, SystemMessage
>>>
>>> async def main():
...     engine = ContextEngine(ContextEngineConfig(default_window_message_num=10))
...     ctx = await engine.create_context(session=None, history_messages=[])
...     await ctx.add_messages([UserMessage(content="hi"), AssistantMessage(content="hello")])
...     window = await ctx.get_context_window(
...         system_messages=[SystemMessage(content="你是指南")],
...         window_size=5,
...     )
...     return len(window.get_messages())
>>>
>>> asyncio.run(main())
3
```

### abstract statistic
```python
abstract statistic() -> "ContextStats"
```
获取计算上下文级别的统计信息。

**返回**：

**ContextStats**，上下文的消息与token数量聚合统计结果。

**样例**：

```python
>>> import asyncio
>>> from openjiuwen.core.context_engine import ContextEngine, ContextEngineConfig
>>> from openjiuwen.core.foundation.llm import UserMessage
>>>
>>> async def main():
...     engine = ContextEngine(ContextEngineConfig())
...     ctx = await engine.create_context(session=None, history_messages=[])
...     await ctx.add_messages([UserMessage(content="hi")])
...     stats = ctx.statistic()
...     return stats.total_messages
>>>
>>> asyncio.run(main())
1
```

### abstract session_id
```python
abstract session_id() -> str
```
获取当前用户会话的全局唯一标识。

**返回**：

**str**，当前用户会话的全局唯一标识。

**样例**：

```python
>>> import asyncio
>>> from openjiuwen.core.context_engine import ContextEngine, ContextEngineConfig
>>>
>>> async def main():
...     engine = ContextEngine(ContextEngineConfig())
...     ctx = await engine.create_context(session=None, history_messages=[])
...     return ctx.session_id()
>>>
>>> asyncio.run(main())
'default_session_id'
```

### abstract context_id
```python
abstract context_id() -> str
```
获取会话内当前上下文的全局唯一标识。

**返回**：

**str**，会话内当前上下文的全局唯一标识。

**样例**：

```python
>>> import asyncio
>>> from openjiuwen.core.context_engine import ContextEngine, ContextEngineConfig
>>>
>>> async def main():
...     engine = ContextEngine(ContextEngineConfig())
...     ctx = await engine.create_context(context_id="my_ctx", session=None, history_messages=[])
...     return ctx.context_id()
>>>
>>> asyncio.run(main())
'my_ctx'
```

### abstract token_counter
```python
abstract token_counter() -> TokenCounter
```
获取token计数器。

**返回**：

**TokenCounter**，token计数器实例。

**样例**：

```python
>>> import asyncio
>>> from openjiuwen.core.context_engine import ContextEngine, ContextEngineConfig, TiktokenCounter
>>>
>>> async def main():
...     engine = ContextEngine(ContextEngineConfig())
...     ctx = await engine.create_context(
...         session=None,
...         history_messages=[],
...         token_counter=TiktokenCounter(),
...     )
...     counter = ctx.token_counter()
...     return counter is not None
>>>
>>> asyncio.run(main())
True
```

### abstract reloader_tool
```python
abstract reloader_tool() -> Tool
```
获取上下文原始消息重载工具。

**返回**：

**Tool**，返回上下文原文重载工具实例。当配置项enable_reload打开时，可以将工具注册到agent中，帮助agent在需要获取详细原文信息时，重载被上下文处理器处理后的消息原文。
`offload_handle`：指向卸载内容的通用唯一标识码（UUID）或文件路径。
`offload_type`：存储后端类型（当前支持："in_memory" 内存存储）。

**样例**：

```python
>>> import asyncio
>>> from openjiuwen.core.context_engine import ContextEngine, ContextEngineConfig
>>>
>>> async def main():
...     engine = ContextEngine(ContextEngineConfig())
...     ctx = await engine.create_context(session=None, history_messages=[])
...     tool = ctx.reloader_tool()
...     # 工具名称通过 `tool.card.name` 获取。
...     return tool.card.name
>>>
>>> asyncio.run(main())
'reload_original_context_messages'
```

## class openjiuwen.core.context_engine.base.ContextStats
```python
class openjiuwen.core.context_engine.base.ContextStats()
```

`ContextStats`表示任意上下文或上下文窗口的总消息数量，总token使用情况以及各类消息的数量和token使用情况（适用于模型上下文 / 上下文窗口）。

**参数**：

- **total_messages**(int)：系统 / 用户 / 助手 / 工具类消息的总数。默认值：`0`。
- **total_tokens**(int)：系统 / 用户 / 助手 / 工具类token的总数。默认值：`0`。

- **system_messages**(int)：系统类消息的数量。默认值：`0`。
- **user_messages**(int)：用户类消息的数量。默认值：`0`。
- **assistant_messages**(int)：助手类消息的数量。默认值：`0`。
- **tool_messages**(int)：工具类消息的数量。默认值：`0`。
- **tools**(int)：注入至提示词中的工具信息对象数量。默认值：`0`。

- **system_message_tokens**(int)：系统类消息token的数量。默认值：`0`。
- **user_message_tokens**(int)：用户类消息token的数量。默认值：`0`。
- **assistant_message_tokens**(int)：助手类消息token的数量。默认值：`0`。
- **tool_message_tokens**(int)：工具类消息token的数量。默认值：`0`。
- **tool_tokens**(int)：注入至提示词中的工具信息对象token的数量。默认值：`0`。

**样例**：

```python
>>> from openjiuwen.core.context_engine import ContextStats
>>>
>>> stats = ContextStats(
...     total_messages=3,
...     total_tokens=100,
...     user_messages=1,
...     assistant_messages=1,
... )
>>> stats.total_tokens
100
```

## class openjiuwen.core.context_engine.base.ContextWindow
```python
class openjiuwen.core.context_engine.base.ContextWindow()
```

表示即将发送给 LLM 的上下文窗口，包含消息与工具信息。

**参数**：

- **system_messages**(List[[BaseMessage](../../openjiuwen.core/foundation/llm/llm.md#class-openjiuwencorefoundationllmschemamessagebasemessage)]): 系统级指令（如操作指引、角色设定），需固定置于最终消息列表的开头位置。默认值：`[]`。
- **context_messages**(List[[BaseMessage](../../openjiuwen.core/foundation/llm/llm.md#class-openjiuwencorefoundationllmschemamessagebasemessage)]): 对话历史或用户输入信息，可由上下文引擎处理器进行截断、压缩或重排处理。默认值：`[]`。

- **tools**(List[[ToolInfo](../../openjiuwen.core/foundation/tool/tool.md)]): 工具定义信息（函数、插件），模型可在本轮对话中调用此类工具。默认值：`[]`。
- **statistic**("ContextStats"): 上下文窗口的统计信息。默认值：`ContextStats()`。

### get_messages
```python
get_messages() -> List[BaseMessage]
```
将系统级指令和对话历史或用户输入信息进行拼接。

**返回**：

**List[[BaseMessage](../../openjiuwen.core/foundation/llm/llm.md#class-openjiuwencorefoundationllmschemamessagebasemessage)]**，返回系统级指令和对话历史或用户输入信息拼接后的消息列表。

**样例**：

```python
>>> from openjiuwen.core.context_engine import ContextWindow, ContextStats
>>> from openjiuwen.core.foundation.llm import SystemMessage, UserMessage
>>>
>>> window = ContextWindow(
...     system_messages=[SystemMessage(content="你是助手")],
...     context_messages=[UserMessage(content="你好")],
...     statistic=ContextStats(),
... )
>>> msgs = window.get_messages()
>>> len(msgs)
2
```

### get_tools
```python
get_tools() -> List[ToolInfo]
```
工具定义信息。

**返回**：

**List[[ToolInfo](../../openjiuwen.core/foundation/tool/tool.md)]**，返回模型可在本轮对话中调用此类工具的列表。

**样例**：

```python
>>> from openjiuwen.core.context_engine import ContextWindow
>>>
>>> window = ContextWindow(tools=[])
>>> window.get_tools()
[]
```