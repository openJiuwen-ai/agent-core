# openjiuwen.core.foundation.store

`openjiuwen.core.foundation.store` is the unified **storage abstraction layer module** in openJiuwen, responsible for:

- Defining `BaseKVStore` key-value store abstract base class;
- Defining `VectorStore` vector store abstract base class;
- Defining `BaseDbStore` database store abstract base class;
- Providing various storage implementations (such as Redis, Chroma, Milvus, etc.).


## class openjiuwen.core.foundation.store.BaseKVStore

```
class openjiuwen.core.foundation.store.BaseKVStore
```

Abstract base class for key-value storage, defining the basic interface for key-value pair storage.

### abstractmethod async set

```
async def set(self, key: str, value: str) -> None
```

Set a key-value pair.

**Parameters**:

* **key** (str): The key name to set.
* **value** (str): The value to set.

**Exceptions**:

* **JiuWenBaseException**: Raised when storage operation fails.

**Example**:

```python
>>> from openjiuwen.core.foundation.store import BaseKVStore
>>> 
>>> # Set key-value pair
>>> await kv_store.set("user:123:name", "John")
>>> await kv_store.set("user:123:age", "25")
```


### abstractmethod async exclusive_set

```
async def exclusive_set(self, key: str, value: str, expiry: int | None = None) -> bool
```

Atomically set a key-value pair, only if the key does not exist.

**Parameters**:

* **key** (str): The key name to set.
* **value** (str): The value to set.
* **expiry** (int | None, optional): Expiration time for the key-value pair (in seconds), after which the value can be overwritten. Default: `None`.

**Returns**:

* **bool**: Returns `True` if the key-value pair was set successfully, `False` if the key already exists and has not expired.

**Exceptions**:

* **JiuWenBaseException**: Raised when storage operation fails.

**Example**:

```python
>>> from openjiuwen.core.foundation.store import BaseKVStore
>>> 
>>> # Atomically set key-value pair, only succeeds if key doesn't exist
>>> success = await kv_store.exclusive_set("lock:resource:123", "locked", expiry=60)
>>> if success:
>>>     print("Lock acquired successfully")
>>> else:
>>>     print("Lock is already held by another process")
```


### abstractmethod async get

```
async def get(self, key: str) -> str | None
```

Get the value corresponding to the specified key from the database, returns `None` if not found.

**Parameters**:

* **key** (str): The key name to retrieve.

**Returns**:

* **str | None**: Returns the value corresponding to the key, or `None` if the key does not exist.

**Exceptions**:

* **JiuWenBaseException**: Raised when storage operation fails.

**Example**:

```python
>>> from openjiuwen.core.foundation.store import BaseKVStore
>>> 
>>> # Get key value
>>> name = await kv_store.get("user:123:name")
>>> if name:
>>>     print(f"Username: {name}")
>>> else:
>>>     print("User does not exist")
```


### abstractmethod async exists

```
async def exists(self, key: str) -> bool
```

Check if a key exists in the database.

**Parameters**:

* **key** (str): The key name.

**Returns**:

* **bool**: Returns `True` if it exists, otherwise `False`.

**Exceptions**:

* **JiuWenBaseException**: Raised when storage operation fails.

**Example**:

```python
>>> from openjiuwen.core.foundation.store import BaseKVStore
>>> 
>>> # Check if key exists
>>> if await kv_store.exists("user:123"):
>>>     print("User exists")
>>> else:
>>>     print("User does not exist")
```


### abstractmethod async delete

```
async def delete(self, key: str) -> bool
```

Delete the specified key from the database.

**Parameters**:

* **key** (str): The key name.

**Returns**:

* **bool**: Returns `True` if the value existed and was deleted successfully, otherwise `False`.

**Exceptions**:

* **JiuWenBaseException**: Raised when storage operation fails.

**Example**:

```python
>>> from openjiuwen.core.foundation.store import BaseKVStore
>>> 
>>> # Delete key-value pair
>>> success = await kv_store.delete("user:123")
>>> if success:
>>>     print("Delete successful")
>>> else:
>>>     print("User does not exist or delete failed")
```


### abstractmethod async get_by_prefix

```
async def get_by_prefix(self, prefix: str) -> dict[str, str]
```

Get key-value pairs from the database that match the specified prefix string.

