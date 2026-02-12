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
    timeout: int = 300,
    environment: Optional[Dict[str, str]] = None,
    options: Optional[Dict[str, Any]] = None) -> ExecuteCodeResult
```

Asynchronously execute code.

**Parameters**:

* **code** (str): Source code string to execute.
* **language** (Literal['python', 'javascript'], optional): Programming language. Default value: "python".
* **timeout** (int, optional): Maximum execution time. Unit: seconds. Default value: 300.
* **environment** (Dict[str, str], optional): Custom environment variables.
* **options** (Dict[str, Any], optional): Extended configuration options. Supports the following keys:
    * **encoding** (str): Character encoding for the output stream. Default value: "utf-8".
    * **force_file** (bool): Whether to force code execution via a temporary file.
        * `True`: Always write the code to a temporary file and execute it.
        * `False` (default): Automatically select based on code length. Short code is executed via CLI; long code exceeding the limit is automatically switched to temporary file execution.

**Returns**:

**[ExecuteCodeResult](./result.md#class-executecoderesult)**, code execution result.

### abstractmethod async execute_code_stream

```python
abstractmethod async execute_code_stream(
    code: str,
    language: Literal['python', 'javascript'] = "python",
    timeout: int = 300,
    environment: Optional[Dict[str, str]] = None,
    options: Optional[Dict[str, Any]] = None) -> AsyncIterator[ExecuteCodeStreamResult]
```

Asynchronously execute code in streaming mode.

**Parameters**:

* **code** (str): Source code string to execute.
* **language** (Literal['python', 'javascript'], optional): Programming language. Default value: "python".
* **timeout** (int, optional): Maximum execution time. Unit: seconds. Default value: 300.
* **environment** (Dict[str, str], optional): Custom environment variables.
* **options** (Dict[str, Any], optional): Extended configuration options. Supports the following keys:
    * **encoding** (str): Character encoding for the output stream. Default value: "utf-8".
    * **chunk_size** (int): Chunk size for streaming output. Unit: bytes.
    * **force_file** (bool): Whether to force code execution via a temporary file.
        * `True`: Always write the code to a temporary file and execute it.
        * `False` (default): Automatically select based on code length. Short code is executed via CLI; long code exceeding the limit is automatically switched to temporary file execution.

**Returns**:

**AsyncIterator[[ExecuteCodeStreamResult](./result.md#class-executecodestreamresult)]**, streaming code execution result.
