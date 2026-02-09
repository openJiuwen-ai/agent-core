# openjiuwen.core.foundation.store

`openjiuwen.core.foundation.store` 是 openJiuwen 中统一的**存储抽象层模块**，负责：

- 定义 `BaseKVStore` 键值存储抽象基类；
- 定义 `VectorStore` 向量存储抽象基类；
- 定义 `BaseDbStore` 数据库存储抽象基类；
- 提供多种存储实现（如 Redis、Chroma、Milvus 等）。


## class openjiuwen.core.foundation.store.BaseKVStore

```
class openjiuwen.core.foundation.store.BaseKVStore
```

键值存储抽象基类，定义了键值对存储的基本接口。

### abstractmethod async set

```
async def set(self, key: str, value: str) -> None
```

设置键值对。

**参数**：

* **key**(str)：设置的键名。
* **value**(str)：设置的值。

**异常**：

* **JiuWenBaseException**：当存储操作失败时抛出。

**样例**：

```python
>>> from openjiuwen.core.foundation.store import BaseKVStore
>>> 
>>> # 设置键值对
>>> await kv_store.set("user:123:name", "张三")
>>> await kv_store.set("user:123:age", "25")
```


### abstractmethod async exclusive_set

```
async def exclusive_set(self, key: str, value: str, expiry: int | None = None) -> bool
```

原子性地设置键值对，仅当该键不存在时才进行设置。

**参数**：

* **key**(str)：设置的键名。
* **value**(str)：设置的值。
* **expiry**(int | None, 可选)：键值对的过期时间（秒），超过后值可被覆盖。默认值：`None`。

**返回**：

* **bool**：如果键值对设置成功则返回 `True`，键已存在并且还未过期则返回 `False`。

**异常**：

* **JiuWenBaseException**：当存储操作失败时抛出。

**样例**：

```python
>>> from openjiuwen.core.foundation.store import BaseKVStore
>>> 
>>> # 原子性设置键值对，仅在键不存在时成功
>>> success = await kv_store.exclusive_set("lock:resource:123", "locked", expiry=60)
>>> if success:
>>>     print("获取锁成功")
>>> else:
>>>     print("锁已被其他进程持有")
```


### abstractmethod async get

```
async def get(self, key: str) -> str | None
```

从数据库获取指定键对应的值，如果没有则返回 `None`。

**参数**：

* **key**(str)：需要获取的键名。

**返回**：

* **str | None**：返回键对应的值，如果键不存在则返回 `None`。

**异常**：

* **JiuWenBaseException**：当存储操作失败时抛出。

**样例**：

```python
>>> from openjiuwen.core.foundation.store import BaseKVStore
>>> 
>>> # 获取键值
>>> name = await kv_store.get("user:123:name")
>>> if name:
>>>     print(f"用户名: {name}")
>>> else:
>>>     print("用户不存在")
```


### abstractmethod async exists

```
async def exists(self, key: str) -> bool
```

判断数据库中某个键是否存在。

**参数**：

* **key**(str)：键名。

**返回**：

* **bool**：如果存在则返回 `True`，否则返回 `False`。

**异常**：

* **JiuWenBaseException**：当存储操作失败时抛出。

**样例**：

```python
>>> from openjiuwen.core.foundation.store import BaseKVStore
>>> 
>>> # 检查键是否存在
>>> if await kv_store.exists("user:123"):
>>>     print("用户存在")
>>> else:
>>>     print("用户不存在")
```


### abstractmethod async delete

```
async def delete(self, key: str) -> bool
```

删除数据库中指定键。

**参数**：

* **key**(str)：键名。

**返回**：

* **bool**：如果值存在且删除成功返回 `True`，否则返回 `False`。

**异常**：

* **JiuWenBaseException**：当存储操作失败时抛出。

**样例**：

```python
>>> from openjiuwen.core.foundation.store import BaseKVStore
>>> 
>>> # 删除键值对
>>> success = await kv_store.delete("user:123")
>>> if success:
>>>     print("删除成功")
>>> else:
>>>     print("用户不存在或删除失败")
```


### abstractmethod async get_by_prefix

```
async def get_by_prefix(self, prefix: str) -> dict[str, str]
```

按照指定的前缀字符串从数据库获取匹配的若干键值对。

**参数**：

* **prefix**(str)：前缀匹配键名。

**返回**：

* **dict[str, str]**：返回前缀匹配的若干键值对。

**异常**：

* **JiuWenBaseException**：当存储操作失败时抛出。

**样例**：