**Parameters**:

* **prefix** (str): The prefix to match key names.

**Returns**:

* **dict[str, str]**: Returns key-value pairs matching the prefix.

**Exceptions**:

* **JiuWenBaseException**: Raised when storage operation fails.

**Example**:

```python
>>> from openjiuwen.core.foundation.store import BaseKVStore
>>> 
>>> # Get all key-value pairs starting with "user:123:"
>>> user_data = await kv_store.get_by_prefix("user:123:")
>>> for key, value in user_data.items():
>>>     print(f"{key}: {value}")
```


### abstractmethod async delete_by_prefix

```
async def delete_by_prefix(self, prefix: str) -> None
```

Delete key-value pairs from the database that match the specified prefix string.

**Parameters**:

* **prefix** (str): The prefix to match key names.

**Exceptions**:

* **JiuWenBaseException**: Raised when storage operation fails.

**Example**:

```python
>>> from openjiuwen.core.foundation.store import BaseKVStore
>>> 
>>> # Delete all key-value pairs starting with "temp:"
>>> await kv_store.delete_by_prefix("temp:")
>>> print("Temporary data cleanup completed")
```


### abstractmethod async mget

```
async def mget(self, keys: List[str]) -> List[str | None]
```

Batch retrieve key-value pairs. If a key does not exist, the corresponding position in the returned list is `None`.

**Parameters**:

* **keys** (List[str]): List of key names to retrieve.

**Returns**:

* **List[str | None]**: Returns a list of values corresponding to the keys. If a key does not exist, the corresponding position in the list is `None`.

**Exceptions**:

* **JiuWenBaseException**: Raised when storage operation fails.

**Example**:

```python
>>> from openjiuwen.core.foundation.store import BaseKVStore
>>> 
>>> # Batch retrieve multiple key values
>>> keys = ["user:123:name", "user:123:age", "user:123:email"]
>>> values = await kv_store.mget(keys)
>>> 
>>> for key, value in zip(keys, values):
>>>     if value is not None:
>>>         print(f"{key}: {value}")
>>>     else:
>>>         print(f"{key}: does not exist")
```


## class openjiuwen.core.retrieval.VectorStore

```
class openjiuwen.core.retrieval.VectorStore
```

Abstract base class for vector storage, defining the basic interface for vector storage and retrieval. For specific abstract interface definitions, please refer to `retrieval/vector_store/base.md`.

**Example**:

```python
>>> from openjiuwen.core.retrieval.vector_store.base import VectorStore
>>> from openjiuwen.core.retrieval.common.retrieval_result import SearchResult
>>> 
>>> # Vector store implementation example
>>> class MyVectorStore(VectorStore):
>>>     async def add(self, data: dict | List[dict], batch_size: int | None = 128, **kwargs: Any):
>>>         # Implement vector addition logic
>>>         pass
>>>     
>>>     async def search(self, query_vector: List[float], top_k: int = 5,
>>>                   filters: Optional[dict] = None, **kwargs: Any) -> List[SearchResult]:
>>>         # Implement vector search logic
>>>         pass
>>>     
>>>     # Other method implementations...
>>> 
>>> # Use vector store
>>> vector_store = MyVectorStore()
>>> await vector_store.add([{"id": "doc1", "embedding": [0.1, 0.2, 0.3]}])
>>> results = await vector_store.search([0.1, 0.2, 0.3], top_k=5)
>>> for result in results:
>>>     print(f"ID: {result.id}, Score: {result.score}")
```


## class openjiuwen.core.foundation.store.BaseDbStore

```
class openjiuwen.core.foundation.store.BaseDbStore
```

Abstract base class for database storage, defining the basic interface for database operations.

### abstractmethod async get_async_engine

```
def get_async_engine(self) -> AsyncEngine
```

Get the asynchronous DB Engine object.

**Returns**:

* **AsyncEngine**: The asynchronous DB Engine object.

**Example**:

```python
>>> from sqlalchemy.ext.asyncio import AsyncEngine
>>> from openjiuwen.core.foundation.store import BaseDbStore
>>> 
>>> # Database store implementation example
>>> class MyDbStore(BaseDbStore):
>>>     def __init__(self, async_conn: AsyncEngine):
>>>         self.async_conn = async_conn
>>> 
>>>     def get_async_engine(self) -> AsyncEngine:
>>>         return self.async_conn
>>>     
>>>     # Other method implementations...
>>> 
>>> # Use database store
>>> db_store = MyDbStore(async_engine)
>>> engine = db_store.get_async_engine()
>>> # Use engine for database operations...
```


