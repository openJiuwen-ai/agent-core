# openjiuwen.core.context_engine.base

## class openjiuwen.core.context_engine.base.ModelContext
```python
class openjiuwen.core.context_engine.base.ModelContext()
```

`ModelContext` model context, defines interfaces for message addition/removal, window construction, statistics, etc. Supports BaseMessage type content format.

### abstract add_message
```python
abstract add_message(message: BaseMessage | List[BaseMessage]) -> List[BaseMessage]
```
Add *one* or *multiple* messages to the model context.

**Parameters**:

- **message** ([BaseMessage](../../openjiuwen.core/foundation/llm/llm.md#class-openjiuwencorefoundationllmschemamessagebasemessage) | List[BaseMessage]): Single or multiple message objects to add, must be BaseMessage or a list of BaseMessage types.

**Returns**:

**List[[BaseMessage](../../openjiuwen.core/foundation/llm/llm.md#class-openjiuwencorefoundationllmschemamessagebasemessage)]**, the message list after appending (result after Processor processing).

**Example**:

```python
>>> import asyncio
>>> from openjiuwen.core.context_engine import ContextEngine, ContextEngineConfig
>>> from openjiuwen.core.foundation.llm import UserMessage
>>>
>>> async def main():
...     engine = ContextEngine(ContextEngineConfig())
...     ctx = await engine.create_context(session=None, history_messages=[])
...     added = await ctx.add_messages(UserMessage(content="Hello"))
...     return len(added), ctx.get_messages()[0].content
>>>
>>> asyncio.run(main())
(1, 'Hello')
```

### abstract get_messages
```python
abstract get_messages(size: Optional[int] = None, with_history: bool = True) -> List[BaseMessage]
```
Retrieve messages from the dialogue context (does not perform removal operation).

**Parameters**:

- **size** (int, optional): Number of messages to retrieve. Default value: `None`, meaning retrieve all messages.
- **with_history** (bool, optional): Whether to include history. If `True`, returns messages including history records; if `False`, only returns the latest messages. Default value: `True`.

**Returns**:

**List[[BaseMessage](../../openjiuwen.core/foundation/llm/llm.md#class-openjiuwencorefoundationllmschemamessagebasemessage)]**, retrieved messages returned in original order, with the most recent message at the end.

**Example**:

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
Replace the current message list with the incoming message list.

**Parameters**:

- **messages** (List[[BaseMessage](../../openjiuwen.core/foundation/llm/llm.md#class-openjiuwencorefoundationllmschemamessagebasemessage)]): New message sequence to place into the model context.
- **with_history** (bool, optional): Replacement scope. `True` means replacing the concatenated message set (composed of "context messages + history messages"); `False` means only replacing history messages, keeping context messages unchanged. Default value: `True`.

**Example**:

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
```

### abstract pop_message
```python
abstract pop_message(size: int = 1, with_history: bool = True) -> List[BaseMessage]
```
Remove and return the latest `size` messages from the current request message list.

**Parameters**:

- **size** (int, optional): Number of messages to pop. Default value: `1`.
- **with_history** (bool, optional): Whether to synchronously update history. `True` means simultaneously removing corresponding messages from the underlying persistent history; `False` means only affecting the current request message list. Default value: `True`.

**Returns**:

**List[[BaseMessage](../../openjiuwen.core/foundation/llm/llm.md#class-openjiuwencorefoundationllmschemamessagebasemessage)]**: The removed messages.

**Example**:

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
Remove all messages added in the current dialogue round.

**Parameters**:

- **with_history** (bool, optional): Whether to clear history. `True` means simultaneously clearing the underlying persistent history messages, i.e., initial messages loaded from memory or passed when creating the context; `False` means only discarding messages accumulated during this request, keeping persistent history messages unchanged. Default value: `True`.

**Example**:

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
Build and retrieve a context window suitable for model inference.

**Parameters**:

- **system_messages** (List[[BaseMessage](../../openjiuwen.core/foundation/llm/llm.md#class-openjiuwencorefoundationllmschemamessagebasemessage)], optional): System-level messages to prepend to the message window.
- **tools** (List[[ToolInfo](../../openjiuwen.core/foundation/tool/tool.md)], optional): Tool definition information to include in the message window.
- **window_size** (int, optional): Maximum number of historical messages to include; if omitted, includes all historical messages by default.
- **dialogue_round** (int, optional): Number of latest dialogue rounds to retain. A round is defined as: starting from a user message, ending at the next assistant message without tool calls (i.e., the final reply in a tool-call round). Incomplete rounds (only user messages, no subsequent assistant messages) still count as one round. When this parameter is specified together with `window_size`, this parameter takes higher priority. If explicitly set, the value must be greater than 0; default value `None` means disabling round-based message truncation logic.
- **kwargs** (dict, optional): Additional context-related parameters.

**Returns**:

**ContextWindow**, a context window containing the constructed message list and metadata.

**Example**:

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
...         system_messages=[SystemMessage(content="You are an assistant")],
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
Get computed context-level statistics.

**Returns**:

**ContextStats**, aggregated statistics of message and token counts for the context.

**Example**:

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
Get the globally unique identifier for the current user session.

**Returns**:

**str**, the globally unique identifier for the current user session.

**Example**:

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
Get the globally unique identifier for the current context within the session.

**Returns**:

**str**, the globally unique identifier for the current context within the session.

**Example**:

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
Get the token counter.

**Returns**:

**TokenCounter**, token counter instance.

**Example**:

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
Get the context original message reload tool.

**Returns**:

**Tool**, returns the context original text reload tool instance. When the `enable_reload` configuration is enabled, the tool can be registered to the agent to help the agent reload original messages processed by context processors when detailed information is needed.
`offload_handle`: Points to the universally unique identifier (UUID) or file path of the offloaded content.
`offload_type`: Storage backend type (currently supports: "in_memory" memory storage).

**Example**:

```python
>>> import asyncio
>>> from openjiuwen.core.context_engine import ContextEngine, ContextEngineConfig
>>>
>>> async def main():
...     engine = ContextEngine(ContextEngineConfig())
...     ctx = await engine.create_context(session=None, history_messages=[])
...     tool = ctx.reloader_tool()
...     # Tool name can be obtained via `tool.card.name`.
...     return tool.card.name
>>>
>>> asyncio.run(main())
'reload_original_context_messages'
```

## class openjiuwen.core.context_engine.base.ContextStats
```python
class openjiuwen.core.context_engine.base.ContextStats()
```

`ContextStats` represents the total message count, total token usage, and counts and token usage of various message types for any context or context window (applicable to model context / context window).

**Parameters**:

- **total_messages** (int): Total number of system / user / assistant / tool messages. Default value: `0`.
- **total_tokens** (int): Total number of system / user / assistant / tool tokens. Default value: `0`.

- **system_messages** (int): Number of system messages. Default value: `0`.
- **user_messages** (int): Number of user messages. Default value: `0`.
- **assistant_messages** (int): Number of assistant messages. Default value: `0`.
- **tool_messages** (int): Number of tool messages. Default value: `0`.
- **tools** (int): Number of tool information objects injected into prompts. Default value: `0`.

- **system_message_tokens** (int): Number of system message tokens. Default value: `0`.
- **user_message_tokens** (int): Number of user message tokens. Default value: `0`.
- **assistant_message_tokens** (int): Number of assistant message tokens. Default value: `0`.
- **tool_message_tokens** (int): Number of tool message tokens. Default value: `0`.
- **tool_tokens** (int): Number of tool information object tokens injected into prompts. Default value: `0`.

**Example**:

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

Represents a context window about to be sent to the LLM, containing messages and tool information.

**Parameters**:

- **system_messages** (List[[BaseMessage](../../openjiuwen.core/foundation/llm/llm.md#class-openjiuwencorefoundationllmschemamessagebasemessage)]): System-level instructions (such as operation guidelines, role settings), must be fixed at the beginning of the final message list. Default value: `[]`.
- **context_messages** (List[[BaseMessage](../../openjiuwen.core/foundation/llm/llm.md#class-openjiuwencorefoundationllmschemamessagebasemessage)]): Dialogue history or user input information, which can be truncated, compressed, or reordered by context engine processors. Default value: `[]`.

- **tools** (List[[ToolInfo](../../openjiuwen.core/foundation/tool/tool.md)]): Tool definition information (functions, plugins), models can call such tools in this round of dialogue. Default value: `[]`.
- **statistic** ("ContextStats"): Statistics for the context window. Default value: `ContextStats()`.

### get_messages
```python
get_messages() -> List[BaseMessage]
```
Concatenate system-level instructions with dialogue history or user input information.

**Returns**:

**List[[BaseMessage](../../openjiuwen.core/foundation/llm/llm.md#class-openjiuwencorefoundationllmschemamessagebasemessage)]**, returns the message list after concatenating system-level instructions with dialogue history or user input information.

**Example**:

```python
>>> from openjiuwen.core.context_engine import ContextWindow, ContextStats
>>> from openjiuwen.core.foundation.llm import SystemMessage, UserMessage
>>>
>>> window = ContextWindow(
...     system_messages=[SystemMessage(content="You are an assistant")],
...     context_messages=[UserMessage(content="Hello")],
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
Tool definition information.

**Returns**:

**List[[ToolInfo](../../openjiuwen.core/foundation/tool/tool.md)]**, returns the list of tools that the model can call in this round of dialogue.

**Example**:

```python
>>> from openjiuwen.core.context_engine import ContextWindow
>>>
>>> window = ContextWindow(tools=[])
>>> window.get_tools()
[]
```
