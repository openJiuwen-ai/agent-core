# openjiuwen.extensions.sys_operation.sandbox.providers.jiuwenbox

`openjiuwen.extensions.sys_operation.sandbox.providers.jiuwenbox` 是 openJiuwen 中对接 **JiuwenBox 沙箱服务**的 provider 实现。它通过 HTTP 网关与远端 JiuwenBox 实例通信，向 `SysOperation` 暴露 **文件系统（fs）/ 命令行（shell）/ 代码执行（code）** 三类能力，并经由 `SandboxRegistry` 以名字 `"jiuwenbox"` 注册。

模块对外暴露：

- 三个 provider 类：`JiuwenBoxFSProvider`、`JiuwenBoxShellProvider`、`JiuwenBoxCodeProvider`；
- 五个模块级工具函数：`build_jiuwenbox_http_client`、`clear_jiuwenbox_shared_sandbox`、`build_jiuwenbox_shared_scope_key`、`force_recreate_jiuwenbox_sandbox`、`delete_jiuwenbox_sandbox`。

> **与 base provider 的关系**：三个 provider 均继承自 `openjiuwen.core.sys_operation.sandbox.providers.base_provider` 下的 `BaseFSProvider` / `BaseShellProvider` / `BaseCodeProvider`（见 [`base_provider.md`](../../../../openjiuwen.core/sys_operation/sandbox/providers/base_provider.md)），并混入私有 `_JiuwenBoxProviderMixin` 以复用 JiuwenBox 专属的沙箱缓存、生命周期钩子与本地回退逻辑。

## 注册说明

三个 provider 通过装饰器在导入时注册到 `SandboxRegistry`：

| Provider 类 | 装饰器 | 能力 |
|---|---|---|
| `JiuwenBoxFSProvider` | `@SandboxRegistry.provider("jiuwenbox", "fs")` | 文件系统 |
| `JiuwenBoxShellProvider` | `@SandboxRegistry.provider("jiuwenbox", "shell")` | 命令行执行 |
| `JiuwenBoxCodeProvider` | `@SandboxRegistry.provider("jiuwenbox", "code")` | 代码执行 |

三者构造签名一致：`__init__(self, endpoint: SandboxEndpoint, config: Optional[SandboxGatewayConfig] = None)`，由 `SandboxRegistry` 在解析到 `"jiuwenbox"` 时按需实例化，**通常不由用户直接构造**。

## class openjiuwen.extensions.sys_operation.sandbox.providers.jiuwenbox.JiuwenBoxFSProvider

```
class openjiuwen.extensions.sys_operation.sandbox.providers.jiuwenbox.JiuwenBoxFSProvider(
    _JiuwenBoxProviderMixin, BaseFSProvider
)
```

JiuwenBox 文件系统 provider。通过 JiuwenBox HTTP 网关在远端沙箱内完成文件读写、目录列举、文件上传/下载与搜索；同时为下载/上传提供流式（stream）变体。

**特性**：

- 文本/字节双模式读写，支持 `head` / `tail` / `line_range` 行选区；
- 列举支持递归、最大深度、排序与按类型过滤；
- 上传/下载支持本地 ↔ 沙箱双向传输与流式分块；
- 支持按 glob 模式搜索文件并排除指定模式。

### __init__

```
JiuwenBoxFSProvider(endpoint: SandboxEndpoint, config: Optional[SandboxGatewayConfig] = None)
```

构造文件系统 provider。**不立即建立网络连接**，仅初始化端点与配置，客户端在首次调用时懒创建。

**参数**：

* **endpoint**(SandboxEndpoint)：沙箱端点，需含 `base_url`（必填）与可选 `sandbox_id` / `isolation_key`。
* **config**(Optional[SandboxGatewayConfig])：网关配置，含 `timeout_seconds`、`launcher_config` 等。默认值：`None`。

### async read_file

```
async def read_file(self, path: str, mode: str = "text", **kwargs) -> ReadFileResult
```

读取沙箱内指定路径的文件内容，支持文本与字节两种模式及行选区。

**参数**：

