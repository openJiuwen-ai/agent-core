# store

`openjiuwen.extensions.store` provides optional storage extension implementations, including vector stores and relational database stores, supporting integrations with Elasticsearch and GaussDB.

## store.vector

`openjiuwen.extensions.store.vector` provides Elasticsearch-based vector storage extension implementations, using Elasticsearch's `dense_vector` field type and k-NN search for vector similarity search capabilities.

**Modules**:

| MODULE | DESCRIPTION |
|---|---|
| [es_vector_store](./store/es_vector_store.md) | Elasticsearch-based vector storage implementation, inheriting `BaseVectorStore`, using `AsyncElasticsearch` for vector CRUD and k-NN similarity search. |

## store.db

`openjiuwen.extensions.store.db` provides GaussDB-based relational database storage extension implementations, interacting with GaussDB databases through the `async_gaussdb` async driver.

**Modules**:

| MODULE | DESCRIPTION |
|---|---|
| [gauss_db_store](./store/gauss_db_store.md) | GaussDB-based database storage implementation, inheriting `BaseDbStore`, wrapping `AsyncEngine` for async database operations. |
