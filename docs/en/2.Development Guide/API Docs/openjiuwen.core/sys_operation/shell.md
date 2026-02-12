# openjiuwen.core.sys_operation.shell

## class BaseShellOperation

```python
class BaseShellOperation()
```

`BaseShellOperation` is the abstract base class providing Shell command execution functionality, inheriting from [BaseOperation](./base.md#class-baseoperation).

### abstractmethod async execute_cmd

```python
abstractmethod async execute_cmd(
    command: str,
    cwd: Optional[str] = None,
    timeout: Optional[int] = 300,
    environment: Optional[Dict[str, str]] = None, 
    options: Optional[Dict[str, Any]] = None) -> ExecuteCmdResult
```

Asynchronously execute Shell command.

**Parameters**:

* **command** (str): Command to execute.
* **cwd** (str, optional): Working directory for command execution. Default value: `None`. When set to `None`, the current directory is treated as the working directory.
* **timeout** (int, optional): Command execution timeout. Unit: seconds. Default value: `300`.
* **environment** (Dict[str, str], optional): Custom environment variables.
* **options** (Dict[str, Any], optional): Extended configuration options. Supports the following keys:
  * **encoding** (str, optional): Character encoding for the output stream. Default value: `"utf-8"`.

**Returns**:

**[ExecuteCmdResult](./result.md#class-executecmdresult)**, command execution result.

### abstractmethod async execute_cmd_stream

```python
abstractmethod async execute_cmd_stream(
    command: str,
    cwd: Optional[str] = None,
    timeout: Optional[int] = 300,
    environment: Optional[Dict[str, str]] = None,
    options: Optional[Dict[str, Any]] = None) -> AsyncIterator[ExecuteCmdStreamResult]
```

Asynchronously execute Shell command in streaming mode.

**Parameters**:

* **command** (str): Command to execute.
* **cwd** (str, optional): Working directory for command execution. Default value: `None`. When set to `None`, the current directory is treated as the working directory.
* **timeout** (int, optional): Command execution timeout. Unit: seconds. Default value: `300`.
* **environment** (Dict[str, str], optional): Custom environment variables.
* **options** (Dict[str, Any], optional): Extended configuration options. Supports the following keys:
  * **encoding** (str, optional): Character encoding for the output stream. Default value: `"utf-8"`.
  * **chunk_size** (int, optional): Chunk size for streaming output. Unit: bytes. Default value: `1024`.

**Returns**:

**AsyncIterator[[ExecuteCmdStreamResult](./result.md#class-executecmdstreamresult)]**, streaming command execution result.
