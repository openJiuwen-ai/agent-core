# openjiuwen.core.sys_operation.code

## class BaseCodeOperation

```python
class BaseCodeOperation()
```

`BaseCodeOperation`是提供代码执行功能的抽象基类，继承于[BaseOperation](./base.md#class-baseoperation)。

### abstractmethod async execute_code

```python
abstractmethod async execute_code(
    code: str,
    language: Literal['python', 'javascript'] = "python",
    timeout: int = 300,
    environment: Optional[Dict[str, str]] = None,
    options: Optional[Dict[str, Any]] = None) -> ExecuteCodeResult
```

异步执行代码。

**参数**：

* **code**(str)：要执行的源代码字符串。
* **language**(Literal['python', 'javascript'], 可选)：编程语言。默认值：`"python"`。
* **timeout**(int, 可选)：最大执行时间。单位：秒。默认值：`300`。
* **environment**(Dict[str, str], 可选)：自定义环境变量。
* **options**(Dict[str, Any], 可选)：扩展配置选项。支持以下键值：
    * **encoding**(str, 可选)：输出流的字符编码。默认值：`"utf-8"`。
    * **force_file**(bool, 可选)：是否强制以文件方式执行。默认值：`False`。
        * `True`：始终将代码写入临时文件并执行。
        * `False`：根据代码长度自动选择。短代码通过命令行 CLI 执行，超过限制的长代码会自动切换为临时文件执行方式。

**返回**：

**[ExecuteCodeResult](./result.md#class-executecoderesult)**，代码执行结果。

### abstractmethod async execute_code_stream

```python
abstractmethod async execute_code_stream(
    code: str,
    language: Literal['python', 'javascript'] = "python",
    timeout: int = 300,
    environment: Optional[Dict[str, str]] = None,
    options: Optional[Dict[str, Any]] = None) -> AsyncIterator[ExecuteCodeStreamResult]
```

异步流式执行代码。

**参数**：

* **code**(str)：要执行的源代码字符串。
* **language**(Literal['python', 'javascript'], 可选)：编程语言。默认值：`"python"`。
* **timeout**(int, 可选)：最大执行时间。单位：秒。默认值：`300`。
* **environment**(Dict[str, str], 可选)：自定义环境变量。
* **options**(Dict[str, Any], 可选)：扩展配置选项。支持以下键值：
    * **encoding**(str, 可选)：输出流的字符编码。默认值：`"utf-8"`。
    * **chunk_size**(int, 可选)：流式输出的分块大小。单位：字节。默认值：`1024`。
    * **force_file**(bool, 可选)：是否强制以文件方式执行。默认值：`False`。
        * `True`：始终将代码写入临时文件并执行。
        * `False`：根据代码长度自动选择。短代码通过命令行 CLI 执行，超过限制的长代码会自动切换为临时文件执行方式。

**返回**：

**AsyncIterator[[ExecuteCodeStreamResult](./result.md#class-executecodestreamresult)]**，流式代码执行结果。