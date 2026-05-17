# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import json
import hashlib
from typing import Any, Dict, List, Optional, Tuple, Union
from datetime import datetime, timezone

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.foundation.llm.schema.message import BaseMessage
from openjiuwen.core.foundation.store.base_message_store import (
    BaseMessageStore, MessageMetadata,
)
from openjiuwen.core.memory.manage.index.base_memory_manager import BaseMemoryManager
from openjiuwen.core.memory.migration.migrator.memory_meta_manager import MemoryMetaManager

DEFAULT_TABLE_NAME = "user_message"
COUNT_QUERY_LIMIT = 1000000


class SqlMessageStore(BaseMessageStore):
    """
    SQL database message storage implementation
    """
    def __init__(self,
        crypto_key: Optional[bytes] = None,
        sql_db_store: object = None,
        table_name: str = DEFAULT_TABLE_NAME):
        """
        Initialize SQL message storage

        Args:
            crypto_key: Encryption key (optional)
            sql_db_store: Existing SqlDbStore instance
            table_name: Message table name
        """
        self.crypto_key = crypto_key
        self.sql_db_store = sql_db_store
        self.table_name = table_name

    def _generate_message_id(self, message: BaseMessage, timestamp: datetime) -> str:
        content_str = json.dumps(message.content, ensure_ascii=False)
        message_hash = hashlib.sha256(f"{content_str}{timestamp}".encode()).hexdigest()
        return f"msg_{message_hash[:16]}_{int(timestamp.timestamp()*1000)}"
    
    async def add_message(self, message_add: Dict[str, Any]) -> str:
        """
        Add a single message

        Args:
            message_add: Dict containing message data

        Returns:
            str: The generated message ID
        """
        message: BaseMessage = message_add['message']
        user_id: str = message_add.get('user_id', '')
        scope_id: str = message_add.get('scope_id', '')
        session_id: str = message_add.get('session_id', '')
        timestamp: datetime = message_add.get('timestamp') or datetime.now(timezone.utc).astimezone()

        message_id = self._generate_message_id(message, timestamp)
        
        content = BaseMemoryManager.encrypt_memory_if_needed(self.crypto_key, message.content)
        
        data = {
            'message_id': message_id,
            'user_id': user_id or '',
            'session_id': session_id or '',
            'scope_id': scope_id or '',
            'role': getattr(message, 'role', '') or '',
            'content': content,
            'timestamp': timestamp
        }

        await self.sql_db_store.write(self.table_name, data)
        
        return message_id
    
    async def add_messages(self, message_adds: List[Dict[str, Any]]) -> List[str]:
        """
        Batch add messages

        Args:
            message_adds: List of dicts containing message data

        Returns:
            List[str]: List of generated message IDs
        """
        message_ids = []
        for message_add in message_adds:
            message_id = await self.add_message(message_add)
            message_ids.append(message_id)

        return message_ids
    
    async def get_message_by_id(self, message_id: str) -> Tuple[BaseMessage, MessageMetadata]:
        """
        Get message by message ID
        
        Args:
            message_id: Message ID
            
        Returns:
            Tuple[BaseMessage, MessageMetadata]: (message object, message metadata) tuple
            
        Raises:
            BaseError: When message does not exist
        """
        filters = {'message_id': [message_id]}
        messages = await self.sql_db_store.condition_get(table=self.table_name, conditions=filters)
        
        if not messages:
            raise build_error(
                StatusCode.MEMORY_GET_MEMORY_EXECUTION_ERROR,
                reason=f"Message with id {message_id} not found")
        
        message_data = messages[0]
        
        content = BaseMemoryManager.decrypt_memory_if_needed(self.crypto_key, message_data['content'])
        
        base_msg = BaseMessage(
            content=content,
            role=message_data.get('role', '')
        )
        
        metadata = MessageMetadata(
            message_id=message_data['message_id'],
            user_id=message_data['user_id'],
            scope_id=message_data['scope_id'],
            session_id=message_data['session_id'],
            timestamp=message_data['timestamp'],
            message_type=message_data.get('role', '')
        )
        
        return base_msg, metadata
    
    async def get_messages(
        self,
        message_filter: Dict[str, Any],
        limit: int = 10,
        order_by: str = "timestamp",
        order_direction: str = "desc",
    ) -> List[Tuple[BaseMessage, MessageMetadata]]:
        """
        Get messages by filter with pagination

        Args:
            message_filter: Dict with filter conditions
            limit: Maximum number of results
            order_by: Field to sort by
            order_direction: Sort direction ("asc" or "desc")

        Returns:
            List[Tuple[BaseMessage, MessageMetadata]]: List of (message object, message metadata) tuples
        """
        filters = {}
        if message_filter.get('user_id'):
            filters['user_id'] = message_filter['user_id']
        if message_filter.get('scope_id'):
            filters['scope_id'] = message_filter['scope_id']
        if message_filter.get('session_id'):
            filters['session_id'] = message_filter['session_id']

        messages = await self.sql_db_store.get_with_sort(
            table=self.table_name,
            filters=filters,
            sort_by=order_by,
            order=order_direction.upper(),
            limit=limit
        )
        
        result = []
        for message_data in messages:
            content = BaseMemoryManager.decrypt_memory_if_needed(self.crypto_key, message_data['content'])
            
            base_msg = BaseMessage(
                content=content,
                role=message_data.get('role', '')
            )
            
            metadata = MessageMetadata(
                message_id=message_data['message_id'],
                user_id=message_data['user_id'],
                scope_id=message_data['scope_id'],
                session_id=message_data['session_id'],
                timestamp=message_data['timestamp'],
                message_type=message_data.get('role', '')
            )
            
            result.append((base_msg, metadata))
        
        return result
    
    async def update_message(self, message_id: str, content: Union[str, List[Union[str, dict]]]) -> bool:
        """
        Update message content
        
        Args:
            message_id: Message ID
            content: New message content
            
        Returns:
            bool: Whether the update was successful
        """
        encrypted_content = BaseMemoryManager.encrypt_memory_if_needed(self.crypto_key, content)
        
        conditions = {'message_id': message_id}
        data = {'content': encrypted_content}
        
        return await self.sql_db_store.update(self.table_name, conditions, data)
    
    async def delete_message_by_id(self, message_id: str) -> bool:
        """
        Delete a single message by message ID
        
        Args:
            message_id: Message ID
            
        Returns:
            bool: Whether the deletion was successful
        """
        conditions = {'message_id': message_id}
        return await self.sql_db_store.delete(self.table_name, conditions)
    
    async def delete_messages(self, message_filter: Dict[str, Any]) -> int:
        """
        Delete messages matching the filter

        Args:
            message_filter: Dict with filter conditions

        Returns:
            int: Number of messages deleted
        """
        conditions = {}
        if message_filter.get('user_id'):
            conditions['user_id'] = message_filter['user_id']
        if message_filter.get('scope_id'):
            conditions['scope_id'] = message_filter['scope_id']
        if message_filter.get('session_id'):
            conditions['session_id'] = message_filter['session_id']

        count = await self.count_messages(message_filter)

        await self.sql_db_store.delete(self.table_name, conditions)

        return count

    async def count_messages(self, message_filter: Dict[str, Any]) -> int:
        """
        Count messages matching the filter

        Args:
            message_filter: Dict with filter conditions

        Returns:
            int: Number of messages
        """
        filters = {}
        if message_filter.get('user_id'):
            filters['user_id'] = message_filter['user_id']
        if message_filter.get('scope_id'):
            filters['scope_id'] = message_filter['scope_id']
        if message_filter.get('session_id'):
            filters['session_id'] = message_filter['session_id']

        messages = await self.sql_db_store.get_with_sort(
            table=self.table_name,
            filters=filters,
            sort_by="timestamp",
            order="ASC",
            limit=COUNT_QUERY_LIMIT
        )

        return len(messages)

    async def get_schema_version(self) -> int | None:
        """
        Get the current schema version of the message store.

        Returns:
            int | None: Current version number or None if not set
        """
        meta_manager = MemoryMetaManager(self.sql_db_store)
        result = await meta_manager.get_by_table_name(self.table_name)
        if result and len(result) > 0:
            version_str = result[0].get('schema_version')
            if version_str:
                return int(version_str)
        return None

    async def set_schema_version(self, version: int) -> None:
        """
        Set the schema version of the message store.

        Args:
            version: New version number to store
        """
        meta_manager = MemoryMetaManager(self.sql_db_store)
        await meta_manager.add(self.table_name, str(version))