* **path**(str)：沙箱内文件路径。
* **mode**(str, 可选)：`"text"`（默认）或 `"bytes"`。
* ****kwargs**：行选区与编码参数：
  * `head`(int, 可选)：取前 N 行；
  * `tail`(int, 可选)：取后 N 行；
  * `line_range`(Tuple[int, int], 可选)：取 [start, end] 行区间；
  * `encoding`(str, 可选)：文本编码，默认值 `"utf-8"`。

**返回**：

* **ReadFileResult**：成功时 `data` 为 `ReadFileData(path, content, mode)`；失败时携带错误信息。

### async write_file

```
async def write_file(self, path: str, content: str | bytes, mode: str = "text", **kwargs) -> WriteFileResult
```

向沙箱内写入文件内容，支持覆盖写入与追加写入。

**参数**：

* **path**(str)：沙箱内目标路径。
* **content**(str | bytes)：写入内容。
* **mode**(str, 可选)：`"text"`（默认）或 `"bytes"`。
* ****kwargs**：
  * `append`(bool, 可选)：为 `True` 时追加写入，默认值 `False`；
  * `prepend_newline`(bool, 可选)：文本模式追加时是否在内容前补换行，默认值 `True`；
  * `append_newline`(bool, 可选)：是否在内容末尾补换行，默认值 `False`；
  * `encoding`(str, 可选)：文本编码，默认值 `"utf-8"`。

**返回**：

* **WriteFileResult**：成功时 `data` 为 `WriteFileData(path, size, mode)`。

### async list_files

```
async def list_files(
    self, path: str, *,
    recursive: bool = False,
    max_depth: Optional[int] = None,
    sort_by: str = "name",
    sort_descending: bool = False,
    file_types: Optional[List[str]] = None,
    **kwargs,
) -> ListFilesResult
```

列出指定路径下的文件。

**参数**：

* **path**(str)：沙箱内目录路径。
* **recursive**(bool, 可选)：是否递归，默认值 `False`。
* **max_depth**(Optional[int], 可选)：递归最大深度，默认值 `None`。
* **sort_by**(str, 可选)：排序字段，默认值 `"name"`。
* **sort_descending**(bool, 可选)：是否降序，默认值 `False`。
* **file_types**(Optional[List[str]], 可选)：按类型过滤，默认值 `None`。

**返回**：

* **ListFilesResult**：成功时 `data` 为 `FileSystemData(total_count, list_items, root_path, recursive, max_depth)`。

### async list_directories

```
async def list_directories(
    self, path: str, *,
    recursive: bool = False,
    max_depth: Optional[int] = None,
    sort_by: str = "name",
    sort_descending: bool = False,
    **kwargs,
) -> ListDirsResult
```

列出指定路径下的子目录。参数与 `list_files` 一致（无 `file_types`）。

**返回**：

* **ListDirsResult**：成功时 `data` 为 `FileSystemData(...)`。

### async read_file_stream

```
async def read_file_stream(
    self, path: str, *,
    mode: str = "text",
    head: Optional[int] = None,
    tail: Optional[int] = None,
    line_range: Optional[Tuple[int, int]] = None,
    encoding: str = "utf-8",
    chunk_size: int = 8192,
    **kwargs,
) -> AsyncIterator[ReadFileStreamResult]
```

以流式分块产出文件内容。先复用 `read_file` 取得完整内容，再按 `chunk_size`（字节模式）或按行（文本模式）逐块 `yield`。

**参数**：行选区与编码同 `read_file`；`chunk_size`(int, 可选) 控制字节模式的分块大小，默认值 `8192`。

**返回**：

* **AsyncIterator[ReadFileStreamResult]**：每个分块 `data` 为 `ReadFileChunkData(path, chunk_content, mode, chunk_size, chunk_index, is_last_chunk)`。

### async upload_file

```
async def upload_file(
    self, local_path: str, target_path: str, *,
    overwrite: bool = False,
    create_parent_dirs: bool = True,
    preserve_permissions: bool = True,
    chunk_size: int = 0,
    **kwargs,
) -> UploadFileResult
```

将本地文件上传至沙箱。`overwrite=False` 时若目标已存在则报错。

**参数**：

