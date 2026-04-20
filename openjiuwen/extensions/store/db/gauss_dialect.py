# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import logging
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from typing import Any


def _patch_gaussdb_driver(driver_module):
    """Patch async_gaussdb module with required DBAPI attributes.

    Args:
        driver_module: The async_gaussdb module to patch.

    Returns:
        The patched driver module.
    """
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
    from sqlalchemy.dialects.postgresql.asyncpg import PGDialect_asyncpg, AsyncAdapt_asyncpg_dbapi
    from sqlalchemy.dialects.postgresql.base import PGCompiler
    from sqlalchemy.dialects import registry

    class GaussCompiler(PGCompiler):
        def for_update_clause(self, select, **kw):
            return " FOR UPDATE"

    class GaussDialectAsyncpg(PGDialect_asyncpg):
        """GaussDB async dialect using async_gaussdb driver via import_dbapi."""

        statement_compiler = GaussCompiler
        name = 'gaussdb'
        driver = 'async_gaussdb'

        @classmethod
        def import_dbapi(cls):
            """Load and return async_gaussdb DBAPI module wrapped in AsyncAdapt_asyncpg_dbapi.

            This method is called by SQLAlchemy to get the underlying DBAPI driver.
            We load async_gaussdb and wrap it in SQLAlchemy's asyncpg adapter.

            Returns:
                AsyncAdapt_asyncpg_dbapi wrapper around async_gaussdb.

            Raises:
                ImportError: If async_gaussdb is not installed.
            """
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

        def get_columns(self, connection, table_name, schema=None, **kw):
            return []

    registry.register("gaussdb.async_gaussdb", __name__, "GaussDialectAsyncpg")
    logger.info("[GaussDialect] Registered gaussdb.async_gaussdb dialect")

    registry.register("gaussdb", __name__, "GaussDialectAsyncpg")
    logger.info("[GaussDialect] Registered gaussdb dialect")

except ImportError as e:
    logger.warning(f"[GaussDialect] Failed to import SQLAlchemy PostgreSQL dialect: {e}")
    logger.warning("[GaussDialect] GaussDB dialect registration failed, dependencies may be missing")
