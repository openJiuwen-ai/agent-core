# openjiuwen.core.foundation.store.object.base_storage_client

## class BaseObjectStorageClient

```python
class openjiuwen.core.foundation.store.object.base_storage_client.BaseObjectStorageClient(ABC)
```

Abstract base class for object storage client, defining a unified interface for basic bucket and object operations such as creating buckets, uploading/downloading files, listing objects, and deleting objects.

Corresponding source code: `openjiuwen.core.foundation.store.object.base_storage_client.BaseObjectStorageClient`.

### abstractmethod async upload_file

```python
async def upload_file(bucket_name, object_name, file_path) -> bool
```

Upload a local file to an object storage bucket.

**Parameters**:

- `bucket_name`: Name of the target bucket.
- `object_name`: Object key (path/name).
- `file_path`: Local file path to upload.

**Returns**:

- `bool`: Returns `True` if upload succeeded, `False` otherwise.

### abstractmethod async download_file

```python
async def download_file(bucket_name: str, object_name: str, file_path: str | Path) -> bool
```

Download an object from Object Storage server.

**Parameters**:

- `bucket_name: str`: Name of the bucket.
- `object_name: str`: Object key to download.
- `file_path: str | Path`: Local file path where the object will be saved.

**Returns**:

- `bool`: Returns `True` if download succeeded, `False` otherwise.

### abstractmethod async delete_object

```python
async def delete_object(bucket_name: str, object_name: str) -> bool
```

Delete an object from an object storage bucket.

**Parameters**:

- `bucket_name: str`: Name of the bucket.
- `object_name: str`: Object key to delete.

**Returns**:

- `bool`: Returns `True` if deletion succeeded, `False` otherwise.

### abstractmethod async create_bucket

```python
async def create_bucket(bucket_name: str, location: str) -> bool
```

Create a new object storage bucket.

**Parameters**:

- `bucket_name: str`: Name of the bucket to be created.
- `location: str`: Region/location where the bucket will be created.

**Returns**:

- `bool`: Returns `True` if creation succeeded, `False` otherwise.

### abstractmethod async delete_bucket

```python
async def delete_bucket(bucket_name: str) -> bool
```

Deletes an existing object storage bucket.

**Parameters**:

- `bucket_name: str`: Name of the bucket to be deleted.

**Returns**:

- `bool`: Returns `True` if deletion succeeded, `False` otherwise.

### abstractmethod async list_objects

```python
async def list_objects(bucket_name: str, object_prefix: str, max_objects: int = 100) -> list[dict] | None
```

List objects in an object storage bucket with a given prefix.

**Parameters**:

- `bucket_name: str`: Name of the bucket.
- `object_prefix: str`: Prefix to filter objects listed.
- `max_objects: int`: Maximum number of objects to be listed at a time. Default: `100`.

**Returns**:

- `list[dict] | None`: List of dict objects if successful, otherwise `None`.

---

## Typical Usage Example

```python
from openjiuwen.core.foundation.store.object.base_storage_client import BaseObjectStorageClient
from openjiuwen.core.foundation.store.object.aioboto_storage_client import AioBotoClient


# Custom implementation of BaseObjectStorageClient
class MyObjectStorageClient(BaseObjectStorageClient):
    async def upload_file(self, bucket_name, object_name, file_path) -> bool:
        # Implement upload logic
        # Return True on success, False on failure
        return True
    
    async def download_file(self, bucket_name: str, object_name: str, file_path: str | Path) -> bool:
        # Implement download logic
        # Return True on success, False on failure
        return True
    
    async def delete_object(self, bucket_name: str, object_name: str) -> bool:
        # Implement delete logic
        # Return True on success, False on failure
        return True
    
    async def create_bucket(self, bucket_name: str, location: str) -> bool:
        # Implement create bucket logic
        # Return True on success, False on failure
        return True
    
    async def delete_bucket(self, bucket_name: str) -> bool:
        # Implement delete bucket logic
        # Return True on success, False on failure
        return True
    
    async def list_objects(self, bucket_name: str, object_prefix: str, max_objects: int = 100) -> list[dict] | None:
        # Implement list objects logic
        # Return list of objects on success, None on failure
        return []


# Use built-in implementation AioBotoClient
client = AioBotoClient()

# Upload file and check return value
success = await client.upload_file("my-bucket", "test/file.txt", "/path/to/local/file.txt")
if not success:
    print("Upload failed")

# Download file and check return value
success = await client.download_file("my-bucket", "test/file.txt", "/path/to/downloaded/file.txt")
if not success:
    print("Download failed")
```

> **Reference Examples**: For more usage examples, please refer to the example code in the `examples/store/` directory of the [openJiuwen/agent-core](https://gitcode.com/openJiuwen/agent-core/) repository, including:
> - `showcase_obs.py`: Demonstrates the complete workflow of object storage operations, including upload, download, list objects, and delete object operations