* **local_path**(str)：本地源文件路径。
* **target_path**(str)：沙箱内目标路径。
* **overwrite**(bool, 可选)：是否覆盖已存在文件，默认值 `False`。
* **create_parent_dirs**(bool, 可选)：是否创建父目录，默认值 `True`。
* **preserve_permissions**(bool, 可选)：是否保留权限，默认值 `True`。
* **chunk_size**(int, 可选)：分块大小，默认值 `0`。

**返回**：

* **UploadFileResult**：成功时 `data` 为 `UploadFileData(local_path, target_path, size)`。

### async upload_file_stream

```
async def upload_file_stream(
    self, local_path: str, target_path: str, *,
    overwrite: bool = False,
    chunk_size: int = 1048576,
    **kwargs,
) -> AsyncIterator[UploadFileStreamResult]
```

上传文件的流式变体。内部委托 `upload_file` 完成传输后产出单个汇总分块。

**返回**：

* **AsyncIterator[UploadFileStreamResult]**：分块 `data` 为 `UploadFileChunkData(local_path, target_path, chunk_size, chunk_index, is_last_chunk)`。

### async download_file

```
async def download_file(
    self, source_path: str, local_path: str, *,
    overwrite: bool = False,
    create_parent_dirs: bool = True,
    preserve_permissions: bool = True,
    chunk_size: int = 0,
    **kwargs,
) -> DownloadFileResult
```

将沙箱内文件下载到本地。

**参数**：

* **source_path**(str)：沙箱内源路径。
* **local_path**(str)：本地目标路径。
* 其余参数同 `upload_file`。

**返回**：

* **DownloadFileResult**：成功时 `data` 为 `DownloadFileData(source_path, local_path, size)`。

### async download_file_stream

```
async def download_file_stream(
    self, source_path: str, local_path: str, *,
    overwrite: bool = False,
    chunk_size: int = 1048576,
    **kwargs,
) -> AsyncIterator[DownloadFileStreamResult]
```

下载文件的流式变体。内部委托 `download_file` 完成传输后产出单个汇总分块。

**返回**：

* **AsyncIterator[DownloadFileStreamResult]**：分块 `data` 为 `DownloadFileChunkData(source_path, local_path, chunk_size, chunk_index, is_last_chunk)`。

### async search_files

```
async def search_files(
    self, path: str, pattern: str, exclude_patterns: Optional[List[str]] = None,
) -> SearchFilesResult
```

在沙箱内按 glob 模式搜索文件。

**参数**：

* **path**(str)：搜索起始目录。
* **pattern**(str)：glob 匹配模式。
* **exclude_patterns**(Optional[List[str]], 可选)：需排除的模式列表，默认值 `None`。

**返回**：

* **SearchFilesResult**：成功时 `data` 为 `SearchFilesData(total_matches, matching_files, search_path, search_pattern, exclude_patterns)`。

## class openjiuwen.extensions.sys_operation.sandbox.providers.jiuwenbox.JiuwenBoxShellProvider

```
class openjiuwen.extensions.sys_operation.sandbox.providers.jiuwenbox.JiuwenBoxShellProvider(
    _JiuwenBoxProviderMixin, BaseShellProvider
)
```

JiuwenBox 命令行 provider。在远端沙箱内通过 `bash -lc <command>` 执行命令，并支持本地回退与排除模式预路由。

### __init__

```
JiuwenBoxShellProvider(endpoint: SandboxEndpoint, config: Optional[SandboxGatewayConfig] = None)
```

构造签名与 `JiuwenBoxFSProvider` 一致。

### async execute_cmd

```
async def execute_cmd(
    self, command: str, cwd: Optional[str] = None, timeout: Optional[int] = 300,
    environment: Optional[Dict[str, str]] = None, **kwargs,
) -> ExecuteCmdResult
```

在沙箱内执行一条 shell 命令。

**参数**：

* **command**(str)：待执行命令（非空）。
* **cwd**(Optional[str], 可选)：工作目录，`None` 或 `"."` 表示默认，默认值 `None`。
* **timeout**(Optional[int], 可选)：执行超时（秒），默认值 `300`；超时返回 `exit_code=124`。
* **environment**(Optional[Dict[str, str]], 可选)：环境变量，默认值 `None`。

