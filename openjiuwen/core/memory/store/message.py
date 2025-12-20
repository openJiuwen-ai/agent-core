#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from sqlalchemy import Column, String
from sqlalchemy.orm import declarative_mixin, declarative_base
from openjiuwen.core.memory.store.base_db_store import BaseDbStore


Base = declarative_base()


@declarative_mixin
class MessageMixin:
    message_id = Column(String(64), primary_key=True)
    user_id = Column(String(64), nullable=False)
    group_id = Column(String(64), nullable=False)
    content = Column(String(4096), nullable=False)
    session_id = Column(String(64), nullable=True)
    role = Column(String(32), nullable=True)
    timestamp = Column(String(32), nullable=True)


class UserMessage(MessageMixin, Base):
    __tablename__ = "user_message"


async def create_tables(
    db_store: BaseDbStore,
):
    # MySQL table
    async with db_store.get_async_engine().begin() as conn:
        await conn.run_sync(
            lambda sync_conn: Base.metadata.create_all(
                sync_conn,
                tables=[UserMessage.__table__],
                checkfirst=True
            )
        )
