# openjiuwen.core.foundation.prompt.template

## class openjiuwen.core.foundation.prompt.template.PromptTemplate

```python
class openjiuwen.core.foundation.prompt.template.PromptTemplate(BaseModel)
```

The `PromptTemplate` class is a prompt template that provides format conversion, prompt filling, and other functions.

**Parameters**:

- **content** (str | List[[BaseMessage](../../../openjiuwen.core/foundation/llm/llm.md#class-openjiuwencorefoundationllmschemamessagebasemessage)]): Template content, which can be a text string or a list of messages; default `""`. Supports the following two types:
  - str: For example, `你是一个{{role}}助手`.
  - List[[BaseMessage](../../../openjiuwen.core/foundation/llm/llm.md#class-openjiuwencorefoundationllmschemamessagebasemessage)]: Supports a list of `BaseMessage`, for example: `[SystemMessage(content="你是一个{{role}}助手")]`.
- **name** (str, optional): Template name. Default value: `""`.
- **placeholder_prefix** (str): Left boundary of placeholder. Default value: `"{{"`.
- **placeholder_suffix** (str): Right boundary of placeholder. Default value: `"}}"`.


> **Note**
>
> Placeholders can be customized when registering prompt templates.

**Example**:

```python
>>> from openjiuwen.core.foundation.prompt import PromptTemplate
>>> from openjiuwen.core.foundation.llm import UserMessage, SystemMessage
>>> 
>>> # 1. String template + placeholder replacement
>>> template = PromptTemplate(
>>>     name="greeting",
>>>     content="你好，{{user_name}}！今天是 {{date}}。",
>>> )
>>> # 2. Custom placeholder format ${}$
>>> template2 = PromptTemplate(
>>>     name="custom",
>>>     content="你是一个精通${domain}$领域的小助手！",
>>>     placeholder_prefix="${",
>>>     placeholder_suffix="}$",
>>> )
>>> # 3. Message list template (placeholders in message content)
>>> template3 = PromptTemplate(
>>>     name="multi_turn",
>>>     content=[
>>>         SystemMessage(content="你是助手。用户叫 {{user_name}}。"),
>>>         UserMessage(content="请介绍一下{{topic}}。"),
>>>     ],
>>> )
```

### format

```python
format(self, keywords: dict = None) -> PromptTemplate
```

Replace placeholders in template content with `keywords` and return a new `PromptTemplate` instance; does not modify the current instance.

**Parameters**:

- **keywords** (dict, optional): Mapping from variable names to replacement values. If `None` or empty dictionary, returns a deep copy of the current template (no replacement performed). Default value: `None`.

**Returns**:

**PromptTemplate**, returns a new filled prompt template object. `name`, `placeholder_prefix`, `placeholder_suffix` are the same as the current instance, `content` is the replaced content.

**Example**:

```python
>>> from openjiuwen.core.foundation.prompt import PromptTemplate
>>> from openjiuwen.core.foundation.llm import UserMessage, SystemMessage
>>> 
>>> # 1. String template + placeholder replacement
>>> template = PromptTemplate(
>>>     name="greeting",
>>>     content="你好，{{user_name}}！今天是 {{date}}。",
>>> )
>>> filled = template.format(keywords={"user_name": "张三", "date": "2025-02-02"})
>>> # 2. Custom placeholder format ${}$
>>> template2 = PromptTemplate(
>>>     name="custom",
>>>     content="你是一个精通${domain}$领域的小助手！",
>>>     placeholder_prefix="${",
>>>     placeholder_suffix="}$",
>>> )
>>> filled2 = template2.format(keywords={"domain": "数学"})
>>> # 3. Message list template (placeholders in message content)
>>> template3 = PromptTemplate(
>>>     name="multi_turn",
>>>     content=[
>>>         SystemMessage(content="你是助手。用户叫 {{user_name}}。"),
>>>         UserMessage(content="请介绍一下{{topic}}。"),
>>>     ],
>>> )
>>> filled3 = template3.format(keywords={"user_name": "李四", "topic": "Python"})
>>>
```

### to_messages

```python
to_messages(self) -> List[BaseMessage]
```

Convert the current template content to a `BaseMessage` list.

**Returns**:

**List[[BaseMessage](../../../openjiuwen.core/foundation/llm/llm.md#class-openjiuwencorefoundationllmschemamessagebasemessage)]**, message list; returns empty list if content is empty.

**Exceptions**:

- **build_error**: Specific error code and error message are [StatusCode.PROMPT_TEMPLATE_INVALID](../../../openjiuwen.core/common/exception/status_code.md), error_msg="prompt template type must be in str or list[BaseMessage]".


> Note:
>
> 1. When converting `string` type template to message list, the default message type returned is `UserMessage`.
> 2. When converting `BaseMessage` type template to message list, validates each item as `BaseMessage` type, deep copies each message and returns. If a non-`BaseMessage` item exists, raises an exception.

**Example**:

```python
>>> from openjiuwen.core.foundation.prompt import PromptTemplate
>>> from openjiuwen.core.foundation.llm import UserMessage, SystemMessage
>>> 
>>> 
>>> # 1. String template + placeholder replacement
>>> template = PromptTemplate(
>>>     name="greeting",
>>>     content="你好，{{user_name}}！今天是 {{date}}。",
>>> )
>>> filled = template.format(keywords={"user_name": "张三", "date": "2025-02-02"})
>>> messages = filled.to_messages()
>>> # messages is [UserMessage(content="你好，张三！今天是 2025-02-02。")]
>>> 
>>> # 2. Custom placeholder format ${}$
>>> template2 = PromptTemplate(
>>>     name="custom",
>>>     content="你是一个精通${domain}$领域的小助手！",
>>>     placeholder_prefix="${",
>>>     placeholder_suffix="}$",
>>> )
>>> filled2 = template2.format(keywords={"domain": "数学"})
>>> messages2 = filled2.to_messages()
>>> # messages is [UserMessage(content="你是一个精通数学领域的小助手！")]
>>> 
>>> # 3. Message list template (placeholders in message content)
>>> template3 = PromptTemplate(
>>>     name="multi_turn",
>>>     content=[
>>>         SystemMessage(content="你是助手。用户叫 {{user_name}}。"),
>>>         UserMessage(content="请介绍一下{{topic}}。"),
>>>     ],
>>> )
>>> filled3 = template3.format(keywords={"user_name": "李四", "topic": "Python"})
>>> messages3 = filled3.to_messages()
>>> # Get two messages, placeholders in content have been replaced
>>> 
>>> # 4. Only convert messages, no replacement
>>> template4 = PromptTemplate(name="simple", content="直接使用这段文字。")
>>> messages4 = template4.to_messages()
>>> # [UserMessage(content="直接使用这段文字。")]
```