## class openjiuwen.core.memory.store.impl.memory_chroma_vector_store.MemoryChromaVectorStore

```
class openjiuwen.core.memory.store.impl.memory_chroma_vector_store.MemoryChromaVectorStore
```

Chroma vector store implementation class, inheriting from the `VectorStore` abstract class, used for managing and operating vector data in the Chroma vector database.

```
MemoryChromaVectorStore(persist_directory: str)
```

Initialize the Chroma vector store client.

**Parameters**:

* **persist_directory** (str): The persistent storage directory path for the Chroma database.

**Example**:

```python
>>> from openjiuwen.core.memory.store.impl.memory_chroma_vector_store import MemoryChromaVectorStore
>>> 
>>> # Create Chroma vector store instance
>>> vector_store = MemoryChromaVectorStore(persist_directory="./chroma_db")
```


### create_client

```
def create_client(self, database_name: str, path_or_uri: str, token: str = "", **kwargs) -> Any
```

Create a client connection (currently not implemented).

**Parameters**:

* **database_name** (str): Database name.
* **path_or_uri** (str): Database path or URI.
* **token** (str, optional): Authentication token. Default: `""`.
* **kwargs** (Any): Additional parameters.

**Returns**:

* **Any**: Client object (currently returns `None`).

**Example**:

```python
>>> from openjiuwen.core.memory.store.impl.memory_chroma_vector_store import MemoryChromaVectorStore
>>> 
>>> # Create client connection (currently not implemented)
>>> vector_store = MemoryChromaVectorStore(persist_directory="./chroma_db")
>>> client = vector_store.create_client(
>>>     database_name="my_db",
>>>     path_or_uri="./chroma_db",
>>>     token="my_token"
>>> )
>>> print(f"Client: {client}")
```


### async get_collection

```
async def get_collection(self, table_name: str)
```

Get or create a Chroma collection with the specified name.

**Parameters**:

* **table_name** (str): Collection name.

**Returns**:

* **Collection**: Chroma collection object.

**Example**:

```python
>>> from openjiuwen.core.memory.store.impl.memory_chroma_vector_store import MemoryChromaVectorStore
>>> 
>>> # Get or create collection
>>> vector_store = MemoryChromaVectorStore(persist_directory="./chroma_db")
>>> collection = await vector_store.get_collection("documents")
>>> print(f"Collection name: {collection.name}")
>>> print(f"Document count: {collection.count()}")
```


### remove_collection_from_cache

```
def remove_collection_from_cache(self, table_name: str)
```

Remove the specified collection from cache.

**Parameters**:

* **table_name** (str): Collection name.

**Example**:

```python
>>> from openjiuwen.core.memory.store.impl.memory_chroma_vector_store import MemoryChromaVectorStore
>>> 
>>> # Remove collection from cache
>>> vector_store = MemoryChromaVectorStore(persist_directory="./chroma_db")
>>> vector_store.remove_collection_from_cache("documents")
>>> print("Collection removed from cache")
```


### check_table_name

```
def check_table_name(self, table_name: Optional[str] = None, operation: Optional[str] = None)
```

Check if the collection name is valid.

**Parameters**:

* **table_name** (Optional[str], optional): Collection name. Default: `None`.
* **operation** (Optional[str], optional): Current operation name, used for error messages. Default: `None`.

**Exceptions**:

* **JiuWenBaseException**: Raises `VALIDATION_INVALID` exception if the collection name is empty or `None`.

**Example**:

```python
>>> from openjiuwen.core.memory.store.impl.memory_chroma_vector_store import MemoryChromaVectorStore
>>> 
>>> # Check collection name
>>> vector_store = MemoryChromaVectorStore(persist_directory="./chroma_db")
>>> 
>>> try:
>>>     vector_store.check_table_name("", "add")
>>> except Exception as e:
>>>     print(f"Error: {e}")
>>> 
>>> try:
>>>     vector_store.check_table_name("documents", "add")
>>>     print("Collection name is valid")
>>> except Exception as e:
>>>     print(f"Error: {e}")
```


