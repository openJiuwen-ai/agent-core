# openjiuwen.core.sys_operation.result

## class BaseResult

```python
class BaseResult(Generic[T])
```

Generic data base class for all operation results. `T` represents any type.

**Parameters**:

* **code** (int): Status code. 0 indicates success, non-zero indicates failure.
* **message** (str): Message details.
* **data** (Optional[T], optional): Business data payload. Default value: `None`.

## class ExecuteCodeData

Code execution result data.

**Parameters**:

* **code_content** (str): Original code executed.
* **language** (str): Programming language of the original code.
* **exit_code** (int, optional): Execution exit code. Default value: `None`.
* **stdout** (str, optional): Standard output stream. Default value: `""`.
* **stderr** (str, optional): Standard error stream. Default value: `""`.

## class ExecuteCodeChunkData

Chunk data during streaming execution.

**Parameters**:

* **text** (str, optional): Original content of the output chunk. Default value: `""`.
* **type** (Literal["stdout", "stderr"]): Type of the output chunk. Default value: `None`.
* **chunk_index** (int): Index of the current chunk. Starts from `0`.
* **exit_code** (int, optional): Execution exit code. Default value: `None`.
* **metadata** (Dict[str, Any], optional): Execution data. Default value: `None`.

## class ExecuteCodeResult

```python
class ExecuteCodeResult(BaseResult[ExecuteCodeData])
```

