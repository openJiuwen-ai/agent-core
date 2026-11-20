# openjiuwen.core.foundation.tool

`openjiuwen.core.foundation.tool` 模块提供了工具（Tool）的抽象定义及多种实现，支持本地函数、RESTful API 和 MCP（Model Context Protocol）工具。工具是 Agent 与外部世界交互的桥梁。

## alias Input

```python
Input = TypeVar('Input', contravariant=True)
```

工具输入的泛型类型变量。

## alias Output

```python
Output = TypeVar('Output', contravariant=True)
```

工具输出的泛型类型变量。

## class ToolCard

```python
class ToolCard(BaseCard)
```

工具卡片数据类，继承自 `BaseCard`，包含工具的基本信息和参数定义。

* **id**(str, 可选)：工具的唯一标识符。
* **name**(str, 可选)：工具名称。
* **description**(str, 可选)：工具描述。
* **input_params**(Dict[str, Any] | Type[BaseModel], 可选)：工具的输入参数模式（Schema）。可以是 JSON Schema 字典或 Pydantic 模型类。默认为空字典，表示没有输入参数模式，接受任何输入。
* **execution**(ToolExecution, 可选)：谁执行该工具的调用。`"server"`：Agent 调用 `invoke`；`"client"`：由连接的客户端执行，见[客户端执行的工具](#客户端执行的工具)。仅供框架判断谁来执行，不会出现在发给模型的工具定义里。默认值：`"server"`。

### tool_info
```python
def tool_info() -> ToolInfo
```

将工具卡片转换为 `ToolInfo` 对象，供 LLM 调用使用。

**返回**：

**ToolInfo**，LLM可识别的工具信息。

## class Tool

```python
class Tool(card: ToolCard)
```

工具的抽象基类。

定义了工具的基本行为，所有具体工具实现都应继承此类。

**参数**：

* **card**(ToolCard)：工具卡片，定义了工具的行为和参数。不可为 None，且必须包含 id。

### card
```python
@property
def card() -> ToolCard
```

获取工具对应的卡片。

**返回**：

**ToolCard**，工具的配置卡片。

### invoke

```python
async def invoke(inputs: Input, **kwargs) -> Output
```
执行工具并返回完整结果。

这是一个抽象方法，子类必须实现具体的执行逻辑。

**参数**：

* **inputs**(Input)：符合工具输入模式的结构化输入数据。
* **\*\*kwargs**：额外的执行参数，如超时、重试策略或特定于工具的选项。

**返回**：

**Output**，工具执行的完整结果。

### stream

```python
async def stream(inputs: Input, **kwargs) -> AsyncIterator[Output]
```

执行工具并流式返回结果。

这是一个抽象方法，支持长运行操作的部分结果返回。

**参数**：

* **inputs**(Input)：符合工具输入模式的结构化输入数据。
* **\*\*kwargs**：额外的流式执行参数。

**返回**：

**AsyncIterator[Output]**，工具执行过程中的增量结果迭代器。

### render_for_llm

```python
def render_for_llm(output: Any) -> str
```

把 `invoke` 的结果渲染成模型读取的纯文本。Agent 构造工具结果消息（`ToolMessage`）时对每次工具调用调用一次；结构化结果本身原样保留给 rail、事件与日志等程序消费者。子类覆写该方法即可定制模型看到的文本。

默认实现：

* 结果为 `ToolOutput` 且 `success=True`：使用 `data["content"]`；`data` 是字符串时直接使用；没有 `content` 的其他载荷序列化为 JSON。
* 结果为 `ToolOutput` 且 `success=False`：使用 `error`；`error` 为空时回落到载荷。
* 渲染结果为空时返回简短占位文本，模型不会收到空白的工具结果。
* 结果不是 `ToolOutput`（如普通函数、REST API 的返回值）：返回 `str(output)`。

自定义实现抛出异常时，Agent 记录日志并回落到默认实现，不会把已执行完成的工具调用变成执行失败。

**参数**：

* **output**(Any)：`invoke` 的返回值，通常为 `ToolOutput`。

**返回**：

**str**，面向模型的工具结果文本。

## 客户端执行的工具

模型像调用普通工具一样调用客户端工具，但由连接的客户端（如打开文档的编辑器前端）执行。所有 `before_tool_call` Rail 放行后，Agent 不调用 `invoke`，而是以 `interrupt_kind="client_tool"` 的中断暂停该调用，请求为 `ClientToolRequest`（`tool_name`、最终参数 `arguments`、`client_request`）。宿主让客户端执行后用 `InteractiveInput.answer(interrupt_id, result)` 回答，结果成为该调用的正常工具输出。

### class ClientTool

```python
class ClientTool(Tool)
```

客户端工具基类。构造时使用标记为 `execution="client"` 的卡片（必要时为副本，不修改传入的卡片）。`invoke` 不会被调用。

* **build_request**(arguments: dict, context: ClientToolContext) -> dict：返回客户端执行所需的信息（作为 `client_request` 发给宿主）。抛出 `ClientToolUnavailable(message)` 时调用直接以工具错误结束，不联系客户端（例如没有打开的文档、输入不合法）。可为 `async`。
* **complete**(arguments: dict, result: ClientToolResult, context: ClientToolContext) -> ToolOutput：把客户端结果变成工具输出；`arguments` 为发给客户端的参数。返回非 `ToolOutput` 时作为结果文本，图片与状态保留。可为 `async`。默认使用 `default_client_output`。

### class ClientToolContext

调用上下文：**tool_call**（含最终参数的调用）、**session**（Agent 会话，可读写会话状态）、**agent**、**callback_context**，以及 **session_id** 属性。

### class ClientToolResult

宿主的答复。

* **status**(ClientToolStatus)：`completed`、`error`、`timeout`、`client_gone`、`cancelled`。除 `completed` 外均为失败；`timeout` / `client_gone` / `cancelled` 为可重试错误（`data["retryable"]=True`）。默认值：`"completed"`。
* **content**(str | List[dict])：结果文本或内容块：`{"type": "text", "text": ...}`、`{"type": "image", "data_url": "data:image/png;base64,..."}` 或 `{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": ...}}`。图片经多模态路径交给模型。
* **is_error**(bool)：客户端执行了但失败。
* **data**(Any)：结构化结果，供 Rail 与宿主使用，不展示给模型。

`ClientToolResult.coerce(value)` 接受本类、字符串、内容块列表或 dict。dict 的键全部属于 `status` / `content` / `is_error` / `data` 且取值合法时按本结构解析；其余 dict 作为整体结果：以 JSON 展示给模型，`data` 保留原 dict，只有 `is_error` 为真或 `status` 为 `completed` 以外的状态时才算失败（如 `{"status": "ok", "sections": 3}` 为成功，`{"status": "error", "message": "..."}` 为失败且保留 `message`）。

### class ClientToolUnavailable

由 `build_request` 抛出，以 `message` 作为该调用的工具错误结束调用，不联系客户端。

## Auth

tool默认注册了ssl认证和参数认证，并支持自定义注册认证方式，详情请参考[auth.md](./auth/auth.md)。

## class LocalFunction

```python
class LocalFunction(card: ToolCard, func: Callable, *, render: Callable[[Any], str] | None = None)
```

本地函数工具类。

**参数**：

* **card**(ToolCard)：工具卡片。
* **func**(Callable)：要封装的本地可调用对象。不可为 None。
* **render**(Callable[[Any], str] | None, 可选)：结果渲染函数，替代默认的 `render_for_llm` 生成面向模型的文本，无需为定制渲染而子类化。默认值：`None`。

### invoke
```python
async def invoke(inputs: Input, **kwargs) -> Output
```

执行本地函数。


**参数**：

* **inputs**(Input)：输入数据。
* **\*\*kwargs**：可选参数，支持可选参数如下：
  * **skip_inputs_validate** (bool)：输入`inputs`是否基于`ToolCard`的`input_params`进行校验。默认为`True`。

**返回**：

**Output**，函数的返回值。

### stream
```python
async def stream(inputs: Input, **kwargs) -> AsyncIterator[Output]
```

流式执行本地函数。

仅支持生成器函数（Generator）或异步生成器函数（AsyncGenerator）。

**参数**：

* **inputs**(Input)：输入数据。
* **\*\*kwargs**：可选参数，支持可选参数如下：
  * **skip_inputs_validate** (bool)：输入`inputs`是否基于`ToolCard`的`input_params`进行校验。默认为`True`。

**返回**：

**AsyncIterator[Output]**，函数 yield 的值的异步迭代器。

## decrator @tool

```python
def tool(
    func: Optional[Callable] = None,
    *,
    name: Optional[str] = None,
    description: Optional[str] = None,
    input_params: Optional[Union[Dict[str, Any], Type[BaseModel]]] = None,
    card: Optional[ToolCard] = None,
    auto_extract: bool = True
) -> LocalFunction | Callable[[Callable], LocalFunction]
```

通用装饰器，用于将函数转换为 `LocalFunction` 工具。

支持多种用法：作为无参装饰器、带参装饰器或直接函数调用。

**参数**：

* **func**(Callable, 可选)：要装饰的函数。如果作为装饰器使用，此参数由 Python 自动传递。
* **name**(str, 可选)：覆盖函数的名称。如果未提供，默认使用函数名。
* **description**(str, 可选)：覆盖函数的描述。如果未提供，尝试从文档字符串或自动提取中获取。
* **input_params**(Dict[str, Any] | Type[BaseModel], 可选)：自定义参数模式。
* **card**(ToolCard, 可选)：预构建的 `ToolCard`。
* **auto_extract**(bool, 可选)：是否从函数签名自动提取参数模式。默认为 True。

**返回**：

**LocalFunction | Callable[[Callable], LocalFunction]**，返回 `LocalFunction` 实例（如果是直接调用或无参装饰器）或装饰器函数。

**样例**：

```python
>>> import asyncio
>>> from openjiuwen.core.foundation.tool import tool
>>> 
>>> # 1. 基本用法
>>> @tool
>>> def add(a: int, b: int) -> int:
>>>     """两数相加"""
>>>     return a + b
>>> 
>>> # 2. 自定义元数据
>>> @tool(name="multiply", description="Multiply two numbers")
>>> def mul(x: int, y: int) -> int:
>>>     return x * y
>>> 
>>> # 3. 异步流式工具
>>> @tool
>>> async def count_up(n: int):
>>>     for i in range(n):
>>>         yield i
>>> 
>>> async def main():
>>>     print(await add.invoke({"a": 1, "b": 2}))
>>>     print(await mul.invoke({"x": 3, "y": 4}))
>>>     async for i in count_up.stream({"n": 3}):
>>>         print(i, end=" ")
>>> 
>>> # 运行样例（实际使用时请在 async 环境中运行）
>>> # asyncio.run(main())
```

## class RestfulApiCard

```python
class RestfulApiCard(ToolCard)
```

RESTful API 工具卡片数据类，继承于`ToolCard`。

* **url**(str)：API 的路径，例如 `/api/v1/users`。必须是有效的 URL。
* **method**(Literal["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])：HTTP 方法。支持所有标准 HTTP 方法。默认为 "POST"。
* **headers**(Dict[str, Any], 可选)：请求头。
* **queries**(Dict[str, Any], 可选)：请求查询参数。
* **paths**(Dict[str, Any], 可选)：URL 路径参数。
* **timeout**(float, 可选)：请求超时时间（秒）。默认为 60.0。
* **max_response_byte_size**(int, 可选)：最大响应字节数。默认为 10MB。
* **name**(str, 可选)：工具名称。
* **description**(str, 可选)：工具描述。
* **input_params**(Dict[str, Any] | Type[BaseModel], 可选)：输入参数模式。

## class RestfulApi

```python
class RestfulApi(card: RestfulApiCard)
```

RESTful API 工具实现。通过 HTTP 请求调用远程 API。不支持 `stream` 方法。

**参数**：

* **card**(RestfulApiCard)：API 配置卡片。

### invoke
```python
async def invoke(inputs: Input, **kwargs) -> Output
```

发送 HTTP 请求并返回响应结果。

**参数**：

* **inputs**(Input)：输入参数，将根据配置映射到 Body、Query、Path、Header 或 Form。
* **\*\*kwargs**：可选参数，支持可选参数如下：
  * **skip_inputs_validate** (bool)：输入`inputs`是否基于`ToolCard`的`input_params`进行校验。默认为`True`。
  * **max_response_byte_size**（int): HTTP回复最大的字节长度，默认为`RestfulApiCard`的`max_response_byte_size`值。
  * **timeout**（float): HTTP请求的超时时间，单位为`s`，默认为`RestfulApiCard`的`timeout`值。

**返回**：

**Output**，API 响应的 JSON 数据。

**样例**：

```python
>>> from openjiuwen.core.foundation.tool import RestfulApi, RestfulApiCard
>>> 
>>> card = RestfulApiCard(
>>>     name="get_user",
>>>     description="Get user info",
>>>     url="https://api.example.com/users/{user_id}",
>>>     method="GET",
>>>     paths={"user_id": 123}, # 默认路径参数
>>>     input_params={"type": "object", "properties": {"user_id": {"type": "integer"}}}
>>> )
>>> 
>>> api_tool = RestfulApi(card)
>>> # await api_tool.invoke({"user_id": 456})
```

## class McpToolCard

```python
class McpToolCard(ToolCard)
```

MCP 工具卡片的数据类，继承于`ToolCard`。


* **server_name**(str)：MCP 服务器名称。
* **server_id**(str, 可选)：服务器 ID，默认为空字符串。
* **name**(str, 可选)：工具名称。
* **description**(str, 可选)：工具描述。
* **input_params**(Dict[str, Any] | Type[BaseModel], 可选)：输入参数模式。

## class MCPTool

```python
class MCPTool(mcp_client: Any, tool_info: McpToolCard)
```

MCP（Model Context Protocol）工具实现。包装 MCP 客户端以调用远程 MCP 工具。不支持 `stream` 方法。

**参数**：

* **mcp_client**(Any)：MCP 客户端实例（如 `McpClient` 子类）。
* **tool_info**(McpToolCard)：MCP 工具配置卡片。

### invoke
```python
async def invoke(inputs: Input, **kwargs) -> Output
```
调用 MCP 工具。

**参数**：

* **inputs**(Input)：输入参数，将根据配置映射到 Body、Query、Path 或 Header。
* **\*\*kwargs**：可选参数。

**返回**：

**Output**，包含结果的字典 `{"result": ...}`。

**样例**：

```python
>>> from openjiuwen.core.foundation.tool import MCPTool, McpToolCard
>>> from unittest.mock import AsyncMock
>>> 
>>> # 模拟 MCP Client
>>> mock_client = AsyncMock()
>>> mock_client.call_tool.return_value = "Result"
>>> 
>>> card = McpToolCard(
>>>     name="mcp_tool",
>>>     server_name="test_server"
>>> )
>>> 
>>> mcp_tool = MCPTool(mcp_client=mock_client, tool_info=card)
>>> # await mcp_tool.invoke({})
```