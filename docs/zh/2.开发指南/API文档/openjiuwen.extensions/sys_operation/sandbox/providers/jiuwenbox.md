# openjiuwen.extensions.sys_operation.sandbox.providers.jiuwenbox

JiuwenBox 沙箱 Provider 将 SysOperation 的文件系统、Shell 和代码执行接口适配到 JiuwenBox HTTP API。模块导入后，会以 `sandbox_type="jiuwenbox"` 注册 `fs`、`shell` 和 `code` 三类 Provider。

基础接口约定参见 [BaseFSProvider、BaseShellProvider 和 BaseCodeProvider](../../../../openjiuwen.core/sys_operation/sandbox/providers/base_provider.md)。各方法的返回类型定义参见 [sys_operation.result](../../../../openjiuwen.core/sys_operation/result.md)。

## 配置示例

实际执行文件、Shell 或代码操作前，需要先启动一个可访问的 JiuwenBox 服务。通常不需要直接实例化 Provider，而是通过 `SysOperationCard` 和 Gateway 选择 JiuwenBox：

```python
from openjiuwen.core.sys_operation import OperationMode, SandboxGatewayConfig, SysOperationCard
from openjiuwen.core.sys_operation.config import (
    ContainerScope,
    PreDeployLauncherConfig,
    SandboxIsolationConfig,
)

card = SysOperationCard(
    id="jiuwenbox_sysop",
    mode=OperationMode.SANDBOX,
    gateway_config=SandboxGatewayConfig(
        isolation=SandboxIsolationConfig(
            container_scope=ContainerScope.CUSTOM,
            custom_id="my-sandbox",
        ),
        launcher_config=PreDeployLauncherConfig(
            base_url="http://127.0.0.1:8321",
            sandbox_type="jiuwenbox",
            idle_ttl_seconds=600,
        ),
        timeout_seconds=30,
    ),
)
```

JiuwenBox 服务启用 Bearer 鉴权时，可以设置环境变量 `JIUWENBOX_API_TOKEN`。也可以通过 `JIUWENBOX_SANDBOX_ID` 指向已有沙箱；否则 Provider 会按 `base_url` 和 `isolation_key` 延迟创建并复用沙箱。

`launcher_config.extra_params` 支持以下 JiuwenBox 专用配置：

| 参数 | 类型 | 说明 |
|---|---|---|
| `sandbox_id` | str | 指向已有沙箱；运行时会重新读取，支持调用方更新。 |
| `policy` | dict | 创建或重建沙箱时传给 JiuwenBox 的安全策略。 |
| `policy_mode` | str | 创建或重建沙箱时使用的策略模式。 |
| `idle_check_interval` | int | JiuwenBox 服务端空闲检查间隔；`idle_ttl_seconds` 用作空闲超时。 |
| `excluded_commands` | List[str] | 使用 `fnmatch` 匹配 Shell 命令或代码首行；命中后改在 SDK 所在主机执行。 |
| `fallback_on_failure` | bool | 沙箱执行管线失败后是否降级到 SDK 所在主机执行。默认值：`False`。 |
| `preserve_files_upload` | List[dict] | 重建后重新上传的文件或目录。每项包含 `host_path`、`sandbox_path`，目录可设置 `kind="directory"`。 |
| `lifecycle_hook` | Callable[[str, dict], None] | 同步生命周期回调，接收事件名及上下文副本。 |

> **安全提示**：`excluded_commands` 和 `fallback_on_failure=True` 会在 SDK 所在主机执行命令或代码，从而绕过沙箱隔离。仅应在明确接受该行为的受控环境中启用。

沙箱不存在并返回匹配的 HTTP 404 时，Provider 会自动重建并重试。环境变量 `JIUWENBOX_SANDBOX_RECREATE_RETRIES` 控制重建重试次数，默认值为 `3`，设为 `0` 可关闭重试。

## function build_jiuwenbox_http_client

```python
build_jiuwenbox_http_client(
    base_url: str,
    timeout_seconds: float = 30.0,
    api_token: str | None = None,
) -> httpx.Client
```

创建访问 JiuwenBox HTTP API 的同步 `httpx.Client`。`base_url` 末尾的 `/` 会被移除。`api_token` 非空时设置 Bearer 鉴权头；未传入时读取 `JIUWENBOX_API_TOKEN`。

调用方负责关闭返回的 Client，也可以将其作为上下文管理器使用。

## function build_jiuwenbox_shared_scope_key

```python
build_jiuwenbox_shared_scope_key(base_url: str, isolation_key: str) -> str
```

生成 Provider 的进程内共享缓存键，格式为 `{去除末尾斜杠的 base_url}|{isolation_key}`。

