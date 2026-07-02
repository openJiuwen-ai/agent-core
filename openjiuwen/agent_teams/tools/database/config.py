# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Database configuration classes."""

from enum import StrEnum

from pydantic import BaseModel


class DatabaseType(StrEnum):
    """Supported database types."""

    SQLITE = "sqlite"
    POSTGRESQL = "postgresql"
    MYSQL = "mysql"


class DatabaseConfig(BaseModel):
    """Database configuration class."""

    db_type: str = DatabaseType.SQLITE
    connection_string: str = ""
    # SQLite busy_timeout (seconds), passed through ``connect_args["timeout"]``:
    # how long a connection waits for the write lock before raising
    # ``database is locked``. Application-level write serialisation (see
    # ``DbSessions``) is the real in-process arbiter, so this only bounds
    # rare cross-process / WAL-checkpoint contention — keep it short.
    db_timeout: int = 5
    db_enable_wal: bool = True

    # ---- File-backed SQLite pool / PRAGMA tuning ----
    # Writes are serialised by an app-level lock (see ``DbSessions``), so a
    # file-backed SQLite db runs a dedicated single-writer engine plus a
    # separate reader pool. These knobs tune that split; all are ignored for
    # ``:memory:`` (one shared StaticPool connection) and for PostgreSQL /
    # MySQL (native concurrency, separate pool settings).
    #
    # Reader connection pool size. Readers run concurrently on WAL, so this
    # caps how many members can read at once without queuing on a checkout.
    read_pool_size: int = 8
    # Writer connection pool size. Writes serialise on the app lock, so a
    # small pool suffices — 2 leaves headroom for a DDL ``engine.begin`` while
    # a write session is checked out.
    write_pool_size: int = 2
    # Per-connection SQLite page cache (KiB, emitted as negative cache_size).
    # The single writer keeps a large cache (helps WAL checkpoints); each
    # reader keeps a small one so raising ``read_pool_size`` does not multiply
    # memory — the page cache is per-connection, not shared.
    write_cache_size_kb: int = 65536
    read_cache_size_kb: int = 8192
    # Memory-mapped I/O window (MiB) per connection; shared at the OS
    # page-cache level, so far less multiplicative than the private page cache.
    mmap_size_mb: int = 256
    # WAL auto-checkpoint threshold in pages. Larger means fewer (but bigger)
    # commit stalls under write-heavy load; SQLite's default is 1000.
    wal_autocheckpoint: int = 1000
