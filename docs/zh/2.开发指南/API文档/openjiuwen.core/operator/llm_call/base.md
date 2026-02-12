# openjiuwen.core.operator.llm_call.base

## class openjiuwen.core.operator.llm_call.base.LLMCall

```python
LLMCall(model_name: str, llm: Model, system_prompt: str | List[BaseMessage] | List[Dict], user_prompt: str | List[BaseMessage] | List[Dict], freeze_system_prompt: bool = False, freeze_user_prompt: bool = True, llm_call_id: str = "llm_call")
```

`LLMCall` 封装单次 LLM 调用的配置与执行逻辑，支持 system/user 提示词模板、历史消息注入和工具调用。调用时根据 `inputs` 填充模板并拼装 `[system, history, user]` 消息序列后转发给底层 `Model`。

**参数**：

* **model_name**(str)：模型名称，调用 `llm.invoke` / `llm.stream` 时传入。
* **llm**(Model)：大模型实例，用于执行 invoke 与 stream。
* **system_prompt**(str | List[BaseMessage] | List[Dict])：系统提示词，支持字符串模板或消息列表。字符串支持 `{{key}}` 占位符。
* **user_prompt**(str | List[BaseMessage] | List[Dict]，可选)：用户提示词，同上。默认值：`"{{query}}"`。
* **freeze_system_prompt**(bool，可选)：为 `True` 时禁止 `update_system_prompt` 修改。默认值：`False`。
* **freeze_user_prompt**(bool，可选)：为 `True` 时禁止 `update_user_prompt` 修改。默认值：`True`。
* **llm_call_id**(str，可选)：本调用的标识符，用于 optimizer_callback 等。默认值：`"llm_call"`。

### async invoke

```python
async invoke(inputs: Dict[str, Any], session: Session, history: Optional[List[BaseMessage]] = None, tools: Optional[List[ToolInfo]] = None) -> BaseMessage
```

根据 `inputs` 填充提示词模板并调用 LLM，返回模型回复消息。若已设置 `optimizer_callback`，则会在调用结束后执行回调。

**参数**：

* **inputs**(Dict[str, Any])：用于填充 system/user 模板的变量，如 `{"query": "用户问题"}`。
* **session**(Session)：会话对象，用于 optimizer_callback 等。
* **history**(List[BaseMessage]，可选)：历史消息列表，插入到 system 与 user 之间。默认值：`None`。
* **tools**(List[ToolInfo]，可选)：可调用工具列表。默认值：`None`。

**返回**：

**BaseMessage**（通常为 AssistantMessage），模型返回的完整消息。

**样例**：

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
...         system_prompt="你是一个助手。",
...         user_prompt="{{query}}",
...     )
...     session = MagicMock()
...     result = await llm_call.invoke(
...         inputs={"query": "你好"},
...         session=session,
...     )
...     return result.content
>>>
>>> asyncio.run(main())
'你好！有什么可以帮助你的？'
```

### async stream

```python
async stream(inputs: Dict[str, Any], session: Session, history: Optional[List[BaseMessage]] = None, tools: Optional[List[ToolInfo]] = None) -> AsyncIterator
```

流式调用 LLM，逐个产出消息块。若已设置 `optimizer_callback`，则在流结束后、基于完整拼接内容执行回调。

**参数**：

* **inputs**(Dict[str, Any])：用于填充模板的变量。
* **session**(Session)：会话对象。
* **history**(List[BaseMessage]，可选)：历史消息列表。默认值：`None`。
* **tools**(List[ToolInfo]，可选)：可调用工具列表。默认值：`None`。

**返回**：

**AsyncIterator**，异步迭代器，逐块产出 `AssistantMessageChunk`。

### get_optimizer_callback

```python
get_optimizer_callback() -> Optional[Callable]
```

返回当前设置的优化回调函数。

### set_optimizer_callback

```python
set_optimizer_callback(callback: Optional[Callable]) -> None
```

设置或清除优化回调。回调签名为 `async (llm_call_id, inputs, response, session)`，在 invoke/stream 完成后调用。

### get_system_prompt

```python
get_system_prompt() -> PromptTemplate
```

返回当前系统提示词模板。

### get_user_prompt

```python
get_user_prompt() -> PromptTemplate
```

返回当前用户提示词模板。

### update_system_prompt

```python
update_system_prompt(system_prompt: str | List[BaseMessage] | List[Dict]) -> None
```

更新系统提示词。若 `freeze_system_prompt` 为 `True`，则不会生效。

### update_user_prompt

```python
update_user_prompt(user_prompt: str | List[BaseMessage] | List[Dict]) -> None
```

更新用户提示词。若 `freeze_user_prompt` 为 `True`，则不会生效。

### set_freeze_system_prompt

```python
set_freeze_system_prompt(switch: bool) -> None
```

设置是否冻结系统提示词。

### set_freeze_user_prompt

```python
set_freeze_user_prompt(switch: bool) -> None
```

设置是否冻结用户提示词。

### get_freeze_system_prompt

```python
get_freeze_system_prompt() -> bool
```

返回系统提示词是否已冻结。

### get_freeze_user_prompt

```python
get_freeze_user_prompt() -> bool
```

返回用户提示词是否已冻结。
