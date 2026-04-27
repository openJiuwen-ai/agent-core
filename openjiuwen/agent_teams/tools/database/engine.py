# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Database engine initialization, session management, and table lifecycle."""

import time
from pathlib import Path

from sqlalchemy import event, inspect
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import AsyncAdaptedQueuePool, StaticPool
from sqlmodel import SQLModel

from openjiuwen.agent_teams.spawn.context import get_session_id
from openjiuwen.agent_teams.tools.database.config import DatabaseConfig, DatabaseType
from openjiuwen.agent_teams.tools.models import (
    TEAM_DYNAMIC_TABLE_PREFIXES,
    TEAM_STATIC_TABLES_TO_CLEAR,
    _clear_session_model_cache,
    _get_message_model,
    _get_message_read_status_model,
    _get_task_dependency_model,
    _get_task_model,
)
from openjiuwen.core.common.logging import team_logger


def get_current_time() -> int:
    """Return current time in milliseconds."""
    return int(round(time.time() * 1000))


def _get_table_names(sync_conn) -> list[str]:
    """Return all table names currently present in the database."""
    return list(inspect(sync_conn).get_table_names())


def _drop_table(sync_conn, table_name: str) -> None:
    """Drop one table with raw SQL to avoid reflection-order issues."""
    quoted_name = table_name.replace('"', '""')
    sync_conn.exec_driver_sql(f'DROP TABLE IF EXISTS "{quoted_name}"')


def _clear_table(sync_conn, table_name: str) -> None:
    """Delete all rows from one reflected table."""
    quoted_name = table_name.replace('"', '""')
    sync_conn.exec_driver_sql(f'DELETE FROM "{quoted_name}"')


async def initialize_engine(
    config: DatabaseConfig,
) -> tuple[AsyncEngine, async_sessionmaker]:
    """Create and configure the async engine and session factory.

    Args:
        config: Database configuration.

    Returns:
        Tuple of (engine, session_factory).
    """
    db_type = config.db_type
    engine: AsyncEngine

    if db_type == DatabaseType.SQLITE:
        conn_str = config.connection_string
        in_memory = conn_str == ":memory:"
        if not in_memory:
            db_path = Path(conn_str).expanduser()
            conn_str = str(db_path)
            if not db_path.parent.exists():
                db_path.parent.mkdir(parents=True, exist_ok=True)

        if in_memory:
            engine = create_async_engine(
                "sqlite+aiosqlite:///:memory:",
                echo=False,
                future=True,
                poolclass=StaticPool,
                connect_args={"check_same_thread": False},
            )
        else:
            engine = create_async_engine(
                f"sqlite+aiosqlite:///{conn_str}",
                echo=False,
                future=True,
                poolclass=AsyncAdaptedQueuePool,
                pool_size=1,
                max_overflow=0,
                pool_pre_ping=False,
                connect_args={
                    "timeout": config.db_timeout,
                    "check_same_thread": False,
                },
            )

        @event.listens_for(engine.sync_engine, "connect")
        def set_sqlite_pragma(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        if config.db_enable_wal and not in_memory:

            @event.listens_for(engine.sync_engine, "first_connect")
            def set_sqlite_wal(dbapi_connection, connection_record):
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.close()

    elif db_type == DatabaseType.POSTGRESQL:
        conn_str = config.connection_string.strip()
        if not conn_str:
            raise ValueError("PostgreSQL requires a non-empty connection_string")
        if conn_str.startswith("postgres://"):
            conn_str = f"postgresql://{conn_str.removeprefix('postgres://')}"
        if conn_str.startswith("postgresql://"):
            conn_str = f"postgresql+asyncpg://{conn_str.removeprefix('postgresql://')}"
        if not conn_str.startswith("postgresql+asyncpg://"):
            raise ValueError("PostgreSQL connection_string must use postgresql+asyncpg:// scheme")

        engine = create_async_engine(
            conn_str,
            echo=False,
            future=True,
            pool_size=10,
            max_overflow=20,
            pool_pre_ping=True,
            pool_recycle=1800,
        )

    elif db_type == DatabaseType.MYSQL:
        conn_str = config.connection_string.strip()
        if not conn_str:
            raise ValueError("MySQL requires a non-empty connection_string")
        if conn_str.startswith("mysql://"):
            conn_str = f"mysql+aiomysql://{conn_str.removeprefix('mysql://')}"
        elif not conn_str.startswith("mysql+aiomysql://"):
            raise ValueError("MySQL connection_string must use mysql:// or mysql+aiomysql:// scheme")

        engine = create_async_engine(
            conn_str,
            echo=False,
            future=True,
            pool_size=10,
            max_overflow=20,
            pool_pre_ping=True,
            pool_recycle=1800,
        )
    else:
        raise NotImplementedError(f"Database type {config.db_type} not yet implemented")

    session_local = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )

    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    return engine, session_local


async def create_cur_session_tables(engine: AsyncEngine) -> None:
    """Create dynamic tables for current session.

    The session_id is obtained from context via get_session_id().
    """
    if engine is None:
        return

    session_id = get_session_id()
    if not session_id:
        team_logger.warning("No session_id in context, cannot create session tables")
        return

    task_model = _get_task_model()
    dep_model = _get_task_dependency_model()
    message_model = _get_message_model()
    read_status_model = _get_message_read_status_model()

    async with engine.begin() as conn:
        for model in (task_model, dep_model, message_model, read_status_model):
            await conn.run_sync(model.__table__.create, checkfirst=True)

    team_logger.info(f"Session tables ready for session {session_id}")


async def drop_cur_session_tables(engine: AsyncEngine) -> None:
    """Drop dynamic tables for current session.

    The session_id is obtained from context via get_session_id().
    """
    if engine is None:
        return

    session_id = get_session_id()
    if not session_id:
        team_logger.warning("No session_id in context, cannot drop session tables")
        return

    task_model = _get_task_model()
    dep_model = _get_task_dependency_model()
    message_model = _get_message_model()
    read_status_model = _get_message_read_status_model()

    async with engine.begin() as conn:
        for model in (task_model, dep_model, message_model, read_status_model):
            await conn.run_sync(model.__table__.drop, checkfirst=True)

    for model in (task_model, dep_model, message_model, read_status_model):
        SQLModel.metadata.remove(model.__table__)

    _clear_session_model_cache(session_id)

    team_logger.info(f"Dropped dynamic tables for session {session_id}")


async def cleanup_all_runtime_state(
    engine: AsyncEngine,
) -> tuple[list[str], list[str]]:
    """Delete all dynamic team tables and clear static team tables.

    This cleanup is storage-level and does not depend on an active
    agent instance or the current session context.
    """
    if engine is None:
        return [], []

    deleted_tables: list[str] = []
    cleared_tables: list[str] = []
    async with engine.begin() as conn:
        table_names = await conn.run_sync(_get_table_names)

        for table_name in table_names:
            if not table_name.startswith(TEAM_DYNAMIC_TABLE_PREFIXES):
                continue
            await conn.run_sync(_drop_table, table_name)
            deleted_tables.append(table_name)

        for table_name in TEAM_STATIC_TABLES_TO_CLEAR:
            if table_name not in table_names:
                continue
            await conn.run_sync(_clear_table, table_name)
            cleared_tables.append(table_name)

    team_logger.info(
        "Cleaned team runtime state: deleted dynamic tables={}, cleared static tables={}",
        deleted_tables,
        cleared_tables,
    )
    return deleted_tables, cleared_tables
