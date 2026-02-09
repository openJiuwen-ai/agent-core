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

**参数**：

* **command**(str)：要执行的命令。
* **cwd**(str, 可选)：命令执行的工作目录。默认值：当前目录。
* **timeout**(int, 可选)：命令执行超时时间。单位：秒。默认值：300。
* **environment**(Dict[str, str], 可选)：自定义环境变量。
* **options**(Dict[str, Any], 可选)：扩展配置选项。

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

**参数**：

* **command**(str)：要执行的命令。
* **cwd**(str, 可选)：命令执行的工作目录。默认值：当前目录。
* **timeout**(int, 可选)：命令执行超时时间。单位：秒。默认值：300。
* **environment**(Dict[str, str], 可选)：自定义环境变量。
* **options**(Dict[str, Any], 可选)：扩展配置选项。

**返回**：

**AsyncIterator[[ExecuteCmdStreamResult](./result.md#class-executecmdstreamresult)]**，流式命令执行结果。