### async table_exists

```
async def table_exists(self, table_name: str) -> bool
```

Check if a collection with the specified name exists.

**Parameters**:

* **table_name** (str): Collection name.

**Returns**:

* **bool**: Returns `True` if the collection exists, otherwise `False`.

**Example**:

```python
>>> from openjiuwen.core.memory.store.impl.memory_chroma_vector_store import MemoryChromaVectorStore
>>> 
>>> # Check if collection exists
>>> vector_store = MemoryChromaVectorStore(persist_directory="./chroma_db")
>>> 
>>> if await vector_store.table_exists("documents"):
>>>     print("documents collection exists")
>>> else:
>>>     print("documents collection does not exist")
```


### async add

```
async def add(self, data: dict | List[dict], batch_size: int | None = 128, **kwargs: Any) -> None
```

Add vector data to the vector store.

**Parameters**:

* **data** (dict | List[dict]): Vector data to add, each dictionary contains "id" and "embedding" fields.
* **batch_size** (int | None, optional): Batch processing size. Default: 128.
* **kwargs** (Any): Additional parameters, must include "table_name" to specify the collection name.

**Exceptions**:

* **JiuWenBaseException**: Raised when add operation fails.

**Example**:

```python
>>> from openjiuwen.core.memory.store.impl.memory_chroma_vector_store import MemoryChromaVectorStore
>>> 
>>> # Add vector data
>>> vector_store = MemoryChromaVectorStore(persist_directory="./chroma_db")
>>> 
>>> # Single vector data
>>> await vector_store.add(
>>>     data={"id": "doc1", "embedding": [0.1, 0.2, 0.3]},
>>>     table_name="documents"
>>> )
>>> 
>>> # Batch vector data
>>> await vector_store.add(
>>>     data=[
>>>         {"id": "doc2", "embedding": [0.4, 0.5, 0.6]},
>>>         {"id": "doc3", "embedding": [0.7, 0.8, 0.9]}
>>>     ],
>>>     table_name="documents",
>>>     batch_size=64
>>> )
>>> print("Vector data added")
```


### async search

```
async def search(self, query_vector: List[float], top_k: int = 5, filters: Optional[dict] = None, **kwargs: Any) -> List[SearchResult]
```

Search for similar vectors based on the query vector.

**Parameters**:

* **query_vector** (List[float]): Query vector.
* **top_k** (int, optional): Number of results to return. Default: 5.
* **filters** (Optional[dict], optional): Filter conditions (currently unused). Default: `None`.
* **kwargs** (Any): Additional parameters, must include "table_name" to specify the collection name.

**Returns**:

* **List[SearchResult]**: List of search results, each result contains id, text, and score fields.

**Exceptions**:

* **JiuWenBaseException**: Raised when search operation fails.

**Example**:

```python
>>> from openjiuwen.core.memory.store.impl.memory_chroma_vector_store import MemoryChromaVectorStore
>>> 
>>> # Search for similar vectors
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
>>>     print(f"ID: {result.id}, Score: {result.score}, Text: {result.text}")
```


### async delete

```
async def delete(self, ids: Optional[List[str]] = None, filter_expr: Optional[str] = None, **kwargs: Any) -> bool
```

Delete vector data.

**Parameters**:

* **ids** (Optional[List[str]], optional): List of vector IDs to delete. Default: `None`.
* **filter_expr** (Optional[str], optional): Filter expression. Default: `None`.
* **kwargs** (Any): Additional parameters, must include "table_name" to specify the collection name.

**Returns**:

* **bool**: Returns `True` if deletion is successful, otherwise `False`.

**Exceptions**:

* **JiuWenBaseException**: Raised when delete operation fails.

**Example**:

```python
>>> from openjiuwen.core.memory.store.impl.memory_chroma_vector_store import MemoryChromaVectorStore
>>> 
>>> # Delete vector data
>>> vector_store = MemoryChromaVectorStore(persist_directory="./chroma_db")
>>> 
>>> # Delete by ID
>>> success = await vector_store.delete(
>>>     ids=["doc1", "doc2"],
>>>     table_name="documents"
>>> )
>>> print(f"Delete by ID result: {success}")
>>> 
>>> # Delete by filter expression
>>> success = await vector_store.delete(
>>>     filter_expr="metadata contains 'category'",
>>>     table_name="documents"
>>> )
>>> print(f"Delete by filter expression result: {success}")
```


