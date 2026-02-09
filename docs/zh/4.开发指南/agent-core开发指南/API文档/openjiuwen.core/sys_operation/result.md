# openjiuwen.core.sys_operation.result

## class BaseResult

```python
class BaseResult(Generic[T])
```

所有操作结果的通用泛型数据基类。其中，`T`表示任意类型。

**参数**：

* **code**(int)：状态码。0表示成功，非0表示失败。
* **message**(str)：消息详情。
* **data**(Optional[T], 可选)：业务数据载荷。默认值：`None`。

## class ExecuteCodeData

代码执行结果数据。

**参数**：

* **code_content**(str)：执行的原始代码。
* **language**(str)：原始代码的编程语言。
* **exit_code**(int, 可选)：执行退出码。默认值：0。
* **stdout**(str, 可选)：标准输出流。默认值：""。
* **stderr**(str, 可选)：标准错误流。默认值：""。

## class ExecuteCodeChunkData

流式执行时的分片数据。

**参数**：

* **text**(str, 可选)：输出分片的原始内容。默认值：""。
* **type**(Literal["stdout", "stderr"])：输出分片的类型。
* **chunk_index**(int)：当前分片的索引。从0开始。
* **exit_code**(int, 可选)：执行退出码。默认值：0。
* **metadata**(Dict[str, Any], 可选)：执行数据。默认值：`None`。

## class ExecuteCodeResult

```python
class ExecuteCodeResult(BaseResult[ExecuteCodeData])
```

