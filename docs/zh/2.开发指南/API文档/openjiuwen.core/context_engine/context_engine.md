# openjiuwen.core.context_engine.context_engine

## class openjiuwen.core.context_engine.context_engine.ContextEngine

```python
ContextEngine(config: ContextEngineConfig = None)
```

`ContextEngine` 是对话上下文管理的入口，负责创建、缓存和持久化 `ModelContext` 实例，并支持通过 `Processor` 进行消息压缩、offload 等处理。

**参数**：

- **config** ([ContextEngineConfig](config.md#class-openjiuwencorecontext_engineconfigcontextengineconfig), 可选)：上下文引擎配置类。默认值：`None`，使用默认 ContextEngineConfig。

**样例**：

```python
>>> from openjiuwen.core.context_engine import ContextEngine, ContextEngineConfig
>>>
>>> config = ContextEngineConfig(default_window_message_num=100)
>>> engine = ContextEngine(config)
>>> engine
<openjiuwen.core.context_engine.context_engine.ContextEngine object at 0x...>
```

### async create_context

```python
async create_context(
        context_id: str = "default_context_id",
        session: Session = None,
        *,
        processors: List[Tuple[str, BaseModel]] = None,
        history_messages: List[BaseMessage] = None,
        token_counter: TokenCounter = None,
) -> ModelContext:
```

创建或获取指定 session 与 context_id 对应的 ModelContext。若池中已存在则加载 session 状态后返回；否则新建并缓存。

**参数**：

- **context_id** (str, 可选)：上下文ID，会话内当前上下文的唯一标识。用于区分同一会话内的多个上下文。默认值：`default_context_id`。
- **session** (Session, 可选)：会话ID。若传入，会从 `session.get_session_id()` 读取会话ID，用于隔离不同会话。为 `None` 时使用 `"default_session_id"`。默认值：`None`。
- **processors** (List[Tuple[str, BaseModel], 可选)：处理器配置列表，每项为 `(processor_type, config)`，如 `[("MessageOffloader", MessageOffloaderConfig(...))]`。默认值：`None`。
- **history_messages** (List[[BaseMessage](../../openjiuwen.core/foundation/llm/llm.md#class-openjiuwencorefoundationllmschemamessagebasemessage)], 可选)：上下文的初始消息列表。默认值：`None`。
- **token_counter** (TokenCounter, 可选)：Token 统计策略，用于在构造窗口时统计消息与工具的 Token 消耗。为 `None` 时使用 TiktokenCounter。默认值：`None`。

**返回**：

- **[ModelContext](../../openjiuwen.core/context_engine/base.md#class-openjiuwencorecontext_enginebasemodelcontext)**：新创建的或从缓存中获取的模型上下文实例。

**样例**：

```python
>>> import asyncio
>>> from openjiuwen.core.context_engine.context_engine import ContextEngine
>>> from openjiuwen.core.foundation.llm import UserMessage
>>> async def main() -> None:
...     engine = ContextEngine()
...     # When session is None, the default session_id "default_session_id" is used.
...     context = await engine.create_context(context_id="chat_1", session=None)
...     await context.add_messages(UserMessage(content="hello"))
...     messages = context.get_messages()
...     print(messages[-1].content)
>>> asyncio.run(main())
hello
```

### get_context

```python
get_context(
        context_id: str = "default_context_id",
        session_id: str = "default_session_id"
) -> Optional[ModelContext]
```

从内部缓存中获取已存在的 `ModelContext`。

**参数**：

- **context_id** (str, 可选)：上下文 ID。默认值：`"default_context_id"`。
- **session_id** (str, 可选)：会话 ID。默认值：`"default_session_id"`。

**返回**：

- 已存在的 [ModelContext](../../openjiuwen.core/context_engine/base.md#class-openjiuwencorecontext_enginebasemodelcontext)，若不存在则返回 `None`。

**样例**：

```python
>>> import asyncio
>>> from openjiuwen.core.context_engine.context_engine import ContextEngine
>>> async def main() -> None:
...     engine = ContextEngine()
...     # No session provided -> "default_session_id" is used internally.
...     context = await engine.create_context("chat_1")
...     existing = engine.get_context(
...         context_id="chat_1",
...         session_id="default_session_id",
...     )
...     assert existing is context
>>> asyncio.run(main())
```

### clear_context

```python
clear_context(
    context_id: str = None,
    session_id: str = None,
)
```
从内部缓存中移除上下文。行为如下：两参数均为 `None` 时清空全部；仅提供 `session_id` 时删除该 session 下所有 context；同时提供两者时删除指定 context。找不到目标时记录警告日志。

**参数**：

- **context_id** (str, 可选)：上下文 ID。提供时须同时提供 `session_id`。默认值：`None`。
- **session_id** (str, 可选)：会话 ID。默认值：`None`。

**样例**：

```python
# Clear a single context under the default session id::
>>> import asyncio
>>> from openjiuwen.core.context_engine.context_engine import ContextEngine
>>> async def main() -> None:
...     engine = ContextEngine()
...     await engine.create_context("chat_1")  # uses "default_session_id"
...     engine.clear_context(
...         context_id="chat_1",
...         session_id="default_session_id",
...     )
...     assert engine.get_context("chat_1", "default_session_id") is None
>>> asyncio.run(main())

# Clear the entire pool (all sessions and contexts)::
>>> engine = ContextEngine()
>>> # ... create some contexts ...
>>> engine.clear_context()  # removes everything
```

### async save_contexts

```python
async save_contexts(
    session: Session,
    context_ids: list[str],
)
```

批量保存多个上下文的运行时状态。各上下文的消息、滑动窗口位置、token计数与统计信息均会被保存。`context_ids` 为 `None` 时持久化该 session 下全部 context。

**参数**：

- **context_ids** (list[str], 可选)：要保存的上下文 ID 列表。默认值：`None`，表示该 session 下全部。
- **session** (Session)：会话对象。为 `None` 时记录警告并直接返回。

**样例**：

```python
>>> import asyncio
>>> from typing import Any, Optional
>>> from openjiuwen.core.context_engine.context_engine import ContextEngine
>>> class DummySession:
...     def __init__(self) -> None:
...         self._session_id = "demo_session"
...         self._state: dict[str, Any] = {}
...     def get_session_id(self) -> str:
...         return self._session_id
...     def get_state(self, key: Optional[str] = None) -> Any:
...         return self._state.get(key)
...     def update_state(self, data: dict) -> None:
...         self._state.update(data)
>>> async def main() -> None:
...     engine = ContextEngine()
...     session = DummySession()
...     await engine.create_context("chat_1", session=session)
...     await engine.create_context("chat_2", session=session)
...     await engine.save_contexts(session=session)
...     print(sorted(session.get_state("context").keys()))
>>> asyncio.run(main())
['chat_1', 'chat_2']
```

### classmethod register_processor

```python
register_processor(processor_class=None)
```

用于将新的上下文处理器注册到上下文引擎的类方法装饰器。使用方式：`@ContextEngine.register_processor()` 修饰 Processor 类，引擎通过 `processor_type()` 查找并实例化。

**样例**：

```python
>>> from openjiuwen.core.context_engine import ContextEngine
>>> from openjiuwen.core.context_engine.processor.base import ContextProcessor
>>> from pydantic import BaseModel, Field
>>>
>>> class MyProcessorConfig(BaseModel):
...     threshold: int = Field(default=5, gt=0)
>>>
>>> @ContextEngine.register_processor()
>>> class MyProcessor(ContextProcessor):
...     def __init__(self, config: MyProcessorConfig):
...         super().__init__(config)
...     async def trigger_add_messages(self, context, messages_to_add, **kwargs):
...         return False
...     async def on_add_messages(self, context, messages_to_add, **kwargs):
...         return None, messages_to_add
...     def load_state(self, state): pass
...     def save_state(self): return {}
...
>>> # MyProcessor 已注册，create_context 时可传入 ("MyProcessor", MyProcessorConfig(...))
```
