# openjiuwen.core.foundation.store

`openjiuwen.core.foundation.store` provides **abstract base classes and built-in implementations** for KV storage, database storage, and vector storage, for reuse by modules such as memory and session within the framework:

- Defines `BaseKVStore` key-value storage abstract interface (set, get, exists, delete, prefix queries, batch mget, etc.);
- Defines `BaseDbStore` database storage abstract interface (get async Engine);
- Defines `BaseVectorStore` vector storage abstract interface (collection management, document insertion, vector search, document deletion);
- Provides `InMemoryKVStore` (in-memory implementation), `DbBasedKVStore` (SQLAlchemy-based implementation), `DefaultDbStore` (BaseDbStore default implementation);
- Provides `create_vector_store()` factory function for creating vector store instances.

Corresponding source code: `openjiuwen.core.foundation.store`.

## class BaseKVStore

```python
class openjiuwen.core.foundation.store.base_kv_store.BaseKVStore(ABC)
```

KV storage abstract base class, defining a unified key-value storage interface. All KV implementations must inherit from this class and implement its abstract methods.

Corresponding source code: `openjiuwen.core.foundation.store.base_kv_store.BaseKVStore`.

### abstractmethod async set

```python
async def set(key: str, value: str)
```

Set key-value pair; if key already exists, overwrite it.

**Parameters**:

- `key: str`: Key, unique identifier. Default value: none.
- `value: str`: Value, string payload. Default value: none.

### abstractmethod async exclusive_set

```python
async def exclusive_set(key: str, value: str, expiry: int | None = None) -> bool
```

Atomically set key-value pair, only when the key does not exist or has expired.

**Parameters**:

- `key: str`: Key. Default value: none.
- `value: str`: Value. Default value: none.
- `expiry: int | None`: Key-value pair expiration time (seconds), optional. Default value: `None`.

**Returns**:

- `bool`: Returns `True` on successful set; returns `False` if key already exists and has not expired.

### abstractmethod async get

```python
async def get(key: str) -> str | None
```

Get value by key; returns `None` when key does not exist.

**Parameters**:

- `key: str`: Key. Default value: none.

**Returns**:

- `str | None`: Value corresponding to key, `None` if it doesn't exist.

### abstractmethod async exists

```python
async def exists(key: str) -> bool
```

Check if key exists.

**Parameters**:

- `key: str`: Key. Default value: none.

**Returns**:

- `bool`: `True` if exists, otherwise `False`.

### abstractmethod async delete

```python
async def delete(key: str)
```

Delete specified key; if key does not exist, no operation is performed.

**Parameters**:

- `key: str`: Key. Default value: none.

### abstractmethod async get_by_prefix

```python
async def get_by_prefix(prefix: str) -> dict[str, str]
```

Get all matching key-value pairs by prefix.

**Parameters**:

- `prefix: str`: Prefix string. Default value: none.

**Returns**:

- `dict[str, str]`: Dictionary of prefix-matched key-value pairs.

### abstractmethod async delete_by_prefix

```python
async def delete_by_prefix(prefix: str)
```

Delete all matching key-value pairs by prefix.

**Parameters**:

- `prefix: str`: Prefix string. Default value: none.

### abstractmethod async mget

```python
async def mget(keys: List[str]) -> List[str | None]
```

Batch get values for multiple keys; when a key does not exist, the corresponding position in the returned list is `None`.

**Parameters**:

- `keys: List[str]`: List of keys. Default value: none.

**Returns**:

- `List[str | None]`: List of values corresponding to `keys` in order, `None` where not present.

---

## class BaseDbStore

```python
class openjiuwen.core.foundation.store.base_db_store.BaseDbStore(ABC)
```

Database storage abstract base class, defining the interface for getting async DB Engine, for callers to perform async database operations (e.g., create tables, execute SQL).

Corresponding source code: `openjiuwen.core.foundation.store.base_db_store.BaseDbStore`.

### abstractmethod get_async_engine

```python
def get_async_engine(self) -> AsyncEngine
```

