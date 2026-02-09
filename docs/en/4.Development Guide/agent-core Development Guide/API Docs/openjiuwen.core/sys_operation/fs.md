# openjiuwen.core.sys_operation.fs

## class BaseFsOperation

```python
class BaseFsOperation()
```

`BaseFsOperation` is the abstract base class providing file read/write, upload/download, list/search and other functionalities, inheriting from [BaseOperation](./base.md#class-baseoperation).

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

Asynchronously read file with specified mode and parameters.

**Parameters**:

* **path** (str): Complete or relative path of the file to read.
* **mode** (Literal['text', 'bytes'], optional): Read mode. 'text' means read file in text mode, 'bytes' means read file in binary byte stream mode. Default value: "text".
* **head** (int, optional): Number of lines to read from the beginning, text mode only.
* **tail** (int, optional): Number of lines to read from the end, text mode only.
* **line_range** (Tuple[int, int], optional): Specific line range to read, line index starts from 1, text mode only. Note: The three parameters `head`, `tail`, and `line_range` are mutually exclusive.
* **encoding** (str, optional): Character encoding for text mode. Default value: "utf-8".
* **chunk_size** (int, optional): Buffer size for binary byte stream mode reading. Unit: bytes. Default value: 8192.
* **options** (Dict[str, Any], optional): Extended configuration options.

**Returns**:

**[ReadFileResult](./result.md#class-readfileresult)**, read file result.

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

Asynchronously read file in streaming mode with specified mode and parameters.

**Parameters**:

* **path** (str): Complete or relative path of the file to read.
* **mode** (Literal['text', 'bytes'], optional): Read mode. 'text' means read file in text mode, 'bytes' means read file in binary byte stream mode. Default value: "text".
* **head** (int, optional): Number of lines to read from the beginning, text mode only.
* **tail** (int, optional): Number of lines to read from the end, text mode only.
* **line_range** (Tuple[int, int], optional): Specific line range to read, line index starts from 1, text mode only. Note: The three parameters `head`, `tail`, and `line_range` are mutually exclusive, it is not recommended to pass them simultaneously.
* **encoding** (str, optional): Character encoding for text mode. Default value: "utf-8".
* **chunk_size** (int, optional): Buffer size for binary byte stream mode reading. Unit: bytes. Default value: 8192.
* **options** (Dict[str, Any], optional): Extended configuration options.

**Returns**:

**AsyncIterator[[ReadFileStreamResult](./result.md#class-readfilestreamresult)]**, streaming read file result.

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

Asynchronously write content to file.

**Parameters**:

* **path** (str): Complete or relative path of the file to write.
* **content** (str | bytes): Data to write. Pass `str` type for text mode, `bytes` type for binary byte stream mode.
* **mode** (Literal['text', 'bytes'], optional): Write mode. 'text' means write file in text mode, 'bytes' means write file in binary byte stream mode. Default value: "text".
* **prepend_newline** (bool, optional): Whether to add newline before content, text mode only. `True` means add newline before content, `False` means do not add newline before content. Default value: `True`.
* **append_newline** (bool, optional): Whether to add newline after content, text mode only. `True` means add newline after content, `False` means do not add newline after content. Default value: `False`.
* **create_if_not_exist** (bool, optional): Whether to automatically create if file does not exist. `True` means automatically create if file does not exist, `False` means do not automatically create if file does not exist. Default value: `True`.
* **permissions** (str, optional): Octal file permissions, Unix/Linux systems only. Default value: "644".
* **encoding** (str, optional): Character encoding. Default value: "utf-8".
* **options** (Dict[str, Any], optional): Extended configuration options.

**Returns**:

**[WriteFileResult](./result.md#class-writefileresult)**, write file result.

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

Asynchronously upload file.

**Parameters**:

* **local_path** (str): Local source file path.
* **target_path** (str): Upload target path.
* **overwrite** (bool, optional): Whether to overwrite existing target file. `True` means overwrite existing target file, `False` means do not overwrite existing target file. Default value: `False`.
* **create_parent_dirs** (bool, optional): Whether to automatically create target parent directories. `True` means automatically create target parent directories, `False` means do not automatically create target parent directories. Default value: `True`.
* **preserve_permissions** (bool, optional): Whether to preserve file permissions. `True` means preserve file permissions, `False` means do not preserve file permissions. Default value: `True`.
* **chunk_size** (int, optional): Transfer chunk size. Default value: 1MB.
* **options** (Dict[str, Any], optional): Extended configuration options.

**Returns**:

**[UploadFileResult](./result.md#class-uploadfileresult)**, upload file result.

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

Asynchronously upload file in streaming mode.

**Parameters**:

* **local_path** (str): Local source file path.
* **target_path** (str): Upload target path.
* **overwrite** (bool, optional): Whether to overwrite existing target file. `True` means overwrite existing target file, `False` means do not overwrite existing target file. Default value: `False`.
* **create_parent_dirs** (bool, optional): Whether to automatically create target parent directories. `True` means automatically create target parent directories, `False` means do not automatically create target parent directories. Default value: `True`.
* **preserve_permissions** (bool, optional): Whether to preserve file permissions. `True` means preserve file permissions, `False` means do not preserve file permissions. Default value: `True`.
* **chunk_size** (int, optional): Transfer chunk size. Default value: 1MB.
* **options** (Dict[str, Any], optional): Extended configuration options.

**Returns**:

**AsyncIterator[[UploadFileStreamResult](./result.md#class-uploadfilestreamresult)]**, streaming upload file result.

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

Asynchronously download file.

**Parameters**:

* **source_path** (str): Source file path.
* **local_path** (str): Local path.
* **overwrite** (bool, optional): Whether to overwrite existing local file. `True` means overwrite existing local file, `False` means do not overwrite existing local file. Default value: `False`.
* **create_parent_dirs** (bool, optional): Whether to automatically create target parent directories. `True` means automatically create target parent directories, `False` means do not automatically create target parent directories. Default value: `True`.
* **preserve_permissions** (bool, optional): Whether to preserve file permissions. `True` means preserve file permissions, `False` means do not preserve file permissions. Default value: `True`.
* **chunk_size** (int, optional): Transfer chunk size. Default value: 1MB.
* **options** (Dict[str, Any], optional): Extended configuration options.

**Returns**:

**[DownloadFileResult](./result.md#class-downloadfileresult)**, download file result.

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

Asynchronously download file in streaming mode.

**Parameters**:

* **source_path** (str): Source file path.
* **local_path** (str): Local path.
* **overwrite** (bool, optional): Whether to overwrite existing local file. `True` means overwrite existing local file, `False` means do not overwrite existing local file. Default value: `False`.
* **create_parent_dirs** (bool, optional): Whether to automatically create target parent directories. `True` means automatically create target parent directories, `False` means do not automatically create target parent directories. Default value: `True`.
* **preserve_permissions** (bool, optional): Whether to preserve file permissions. `True` means preserve file permissions, `False` means do not preserve file permissions. Default value: `True`.
* **chunk_size** (int, optional): Transfer chunk size. Default value: 1MB.
* **options** (Dict[str, Any], optional): Extended configuration options.

**Returns**:

**AsyncIterator[[DownloadFileStreamResult](./result.md#class-downloadfilestreamresult)]**, streaming download file result.

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

Asynchronously list files under the specified path.

**Parameters**:

* **path** (str): Target parent directory path.
* **recursive** (bool, optional): Whether to recursively list files in subdirectories. `True` means recursively list files in subdirectories, `False` means do not recursively list files in subdirectories. Default value: `False`.
* **max_depth** (int, optional): Maximum recursion depth. Configure when recursively listing files in subdirectories is needed.
* **sort_by** (Literal['name', 'modified_time', 'size'], optional): Sort field. Default value: "name".
* **sort_descending** (bool, optional): Whether to sort in descending order. `True` means descending order, `False` means ascending order. Default value: `False`.
* **file_types** (List[str], optional): Filter files by extension, e.g., `['.txt', '.pdf']`.
* **options** (Dict[str, Any], optional): Extended configuration options.

**Returns**:

**[ListFilesResult](./result.md#class-listfilesresult)**, file list result.

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

Asynchronously list directories under the specified path.

**Parameters**:

* **path** (str): Target parent directory path.
* **recursive** (bool, optional): Whether to recursively list files in subdirectories. `True` means recursively list files in subdirectories, `False` means do not recursively list files in subdirectories. Default value: `False`.
* **max_depth** (int, optional): Maximum recursion depth. Configure when recursively listing files in subdirectories is needed.
* **sort_by** (Literal['name', 'modified_time', 'size'], optional): Sort field. Default value: "name".
* **sort_descending** (bool, optional): Whether to sort in descending order. `True` means descending order, `False` means ascending order. Default value: `False`.
* **options** (Dict[str, Any], optional): Extended configuration options.

**Returns**:

**[ListDirsResult](./result.md#class-listdirsresult)**, directory list result.

### abstractmethod async search_files

```python
abstractmethod async search_files(
    path: str, 
    pattern: str,
    exclude_patterns: Optional[List[str]] = None) -> SearchFilesResult
```

Asynchronously search files under the specified path.

**Parameters**:

* **path** (str): Search starting directory path.
* **pattern** (str): File name matching pattern.
* **exclude_patterns** (List[str], optional): List of exclusion patterns.

**Returns**:

**[SearchFilesResult](./result.md#class-searchfilesresult)**, search files result.