`ExecuteCodeResult` is the data class for code execution results, inheriting from [BaseResult](#class-baseresult).

## class ExecuteCodeStreamResult

```python
class ExecuteCodeStreamResult(BaseResult[ExecuteCodeChunkData])
```

`ExecuteCodeStreamResult` is the data class for streaming code execution results, inheriting from [BaseResult](#class-baseresult).

## class ExecuteCmdData

Shell command execution result data.

**Parameters**:

* **command** (str): Original Shell command executed.
* **cwd** (str, optional): Current working directory. Default value: `"."`.
* **exit_code** (int, optional): Command exit code. Default value: `None`.
* **stdout** (str, optional): Standard output stream. Default value: `""`.
* **stderr** (str, optional): Standard error stream. Default value: `""`.

## class ExecuteCmdChunkData

Shell streaming execution chunk data.

**Parameters**:

* **text** (str, optional): Original content of the output chunk. Default value: `""`.
* **type** (Literal["stdout", "stderr"]): Type of the output chunk. Default value: `None`.
* **chunk_index** (int): Index of the current chunk. Starts from `0`.
* **exit_code** (int, optional): Command exit code. Default value: `None`.
* **metadata** (Dict[str, Any], optional): Command data. Default value: `None`.

## class ExecuteCmdResult

```python
class ExecuteCmdResult(BaseResult[ExecuteCmdData])
```

`ExecuteCmdResult` is the data class for Shell command execution results, inheriting from [BaseResult](#class-baseresult).

## class ExecuteCmdStreamResult

```python
class ExecuteCmdStreamResult(BaseResult[ExecuteCmdChunkData])
```

`ExecuteCmdStreamResult` is the data class for streaming Shell command execution results, inheriting from [BaseResult](#class-baseresult).

## class FileSystemItem

Basic attributes of files or directories.

**Parameters**:

* **name** (str): File/directory name.
* **path** (str): Complete absolute path of the file/directory.
* **size** (int): Size. Unit: bytes.
* **modified_time** (str): Last modification time.
* **is_directory** (bool): Whether it is a directory.
* **type** (str, optional): File extension, only available for files. Default value: `None`.

## class FileSystemData

Result data for listing files or directories.

**Parameters**:

* **total_count** (int): Total number of items.
* **list_items** (List[FileSystemItem]): List of file/directory details.
* **root_path** (str): Original input directory path.
* **recursive** (bool): Whether to recursively list files in subdirectories. `True` means recursively list files in subdirectories, `False` means do not recursively list files in subdirectories.
* **max_depth** (int, optional): Maximum recursion depth. Default value: `None`.

## class SearchFilesData

Result data for searching files.

**Parameters**:

* **total_matches** (int): Total number of matching files.
* **matching_files** (List[FileSystemItem]): List of matching files.
* **search_path** (str): Search starting path.
* **search_pattern** (str): Search pattern.
* **exclude_patterns** (List[str], optional): List of exclusion patterns. Default value: `None`.

## class ReadFileData

Read file data.

**Parameters**:

* **path** (str): File path.
* **content** (Union[str, bytes]): File content.
* **mode** (Literal['text', 'bytes']): Read mode.

## class ReadFileChunkData

Read file chunk data.

**Parameters**:

* **path** (str): File path.
* **chunk_content** (Union[str, bytes]): Current chunk content.
* **mode** (Literal['text', 'bytes']): Read mode.
* **chunk_size** (int): Chunk size. Unit: bytes.
* **chunk_index** (int): Chunk index.
* **is_last_chunk** (bool): Whether it is the last chunk.

## class WriteFileData

Write file data.

**Parameters**:

* **path** (str): File path.
* **size** (int): File content size. Unit: bytes.
* **mode** (Literal['text', 'bytes']): Write mode.

## class UploadFileData

Upload file data.

**Parameters**:

* **local_path** (str): Local file path.
* **target_path** (str): Target file path.
* **size** (int): File content size. Unit: bytes.

## class UploadFileChunkData

Upload file chunk data.

**Parameters**:

* **local_path** (str): Local file path.
* **target_path** (str): Target file path.
* **chunk_size** (int): Chunk size. Unit: bytes.
* **chunk_index** (int): Chunk index.
* **is_last_chunk** (bool): Whether it is the last chunk.

## class DownloadFileData

Download file data.

**Parameters**:

* **source_path** (str): Source file path.
* **local_path** (str): Local file path.
* **size** (int): File content size. Unit: bytes.

## class DownloadFileChunkData

Download file chunk data.

**Parameters**:

* **source_path** (str): Source file path.
* **local_path** (str): Local file path.
* **chunk_size** (int): Chunk size. Unit: bytes.
* **chunk_index** (int): Chunk index.
* **is_last_chunk** (bool): Whether it is the last chunk.

## class ReadFileResult

```python
class ReadFileResult(BaseResult[ReadFileData])
```

`ReadFileResult` is the data class for read file results, inheriting from [BaseResult](#class-baseresult).

## class ReadFileStreamResult

```python
class ReadFileStreamResult(BaseResult[ReadFileChunkData])
```

`ReadFileStreamResult` is the data class for streaming read file results, inheriting from [BaseResult](#class-baseresult).

## class WriteFileResult

```python
class WriteFileResult(BaseResult[WriteFileData])
```

`WriteFileResult` is the data class for write file results, inheriting from [BaseResult](#class-baseresult).

## class UploadFileResult

```python
class UploadFileResult(BaseResult[UploadFileData])
```

`UploadFileResult` is the data class for upload file results, inheriting from [BaseResult](#class-baseresult).

## class UploadFileStreamResult

```python
class UploadFileStreamResult(BaseResult[UploadFileChunkData])
```

`UploadFileStreamResult` is the data class for streaming upload file results, inheriting from [BaseResult](#class-baseresult).

## class DownloadFileResult

```python
class DownloadFileResult(BaseResult[DownloadFileData])
```

`DownloadFileResult` is the data class for download file results, inheriting from [BaseResult](#class-baseresult).

## class DownloadFileStreamResult

```python
class DownloadFileStreamResult(BaseResult[DownloadFileChunkData])
```

`DownloadFileStreamResult` is the data class for streaming download file results, inheriting from [BaseResult](#class-baseresult).

## class ListFilesResult

```python
class ListFilesResult(BaseResult[FileSystemData])
```

`ListFilesResult` is the data class for list files results, inheriting from [BaseResult](#class-baseresult).

## class ListDirsResult

```python
class ListDirsResult(BaseResult[FileSystemData])
```

`ListDirsResult` is the data class for list directories results, inheriting from [BaseResult](#class-baseresult).

## class SearchFilesResult

```python
class SearchFilesResult(BaseResult[SearchFilesData])
```

`SearchFilesResult` is the data class for search files results, inheriting from [BaseResult](#class-baseresult).