Return async SQLAlchemy engine instance.

**Returns**:

- `AsyncEngine`: Async SQLAlchemy engine for executing async database operations.

---

## class InMemoryKVStore

```python
class openjiuwen.core.foundation.store.in_memory_kv_store.InMemoryKVStore(BaseKVStore)
```

In-memory KV storage implementation, implementing all interfaces of `BaseKVStore`; supports `exclusive_set`'s `expiry` expiration time, expired keys are treated as non-existent during `get` (not automatically deleted).

Corresponding source code: `openjiuwen.core.foundation.store.kv.in_memory_kv_store.InMemoryKVStore`.

```python
InMemoryKVStore()
```

Parameterless constructor; internally uses dictionary and `asyncio.Lock` to ensure concurrency safety.

**Behavior**:

1. Initialize internal dictionary `_store: dict[str, tuple[str, Optional[int]]]` (value and expiration timestamp);
2. Initialize `_lock = asyncio.Lock()`;
3. Implement `BaseKVStore`'s set, get, delete, exclusive_set, exists, get_by_prefix, delete_by_prefix, mget; expired keys return `None` during get, not automatically removed from dictionary.

---

## class DbBasedKVStore

```python
class openjiuwen.core.foundation.store.db_based_kv_store.DbBasedKVStore(BaseKVStore)
```

SQLAlchemy async engine-based KV storage implementation, using table `kv_store` (key, value columns); automatically creates table on first call to any interface.

Corresponding source code: `openjiuwen.core.foundation.store.kv.db_based_kv_store.DbBasedKVStore`.

```python
DbBasedKVStore(engine: AsyncEngine)
```

**Parameters**:

- `engine: AsyncEngine`: Async database engine for creating sessions and executing SQL.

**Behavior**:

1. Save `engine`, create `async_sessionmaker(engine, ..., class_=AsyncSession)`;
2. Set `table_created = False`, create `kv_store` table through `_create_table_if_not_exist()` on first call to set/get, etc.;
3. Implement all `BaseKVStore` interfaces; `exclusive_set`'s expiry is implemented by storing `{"value": ..., "expiry": ...}` in JSON within value.

---

## class DefaultDbStore

```python
class openjiuwen.core.foundation.store.default_db_store.DefaultDbStore(BaseDbStore)
```

Default implementation of `BaseDbStore`, directly holds and returns the passed `AsyncEngine`.

Corresponding source code: `openjiuwen.core.foundation.store.db.default_db_store.DefaultDbStore`.

```python
DefaultDbStore(async_conn: AsyncEngine)
```

**Parameters**:

- `async_conn: AsyncEngine`: Async database connection (engine).

### get_async_engine

```python
def get_async_engine(self) -> AsyncEngine
```

Return the `async_conn` passed during construction.

**Returns**:

- `AsyncEngine`: Async engine instance.

---

## class BaseVectorStore

```python
class openjiuwen.core.foundation.store.base_vector_store.BaseVectorStore(ABC)
```

Vector storage abstract base class, defining a unified vector storage interface supporting collection management, document insertion, vector search, and document deletion operations.

Corresponding source code: `openjiuwen.core.foundation.store.base_vector_store.BaseVectorStore`.

### abstractmethod async create_collection

```python
async def create_collection(
    self,
    collection_name: str,
    schema: Union[CollectionSchema, Dict[str, Any]],
    **kwargs: Any,
) -> None
```

Create a new collection with specified schema.

**Parameters**:

- `collection_name: str`: Name of the collection to create
- `schema: Union[CollectionSchema, Dict[str, Any]]`: CollectionSchema instance or schema dictionary
- `**kwargs: Any`: Additional parameters
    - `distance_metric: str`: Vector search distance metric (e.g., "COSINE", "L2", "IP")

### abstractmethod async delete_collection

```python
async def delete_collection(self, collection_name: str, **kwargs: Any) -> None
```

Delete a collection by name.

**Parameters**:

- `collection_name: str`: Name of the collection to delete
- `**kwargs: Any`: Additional parameters

