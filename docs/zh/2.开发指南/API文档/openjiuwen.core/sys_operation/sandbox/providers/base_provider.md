# openjiuwen.core.sys_operation.base_provider

## class BaseFSProvider

```python
class BaseFSProvider(BaseFsProtocal, ABC)
```

文件系统能力的抽象接口，用于实现自定义沙箱的文件系统操作。

**参数**：

* **endpoint**([SandboxEndpoint](../gateway/gateway.md#class-sandboxendpoint))：沙箱端点信息。
* **config**([SandboxGatewayConfig](../sandbox_config.md#class-sandboxgatewayconfig), 可选)：沙箱网关配置。默认值：`None`。

### async read_file

```python
async read_file(
    path: str,
    *,
    mode: Literal['text', 'bytes'] = "text",
    head: Optional[int] = None,
    tail: Optional[int] = None,
    line_range: Optional[Tuple[int, int]] = None,
    encoding: str = "utf-8",
    chunk_size: int = DEFAULT_READ_CHUNK_SIZE,
    options: Optional[Dict[str, Any]] = None
) -> ReadFileResult
```

读取文件内容。

**参数**：

* **path**(str)：文件路径。
* **mode**(Literal['text', 'bytes'])：读取模式。默认值：`"text"`。
* **head**(int, 可选)：读取前 N 行。默认值：`None`。
* **tail**(int, 可选)：读取后 N 行。默认值：`None`。
* **line_range**(Tuple[int, int], 可选)：读取指定行范围。默认值：`None`。
* **encoding**(str)：文件编码。默认值：`"utf-8"`。
* **chunk_size**(int)：读取块大小。默认值：`DEFAULT_READ_CHUNK_SIZE`。
* **options**(Dict[str, Any], 可选)：额外选项。默认值：`None`。

**返回**：

**[ReadFileResult](../../result.md#class-readfileresult)**，读取结果。

### async read_file_stream

```python
async read_file_stream(
    path: str,
    *,
    mode: Literal['text', 'bytes'] = "text",
    head: Optional[int] = None,
    tail: Optional[int] = None,
    line_range: Optional[Tuple[int, int]] = None,
    encoding: str = "utf-8",
    chunk_size: int = DEFAULT_READ_STREAM_CHUNK_SIZE,
    options: Optional[Dict[str, Any]] = None
) -> AsyncIterator[ReadFileStreamResult]
```

流式读取文件内容。

**参数**：

* **path**(str)：文件路径。
* **mode**(Literal['text', 'bytes'])：读取模式。默认值：`"text"`。
* **head**(int, 可选)：读取前 N 行。默认值：`None`。
* **tail**(int, 可选)：读取后 N 行。默认值：`None`。
* **line_range**(Tuple[int, int], 可选)：读取指定行范围。默认值：`None`。
* **encoding**(str)：文件编码。默认值：`"utf-8"`。
* **chunk_size**(int)：读取块大小。默认值：`DEFAULT_READ_STREAM_CHUNK_SIZE`。
* **options**(Dict[str, Any], 可选)：额外选项。默认值：`None`。

**返回**：

**AsyncIterator[[ReadFileStreamResult](../../result.md#class-readfilestreamresult)]**，流式读取结果。

### async write_file

```python
async write_file(
    path: str,
    content: str | bytes,
    *,
    mode: Literal['text', 'bytes'] = "text",
    prepend_newline: bool = True,
    append_newline: bool = False,
    append: bool = False,
    create_if_not_exist: bool = True,
    permissions: str = "644",
    encoding: str = "utf-8",
    options: Optional[Dict[str, Any]] = None
) -> WriteFileResult
```

写入文件内容。

**参数**：

* **path**(str)：文件路径。
* **content**(str | bytes)：文件内容。
* **mode**(Literal['text', 'bytes'])：写入模式。默认值：`"text"`。
* **prepend_newline**(bool)：是否在内容前添加换行。默认值：`True`。
* **append_newline**(bool)：是否在内容后添加换行。默认值：`False`。
* **append**(bool)：是否追加模式。默认值：`False`。
* **create_if_not_exist**(bool)：文件不存在时是否创建。默认值：`True`。
* **permissions**(str)：文件权限。默认值：`"644"`。
* **encoding**(str)：文件编码。默认值：`"utf-8"`。
* **options**(Dict[str, Any], 可选)：额外选项。默认值：`None`。

**返回**：

**[WriteFileResult](../../result.md#class-writefileresult)**，写入结果。

### async upload_file

```python
async upload_file(
    local_path: str,
    target_path: str,
    *,
    overwrite: bool = False,
    create_parent_dirs: bool = True,
    preserve_permissions: bool = True,
    chunk_size: int = DEFAULT_UPLOAD_CHUNK_SIZE,
    options: Optional[Dict[str, Any]] = None
) -> UploadFileResult
```

上传文件到沙箱。

**参数**：

* **local_path**(str)：本地文件路径。
* **target_path**(str)：目标文件路径。
* **overwrite**(bool)：是否覆盖已存在的文件。默认值：`False`。
* **create_parent_dirs**(bool)：是否创建父目录。默认值：`True`。
* **preserve_permissions**(bool)：是否保留文件权限。默认值：`True`。
* **chunk_size**(int)：上传块大小。默认值：`DEFAULT_UPLOAD_CHUNK_SIZE`。
* **options**(Dict[str, Any], 可选)：额外选项。默认值：`None`。

**返回**：

**[UploadFileResult](../../result.md#class-uploadfileresult)**，上传结果。

### async upload_file_stream

```python
async upload_file_stream(
    local_path: str,
    target_path: str,
    *,
    overwrite: bool = False,
    create_parent_dirs: bool = True,
    preserve_permissions: bool = True,
    chunk_size: int = DEFAULT_UPLOAD_STREAM_CHUNK_SIZE,
    options: Optional[Dict[str, Any]] = None
) -> AsyncIterator[UploadFileStreamResult]
```

流式上传文件到沙箱。

**参数**：

* **local_path**(str)：本地文件路径。
* **target_path**(str)：目标文件路径。
* **overwrite**(bool)：是否覆盖已存在的文件。默认值：`False`。
* **create_parent_dirs**(bool)：是否创建父目录。默认值：`True`。
* **preserve_permissions**(bool)：是否保留文件权限。默认值：`True`。
* **chunk_size**(int)：上传块大小。默认值：`DEFAULT_UPLOAD_STREAM_CHUNK_SIZE`。
* **options**(Dict[str, Any], 可选)：额外选项。默认值：`None`。

**返回**：

**AsyncIterator[[UploadFileStreamResult](../../result.md#class-uploadfilestreamresult)]**，流式上传结果。

### async download_file

```python
async download_file(
    source_path: str,
    local_path: str,
    *,
    overwrite: bool = False,
    create_parent_dirs: bool = True,
    preserve_permissions: bool = True,
    chunk_size: int = DEFAULT_DOWNLOAD_CHUNK_SIZE,
    options: Optional[Dict[str, Any]] = None
) -> DownloadFileResult
```

从沙箱下载文件。

**参数**：

* **source_path**(str)：沙箱内文件路径。
* **local_path**(str)：本地文件路径。
* **overwrite**(bool)：是否覆盖已存在的文件。默认值：`False`。
* **create_parent_dirs**(bool)：是否创建父目录。默认值：`True`。
* **preserve_permissions**(bool)：是否保留文件权限。默认值：`True`。
* **chunk_size**(int)：下载块大小。默认值：`DEFAULT_DOWNLOAD_CHUNK_SIZE`。
* **options**(Dict[str, Any], 可选)：额外选项。默认值：`None`。

**返回**：

**[DownloadFileResult](../../result.md#class-downloadfileresult)**，下载结果。

### async download_file_stream

```python
async download_file_stream(
    source_path: str,
    local_path: str,
    *,
    overwrite: bool = False,
    create_parent_dirs: bool = True,
    preserve_permissions: bool = True,
    chunk_size: int = DEFAULT_DOWNLOAD_STREAM_CHUNK_SIZE,
    options: Optional[Dict[str, Any]] = None
) -> AsyncIterator[DownloadFileStreamResult]
```

流式从沙箱下载文件。

**参数**：

* **source_path**(str)：沙箱内文件路径。
* **local_path**(str)：本地文件路径。
* **overwrite**(bool)：是否覆盖已存在的文件。默认值：`False`。
* **create_parent_dirs**(bool)：是否创建父目录。默认值：`True`。
* **preserve_permissions**(bool)：是否保留文件权限。默认值：`True`。
* **chunk_size**(int)：下载块大小。默认值：`DEFAULT_DOWNLOAD_STREAM_CHUNK_SIZE`。
* **options**(Dict[str, Any], 可选)：额外选项。默认值：`None`。

**返回**：

**AsyncIterator[[DownloadFileStreamResult](../../result.md#class-downloadfilestreamresult)]**，流式下载结果。

### async list_files

```python
async list_files(
    path: str,
    *,
    recursive: bool = False,
    max_depth: Optional[int] = None,
    sort_by: Literal['name', 'modified_time', 'size'] = "name",
    sort_descending: bool = False,
    file_types: Optional[List[str]] = None,
    options: Optional[Dict[str, Any]] = None
) -> ListFilesResult
```

列出文件。

**参数**：

* **path**(str)：目录路径。
* **recursive**(bool)：是否递归列出。默认值：`False`。
* **max_depth**(int, 可选)：最大递归深度。默认值：`None`。
* **sort_by**(Literal['name', 'modified_time', 'size'])：排序字段。默认值：`"name"`。
* **sort_descending**(bool)：是否降序排序。默认值：`False`。
* **file_types**(List[str], 可选)：文件类型过滤。默认值：`None`。
* **options**(Dict[str, Any], 可选)：额外选项。默认值：`None`。

**返回**：

**[ListFilesResult](../../result.md#class-listfilesresult)**，文件列表结果。

### async list_directories

```python
async list_directories(
    path: str,
    *,
    recursive: bool = False,
    max_depth: Optional[int] = None,
    sort_by: Literal['name', 'modified_time', 'size'] = "name",
    sort_descending: bool = False,
    options: Optional[Dict[str, Any]] = None
) -> ListDirsResult
```

列出目录。

**参数**：

* **path**(str)：目录路径。
* **recursive**(bool)：是否递归列出。默认值：`False`。
* **max_depth**(int, 可选)：最大递归深度。默认值：`None`。
* **sort_by**(Literal['name', 'modified_time', 'size'])：排序字段。默认值：`"name"`。
* **sort_descending**(bool)：是否降序排序。默认值：`False`。
* **options**(Dict[str, Any], 可选)：额外选项。默认值：`None`。

**返回**：

**[ListDirsResult](../../result.md#class-listdirsresult)**，目录列表结果。

### async search_files

```python
async search_files(
    path: str,
    pattern: str,
    exclude_patterns: Optional[List[str]] = None
) -> SearchFilesResult
```

搜索文件。

**参数**：

* **path**(str)：搜索起始路径。
* **pattern**(str)：搜索模式。
* **exclude_patterns**(List[str], 可选)：排除模式列表。默认值：`None`。

**返回**：

**[SearchFilesResult](../../result.md#class-searchfilesresult)**，搜索结果。

---

## class BaseShellProvider

```python
class BaseShellProvider(BaseShellProtocal, ABC)
```

Shell 执行能力的抽象接口，用于实现自定义沙箱的 Shell 命令执行。

**参数**：

* **endpoint**([SandboxEndpoint](../gateway/gateway.md#class-sandboxendpoint))：沙箱端点信息。
* **config**([SandboxGatewayConfig](../sandbox_config.md#class-sandboxgatewayconfig), 可选)：沙箱网关配置。默认值：`None`。

### async execute_cmd

```python
async execute_cmd(
    command: str,
    *,
    cwd: Optional[str] = None,
    timeout: Optional[int] = 300,
    environment: Optional[Dict[str, str]] = None,
    options: Optional[Dict[str, Any]] = None
) -> ExecuteCmdResult
```

执行 Shell 命令。

**参数**：

* **command**(str)：要执行的命令。
* **cwd**(str, 可选)：工作目录。默认值：`None`。
* **timeout**(int, 可选)：超时时间（秒）。默认值：`300`。
* **environment**(Dict[str, str], 可选)：环境变量。默认值：`None`。
* **options**(Dict[str, Any], 可选)：额外选项。默认值：`None`。

**返回**：

**[ExecuteCmdResult](../../result.md#class-executecmdresult)**，命令执行结果。

### async execute_cmd_stream

```python
async execute_cmd_stream(
    command: str,
    *,
    cwd: Optional[str] = None,
    timeout: Optional[int] = 300,
    environment: Optional[Dict[str, str]] = None,
    options: Optional[Dict[str, Any]] = None
) -> AsyncIterator[ExecuteCmdStreamResult]
```

流式执行 Shell 命令。

**参数**：

* **command**(str)：要执行的命令。
* **cwd**(str, 可选)：工作目录。默认值：`None`。
* **timeout**(int, 可选)：超时时间（秒）。默认值：`300`。
* **environment**(Dict[str, str], 可选)：环境变量。默认值：`None`。
* **options**(Dict[str, Any], 可选)：额外选项。默认值：`None`。

**返回**：

**AsyncIterator[[ExecuteCmdStreamResult](../../result.md#class-executecmdstreamresult)]**，流式命令执行结果。

---

## class BaseCodeProvider

```python
class BaseCodeProvider(BaseCodeProtocal, ABC)
```

代码执行能力的抽象接口，用于实现自定义沙箱的代码执行。

**参数**：

* **endpoint**([SandboxEndpoint](../gateway/gateway.md#class-sandboxendpoint))：沙箱端点信息。
* **config**([SandboxGatewayConfig](../sandbox_config.md#class-sandboxgatewayconfig), 可选)：沙箱网关配置。默认值：`None`。

### async execute_code

```python
async execute_code(
    code: str,
    *,
    language: Literal['python', 'javascript'] = "python",
    timeout: int = 300,
    environment: Optional[Dict[str, str]] = None,
    options: Optional[Dict[str, Any]] = None
) -> ExecuteCodeResult
```

执行代码。

**参数**：

* **code**(str)：要执行的代码。
* **language**(Literal['python', 'javascript'])：编程语言。默认值：`"python"`。
* **timeout**(int)：超时时间（秒）。默认值：`300`。
* **environment**(Dict[str, str], 可选)：环境变量。默认值：`None`。
* **options**(Dict[str, Any], 可选)：额外选项。默认值：`None`。

**返回**：

**[ExecuteCodeResult](../../result.md#class-executecoderesult)**，代码执行结果。

### async execute_code_stream

```python
async execute_code_stream(
    code: str,
    *,
    language: Literal['python', 'javascript'] = "python",
    timeout: int = 300,
    environment: Optional[Dict[str, str]] = None,
    options: Optional[Dict[str, Any]] = None
) -> AsyncIterator[ExecuteCodeStreamResult]
```

流式执行代码。

**参数**：

* **code**(str)：要执行的代码。
* **language**(Literal['python', 'javascript'])：编程语言。默认值：`"python"`。
* **timeout**(int)：超时时间（秒）。默认值：`300`。
* **environment**(Dict[str, str], 可选)：环境变量。默认值：`None`。
* **options**(Dict[str, Any], 可选)：额外选项。默认值：`None`。

**返回**：

**AsyncIterator[[ExecuteCodeStreamResult](../../result.md#class-executecodestreamresult)]**，流式代码执行结果。