```python
>>> from openjiuwen.core.foundation.store import BaseKVStore
>>> 
>>> # 获取所有以"user:123:"开头的键值对
>>> user_data = await kv_store.get_by_prefix("user:123:")
>>> for key, value in user_data.items():
>>>     print(f"{key}: {value}")
```


### abstractmethod async delete_by_prefix

```
async def delete_by_prefix(self, prefix: str) -> None
```

按照指定的前缀字符串删除数据库中匹配的键值对。

**参数**：

* **prefix**(str)：前缀匹配键名。

**异常**：

* **JiuWenBaseException**：当存储操作失败时抛出。

**样例**：

```python
>>> from openjiuwen.core.foundation.store import BaseKVStore
>>> 
>>> # 删除所有以"temp:"开头的键值对
>>> await kv_store.delete_by_prefix("temp:")
>>> print("临时数据清理完成")
```


### abstractmethod async mget

```
async def mget(self, keys: List[str]) -> List[str | None]
```

批量获取键值对，如果某个键不存在，返回的列表中，对应位置的值为 `None`。

**参数**：

* **keys**(List[str])：需要获取的键名列表。

**返回**：

* **List[str | None]**：返回键名对应的值列表，如果某个键不存在，则列表对应位置的值为 `None`。

**异常**：

* **JiuWenBaseException**：当存储操作失败时抛出。

**样例**：

```python
>>> from openjiuwen.core.foundation.store import BaseKVStore
>>> 
>>> # 批量获取多个键的值
>>> keys = ["user:123:name", "user:123:age", "user:123:email"]
>>> values = await kv_store.mget(keys)
>>> 
>>> for key, value in zip(keys, values):
>>>     if value is not None:
>>>         print(f"{key}: {value}")
>>>     else:
>>>         print(f"{key}: 不存在")
```


## class openjiuwen.core.retrieval.VectorStore

```
class openjiuwen.core.retrieval.VectorStore
```

向量存储抽象基类，定义了向量存储和检索的基本接口。具体的抽象接口定义请参考 `retrieval/vector_store/base.md`。

**样例**：

```python
>>> from openjiuwen.core.retrieval.vector_store.base import VectorStore
>>> from openjiuwen.core.retrieval.common.retrieval_result import SearchResult
>>> 
>>> # 向量存储实现示例
>>> class MyVectorStore(VectorStore):
>>>     async def add(self, data: dict | List[dict], batch_size: int | None = 128, **kwargs: Any):
>>>         # 实现向量添加逻辑
>>>         pass
>>>     
>>>     async def search(self, query_vector: List[float], top_k: int = 5,
>>>                   filters: Optional[dict] = None, **kwargs: Any) -> List[SearchResult]:
>>>         # 实现向量搜索逻辑
>>>         pass
>>>     
>>>     # 其他方法实现...
>>> 
>>> # 使用向量存储
>>> vector_store = MyVectorStore()
>>> await vector_store.add([{"id": "doc1", "embedding": [0.1, 0.2, 0.3]}])
>>> results = await vector_store.search([0.1, 0.2, 0.3], top_k=5)
>>> for result in results:
>>>     print(f"ID: {result.id}, 分数: {result.score}")
```


## class openjiuwen.core.foundation.store.BaseDbStore

```
class openjiuwen.core.foundation.store.BaseDbStore
```

数据库存储抽象基类，定义了数据库操作的基本接口。

### abstractmethod async get_async_engine

```
def get_async_engine(self) -> AsyncEngine
```

获取异步 DB Engine 对象。

**返回**：

* **AsyncEngine**：异步 DB Engine 对象。

**样例**：

```python
>>> from sqlalchemy.ext.asyncio import AsyncEngine
>>> from openjiuwen.core.foundation.store import BaseDbStore
>>> 
>>> # 数据库存储实现示例
>>> class MyDbStore(BaseDbStore):
>>>     def __init__(self, async_conn: AsyncEngine):
>>>         self.async_conn = async_conn
>>> 
>>>     def get_async_engine(self) -> AsyncEngine:
>>>         return self.async_conn
>>>     
>>>     # 其他方法实现...
>>> 
>>> # 使用数据库存储
>>> db_store = MyDbStore(async_engine)
>>> engine = db_store.get_async_engine()
>>> # 使用 engine 进行数据库操作...
```


## class openjiuwen.core.memory.store.impl.memory_chroma_vector_store.MemoryChromaVectorStore

```
class openjiuwen.core.memory.store.impl.memory_chroma_vector_store.MemoryChromaVectorStore
```