### abstractmethod async collection_exists

```python
async def collection_exists(self, collection_name: str, **kwargs: Any) -> bool
```

Check if a collection exists.

**Parameters**:

- `collection_name: str`: Collection name
- `**kwargs: Any`: Additional parameters

**Returns**:

- `bool`: `True` if the collection exists, `False` otherwise

### abstractmethod async get_schema

```python
async def get_schema(self, collection_name: str, **kwargs: Any) -> CollectionSchema
```

Get the schema of a collection.

**Parameters**:

- `collection_name: str`: Collection name
- `**kwargs: Any`: Additional parameters

**Returns**:

- `CollectionSchema`: The schema of the collection

### abstractmethod async add_docs

```python
async def add_docs(
    self,
    collection_name: str,
    docs: List[Dict[str, Any]],
    **kwargs: Any,
) -> None
```

Add documents to a collection.

**Parameters**:

- `collection_name: str`: Name of the target collection
- `docs: List[Dict[str, Any]]`: List of documents to add, each containing:
    - `id: str` (optional): Document ID
    - `embedding: List[float]`: Document vector embedding
    - `text: str`: Document text content
    - `metadata: Dict[str, Any]` (optional): Additional metadata
- `**kwargs: Any`: Additional parameters
    - `batch_size: int` (optional): Batch size for bulk insertion

### abstractmethod async search

```python
async def search(
    self,
    collection_name: str,
    query_vector: List[float],
    vector_field: str,
    top_k: int = 5,
    filters: Optional[Dict[str, Any]] = None,
    **kwargs: Any,
) -> List[VectorSearchResult]
```

Search for the most relevant documents by vector similarity.

**Parameters**:

- `collection_name: str`: Name of the collection to search
- `query_vector: List[float]`: Query vector for similarity search
- `vector_field: str`: Name of the vector field to search against (e.g., "embedding")
- `top_k: int`: Number of most relevant documents to return, default 5
- `filters: Optional[Dict[str, Any]]`: Scalar field filters for filtering results (equality filter only), default `None`
- `**kwargs: Any`: Additional search parameters
    - `metric_type: str` (optional): Distance metric type
    - `output_fields: List[str]` (optional): Fields to return in results

**Returns**:

- `List[VectorSearchResult]`: List of search results, each containing:
    - `score: float`: Relevance score (higher is more relevant)
    - `fields: Dict[str, Any]`: All field values from the matched document

### abstractmethod async delete_docs_by_ids

```python
async def delete_docs_by_ids(
    self,
    collection_name: str,
    ids: List[str],
    **kwargs: Any,
) -> None
```

Delete documents by their IDs.

**Parameters**:

- `collection_name: str`: Collection name
- `ids: List[str]`: List of document IDs to delete
- `**kwargs: Any`: Additional parameters

### abstractmethod async delete_docs_by_filters

```python
async def delete_docs_by_filters(
    self,
    collection_name: str,
    filters: Dict[str, Any],
    **kwargs: Any,
) -> None
```

Delete documents by scalar field filters.

**Parameters**:

- `collection_name: str`: Collection name
- `filters: Dict[str, Any]`: Scalar field filters for matching documents to delete (equality filter only)
- `**kwargs: Any`: Additional parameters

### abstractmethod async list_collection_names

```python
async def list_collection_names(self) -> List[str]
```

List all collection names in the vector store.

This method returns a list of all collection names currently in the vector store.

**Returns**:

- `List[str]`: A list of collection names

### abstractmethod async update_schema

```python
async def update_schema(
    self,
    collection_name: str,
    operations: List[BaseOperation],
) -> None
```

Update the schema of a collection for vector data migration.

This method applies a series of schema migration operations to modify the structure of a collection. Supported operations include:
- `AddScalarFieldOperation`: Add a new scalar field
- `RenameScalarFieldOperation`: Rename an existing scalar field
- `UpdateScalarFieldTypeOperation`: Change the data type of a scalar field
- `UpdateEmbeddingDimensionOperation`: Modify the dimension of vector embeddings