**返回**：

* **ExecuteCmdResult**：成功时 `data` 为 `ExecuteCmdData(command, cwd, stdout, stderr, exit_code)`；命令为空时返回错误结果。

**行为说明**：

- 命令按 `launcher_config.extra_params["excluded_commands"]`（fnmatch）做叶子级路由：
  - **全命中**：整条在本地 `bash -lc` 执行；
  - **全未命中**：整条走 sandbox（现有 `_run_exec_pipeline`，可配合 `fallback_on_failure`）；
  - **混合命中**（如 `cat host.txt | grep x` 且排除 `cat`）：本地 bash 编排控制流，未命中段改写为 `jiuwenbox sandbox exec`（管道消费者加 `--stdin -`）；**仅混合路径**会插入 CLI；
  - 分类结果为 `unsupported`（如空命令）时：**整条命令放到沙箱执行**；
  - 无 tree-sitter 时降级为旧版整命令预路由。
- 未配置 `excluded_commands` 时行为不变：整条走 sandbox，不插入 CLI；
- `execute_code` 仍按代码首行整段预路由，不做管道混跑；
- 超时（`exit_code=124`）返回带超时提示的错误结果。

### async execute_cmd_stream

```
async def execute_cmd_stream(
    self, command: str, *,
    cwd: Optional[str] = None, timeout: Optional[int] = 300,
    environment: Optional[Dict[str, str]] = None, **kwargs,
) -> AsyncIterator[ExecuteCmdStreamResult]
```

命令执行的流式变体。先以 `execute_cmd` 取得完整结果，再按 stdout / stderr 行逐块产出，最后产出携带 `exit_code` 的终止分块。

**返回**：

* **AsyncIterator[ExecuteCmdStreamResult]**：分块 `data` 为 `ExecuteCmdChunkData(text, type, chunk_index, exit_code)`（`type` 为 `"stdout"` / `"stderr"`）。

## class openjiuwen.extensions.sys_operation.sandbox.providers.jiuwenbox.JiuwenBoxCodeProvider

```
class openjiuwen.extensions.sys_operation.sandbox.providers.jiuwenbox.JiuwenBoxCodeProvider(
    _JiuwenBoxProviderMixin, BaseCodeProvider
)
```

JiuwenBox 代码执行 provider。支持 `python` 与 `javascript` 两种语言，在沙箱内执行代码片段并返回输出。

### __init__

```
JiuwenBoxCodeProvider(endpoint: SandboxEndpoint, config: Optional[SandboxGatewayConfig] = None)
```

构造签名与前两者一致。

### async execute_code

```
async def execute_code(
    self, code: str, *,
    language: str = "python", timeout: int = 300,
    environment: Optional[Dict[str, str]] = None, cwd: Optional[str] = None,
    options: Optional[Dict[str, Any]] = None, **kwargs,
) -> ExecuteCodeResult
```

在沙箱内执行代码片段。

**参数**：

* **code**(str)：待执行代码（非空）。
* **language**(str, 可选)：`"python"`（默认）或 `"javascript"`，其他值报错。
* **timeout**(int, 可选)：执行超时（秒），默认值 `300`；超时返回 `exit_code=124`。
* **environment**(Optional[Dict[str, str]], 可选)：环境变量，默认值 `None`。
* **cwd**(Optional[str], 可选)：工作目录，默认值 `None`。
* **options**(Optional[Dict[str, Any]], 可选)：执行选项，`force_file=True` 时以临时文件方式执行（经 base64 编码落地），默认值 `None`。

**返回**：

* **ExecuteCodeResult**：成功时 `data` 为 `ExecuteCodeData(code_content, language, stdout, stderr, exit_code)`；代码为空或语言不支持时返回错误结果。

**行为说明**：

- 构造子进程命令：`python` 默认 `python3 -c <code>`，`force_file` 时落地临时 `.py`；`javascript` 默认 `node -e <code>`，`force_file` 时落地临时 `.js`；
- 为 `python` 注入 `PYTHONIOENCODING=utf-8` / `PYTHONUTF8=1`，为 `javascript` 注入 `NODE_DISABLE_COLORS=1`；
- 代码首行命中 `excluded_commands` 模式时预路由本地；否则走 sandbox，`fallback_on_failure=True` 时回退本地。

