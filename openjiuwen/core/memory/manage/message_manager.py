# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from datetime import datetime, timezone
from typing import Dict, Any, Tuple, Optional

from pydantic import BaseModel, Field

from openjiuwen.core.memory.manage.base_memory_manager import BaseMemoryManager
from openjiuwen.core.memory.manage.data_id_manager import DataIdManager
from openjiuwen.core.memory.store.sql_db_store import SqlDbStore
from openjiuwen.core.foundation.llm import BaseMessage


class MessageAddRequest(BaseModel):
    user_id: Optional[str] = None
    group_id: Optional[str] = None
    content: Optional[str] = None
    role: Optional[str] = None
    session_id: Optional[str] = None
    timestamp: Optional[datetime] = Field(default_factory=lambda: datetime.now(timezone.utc))

## DB-Based Message Management


class MessageManager:
    def __init__(self,
                 sql_db_store: SqlDbStore,
                 data_id_manager: DataIdManager,
                 crypto_key: bytes):
        self.sql_db = sql_db_store
        self.message_table = "user_message"
        self.data_id = data_id_manager
        self.crypto_key = crypto_key

    async def add(self, req: MessageAddRequest) -> str:
        if req.user_id is None:
            raise ValueError('Must provide user_id')
        if req.group_id is None:
            raise ValueError('Must provide group_id')
        if req.content is None:
            raise ValueError('Must provide content')
        message_id = str(await self.data_id.generate_next_id(user_id=req.user_id))
        time = datetime.now(timezone.utc) if not req.timestamp else req.timestamp
        req.content = BaseMemoryManager.encrypt_memory_if_needed(self.crypto_key, req.content)
        data = {
            'message_id': message_id,
            'user_id': req.user_id or '',
            'session_id': req.session_id or '',
            'group_id': req.group_id or '',
            'role': req.role or '',
            'content': req.content,
            'timestamp': time
        }
        await self.sql_db.write(self.message_table, data)
        return message_id

    async def get(self, user_id: str = None, group_id: str = None, session_id: str = None,
                  message_len: int = 10) -> list[Tuple[BaseMessage, datetime]]:
        filters: Dict[str, Any] = {}
        if user_id is not None:
            filters['user_id'] = user_id
        if group_id is not None:
            filters['group_id'] = group_id
        if session_id is not None:
            filters['session_id'] = session_id
        if message_len <= 0:
            raise ValueError('message_len Must bigger than zero')
        messages = await self.sql_db.get_with_sort(table=self.message_table, filters=filters, order="DESC",
                                                   limit=message_len)
        result = []
        for message in reversed(messages):
            base_msg = BaseMessage(**message)
            base_msg.content = BaseMemoryManager.decrypt_memory_if_needed(
                key=self.crypto_key,
                ciphertext=base_msg.content)
            result.append((base_msg, message['timestamp']))
        return result

    async def get_by_id(self, msg_id: str) -> Tuple[BaseMessage, datetime] | None:
        filters: Dict[str, Any] = {'message_id': [msg_id]}
        messages = await self.sql_db.condition_get(table=self.message_table, conditions=filters)
        if not messages:
            return None
        base_msg, msg_datetime = BaseMessage(**messages[0]), messages[0]['timestamp']
        base_msg.content = BaseMemoryManager.decrypt_memory_if_needed(
            key=self.crypto_key,
            ciphertext=base_msg.content)
        return base_msg, msg_datetime
