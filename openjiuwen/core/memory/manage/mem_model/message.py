# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from sqlalchemy import inspect, Column, String
from sqlalchemy.orm import declarative_mixin, declarative_base
from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.store.base_db_store import BaseDbStore


Base = declarative_base()


@declarative_mixin
class MessageMixin:
    message_id = Column(String(64), primary_key=True)
    user_id = Column(String(64), nullable=False)
    scope_id = Column(String(64), nullable=False)
    content = Column(String(4096), nullable=False)
    session_id = Column(String(64), nullable=True)
    role = Column(String(32), nullable=True)
    timestamp = Column(String(32), nullable=True)


@declarative_mixin
class ScopeUserMixin:
    user_id = Column(String(64), nullable=False, primary_key=True)
    scope_id = Column(String(64), nullable=False, primary_key=True)


class UserMessage(MessageMixin, Base):
    __tablename__ = "user_message"


class ScopeUserMapping(ScopeUserMixin, Base):
    __tablename__ = "scope_user_mapping"


async def create_tables(
    db_store: BaseDbStore,
):
    async with db_store.get_async_engine().begin() as conn:
        def check_and_create(sync_conn):
            inspector = inspect(sync_conn)
            table_name = UserMessage.__tablename__

            if inspector.has_table(table_name):
                columns = inspector.get_columns(table_name)
                column_names = [col['name'] for col in columns]

                if 'group_id' in column_names:
                    UserMessage.__table__.drop(sync_conn, checkfirst=True)
                    logger.debug(f"delete old version sql table")

            Base.metadata.create_all(
                sync_conn,
                tables=[
                    UserMessage.__table__,
                    ScopeUserMapping.__table__
                ],
                checkfirst=True
            )

        await conn.run_sync(check_and_create)