Chroma 向量存储实现类，继承自 `VectorStore` 抽象类，用于管理和操作 Chroma 向量数据库中的向量数据。

```
MemoryChromaVectorStore(persist_directory: str)
```

初始化 Chroma 向量存储客户端。

**参数**：

* **persist_directory**(str)：Chroma 数据库的持久化存储目录路径。

**样例**：

```python
>>> from openjiuwen.core.memory.store.impl.memory_chroma_vector_store import MemoryChromaVectorStore
>>> 
>>> # 创建 Chroma 向量存储实例
>>> vector_store = MemoryChromaVectorStore(persist_directory="./chroma_db")
```


### create_client

```
def create_client(self, database_name: str, path_or_uri: str, token: str = "", **kwargs) -> Any
```

创建客户端连接（当前未实现）。

**参数**：

* **database_name**(str)：数据库名称。
* **path_or_uri**(str)：数据库路径或 URI。
* **token**(str, 可选)：认证令牌。默认值：`""`。
* **kwargs**(Any)：额外参数。

**返回**：

* **Any**：客户端对象（当前返回 `None`）。

**样例**：

```python
>>> from openjiuwen.core.memory.store.impl.memory_chroma_vector_store import MemoryChromaVectorStore
>>> 
>>> # 创建客户端连接（当前未实现）
>>> vector_store = MemoryChromaVectorStore(persist_directory="./chroma_db")
>>> client = vector_store.create_client(
>>>     database_name="my_db",
>>>     path_or_uri="./chroma_db",
>>>     token="my_token"
>>> )
>>> print(f"客户端: {client}")
```


### async get_collection

```
async def get_collection(self, table_name: str)
```

获取或创建指定名称的 Chroma 集合。

**参数**：

* **table_name**(str)：集合名称。

**返回**：

* **Collection**：Chroma 集合对象。

**样例**：

```python
>>> from openjiuwen.core.memory.store.impl.memory_chroma_vector_store import MemoryChromaVectorStore
>>> 
>>> # 获取或创建集合
>>> vector_store = MemoryChromaVectorStore(persist_directory="./chroma_db")
>>> collection = await vector_store.get_collection("documents")
>>> print(f"集合名称: {collection.name}")
>>> print(f"文档数量: {collection.count()}")
```


### remove_collection_from_cache

```
def remove_collection_from_cache(self, table_name: str)
```

从缓存中移除指定的集合。

**参数**：

* **table_name**(str)：集合名称。

**样例**：

```python
>>> from openjiuwen.core.memory.store.impl.memory_chroma_vector_store import MemoryChromaVectorStore
>>> 
>>> # 从缓存中移除集合
>>> vector_store = MemoryChromaVectorStore(persist_directory="./chroma_db")
>>> vector_store.remove_collection_from_cache("documents")
>>> print("集合已从缓存中移除")
```


### check_table_name

```
def check_table_name(self, table_name: Optional[str] = None, operation: Optional[str] = None)
```

检查集合名称是否有效。

**参数**：

* **table_name**(Optional[str], 可选)：集合名称。默认值：`None`。
* **operation**(Optional[str], 可选)：当前操作名称，用于错误信息。默认值：`None`。

**异常**：

* **JiuWenBaseException**：如果集合名称为空或 `None`，抛出 `VALIDATION_INVALID` 异常。

**样例**：

```python
>>> from openjiuwen.core.memory.store.impl.memory_chroma_vector_store import MemoryChromaVectorStore
>>> 
>>> # 检查集合名称
>>> vector_store = MemoryChromaVectorStore(persist_directory="./chroma_db")
>>> 
>>> try:
>>>     vector_store.check_table_name("", "add")
>>> except Exception as e:
>>>     print(f"错误: {e}")
>>> 
>>> try:
>>>     vector_store.check_table_name("documents", "add")
>>>     print("集合名称有效")
>>> except Exception as e:
>>>     print(f"错误: {e}")
```


### async table_exists

```
async def table_exists(self, table_name: str) -> bool
```

检查指定名称的集合是否存在。

**参数**：

* **table_name**(str)：集合名称。

**返回**：

* **bool**：如果集合存在返回 `True`，否则返回 `False`。

**样例**：

```python
>>> from openjiuwen.core.memory.store.impl.memory_chroma_vector_store import MemoryChromaVectorStore
>>> 
>>> # 检查集合是否存在
>>> vector_store = MemoryChromaVectorStore(persist_directory="./chroma_db")
>>> 
>>> if await vector_store.table_exists("documents"):
>>>     print("documents 集合存在")
>>> else:
>>>     print("documents 集合不存在")
```


