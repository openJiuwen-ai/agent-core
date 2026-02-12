# openjiuwen.core.foundation.store.object.aioboto_storage_client

## class AioBotoClient

```python
class openjiuwen.core.foundation.store.object.aioboto_storage_client.AioBotoClient(server: str = None, access_key_id: str = None, secret_access_key: str = None, region_name: str = None)
```

Async S3 client implementation using aioboto3, supporting S3-compatible object storage services (such as Huawei Cloud OBS).

Corresponding source code: `openjiuwen.core.foundation.store.object.aioboto_storage_client.AioBotoClient`.

**Parameters**:

- `server: str`: Object storage service endpoint URL, optional. Default: `None` (read from environment variable `OBS_SERVER`).
- `access_key_id: str`: Access key ID, optional. Default: `None` (read from environment variable `OBS_ACCESS_KEY_ID`).
- `secret_access_key: str`: Secret access key, optional. Default: `None` (read from environment variable `OBS_SECRET_ACCESS_KEY`).
- `region_name: str`: Region name, optional. Default: `None` (read from environment variable `OBS_REGION`).

### create_client

```python
def create_client(self)
```

Create and return an S3 client instance.

**Returns**:

- S3 client instance.

### async create_bucket

```python
async def create_bucket(bucket_name: str, location: str) -> bool
```

Create a new object storage bucket.

**Parameters**:

- `bucket_name: str`: Name of the bucket to be created.
- `location: str`: Region/location where the bucket will be created.

**Returns**:

- `bool`: Returns `True` if creation succeeded, `False` otherwise.

### async delete_bucket

```python
async def delete_bucket(bucket_name: str) -> bool
```

Delete an existing object storage bucket.

**Parameters**:

- `bucket_name: str`: Name of the bucket to be deleted.

**Returns**:

- `bool`: Returns `True` if deletion succeeded, `False` otherwise.

### async upload_file

```python
async def upload_file(bucket_name: str, object_name: str, file_path: str | Path) -> bool
```

Upload a local file to an object storage bucket.

**Parameters**:

- `bucket_name: str`: Name of the target bucket.
- `object_name: str`: Object key (path/name).
- `file_path: str | Path`: Local file path to upload.

**Returns**:

- `bool`: Returns `True` if upload succeeded, `False` otherwise.

### async download_file

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

### async delete_object

```python
async def delete_object(bucket_name: str, object_name: str) -> bool
```

Delete an object from an object storage bucket.

**Parameters**:

- `bucket_name: str`: Name of the bucket.
- `object_name: str`: Object key to delete.

**Returns**:

- `bool`: Returns `True` if deletion succeeded, `False` otherwise.

### async list_objects

```python
async def list_objects(bucket_name: str, object_prefix: str, max_objects: int = 100) -> List[dict] | None
```

List objects in an object storage bucket with a given prefix.

**Parameters**:

- `bucket_name: str`: Name of the bucket.
- `object_prefix: str`: Prefix to filter objects listed.
- `max_objects: int`: Maximum number of objects to be listed at a time. Default: `100`.

**Returns**:

- `List[dict] | None`: List of dict objects if successful, otherwise `None`.

---

## Typical Usage Example

```python
import asyncio
from pathlib import Path
from openjiuwen.core.foundation.store.object.aioboto_storage_client import AioBotoClient


async def main():
    # Initialize client (parameters can be set via environment variables)
    client = AioBotoClient(
        server="https://obs.example.com",
        access_key_id="your_access_key",
        secret_access_key="your_secret_key",
        region_name="cn-north-1"
    )
    
    bucket_name = "my-bucket"
    object_name = "test/file.txt"
    local_file = Path("/path/to/local/file.txt")
    
    # Upload file and check return value
    success = await client.upload_file(bucket_name, object_name, local_file)
    if not success:
        print("Upload failed")
        return
    
    # List objects
    objects = await client.list_objects(bucket_name, "test/")
    print(f"Found {len(objects) if objects else 0} objects")
    
    # Download file and check return value
    download_path = Path("/path/to/downloaded/file.txt")
    success = await client.download_file(bucket_name, object_name, download_path)
    if not success:
        print("Download failed")
        return
    
    # Delete object and check return value
    success = await client.delete_object(bucket_name, object_name)
    if not success:
        print("Delete failed")


if __name__ == "__main__":
    asyncio.run(main())
```

> **Reference Examples**: For more usage examples, please refer to the example code in the `examples/store/` directory of the [openJiuwen/agent-core](https://gitcode.com/openJiuwen/agent-core/) repository, including:
> - `showcase_obs.py`: Demonstrates the complete workflow of AioBotoClient operations, including upload, download, list objects, and delete object operations
> - `configs.py`: Shows how to configure OBS client parameters (via environment variables or configuration files)
