# openjiuwen.core.foundation.store.base_memory_index / index.simple_memory_index

`openjiuwen.core.foundation.store` provides a **memory index** abstraction and a legacy-compatible implementation on top of KV storage and vector storage:

- Defines the `StorageCodec` protocol for encoding/decoding memory text during write/read operations (e.g., AES encryption/decryption);
- Defines the `MemoryDoc` data model, encapsulating `id` / `text` / `type` / `timestamp` / extension fields for a single memory entry;
- Defines the `BaseMemoryIndex` abstract base class, specifying interfaces for memory CRUD, bulk deletion by user/scope, semantic retrieval, paginated listing, backup, and version management;
- Provides `SimpleMemoryIndex`, which implements the above interfaces based on `BaseKVStore` + `BaseVectorStore`, **retained only for backward compatibility with legacy `SemanticStore + UserMemStore` data; deprecated**.

Source code: `openjiuwen.core.foundation.store.base_memory_index`, `openjiuwen.core.foundation.store.index.simple_memory_index`.

## class StorageCodec

```python
class openjiuwen.core.foundation.store.base_memory_index.StorageCodec(
    Protocol
)
```

Memory text encoding/decoding protocol (`typing.Protocol`, with `@runtime_checkable`). Implementations must provide `encode` / `decode` methods for encoding text before writing to KV storage (e.g., encryption) and decoding after reading (e.g., decryption).

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

Encodes plaintext memory text into a persistable string (e.g., ciphertext).

**Parameters**:

- `text: str`: Plaintext memory text.

**Returns**: `str`, the encoded string.

### decode

```python
def decode(self, data: str) -> str
```

Decodes a persisted string back into plaintext memory text.

**Parameters**:

- `data: str`: The encoded string.

**Returns**: `str`, the decoded plaintext text.

## class MemoryDoc

```python
class openjiuwen.core.foundation.store.base_memory_index.MemoryDoc(BaseModel)
```

Data model for a single memory document (`pydantic.BaseModel`), encapsulating the unique identifier, text content, type, and timestamp of a memory entry, with support for arbitrary extension fields.

**Fields**:

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `id` | `str` | `""` | Unique identifier for the memory document. |
| `text` | `str` | `""` | Text content of the memory. |
| `type` | `str` | `""` | Type/category of the memory (e.g., `user_profile`). |
| `timestamp` | `datetime` | Current local timezone time (`datetime.now(timezone.utc).astimezone()`) | Timestamp of the memory entry. |
| `fields` | `dict[str, Any]` | `{}` | Additional extension fields. |

## class BaseMemoryIndex

```python
class openjiuwen.core.foundation.store.base_memory_index.BaseMemoryIndex(ABC)
```

Abstract base class for memory index, defining a unified interface for memory storage and retrieval. Concrete implementations should persist and retrieve `MemoryDoc` using specific backends (vector databases, relational databases, etc.).

Memory documents are scoped by the dual dimensions of `user_id` and `scope_id`, supporting multi-tenant, multi-scenario memory management. Subclasses must implement the methods marked as `abstractmethod`; `list_memories` / `get_schema_version` / `update_schema_version` / `create_backup` / `restore_backup` have default (no-op) implementations that subclasses can override as needed.

Source code: `openjiuwen.core.foundation.store.base_memory_index.BaseMemoryIndex`.

### abstractmethod set_storage_codec

```python
def set_storage_codec(self, codec: StorageCodec) -> None
```

Sets the encoder/decoder for memory text, used for encryption/decryption during subsequent write/read operations.

**Parameters**:

- `codec: StorageCodec`: An encoder/decoder instance implementing `encode` / `decode`.

### abstractmethod async add_memories

```python
async def add_memories(self, user_id: str, scope_id: str, memories: list[MemoryDoc])
```

Adds memory documents.

**Parameters**:

- `user_id: str`: User identifier; memories are scoped by this.
- `scope_id: str`: Scope identifier for grouping related memories.
- `memories: list[MemoryDoc]`: List of memory documents to add.

### abstractmethod async update_memories