### async add

```
async def add(self, data: dict | List[dict], batch_size: int | None = 128, **kwargs: Any) -> None
```

向向量存储中添加向量数据。

**参数**：

* **data**(dict | List[dict])：要添加的向量数据，每个字典包含 "id" 和 "embedding" 字段。
* **batch_size**(int | None, 可选)：批量处理大小。默认值：128。
* **kwargs**(Any)：额外参数，必须包含 "table_name" 指定集合名称。

**异常**：

* **JiuWenBaseException**：当添加操作失败时抛出。

**样例**：

```python
>>> from openjiuwen.core.memory.store.impl.memory_chroma_vector_store import MemoryChromaVectorStore
>>> 
>>> # 添加向量数据
>>> vector_store = MemoryChromaVectorStore(persist_directory="./chroma_db")
>>> 
>>> # 单个向量数据
>>> await vector_store.add(
>>>     data={"id": "doc1", "embedding": [0.1, 0.2, 0.3]},
>>>     table_name="documents"
>>> )
>>> 
>>> # 批量向量数据
>>> await vector_store.add(
>>>     data=[
>>>         {"id": "doc2", "embedding": [0.4, 0.5, 0.6]},
>>>         {"id": "doc3", "embedding": [0.7, 0.8, 0.9]}
>>>     ],
>>>     table_name="documents",
>>>     batch_size=64
>>> )
>>> print("向量数据添加完成")
```


### async search

```
async def search(self, query_vector: List[float], top_k: int = 5, filters: Optional[dict] = None, **kwargs: Any) -> List[SearchResult]
```

根据查询向量搜索相似向量。

**参数**：

* **query_vector**(List[float])：查询向量。
* **top_k**(int, 可选)：返回结果的数量。默认值：5。
* **filters**(Optional[dict], 可选)：过滤条件（当前未使用）。默认值：`None`。
* **kwargs**(Any)：额外参数，必须包含 "table_name" 指定集合名称。

**返回**：

* **List[SearchResult]**：搜索结果列表，每个结果包含 id、text 和 score 字段。

**异常**：

* **JiuWenBaseException**：当搜索操作失败时抛出。

**样例**：

```python
>>> from openjiuwen.core.memory.store.impl.memory_chroma_vector_store import MemoryChromaVectorStore
>>> 
>>> # 搜索相似向量
>>> vector_store = MemoryChromaVectorStore(persist_directory="./chroma_db")
>>> 
>>> query_vector = [0.1, 0.2, 0.3]
>>> results = await vector_store.search(
>>>     query_vector=query_vector,
>>>     top_k=3,
>>>     table_name="documents"
>>> )
>>> 
>>> for result in results:
>>>     print(f"ID: {result.id}, 分数: {result.score}, 文本: {result.text}")
```


### async delete

```
async def delete(self, ids: Optional[List[str]] = None, filter_expr: Optional[str] = None, **kwargs: Any) -> bool
```

删除向量数据。

**参数**：

* **ids**(Optional[List[str]], 可选)：要删除的向量 ID 列表。默认值：`None`。
* **filter_expr**(Optional[str], 可选)：过滤表达式。默认值：`None`。
* **kwargs**(Any)：额外参数，必须包含 "table_name" 指定集合名称。

**返回**：

* **bool**：删除成功返回 `True`，否则返回 `False`。

**异常**：

* **JiuWenBaseException**：当删除操作失败时抛出。

**样例**：

```python
>>> from openjiuwen.core.memory.store.impl.memory_chroma_vector_store import MemoryChromaVectorStore
>>> 
>>> # 删除向量数据
>>> vector_store = MemoryChromaVectorStore(persist_directory="./chroma_db")
>>> 
>>> # 按 ID 删除
>>> success = await vector_store.delete(
>>>     ids=["doc1", "doc2"],
>>>     table_name="documents"
>>> )
>>> print(f"按 ID 删除结果: {success}")
>>> 
>>> # 按过滤表达式删除
>>> success = await vector_store.delete(
>>>     filter_expr="metadata contains 'category'",
>>>     table_name="documents"
>>> )
>>> print(f"按过滤表达式删除结果: {success}")
```


### async delete_table

```
async def delete_table(self, table_name: str) -> bool
```

删除整个集合。

**参数**：

* **table_name**(str)：集合名称。

**返回**：

* **bool**：删除成功返回 `True`，否则返回 `False`。

