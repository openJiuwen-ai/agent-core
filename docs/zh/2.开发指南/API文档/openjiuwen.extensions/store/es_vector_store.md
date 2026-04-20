# openjiuwen.extensions.store.vector.es_vector_store

## class openjiuwen.extensions.store.vector.es_vector_store.ElasticsearchVectorStore

基于 Elasticsearch 的向量存储实现，使用 Elasticsearch 的 `dense_vector` 字段类型和 k-NN 搜索提供向量相似度搜索功能。

每个集合（Collection）映射到一个 Elasticsearch 索引，索引的映射从提供的 `CollectionSchema` 派生。

### 实现细节

- 使用 `AsyncElasticsearch` 进行所有操作
- 向量字段存储为 `dense_vector` 类型，并设置 `index: true` 以启用原生 k-NN 搜索（ES 8.x+）
- 标量字段映射到最接近的 ES 类型
- 集合元数据（schema、距离度量、schema 版本）存储在索引内的专用 `_meta` 文档中

> **参考示例**：更多使用示例请参考项目中的示例代码：
> - `examples/es_vector_store_example.py` - ES Vector Store 完整使用示例

---

## __init__

```python
__init__(es: AsyncElasticsearch, index_prefix: str = "agent_vector")
```

初始化 Elasticsearch 向量存储。

**参数**：

* **es**(AsyncElasticsearch)：异步 Elasticsearch 客户端实例。
* **index_prefix**(str，可选)：索引前缀。默认值："agent_vector"。

---

## async create_collection

```python
async create_collection(collection_name: str, schema: Union[CollectionSchema, Dict[str, Any]], **kwargs: Any) -> None
```

创建一个新的集合（在 Elasticsearch 中创建索引）。

**参数**：

* **collection_name**(str)：集合名称。
* **schema**(CollectionSchema | Dict[str, Any])：集合 schema，可以是 CollectionSchema 对象或字典。
* **kwargs**(Any)：可变参数，支持的额外配置：
    * **distance_metric**(str，可选)：距离度量类型，支持 "COSINE"、"L2"、"IP"。默认值："COSINE"。

**异常**：

* **BaseError**：当 schema 中缺少 FLOAT_VECTOR 字段时抛出。

**示例**：

```python
from openjiuwen.core.foundation.store.base_vector_store import CollectionSchema, FieldSchema, VectorDataType
from openjiuwen.extensions.store.vector.es_vector_store import ElasticsearchVectorStore

# 创建 schema
schema = CollectionSchema(description="测试集合")
schema.add_field(FieldSchema(name="id", dtype=VectorDataType.VARCHAR, is_primary=True))
schema.add_field(FieldSchema(name="embedding", dtype=VectorDataType.FLOAT_VECTOR, dim=768))
schema.add_field(FieldSchema(name="content", dtype=VectorDataType.VARCHAR))

# 创建集合
await store.create_collection("my_collection", schema, distance_metric="COSINE")
```

---

## async delete_collection

```python
async delete_collection(collection_name: str, **kwargs: Any) -> None
```

删除集合（删除 Elasticsearch 索引）。

**参数**：

* **collection_name**(str)：集合名称。
* **kwargs**(Any)：可变参数，预留扩展使用。

**示例**：

```python
await store.delete_collection("my_collection")
```

---

## async collection_exists

```python
async collection_exists(collection_name: str, **kwargs: Any) -> bool
```

检查集合是否存在。

**参数**：

* **collection_name**(str)：集合名称。
* **kwargs**(Any)：可变参数，预留扩展使用。

**返回**：

**bool**，如果集合存在则返回 True，否则返回 False。

**示例**：

```python
exists = await store.collection_exists("my_collection")
if exists:
    print("集合存在")
else:
    print("集合不存在")
```

---

## async get_schema

```python
async get_schema(collection_name: str, **kwargs: Any) -> CollectionSchema
```

获取集合的 schema。

**参数**：

* **collection_name**(str)：集合名称。
* **kwargs**(Any)：可变参数，支持的额外配置：
    * **primary_key_field**(str，可选)：主键字段名。默认值："id"。

**返回**：

**CollectionSchema**，集合的 schema 对象。

**异常**：

* **BaseError**：当集合不存在时抛出。

**示例**：

```python
schema = await store.get_schema("my_collection")
print(f"集合有 {len(schema.fields)} 个字段")
for field in schema.fields:
    print(f"- {field.name}: {field.dtype.value}")
```

---

## async add_docs

```python
async add_docs(collection_name: str, docs: List[Dict[str, Any]], **kwargs: Any) -> None
```

批量添加文档到集合。

**参数**：

* **collection_name**(str)：集合名称。
* **docs**(List[Dict[str, Any]])：文档列表，每个文档是一个字典。
* **kwargs**(Any)：可变参数，支持的额外配置：
    * **batch_size**(int，可选)：批处理大小。默认值：500。