```python
async def update_memories(self, user_id: str, scope_id: str, memories: list[MemoryDoc])
```

Updates memory documents.

**Parameters**:

- `user_id: str`: User identifier.
- `scope_id: str`: Scope identifier.
- `memories: list[MemoryDoc]`: List of memory documents to update.

### abstractmethod async delete_memories

```python
async def delete_memories(self, user_id: str, scope_id: str, ids: list[str])
```

Deletes specified memory documents by ID.

**Parameters**:

- `user_id: str`: User identifier.
- `scope_id: str`: Scope identifier.
- `ids: list[str]`: List of memory document IDs to delete.

### abstractmethod async delete_by_user

```python
async def delete_by_user(self, user_id: str)
```

Deletes all memories for a given user across all scopes.

**Parameters**:

- `user_id: str`: User identifier whose memories should be cleared.

### abstractmethod async delete_by_scope

```python
async def delete_by_scope(self, scope_id: str)
```

Deletes all memories for all users within a given scope.

**Parameters**:

- `scope_id: str`: Scope identifier whose memories should be cleared.

### abstractmethod async delete_by_user_and_scope

```python
async def delete_by_user_and_scope(self, user_id: str, scope_id: str)
```

Deletes all memories for a specific user-scope combination.

**Parameters**:

- `user_id: str`: User identifier.
- `scope_id: str`: Scope identifier.

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

Performs semantic retrieval over stored memories, returning the most relevant results with their relevance scores.

**Parameters**:

- `user_id: str`: User identifier.
- `scope_id: str`: Scope identifier.
- `query: str`: Query text for retrieval.
- `mem_types: list[str] | None`: Filter by memory type:
  - One or more specific types (e.g., `["user_profile"]`);
  - Empty list `[]` means retrieve all types.
- `top_k: int = 10`: Maximum number of results to return.

**Returns**: `list[tuple[MemoryDoc, float]]`, each item is `(memory_document, relevance_score)`. Scores typically fall in `[0, 1]`, with higher values indicating greater relevance.

### abstractmethod async get_by_id

```python
async def get_by_id(self, user_id: str, scope_id: str, mem_id: str) -> MemoryDoc | None
```

Retrieves a single memory document by `mem_id`; returns `None` if not found.

**Parameters**:

- `user_id: str`: User identifier.
- `scope_id: str`: Scope identifier.
- `mem_id: str`: Unique identifier of the memory document.

**Returns**: `MemoryDoc | None`.

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

Lists memory documents for a given user and scope with pagination. The base class provides a no-op implementation; subclasses can override as needed.

**Parameters**:

- `user_id: str`: User identifier.
- `scope_id: str`: Scope identifier.
- `offset: int = 0`: Starting offset.
- `limit: int = 100`: Maximum number of documents to return.
- `mem_types: list[str] | None`: Filter by type:
  - One or more specific types (e.g., `["user_profile"]`);
  - Empty list `[]` means list all types;
  - When multiple types are specified, results are output in the given `mem_type` order.

**Returns**: `list[MemoryDoc]`.

### get_schema_version

```python
def get_schema_version(self) -> int
```

Gets the current schema version. The base class returns `0` by default; subclasses can override as needed.

**Returns**: `int`.

### update_schema_version

```python
def update_schema_version(self, version: int) -> None
```

Updates the schema version. The base class provides a no-op implementation; subclasses can override as needed.

**Parameters**:

- `version: int`: The new version number.

### async create_backup

```python
async def create_backup(self) -> str
```

Creates a backup of the current data and returns a backup identifier. The base class provides a no-op implementation; subclasses can override as needed.

**Returns**: `str`, the backup identifier.

### async restore_backup

```python
async def restore_backup(self, backup_id: str) -> None
```

Restores data from a backup. The base class provides a no-op implementation; subclasses can override as needed.

**Parameters**:

- `backup_id: str`: The backup identifier.

### abstractmethod async cleanup_backup

```python
async def cleanup_backup(self, backup_id: str) -> None
```

Cleans up a specified backup. Subclasses must implement this.

**Parameters**:

- `backup_id: str`: The backup identifier.

