# openjiuwen.core.runner.resources_manager.base

## alias AgentProvider

```python
AgentProvider = Callable[[AgentCard], Awaitable[BaseAgent]] | Callable[[AgentCard], BaseAgent]
```

Agent factory type definition.


## alias AgentGroupProvider

```python
AgentGroupProvider = Callable[[GroupCard], Awaitable[BaseGroup]] | Callable[[GroupCard], BaseGroup]
```

AgentGroup factory class type definition.



## alias WorkflowProvider

```python
WorkflowProvider = Callable[[WorkflowCard], Awaitable[Workflow]] | Callable[[WorkflowCard], Workflow]
```

Workflow factory class type definition.



## alias ModelProvider

```python
ModelProvider = Callable[[...], Awaitable[BaseModel]] | Callable[[...], BaseModel]
```

Model factory class type definition.


## alias Tag

```python
Tag = str
```

Tag type definition for categorizing and filtering resources.

## constant GLOBAL

```python
GLOBAL: Tag = "__global__"
```

Default tag constant for resources without explicit tags, used to mark resources as globally shared.


## enum TagMatchStrategy

Defines the strategy for matching multiple tags when querying or filtering resources.

* **ALL**: All-match strategy: resource must contain all specified tags.
* **ANY**: Partial-match strategy: resource must contain any specified tag.

## enum TagUpdateStrategy

Defines the strategy for updating resource tags.

* **MERGE**: Merge strategy: merge new tags with existing tags, removing duplicates.
* **REPLACE**: Replace strategy: completely replace all existing tags with new tags.

## class Ok

```python
class Ok(value: T)
```

Represents a successful operation result.


**Parameters**:

* **value** (T): The successful result value to be wrapped.

### is_ok
```python
def is_ok() -> bool
```

Check if the result indicates success.

**Returns**:

**bool**, always True, as this is an Ok instance.

### is_err
```python
def is_err() -> bool
```

Check if the result indicates an error.

**Returns**:

**bool**, always False, as this is an Ok instance.

### msg
```python
def msg() -> T
```

Get the success message/value.

**Returns**:

**T**, the wrapped success value.

## class Error

```python
class Error(error: E = None)
```

Represents a failed operation result.


**Parameters**:

* **error** (E, optional): The error value to be wrapped.

### is_ok
```python
def is_ok() -> bool
```

Check if the result indicates success.

**Returns**:

**bool**, always False, as this is an Error instance.

### is_err
```python
def is_err() -> bool
```

Check if the result indicates an error.

**Returns**:

**bool**, always True, as this is an Error instance.

### msg
```python
def msg() -> E
```

Get the error message/value.

**Returns**:

**E**, the wrapped error value.

### error
```python
def error() -> E
```

Get the error value (alternative method to msg()).

**Returns**:

**E**, the wrapped error value.

## alias Result

```python
Result: TypeAlias = Ok[T] | Error[E]
```

Operation result type alias using the Result pattern.

Represents a successful result (Ok[T]) or an error result (Error[E]). This pattern provides explicit error handling without exceptions, making error states part of the type system and API contract.

**Example**:

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
