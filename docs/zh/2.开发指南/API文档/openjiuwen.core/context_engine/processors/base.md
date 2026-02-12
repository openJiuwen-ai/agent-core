# openjiuwen.core.context_engine.processor.base


## class openjiuwen.core.context_engine.processor.base.ContextProcessor

```python
ContextProcessor(config: BaseModel)
```

`ContextProcessor` 是上下文处理器的抽象基类。开发者实现自定义处理器时，需要继承该类。处理器可在两个生命周期介入：消息写入前（`on_add_messages`）、窗口输出前（`on_get_context_window`）。通过对应的 `trigger_*` 判断是否介入，若返回 `True` 则执行 `on_*` 做实际处理。

**参数**：

* **config**(BaseModel)：处理器专属配置对象，通常为 Pydantic 模型实例。

**样例**：

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

在消息即将追加到上下文前，对消息进行变换或过滤。仅在 `trigger_add_messages` 返回 `True` 时被调用。

**参数**：

* **context**(ModelContext)：当前上下文实例。
* **messages_to_add**(List[BaseMessage])：待追加的消息列表。
* **kwargs**：可变参数，传递给处理器链。

**返回**：

**Tuple[ContextEvent | None, List[BaseMessage]]**，二元组。第一个元素为事件对象或 None；第二个元素为处理后的消息列表，将传给下一处理器。返回空列表可取消本次追加。

### async on_get_context_window

```python
async on_get_context_window(context: ModelContext, context_window: ContextWindow, **kwargs) -> Tuple[ContextEvent | None, ContextWindow]
```

在上下文窗口即将返回给调用方前，对窗口进行压缩、重排或替换。仅在 `trigger_get_context_window` 返回 `True` 时被调用。

**参数**：

* **context**(ModelContext)：当前上下文实例。
* **context_window**(ContextWindow)：即将返回的上下文窗口。
* **kwargs**：可变参数，传递给处理器链。

**返回**：

**Tuple[ContextEvent | None, ContextWindow]**，二元组。第一个元素为事件对象或 None；第二个元素为处理后的上下文窗口，禁止返回 None。

### async trigger_add_messages

```python
async trigger_add_messages(context: ModelContext, messages_to_add: List[BaseMessage], **kwargs) -> bool
```

判断是否在消息追加前介入。每次追加都会调用，应保持轻量。返回 `True` 时，会调用 `on_add_messages`。

**参数**：

* **context**(ModelContext)：当前上下文实例。
* **messages_to_add**(List[BaseMessage])：待追加的消息列表。
* **kwargs**：可变参数。

**返回**：

**bool**，`True` 表示需要介入，`False` 表示不介入。默认实现恒返回 `False`。

### async trigger_get_context_window

```python
async trigger_get_context_window(context: ModelContext, context_window: ContextWindow, **kwargs) -> bool
```

判断是否在窗口输出前介入。每次获取窗口都会调用，应保持轻量。返回 `True` 时，会调用 `on_get_context_window`。

**参数**：

* **context**(ModelContext)：当前上下文实例。
* **context_window**(ContextWindow)：即将返回的上下文窗口。
* **kwargs**：可变参数。

**返回**：

**bool**，`True` 表示需要介入，`False` 表示不介入。默认实现恒返回 `False`。

### classmethod processor_type

```python
processor_type() -> str
```

返回处理器类型标识字符串，由元类设置。用于注册与查找。

**返回**：

**str**，处理器类型名；若未注册则返回空字符串。

### config

**类型**：property

返回构造时传入的配置对象，只读。

### async offload_messages

```python
async offload_messages(role: str, content: str, messages: List[BaseMessage], *, context: ModelContext = None, offload_handle: str = None, offload_type: str = "in_memory", **kwargs) -> Optional[BaseMessage]
```

将指定消息 offload 到旁路存储，主上下文中用占位符替换。用于实现消息卸载能力。

**参数**：

* **role**(str)：占位消息的 role。
* **content**(str)：占位消息的文本内容（含占位符）。
* **messages**(List[BaseMessage])：待 offload 的原始消息列表。
* **context**(ModelContext，可选)：当前上下文实例。默认值：`None`。
* **offload_handle**(str，可选)：offload 句柄，为空时自动生成 UUID。默认值：`None`。
* **offload_type**(str，可选)：存储类型。默认值：`"in_memory"`。

**返回**：

**Optional[BaseMessage]**，占位消息实例；若 offload 失败则返回 `None`。
