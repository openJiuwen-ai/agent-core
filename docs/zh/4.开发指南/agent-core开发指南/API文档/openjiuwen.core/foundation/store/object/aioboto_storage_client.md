# openjiuwen.core.foundation.store.object.aioboto_storage_client

## class AioBotoClient

```python
class openjiuwen.core.foundation.store.object.aioboto_storage_client.AioBotoClient(server: str = None, access_key_id: str = None, secret_access_key: str = None, region_name: str = None)
```

基于 aioboto3 的异步 S3 客户端实现，支持 S3 兼容的对象存储服务（如华为云 OBS）。

对应源码：`openjiuwen.core.foundation.store.object.aioboto_storage_client.AioBotoClient`。

**参数**：

- `server: str`：对象存储服务端点 URL，可选。默认值：`None`（从环境变量 `OBS_SERVER` 读取）。
- `access_key_id: str`：访问密钥 ID，可选。默认值：`None`（从环境变量 `OBS_ACCESS_KEY_ID` 读取）。
- `secret_access_key: str`：秘密访问密钥，可选。默认值：`None`（从环境变量 `OBS_SECRET_ACCESS_KEY` 读取）。
- `region_name: str`：区域名称，可选。默认值：`None`（从环境变量 `OBS_REGION` 读取）。

### create_client

```python
def create_client(self)
```

创建并返回 S3 客户端实例。

**返回**：

- S3 客户端实例。

### async create_bucket

```python
async def create_bucket(bucket_name: str, location: str)
```

创建新的对象存储桶。

**参数**：

- `bucket_name: str`：要创建的桶名称。
- `location: str`：创建桶的区域/位置。

### async delete_bucket

```python
async def delete_bucket(bucket_name: str)
```

删除现有的对象存储桶。

**参数**：

- `bucket_name: str`：要删除的桶名称。

### async upload_file

```python
async def upload_file(bucket_name: str, object_name: str, file_path: str | Path)
```

将本地文件上传到对象存储桶。

**参数**：

- `bucket_name: str`：目标桶名称。
- `object_name: str`：对象键（路径/名称）。
- `file_path: str | Path`：要上传的本地文件路径。

### async download_file

```python
async def download_file(bucket_name: str, object_name: str, file_path: str | Path)
```

从对象存储服务器下载对象。

**参数**：

- `bucket_name: str`：桶名称。
- `object_name: str`：要下载的对象键。
- `file_path: str | Path`：保存对象的本地文件路径。

### async delete_object

```python
async def delete_object(bucket_name: str, object_name: str)
```

从对象存储桶中删除对象。

**参数**：

- `bucket_name: str`：桶名称。
- `object_name: str`：要删除的对象键。

### async list_objects

```python
async def list_objects(bucket_name: str, object_prefix: str, max_objects: int = 100) -> List[dict] | None
```

列出对象存储桶中具有指定前缀的对象。

**参数**：

- `bucket_name: str`：桶名称。
- `object_prefix: str`：用于过滤列出对象的前缀。
- `max_objects: int`：一次列出的最大对象数量。默认值：`100`。

**返回**：

- `List[dict] | None`：成功时返回字典对象列表，否则返回 `None`。

---

## 典型使用流程示例

```python
import asyncio
from pathlib import Path
from openjiuwen.core.foundation.store.object.aioboto_storage_client import AioBotoClient


async def main():
    # 初始化客户端（参数可通过环境变量设置）
    client = AioBotoClient(
        server="https://obs.example.com",
        access_key_id="your_access_key",
        secret_access_key="your_secret_key",
        region_name="cn-north-1"
    )
    
    bucket_name = "my-bucket"
    object_name = "test/file.txt"
    local_file = Path("/path/to/local/file.txt")
    
    # 上传文件
    await client.upload_file(bucket_name, object_name, local_file)
    
    # 列出对象
    objects = await client.list_objects(bucket_name, "test/")
    print(f"Found {len(objects) if objects else 0} objects")
    
    # 下载文件
    download_path = Path("/path/to/downloaded/file.txt")
    await client.download_file(bucket_name, object_name, download_path)
    
    # 删除对象
    await client.delete_object(bucket_name, object_name)


if __name__ == "__main__":
    asyncio.run(main())
```

> **参考示例**：更多使用示例请参考 [openJiuwen/agent-core](https://gitcode.com/openJiuwen/agent-core/) 仓库中 `examples/store/` 目录下的示例代码，包括：
> - `showcase_obs.py`：演示 AioBotoClient 的完整操作流程，包括上传、下载、列出对象和删除对象等操作
> - `configs.py`：展示如何配置 OBS 客户端参数（通过环境变量或配置文件）