### async delete_table

```
async def delete_table(self, table_name: str) -> bool
```

Delete the entire collection.

**Parameters**:

* **table_name** (str): Collection name.

**Returns**:

* **bool**: Returns `True` if deletion is successful, otherwise `False`.

**Exceptions**:

* **JiuWenBaseException**: Raised when delete operation fails.

**Example**:

```python
>>> from openjiuwen.core.memory.store.impl.memory_chroma_vector_store import MemoryChromaVectorStore
>>> 
>>> # Delete collection
>>> vector_store = MemoryChromaVectorStore(persist_directory="./chroma_db")
>>> 
>>> success = await vector_store.delete_table("documents")
>>> if success:
>>>     print("documents collection deleted successfully")
>>> else:
>>>     print("documents collection deletion failed")
```


### check_vector_field

```
def check_vector_field(self) -> None
```

Check if the vector field configuration is consistent with the actual database.

**Example**:

```python
>>> from openjiuwen.core.memory.store.impl.memory_chroma_vector_store import MemoryChromaVectorStore
>>> 
>>> # Check vector field configuration
>>> vector_store = MemoryChromaVectorStore(persist_directory="./chroma_db")
>>> vector_store.check_vector_field()
>>> print("Vector field configuration check completed")
```


## class openjiuwen.core.memory.store.impl.memory_milvus_vector_store.MemoryMilvusVectorStore

```
class openjiuwen.core.memory.store.impl.memory_milvus_vector_store.MemoryMilvusVectorStore
```

Milvus vector store implementation class, inheriting from the `VectorStore` abstract class, used for managing and operating vector data in the Milvus vector database.

```
MemoryMilvusVectorStore(milvus_host: str, milvus_port: str, token: str | None, embedding_dims: int)
```

Initialize the Milvus vector store client.

**Parameters**:

* **milvus_host** (str): Milvus server host address.
* **milvus_port** (str): Milvus server port.
* **token** (str | None): Authentication token.
* **embedding_dims** (int): Vector dimensions.

**Example**:

