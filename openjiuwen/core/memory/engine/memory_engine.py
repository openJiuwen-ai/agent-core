# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import Any, Tuple

from openjiuwen.core.common.logging import logger
from openjiuwen.core.component.common.configs.model_config import ModelConfig
from openjiuwen.core.memory.config.config import SysMemConfig, MemoryConfig
from openjiuwen.core.memory.generation.generation import Generator
from openjiuwen.core.memory.manage.data_id_manager import DataIdManager
from openjiuwen.core.memory.manage.message_manager import MessageManager
from openjiuwen.core.memory.manage.user_profile_manager import UserProfileManager
from openjiuwen.core.memory.manage.variable_manager import VariableManager
from openjiuwen.core.memory.manage.write_manager import WriteManager
from openjiuwen.core.memory.mem_unit.memory_unit import BaseMemoryUnit, MemoryType
from openjiuwen.core.memory.search.search_manager.search_manager import SearchManager
from openjiuwen.core.memory.store.base_db_store import BaseDbStore
from openjiuwen.core.memory.store.base_kv_store import BaseKVStore
from openjiuwen.core.memory.store.base_semantic_store import BaseSemanticStore
from openjiuwen.core.memory.store.message import create_tables
from openjiuwen.core.memory.store.sql_db_store import SqlDbStore
from openjiuwen.core.memory.store.user_mem_store import UserMemStore
from openjiuwen.core.utils.llm.base import BaseModelClient
from openjiuwen.core.utils.llm.messages import BaseMessage, HumanMessage
from openjiuwen.core.utils.llm.model_utils.model_factory import ModelFactory
from openjiuwen.core.memory.common.distributed_lock import DistributedLock


