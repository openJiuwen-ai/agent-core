# openjiuwen.core.foundation.store.base_memory_index / index.simple_memory_index

`openjiuwen.core.foundation.store` 在 KV 存储与向量存储之上，额外提供 **记忆索引（memory index）** 抽象与一份面向旧数据的兼容实现：

- 定义 `StorageCodec` 协议，用于在写入/读取记忆文本时做编解码（例如 AES 加解密）；
- 定义 `MemoryDoc` 数据模型，封装单条记忆的 `id` / `text` / `type` / `timestamp` / 扩展字段；
- 定义 `BaseMemoryIndex` 抽象基类，规定记忆的增删改查、按用户/作用域批量删除、语义检索、分页列举、备份与版本管理等接口；
- 提供 `SimpleMemoryIndex`，基于 `BaseKVStore` + `BaseVectorStore` 实现上述接口，**仅为兼容旧版 `SemanticStore + UserMemStore` 数据而保留，已弃用**。

对应源码：`openjiuwen.core.foundation.store.base_memory_index`、`openjiuwen.core.foundation.store.index.simple_memory_index`。

## class StorageCodec

```python
class openjiuwen.core.foundation.store.base_memory_index.StorageCodec(
    Protocol
)
```

记忆文本编解码协议（`typing.Protocol`，带 `@runtime_checkable`）。实现类需提供 `encode` / `decode` 两个方法，用于在记忆写入 KV 存储前对文本做编码（如加密）、在读取后做解码（如解密）。

```python
class AesStorageCodec:
    def __init__(self, key: bytes):
        self._key = key

    def encode(self, text: str) -> str:
        return aes_encrypt(text)

    def decode(self, data: str) -> str:
        return aes_decrypt(data)

index = SimpleMemoryIndex(...)
index.set_storage_codec(AesStorageCodec(key=b"..."))
```

### encode

```python
def encode(self, text: str) -> str
```

将明文记忆文本编码为可持久化的字符串（如密文）。

**参数**：

- `text: str`：明文记忆文本。

**返回**：`str`，编码后的字符串。

### decode

```python
def decode(self, data: str) -> str
```

将持久化的字符串解码回明文记忆文本。

**参数**：

- `data: str`：编码后的字符串。

**返回**：`str`，解码后的明文文本。

## class MemoryDoc

```python
class openjiuwen.core.foundation.store.base_memory_index.MemoryDoc(BaseModel)
```

单条记忆文档的数据模型（`pydantic.BaseModel`），封装记忆的唯一标识、文本内容、类型与时间戳，并支持任意扩展字段。

**字段**：

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `id` | `str` | `""` | 记忆文档的唯一标识。 |
| `text` | `str` | `""` | 记忆的文本内容。 |
| `type` | `str` | `""` | 记忆的类型/分类（如 `user_profile`）。 |
| `timestamp` | `datetime` | 当前本地时区时间（`datetime.now(timezone.utc).astimezone()`） | 记忆条目的时间戳。 |
| `fields` | `dict[str, Any]` | `{}` | 额外的扩展字段。 |

## class BaseMemoryIndex

```python
class openjiuwen.core.foundation.store.base_memory_index.BaseMemoryIndex(ABC)
```

记忆索引的抽象基类，定义记忆存储与检索的统一接口。具体实现应基于特定的后端（向量库、数据库等）持久化与检索 `MemoryDoc`。

记忆文档按 `user_id` 与 `scope_id` 双维度划分作用域，支持多租户、多场景的记忆管理。子类需实现下列标注 `abstractmethod` 的方法；`list_memories` / `get_schema_version` / `update_schema_version` / `create_backup` / `restore_backup` 已提供默认（空）实现，子类按需覆写。

对应源码：`openjiuwen.core.foundation.store.base_memory_index.BaseMemoryIndex`。

### abstractmethod set_storage_codec

```python
def set_storage_codec(self, codec: StorageCodec) -> None
```

设置记忆文本的编解码器，供后续写入/读取时对文本做加解密等处理。

**参数**：

- `codec: StorageCodec`：实现 `encode` / `decode` 的编解码器实例。

### abstractmethod async add_memories

```python
async def add_memories(self, user_id: str, scope_id: str, memories: list[MemoryDoc])
```

新增记忆文档。

**参数**：

