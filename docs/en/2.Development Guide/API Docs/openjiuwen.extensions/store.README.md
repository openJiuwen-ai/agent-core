# store.db

`openjiuwen.extensions.store.db` provides GaussDB-based relational database storage extension implementations, interacting with GaussDB databases through the `async_gaussdb` async driver.

**Modules**:

| MODULE | DESCRIPTION |
|---|---|
| [gauss_db_store](./store/gauss_db_store.md) | GaussDB-based database storage implementation, inheriting `BaseDbStore`, wrapping `AsyncEngine` for async database operations. |
