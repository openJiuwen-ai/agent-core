# openjiuwen.core.sys_operation.code

## class BaseCodeOperation

```python
class BaseCodeOperation()
```

`BaseCodeOperation` is the abstract base class providing code execution functionality, inheriting from [BaseOperation](./base.md#class-baseoperation).

### abstractmethod async execute_code

```python
abstractmethod async execute_code(
    code: str,
    language: Literal['python', 'javascript'] = "python",
    time_out: int = 300,
    environment: Optional[Dict[str, str]] = None,
    options: Optional[Dict[str, Any]] = None) -> ExecuteCodeResult
```

Asynchronously execute code.

**Parameters**:

* **code** (str): Source code string to execute.
* **language** (Literal['python', 'javascript'], optional): Programming language. Default value: "python".
* **time_out** (int, optional): Maximum execution time. Unit: seconds. Default value: 300.
* **environment** (Dict[str, str], optional): Custom environment variables.
* **options** (Dict[str, Any], optional): Extended configuration options.

**Returns**:

**[ExecuteCodeResult](./result.md#class-executecoderesult)**, code execution result.

### abstractmethod aysnc execute_code_stream

```python
abstractmethod async execute_code_stream(
    code: str,
    language: Literal['python', 'javascript'] = "python",
    time_out: int = 300,
    environment: Optional[Dict[str, str]] = None,
    options: Optional[Dict[str, Any]] = None) -> 		 AsyncIterator[ExecuteCodeStreamResult]
```

Asynchronously execute code in streaming mode.

**Parameters**:

* **code** (str): Source code string to execute.
* **language** (Literal['python', 'javascript'], optional): Programming language. Default value: "python".
* **time_out** (int, optional): Maximum execution time. Unit: seconds. Default value: 300.
* **environment** (Dict[str, str], optional): Custom environment variables.
* **options** (Dict[str, Any], optional): Extended configuration options.

**Returns**:

**AsyncIterator[[ExecuteCodeStreamResult](./result.md#class-executecodestreamresult)]**, streaming code execution result.
