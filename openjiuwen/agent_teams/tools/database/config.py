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
