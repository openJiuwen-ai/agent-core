# openjiuwen.core.operator.llm_call.base

## class openjiuwen.core.operator.llm_call.base.LLMCall

```python
LLMCall(model_name: str, llm: Model, system_prompt: str | List[BaseMessage] | List[Dict], user_prompt: str | List[BaseMessage] | List[Dict], freeze_system_prompt: bool = False, freeze_user_prompt: bool = True, llm_call_id: str = "llm_call")
```

`LLMCall` encapsulates the configuration and execution logic for a single LLM call, supporting system/user prompt templates, history message injection, and tool calls. When called, it fills the templates based on `inputs` and assembles the `[system, history, user]` message sequence before forwarding to the underlying `Model`.

**Parameters**:

* **model_name** (str): Model name, passed when calling `llm.invoke` / `llm.stream`.
* **llm** (Model): LLM instance for executing invoke and stream.
* **system_prompt** (str | List[BaseMessage] | List[Dict]): System prompt, supports string template or message list. Strings support `{{key}}` placeholders.
* **user_prompt** (str | List[BaseMessage] | List[Dict], optional): User prompt, same as above. Default: `"{{query}}"`.
* **freeze_system_prompt** (bool, optional): When `True`, prevents `update_system_prompt` from modifying. Default: `False`.
* **freeze_user_prompt** (bool, optional): When `True`, prevents `update_user_prompt` from modifying. Default: `True`.
* **llm_call_id** (str, optional): Identifier for this call, used for optimizer_callback, etc. Default: `"llm_call"`.

### async invoke

```python
async invoke(inputs: Dict[str, Any], session: Session, history: Optional[List[BaseMessage]] = None, tools: Optional[List[ToolInfo]] = None) -> BaseMessage
```

Fill the prompt templates based on `inputs` and call the LLM, returning the model's reply message. If `optimizer_callback` is set, it will be executed after the call completes.

**Parameters**:

* **inputs** (Dict[str, Any]): Variables for filling system/user templates, e.g., `{"query": "user question"}`.
* **session** (Session): Session object for optimizer_callback, etc.
* **history** (List[BaseMessage], optional): History message list, inserted between system and user. Default: `None`.
* **tools** (List[ToolInfo], optional): List of callable tools. Default: `None`.

**Returns**:

**BaseMessage** (usually AssistantMessage), the complete message returned by the model.

**Example**:

```python
>>> import os
>>> import asyncio
>>> from unittest.mock import MagicMock
>>> from openjiuwen.core.operator.llm_call import LLMCall
>>> from openjiuwen.core.foundation.llm import Model, ModelRequestConfig, ModelClientConfig
>>>
>>> API_BASE = os.getenv("API_BASE", "your api base")
>>> API_KEY = os.getenv("API_KEY", "your api key")
>>> MODEL_NAME = os.getenv("MODEL_NAME", "gpt-4o-mini")
>>> MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "OpenAI")
>>>
>>> async def main():
...     model_config = ModelRequestConfig(model=MODEL_NAME)
...     model_client_config = ModelClientConfig(
...         client_provider=MODEL_PROVIDER,
...         api_base=API_BASE,
...         api_key=API_KEY,
...     )
...     llm = Model(model_client_config, model_config)
...     llm_call = LLMCall(
...         model_name=MODEL_NAME,
...         llm=llm,
...         system_prompt="You are an assistant.",
...         user_prompt="{{query}}",
...     )
...     session = MagicMock()
...     result = await llm_call.invoke(
...         inputs={"query": "Hello"},
...         session=session,
...     )
...     return result.content
>>>
>>> asyncio.run(main())
'Hello! How can I help you?'
```

### async stream

```python
async stream(inputs: Dict[str, Any], session: Session, history: Optional[List[BaseMessage]] = None, tools: Optional[List[ToolInfo]] = None) -> AsyncIterator
```

Stream call to LLM, yielding message chunks one by one. If `optimizer_callback` is set, it will be executed after streaming ends, based on the complete concatenated content.

**Parameters**:

* **inputs** (Dict[str, Any]): Variables for filling templates.
* **session** (Session): Session object.
* **history** (List[BaseMessage], optional): History message list. Default: `None`.
* **tools** (List[ToolInfo], optional): List of callable tools. Default: `None`.

**Returns**:

**AsyncIterator**, an asynchronous iterator that yields `AssistantMessageChunk` chunks.

### get_optimizer_callback

```python
get_optimizer_callback() -> Optional[Callable]
```

Return the currently set optimizer callback function.

### set_optimizer_callback

```python
set_optimizer_callback(callback: Optional[Callable]) -> None
```

Set or clear the optimizer callback. The callback signature is `async (llm_call_id, inputs, response, session)`, called after invoke/stream completes.

### get_system_prompt

```python
get_system_prompt() -> PromptTemplate
```

Return the current system prompt template.

### get_user_prompt

```python
get_user_prompt() -> PromptTemplate
```

Return the current user prompt template.

### update_system_prompt

```python
update_system_prompt(system_prompt: str | List[BaseMessage] | List[Dict]) -> None
```

Update the system prompt. If `freeze_system_prompt` is `True`, it will not take effect.

### update_user_prompt

```python
update_user_prompt(user_prompt: str | List[BaseMessage] | List[Dict]) -> None
```

Update the user prompt. If `freeze_user_prompt` is `True`, it will not take effect.

### set_freeze_system_prompt

```python
set_freeze_system_prompt(switch: bool) -> None
```

Set whether to freeze the system prompt.

### set_freeze_user_prompt

```python
set_freeze_user_prompt(switch: bool) -> None
```

Set whether to freeze the user prompt.

### get_freeze_system_prompt

```python
get_freeze_system_prompt() -> bool
```

Return whether the system prompt is frozen.

### get_freeze_user_prompt

```python
get_freeze_user_prompt() -> bool
```

Return whether the user prompt is frozen.