```python
>>> from openjiuwen.core.memory.store.impl.memory_milvus_vector_store import MemoryMilvusVectorStore
>>> 
>>> # Create Milvus vector store instance
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

Add vector data to the vector store.

**Parameters**:

* **data** (dict | List[dict]): Vector data to add, each dictionary contains "id" and "embedding" fields.
* **batch_size** (int | None, optional): Batch processing size. Default: 128.
* **kwargs** (Any): Additional parameters, must include "table_name" to specify the collection name.

**Exceptions**:

* **JiuWenBaseException**: Raised when add operation fails (`MEMORY_STORE_VALIDATION_INVALID`).

**Example**:

```python
>>> from openjiuwen.core.memory.store.impl.memory_milvus_vector_store import MemoryMilvusVectorStore
>>> 
>>> # Add vector data
>>> vector_store = MemoryMilvusVectorStore(
>>>     milvus_host="localhost",
>>>     milvus_port="19530",
>>>     token="your_token_here",
>>>     embedding_dims=768
>>> )
>>> 
>>> # Single vector data
>>> await vector_store.add(
>>>     data={"id": "doc1", "embedding": [0.1, 0.2, 0.3] + [0.0]*765},
>>>     table_name="documents"
>>> )
>>> 
>>> # Batch vector data
>>> await vector_store.add(
>>>     data=[
>>>         {"id": "doc2", "embedding": [0.4, 0.5, 0.6] + [0.0]*765},
>>>         {"id": "doc3", "embedding": [0.7, 0.8, 0.9] + [0.0]*765}
>>>     ],
>>>     table_name="documents",
>>>     batch_size=64
>>> )
>>> print("Vector data added")
```


### async search

```
async def search(self, query_vector: List[float], top_k: int = 5, filters: Optional[dict] = None, **kwargs: Any) -> List[SearchResult]
```

Search for similar vectors based on the query vector.

**Parameters**:

* **query_vector** (List[float]): Query vector.
* **top_k** (int, optional): Number of results to return. Default: 5.
* **filters** (Optional[dict], optional): Filter conditions (currently unused). Default: `None`.
* **kwargs** (Any): Additional parameters, must include "table_name" to specify the collection name.

**Returns**:

* **List[SearchResult]**: List of search results, each result contains id, text, and score fields.

**Exceptions**:

* **JiuWenBaseException**: Raised when search operation fails (`MEMORY_STORE_VALIDATION_INVALID`).

**Example**:

```python
>>> from openjiuwen.core.memory.store.impl.memory_milvus_vector_store import MemoryMilvusVectorStore
>>> 
>>> # Search for similar vectors
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
>>>     print(f"ID: {result.id}, Score: {result.score}, Text: {result.text}")
```


### async delete

```
async def delete(self, ids: Optional[List[str]] = None, filter_expr: Optional[str] = None, **kwargs: Any) -> bool
```

Delete vector data.

**Parameters**:

* **ids** (Optional[List[str]], optional): List of vector IDs to delete. Default: `None`.
* **filter_expr** (Optional[str], optional): Filter expression. Default: `None`.
* **kwargs** (Any): Additional parameters, must include "table_name" to specify the collection name.

**Returns**:

* **bool**: Returns `True` if deletion is successful, otherwise `False`.

**Exceptions**:

* **JiuWenBaseException**: Raised when delete operation fails (`MEMORY_STORE_VALIDATION_INVALID`).

**Example**:

```python
>>> from openjiuwen.core.memory.store.impl.memory_milvus_vector_store import MemoryMilvusVectorStore
>>> 
>>> # Delete vector data
>>> vector_store = MemoryMilvusVectorStore(
>>>     milvus_host="localhost",
>>>     milvus_port="19530",
>>>     token="your_token_here",
>>>     embedding_dims=768
>>> )
>>> 
>>> # Delete by ID
>>> success = await vector_store.delete(
>>>     ids=["doc1", "doc2"],
>>>     table_name="documents"
>>> )
>>> print(f"Delete by ID result: {success}")
```


### async delete_table

```
async def delete_table(self, table_name: str) -> bool
```

Delete the entire collection.

**Parameters**:

* **table_name** (str): Collection name.

**Returns**:

* **bool**: Returns `True` if deletion is successful, otherwise `False`.

**Example**:

```python
>>> from openjiuwen.core.memory.store.impl.memory_milvus_vector_store import MemoryMilvusVectorStore
>>> 
>>> # Delete collection
>>> vector_store = MemoryMilvusVectorStore(
>>>     milvus_host="localhost",
>>>     milvus_port="19530",
>>>     token="your_token_here",
>>>     embedding_dims=768
>>> )
>>> 
>>> success = await vector_store.delete_table("documents")
>>> if success:
>>>     print("documents collection deleted successfully")
>>> else:
>>>     print("documents collection deletion failed")
```


### async table_exists

```
async def table_exists(self, table_name: str) -> bool
```

Check if a collection with the specified name exists.

**Parameters**:

* **table_name** (str): Collection name.

**Returns**:

* **bool**: Returns `True` if the collection exists, otherwise `False`.

**Example**:

```python
>>> from openjiuwen.core.memory.store.impl.memory_milvus_vector_store import MemoryMilvusVectorStore
>>> 
>>> # Check if collection exists
>>> vector_store = MemoryMilvusVectorStore(
>>>     milvus_host="localhost",
>>>     milvus_port="19530",
>>>     token="your_token_here",
>>>     embedding_dims=768
>>> )
>>> 
>>> if await vector_store.table_exists("documents"):
>>>     print("documents collection exists")
>>> else:
>>>     print("documents collection does not exist")
```


### check_vector_field

```
def check_vector_field(self) -> None
```

Check if the vector field configuration is consistent with the actual database.

**Example**:

```python
>>> from openjiuwen.core.memory.store.impl.memory_milvus_vector_store import MemoryMilvusVectorStore
>>> 
>>> # Check vector field configuration
>>> vector_store = MemoryMilvusVectorStore(
>>>     milvus_host="localhost",
>>>     milvus_port="19530",
>>>     token="your_token_here",
>>>     embedding_dims=768
>>> )
>>> vector_store.check_vector_field()
>>> print("Vector field configuration check completed")
```