- `user_id: str`：用户标识，记忆按此划分作用域。
- `scope_id: str`：作用域标识，用于分组相关记忆。
- `memories: list[MemoryDoc]`：待新增的记忆文档列表。

### abstractmethod async update_memories

```python
async def update_memories(self, user_id: str, scope_id: str, memories: list[MemoryDoc])
```

更新记忆文档。

**参数**：

- `user_id: str`：用户标识。
- `scope_id: str`：作用域标识。
- `memories: list[MemoryDoc]`：待更新的记忆文档列表。

### abstractmethod async delete_memories

```python
async def delete_memories(self, user_id: str, scope_id: str, ids: list[str])
```

按 ID 删除指定记忆文档。

**参数**：

- `user_id: str`：用户标识。
- `scope_id: str`：作用域标识。
- `ids: list[str]`：待删除的记忆文档 ID 列表。

### abstractmethod async delete_by_user

```python
async def delete_by_user(self, user_id: str)
```

删除某用户在所有作用域下的全部记忆。

**参数**：

- `user_id: str`：待清空记忆的用户标识。

### abstractmethod async delete_by_scope

```python
async def delete_by_scope(self, scope_id: str)
```

删除某作用域下所有用户的全部记忆。

**参数**：

- `scope_id: str`：待清空记忆的作用域标识。

### abstractmethod async delete_by_user_and_scope

```python
async def delete_by_user_and_scope(self, user_id: str, scope_id: str)
```

删除指定用户与作用域组合下的全部记忆。

**参数**：

- `user_id: str`：用户标识。
- `scope_id: str`：作用域标识。

### abstractmethod async search

```python
async def search(
    self,
    user_id: str,
    scope_id: str,
    query: str,
    mem_types: list[str] | None = None,
    top_k: int = 10,
) -> list[tuple[MemoryDoc, float]]
```

对存储的记忆做语义检索，返回最相关的结果及其相关性分数。

**参数**：

- `user_id: str`：用户标识。
- `scope_id: str`：作用域标识。
- `query: str`：检索查询文本。
- `mem_types: list[str] | None`：按记忆类型过滤，可取：
  - 一个或多个具体类型（如 `["user_profile"]`）；
  - 空列表 `[]` 表示检索全部类型。
- `top_k: int = 10`：最多返回的结果数。

**返回**：`list[tuple[MemoryDoc, float]]`，每项为 `(记忆文档, 相关性分数)`，分数通常落在 `[0, 1]`，越大越相关。

### abstractmethod async get_by_id

```python
async def get_by_id(self, user_id: str, scope_id: str, mem_id: str) -> MemoryDoc | None
```

按 `mem_id` 取回单条记忆文档；不存在时返回 `None`。

**参数**：

- `user_id: str`：用户标识。
- `scope_id: str`：作用域标识。
- `mem_id: str`：记忆文档的唯一标识。

**返回**：`MemoryDoc | None`。

### async list_memories

```python
async def list_memories(
    self,
    user_id: str,
    scope_id: str,
    offset: int = 0,
    limit: int = 100,
    mem_types: list[str] | None = None,
) -> list[MemoryDoc]
```

分页列举指定用户与作用域下的记忆文档。基类提供空实现，子类按需覆写。

**参数**：

- `user_id: str`：用户标识。
- `scope_id: str`：作用域标识。
- `offset: int = 0`：起始偏移。
- `limit: int = 100`：最多返回的文档数。
- `mem_types: list[str] | None`：按类型过滤，可取：
  - 一个或多个具体类型（如 `["user_profile"]`）；
  - 空列表 `[]` 表示列举全部类型；
  - 传入多个类型时，结果按 `mem_type` 的给定顺序输出。

**返回**：`list[MemoryDoc]`。

### get_schema_version

```python
def get_schema_version(self) -> int
```

获取当前 schema 版本。基类默认返回 `0`，子类按需覆写。

**返回**：`int`。

### update_schema_version

```python
def update_schema_version(self, version: int) -> None
```

更新 schema 版本。基类提供空实现，子类按需覆写。

**参数**：

- `version: int`：新的版本号。

### async create_backup

```python
async def create_backup(self) -> str
```

创建当前数据的备份，返回备份标识。基类提供空实现，子类按需覆写。

**返回**：`str`，备份标识。

### async restore_backup

```python
async def restore_backup(self, backup_id: str) -> None
```

从备份恢复数据。基类提供空实现，子类按需覆写。