**示例**：

```python
documents = [
    {
        "id": "doc1",
        "embedding": [0.1, 0.2, 0.3],
        "content": "这是第一个文档",
    },
    {
        "id": "doc2",
        "embedding": [0.4, 0.5, 0.6],
        "content": "这是第二个文档",
    },
]

await store.add_docs("my_collection", documents, batch_size=100)
```

---

## async search

```python
async search(collection_name: str, query_vector: List[float], vector_field: str, top_k: int = 5, filters: Optional[Dict[str, Any]] = None, **kwargs: Any) -> List[VectorSearchResult]
```

向量相似度搜索。

**参数**：

* **collection_name**(str)：集合名称。
* **query_vector**(List[float])：查询向量。
* **vector_field**(str)：向量字段名。
* **top_k**(int，可选)：返回的相似结果数量。默认值：5。
* **filters**(Dict[str, Any]，可选)：元数据过滤条件。默认值：None。
* **kwargs**(Any)：可变参数，支持的额外配置：
    * **metric_type**(str，可选)：距离度量类型。默认值：使用集合创建时指定的度量。
    * **num_candidates**(int，可选）：k-NN 候选数量。默认值：max(top_k * 10, 100)。
    * **output_fields**(List[str]，可选）：输出的字段列表。

**返回**：

**List[VectorSearchResult]**，搜索结果列表，每个结果包含分数和字段信息。

**示例**：

```python
# 基本搜索
query = [0.1, 0.2, 0.3]
results = await store.search("my_collection", query, "embedding", top_k=5)

# 带过滤器的搜索
results = await store.search(
    "my_collection",
    query,
    "embedding",
    top_k=10,
    filters={"category": "tech"}
)

# 带列表过滤的搜索
results = await store.search(
    "my_collection",
    query,
    "embedding",
    top_k=10,
    filters={"category": ["tech", "science"]}
)

for result in results:
    print(f"ID: {result.fields['id']}, Score: {result.score:.4f}")
    print(f"Content: {result.fields['content']}")
```

---

## async delete_docs_by_ids

```python
async delete_docs_by_ids(collection_name: str, ids: List[str], **kwargs: Any) -> None
```

根据文档 ID 删除文档。

**参数**：

* **collection_name**(str)：集合名称。
* **ids**(List[str])：要删除的文档 ID 列表。
* **kwargs**(Any)：可变参数，支持的额外配置：
    * **batch_size**(int，可选)：批处理大小。默认值：500。

**示例**：

```python
await store.delete_docs_by_ids("my_collection", ["doc1", "doc2"])
```

---

## async delete_docs_by_filters

```python
async delete_docs_by_filters(collection_name: str, filters: Dict[str, Any], **kwargs: Any) -> None
```

根据过滤条件删除文档。

**参数**：

* **collection_name**(str)：集合名称。
* **filters**(Dict[str, Any])：过滤条件。
* **kwargs**(Any)：可变参数，预留扩展使用。

**示例**：

```python
# 删除 category 为 "tech" 的文档
await store.delete_docs_by_filters("my_collection", {"category": "tech"})

# 删除 category 在 ["tech", "science"] 中的文档
await store.delete_docs_by_filters("my_collection", {"category": ["tech", "science"]})
```

---

## async list_collection_names

```python
async list_collection_names() -> List[str]
```

列出所有集合名称。

**返回**：

**List[str]**，集合名称列表。

**示例**：

```python
collections = await store.list_collection_names()
for coll in collections:
    print(f"集合: {coll}")
```

---

## async get_collection_metadata

```python
async get_collection_metadata(collection_name: str) -> Dict[str, Any]
```

获取集合的元数据。

**参数**：

* **collection_name**(str)：集合名称。

**返回**：

**Dict[str, Any]**，元数据字典，包含以下键：
* **schema**(Dict)：集合 schema
* **distance_metric**(str)：距离度量类型
* **vector_field**(str)：向量字段名
* **vector_dim**(int)：向量维度
* **schema_version**(int)：schema 版本号
* **collection_name**(str)：集合名称

**示例**：

```python
metadata = await store.get_collection_metadata("my_collection")
print(f"距离度量: {metadata['distance_metric']}")
print(f"向量维度: {metadata['vector_dim']}")
print(f"Schema 版本: {metadata['schema_version']}")
```

---

## async update_collection_metadata

```python
async update_collection_metadata(collection_name: str, metadata: Dict[str, Any]) -> None
```

更新集合的元数据。

**参数**：

* **collection_name**(str)：集合名称。
* **metadata**(Dict[str, Any])：要更新的元数据。

**异常**：

* **BaseError**：当 schema_version 为负数或非整数时抛出。

**示例**：

```python
await store.update_collection_metadata("my_collection", {"schema_version": 1})
```

---

## async update_schema

```python
async update_schema(collection_name: str, operations: List[BaseOperation]) -> None
```

更新集合的 schema。

**参数**：

* **collection_name**(str)：集合名称。
* **operations**(List[BaseOperation])：schema 操作列表。

**说明**：

此方法会创建一个新的临时集合，迁移数据后替换原集合。操作包括添加字段、删除字段等。

**示例**：

```python
from openjiuwen.core.memory.migration.operation.add_field_operation import AddFieldOperation

# 添加新字段
operation = AddFieldOperation(
    field_name="new_field",
    field_type="VARCHAR",
    default_value=""
)

await store.update_schema("my_collection", [operation])
```

---

## 支持的数据类型映射

ElasticsearchVectorStore 支持以下数据类型到 Elasticsearch 类型的映射：

| VectorDataType | Elasticsearch 类型 | 说明 |
|---------------|-------------------|------|
| FLOAT_VECTOR | dense_vector | 向量字段，支持 k-NN 搜索 |
| VARCHAR | keyword | 字符串字段 |
| INT64 | long | 64位整数 |
| INT32 | integer | 32位整数 |
| INT16 | integer | 16位整数 |
| INT8 | integer | 8位整数 |
| FLOAT | float | 单精度浮点数 |
| DOUBLE | double | 双精度浮点数 |
| BOOL | boolean | 布尔值 |
| JSON | object | JSON 对象 |
| ARRAY | object | 数组（作为对象存储） |

---

## 支持的距离度量

| 度量类型 | ES similarity | 说明 |
|---------|--------------|------|
| COSINE | cosine | 余弦相似度（默认） |
| L2 | l2_norm | 欧几里得距离 |
| IP | dot_product | 内积 |

---

## 完整示例

```python
import asyncio
from elasticsearch import AsyncElasticsearch
from openjiuwen.core.foundation.store.base_vector_store import (
    CollectionSchema,
    FieldSchema,
    VectorDataType,
)
from openjiuwen.extensions.store.vector.es_vector_store import ElasticsearchVectorStore


async def main():
    # 连接 Elasticsearch
    es = AsyncElasticsearch("http://localhost:9200", verify_certs=False)
    store = ElasticsearchVectorStore(es=es, index_prefix="my_app")

    # 创建集合
    schema = CollectionSchema(description="文档向量存储")
    schema.add_field(FieldSchema(name="id", dtype=VectorDataType.VARCHAR, is_primary=True))
    schema.add_field(FieldSchema(name="embedding", dtype=VectorDataType.FLOAT_VECTOR, dim=768))
    schema.add_field(FieldSchema(name="title", dtype=VectorDataType.VARCHAR))
    schema.add_field(FieldSchema(name="content", dtype=VectorDataType.VARCHAR))
    schema.add_field(FieldSchema(name="category", dtype=VectorDataType.VARCHAR))

    await store.create_collection("documents", schema, distance_metric="COSINE")

    # 添加文档
    docs = [
        {
            "id": "1",
            "embedding": [0.1] * 768,
            "title": "Python教程",
            "content": "Python是一种编程语言",
            "category": "编程",
        },
        {
            "id": "2",
            "embedding": [0.2] * 768,
            "title": "机器学习基础",
            "content": "机器学习是AI的一个分支",
            "category": "AI",
        },
    ]

    await store.add_docs("documents", docs)

    # 搜索
    query = [0.15] * 768
    results = await store.search("documents", query, "embedding", top_k=5)

    for result in results:
        print(f"Score: {result.score:.4f}")
        print(f"Title: {result.fields['title']}")

    # 清理
    await store.delete_collection("documents")
    await es.close()


asyncio.run(main())
```

---

## 注意事项

1. **Elasticsearch 版本**：需要 Elasticsearch 8.x 或更高版本以支持原生 k-NN 搜索。
2. **索引命名**：索引名称格式为 `{index_prefix}__{collection_name}`。
3. **元数据存储**：集合元数据存储在索引内 ID 为 `__collection_metadata__` 的特殊文档中。
4. **异步操作**：所有方法都是异步的，需要使用 `await` 调用。
5. **批量操作**：添加和删除文档支持批量处理，可以通过 `batch_size` 参数调整。
6. **向量维度**：创建集合时必须指定向量字段的维度。
7. **主键字段**：建议始终指定主键字段以便于文档管理。
8. **索引刷新**：添加文档后会自动刷新索引以确保数据可搜索。

---

## 相关文档

- [BaseVectorStore](../../foundation/store/base_vector_store.md) - 向量存储基类
- [CollectionSchema](../../foundation/store/base_vector_store.md) - 集合 Schema
- [VectorSearchResult](../../foundation/store/base_vector_store.md) - 搜索结果
- [ES Vector Store 示例](../../../../examples/es_vector_store_example.py) - 完整使用示例
