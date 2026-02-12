# openjiuwen.core.runner.resources_manager.base

## alias AgentProvider

```python
AgentProvider = Callable[[AgentCard], Awaitable[BaseAgent]] | Callable[[AgentCard], BaseAgent]
```

Agent工厂类型定义。


## alias AgentGroupProvider

```python
AgentGroupProvider = Callable[[GroupCard], Awaitable[BaseGroup]] | Callable[[GroupCard], BaseGroup]
```

AgentGroup工厂类类型定义。


## alias WorkflowProvider

```python
WorkflowProvider = Callable[[WorkflowCard], Awaitable[Workflow]] | Callable[[WorkflowCard], Workflow]
```

工作流工厂类类型定义。



## alias ModelProvider

```python
ModelProvider = Callable[[...], Awaitable[BaseModel]] | Callable[[...], BaseModel]
```

模型工厂类类型定义。


## alias Tag

```python
Tag = str
```

用于分类和过滤资源的标签类型定义。

## constant GLOBAL

```python
GLOBAL: Tag = "__global__"
```

没有显式标签资源的默认标签常量，用于标记资源全局共享。


## enum TagMatchStrategy

定义了查询或过滤资源时匹配多个标签的策略。

* **ALL**：全匹配策略：资源必须包含所有指定的标签。
* **ANY**：部分匹配策略：资源必须包含任何指定的标签。

## enum TagUpdateStrategy

定义了更新资源标签的策略。

* **MERGE**：合并策略：将新标签与现有标签合并，去除重复项。
* **REPLACE**：替换策略：用新标签完全替换所有现有标签。

## class Ok

```python
class Ok(value: T)
```

表示成功的操作结果。


**参数**：

* **value**(T)：要封装的成功结果值。

### is_ok

```python
def is_ok() -> bool
```

检查结果是否表示成功。

**返回**：

**bool**，始终为True，因为这是Ok实例。

### is_err

```python
def is_err() -> bool
```

检查结果是否表示错误。

**返回**：

**bool**，始终为False，因为这是Ok实例。

### msg

```python
def msg() -> T
```

获取成功消息/值。

**返回**：

**T**，封装的成功值。

## class Error

```python
class Error(error: E = None)
```

表示失败的操作结果。


**参数**：

* **error**(E, 可选)：要封装的错误值。

### is_ok

```python
def is_ok() -> bool
```

检查结果是否表示成功。

**返回**：

**bool**，始终为False，因为这是Error实例。

### is_err

```python
def is_err() -> bool
```

检查结果是否表示错误。

**返回**：

**bool**，始终为True，因为这是Error实例。

### msg

```python
def msg() -> E
```

获取错误消息/值。

**返回**：

**E**，封装的错误值。

### error

```python
def error() -> E
```

获取错误值（msg()的替代方法）。

**返回**：

**E**，封装的错误值。

## alias Result

```python
Result: TypeAlias = Ok[T] | Error[E]
```

使用结果模式的操作结果类型别名。

表示成功结果（Ok[T]）或错误结果（Error[E]）。此模式提供显式错误处理而无需异常，使错误状态成为类型系统和API契约的一部分。

**样例**：

```python
>>> from openjiuwen.core.runner.resources_manager.base import Ok, Error, Result
>>> 
>>> def divide(a: int, b: int) -> Result[float, str]:
>>>     if b == 0:
>>>         return Error("Division by zero")
>>>     return Ok(a / b)
>>> 
>>> result = divide(10, 2)
>>> if result.is_ok():
>>>     print(f"Result: {result.msg()}")
>>> else:
>>>     print(f"Error: {result.msg()}")
```