**Parameters**:

- `collection_name: str`: The name of the collection to modify
- `operations: List[BaseOperation]`: A list of migration operations to apply

### abstractmethod async get_collection_metadata

```python
async def get_collection_metadata(
    self,
    collection_name: str,
) -> Dict[str, Any]
```

Get collection metadata including distance metric, vector field, and schema version.

This method retrieves metadata about a collection, including:
- distance_metric: The distance metric used for vector search (e.g., "COSINE", "L2", "IP")
- vector_field: The name of the vector field in the collection
- schema_version: The schema version stored in collection properties (0 if not set)

Implementation should first check local cache, and if cache miss, fetch from
the underlying storage (e.g., Milvus collection properties and schema).

**Parameters**:

- `collection_name: str`: Name of the collection to get metadata for

**Returns**:

- `Dict[str, Any]`: Dictionary containing collection metadata with keys:
    - `distance_metric: str`: The distance metric type (e.g., "COSINE", "L2", "IP")
    - `vector_field: str`: The vector field name
    - `schema_version: int`: The schema version number (0 if not set)

### abstractmethod async update_collection_metadata

```python
async def update_collection_metadata(
    self,
    collection_name: str,
    metadata: Dict[str, Any],
) -> None
```

Update collection metadata stored in the collection properties.

This method updates metadata for a collection, storing the key-value pairs
in the underlying storage's collection properties (e.g., Milvus collection
properties). All values are converted to strings for storage.

The metadata typically includes:
- schema_version: The schema version number (must be a non-negative integer)
- Other custom properties (all values will be converted to strings)

Implementation should:
1. Validate input (e.g., schema_version must be non-negative integer)
2. Store metadata in underlying storage's collection properties
3. Update local cache to keep it in sync

**Parameters**:

- `collection_name: str`: Name of the collection to update
- `metadata: Dict[str, Any]`: Dictionary of metadata key-value pairs to update.
    Supported keys include:
    - `schema_version: int`: The schema version number (must be >= 0)
    - Other custom properties (all values will be converted to strings)
----

## class GaussVectorStore

```python
class openjiuwen.core.foundation.store.vector.gauss_vector_store.GaussVectorStore(BaseVectorStore)
```

Vector storage implementation based on GaussVector Database, inheriting from `BaseVectorStore` and implementing all abstract methods.

Corresponding source code: `openjiuwen.core.foundation.store.vector.gauss_vector_store.GaussVectorStore`.

```python
GaussVectorStore(
    host: str = "localhost",
    port: int = 5432,
    database: str = "postgres",
    user: str = "postgres",
    password: str = "",
    **kwargs: Any,
)
```

**Parameters**:

- `host: str`: GaussVector server host address. Default: `"localhost"`.
- `port: int`: GaussVector server port. Default: `5432`.
- `database: str`: Database name. Default: `"postgres"`.
- `user: str`: Database user. Default: `"postgres"`.
- `password: str`: Database password. Default: `""`.
- `**kwargs: Any`: Additional connection parameters (e.g., `connection_timeout`, `sslmode`).

**Behavior**:

1. Connection uses lazy loading mode, established on first use;
2. Internally uses `psycopg2` to connect to GaussDB;
3. Supports collection (table) management, document CRUD, and vector search operations.

### property connection

```python
@property
def connection(self) -> Any
```

Get database connection (lazy loading). Automatically creates connection on first access.

**Returns**:

- `Any`: psycopg2 database connection object.

### close

```python
def close(self) -> None
```

Close the database connection.

### create_collection

```python
async def create_collection(
    self,
    collection_name: str,
    schema: Union[CollectionSchema, Dict[str, Any]],
    **kwargs: Any,
) -> None
```

Create a new collection (table).

**Parameters**:

- `collection_name: str`: Name of the collection to create.
- `schema: Union[CollectionSchema, Dict[str, Any]]`: CollectionSchema instance or schema dictionary.
- `**kwargs: Any`: Additional parameters
    - `distance_metric: str`: Distance metric (default: `"COSINE"`, options: `"L2"`)
    - `index_type: str`: Index type (default: `"diskann"`, currently only supports DiskANN)
    - `pg_nseg: int`: PQ segment count (default: 128)
    - `pg_nclus: int`: Number of clusters (default: 16)
    - `num_parallels: int`: Number of parallels (default: 32)

### delete_collection

```python
async def delete_collection(
    self,
    collection_name: str,
    **kwargs: Any,
) -> None
```

Delete a specified collection.

**Parameters**:

- `collection_name: str`: Name of the collection to delete.
- `**kwargs: Any`: Additional parameters.

### collection_exists

```python
async def collection_exists(
    self,
    collection_name: str,
    **kwargs: Any,
) -> bool
```

Check if a collection exists.

**Parameters**:

- `collection_name: str`: Collection name.
- `**kwargs: Any`: Additional parameters.

**Returns**:

- `bool`: Returns `True` if exists, `False` otherwise.

### get_schema

```python
async def get_schema(
    self,
    collection_name: str,
    **kwargs: Any,
) -> CollectionSchema
```

Get the schema of a collection.

**Parameters**:

- `collection_name: str`: Collection name.
- `**kwargs: Any`: Additional parameters.

**Returns**:

- `CollectionSchema`: The schema of the collection.

### add_docs

```python
async def add_docs(
    self,
    collection_name: str,
    docs: List[Dict[str, Any]],
    **kwargs: Any,
) -> None
```

Add documents to a collection.

**Parameters**:

- `collection_name: str`: Name of the target collection.
- `docs: List[Dict[str, Any]]`: List of documents to add, each containing:
    - `id: str` (optional): Document ID
    - `embedding: List[float]`: Document vector embedding
    - `text: str`: Document text content
    - `metadata: Dict[str, Any]` (optional): Additional metadata
- `**kwargs: Any`: Additional parameters
    - `batch_size: int` (optional): Batch size for bulk insertion (default: 128)

### search

```python
async def search(
    self,
    collection_name: str,
    query_vector: List[float],
    vector_field: str,
    top_k: int = 5,
    filters: Optional[Dict[str, Any]] = None,
    **kwargs: Any,
) -> List[VectorSearchResult]
```

Search for the most relevant documents by vector similarity.

**Parameters**:

- `collection_name: str`: Name of the collection to search.
- `query_vector: List[float]`: Query vector for similarity search.
- `vector_field: str`: Name of the vector field to search against.
- `top_k: int`: Number of most relevant documents to return, default 5.
- `filters: Optional[Dict[str, Any]]`: Scalar field filters for filtering results (equality filter only), default `None`.
- `**kwargs: Any`: Additional search parameters
    - `metric_type: str` (optional): Distance metric type
    - `output_fields: List[str]` (optional): Fields to return in results

**Returns**:

- `List[VectorSearchResult]`: List of search results, each containing:
    - `score: float`: Relevance score (higher is more relevant)
    - `fields: Dict[str, Any]`: All field values from the matched document

### delete_docs_by_ids

```python
async def delete_docs_by_ids(
    self,
    collection_name: str,
    ids: List[str],
    **kwargs: Any,
) -> None
```

Delete documents by their IDs.

**Parameters**:

- `collection_name: str`: Collection name.
- `ids: List[str]`: List of document IDs to delete.
- `**kwargs: Any`: Additional parameters
    - `id_column: str` (optional): ID field name (default: `"id"`).

### delete_docs_by_filters

```python
async def delete_docs_by_filters(
    self,
    collection_name: str,
    filters: Dict[str, Any],
    **kwargs: Any,
) -> None
```

Delete documents by scalar field filters.

**Parameters**:

- `collection_name: str`: Collection name.
- `filters: Dict[str, Any]`: Scalar field filters for matching documents to delete (equality filter only).
- `**kwargs: Any`: Additional parameters.

### list_collection_names

```python
async def list_collection_names(self) -> List[str]
```