class BaseMemoryEngine(ABC):
    """
    Abstract base class for memory engine.

    Defines the core interface for memory storage and retrieval operations.
    Provides unified memory management functionality including conversation memory,
    user variables, semantic search, and persistence.

    Concrete implementations should handle memory operations across multiple storage
    backends (KV store, semantic store, database store).
    """

    def __init__(self, config: SysMemConfig, kv_store: BaseKVStore, semantic_store: BaseSemanticStore,
                 db_store: BaseDbStore, **kwargs):
        """
        Initialize the memory engine with required storage components.

        Args:
            config: System memory configuration parameters
            kv_store: Key-value store for fast structured data access
            semantic_store: Semantic storage for vector-based similarity search
            db_store: Database store for persistent data storage
            **kwargs: Additional keyword arguments
        """
        pass

    @abstractmethod
    def init_base_llm(self, llm_config: ModelConfig) -> bool:
        """
        Initialize the default LLM for memory processing tasks.

        Args:
            llm_config: Language model client configuration
        Returns:
            True if initialization succeeded
        """
        pass

    @abstractmethod
    def set_group_config(self, group_id: str, config: MemoryConfig) -> bool:
        """
        Set memory configuration for a specific group.

        Allows different groups to have customized memory retention policies,
        storage limits, and behavior settings.

        Args:
            group_id: Unique identifier for the group
            config: Group-specific memory configuration
        Returns:
            True if setting succeeded
        """
        pass

    @abstractmethod
    def set_group_llm_config(self, group_id: str, llm_config: ModelConfig) -> bool:
        """
        Set a dedicated LLM for a specific group.

        Enables groups to use different language models tailored to their
        specific needs or domains.

        Args:
            group_id: Unique identifier for the group
            llm_config: Language model client instance configuration
        Returns:
            True if setting succeeded
        """
        pass

    @abstractmethod
    def set_group_llm(self, group_id: str, model_name: str, llm: BaseModelClient) -> bool:
        """
        Set a dedicated LLM for a specific group.

        Enables groups to use different language models tailored to their
        specific needs or domains.

        Args:
            group_id: Unique identifier for the group
            model_name: LLM model identifier for this group
            llm: Language model client instance
        Returns:
            True if setting succeeded
        """
        pass

    @abstractmethod
    async def add_conversation_messages(
            self,
            user_id: str,
            group_id: str,
            messages: list[BaseMessage],
            timestamp: datetime | None = None,
            session_id: str | None = None
    ) -> str | None:
        """
        Add conversation messages to memory storage.

        Stores conversation history with metadata for future retrieval and context.
        May generate embeddings for semantic search capabilities.

        Args:
            user_id: Unique identifier for the user
            group_id: Unique identifier for the group/chat
            messages: List of message objects to store
            timestamp: When the messages were created
            session_id: Optional session identifier for grouping related messages

        Returns:
            Memory ID if successful, "-1" if failed, None when not record message
        """
        pass

    @abstractmethod
    async def get_message_by_id(self, msg_id: str) -> Tuple[BaseMessage, datetime] | None:
        """
        Retrieve a specific message by its unique identifier.

        Args:
            msg_id: Unique identifier of the message to retrieve

        Returns:
            Tuple of (message object, creation timestamp), None if message not found
        """
        pass

    @abstractmethod
    async def delete_mem_by_id(self, user_id: str, group_id: str, mem_id: str) -> bool:
        """
        Delete a specific memory entry(messages, variables, profiles)  by its ID.

        Args:
            user_id: Unique identifier for the user
            group_id: Unique identifier for the group
            mem_id: Unique identifier of the memory to delete

        Returns:
            True if deletion was successful, False otherwise
        """
        pass

    @abstractmethod
    async def delete_mem_by_user_id(self, user_id: str, group_id: str) -> bool:
        """
        Delete all memories(messages, variables, profiles) for a specific user in a group.

        Useful for implementing "forget me" functionality or cleaning up user data.

        Args:
            user_id: User identifier whose memories should be deleted
            group_id: Group identifier to scope the deletion

        Returns:
            True if deletion was successful, False otherwise
        """
        pass

    @abstractmethod
    async def update_mem_by_id(self, user_id: str, group_id: str, mem_id: str, memory: str) -> bool:
        """
        Update the content of an existing memory entry.

        Args:
            user_id: Unique identifier for the user
            group_id: Unique identifier for the group
            mem_id: Unique identifier of the memory to update
            memory: New content for the memory

        Returns:
            True if update was successful, False otherwise
        """
        pass

    @abstractmethod
    async def get_user_variable(self, user_id: str, group_id: str, name: str) -> str:
        """
        Retrieve a specific user variable by name.

        User variables store persistent user preferences, settings, or context
        that should be maintained across conversations.

        Args:
            user_id: User identifier
            group_id: Group identifier
            name: Name of the variable to retrieve

        Returns:
            Value of the user variable as string

        Raises:
            VariableNotFoundError: If the variable doesn't exist
        """
        pass

    @abstractmethod
    async def list_user_variables(self, user_id: str, group_id: str) -> dict[str, str]:
        """
        Retrieve all variables for a specific user in a group.

        Args:
            user_id: User identifier
            group_id: Group identifier

        Returns:
            Dictionary mapping variable names to their values
        """
        pass

    @abstractmethod
    async def search_user_mem(self, user_id: str, group_id: str, query: str, num: int,
                              threshold: float = 0.3) -> list[dict[str, Any]]:
        """
        Search user memories using semantic similarity.

        Performs vector-based similarity search to find relevant memories
        based on the query content rather than exact keyword matching.

        Args:
            user_id: User identifier to search within
            group_id: Group identifier to scope the search
            query: Search query text
            num: Maximum number of results to return
            threshold: Similarity threshold (0.0-1.0) for filtering results

        Returns:
            List of memory dictionaries with similarity scores and metadata
        """
        pass

    @abstractmethod
    async def list_user_mem(self, user_id: str, group_id: str, num: int, page: int) -> list[dict[str, Any]]:
        """
        List user memories with pagination support.

        Retrieves memories in chronological order, suitable for displaying
        conversation history or memory browsing interfaces.

        Args:
            user_id: User identifier
            group_id: Group identifier
            num: Number of memories per page
            page: Page number (0-indexed)

        Returns:
            List of memory dictionaries with content and metadata
        """
        pass

    @abstractmethod
    async def update_user_variable(self, user_id: str, group_id: str, name: str, value: str):
        """
        Create or update a user variable.

        Args:
            user_id: User identifier
            group_id: Group identifier
            name: Name of the variable to set
            value: New value for the variable
        """
        pass

    @abstractmethod
    async def delete_user_variable(self, user_id: str, group_id: str, name: str):
        """
        Delete a specific user variable.

        Args:
            user_id: User identifier
            group_id: Group identifier
            name: Name of the variable to delete
        """
        pass