**参数**：

- `backup_id: str`：备份标识。

### abstractmethod async cleanup_backup

```python
async def cleanup_backup(self, backup_id: str) -> None
```

清理指定备份。子类必须实现。

**参数**：

- `backup_id: str`：备份标识。

### abstractmethod async list_user_scopes

```python
async def list_user_scopes(self) -> list[tuple[str, str]]
```

列出索引中所有 `(user_id, scope_id)` 组合。

**返回**：`list[tuple[str, str]]`。

## class SimpleMemoryIndex

```python
class openjiuwen.core.foundation.store.index.simple_memory_index.SimpleMemoryIndex(
    BaseMemoryIndex
)
```

基于 `BaseKVStore` + `BaseVectorStore` 的记忆索引实现，**仅为兼容旧版 `SemanticStore + UserMemStore` 架构写入的数据而保留**。它将记忆元数据写入 KV 存储、将向量写入按 `mem_type` 分开的向量集合，并在两者间维护 ID 追踪。

> **已弃用**：本类仅为兼容旧数据而存在，未来版本可能移除。请勿在其上构建新功能或长期组件；新场景应使用 `openjiuwen.core.memory` 下的记忆实现（如 `LakeBaseMemoryProvider`）。

对应源码：`openjiuwen.core.foundation.store.index.simple_memory_index.SimpleMemoryIndex`。

### __init__

```python
def __init__(
    self,
    kv_store: BaseKVStore,
    vector_store: BaseVectorStore,
    embedding_model: Any = None,
)
```

构造记忆索引实例，绑定 KV 存储、向量存储与可选的 embedding 模型。

**参数**：

- `kv_store: BaseKVStore`：记忆元数据所用的 KV 存储。
- `vector_store: BaseVectorStore`：向量所用的向量存储。
- `embedding_model: Any = None`：用于生成文本向量的 embedding 模型，需提供 `embed_documents(texts)` 与 `embed_query(text)` 两个异步方法。可在构造时传入，也可稍后用 `set_embedding_model` 注入；执行写入/检索前必须已设置，否则 `add_memories` 抛出 `MEMORY_ADD_MEMORY_EXECUTION_ERROR`，`search` 返回空列表。

### set_embedding_model

```python
def set_embedding_model(self, embedding_model: Any) -> None
```

延迟注入 embedding 模型。

**参数**：

- `embedding_model: Any`：embedding 模型实例。

### set_storage_codec

```python
def set_storage_codec(self, codec: StorageCodec) -> None
```

设置记忆文本编解码器。设置后，写入 KV 时会对 `mem` 字段调用 `codec.encode`，读取时调用 `codec.decode`。

**参数**：

- `codec: StorageCodec`：编解码器实例。

### 方法实现说明

下列方法均为 `BaseMemoryIndex` 对应抽象方法的具体实现，签名与基类一致，差异在于内部行为：

| 方法 | 实现要点 |
|---|---|
| `add_memories` | 按 `mem_type` 分组：用 embedding 模型生成向量、按需创建向量集合、写入向量；随后将元数据以 JSON 写入 KV（若设置了 codec 则对 `mem` 字段加解密），并把 ID 追加到全局与分类型的 ID 追踪键。embedding 未初始化时抛 `MEMORY_ADD_MEMORY_EXECUTION_ERROR`。 |
| `search` | 用 embedding 模型生成查询向量；按 `mem_types` 过滤或在未指定时枚举该用户+作用域下所有集合；对每个集合做向量检索拿到命中 ID 与分数，再从 KV 批量取回元数据并解码，最后按分数降序取前 `top_k`。embedding 未初始化时返回空列表。 |
| `update_memories` | 先按 ID 删除旧记忆，再调用 `add_memories` 写入新记忆。 |
| `delete_memories` | 逐个 ID 从 KV 取出 `mem_type`，删除 KV 条目并更新 ID 追踪；随后在该用户+作用域的所有集合中按 ID 删除向量。 |
| `delete_by_user` | 按 `UMD/{user_id}/` 前缀删除 KV，并删除所有以 `uid_{user_id}_gid_` 开头的向量集合。 |
| `delete_by_scope` | 扫描 `UMD/` 前缀下所有键，按第 3 段匹配 `scope_id` 后批量删除；并删除集合名含 `_gid_{scope_id}_mtype_` 的向量集合。 |
| `delete_by_user_and_scope` | 按 `UMD/{user_id}/{scope_id}/` 前缀删除 KV，并删除该用户+作用域下的所有向量集合。 |
| `get_by_id` | 从 KV 取回单条元数据并解码（含 codec 解密），构造 `MemoryDoc` 返回；不存在返回 `None`。 |
| `list_memories` | 从全局 ID 追踪键取出全部 ID，批量从 KV 取回元数据并解码；按 `mem_types` 过滤，并在指定多类型时按类型顺序、时间倒序排序，最后分页返回。 |
| `get_schema_version` / `update_schema_version` | 在实例内维护 `_schema_version` 整数。 |
| `create_backup` | 生成 UUID 备份 ID，将当前 `schema_version` 存入内存备份表。 |
| `restore_backup` | 从内存备份表恢复 `schema_version`；备份不存在时抛 `ValueError`。 |
| `cleanup_backup` | 从内存备份表中移除该备份。 |
| `list_user_scopes` | 扫描 `UMD/` 前缀下所有键，按 `UMD/{user_id}/{scope_id}/...` 解析出 `(user_id, scope_id)` 去重返回。 |