`ExecuteCodeResult`是代码执行结果的数据类，继承于[BaseResult](#class-baseresult)。

## class ExecuteCodeStreamResult

```python
class ExecuteCodeStreamResult(BaseResult[ExecuteCodeChunkData])
```

`ExecuteCodeStreamResult`是代码流式执行结果的数据类，继承于[BaseResult](#class-baseresult)。

## class ExecuteCmdData

Shell命令执行结果数据。

**参数**：

* **command**(str)：执行的原始Shell命令。
* **cwd**(str, 可选)：当前工作目录。默认值："."。
* **exit_code**(int, 可选)：命令退出码。默认值：0。
* **stdout**(str, 可选)：标准输出流。默认值：""。
* **stderr**(str, 可选)：标准错误流。默认值：""。

## class ExecuteCmdChunkData

Shell流式执行分片数据。

**参数**：

* **text**(str, 可选)：输出分片的原始内容。默认值：""。
* **type**(Literal["stdout", "stderr"])：输出分片的类型。
* **chunk_index**(int)：当前分片的索引。从0开始。
* **exit_code**(int, 可选)：命令退出码。默认值：0。
* **metadata**(Dict[str, Any], 可选)：命令数据。默认值：`None`。

## class ExecuteCmdResult

```python
class ExecuteCmdResult(BaseResult[ExecuteCmdData])
```

`ExecuteCmdResult`是Shell命令执行结果的数据类，继承于[BaseResult](#class-baseresult)。

## class ExecuteCmdStreamResult

```python
class ExecuteCmdStreamResult(BaseResult[ExecuteCmdChunkData])
```

`ExecuteCmdStreamResult`是Shell命令流式执行结果的数据类，继承于[BaseResult](#class-baseresult)。

## class FileSystemItem

文件或目录的基本属性。

**参数**：

* **name**(str)：文件/目录名称。
* **path**(str)：文件/目录的完整绝对路径。
* **size**(int)：大小。单位：字节。
* **modified_time**(str)：最后修改时间。
* **is_directory**(bool)：是否为目录。
* **type**(str, 可选)：文件扩展名，仅文件情况下有该参数。默认值：`None`。

## class FileSystemData

列出文件或目录的结果数据。

**参数**：

* **total_count**(int)：项目总数。
* **list_items**(List[FileSystemItem])：文件/目录详情列表。
* **root_path**(str)：原始输入目录路径。
* **recursive**(bool)：是否递归列出子目录中的文件。`True`表示递归列出子目录中的文件，`False`表示不递归列出子目录中的文件。。
* **max_depth**(int, 可选)：最大递归深度。默认值：`None`。

## class SearchFilesData

搜索文件的结果数据。

**参数**：

* **total_matches**(int)：匹配文件总数。
* **matching_files**(List[FileSystemItem])：匹配文件列表。
* **search_path**(str)：搜索起始路径。
* **search_pattern**(str)：搜索模式。
* **exclude_patterns**(List[str], 可选)：排除模式列表。默认值：`None`。

## class ReadFileData

读取文件数据。

**参数**：

* **path**(str)：文件路径。
* **content**(Union[str, bytes])：文件内容。
* **mode**(Literal['text', 'bytes'])：读取模式。

## class ReadFileChunkData

读取文件分片数据。

**参数**：

* **path**(str)：文件路径。
* **chunk_content**(Union[str, bytes])：当前分片内容。
* **mode**(Literal['text', 'bytes'])：读取模式。
* **chunk_size**(int)：分片大小。单位：字节。
* **chunk_index**(int)：分片索引。
* **is_last_chunk**(bool)：是否为最后一个分片。

## class WriteFileData

写入文件数据。

**参数**：

* **path**(str)：文件路径。
* **size**(int)：文件内容大小。单位：字节。
* **mode**(Literal['text', 'bytes'])：写入模式。

## class UploadFileData

上传文件数据。

**参数**：

* **local_path**(str)：本地文件路径。
* **target_path**(str)：目标文件路径。
* **size**(int)：文件内容大小。单位：字节。

## class UploadFileChunkData

上传文件分片数据。

**参数**：

* **local_path**(str)：本地文件路径。
* **target_path**(str)：目标文件路径。
* **chunk_size**(int)：分片大小。单位：字节。
* **chunk_index**(int)：分片索引。
* **is_last_chunk**(bool)：是否为最后一个分片。

## class DownloadFileData

下载文件数据。

**参数**：

* **source_path**(str)：源文件路径。
* **local_path**(str)：本地文件路径。
* **size**(int)：文件内容大小。单位：字节。

## class DownloadFileChunkData

下载文件分片数据。

**参数**：

* **source_path**(str)：源文件路径。
* **local_path**(str)：本地文件路径。
* **chunk_size**(int)：分片大小。单位：字节。
* **chunk_index**(int)：分片索引。
* **is_last_chunk**(bool)：是否为最后一个分片。

## class ReadFileResult

```python
class ReadFileResult(BaseResult[ReadFileData])
```

`ReadFileResult`是读取文件结果的数据类，继承于[BaseResult](#class-baseresult)。

## class ReadFileStreamResult

```python
class ReadFileStreamResult(BaseResult[ReadFileChunkData])
```

`ReadFileStreamResult`是流式读取文件结果的数据类，继承于[BaseResult](#class-baseresult)。

## class WriteFileResult

```python
class WriteFileResult(BaseResult[WriteFileData])
```

`WriteFileResult`是写入文件结果的数据类，继承于[BaseResult](#class-baseresult)。

## class UploadFileResult

```python
class UploadFileResult(BaseResult[UploadFileData])
```

`UploadFileResult`是上传文件结果的数据类，继承于[BaseResult](#class-baseresult)。

## class UploadFileStreamResult

```python
class UploadFileStreamResult(BaseResult[UploadFileChunkData])
```

`UploadFileStreamResult`是流式上传文件结果的数据类，继承于[BaseResult](#class-baseresult)。

## class DownloadFileResult

```python
class DownloadFileResult(BaseResult[DownloadFileData])
```

`DownloadFileResult`是下载文件结果的数据类，继承于[BaseResult](#class-baseresult)。

## class DownloadFileStreamResult

```python
class DownloadFileStreamResult(BaseResult[DownloadFileChunkData])
```

`DownloadFileResult`是流式下载文件结果的数据类，继承于[BaseResult](#class-baseresult)。

## class ListFilesResult

```python
class ListFilesResult(BaseResult[FileSystemData])
```

`ListFilesResult`是列出文件结果的数据类，继承于[BaseResult](#class-baseresult)。

## class ListDirsResult

```python
class ListDirsResult(BaseResult[FileSystemData])
```

`ListDirsResult`是列出目录结果的数据类，继承于[BaseResult](#class-baseresult)。

## class SearchFilesResult

```python
class SearchFilesResult(BaseResult[SearchFilesData])
```

`SearchFilesResult`是搜索文件结果的数据类，继承于[BaseResult](#class-baseresult)。
