# openjiuwen.extensions.sys_operation.sandbox.providers.jiuwenbox

The JiuwenBox sandbox provider adapts SysOperation file-system, shell, and code-execution interfaces to the JiuwenBox HTTP API. Importing the module registers `fs`, `shell`, and `code` providers for `sandbox_type="jiuwenbox"`.

See [BaseFSProvider, BaseShellProvider, and BaseCodeProvider](../../../../openjiuwen.core/sys_operation/sandbox/providers/base_provider.md) for the base contracts. Return types are defined in [sys_operation.result](../../../../openjiuwen.core/sys_operation/result.md).

## Configuration Example

A reachable JiuwenBox service must be running before file, shell, or code operations can be executed. Applications normally select JiuwenBox through a `SysOperationCard` and the gateway rather than instantiating providers directly:

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

If the JiuwenBox service uses Bearer authentication, set `JIUWENBOX_API_TOKEN`. Set `JIUWENBOX_SANDBOX_ID` to use an existing sandbox; otherwise, providers lazily create and share a sandbox by `base_url` and `isolation_key`.

`launcher_config.extra_params` accepts these JiuwenBox-specific options:

| Option | Type | Description |
|---|---|---|
| `sandbox_id` | str | Selects an existing sandbox. The value is reread at runtime so callers can update it. |
| `policy` | dict | Security policy sent when creating or recreating a sandbox. |
| `policy_mode` | str | Policy mode used when creating or recreating a sandbox. |
| `idle_check_interval` | int | JiuwenBox server idle-check interval; `idle_ttl_seconds` supplies the idle timeout. |
| `excluded_commands` | List[str] | Shell: `fnmatch` per simple-command leaf. All-local / all-remote keep the original string; mixed leaves rewrite remote segments to `jiuwenbox sandbox exec` (local bash orchestrates control flow). Code: still matched on the first line only. |
| `fallback_on_failure` | bool | Runs on the SDK host if the **whole remote** sandbox execution pipeline fails. Does **not** apply to hybrid rewritten commands. Default: `False`. |
| `preserve_files_upload` | List[dict] | Files or directories re-uploaded after recreation. Each entry contains `host_path` and `sandbox_path`; directories may set `kind="directory"`. |
| `lifecycle_hook` | Callable[[str, dict], None] | Synchronous callback receiving an event name and a shallow copy of its context. |

> **Security warning:** `excluded_commands` and `fallback_on_failure=True` execute commands or code on the SDK host and therefore bypass sandbox isolation. Hybrid rewrite additionally requires a trusted `jiuwenbox` CLI on the host and sends auth via `JIUWENBOX_API_TOKEN` (never on the process argv). Enable them only in controlled environments where this behavior is explicitly acceptable.

When a missing sandbox produces the recognized HTTP 404 response, the provider automatically recreates it and retries. `JIUWENBOX_SANDBOX_RECREATE_RETRIES` controls the number of recreation retries. The default is `3`; set it to `0` to disable retries.

## function build_jiuwenbox_http_client

```python
build_jiuwenbox_http_client(
    base_url: str,
    timeout_seconds: float = 30.0,
    api_token: str | None = None,
) -> httpx.Client
```

Creates a synchronous `httpx.Client` for the JiuwenBox HTTP API. A trailing `/` is removed from `base_url`. TCP endpoints use `http://host:port`; Unix Domain Socket endpoints use `unix:///abs/socket/path` (three slashes and an absolute path) via `httpx.HTTPTransport(uds=...)`. A non-empty `api_token` adds Bearer authentication; when omitted, the function reads `JIUWENBOX_API_TOKEN`.

The caller must close the returned client or use it as a context manager.

## function build_jiuwenbox_shared_scope_key

```python
build_jiuwenbox_shared_scope_key(base_url: str, isolation_key: str) -> str
```

Builds the provider's in-process shared-cache key in the form `{base_url without trailing slash}|{isolation_key}`.

## function clear_jiuwenbox_shared_sandbox

```python
clear_jiuwenbox_shared_sandbox(base_url: str) -> list[str]
```

Clears the in-process sandbox-ID cache for `base_url` and returns the deduplicated IDs that were removed. It clears local cache entries only and does not delete remote sandboxes.

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

Creates a new sandbox, updates the shared cache, and attempts to delete stale sandboxes. The new sandbox is created before stale sandboxes are deleted so that a failed creation does not prematurely remove the old sandbox.

**Parameters**:

- **base_url** (str): JiuwenBox service URL.
- **shared_key** (str, optional): Cache key made from the base URL and isolation key. If omitted, all cached entries for the URL are cleared for compatibility with single-tenant callers.
- **policy** (dict, optional): Security policy for the new sandbox.
- **policy_mode** (str, optional): Policy mode.
- **timeout_seconds** (float): HTTP timeout in seconds. Default: `30.0`.
- **preserve_files_upload** (Any, optional): Local files or directories to upload after creation.
- **extra_stale_sandbox_ids** (Sequence[str], optional): Additional stale sandbox IDs to clean up.
- **lifecycle_hook** (Callable, optional): Synchronous lifecycle callback. Recreation emits `before_recreate` and `after_recreate`.
- **reason** (str): Recreation reason included in callback context. Default: `"sandbox_lost"`.

**Returns**: The new sandbox ID.

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

Deletes remote sandboxes associated with the in-process cache. By default, it handles only the entry selected by `shared_key` or a cached `sandbox_id`. With `delete_all=True`, it drains the process-wide JiuwenBox cache and deletes each associated remote sandbox.

Cached lifecycle callbacks receive `before_delete` and `after_delete`. A remote deletion failure is logged and processing continues; the return value contains only successfully deleted sandbox IDs.

## class JiuwenBoxFSProvider

```python
class JiuwenBoxFSProvider(BaseFSProvider)
```

JiuwenBox file-system provider registered as `("jiuwenbox", "fs")`.

### Constructor

```python
JiuwenBoxFSProvider(
    endpoint: SandboxEndpoint,
    config: SandboxGatewayConfig | None = None,
)
```

- **endpoint**: Sandbox endpoint containing `base_url`, optional `sandbox_id`, and optional `isolation_key`.
- **config**: Gateway configuration used to read timeouts, launcher settings, and `extra_params`.

### File Methods

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

`read_file` supports `mode="text"` and `mode="bytes"`. Text mode accepts one of `head`, `tail`, or `line_range=(start, end)`; these selectors cannot be combined. Byte mode does not support line selection. `write_file` accepts `append`, `prepend_newline`, and `append_newline`.

List results can be sorted by `name`, `modified_time`, or `size`. `list_files` can also filter on the file type returned by JiuwenBox through `file_types`.

### Streaming and Transfer Methods

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

Uploads read `local_path` from the SDK host, and downloads write to `local_path` on that host. The current upload, download, shell, and code “streaming” methods complete the remote operation before yielding stream result objects; they do not relay the JiuwenBox HTTP response in real time.

## class JiuwenBoxShellProvider

```python
class JiuwenBoxShellProvider(BaseShellProvider)
```

JiuwenBox shell provider registered as `("jiuwenbox", "shell")`.

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

Runs the command through `bash -lc` inside the sandbox. Both `cwd="."` and an omitted `cwd` use the JiuwenBox default working directory. A non-positive `timeout` does not set an execution timeout on the remote request.

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

After the command completes, yields stdout and stderr line chunks followed by a final chunk containing the exit code. This method waits for `execute_cmd` and does not provide real-time remote output.

## class JiuwenBoxCodeProvider

```python
class JiuwenBoxCodeProvider(BaseCodeProvider)
```

JiuwenBox code-execution provider registered as `("jiuwenbox", "code")`. It currently supports `language="python"` and `language="javascript"`, invoking `python3` and `node` inside the sandbox.

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

Executes Python or JavaScript code. With `options={"force_file": True}`, it creates and runs a temporary script under `/tmp`; otherwise, it uses the interpreter's command-line argument. The current implementation always executes under `/tmp`, so `cwd` does not change the working directory.

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

After execution completes, yields stdout and stderr line chunks followed by a final chunk containing the exit code. This method waits for `execute_code` and does not provide real-time remote output.

## Error Handling and Lifecycle

Provider methods generally convert file-system, shell, and code-execution exceptions into the corresponding error `*Result` rather than raising them directly. Automatic recreation occurs only for an HTTP 404 that can be identified specifically as “sandbox not found”; an ordinary missing-file or missing-directory 404 does not trigger recreation.

File-system, shell, and code providers with the same `base_url` and `isolation_key` share one sandbox. Lifecycle callbacks may receive these events:

- `before_create`, `after_create`
- `before_recreate`, `after_recreate`
- `before_delete`, `after_delete`

Callbacks run synchronously in the calling thread. Exceptions raised by a callback propagate to the caller.