### abstractmethod async list_user_scopes

```python
async def list_user_scopes(self) -> list[tuple[str, str]]
```

Lists all `(user_id, scope_id)` combinations in the index.

**Returns**: `list[tuple[str, str]]`.

## class SimpleMemoryIndex

```python
class openjiuwen.core.foundation.store.index.simple_memory_index.SimpleMemoryIndex(
    BaseMemoryIndex
)
```

A memory index implementation based on `BaseKVStore` + `BaseVectorStore`, **retained only for backward compatibility with data written by the legacy `SemanticStore + UserMemStore` architecture**. It writes memory metadata to KV storage and vectors to separate vector collections by `mem_type`, maintaining ID tracking between the two.

> **Deprecated**: This class exists solely for backward compatibility with legacy data and may be removed in future versions. Do not build new features or long-lived components on top of it; new scenarios should use the memory implementations under `openjiuwen.core.memory` (e.g., `LakeBaseMemoryProvider`).

Source code: `openjiuwen.core.foundation.store.index.simple_memory_index.SimpleMemoryIndex`.

### __init__

```python
def __init__(
    self,
    kv_store: BaseKVStore,
    vector_store: BaseVectorStore,
    embedding_model: Any = None,
)
```

Constructs a memory index instance, binding KV storage, vector storage, and an optional embedding model.

**Parameters**:

- `kv_store: BaseKVStore`: KV storage for memory metadata.
- `vector_store: BaseVectorStore`: Vector storage for vectors.
- `embedding_model: Any = None`: Embedding model for generating text vectors. Must provide `embed_documents(texts)` and `embed_query(text)` async methods. Can be passed at construction time or injected later via `set_embedding_model`; must be set before write/retrieval operations, otherwise `add_memories` raises `MEMORY_ADD_MEMORY_EXECUTION_ERROR` and `search` returns an empty list.

### set_embedding_model

```python
def set_embedding_model(self, embedding_model: Any) -> None
```

Deferred injection of the embedding model.

**Parameters**:

- `embedding_model: Any`: Embedding model instance.

### set_storage_codec

```python
def set_storage_codec(self, codec: StorageCodec) -> None
```

Sets the memory text encoder/decoder. Once set, writing to KV will call `codec.encode` on the `mem` field, and reading will call `codec.decode`.

**Parameters**:

- `codec: StorageCodec`: Encoder/decoder instance.

### Method Implementation Notes

The following methods are concrete implementations of the corresponding abstract methods in `BaseMemoryIndex`. Signatures match the base class; differences lie in internal behavior:

| Method | Implementation Notes |
|--------|---------------------|
| `add_memories` | Groups by `mem_type`: generates vectors using the embedding model, creates vector collections as needed, writes vectors; then writes metadata as JSON to KV (encrypting/decrypting the `mem` field if codec is set), and appends IDs to global and per-type ID tracking keys. Raises `MEMORY_ADD_MEMORY_EXECUTION_ERROR` if embedding is not initialized. |
| `search` | Generates query vector using the embedding model; filters by `mem_types` or enumerates all collections for the user+scope when unspecified; performs vector search on each collection to get hit IDs and scores, batch-retrieves metadata from KV and decodes, then returns the top `top_k` results sorted by score descending. Returns an empty list if embedding is not initialized. |
| `update_memories` | Deletes old memories by ID first, then calls `add_memories` to write new ones. |
| `delete_memories` | For each ID, retrieves `mem_type` from KV, deletes the KV entry and updates ID tracking; then deletes vectors by ID across all collections for that user+scope. |
| `delete_by_user` | Deletes KV entries by `UMD/{user_id}/` prefix and deletes all vector collections starting with `uid_{user_id}_gid_`. |
| `delete_by_scope` | Scans all keys under the `UMD/` prefix, matches `scope_id` at segment 3 for bulk deletion; also deletes vector collections with names containing `_gid_{scope_id}_mtype_`. |
| `delete_by_user_and_scope` | Deletes KV entries by `UMD/{user_id}/{scope_id}/` prefix and deletes all vector collections for that user+scope. |
| `get_by_id` | Retrieves a single metadata entry from KV and decodes it (including codec decryption), constructs and returns a `MemoryDoc`; returns `None` if not found. |
| `list_memories` | Retrieves all IDs from the global ID tracking key, batch-retrieves metadata from KV and decodes; filters by `mem_types`, sorts by type order and reverse timestamp when multiple types are specified, then returns paginated results. |
| `get_schema_version` / `update_schema_version` | Maintains a `_schema_version` integer within the instance. |
| `create_backup` | Generates a UUID backup ID and stores the current `schema_version` in an in-memory backup table. |
| `restore_backup` | Restores `schema_version` from the in-memory backup table; raises `ValueError` if the backup does not exist. |
| `cleanup_backup` | Removes the backup from the in-memory backup table. |
| `list_user_scopes` | Scans all keys under the `UMD/` prefix, parses `(user_id, scope_id)` from the `UMD/{user_id}/{scope_id}/...` pattern, and returns deduplicated results. |

