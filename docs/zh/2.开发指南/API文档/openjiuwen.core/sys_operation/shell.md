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

## 本地 Shell 子进程生命周期

以下规则适用于本地 `ShellOperation`，不替代沙箱提供方的生命周期管理。

POSIX 命令执行前解除继承的控制终端，同时保留执行组归属，不能通过 SDK 宿主的
`/dev/tty` 进行交互式提示。这不创建新的 OS session；显式 PTY 包装器仍可创建自己的终端。
无法枚举进程时保留归属并重新计算空组观察间隔，不将探测失败视作预览已退出；显式停止仍会尝试清理。

- 命令正常结束不代表它启动的后台服务已结束。预览服务会保留到显式停止会话或 SDK 进程退出，不因下一次工具调用、对话轮次结束而回收，也不设自动存活时间或内存上限。
- Linux 使用独立进程组及保留组身份的守护进程，Windows 使用 Job Object，并在命令开始执行前完成归属绑定。命令超时、取消、提前关闭正在执行的流式迭代器、后台启动失败时，清理对应执行组；Windows 显式停止使用非零退出码。
- `execute_cmd_background` 返回的 PID 仍是启动命令的 PID，不是内部守护进程的 PID。也可以使用普通 Shell 的后台语法，但必须重定向子进程的输入、输出和错误流，避免继承的管道阻止前台调用完成。
- 宿主应通过 `set_shell_session_id` / `reset_shell_session_id` 绑定和恢复会话上下文。`kill_shell_processes_for_session(sid)` 停止精确会话；`kill_shell_processes_for_session_tree(sid)` 还覆盖以 `sid + "_"` 开头的子会话。返回值是本次停止的执行组数量，不是所有后代 PID 的数量。停止期间已开始创建的进程也会被清理；之后的新调用可以继续使用同一会话。
- 无会话标识的执行仍由 SDK 进程持有，但不能按会话停止。SDK 正常退出会释放归属；Windows Job 句柄关闭、POSIX 守护进程检测到宿主管道关闭时，也会尝试清理仍被持有的子进程。

这不是安全沙箱。POSIX 程序主动通过 `setsid` / `setpgid` 离开进程组时，不能保证回收；需要约束此类程序时应使用容器或 cgroup。历史环境变量 `JW_START_NEW_SESSION=false` 仍可在 POSIX 显式关闭进程组归属，此时只保证直接命令的停止，不会向 SDK 所在的共享进程组发信号。手动注册外部创建的 PID 也不能补建完整的后代归属。
