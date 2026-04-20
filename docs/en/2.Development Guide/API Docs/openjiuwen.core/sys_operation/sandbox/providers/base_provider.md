# openjiuwen.core.sys_operation.base_provider

## class BaseFSProvider

```python
class BaseFSProvider(BaseFsProtocal, ABC)
```

Abstract interface for File System capabilities of a sandbox.

**Parameters**:

* **endpoint**([SandboxEndpoint](../gateway/gateway.md#class-sandboxendpoint)): Sandbox endpoint information.
* **config**([SandboxGatewayConfig](../sandbox_config.md#class-sandboxgatewayconfig), optional): Sandbox gateway config. Default: `None`.

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

Read file content.

**Parameters**:

* **path**(str): File path.
* **mode**(Literal['text', 'bytes']): Read mode. Default: `"text"`.
* **head**(int, optional): Read first N lines. Default: `None`.
* **tail**(int, optional): Read last N lines. Default: `None`.
* **line_range**(Tuple[int, int], optional): Read specified line range. Default: `None`.
* **encoding**(str): File encoding. Default: `"utf-8"`.
* **chunk_size**(int): Read chunk size. Default: `DEFAULT_READ_CHUNK_SIZE`.
* **options**(Dict[str, Any], optional): Additional options. Default: `None`.

**Returns**:

**[ReadFileResult](../../result.md#class-readfileresult)**, read result.

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

Stream read file content.

**Parameters**:

* **path**(str): File path.
* **mode**(Literal['text', 'bytes']): Read mode. Default: `"text"`.
* **head**(int, optional): Read first N lines. Default: `None`.
* **tail**(int, optional): Read last N lines. Default: `None`.
* **line_range**(Tuple[int, int], optional): Read specified line range. Default: `None`.
* **encoding**(str): File encoding. Default: `"utf-8"`.
* **chunk_size**(int): Read chunk size. Default: `DEFAULT_READ_STREAM_CHUNK_SIZE`.
* **options**(Dict[str, Any], optional): Additional options. Default: `None`.

**Returns**:

**AsyncIterator[[ReadFileStreamResult](../../result.md#class-readfilestreamresult)]**, stream read result.

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

Write file content.

**Parameters**:

* **path**(str): File path.
* **content**(str | bytes): File content.
* **mode**(Literal['text', 'bytes']): Write mode. Default: `"text"`.
* **prepend_newline**(bool): Whether to prepend newline. Default: `True`.
* **append_newline**(bool): Whether to append newline. Default: `False`.
* **append**(bool): Whether to append mode. Default: `False`.
* **create_if_not_exist**(bool): Whether to create if not exist. Default: `True`.
* **permissions**(str): File permissions. Default: `"644"`.
* **encoding**(str): File encoding. Default: `"utf-8"`.
* **options**(Dict[str, Any], optional): Additional options. Default: `None`.

**Returns**:

**[WriteFileResult](../../result.md#class-writefileresult)**, write result.

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

Upload file to sandbox.

**Parameters**:

* **local_path**(str): Local file path.
* **target_path**(str): Target file path.
* **overwrite**(bool): Whether to overwrite existing file. Default: `False`.
* **create_parent_dirs**(bool): Whether to create parent directories. Default: `True`.
* **preserve_permissions**(bool): Whether to preserve file permissions. Default: `True`.
* **chunk_size**(int): Upload chunk size. Default: `DEFAULT_UPLOAD_CHUNK_SIZE`.
* **options**(Dict[str, Any], optional): Additional options. Default: `None`.

**Returns**:

**[UploadFileResult](../../result.md#class-uploadfileresult)**, upload result.

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

Stream upload file to sandbox.

**Parameters**:

* **local_path**(str): Local file path.
* **target_path**(str): Target file path.
* **overwrite**(bool): Whether to overwrite existing file. Default: `False`.
* **create_parent_dirs**(bool): Whether to create parent directories. Default: `True`.
* **preserve_permissions**(bool): Whether to preserve file permissions. Default: `True`.
* **chunk_size**(int): Upload chunk size. Default: `DEFAULT_UPLOAD_STREAM_CHUNK_SIZE`.
* **options**(Dict[str, Any], optional): Additional options. Default: `None`.

**Returns**:

**AsyncIterator[[UploadFileStreamResult](../../result.md#class-uploadfilestreamresult)]**, stream upload result.

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

Download file from sandbox.

**Parameters**:

* **source_path**(str): Source file path in sandbox.
* **local_path**(str): Local file path.
* **overwrite**(bool): Whether to overwrite existing file. Default: `False`.
* **create_parent_dirs**(bool): Whether to create parent directories. Default: `True`.
* **preserve_permissions**(bool): Whether to preserve file permissions. Default: `True`.
* **chunk_size**(int): Download chunk size. Default: `DEFAULT_DOWNLOAD_CHUNK_SIZE`.
* **options**(Dict[str, Any], optional): Additional options. Default: `None`.

**Returns**:

**[DownloadFileResult](../../result.md#class-downloadfileresult)**, download result.

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

Stream download file from sandbox.

**Parameters**:

* **source_path**(str): Source file path in sandbox.
* **local_path**(str): Local file path.
* **overwrite**(bool): Whether to overwrite existing file. Default: `False`.
* **create_parent_dirs**(bool): Whether to create parent directories. Default: `True`.
* **preserve_permissions**(bool): Whether to preserve file permissions. Default: `True`.
* **chunk_size**(int): Download chunk size. Default: `DEFAULT_DOWNLOAD_STREAM_CHUNK_SIZE`.
* **options**(Dict[str, Any], optional): Additional options. Default: `None`.

**Returns**:

**AsyncIterator[[DownloadFileStreamResult](../../result.md#class-downloadfilestreamresult)]**, stream download result.

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

List files.

**Parameters**:

* **path**(str): Directory path.
* **recursive**(bool): Whether to list recursively. Default: `False`.
* **max_depth**(int, optional): Maximum recursion depth. Default: `None`.
* **sort_by**(Literal['name', 'modified_time', 'size']): Sort field. Default: `"name"`.
* **sort_descending**(bool): Whether to sort descending. Default: `False`.
* **file_types**(List[str], optional): File type filter. Default: `None`.
* **options**(Dict[str, Any], optional): Additional options. Default: `None`.

**Returns**:

**[ListFilesResult](../../result.md#class-listfilesresult)**, file list result.

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

List directories.

**Parameters**:

* **path**(str): Directory path.
* **recursive**(bool): Whether to list recursively. Default: `False`.
* **max_depth**(int, optional): Maximum recursion depth. Default: `None`.
* **sort_by**(Literal['name', 'modified_time', 'size']): Sort field. Default: `"name"`.
* **sort_descending**(bool): Whether to sort descending. Default: `False`.
* **options**(Dict[str, Any], optional): Additional options. Default: `None`.

**Returns**:

**[ListDirsResult](../../result.md#class-listdirsresult)**, directory list result.

### async search_files

```python
async search_files(
    path: str,
    pattern: str,
    exclude_patterns: Optional[List[str]] = None
) -> SearchFilesResult
```

Search files.

**Parameters**:

* **path**(str): Search starting path.
* **pattern**(str): Search pattern.
* **exclude_patterns**(List[str], optional): Exclude patterns. Default: `None`.

**Returns**:

**[SearchFilesResult](../../result.md#class-searchfilesresult)**, search result.

---

## class BaseShellProvider

```python
class BaseShellProvider(BaseShellProtocal, ABC)
```

Abstract interface for Shell execution capabilities of a sandbox.

**Parameters**:

* **endpoint**([SandboxEndpoint](../gateway/gateway.md#class-sandboxendpoint)): Sandbox endpoint information.
* **config**([SandboxGatewayConfig](../sandbox_config.md#class-sandboxgatewayconfig), optional): Sandbox gateway config. Default: `None`.

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

Execute shell command.

**Parameters**:

* **command**(str): Command to execute.
* **cwd**(str, optional): Working directory. Default: `None`.
* **timeout**(int, optional): Timeout in seconds. Default: `300`.
* **environment**(Dict[str, str], optional): Environment variables. Default: `None`.
* **options**(Dict[str, Any], optional): Additional options. Default: `None`.

**Returns**:

**[ExecuteCmdResult](../../result.md#class-executecmdresult)**, command execution result.

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

Stream execute shell command.

**Parameters**:

* **command**(str): Command to execute.
* **cwd**(str, optional): Working directory. Default: `None`.
* **timeout**(int, optional): Timeout in seconds. Default: `300`.
* **environment**(Dict[str, str], optional): Environment variables. Default: `None`.
* **options**(Dict[str, Any], optional): Additional options. Default: `None`.

**Returns**:

**AsyncIterator[[ExecuteCmdStreamResult](../../result.md#class-executecmdstreamresult)]**, stream command execution result.

---

## class BaseCodeProvider

```python
class BaseCodeProvider(BaseCodeProtocal, ABC)
```

Abstract interface for Code execution capabilities of a sandbox.

**Parameters**:

* **endpoint**([SandboxEndpoint](../gateway/gateway.md#class-sandboxendpoint)): Sandbox endpoint information.
* **config**([SandboxGatewayConfig](../sandbox_config.md#class-sandboxgatewayconfig), optional): Sandbox gateway config. Default: `None`.

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

Execute code.

**Parameters**:

* **code**(str): Code to execute.
* **language**(Literal['python', 'javascript']): Programming language. Default: `"python"`.
* **timeout**(int): Timeout in seconds. Default: `300`.
* **environment**(Dict[str, str], optional): Environment variables. Default: `None`.
* **options**(Dict[str, Any], optional): Additional options. Default: `None`.

**Returns**:

**[ExecuteCodeResult](../../result.md#class-executecoderesult)**, code execution result.

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

Stream execute code.

**Parameters**:

* **code**(str): Code to execute.
* **language**(Literal['python', 'javascript']): Programming language. Default: `"python"`.
* **timeout**(int): Timeout in seconds. Default: `300`.
* **environment**(Dict[str, str], optional): Environment variables. Default: `None`.
* **options**(Dict[str, Any], optional): Additional options. Default: `None`.

**Returns**:

**AsyncIterator[[ExecuteCodeStreamResult](../../result.md#class-executecodestreamresult)]**, stream code execution result.
