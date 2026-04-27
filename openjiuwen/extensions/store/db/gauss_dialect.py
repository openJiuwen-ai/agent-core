# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import datetime
import logging
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from typing import Any


def _patch_gaussdb_driver(driver_module):
    if not hasattr(driver_module, 'paramstyle'):
        driver_module.paramstyle = 'format'
    if not hasattr(driver_module, 'Error'):
        driver_module.Error = getattr(driver_module, 'GaussDBError', Exception)
    if not hasattr(driver_module, 'apilevel'):
        driver_module.apilevel = '2.0'
    if not hasattr(driver_module, 'threadsafety'):
        driver_module.threadsafety = 0
    return driver_module


try:
    from sqlalchemy import String
    from sqlalchemy.dialects.postgresql.asyncpg import PGDialect_asyncpg, AsyncAdapt_asyncpg_dbapi
    from sqlalchemy.dialects.postgresql.base import PGCompiler
    from sqlalchemy.dialects import registry

    class GaussCompiler(PGCompiler):
        def for_update_clause(self, select, **kw):
            return " FOR UPDATE"

    class GaussString(String):
        """自定义 String 类型，确保所有传入 String 列的数据在进入驱动前被转为字符串"""

        def bind_processor(self, dialect):
            parent_processor = super().bind_processor(dialect)

            def process(value):
                if value is None:
                    return None
                if not isinstance(value, str):
                    if isinstance(value, datetime.datetime):
                        value = value.strftime('%Y-%m-%d %H:%M:%S.%f')
                    else:
                        value = str(value)
                if parent_processor:
                    return parent_processor(value)
                return value

            return process

    class GaussDialectAsyncpg(PGDialect_asyncpg):
        statement_compiler = GaussCompiler
        name = 'gaussdb'
        driver = 'async_gaussdb'

        supports_statement_cache = True
        supports_native_enum = False
        supports_native_uuid = False
        use_insertmanyvalues = False

        colspecs = {
            **PGDialect_asyncpg.colspecs,
            String: GaussString,
        }

        @classmethod
        def import_dbapi(cls):
            try:
                import async_gaussdb
                patched_driver = _patch_gaussdb_driver(async_gaussdb)
                logger.info("[GaussDialect] Loaded async_gaussdb via import_dbapi")
                return AsyncAdapt_asyncpg_dbapi(patched_driver)
            except ImportError as import_error:
                raise ImportError(
                    "Please install async-gaussdb to use the gaussdb dialect. "
                    "Run: pip install openjiuwen[gaussdb] or pip install openjiuwen[all-storage]"
                ) from import_error

        def _get_server_version_info(self, connection):
            return (9, 2)

        def _domain_query(self, schema):
            from sqlalchemy import text
            return text("SELECT 1 WHERE FALSE")

        def _enum_query(self, schema):
            from sqlalchemy import text
            return text("SELECT 1 WHERE FALSE")

    registry.register("gaussdb.async_gaussdb", __name__, "GaussDialectAsyncpg")
    logger.info("[GaussDialect] Registered gaussdb.async_gaussdb dialect")

    registry.register("gaussdb", __name__, "GaussDialectAsyncpg")
    logger.info("[GaussDialect] Registered gaussdb dialect")

except ImportError as e:
    logger.warning(f"[GaussDialect] Failed to import SQLAlchemy PostgreSQL dialect: {e}")
    logger.warning("[GaussDialect] GaussDB dialect registration failed, dependencies may be missing")