## function clear_jiuwenbox_shared_sandbox

```python
clear_jiuwenbox_shared_sandbox(base_url: str) -> list[str]
```

清除指定 `base_url` 对应的进程内沙箱 ID 缓存，返回去重后的已移除 ID。该函数只清理本地缓存，不会删除远端沙箱。

## async function force_recreate_jiuwenbox_sandbox

```python
async force_recreate_jiuwenbox_sandbox(
    base_url: str,
    *,
    shared_key: str | None = None,
    policy: dict | None = None,
    policy_mode: str | None = None,
    timeout_seconds: float = 30.0,
    preserve_files_upload: Any = None,
    extra_stale_sandbox_ids: Sequence[str] | None = None,
    lifecycle_hook: Callable[[str, dict], None] | None = None,
    reason: str = "sandbox_lost",
) -> str
```

创建新沙箱、更新共享缓存，并尝试删除旧沙箱。新沙箱创建成功后才会删除旧沙箱，避免创建失败时提前丢失原沙箱。

**参数**：

- **base_url**(str)：JiuwenBox 服务地址。
- **shared_key**(str, 可选)：由 `base_url` 和隔离键组成的缓存键。未设置时会清理该地址下的全部缓存，主要用于兼容单租户调用。
- **policy**(dict, 可选)：新沙箱的安全策略。
- **policy_mode**(str, 可选)：策略模式。
- **timeout_seconds**(float)：HTTP 超时秒数。默认值：`30.0`。
- **preserve_files_upload**(Any, 可选)：创建后重新上传的本地文件或目录列表。
- **extra_stale_sandbox_ids**(Sequence[str], 可选)：除缓存值外还需要清理的旧沙箱 ID。
- **lifecycle_hook**(Callable, 可选)：同步生命周期回调。重建时触发 `before_recreate` 和 `after_recreate`。
- **reason**(str)：写入回调上下文的重建原因。默认值：`"sandbox_lost"`。

**返回**：新沙箱 ID。

## async function delete_jiuwenbox_sandbox

```python
async delete_jiuwenbox_sandbox(
    *,
    sandbox_id: str | None = None,
    shared_key: str | None = None,
    delete_all: bool = False,
    reason: str = "teardown",
    timeout_seconds: float = 30.0,
) -> list[str]
```

删除进程内缓存所关联的远端沙箱。默认只处理 `shared_key` 或已缓存的 `sandbox_id`；`delete_all=True` 会排空当前进程的全部 JiuwenBox 沙箱缓存并逐个删除远端沙箱。

删除前后分别触发缓存的 `before_delete` 和 `after_delete` 生命周期回调。远端删除失败时记录警告并继续处理其他沙箱；返回值只包含成功删除的沙箱 ID。

## class JiuwenBoxFSProvider

```python
class JiuwenBoxFSProvider(BaseFSProvider)
```

JiuwenBox 文件系统 Provider，注册名为 `("jiuwenbox", "fs")`。

### 构造函数

```python
JiuwenBoxFSProvider(
    endpoint: SandboxEndpoint,
    config: SandboxGatewayConfig | None = None,
)
```

- **endpoint**：包含 `base_url`、可选 `sandbox_id` 和可选 `isolation_key` 的沙箱端点。
- **config**：Gateway 配置，用于读取超时、Launcher 和 `extra_params`。

### 文件方法

```python
async read_file(path: str, mode: str = "text", **kwargs) -> ReadFileResult
async write_file(path: str, content: str | bytes, mode: str = "text", **kwargs) -> WriteFileResult

async list_files(
    path: str,
    *,
    recursive: bool = False,
    max_depth: int | None = None,
    sort_by: str = "name",
    sort_descending: bool = False,
    file_types: list[str] | None = None,
    **kwargs,
) -> ListFilesResult

async list_directories(
    path: str,
    *,
    recursive: bool = False,
    max_depth: int | None = None,
    sort_by: str = "name",
    sort_descending: bool = False,
    **kwargs,
) -> ListDirsResult

async search_files(
    path: str,
    pattern: str,
    exclude_patterns: list[str] | None = None,
) -> SearchFilesResult
```

`read_file` 支持 `mode="text"` 或 `mode="bytes"`。文本模式可通过 `head`、`tail` 或 `line_range=(start, end)` 选择行，这三个参数不能同时使用；字节模式不支持行选择。`write_file` 可通过 `append`、`prepend_newline` 和 `append_newline` 控制写入方式。

列表结果可以按 `name`、`modified_time` 或 `size` 排序。`list_files` 还支持使用 `file_types` 过滤 JiuwenBox 返回的文件类型。

### 流式与传输方法

