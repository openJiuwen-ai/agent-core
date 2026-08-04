# Developing Store-Backend Plugins

openJiuwen's store layer supports third-party backends via Python's standard entry_points mechanism — no core-code modification needed. This document covers how to publish a vector-store plugin and how KV / DB stores are integrated.

In addition, `openjiuwen.extensions.message_queue` provides a Pulsar message-queue adapter. It is independent of the store entry_points mechanism and is a thin wrapper around the Pulsar Python Client. The final section of this chapter describes its dependency on the backend service and the responsibility boundary.

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

## Message-Queue Backend: Pulsar

`openjiuwen.extensions.message_queue.message_queue_pulsar` adapts the SDK's `MessageQueueBase` interface to Apache Pulsar. It is responsible for:

- Creating and closing Pulsar clients, producers, and consumers
- Serializing, sending, and receiving `QueueMessage` objects
- Binding asynchronous message handlers to subscriptions and acknowledging successfully handled messages
- Reusing a thread pool for blocking Pulsar Python Client calls

This module is not an independent message-queue implementation and does not persist messages inside the SDK process. It requires a reachable Pulsar cluster. After a message reaches the broker, persistence, replication, retention, and consumer cursors are managed by the Pulsar backend.

### Installation and Connection

Install the Pulsar optional dependency:

```bash
pip install "openjiuwen[pulsar]"
```

To create the adapter, provide the Pulsar broker URL and the number of worker threads used for blocking client calls:

```python
from openjiuwen.core.runner.runner_config import PulsarConfig
from openjiuwen.extensions.message_queue.message_queue_pulsar import MessageQueuePulsar

mq = MessageQueuePulsar(
    PulsarConfig(
        url="pulsar://localhost:6650",
        max_workers=8,
    )
)
mq.start()
```

On application shutdown, call `await mq.stop()` to close subscriptions, producers, the client, and the thread pool. `stop(drain_timeout=...)` can wait for the current consume loop to drain before stopping, but it is not a message-retry or dead-letter mechanism.

### Persistence, Retry, and Dead-Letter Responsibilities

| Capability | Owner | SDK Adapter Behavior |
|---|---|---|
| Message persistence, replication, and retention | Pulsar broker and its storage backend | Does not persist locally; only sends through a producer |
| Redelivery of unacknowledged messages | Pulsar subscription and broker policies | Acknowledges successful handling; does not implement a separate retry queue or scheduler |
| Dead-letter queue (DLQ) | Pulsar consumer/backend policy | Does not create a dead-letter topic or configure `DeadLetterPolicy` |
| SDK startup retry | `MessageQueuePulsar.start_with_retry()` | Retries client initialization only; it can fall back to in-process `FakeMQ` after all attempts fail |

If an application requires message redelivery or dead-letter handling, configure those mechanisms in the Pulsar deployment and consumer policy, together with appropriate retention, acknowledgement timeout, maximum-redelivery, dead-letter-topic, and monitoring settings. The `max_retries` argument of `start_with_retry()` controls connection-initialization attempts only; it does not guarantee redelivery of an application message.

> **Note:** `FakeMQ` is an optional local fallback when Pulsar is unavailable. It does not provide cross-process sharing or the persistence, redelivery, replication, and dead-letter guarantees supplied by Pulsar. For production workloads that require reliable delivery, decide explicitly whether fallback is acceptable and set `fallback_to_fake=False` when it is not.

## Compatibility

`Base*Store` ABCs are treated as stable public APIs. Breaking changes are announced at least one minor release in advance. openJiuwen is currently in the 0.1.x series; pin your plugin to the actual release that contains entry_points support:

```toml
dependencies = ["openjiuwen>=0.1.11,<0.2"]
```