**异常**：

* **JiuWenBaseException**：当删除操作失败时抛出。

**样例**：

```python
>>> from openjiuwen.core.memory.store.impl.memory_chroma_vector_store import MemoryChromaVectorStore
>>> 
>>> # 删除集合
>>> vector_store = MemoryChromaVectorStore(persist_directory="./chroma_db")
>>> 
>>> success = await vector_store.delete_table("documents")
>>> if success:
>>>     print("documents 集合删除成功")
>>> else:
>>>     print("documents 集合删除失败")
```


### check_vector_field

```
def check_vector_field(self) -> None
```

检查向量字段配置是否与实际数据库一致。

**样例**：

```python
>>> from openjiuwen.core.memory.store.impl.memory_chroma_vector_store import MemoryChromaVectorStore
>>> 
>>> # 检查向量字段配置
>>> vector_store = MemoryChromaVectorStore(persist_directory="./chroma_db")
>>> vector_store.check_vector_field()
>>> print("向量字段配置检查完成")
```


## class openjiuwen.core.memory.store.impl.memory_milvus_vector_store.MemoryMilvusVectorStore

```
class openjiuwen.core.memory.store.impl.memory_milvus_vector_store.MemoryMilvusVectorStore
```

Milvus 向量存储实现类，继承自 `VectorStore` 抽象类，用于管理和操作 Milvus 向量数据库中的向量数据。

```
MemoryMilvusVectorStore(milvus_host: str, milvus_port: str, token: str | None, embedding_dims: int)
```

初始化 Milvus 向量存储客户端。

**参数**：

* **milvus_host**(str)：Milvus 服务器主机地址。
* **milvus_port**(str)：Milvus 服务器端口。
* **token**(str | None)：认证令牌。
* **embedding_dims**(int)：向量维度。

**样例**：

```python
>>> from openjiuwen.core.memory.store.impl.memory_milvus_vector_store import MemoryMilvusVectorStore
>>> 
>>> # 创建 Milvus 向量存储实例
>>> vector_store = MemoryMilvusVectorStore(
>>>     milvus_host="localhost",
>>>     milvus_port="19530",
>>>     token="your_token_here",
>>>     embedding_dims=768
>>> )
```


### async add

```
async def add(self, data: dict | List[dict], batch_size: int | None = 128, **kwargs: Any) -> None
```

向向量存储中添加向量数据。

**参数**：

* **data**(dict | List[dict])：要添加的向量数据，每个字典包含 "id" 和 "embedding" 字段。
* **batch_size**(int | None, 可选)：批量处理大小。默认值：128。
* **kwargs**(Any)：额外参数，必须包含 "table_name" 指定集合名称。

**异常**：

* **JiuWenBaseException**：当添加操作失败时抛出（`MEMORY_STORE_VALIDATION_INVALID`）。

**样例**：

```python
>>> from openjiuwen.core.memory.store.impl.memory_milvus_vector_store import MemoryMilvusVectorStore
>>> 
>>> # 添加向量数据
>>> vector_store = MemoryMilvusVectorStore(
>>>     milvus_host="localhost",
>>>     milvus_port="19530",
>>>     token="your_token_here",
>>>     embedding_dims=768
>>> )
>>> 
>>> # 单个向量数据
>>> await vector_store.add(
>>>     data={"id": "doc1", "embedding": [0.1, 0.2, 0.3] + [0.0]*765},
>>>     table_name="documents"
>>> )
>>> 
>>> # 批量向量数据
>>> await vector_store.add(
>>>     data=[
>>>         {"id": "doc2", "embedding": [0.4, 0.5, 0.6] + [0.0]*765},
>>>         {"id": "doc3", "embedding": [0.7, 0.8, 0.9] + [0.0]*765}
>>>     ],
>>>     table_name="documents",
>>>     batch_size=64
>>> )
>>> print("向量数据添加完成")
```


### async search

```
async def search(self, query_vector: List[float], top_k: int = 5, filters: Optional[dict] = None, **kwargs: Any) -> List[SearchResult]
```

根据查询向量搜索相似向量。

**参数**：

* **query_vector**(List[float])：查询向量。
* **top_k**(int, 可选)：返回结果的数量。默认值：5。
* **filters**(Optional[dict], 可选)：过滤条件（当前未使用）。默认值：`None`。
* **kwargs**(Any)：额外参数，必须包含 "table_name" 指定集合名称。

**返回**：

* **List[SearchResult]**：搜索结果列表，每个结果包含 id、text 和 score 字段。

