# openjiuwen.core.foundation.prompt.template

## class openjiuwen.core.foundation.prompt.template.PromptTemplate

```python
class openjiuwen.core.foundation.prompt.template.PromptTemplate(BaseModel)
```

`PromptTemplate`类为提示词模板，提供格式转换、提示词填充等功能。

**参数**：

- **content**(str | List[[BaseMessage](../../../openjiuwen.core/foundation/llm/llm.md#class-openjiuwencorefoundationllmschemamessagebasemessage)])：模板内容，可为一段文本或消息列表；默认 `""`。支持以下两种类型：
  - str：例如`你是一个{{role}}助手`。
  - List[[BaseMessage](../../../openjiuwen.core/foundation/llm/llm.md#class-openjiuwencorefoundationllmschemamessagebasemessage)]：支持`BaseMessage`的列表，例如：`[SystemMessage(content="你是一个{{role}}助手")]`。
- **name**(str, 可选)：模板名称，默认值：`""`。
- **placeholder_prefix**(str)：占位符左边界，默认值：`"{{"`。
- **placeholder_suffix**(str)：占位符右边界，默认值：`"}}"`。


> **说明**
>
> 注册提示词模板时占位符可自定义传入。

**样例**：

```python
>>> from openjiuwen.core.foundation.prompt import PromptTemplate
>>> from openjiuwen.core.foundation.llm import UserMessage, SystemMessage
>>> 
>>> # 1. 字符串模板 + 占位符替换
>>> template = PromptTemplate(
>>>     name="greeting",
>>>     content="你好，{{user_name}}！今天是 {{date}}。",
>>> )
>>> # 2. 占位符为自定义格式${}$
>>> template2 = PromptTemplate(
>>>     name="custom",
>>>     content="你是一个精通${domain}$领域的小助手！",
>>>     placeholder_prefix="${",
>>>     placeholder_suffix="}$",
>>> )
>>> # 3. 消息列表模板（占位符在消息 content 中）
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

用 `keywords` 替换模板内容中的占位符，返回新的 `PromptTemplate` 实例；不修改当前实例。

**参数**：

- **keywords**(dict, 可选)：变量名到替换值的映射。若为 `None` 或空字典，则返回当前模板的深拷贝（未做替换）。默认值：`None`。

**返回**：

**PromptTemplate**，返回一个新的填充后的提示词模板对象。`name`、`placeholder_prefix`、`placeholder_suffix` 与当前实例相同，`content` 为替换后的内容。

**样例**：

```python
>>> from openjiuwen.core.foundation.prompt import PromptTemplate
>>> from openjiuwen.core.foundation.llm import UserMessage, SystemMessage
>>> 
>>> # 1. 字符串模板 + 占位符替换
>>> template = PromptTemplate(
>>>     name="greeting",
>>>     content="你好，{{user_name}}！今天是 {{date}}。",
>>> )
>>> filled = template.format(keywords={"user_name": "张三", "date": "2025-02-02"})
>>> # 2. 占位符为自定义格式${}$
>>> template2 = PromptTemplate(
>>>     name="custom",
>>>     content="你是一个精通${domain}$领域的小助手！",
>>>     placeholder_prefix="${",
>>>     placeholder_suffix="}$",
>>> )
>>> filled2 = template2.format(keywords={"domain": "数学"})
>>> # 3. 消息列表模板（占位符在消息 content 中）
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

将当前模板内容转换为 `BaseMessage` 列表。

**返回**：

**List[[BaseMessage](../../../openjiuwen.core/foundation/llm/llm.md#class-openjiuwencorefoundationllmschemamessagebasemessage)]**，消息列表；若 content 为空则返回空列表。

**异常**：

- **build_error**：具体错误码和错误信息为[StatusCode.PROMPT_TEMPLATE_INVALID](../../../openjiuwen.core/common/exception/status_code.md), error_msg="prompt template type must be in str or list[BaseMessage]"。


> 说明：
>
> 1. 转换`string`类型模板到消息列表时，默认返回的消息类型为`UserMessage`。
> 2. 转换`BaseMessage`类型模板到消息列表时，校验每项为 `BaseMessage` 类型，对每条消息深拷贝后返回。若存在非 `BaseMessage` 项，抛出异常信息。

**样例**：

```python
>>> from openjiuwen.core.foundation.prompt import PromptTemplate
>>> from openjiuwen.core.foundation.llm import UserMessage, SystemMessage
>>> 
>>> 
>>> # 1. 字符串模板 + 占位符替换
>>> template = PromptTemplate(
>>>     name="greeting",
>>>     content="你好，{{user_name}}！今天是 {{date}}。",
>>> )
>>> filled = template.format(keywords={"user_name": "张三", "date": "2025-02-02"})
>>> messages = filled.to_messages()
>>> # messages 为 [UserMessage(content="你好，张三！今天是 2025-02-02。")]
>>> 
>>> # 2. 占位符为自定义格式${}$
>>> template2 = PromptTemplate(
>>>     name="custom",
>>>     content="你是一个精通${domain}$领域的小助手！",
>>>     placeholder_prefix="${",
>>>     placeholder_suffix="}$",
>>> )
>>> filled2 = template2.format(keywords={"domain": "数学"})
>>> messages2 = filled2.to_messages()
>>> # messages 为 [UserMessage(content="你是一个精通数学领域的小助手！")]
>>> 
>>> # 3. 消息列表模板（占位符在消息 content 中）
>>> template3 = PromptTemplate(
>>>     name="multi_turn",
>>>     content=[
>>>         SystemMessage(content="你是助手。用户叫 {{user_name}}。"),
>>>         UserMessage(content="请介绍一下{{topic}}。"),
>>>     ],
>>> )
>>> filled3 = template3.format(keywords={"user_name": "李四", "topic": "Python"})
>>> messages3 = filled3.to_messages()
>>> # 得到两条消息，content 中占位符已替换
>>> 
>>> # 4. 仅转消息、不替换
>>> template4 = PromptTemplate(name="simple", content="直接使用这段文字。")
>>> messages4 = template4.to_messages()
>>> # [UserMessage(content="直接使用这段文字。")]
```