List all collection names in the vector store.

**Returns**:

- `List[str]`: A list of collection names.

### get_collection_metadata

```python
async def get_collection_metadata(
    self,
    collection_name: str,
) -> Dict[str, Any]
```

Get collection metadata.

**Parameters**:

- `collection_name: str`: Collection name.

**Returns**:
- `Dict[str, Any]`: Dictionary containing collection metadata with keys:
    - `distance_metric: str`: The distance metric type (e.g., `"COSINE"`, `"L2"`)
    - `vector_field: str`: The vector field name
    - `schema_version: int`: The schema version number (0 if not set)

### update_collection_metadata

```python
async def update_collection_metadata(
    self,
    collection_name: str,
    metadata: Dict[str, Any],
) -> None
```

Update collection metadata.

**Parameters**:

- `collection_name: str`: Collection name.
- `metadata: Dict[str, Any]`: Dictionary of metadata to update. Supports keys:
    - `schema_version: int`: The schema version number (must be a non-negative integer)
    - Other custom properties.

### update_schema

```python
async def update_schema(
    self,
    collection_name: str,
    operations: List[BaseOperation],
) -> None
```

Update the schema of a collection for vector data migration.

This method applies a series of schema migration operations to modify the structure of a collection. Supported operations include:
- `AddScalarFieldOperation`: Add a new scalar field
- `RenameScalarFieldOperation`: Rename an existing scalar field
- `UpdateScalarFieldTypeOperation`: Change the data type of a scalar field
- `UpdateEmbeddingDimensionOperation`: Modify the dimension of vector embeddings

**Parameters**:

- `collection_name: str`: The name of the collection to modify.
- `operations: List[BaseOperation]`: A list of migration operations to apply.

---

## function create_vector_store

```python
def openjiuwen.core.foundation.store.create_vector_store(
    store_type: str,
    **kwargs: Any,
) -> BaseVectorStore | None
```

Vector-store factory function. Resolution order:

1. **Built-in backends**: `"chroma"`, `"milvus"`, `"gaussvector"` (closed set, always wins).
2. **Explicit registrations**: implementations registered in-process via `register_vector_store(name, factory)`.
3. **Entry points**: third-party plugins published under the `openjiuwen.vector_stores` group.

A plugin that fails to load or instantiate is logged at WARNING and returns `None` — a broken plugin cannot crash the factory for the whole application.

**Parameters**:

- `store_type: str`: Storage type. In addition to built-ins, may be any name registered via `register_vector_store` or exposed by an installed plugin's entry_points.
- `**kwargs: Any`: Additional parameters passed verbatim to the backend constructor.

**Returns**:

- `BaseVectorStore | None`: Vector-store instance, or `None` if no backend matches / plugin loading failed.

---

## function register_vector_store

```python
def openjiuwen.core.foundation.store.register_vector_store(
    name: str,
    factory: Callable[..., BaseVectorStore],
) -> None
```

Programmatically register a third-party vector-store implementation in the current process. Useful for **private backends** that are not shipped via PyPI and therefore cannot use the entry_points mechanism. After registration, `create_vector_store(name, ...)` can create instances by name.

**Parameters**:

- `name: str`: Backend identifier to be passed later to `create_vector_store(name, ...)`.
- `factory: Callable[..., BaseVectorStore]`: Callable accepting `**kwargs` and returning a `BaseVectorStore` (typically the class itself).

**Behavior**:

- Thread-safety: **not thread-safe**. Call during application init, before any worker thread starts.
- Built-in names (`chroma` / `milvus` / `gaussvector`) cannot be overridden: calling `register_vector_store` with a built-in name has no effect (the built-in always wins in factory resolution).

---

## constant VECTOR_STORE_ENTRY_POINT_GROUP

```python
VECTOR_STORE_ENTRY_POINT_GROUP = "openjiuwen.vector_stores"
```

The Python entry_points group name that third-party vector-store plugins must declare. **Stable public constant** — changing this string would break every published plugin.

Plugin authors declare it in their package's `pyproject.toml`:

```toml
[project.entry-points."openjiuwen.vector_stores"]
my_backend = "my_package.my_vector_store:MyVectorStore"
```

After `pip install my-package`, users call `create_vector_store("my_backend", ...)` to obtain an instance. Full authoring guide: [Store Plugin Development](../../../Advanced%20Usage/Store%20Plugin%20Development.md).

---

## Typical Usage Flow Example

```python
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncEngine

from openjiuwen.core.foundation.store import (
    BaseKVStore,
    BaseDbStore,
    InMemoryKVStore,
    DbBasedKVStore,
    DefaultDbStore,
    BaseVectorStore,
    CollectionSchema,
    FieldSchema,
    VectorDataType,
    create_vector_store,
)


async def demo_kv_store():
    # 1. In-memory KV storage (no external dependencies)
    kv = InMemoryKVStore()
    await kv.set("user:001:name", "Alice")
    value = await kv.get("user:001:name")
    print(value)  # Alice

    exists = await kv.exists("user:001:name")
    print(exists)  # True

    await kv.delete("user:001:name")
    print(await kv.get("user:001:name"))  # None


async def demo_db_store():
    # 2. Database-based KV storage (requires SQLite/MySQL, etc.)
    async_engine = create_async_engine(
        "sqlite+aiosqlite:///demo.db",
        echo=False,
    )
    db_kv = DbBasedKVStore(async_engine)
    await db_kv.set("config:key1", "value1")
    result = await db_kv.get("config:key1")
    print(result)  # value1


async def demo_default_db_store():
    # 3. DefaultDbStore only exposes Engine for modules like memory engine to create tables, execute SQL
    async_engine = create_async_engine("sqlite+aiosqlite:///app.db")
    db_store = DefaultDbStore(async_engine)
    engine = db_store.get_async_engine()
    assert engine is async_engine


async def demo_vector_store():
    # 4. Vector storage (using ChromaDB or Milvus)
    # Create ChromaDB vector store using factory function
    store = create_vector_store("chroma", persist_directory="./data/chroma")

    # Define collection schema
    schema = CollectionSchema(
        description="Document collection",
        enable_dynamic_field=False,
    )
    schema.add_field(FieldSchema(
        name="id",
        dtype=VectorDataType.VARCHAR,
        max_length=256,
        is_primary=True,
    ))
    schema.add_field(FieldSchema(
        name="embedding",
        dtype=VectorDataType.FLOAT_VECTOR,
        dim=768,
    ))
    schema.add_field(FieldSchema(
        name="text",
        dtype=VectorDataType.VARCHAR,
        max_length=65535,
    ))
    schema.add_field(FieldSchema(
        name="metadata",
        dtype=VectorDataType.JSON,
    ))

    # Create collection
    await store.create_collection("documents", schema, distance_metric="cosine")

    # Add documents
    docs = [
        {
            "id": "doc1",
            "embedding": [0.1] * 768,
            "text": "This is the first document",
            "metadata": {"category": "tech"},
        },
        {
            "id": "doc2",
            "embedding": [0.2] * 768,
            "text": "This is the second document",
            "metadata": {"category": "news"},
        },
    ]
    await store.add_docs("documents", docs)

    # Vector search
    query_vector = [0.15] * 768
    results = await store.search(
        collection_name="documents",
        query_vector=query_vector,
        vector_field="embedding",
        top_k=10,
        filters={"category": "tech"},
    )

    for result in results:
        print(f"Score: {result.score:.4f}, Text: {result.fields.get('text')}")


asyncio.run(demo_kv_store())
asyncio.run(demo_db_store())
asyncio.run(demo_default_db_store())
asyncio.run(demo_vector_store())
```

> **Note**: `InMemoryKVStore` is suitable for single-process, non-persistent scenarios; `DbBasedKVStore` is suitable for scenarios requiring persistence, multi-process sharing, or integration with memory/session modules; `DefaultDbStore` is typically used with `LongTermMemory.register_store(db_store=...)`, etc.