```python
async read_file_stream(
    path: str,
    *,
    mode: str = "text",
    head: int | None = None,
    tail: int | None = None,
    line_range: tuple[int, int] | None = None,
    encoding: str = "utf-8",
    chunk_size: int = 8192,
    **kwargs,
) -> AsyncIterator[ReadFileStreamResult]

async upload_file(
    local_path: str,
    target_path: str,
    *,
    overwrite: bool = False,
    create_parent_dirs: bool = True,
    preserve_permissions: bool = True,
    chunk_size: int = 0,
    **kwargs,
) -> UploadFileResult

async upload_file_stream(
    local_path: str,
    target_path: str,
    *,
    overwrite: bool = False,
    chunk_size: int = 1048576,
    **kwargs,
) -> AsyncIterator[UploadFileStreamResult]

async download_file(
    source_path: str,
    local_path: str,
    *,
    overwrite: bool = False,
    create_parent_dirs: bool = True,
    preserve_permissions: bool = True,
    chunk_size: int = 0,
    **kwargs,
) -> DownloadFileResult

async download_file_stream(
    source_path: str,
    local_path: str,
    *,
    overwrite: bool = False,
    chunk_size: int = 1048576,
    **kwargs,
) -> AsyncIterator[DownloadFileStreamResult]
```

上传从 SDK 所在主机读取 `local_path`，下载则向主机的 `local_path` 写入内容。当前实现的上传、下载和 Shell/代码“流式”方法会先完成整个远端操作，再生成流式结果，并非 JiuwenBox HTTP 响应的实时透传。

## class JiuwenBoxShellProvider

```python
class JiuwenBoxShellProvider(BaseShellProvider)
```

JiuwenBox Shell Provider，注册名为 `("jiuwenbox", "shell")`。

### async execute_cmd

```python
async execute_cmd(
    command: str,
    cwd: str | None = None,
    timeout: int | None = 300,
    environment: dict[str, str] | None = None,
    **kwargs,
) -> ExecuteCmdResult
```

通过 `bash -lc` 在沙箱中执行命令。`cwd="."` 与未设置 `cwd` 都使用 JiuwenBox 默认工作目录。`timeout` 为非正数时不向远端请求设置执行超时。

### async execute_cmd_stream

```python
async execute_cmd_stream(
    command: str,
    *,
    cwd: str | None = None,
    timeout: int | None = 300,
    environment: dict[str, str] | None = None,
    **kwargs,
) -> AsyncIterator[ExecuteCmdStreamResult]
```

执行命令后按行生成 stdout、stderr 数据块，最后生成包含退出码的数据块。该方法会先等待 `execute_cmd` 完成，不提供实时远端输出。

## class JiuwenBoxCodeProvider

```python
class JiuwenBoxCodeProvider(BaseCodeProvider)
```

JiuwenBox 代码执行 Provider，注册名为 `("jiuwenbox", "code")`。当前支持 `language="python"` 和 `language="javascript"`，分别调用沙箱中的 `python3` 和 `node`。

### async execute_code

```python
async execute_code(
    code: str,
    *,
    language: str = "python",
    timeout: int = 300,
    environment: dict[str, str] | None = None,
    cwd: str | None = None,
    options: dict[str, Any] | None = None,
    **kwargs,
) -> ExecuteCodeResult
```

执行 Python 或 JavaScript 代码。`options={"force_file": True}` 时先在沙箱 `/tmp` 创建临时脚本文件再执行，否则使用解释器的命令行参数执行。当前实现固定以 `/tmp` 作为执行目录，`cwd` 参数不会改变该目录。

### async execute_code_stream

```python
async execute_code_stream(
    code: str,
    *,
    language: str = "python",
    timeout: int = 300,
    environment: dict[str, str] | None = None,
    cwd: str | None = None,
    options: dict[str, Any] | None = None,
    **kwargs,
) -> AsyncIterator[ExecuteCodeStreamResult]
```

执行代码后按行生成 stdout、stderr 数据块，最后生成包含退出码的数据块。该方法会先等待 `execute_code` 完成，不提供实时远端输出。

## 错误处理与生命周期

Provider 方法通常将文件系统、Shell 或代码执行异常转换为对应的 `*Result` 错误结果，而不是直接向调用方抛出异常。远端沙箱丢失时，只对能明确识别为“沙箱不存在”的 HTTP 404 触发自动重建，普通文件或目录 404 不会误触发重建。

同一 `base_url` 和 `isolation_key` 下的文件系统、Shell 与代码 Provider 共用一个沙箱。生命周期回调可能收到以下事件：

- `before_create`、`after_create`
- `before_recreate`、`after_recreate`
- `before_delete`、`after_delete`

回调在调用线程中同步执行；回调抛出的异常会继续向上传播。
