# openjiuwen.core.foundation.tool

The `openjiuwen.core.foundation.tool` module provides abstract definitions and multiple implementations of tools (Tool), supporting local functions, RESTful APIs, and MCP (Model Context Protocol) tools. Tools are the bridge for Agents to interact with the external world.

## alias Input

```python
Input = TypeVar('Input', contravariant=True)
```

Generic type variable for tool input.

## alias Output

```python
Output = TypeVar('Output', contravariant=True)
```

Generic type variable for tool output.

## class ToolCard

```python
class ToolCard(BaseCard)
```

Tool card data class, inheriting from `BaseCard`, containing basic information and parameter definitions of the tool.

* **id** (str, optional): Unique identifier of the tool.
* **name** (str, optional): Tool name.
* **description** (str, optional): Tool description.
* **input_params** (Dict[str, Any] | Type[BaseModel], optional): Input parameter schema of the tool. Can be a JSON Schema dictionary or Pydantic model class. Defaults to empty dictionary, indicating no input parameter schema, accepting any input.

### tool_info
```python
def tool_info() -> ToolInfo
```

Convert the tool card to a `ToolInfo` object for LLM invocation.

**Returns**:

**ToolInfo**, tool information recognizable by LLM.

## class Tool

```python
class Tool(card: ToolCard)
```

Abstract base class for tools.

Defines the basic behavior of tools, and all specific tool implementations should inherit from this class.

**Parameters**:

* **card** (ToolCard): Tool card that defines the behavior and parameters of the tool. Cannot be None, and must contain id.

### card
```python
@property
def card() -> ToolCard
```

Get the card corresponding to the tool.

**Returns**:

**ToolCard**, the configuration card of the tool.

### invoke

```python
async def invoke(inputs: Input, **kwargs) -> Output
```
Execute the tool and return the complete result.

This is an abstract method, and subclasses must implement the specific execution logic.

**Parameters**:

* **inputs** (Input): Structured input data conforming to the tool's input schema.
* **\*\*kwargs**: Additional execution parameters, such as timeout, retry strategy, or tool-specific options.

**Returns**:

**Output**, the complete result of tool execution.

### stream

```python
async def stream(inputs: Input, **kwargs) -> AsyncIterator[Output]
```

Execute the tool and return results in a streaming manner.

This is an abstract method that supports partial result returns for long-running operations.

**Parameters**:

* **inputs** (Input): Structured input data conforming to the tool's input schema.
* **\*\*kwargs**: Additional streaming execution parameters.

**Returns**:

**AsyncIterator[Output]**, an iterator of incremental results during tool execution.

## class LocalFunction

```python
class LocalFunction(card: ToolCard, func: Callable)
```

Local function tool class.

**Parameters**:

* **card** (ToolCard): Tool card.
* **func** (Callable): Local callable object to be wrapped. Cannot be None.

### invoke
```python
async def invoke(inputs: Input, **kwargs) -> Output
```

Execute a local function.


**Parameters**:

* **inputs** (Input): Input data.
* **\*\*kwargs**: Optional parameters, supporting the following optional parameters:
  * **skip_inputs_validate** (bool): Whether to validate input `inputs` based on `ToolCard`'s `input_params`. Default is `True`.

**Returns**:

**Output**, the return value of the function.

### stream
```python
async def stream(inputs: Input, **kwargs) -> AsyncIterator[Output]
```

Execute a local function in a streaming manner.

Only supports generator functions (Generator) or async generator functions (AsyncGenerator).

**Parameters**:

* **inputs** (Input): Input data.
* **\*\*kwargs**: Optional parameters, supporting the following optional parameters:
  * **skip_inputs_validate** (bool): Whether to validate input `inputs` based on `ToolCard`'s `input_params`. Default is `True`.

**Returns**:

**AsyncIterator[Output]**, an async iterator of values yielded by the function.

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

Universal decorator for converting functions to `LocalFunction` tools.

Supports multiple usage patterns: as a parameterless decorator, parameterized decorator, or direct function call.

**Parameters**:

* **func** (Callable, optional): Function to be decorated. If used as a decorator, this parameter is automatically passed by Python.
* **name** (str, optional): Override the function name. If not provided, the function name is used by default.
* **description** (str, optional): Override the function description. If not provided, attempts to obtain from docstring or auto-extraction.
* **input_params** (Dict[str, Any] | Type[BaseModel], optional): Custom parameter schema.
* **card** (ToolCard, optional): Pre-built `ToolCard`.
* **auto_extract** (bool, optional): Whether to automatically extract parameter schema from function signature. Default is True.