### async execute_code_stream

```
async def execute_code_stream(
    self, code: str, *,
    language: str = "python", timeout: int = 300,
    environment: Optional[Dict[str, str]] = None, cwd: Optional[str] = None,
    options: Optional[Dict[str, Any]] = None, **kwargs,
) -> AsyncIterator[ExecuteCodeStreamResult]
```

代码执行的流式变体。先以 `execute_code` 取得完整结果，再按 stdout / stderr 行逐块产出，最后产出携带 `exit_code` 的终止分块。

**返回**：

* **AsyncIterator[ExecuteCodeStreamResult]**：分块 `data` 为 `ExecuteCodeChunkData(text, type, chunk_index, exit_code)`。

## 模块函数

### build_jiuwenbox_http_client

```
def build_jiuwenbox_http_client(
    base_url: str,
    timeout_seconds: float = 30.0,
    api_token: str | None = None,
) -> httpx.Client
```

为 JiuwenBox HTTP API 构造一个同步 `httpx.Client`，可选注入 Bearer 鉴权头。

**参数**：

* **base_url**(str)：JiuwenBox 服务基础 URL，末尾 `/` 会被去除。
* **timeout_seconds**(float, 可选)：请求超时（秒），默认值 `30.0`。
* **api_token**(str | None, 可选)：API token；为 `None` 时从默认解析逻辑取，无则不加鉴权头。默认值 `None`。

**返回**：

* **httpx.Client**：配置好 `base_url`、超时与（可选）`Authorization: Bearer <token>` 头的客户端。

### clear_jiuwenbox_shared_sandbox

```
def clear_jiuwenbox_shared_sandbox(base_url: str) -> list[str]
```

清理进程内某 `base_url` 对应的共享沙箱缓存。

**参数**：

* **base_url**(str)：JiuwenBox 服务基础 URL。

**返回**：

* **list[str]**：被移除的 `sandbox_id` 列表（去重）。

### build_jiuwenbox_shared_scope_key

```
def build_jiuwenbox_shared_scope_key(base_url: str, isolation_key: str) -> str
```

为 `base_url` + `isolation_key` 组合构造 provider 共享缓存键。

**参数**：

* **base_url**(str)：JiuwenBox 服务基础 URL。
* **isolation_key**(str)：隔离键（通常由 SysOperation 经网关 `SandboxEndpoint` 传入）。

**返回**：

* **str**：形如 `"<base_url.rstrip('/')>|<isolation_key>"` 的缓存键。

### async force_recreate_jiuwenbox_sandbox

```
async def force_recreate_jiuwenbox_sandbox(
    base_url: str,
    *,
    shared_key: str | None = None,
    policy: dict | None = None,
    policy_mode: str | None = None,
    timeout_seconds: float = 30.0,
    preserve_files_upload: Any = None,
    extra_stale_sandbox_ids: Sequence[str] | None = None,
    lifecycle_hook: Optional[Callable[[str, dict], None]] = None,
    reason: str = "sandbox_lost",
) -> str
```

清理缓存并为 `base_url` 创建一个**新的远端沙箱**。用于策略热更新（`/sandbox` 文件 allow/deny）与“沙箱丢失”重试。

**参数**：

* **base_url**(str)：JiuwenBox 服务基础 URL。
* **shared_key**(str | None, 可选)：作用域缓存键（`base_url|isolation`）；设置时仅清理该条，其他 agent 的沙箱不受影响。默认值 `None`。
* **policy**(dict | None, 可选)：新沙箱的安全策略。默认值 `None`。
* **policy_mode**(str | None, 可选)：新沙箱的策略模式。默认值 `None`。
* **timeout_seconds**(float, 可选)：HTTP 客户端超时（秒），默认值 `30.0`。
* **preserve_files_upload**(Any, 可选)：copy 模式下需重新上传的文件/目录。默认值 `None`。
* **extra_stale_sandbox_ids**(Sequence[str] | None, 可选)：创建后需额外删除的陈旧 ID 列表。默认值 `None`。
* **lifecycle_hook**(Optional[Callable[[str, dict], None]], 可选)：`before_recreate` / `after_recreate` 事件的严格生命周期钩子。默认值 `None`。
* **reason**(str, 可选)：钩子上下文中的重建原因，`"sandbox_lost"` 或 `"policy_changed"`。默认值 `"sandbox_lost"`。

