# openjiuwen.core.context_engine.context_engine

## class openjiuwen.core.context_engine.context_engine.ContextEngine

```python
ContextEngine(config: ContextEngineConfig = None)
```

`ContextEngine` is the entry point for dialogue context management, responsible for creating, caching, and persisting `ModelContext` instances, and supports message compression, offload, and other processing through `Processor`.

**Parameters**:

- **config** ([ContextEngineConfig](config.md#class-openjiuwencorecontext_engineconfigcontextengineconfig), optional): Context engine configuration class. Default value: `None`, uses default ContextEngineConfig.

**Example**:

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

Create or retrieve the `ModelContext` corresponding to the specified session and context_id. If it already exists in the pool, load the session state and return it; otherwise, create a new one and cache it.

**Parameters**:

- **context_id** (str, optional): Context ID, unique identifier for the current context within a session. Used to distinguish multiple contexts within the same session. Default value: `default_context_id`.
- **session** (Session, optional): Session ID. If provided, reads the session ID from `session.get_session_id()` to isolate different sessions. When `None`, uses `"default_session_id"`. Default value: `None`.
- **processors** (List[Tuple[str, BaseModel], optional): Processor configuration list, each item is `(processor_type, config)`, such as `[("MessageOffloader", MessageOffloaderConfig(...))]`. Default value: `None`.
- **history_messages** (List[[BaseMessage](../../openjiuwen.core/foundation/llm/llm.md#class-openjiuwencorefoundationllmschemamessagebasemessage)], optional): Initial message list for the context. Default value: `None`.
- **token_counter** (TokenCounter, optional): Token counting strategy, used to count message and tool token consumption when constructing windows. When `None`, uses TiktokenCounter. Default value: `None`.

**Returns**:

- **[ModelContext](../../openjiuwen.core/context_engine/base.md#class-openjiuwencorecontext_enginebasemodelcontext)**: Newly created or retrieved model context instance from cache.

**Example**:

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

Retrieve an existing `ModelContext` from the internal cache.

**Parameters**:

- **context_id** (str, optional): Context ID. Default value: `"default_context_id"`.
- **session_id** (str, optional): Session ID. Default value: `"default_session_id"`.

**Returns**:

- Existing [ModelContext](../../openjiuwen.core/context_engine/base.md#class-openjiuwencorecontext_enginebasemodelcontext), or `None` if it doesn't exist.

**Example**:

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
Remove context from the internal cache. Behavior: when both parameters are `None`, clear all; when only `session_id` is provided, delete all contexts under that session; when both are provided, delete the specified context. Logs a warning when the target is not found.

**Parameters**:

- **context_id** (str, optional): Context ID. When provided, `session_id` must also be provided. Default value: `None`.
- **session_id** (str, optional): Session ID. Default value: `None`.

**Example**:

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

Batch save runtime state of multiple contexts. Messages, sliding window positions, token counts, and statistics of each context will be saved. When `context_ids` is `None`, persist all contexts under that session.

**Parameters**:

- **context_ids** (list[str], optional): List of context IDs to save. Default value: `None`, meaning all under that session.
- **session** (Session): Session object. When `None`, logs a warning and returns directly.

**Example**:

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

Class method decorator for registering new context processors to the context engine. Usage: `@ContextEngine.register_processor()` decorates the Processor class, and the engine finds and instantiates it through `processor_type()`.

**Example**:

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
>>> # MyProcessor is registered, can pass ("MyProcessor", MyProcessorConfig(...)) when create_context
```
