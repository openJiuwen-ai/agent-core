# openjiuwen.core.foundation.store.object.base_storage_client

## class BaseObjectStorageClient

```python
class openjiuwen.core.foundation.store.object.base_storage_client.BaseObjectStorageClient(ABC)
```

对象存储客户端抽象基类，定义统一的桶和对象操作接口，包括创建桶、上传/下载文件、列出对象和删除对象等基本操作。

对应源码：`openjiuwen.core.foundation.store.object.base_storage_client.BaseObjectStorageClient`。

### abstractmethod async upload_file

```python
async def upload_file(bucket_name, object_name, file_path) -> bool
```

将本地文件上传到对象存储桶。

**参数**：

- `bucket_name`：目标桶名称。
- `object_name`：对象键（路径/名称）。
- `file_path`：要上传的本地文件路径。

**返回**：

- `bool`：上传成功返回 `True`，否则返回 `False`。

### abstractmethod async download_file

```python
async def download_file(bucket_name: str, object_name: str, file_path: str | Path) -> bool
```

从对象存储服务器下载对象。

**参数**：

- `bucket_name: str`：桶名称。
- `object_name: str`：要下载的对象键。
- `file_path: str | Path`：保存对象的本地文件路径。

**返回**：

- `bool`：下载成功返回 `True`，否则返回 `False`。

### abstractmethod async delete_object

```python
async def delete_object(bucket_name: str, object_name: str) -> bool
```

从对象存储桶中删除对象。

**参数**：

- `bucket_name: str`：桶名称。
- `object_name: str`：要删除的对象键。

**返回**：

- `bool`：删除成功返回 `True`，否则返回 `False`。

### abstractmethod async create_bucket

```python
async def create_bucket(bucket_name: str, location: str) -> bool
```

创建新的对象存储桶。

**参数**：

- `bucket_name: str`：要创建的桶名称。
- `location: str`：创建桶的区域/位置。

**返回**：

- `bool`：创建成功返回 `True`，否则返回 `False`。

### abstractmethod async delete_bucket

```python
async def delete_bucket(bucket_name: str) -> bool
```

删除现有的对象存储桶。

**参数**：

- `bucket_name: str`：要删除的桶名称。

**返回**：

- `bool`：删除成功返回 `True`，否则返回 `False`。

### abstractmethod async list_objects

```python
async def list_objects(bucket_name: str, object_prefix: str, max_objects: int = 100) -> list[dict] | None
```

列出对象存储桶中具有指定前缀的对象。

**参数**：

- `bucket_name: str`：桶名称。
- `object_prefix: str`：用于过滤列出对象的前缀。
- `max_objects: int`：一次列出的最大对象数量。默认值：`100`。

**返回**：

- `list[dict] | None`：成功时返回字典对象列表，否则返回 `None`。

---

## 典型使用流程示例

```python
from openjiuwen.core.foundation.store.object.base_storage_client import BaseObjectStorageClient
from openjiuwen.core.foundation.store.object.aioboto_storage_client import AioBotoClient


# 自定义实现 BaseObjectStorageClient
class MyObjectStorageClient(BaseObjectStorageClient):
    async def upload_file(self, bucket_name, object_name, file_path) -> bool:
        # 实现上传逻辑
        # 成功返回 True，失败返回 False
        return True
    
    async def download_file(self, bucket_name: str, object_name: str, file_path: str | Path) -> bool:
        # 实现下载逻辑
        # 成功返回 True，失败返回 False
        return True
    
    async def delete_object(self, bucket_name: str, object_name: str) -> bool:
        # 实现删除逻辑
        # 成功返回 True，失败返回 False
        return True
    
    async def create_bucket(self, bucket_name: str, location: str) -> bool:
        # 实现创建桶逻辑
        # 成功返回 True，失败返回 False
        return True
    
    async def delete_bucket(self, bucket_name: str) -> bool:
        # 实现删除桶逻辑
        # 成功返回 True，失败返回 False
        return True
    
    async def list_objects(self, bucket_name: str, object_prefix: str, max_objects: int = 100) -> list[dict] | None:
        # 实现列出对象逻辑
        # 成功返回对象列表，失败返回 None
        return []


# 使用内置实现 AioBotoClient
client = AioBotoClient()

# 上传文件并检查返回值
success = await client.upload_file("my-bucket", "test/file.txt", "/path/to/local/file.txt")
if not success:
    print("上传失败")

# 下载文件并检查返回值
success = await client.download_file("my-bucket", "test/file.txt", "/path/to/downloaded/file.txt")
if not success:
    print("下载失败")
```

> **参考示例**：更多使用示例请参考 [openJiuwen/agent-core](https://gitcode.com/openJiuwen/agent-core/) 仓库中 `examples/store/` 目录下的示例代码，包括：
> - `showcase_obs.py`：演示对象存储的完整操作流程，包括上传、下载、列出对象和删除对象等操作