**返回**：

* **str**：新建沙箱的 `sandbox_id`。

### async delete_jiuwenbox_sandbox

```
async def delete_jiuwenbox_sandbox(
    *,
    sandbox_id: Optional[str] = None,
    shared_key: Optional[str] = None,
    delete_all: bool = False,
    reason: str = "teardown",
    timeout_seconds: float = 30.0,
) -> list[str]
```

在 SysOperation 拆卸时删除缓存的远端 JiuwenBox 沙箱。

**参数**：

* **sandbox_id**(Optional[str], 可选)：`shared_key` 缓存未命中时按此 ID 删除的远端沙箱 ID。默认值 `None`。
* **shared_key**(Optional[str], 可选)：作用域缓存键（`base_url|isolation_key`）。默认值 `None`。
* **delete_all**(bool, 可选)：为 `True` 时清空进程内全部缓存沙箱（用于显式批量清理，非单 agent 网关释放）。默认值 `False`。
* **reason**(str, 可选)：传入删除钩子上下文的原因，默认值 `"teardown"`。
* **timeout_seconds**(float, 可选)：HTTP 客户端超时（秒），默认值 `30.0`。

**返回**：

* **list[str]**：成功删除的 `sandbox_id` 列表（按顺序）。

## 实现细节：_JiuwenBoxProviderMixin

三个 provider 共同混入私有 `_JiuwenBoxProviderMixin`，复用以下 JiuwenBox 专属机制：

**共享沙箱缓存**：同一 `base_url` + `isolation_key`（即 `shared_scope_key`）下的 fs / shell / code 三个 provider **共享同一个远端沙箱**，而非按 agent 隔离。缓存以类级 `_shared_sandbox_ids: Dict[str, str]` 维护，由 `_shared_lock = threading.Lock()` 保护。`isolation_key` 由 SysOperation 经网关 `SandboxEndpoint` 传入，确保不同 agent 即便共用 `base_url` 也不互相干扰。

**懒加载客户端**：`_get_client()` 在首次调用时依据 `endpoint.base_url` 创建 `_JiuwenBoxClient`（内部 httpx 客户端）；`base_url` 缺失时抛 `ValueError("jiuwenbox provider requires endpoint.base_url")`。`sandbox_id` 优先取端点配置，否则回退环境变量 `JIUWENBOX_SANDBOX_ID`。

**生命周期钩子**：`launcher_config.extra_params["lifecycle_hook"]` 可传入 `Callable[[str, dict], None]`，在沙箱创建/删除/重建事件中回调；创建时捕获的钩子缓存于类级 `_lifecycle_hooks`，使 `delete_jiuwenbox_sandbox` 在未显式传入钩子时仍能触发删除事件。

**排除模式预路由与本地回退**：`launcher_config.extra_params["excluded_commands"]`（fnmatch）对 shell 做叶子级匹配：同质全本地/全远端不改写；混合时由本地 bash + `jiuwenbox sandbox exec` 跨端编排（需宿主已安装 CLI，且 token 经 `JIUWENBOX_API_TOKEN` 环境传递、不进 argv）。混合启动后不自动 recreate/整条 fallback，避免重复本地副作用。`execute_code` 仍按首行整段预路由。`fallback_on_failure=True` 仅作用于整条远端 pipeline。本地结果经 `_wrap_shell_local_result` / `_wrap_code_local_result` 包装，`exit_code=124` 表示超时。

**并发重建锁**：类级 `_recreate_lock`（懒初始化的 `asyncio.Lock`）串行化并发的沙箱重建操作，避免重复创建；`_idle_timeout_cache` 按 `base_url` 去重 PUT `/api/v1/timeout` 请求。
