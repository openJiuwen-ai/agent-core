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
    db_timeout: int = 30
    db_enable_wal: bool = True