## 实现细节

### 旧版数据布局

`SimpleMemoryIndex` 操作的数据由旧版 `SemanticStore + UserMemStore` 架构写入：

- **KV 键**：`UMD/{user_id}/{scope_id}/{mem_id}`
- **KV 值**：JSON 字符串，含 `id`、`mem`（记忆文本，可能被 codec 加密）、`mem_type`、`timestamp`、`user_id`、`scope_id` 及任意扩展字段。
- **向量集合名**：`uid_{user_id}_gid_{scope_id}_mtype_{mem_type}`，每个 `(用户, 作用域, 类型)` 组合对应一个集合。
- **向量集合 schema**：`id`（`VARCHAR`，主键，最大长度 256）+ `embedding`（`FLOAT_VECTOR`），禁用动态字段。

### KV 键名约定

| 用途 | 键格式 | 说明 |
|---|---|---|
| 单条记忆 | `UMD/{user_id}/{scope_id}/{mem_id}` | 值为记忆元数据 JSON。 |
| 全局 ID 追踪 | `UMD/{user_id}/{scope_id}/ids` | 值为定长 ID 串拼接（每 ID 24 字符）。 |
| 分类型 ID 追踪 | `UMD/{user_id}/{scope_id}/{mem_type}/ids` | 按记忆类型分别追踪 ID。 |

前缀 `UMD` 与分隔符 `/` 由类常量 `_KV_PREFIX`、`_KV_SEP` 定义。

### ID 追踪

为支持列举与按类型过滤，`SimpleMemoryIndex` 在 KV 中维护 ID 追踪键：

- **定长编码**：每个记忆 ID 固定占用 `_BYTE_NUM_PER_ID = 24` 个字符；`_parse_all_ids` 按此长度切分原始串得到 ID 列表。
- **追加**：`_append_id` 直接将 ID 拼到串尾，并先去重。
- **移除**：`_remove_id` 按定长切片定位并删除目标 ID，删除后串为空则删除整个键。
- **追踪维护**：`add_memories` 时同步写入全局与分类型追踪键；`delete_memories` 时同步从中移除。

### 向量集合命名与发现

- 集合名由 `_get_collection_name(user_id, scope_id, mem_type)` 拼成 `uid_{user_id}_gid_{scope_id}_mtype_{mem_type}`。
- `_parse_mem_type_from_collection(name)` 从集合名反解出 `mem_type`（取最后一个 `_mtype_` 之后的部分）。
- `_collections_for(user_id, scope_id)` 列举向量存储中所有以 `uid_{user_id}_gid_{scope_id}_mtype_` 为前缀的集合，用于未显式指定 `mem_types` 时的检索与删除。
- `_ensure_collection(name, dim)` 在首次写入时按向量维度 `dim` 创建集合，并在实例内用 `_created_collections` 缓存以避免重复创建。

### 编解码（codec）集成

若通过 `set_storage_codec` 设置了 `StorageCodec`：

- `add_memories` 写入 KV 前，对元数据的 `mem` 字段调用 `codec.encode`；
- `search` / `get_by_id` / `list_memories` 读取 KV 后，对 `mem` 字段调用 `codec.decode` 还原明文。

未设置 codec 时，`mem` 字段以明文存取。