**异常**：

* **JiuWenBaseException**：当搜索操作失败时抛出（`MEMORY_STORE_VALIDATION_INVALID`）。

**样例**：

```python
>>> from openjiuwen.core.memory.store.impl.memory_milvus_vector_store import MemoryMilvusVectorStore
>>> 
>>> # 搜索相似向量
>>> vector_store = MemoryMilvusVectorStore(
>>>     milvus_host="localhost",
>>>     milvus_port="19530",
>>>     token="your_token_here",
>>>     embedding_dims=768
>>> )
>>> 
>>> query_vector = [0.1, 0.2, 0.3] + [0.0]*765
>>> results = await vector_store.search(
>>>     query_vector=query_vector,
>>>     top_k=3,
>>>     table_name="documents"
>>> )
>>> 
>>> for result in results:
>>>     print(f"ID: {result.id}, 分数: {result.score}, 文本: {result.text}")
```


### async delete

```
async def delete(self, ids: Optional[List[str]] = None, filter_expr: Optional[str] = None, **kwargs: Any) -> bool
```

删除向量数据。

**参数**：

* **ids**(Optional[List[str]], 可选)：要删除的向量 ID 列表。默认值：`None`。
* **filter_expr**(Optional[str], 可选)：过滤表达式。默认值：`None`。
* **kwargs**(Any)：额外参数，必须包含 "table_name" 指定集合名称。

**返回**：

* **bool**：删除成功返回 `True`，否则返回 `False`。

**异常**：

* **JiuWenBaseException**：当删除操作失败时抛出（`MEMORY_STORE_VALIDATION_INVALID`）。

**样例**：

```python
>>> from openjiuwen.core.memory.store.impl.memory_milvus_vector_store import MemoryMilvusVectorStore
>>> 
>>> # 删除向量数据
>>> vector_store = MemoryMilvusVectorStore(
>>>     milvus_host="localhost",
>>>     milvus_port="19530",
>>>     token="your_token_here",
>>>     embedding_dims=768
>>> )
>>> 
>>> # 按 ID 删除
>>> success = await vector_store.delete(
>>>     ids=["doc1", "doc2"],
>>>     table_name="documents"
>>> )
>>> print(f"按 ID 删除结果: {success}")
```


### async delete_table

```
async def delete_table(self, table_name: str) -> bool
```

删除整个集合。

**参数**：

* **table_name**(str)：集合名称。

**返回**：

* **bool**：删除成功返回 `True`，否则返回 `False`。

**样例**：

```python
>>> from openjiuwen.core.memory.store.impl.memory_milvus_vector_store import MemoryMilvusVectorStore
>>> 
>>> # 删除集合
>>> vector_store = MemoryMilvusVectorStore(
>>>     milvus_host="localhost",
>>>     milvus_port="19530",
>>>     token="your_token_here",
>>>     embedding_dims=768
>>> )
>>> 
>>> success = await vector_store.delete_table("documents")
>>> if success:
>>>     print("documents 集合删除成功")
>>> else:
>>>     print("documents 集合删除失败")
```


### async table_exists

```
async def table_exists(self, table_name: str) -> bool
```

检查指定名称的集合是否存在。

**参数**：

* **table_name**(str)：集合名称。

**返回**：

* **bool**：如果集合存在返回 `True`，否则返回 `False`。

**样例**：

```python
>>> from openjiuwen.core.memory.store.impl.memory_milvus_vector_store import MemoryMilvusVectorStore
>>> 
>>> # 检查集合是否存在
>>> vector_store = MemoryMilvusVectorStore(
>>>     milvus_host="localhost",
>>>     milvus_port="19530",
>>>     token="your_token_here",
>>>     embedding_dims=768
>>> )
>>> 
>>> if await vector_store.table_exists("documents"):
>>>     print("documents 集合存在")
>>> else:
>>>     print("documents 集合不存在")
```


### check_vector_field

```
def check_vector_field(self) -> None
```

检查向量字段配置是否与实际数据库一致。

**样例**：

```python
>>> from openjiuwen.core.memory.store.impl.memory_milvus_vector_store import MemoryMilvusVectorStore
>>> 
>>> # 检查向量字段配置
>>> vector_store = MemoryMilvusVectorStore(
>>>     milvus_host="localhost",
>>>     milvus_port="19530",
>>>     token="your_token_here",
>>>     embedding_dims=768
>>> )
>>> vector_store.check_vector_field()
>>> print("向量字段配置检查完成")
```