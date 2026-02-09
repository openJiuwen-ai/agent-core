# openjiuwen.core.sys_operation.fs

## class BaseFsOperation

```python
class BaseFsOperation()
```

`BaseFsOperation`是提供文件读写、上传下载、列表搜索等功能的抽象基类，继承于[BaseOperation](./base.md#class-baseoperation)。

### abstractmethod async read_file

```python
abstractmethod async read_file(
    path: str,
    mode: Literal['text', 'bytes'] = "text",
    head: Optional[int] = None, 
    tail: Optional[int] = None, 
    line_range: Optional[Tuple[int, int]] = None,
    encoding: str = "utf-8", 
    chunk_size: int = 8192,
    options: Optional[Dict[str, Any]] = None) -> ReadFileResult
```

使用指定模式和参数异步读取文件。

**参数**：

* **path**(str)：要读取的文件的完整路径或相对路径。
* **mode**(Literal['text', 'bytes'], 可选)：读取模式。'text'表示文本模式读取文件，'bytes'表示二进制字节流模式读取文件。默认值："text"。
* **head**(int, 可选)：从开头读取的行数，仅限文本模式。
* **tail**(int, 可选)：从末尾读取的行数，仅限文本模式。
* **line_range**(Tuple[int, int], 可选)：要读取的具体行范围，行数索引从1开始，仅限文本模式。注意，`head`、`tail`、`line_range`三个参数互斥。
* **encoding**(str, 可选)：文本模式的字符编码。默认值："utf-8"。
* **chunk_size**(int, 可选)：二进制字节流模式读取的缓冲区大小。单位：字节。默认值：8192。
* **options**(Dict[str, Any], 可选)：扩展配置选项。

**返回**：

**[ReadFileResult](./result.md#class-readfileresult)**，读取文件结果。

### abstractmethod async read_file_stream

```python
abstractmethod async read_file_stream(
    path: str,
    mode: Literal['text', 'bytes'] = "text",
    head: Optional[int] = None,
    tail: Optional[int] = None,
    line_range: Optional[Tuple[int, int]] = None,
    encoding: str = "utf-8",
    chunk_size: int = 8192,
    options: Optional[Dict[str, Any]] = None) -> AsyncIterator[ReadFileStreamResult]
```

使用指定模式和参数异步流式读取文件。

**参数**：

* **path**(str)：要读取的文件的完整路径或相对路径。
* **mode**(Literal['text', 'bytes'], 可选)：读取模式。'text'表示文本模式读取文件，'bytes'表示二进制字节流模式读取文件。默认值："text"。
* **head**(int, 可选)：从开头读取的行数，仅限文本模式。
* **tail**(int, 可选)：从末尾读取的行数，仅限文本模式。
* **line_range**(Tuple[int, int], 可选)：要读取的具体行范围，行数索引从1开始，仅限文本模式。需要注意，`head`、`tail`、`line_range`三个参数互斥，不建议同时传入。
* **encoding**(str, 可选)：文本模式的字符编码。默认值："utf-8"。
* **chunk_size**(int, 可选)：二进制字节流模式读取的缓冲区大小。单位：字节。默认值：8192。
* **options**(Dict[str, Any], 可选)：扩展配置选项。

**返回**：

**AsyncIterator[[ReadFileStreamResult](./result.md#class-readfilestreamresult)]**，流式读取文件结果。

### abstractmethod async write_file

```python
abstractmethod async write_file(
    path: str,
    content: str | bytes,
    mode: Literal['text', 'bytes'] = "text",
    prepend_newline: bool = True,
    append_newline: bool = False,
    create_if_not_exist: bool = True,
    permissions: str = "644",
    encoding: str = "utf-8",
    options: Optional[Dict[str, Any]] = None) -> WriteFileResult
```

异步将内容写入文件。

**参数**：

* **path**(str)：要写入的文件的完整路径或相对路径。
* **content**(str | bytes)：要写入的数据。文本模式传入类型为`str`，二进制字节流模式传入类型为`bytes`。
* **mode**(Literal['text', 'bytes'], 可选)：写入模式。'text'表示文本模式写入文件，'bytes'表示二进制字节流模式写入文件。默认值："text"。
* **prepend_newline**(bool, 可选)：是否在内容前添加换行符，仅文本模式。`True`表示在内容前添加换行符，`False`表示在内容前不添加换行符。默认值：`True`。
* **append_newline**(bool, 可选)：是否在内容后添加换行符，仅文本模式。`True`表示在内容后添加换行符，`False`表示在内容后不添加换行符。默认值：`False`。
* **create_if_not_exist**(bool, 可选)：若文件不存在，是否自动创建。`True`表示文件不存在则自动创建，`False`表示文件不存在不自动创建。默认值：`True`。
* **permissions**(str, 可选)：八进制文件权限，仅 Unix/Linux系统支持。默认值："644"。
* **encoding**(str, 可选)：字符编码。默认值："utf-8"。
* **options**(Dict[str, Any], 可选)：扩展配置选项。

**返回**：

**[WriteFileResult](./result.md#class-writefileresult)**，写入文件结果。

### abstractmethod async upload_file

```python
abstractmethod async upload_file(
    local_path: str,
    target_path: str,
    overwrite: bool = False,
    create_parent_dirs: bool = True,
    preserve_permissions: bool = True,
    chunk_size: int = 1024 * 1024,
    options: Optional[Dict[str, Any]] = None) -> UploadFileResult
```

异步上传文件。

**参数**：

* **local_path**(str)：本地源文件路径。
* **target_path**(str)：上传目标路径。
* **overwrite**(bool, 可选)：是否覆盖现有目标文件。`True`表示覆盖现有目标文件，`False`表示不覆盖现有目标文件。默认值：`False`。
* **create_parent_dirs**(bool, 可选)：是否自动创建目标父目录。`True`表示自动创建目标父目录，`False`表示不自动创建目标父目录。默认值：`True`。
* **preserve_permissions**(bool, 可选)：是否保留文件权限。`True`表示保留文件权限，`False`表示不保留文件权限。默认值：`True`。
* **chunk_size**(int, 可选)：传输分块大小。默认值：1MB。
* **options**(Dict[str, Any], 可选)：扩展配置选项。

**返回**：

**[UploadFileResult](./result.md#class-uploadfileresult)**，上传文件结果。

### abstractmethod async upload_file_stream

```python
abstractmethod async upload_file_stream(
    local_path: str,
    target_path: str,
    overwrite: bool = False,
    create_parent_dirs: bool = True,
    preserve_permissions: bool = True,
    chunk_size: int = 1024 * 1024,
    options: Optional[Dict[str, Any]] = None) -> AsyncIterator[UploadFileStreamResult]
```

异步流式上传文件。

**参数**：

* **local_path**(str)：本地源文件路径。
* **target_path**(str)：上传目标路径。
* **overwrite**(bool, 可选)：是否覆盖现有目标文件。`True`表示覆盖现有目标文件，`False`表示不覆盖现有目标文件。默认值：`False`。
* **create_parent_dirs**(bool, 可选)：是否自动创建目标父目录。`True`表示自动创建目标父目录，`False`表示不自动创建目标父目录。默认值：`True`。
* **preserve_permissions**(bool, 可选)：是否保留文件权限。`True`表示保留文件权限，`False`表示不保留文件权限。默认值：`True`。
* **chunk_size**(int, 可选)：传输分块大小。默认值：1MB。
* **options**(Dict[str, Any], 可选)：扩展配置选项。

**返回**：

**AsyncIterator[[UploadFileStreamResult](./result.md#class-uploadfilestreamresult)]**，流式上传文件结果。

### abstractmethod async download_file

```python
abstractmethod async download_file(
    source_path: str,
    local_path: str,
    overwrite: bool = False,
    create_parent_dirs: bool = True,
    preserve_permissions: bool = True,
    chunk_size: int = 1024 * 1024,
    options: Optional[Dict[str, Any]] = None) -> DownloadFileResult
```

异步下载文件。

**参数**：

* **source_path**(str)：源文件路径。
* **local_path**(str)：本地路径。
* **overwrite**(bool, 可选)：是否覆盖现有本地文件。`True`表示覆盖现有本地文件，`False`表示不覆盖现有本地文件。默认值：`False`。
* **create_parent_dirs**(bool, 可选)：是否自动创建目标父目录。`True`表示自动创建目标父目录，`False`表示不自动创建目标父目录。默认值：`True`。
* **preserve_permissions**(bool, 可选)：是否保留文件权限。`True`表示保留文件权限，`False`表示不保留文件权限。默认值：`True`。
* **chunk_size**(int, 可选)：传输分块大小。默认值：1MB。
* **options**(Dict[str, Any], 可选)：扩展配置选项。

**返回**：

**[DownloadFileResult](./result.md#class-downloadfileresult)**，下载文件结果。

### abstractmethod async download_file_stream

```python
abstractmethod async download_file_stream(
    source_path: str,
    local_path: str,
    overwrite: bool = False,
    create_parent_dirs: bool = True,
    preserve_permissions: bool = True,
    chunk_size: int = 1024 * 1024,
    options: Optional[Dict[str, Any]] = None) -> AsyncIterator[DownloadFileStreamResult]
```

异步流式下载文件。

**参数**：

* **source_path**(str)：源文件路径。
* **local_path**(str)：本地路径。
* **overwrite**(bool, 可选)：是否覆盖现有本地文件。`True`表示覆盖现有本地文件，`False`表示不覆盖现有本地文件。默认值：`False`。
* **create_parent_dirs**(bool, 可选)：是否自动创建目标父目录。`True`表示自动创建目标父目录，`False`表示不自动创建目标父目录。默认值：`True`。
* **preserve_permissions**(bool, 可选)：是否保留文件权限。`True`表示保留文件权限，`False`表示不保留文件权限。默认值：`True`。
* **chunk_size**(int, 可选)：传输分块大小。默认值：1MB。
* **options**(Dict[str, Any], 可选)：扩展配置选项。

**返回**：

**AsyncIterator[[DownloadFileStreamResult](./result.md#class-downloadfilestreamresult)]**，流式下载文件结果。

### abstractmethod async list_files

```python
abstractmethod async list_files(
    path: str,
    recursive: bool = False, 
    max_depth: Optional[int] = None,
    sort_by: Literal['name', 'modified_time', 'size'] = "name", 
    sort_descending: bool = False, 
    file_types: Optional[List[str]] = None,
    options: Optional[Dict[str, Any]] = None) -> ListFilesResult
```

异步列出指定路径下的文件。

**参数**：

* **path**(str)：目标父目录路径。
* **recursive**(bool, 可选)：是否递归列出子目录中的文件。`True`表示递归列出子目录中的文件，`False`表示不递归列出子目录中的文件。默认值：`False`。
* **max_depth**(int, 可选)：最大递归深度。在需要递归列出子目录中的文件时进行配置。
* **sort_by**(Literal['name', 'modified_time', 'size'], 可选)：排序字段。默认值："name"。
* **sort_descending**(bool, 可选)：是否降序排列。`True`表示降序排列，`False`表示升序排列。默认值：`False`。
* **file_types**(List[str], 可选)：按扩展名过滤文件，例如` ['.txt', '.pdf']`。
* **options**(Dict[str, Any], 可选)：扩展配置选项。

**返回**：

**[ListFilesResult](./result.md#class-listfilesresult)**，文件列表结果。

### abstractmethod async list_directories

```python
abstractmethod async list_directories(
    path: str,
    recursive: bool = False,
    max_depth: Optional[int] = None,
    sort_by: Literal['name', 'modified_time', 'size'] = "name", 
    sort_descending: bool = False,
    options: Optional[Dict[str, Any]] = None) -> ListDirsResult
```

异步列出指定路径下的目录。

**参数**：

* **path**(str)：目标父目录路径。
* **recursive**(bool, 可选)：是否递归列出子目录中的文件。`True`表示递归列出子目录中的文件，`False`表示不递归列出子目录中的文件。默认值：`False`。
* **max_depth**(int, 可选)：最大递归深度。在需要递归列出子目录中的文件时进行配置。
* **sort_by**(Literal['name', 'modified_time', 'size'], 可选)：排序字段。默认值："name"。
* **sort_descending**(bool, 可选)：是否降序排列。`True`表示降序排列，`False`表示升序排列。默认值：`False`。
* **options**(Dict[str, Any], 可选)：扩展配置选项。

**返回**：

**[ListDirsResult](./result.md#class-listdirsresult)**，目录列表结果。

### abstractmethod async search_files

```python
abstractmethod async search_files(
    path: str, 
    pattern: str,
    exclude_patterns: Optional[List[str]] = None) -> SearchFilesResult
```

异步搜索指定路径下的文件。

**参数**：

* **path**(str)：搜索起始目录路径。
* **pattern**(str)：文件名匹配模式。
* **exclude_patterns**(List[str], 可选)：排除模式列表。

**返回**：

**[SearchFilesResult](./result.md#class-searchfilesresult)**，搜索文件结果。