# openjiuwen.core.sys_operation.shell

## class BaseShellOperation

```python
class BaseShellOperation()
```

`BaseShellOperation`是提供Shell命令执行功能的抽象基类，继承于[BaseOperation](./base.md#class-baseoperation)。

### abstractmethod async execute_cmd

```python
abstractmethod async execute_cmd(
    command: str,
    cwd: Optional[str] = None,
    timeout: Optional[int] = 300,
    environment: Optional[Dict[str, str]] = None, 
    options: Optional[Dict[str, Any]] = None) -> ExecuteCmdResult
```

异步执行Shell命令。

本地实现直接使用传入的 `timeout`，不再通过 `JW_EXECUTE_CMD_MAX_TIMEOUT` 施加第二层上限。
省略或传入 `None` 时使用 300 秒；调用方负责校验和限制执行预算。

**参数**：

* **command**(str)：要执行的命令。
* **cwd**(str, 可选)：命令执行的工作目录。默认值：`None`。设置为`None`，则认为当前目录为工作目录。
* **timeout**(int, 可选)：命令执行超时时间。单位：秒。默认值：`300`。
* **environment**(Dict[str, str], 可选)：自定义环境变量。
* **options**(Dict[str, Any], 可选)：扩展配置选项。支持以下键值：
  * **encoding**(str, 可选)：输出流的字符编码。默认值：`"utf-8"`。

**返回**：

**[ExecuteCmdResult](./result.md#class-executecmdresult)**，命令执行结果。

### abstractmethod async execute_cmd_stream

```python
abstractmethod async execute_cmd_stream(
    command: str,
    cwd: Optional[str] = None,
    timeout: Optional[int] = 300,
    environment: Optional[Dict[str, str]] = None,
    options: Optional[Dict[str, Any]] = None) -> AsyncIterator[ExecuteCmdStreamResult]
```

异步流式执行Shell命令。

本地实现与 `execute_cmd` 使用相同的超时规则，直接使用调用方传入的执行预算。

**参数**：

* **command**(str)：要执行的命令。
* **cwd**(str, 可选)：命令执行的工作目录。默认值：`None`。设置为`None`，则认为当前目录为工作目录。
* **timeout**(int, 可选)：命令执行超时时间。单位：秒。默认值：`300`。
* **environment**(Dict[str, str], 可选)：自定义环境变量。
* **options**(Dict[str, Any], 可选)：扩展配置选项。支持以下键值：
  * **encoding**(str, 可选)：输出流的字符编码。默认值：`"utf-8"`。
  * **chunk_size**(int, 可选)：流式输出的分块大小。单位：字节。默认值：`1024`。

**返回**：

**AsyncIterator[[ExecuteCmdStreamResult](./result.md#class-executecmdstreamresult)]**，流式命令执行结果。
