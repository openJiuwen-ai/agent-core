# openjiuwen.core.context_engine.processor.base


## class openjiuwen.core.context_engine.processor.base.ContextProcessor

```python
ContextProcessor(config: BaseModel)
```

`ContextProcessor` is the abstract base class for context processors. When developers implement custom processors, they need to inherit from this class. Processors can intervene at two lifecycle points: before messages are written (`on_add_messages`) and before window output (`on_get_context_window`). Use the corresponding `trigger_*` methods to determine whether to intervene; if they return `True`, then execute `on_*` to perform the actual processing.

**Parameters**:

* **config** (BaseModel): Processor-specific configuration object, usually a Pydantic model instance.

**Example**:

```python
>>> import asyncio
>>> from pydantic import BaseModel, Field
>>> from openjiuwen.core.context_engine import ContextEngine, ContextEngineConfig
>>> from openjiuwen.core.context_engine.processor.base import ContextProcessor
>>> from openjiuwen.core.foundation.llm import UserMessage
>>>
>>> class MyProcessorConfig(BaseModel):
...     threshold: int = Field(default=5, gt=0)
>>>
>>> @ContextEngine.register_processor()
>>> class MyProcessor(ContextProcessor):
...     def __init__(self, config: MyProcessorConfig):
...         super().__init__(config)
...
...     async def trigger_add_messages(self, context, messages_to_add, **kwargs):
...         return len(context) + len(messages_to_add) > self.config.threshold
...
...     async def on_add_messages(self, context, messages_to_add, **kwargs):
...         return None, messages_to_add
...
...     def load_state(self, state):
...         pass
...
...     def save_state(self):
...         return {}
>>>
>>> async def main():
...     config = ContextEngineConfig(default_window_message_num=100)
...     engine = ContextEngine(config)
...     processor_config = MyProcessorConfig(threshold=3)
...     ctx = await engine.create_context(
...         "test_ctx", None,
...         processors=[("MyProcessor", processor_config)],
...     )
...     await ctx.add_messages([UserMessage(content="m1"), UserMessage(content="m2")])
...     return len(ctx.get_messages())
>>>
>>> asyncio.run(main())
2
```

### async on_add_messages

```python
async on_add_messages(context: ModelContext, messages_to_add: List[BaseMessage], **kwargs) -> Tuple[ContextEvent | None, List[BaseMessage]]
```

Transform or filter messages before they are appended to the context. Only called when `trigger_add_messages` returns `True`.

**Parameters**:

* **context** (ModelContext): Current context instance.
* **messages_to_add** (List[BaseMessage]): List of messages to be appended.
* **kwargs**: Variadic parameters, passed to the processor chain.

**Returns**:

**Tuple[ContextEvent | None, List[BaseMessage]]**, a tuple. The first element is an event object or None; the second element is the processed message list, which will be passed to the next processor. Returning an empty list cancels this append operation.

### async on_get_context_window

```python
async on_get_context_window(context: ModelContext, context_window: ContextWindow, **kwargs) -> Tuple[ContextEvent | None, ContextWindow]
```

Compress, reorder, or replace the window before the context window is returned to the caller. Only called when `trigger_get_context_window` returns `True`.

**Parameters**:

* **context** (ModelContext): Current context instance.
* **context_window** (ContextWindow): Context window about to be returned.
* **kwargs**: Variadic parameters, passed to the processor chain.

**Returns**:

**Tuple[ContextEvent | None, ContextWindow]**, a tuple. The first element is an event object or None; the second element is the processed context window, None is not allowed.

### async trigger_add_messages

```python
async trigger_add_messages(context: ModelContext, messages_to_add: List[BaseMessage], **kwargs) -> bool
```

Determine whether to intervene before message append. Called on every append, should remain lightweight. When it returns `True`, `on_add_messages` will be called.

**Parameters**:

* **context** (ModelContext): Current context instance.
* **messages_to_add** (List[BaseMessage]): List of messages to be appended.
* **kwargs**: Variadic parameters.

**Returns**:

**bool**, `True` means intervention is needed, `False` means no intervention. Default implementation always returns `False`.

### async trigger_get_context_window

```python
async trigger_get_context_window(context: ModelContext, context_window: ContextWindow, **kwargs) -> bool
```

Determine whether to intervene before window output. Called on every window retrieval, should remain lightweight. When it returns `True`, `on_get_context_window` will be called.

**Parameters**:

* **context** (ModelContext): Current context instance.
* **context_window** (ContextWindow): Context window about to be returned.
* **kwargs**: Variadic parameters.

**Returns**:

**bool**, `True` means intervention is needed, `False` means no intervention. Default implementation always returns `False`.

### classmethod processor_type

```python
processor_type() -> str
```

Return the processor type identifier string, set by the metaclass. Used for registration and lookup.

**Returns**:

**str**, processor type name; returns empty string if not registered.

### config

**Type**: property

Return the configuration object passed during construction, read-only.

### async offload_messages

```python
async offload_messages(role: str, content: str, messages: List[BaseMessage], *, context: ModelContext = None, offload_handle: str = None, offload_type: str = "in_memory", **kwargs) -> Optional[BaseMessage]
```

Offload specified messages to side storage, replacing them with placeholders in the main context. Used to implement message offloading capability.

**Parameters**:

* **role** (str): Role of the placeholder message.
* **content** (str): Text content of the placeholder message (containing placeholder).
* **messages** (List[BaseMessage]): List of original messages to be offloaded.
* **context** (ModelContext, optional): Current context instance. Default value: `None`.
* **offload_handle** (str, optional): Offload handle, automatically generates UUID if empty. Default value: `None`.
* **offload_type** (str, optional): Storage type. Default value: `"in_memory"`.

**Returns**:

**Optional[BaseMessage]**, placeholder message instance; returns `None` if offload fails.
