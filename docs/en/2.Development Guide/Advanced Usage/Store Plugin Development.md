# Developing Store-Backend Plugins

openJiuwen's store layer supports third-party backends via Python's standard entry_points mechanism — no core-code modification needed. This document covers how to publish a vector-store plugin and how KV / DB stores are integrated.

## Concepts

| Type | ABC | Factory | Integration |
|------|-----|---------|-------------|
| Vector | `BaseVectorStore` | `create_vector_store(name, **kwargs)` | entry_points or explicit `register_vector_store()` |
| KV | `BaseKVStore` | none | Direct `from X import Y` + instantiate |
| DB | `BaseDbStore` | none | Direct `from X import Y` + instantiate |

Vector stores have a factory because higher-level components (e.g. KnowledgeRetrieval) create them by name. KV / DB stores are typically application-owned components; no name-based lookup is needed.

## Writing a Vector-Store Plugin

### 1. Subclass BaseVectorStore

```python
# my_package/my_vector_store.py
from openjiuwen.core.foundation.store.base_vector_store import (
    BaseVectorStore, CollectionSchema, VectorSearchResult,
)

class MyVectorStore(BaseVectorStore):
    def __init__(self, connection_uri: str, **kwargs):
        self._uri = connection_uri

    async def create_collection(self, collection_name, schema, **kwargs):
        ...  # Implement all abstract methods
```

Full interface: `openjiuwen/core/foundation/store/base_vector_store.py`. Implement every `@abstractmethod`.

### 2. Declare entry_point in pyproject.toml

```toml
[project]
name = "my-openjiuwen-vector"
dependencies = ["openjiuwen>=0.1.11,<0.2"]

[project.entry-points."openjiuwen.vector_stores"]
my_backend = "my_package.my_vector_store:MyVectorStore"
```

Entry-point format: `name = "module.path:ClassName"`. The `name` is the string users pass to `create_vector_store(name, ...)`.

### 3. Publish to PyPI

```bash
python -m build
twine upload dist/*
```

### 4. User Side

```bash
pip install openjiuwen my-openjiuwen-vector
```

```python
from openjiuwen.core.foundation.store import create_vector_store
store = create_vector_store("my_backend", connection_uri="...")
```

## Explicit Registration (private backends)

If you don't plan to publish to PyPI, register at app startup:

```python
from openjiuwen.core.foundation.store import register_vector_store
from my_private_pkg.backend import PrivateBackend

register_vector_store("private", PrivateBackend)
# Now create_vector_store("private", ...) works
```

## Name Collision

Resolution order is **built-in → explicit registrations → entry_points**. Built-in names (`chroma` / `milvus` / `gaussvector`) cannot be overridden — plugins that claim those names are silently ignored in favor of the built-in.

## Error Handling

- Plugin `load()` fails: logged at WARNING, `create_vector_store` returns `None`, no exception.
- Plugin constructor raises: logged at WARNING, returns `None`.
- A broken plugin never crashes the factory for the whole application.

## KV / DB Plugins

KV / DB have no factory. Pattern:

```python
# my_package/my_kv_store.py
from openjiuwen.core.foundation.store.base_kv_store import BaseKVStore

class MyKVStore(BaseKVStore):
    async def set(self, key, value): ...
    async def get(self, key): ...
    # ... other abstract methods
```

User side imports directly:

```python
from my_package.my_kv_store import MyKVStore
kv = MyKVStore(...)
long_term_memory.register_store(kv_store=kv, ...)
```

## Compatibility

`Base*Store` ABCs are treated as stable public APIs. Breaking changes are announced at least one minor release in advance. openJiuwen is currently in the 0.1.x series; pin your plugin to the actual release that contains entry_points support:

```toml
dependencies = ["openjiuwen>=0.1.11,<0.2"]
```
