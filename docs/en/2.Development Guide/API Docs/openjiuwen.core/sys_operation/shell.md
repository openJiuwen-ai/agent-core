# openjiuwen.core.sys_operation.shell

## class BaseShellOperation

```python
class BaseShellOperation()
```

`BaseShellOperation` is the abstract base class providing Shell command execution functionality, inheriting from [BaseOperation](./base.md#class-baseoperation).

### abstractmethod async execute_cmd

The local implementation honors the supplied `timeout` without a secondary
`JW_EXECUTE_CMD_MAX_TIMEOUT` cap. Omitted or `None` values use 300 seconds;
callers are responsible for validating and limiting the execution budget.

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

The local implementation uses the same timeout rules as `execute_cmd` and
preserves the caller's execution budget.

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

## Local Shell Process Lifetime

These rules apply to local `ShellOperation`, not to sandbox-provider lifecycle management.

- Successful command completion does not imply that its background services have exited. Preview services remain until an explicit session stop or SDK process exit. Another tool call or a completed conversation turn does not stop them. No automatic lifetime or memory limit is imposed.
- Linux uses a dedicated process group with a guardian retaining its identity. Windows uses a Job Object assigned before the command starts executing. Timeout, cancellation, early closure of an active stream, and background startup failure clean up the owned execution. Explicit Windows stops use a nonzero exit code.
- The PID returned by `execute_cmd_background` remains the command PID, not the internal guardian PID. Ordinary shell background syntax is also supported, but redirect the child's standard input, output, and error to prevent inherited pipes from keeping the foreground call open.
- Hosts bind and restore session context with `set_shell_session_id` / `reset_shell_session_id`. `kill_shell_processes_for_session(sid)` targets an exact session; `kill_shell_processes_for_session_tree(sid)` also targets child keys beginning with `sid + "_"`. The return value counts stopped executions, not individual descendant PIDs. A stop also covers process creation already in progress; a later invocation can reuse the same session.
- Executions without a session ID are still owned by the SDK process but cannot be stopped by session. Normal SDK exit releases ownership. Windows Job handle closure and POSIX guardian detection of a closed host pipe also attempt to clean up remaining owned processes.

This is not a security sandbox. POSIX programs that deliberately leave their group using `setsid` / `setpgid` are outside this containment; use a container or cgroup when they must be contained. The historical `JW_START_NEW_SESSION=false` setting still explicitly disables POSIX group ownership. In that mode, only direct-command termination is guaranteed; the SDK's shared process group is never signalled. Manually registering an externally created PID cannot retroactively establish full descendant ownership either.