## Implementation Details

### Legacy Data Layout

`SimpleMemoryIndex` operates on data written by the legacy `SemanticStore + UserMemStore` architecture:

- **KV Key**: `UMD/{user_id}/{scope_id}/{mem_id}`
- **KV Value**: JSON string containing `id`, `mem` (memory text, possibly encrypted by codec), `mem_type`, `timestamp`, `user_id`, `scope_id`, and arbitrary extension fields.
- **Vector Collection Name**: `uid_{user_id}_gid_{scope_id}_mtype_{mem_type}`, one collection per `(user, scope, type)` combination.
- **Vector Collection Schema**: `id` (`VARCHAR`, primary key, max length 256) + `embedding` (`FLOAT_VECTOR`), dynamic fields disabled.

### KV Key Naming Convention

| Purpose | Key Format | Description |
|---------|-----------|-------------|
| Single memory | `UMD/{user_id}/{scope_id}/{mem_id}` | Value is memory metadata JSON. |
| Global ID tracking | `UMD/{user_id}/{scope_id}/ids` | Value is concatenated fixed-length ID strings (24 chars per ID). |
| Per-type ID tracking | `UMD/{user_id}/{scope_id}/{mem_type}/ids` | Tracks IDs by memory type. |

The prefix `UMD` and separator `/` are defined by class constants `_KV_PREFIX` and `_KV_SEP`.

### ID Tracking

To support listing and type-based filtering, `SimpleMemoryIndex` maintains ID tracking keys in KV:

- **Fixed-length encoding**: Each memory ID occupies a fixed `_BYTE_NUM_PER_ID = 24` characters; `_parse_all_ids` splits the raw string by this length to obtain the ID list.
- **Append**: `_append_id` appends the ID to the end of the string with deduplication.
- **Remove**: `_remove_id` locates and deletes the target ID by fixed-length slicing; deletes the entire key if the string becomes empty after removal.
- **Tracking maintenance**: `add_memories` synchronously writes to both global and per-type tracking keys; `delete_memories` synchronously removes from them.

### Vector Collection Naming and Discovery

- Collection names are constructed by `_get_collection_name(user_id, scope_id, mem_type)` as `uid_{user_id}_gid_{scope_id}_mtype_{mem_type}`.
- `_parse_mem_type_from_collection(name)` reverse-parses `mem_type` from the collection name (taking the part after the last `_mtype_`).
- `_collections_for(user_id, scope_id)` lists all collections in vector storage prefixed with `uid_{user_id}_gid_{scope_id}_mtype_`, used for retrieval and deletion when `mem_types` is not explicitly specified.
- `_ensure_collection(name, dim)` creates a collection with vector dimension `dim` on first write, and caches within the instance using `_created_collections` to avoid redundant creation.

### Codec Integration

If a `StorageCodec` is set via `set_storage_codec`:

- `add_memories` calls `codec.encode` on the metadata's `mem` field before writing to KV;
- `search` / `get_by_id` / `list_memories` call `codec.decode` on the `mem` field after reading from KV to restore plaintext.

When no codec is set, the `mem` field is stored and retrieved in plaintext.