class MemoryEngine(BaseMemoryEngine):
    _mem_engine_instance: BaseMemoryEngine | None = None
    _kv_store_instance: BaseKVStore | None = None
    _semantic_store_instance: BaseSemanticStore | None = None
    _db_store_instance: BaseDbStore | None = None

    def __init__(self, config: SysMemConfig, kv_store: BaseKVStore, semantic_store: BaseSemanticStore,
                 db_store: BaseDbStore, **kwargs):
        super().__init__(config=config, kv_store=kv_store, semantic_store=semantic_store, db_store=db_store)
        # config
        self._sys_mem_config = config  # sys mem config
        self._group_config: dict[str, MemoryConfig] = {}  # group mem config map
        # store and manager
        self.kv_store = kv_store
        data_id_generator = DataIdManager()
        user_mem_store = UserMemStore(kv_store)
        if db_store:
            sql_db_store = SqlDbStore(db_store)
            self.message_manager = MessageManager(sql_db_store, data_id_generator, self._sys_mem_config.crypto_key)
        else:
            self.message_manager = None
        self.user_profile_manager = UserProfileManager(
            semantic_recall_instance=semantic_store,
            user_mem_store=user_mem_store,
            data_id_generator=data_id_generator,
            crypto_key=self._sys_mem_config.crypto_key
        )
        self.variable_manager = VariableManager(kv_store, self._sys_mem_config.crypto_key)
        managers = {
            MemoryType.USER_PROFILE.value: self.user_profile_manager,
            MemoryType.VARIABLE.value: self.variable_manager
        }
        self.write_manager = WriteManager(managers, user_mem_store)
        self.search_manager = SearchManager(managers, user_mem_store, self._sys_mem_config.crypto_key)
        self.generator = Generator()
        # llm
        self._base_llm: Tuple[str, BaseModelClient] | None = None
        self._group_llm: dict[str, Tuple[str, BaseModelClient]] = {}

    def init_base_llm(self, llm_config: ModelConfig) -> bool:
        llm = MemoryEngine._get_llm_from_config(llm_config)
        self._base_llm = (llm_config.model_info.model_name, llm)
        return True

    def set_group_config(self, group_id: str, config: MemoryConfig) -> bool:
        self._group_config[group_id] = config
        return True

    def set_group_llm(self, group_id: str, model_name: str, llm: BaseModelClient) -> bool:
        self._group_llm[group_id] = (model_name, llm)
        return True

    def set_group_llm_config(self, group_id: str, llm_config: ModelConfig) -> bool:
        llm = MemoryEngine._get_llm_from_config(llm_config)
        return self.set_group_llm(group_id=group_id, model_name=llm_config.model_info.model_name, llm=llm)

    async def add_conversation_messages(
            self,
            user_id: str,
            group_id: str,
            messages: list[BaseMessage],
            timestamp: datetime | None = None,
            session_id: str | None = None
    ) -> str | None:
        msg_id = "-1"
        llm = self._get_group_llm(group_id)
        # user level distributed lock
        lock = DistributedLock(self.kv_store, f"user/{user_id}")
        async with lock:
            if not llm:
                logger.error("llm is not initialized.")
                return msg_id
            history_messages = await self._get_history_messages(user_id=user_id,
                                                                group_id=group_id,
                                                                session_id=session_id,
                                                                config=self._sys_mem_config)
            # when multi messages, use last msg_id
            if self._sys_mem_config.record_message and self.message_manager:
                for i, msg in enumerate(messages):
                    msg_timestamp = timestamp + timedelta(milliseconds=i)
                    msg_id = await self.message_manager.add(
                        user_id=user_id,
                        group_id=group_id,
                        role=msg.role,
                        content=msg.content,
                        session_id=session_id,
                        timestamp=msg_timestamp
                    )
            else:
                msg_id = None

            check_res, messages = self._check_messages(messages=messages)
            if not check_res:
                logger.info("Memory engine no need to process messages.")
                return msg_id

            group_mem_config = self._get_group_config(group_id)

            all_memory: list[BaseMemoryUnit] = await self.generator.gen_all_memory(
                group_id=group_id,
                user_id=user_id,
                messages=messages,
                history_messages=history_messages,
                session_id=session_id,
                config=group_mem_config,
                base_chat_model=llm,
                message_mem_id=msg_id
            )
            try:
                await self.write_manager.add_mem(mem_units=all_memory, llm=llm)
            except ValueError as e:
                logger.error(f"Failed to add mem, error: {str(e)}")
                raise ValueError(f"Failed to add mem, error: {str(e)}") from e
            return msg_id

    async def get_message_by_id(self, msg_id: str) -> Tuple[BaseMessage, datetime] | None:
        if not self.message_manager:
            logger.warning("Message manager is not initialized.")
            return None
        return await self.message_manager.get_by_id(msg_id)

    async def delete_mem_by_id(self, user_id: str, group_id: str, mem_id: str) -> bool:
        lock = DistributedLock(self.kv_store, f"user/{user_id}")
        async with lock:
            if not self.write_manager:
                raise ValueError("Write manager is not initialized.")
            await self.write_manager.delete_mem_by_id(user_id=user_id, group_id=group_id, mem_id=mem_id)
            return True

    async def delete_mem_by_user_id(self, user_id: str, group_id: str) -> bool:
        lock = DistributedLock(self.kv_store, f"user/{user_id}")
        async with lock:
            if not self.write_manager:
                raise ValueError("Write manager is not initialized.")
            await self.write_manager.delete_mem_by_user_id(user_id=user_id, group_id=group_id)
            return True

    async def update_mem_by_id(self, user_id: str, group_id: str, mem_id: str, memory: str) -> bool:
        lock = DistributedLock(self.kv_store, f"user/{user_id}")
        async with lock:
            if not self.write_manager:
                raise ValueError("Write manager is not initialized.")
            await self.write_manager.update_mem_by_id(user_id=user_id, group_id=group_id, mem_id=mem_id, memory=memory)
            return True

    async def get_user_variable(self, user_id: str, group_id: str, name: str) -> str:
        if not self.search_manager:
            raise ValueError("Search manager is not initialized.")
        return await self.search_manager.get_user_variable(user_id, group_id, name)

    async def list_user_variables(self, user_id: str, group_id: str) -> dict[str, str]:
        if not self.search_manager:
            raise ValueError("Search manager is not initialized.")
        return await self.search_manager.get_all_user_variable(user_id, group_id)

    async def search_user_mem(self, user_id: str, group_id: str, query: str, num: int,
                              threshold: float = 0.3) -> list[dict[str, Any]]:
        if not self.search_manager:
            raise ValueError("Search manager is not initialized.")
        try:
            return await self.search_manager.search(user_id=user_id,
                                                    group_id=group_id,
                                                    query=query,
                                                    top_k=num,
                                                    threshold=threshold)
        except AttributeError as e:
            logger.debug(f"Search user mem has attribute exception: {str(e)}")
            return []
        except ValueError as e:
            logger.warning(f"Search user mem has value exception: {str(e)}")
            return []
        except Exception as e:
            logger.warning(f"Search user mem has exception: {str(e)}")
            return []

    async def list_user_mem(self, user_id: str, group_id: str, num: int, page: int) -> list[dict[str, Any]]:
        if not self.search_manager:
            raise ValueError("Search manager is not initialized.")
        return await self.search_manager.list_user_mem(user_id=user_id, group_id=group_id, nums=num, pages=page)

    async def update_user_variable(self, user_id: str, group_id: str, name: str, value: str):
        lock = DistributedLock(self.kv_store, f"user/{user_id}")
        async with lock:
            if not self.variable_manager:
                raise ValueError("Variable manager is not initialized.")
            await self.variable_manager.update_user_variable(user_id=user_id, group_id=group_id, var_name=name,
                                                             var_mem=value)
            return True

    async def delete_user_variable(self, user_id: str, group_id: str, name: str):
        lock = DistributedLock(self.kv_store, f"user/{user_id}")
        async with lock:
            if not self.variable_manager:
                raise ValueError("Variable manager is not initialized.")
            await self.variable_manager.delete_user_variable(user_id=user_id, group_id=group_id, var_name=name)
            return True

    @staticmethod
    def _get_llm_from_config(model_config: ModelConfig) -> BaseModelClient:
        return ModelFactory().get_model(
            model_provider=model_config.model_provider,
            api_key=model_config.model_info.api_key,
            api_base=model_config.model_info.api_base,
        )

    def _get_group_config(self, group_id: str) -> MemoryConfig:
        if group_id not in self._group_config.keys():
            return MemoryConfig()
        return self._group_config[group_id]

    def _get_group_llm(self, group_id: str) -> Tuple[str, BaseModelClient] | None:
        if group_id not in self._group_llm.keys():
            return self._base_llm
        return self._group_llm[group_id]

    def _check_messages(self, messages: list[BaseMessage]) -> Tuple[bool, list[BaseMessage]]:
        out_messages = []
        has_human_msg = False
        human_message: HumanMessage = HumanMessage()
        for msg in messages:
            if msg.role == human_message.role:
                out_messages.append(msg)
                has_human_msg = True
                continue
            msg.content = msg.content[:self._sys_mem_config.ai_msg_gen_max_len]
            out_messages.append(msg)

        return has_human_msg, out_messages

    async def _get_history_messages(self,
                                    user_id: str,
                                    group_id: str,
                                    session_id: str,
                                    config: SysMemConfig,
                                    ) -> list[BaseMessage]:
        threshold = config.history_window_size_to_gen_mem
        history_message_length_limit = config.ai_msg_gen_max_len
        if not self.message_manager:
            return []
        history_messages_tuple = await self.message_manager.get(
            user_id=user_id,
            group_id=group_id,
            session_id=session_id,
            message_len=threshold
        )
        history_messages = []
        human_message: HumanMessage = HumanMessage()
        for msg, _ in history_messages_tuple:
            if msg.role == human_message.role:
                history_messages.append(msg)
                continue
            msg.content = msg.content[:self._sys_mem_config.ai_msg_gen_max_len]
            history_messages.append(msg)
        return history_messages

    @classmethod
    def register_store(cls, kv_store: BaseKVStore, semantic_store: BaseSemanticStore | None = None,
                       db_store: BaseDbStore | None = None):
        if issubclass(kv_store.__class__, BaseKVStore):
            cls._kv_store_instance = kv_store
        else:
            logger.error("kv_store must be subclass of BaseKVStore")

        if semantic_store is not None:
            if issubclass(semantic_store.__class__, BaseSemanticStore):
                cls._semantic_store_instance = semantic_store
            else:
                logger.error("semantic_store must be subclass of BaseSemanticStore")

        if db_store is not None:
            if issubclass(db_store.__class__, BaseDbStore):
                cls._db_store_instance = db_store
            else:
                logger.error("db_store must be subclass of BaseDBStore")

        return cls

    @classmethod
    def get_mem_engine_instance(cls) -> BaseMemoryEngine | None:
        return cls._mem_engine_instance

    @classmethod
    async def create_mem_engine_instance(cls, config: SysMemConfig):
        if cls._kv_store_instance is None:
            logger.error("Failed to create memory engine instance, you need resister kv store")
            return None
        if cls._db_store_instance is not None:
            await create_tables(cls._db_store_instance)
        cls._mem_engine_instance = cls(config=config, kv_store=cls._kv_store_instance,
                                       semantic_store=cls._semantic_store_instance,
                                       db_store=cls._db_store_instance)
        return cls._mem_engine_instance