**Returns**:

**LocalFunction | Callable[[Callable], LocalFunction]**, returns a `LocalFunction` instance (if direct call or parameterless decorator) or decorator function.

**Example**:

```python
>>> import asyncio
>>> from openjiuwen.core.foundation.tool import tool
>>> 
>>> # 1. Basic usage
>>> @tool
>>> def add(a: int, b: int) -> int:
>>>     """Add two numbers"""
>>>     return a + b
>>> 
>>> # 2. Custom metadata
>>> @tool(name="multiply", description="Multiply two numbers")
>>> def mul(x: int, y: int) -> int:
>>>     return x * y
>>> 
>>> # 3. Async streaming tool
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
>>> # Run example (please run in async environment for actual use)
>>> # asyncio.run(main())
```

## class RestfulApiCard

```python
class RestfulApiCard(ToolCard)
```

RESTful API tool card data class, inheriting from `ToolCard`.

* **url** (str): API path, e.g., `/api/v1/users`. Must be a valid URL.
* **method** (Literal["POST", "GET"]): HTTP method, only supports POST or GET. Default is "POST".
* **headers** (Dict[str, Any], optional): Request headers.
* **queries** (Dict[str, Any], optional): Request query parameters.
* **paths** (Dict[str, Any], optional): URL path parameters.
* **timeout** (float, optional): Request timeout in seconds. Default is 60.0.
* **max_response_byte_size** (int, optional): Maximum response byte size. Default is 10MB.
* **name** (str, optional): Tool name.
* **description** (str, optional): Tool description.
* **input_params** (Dict[str, Any] | Type[BaseModel], optional): Input parameter schema.

## class RestfulApi

```python
class RestfulApi(card: RestfulApiCard)
```

RESTful API tool implementation. Calls remote APIs through HTTP requests. Does not support the `stream` method.

**Parameters**:

* **card** (RestfulApiCard): API configuration card.

### invoke
```python
async def invoke(inputs: Input, **kwargs) -> Output
```

Send HTTP request and return response result.

**Parameters**:

* **inputs** (Input): Input parameters, which will be mapped to Body, Query, Path, or Header according to configuration.
* **\*\*kwargs**: Optional parameters, supporting the following optional parameters:
  * **skip_inputs_validate** (bool): Whether to validate input `inputs` based on `ToolCard`'s `input_params`. Default is `True`.
  * **max_response_byte_size** (int): Maximum byte length of HTTP response, default is `RestfulApiCard`'s `max_response_byte_size` value.
  * **timeout** (float): HTTP request timeout in seconds, default is `RestfulApiCard`'s `timeout` value.

**Returns**:

**Output**, JSON data of API response.

**Example**:

```python
>>> from openjiuwen.core.foundation.tool import RestfulApi, RestfulApiCard
>>> 
>>> card = RestfulApiCard(
>>>     name="get_user",
>>>     description="Get user info",
>>>     url="https://api.example.com/users/{user_id}",
>>>     method="GET",
>>>     paths={"user_id": 123}, # Default path parameters
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

MCP tool card data class, inheriting from `ToolCard`.


* **server_name** (str): MCP server name.
* **server_id** (str, optional): Server ID, default is empty string.
* **name** (str, optional): Tool name.
* **description** (str, optional): Tool description.
* **input_params** (Dict[str, Any] | Type[BaseModel], optional): Input parameter schema.

## class MCPTool

```python
class MCPTool(mcp_client: Any, tool_info: McpToolCard)
```

MCP (Model Context Protocol) tool implementation. Wraps MCP client to call remote MCP tools. Does not support the `stream` method.

**Parameters**:

* **mcp_client** (Any): MCP client instance (e.g., `McpClient` subclass).
* **tool_info** (McpToolCard): MCP tool configuration card.

### invoke
```python
async def invoke(inputs: Input, **kwargs) -> Output
```
Call MCP tool.

**Parameters**:

* **inputs** (Input): Input parameters, which will be mapped to Body, Query, Path, or Header according to configuration.
* **\*\*kwargs**: Optional parameters.

**Returns**:

**Output**, dictionary containing results `{"result": ...}`.

**Example**:

```python
>>> from openjiuwen.core.foundation.tool import MCPTool, McpToolCard
>>> from unittest.mock import AsyncMock
>>> 
>>> # Mock MCP Client
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
