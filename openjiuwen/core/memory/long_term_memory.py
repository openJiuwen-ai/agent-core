# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from datetime import datetime, timedelta, timezone
from typing import Tuple
from pydantic import BaseModel, Field

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm.schema.config import ModelRequestConfig, ModelClientConfig
from openjiuwen.core.memory.common.distributed_lock import DistributedLock
from openjiuwen.core.memory.config.config import MemoryEngineConfig, MemoryScopeConfig
from openjiuwen.core.memory.generation.generation import Generator
from openjiuwen.core.memory.manage.data_id_manager import DataIdManager
from openjiuwen.core.memory.manage.message_manager import MessageManager, MessageAddRequest
from openjiuwen.core.memory.manage.user_profile_manager import UserProfileManager
from openjiuwen.core.memory.manage.variable_manager import VariableManager
from openjiuwen.core.memory.manage.write_manager import WriteManager
from openjiuwen.core.memory.mem_unit.memory_unit import BaseMemoryUnit, MemoryType
from openjiuwen.core.memory.search.search_manager.search_manager import SearchManager, SearchParams
from openjiuwen.core.memory.store.base_db_store import BaseDbStore
from openjiuwen.core.memory.store.base_kv_store import BaseKVStore
from openjiuwen.core.memory.store.base_semantic_store import BaseSemanticStore
from openjiuwen.core.memory.store.message import create_tables
from openjiuwen.core.memory.store.sql_db_store import SqlDbStore
from openjiuwen.core.memory.store.user_mem_store import UserMemStore
from openjiuwen.core.foundation.llm import UserMessage, BaseMessage, Model
from openjiuwen.core.common.utils.singleton import Singleton


class MemInfo(BaseModel):
    mem_id: str = Field(default="", description="memory id")
    content: str = Field(default="", description="memory content")
    type: MemoryType = Field(default=MemoryType.USER_PROFILE, description="memory type")


class MemResult(BaseModel):
    mem_info: MemInfo = Field(default=None, description="memory information")
    score: float = Field(default=0.0, description="memory score of relevance")


class LongTermMemory(metaclass=Singleton):
    """
    Abstract base class for memory engine.

    Defines the core interface for memory storage and retrieval operations.
    Provides unified memory management functionality including conversation memory,
    user variables, semantic search, and persistence.

    Concrete implementations should handle memory operations across multiple storage
    backends (KV store, semantic store, database store).
    """
    DEFAULT_VALUE: str = "__default__"

    def __init__(self):
        """
        Initialize the memory engine
        """
        # config
        self._sys_mem_config: MemoryEngineConfig | None = None
        self._scope_config: dict[str, MemoryScopeConfig] = {}
        # store
        self.kv_store: BaseKVStore | None = None
        self.semantic_store: BaseSemanticStore | None = None
        self.db_store: BaseDbStore | None = None
        # managers
        self.message_manager = None
        self.user_profile_manager = None
        self.variable_manager = None
        self.write_manager = None
        self.search_manager = None
        self.generator = None
        # llm
        self._base_llm: Tuple[str, Model] | None = None
        self._scope_llm: dict[str, Tuple[str, Model]] = {}

    async def register_store(self, kv_store: BaseKVStore,
                             semantic_store: BaseSemanticStore | None = None,
                             db_store: BaseDbStore | None = None):
        """
        Register store instance.

        Args:
            kv_store: Key-value store for fast structured data access
            semantic_store: Semantic storage for vector-based similarity search
            db_store: Database store for persistent data storage
        """
        if kv_store is None:
            raise ValueError("kv_store is required, cannot be None")

        if semantic_store is not None and not isinstance(semantic_store, BaseSemanticStore):
            raise TypeError("semantic_store must be instance of BaseSemanticStore")

        if db_store is not None and not isinstance(db_store, BaseDbStore):
            raise TypeError("db_store must be instance of BaseDbStore")

        self.kv_store = kv_store
        self.semantic_store = semantic_store
        self.db_store = db_store

        if self.db_store:
            await create_tables(self.db_store)

    def set_config(self, config: MemoryEngineConfig):
        """
        Set configuration.

        Args:
            config: memory engine configuration parameters
        """
        if not self.kv_store or not self.semantic_store or not self.db_store:
            raise ValueError("Stores must be registered before setting config.")
        self._sys_mem_config = config
        data_id_generator = DataIdManager()
        user_mem_store = UserMemStore(self.kv_store)
        if self.db_store:
            sql_db_store = SqlDbStore(self.db_store)
            self.message_manager = MessageManager(
                sql_db_store,
                data_id_generator,
                config.crypto_key
            )
        self.user_profile_manager = UserProfileManager(
            semantic_recall_instance=self.semantic_store,
            user_mem_store=user_mem_store,
            data_id_generator=data_id_generator,
            crypto_key=config.crypto_key
        )
        self.variable_manager = VariableManager(
            self.kv_store,
            config.crypto_key
        )
        managers = {
            MemoryType.USER_PROFILE.value: self.user_profile_manager,
            MemoryType.VARIABLE.value: self.variable_manager
        }
        self.write_manager = WriteManager(managers, user_mem_store)
        self.search_manager = SearchManager(
            managers,
            user_mem_store,
            config.crypto_key
        )
        self.generator = Generator()
        # set init llm
        llm = LongTermMemory._get_llm_from_config(model_config=config.default_model_cfg,
                                                model_client_config=config.default_model_client_cfg)
        self._base_llm = (config.default_model_cfg.model_name, llm)

    def set_scope_config(self, scope_id: str, memory_scope_config: MemoryScopeConfig) -> bool:
        self._scope_config[scope_id] = memory_scope_config
        llm = LongTermMemory._get_llm_from_config(model_config=memory_scope_config.model_cfg,
                                                model_client_config=memory_scope_config.model_client_cfg)
        self._scope_llm[scope_id] = (memory_scope_config.model_cfg.model_name, llm)
        return True

    async def add_messages(
            self,
            messages: list[BaseMessage],
            *,
            user_id: str = DEFAULT_VALUE,
            scope_id: str = DEFAULT_VALUE,
            session_id: str = DEFAULT_VALUE,
            timestamp: datetime | None = None,
            gen_mem: bool = True,
            gen_mem_with_history_msg_num: int = 5
    ):
        msg_id = "-1"
        llm = self._get_group_llm(scope_id)
        # user level distributed lock
        lock = DistributedLock(self.kv_store, f"user/{user_id}")
        async with lock:
            if not llm:
                logger.error("llm is not initialized.")
                return
            history_messages = await self._get_history_messages(
                user_id=user_id,
                group_id=scope_id,
                session_id=session_id,
                history_window_size=gen_mem_with_history_msg_num)
            if not timestamp:
                timestamp = datetime.now(timezone.utc)
            # when multi messages, use last msg_id
            if gen_mem:
                for i, msg in enumerate(messages):
                    msg_timestamp = timestamp + timedelta(milliseconds=i)
                    add_req = MessageAddRequest(
                        user_id=user_id,
                        group_id=scope_id,
                        role=msg.role,
                        content=msg.content,
                        session_id=session_id,
                        timestamp=msg_timestamp
                    )
                    msg_id = await self.message_manager.add(add_req)
            else:
                msg_id = None

            check_res, messages = self._check_messages(messages=messages)
            if not check_res:
                logger.info("Memory engine no need to process messages.")
                return

            group_mem_config = self._get_group_config(scope_id)

            all_memory: list[BaseMemoryUnit] = await self.generator.gen_all_memory(
                group_id=scope_id,
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
            return

    async def get_recent_messages(
            self,
            user_id: str = DEFAULT_VALUE,
            scope_id: str = DEFAULT_VALUE,
            session_id: str = DEFAULT_VALUE,
            num: int = 10
    ) -> list[BaseMessage]:
        """
        Get recent messages.

        Args:
            user_id: Unique identifier for the user
            scope_id: Unique identifier for the scope
            session_id: Optional session identifier for grouping related messages
            num: message num

        Returns:
            Message list in order of writing.
        """
        recent_messages_tuple = await self.message_manager.get(
            user_id=user_id,
            group_id=scope_id,
            session_id=session_id,
            message_len=num
        )
        recent_messages = [msg for msg, _ in recent_messages_tuple]
        return recent_messages

    async def get_message_by_id(self, msg_id: str) -> Tuple[BaseMessage, datetime] | None:
        """
        Retrieve a specific message by its unique identifier.

        Args:
            msg_id: Unique identifier of the message to retrieve

        Returns:
            Tuple of (message object, creation timestamp)
        """
        if not self.message_manager:
            logger.warning("Message manager is not initialized.")
            return None
        return await self.message_manager.get_by_id(msg_id)

    async def delete_mem_by_id(self,
                               mem_id: str,
                               user_id: str = DEFAULT_VALUE,
                               scope_id: str = DEFAULT_VALUE):
        """
        Delete a specific memory by ID.

        Args:
            user_id: Unique identifier for the user
            scope_id: Unique identifier for the scope
            mem_id: Unique identifier of the memory to delete
        """
        lock = DistributedLock(self.kv_store, f"user/{user_id}")
        async with lock:
            if not self.write_manager:
                raise ValueError("Write manager is not initialized.")
            await self.write_manager.delete_mem_by_id(user_id=user_id, group_id=scope_id, mem_id=mem_id)

    async def delete_mem_by_user_id(self,
                                    user_id: str = DEFAULT_VALUE,
                                    scope_id: str = DEFAULT_VALUE):
        """
        Delete all type memories for a user with scope id.

        Useful for implementing "forget me" functionality or cleaning up user data.

        Args:
            user_id: User identifier whose memories should be deleted
            scope_id: Unique identifier for the scope
        """
        lock = DistributedLock(self.kv_store, f"user/{user_id}")
        async with lock:
            if not self.write_manager:
                raise ValueError("Write manager is not initialized.")
            await self.write_manager.delete_mem_by_user_id(user_id=user_id, group_id=scope_id)

    async def update_mem_by_id(self,
                               mem_id: str,
                               memory: str,
                               user_id: str = DEFAULT_VALUE,
                               scope_id: str = DEFAULT_VALUE):
        """
        Update the content of an existing memory entry.

        Args:
            mem_id: Unique identifier of the memory to update
            memory: New content for the memory
            user_id: Unique identifier for the user
            scope_id: Unique identifier for the scope
        """
        lock = DistributedLock(self.kv_store, f"user/{user_id}")
        async with lock:
            if not self.write_manager:
                raise ValueError("Write manager is not initialized.")
            await self.write_manager.update_mem_by_id(user_id=user_id, group_id=scope_id,
                                                      mem_id=mem_id, memory=memory)

    async def get_user_variable(self,
                                names: list[str] | str | None = None,
                                user_id: str = DEFAULT_VALUE,
                                scope_id: str = DEFAULT_VALUE) -> dict[str, str]:
        """
            Get user variable(s)

            Args:
                names: Name of the variable(s) to get.
                       - None: return all variables
                       - str: return one variable
                       - list[str]: return multiple variables
                user_id: user identifier
                scope_id: scope identifier

            Returns:
                dict[str, str]: variable name -> value
        """
        if not self.search_manager:
            raise ValueError("Search manager is not initialized.")
        ret: dict[str, str] = {}
        if names is None:
            return await self.search_manager.get_all_user_variable(user_id=user_id, group_id=scope_id)
        if isinstance(names, str):
            value = await self.search_manager.get_user_variable(user_id, scope_id, names)
            ret[names] = value
            return ret
        if isinstance(names, list):
            for name in names:
                value = await self.search_manager.get_user_variable(user_id, scope_id, name)
                ret[name] = value
            return ret
        raise TypeError("names must be str | list[str] | None")

    async def search_user_mem(self,
                              query: str,
                              num: int,
                              user_id: str = DEFAULT_VALUE,
                              scope_id: str = DEFAULT_VALUE,
                              threshold: float = 0.3
                              ) -> list[MemResult]:
        if not self.search_manager:
            raise ValueError("Search Manager is not initialized")
        params = SearchParams(
            query=query,
            group_id=scope_id,
            top_k=num,
            user_id=user_id,
            threshold=threshold
        )
        try:
            search_data = await self.search_manager.search(params)
            mem_results: list[MemResult] = [
                MemResult(
                    mem_info=MemInfo(
                        mem_id=item["id"],
                        content=item["mem"],
                        type=item.get("mem_type", MemoryType.USER_PROFILE)
                    ),
                    score=item.get("score", 0.0)
                )
                for item in search_data
            ]
            return mem_results
        except AttributeError as e:
            logger.debug(f"Search user mem has attribute exception: {str(e)}")
            return []
        except ValueError as e:
            logger.warning(f"Search user mem has value exception: {str(e)}")
            return []
        except Exception as e:
            logger.warning(f"Search user mem has exception: {str(e)}")
            return []

    async def user_mem_total_num(self,
                                 user_id: str = DEFAULT_VALUE,
                                 scope_id: str = DEFAULT_VALUE) -> int:
        """
        return total number of user memory
        """
        data = await self.search_manager.list_user_profile(user_id=user_id, group_id=scope_id)
        return len(data)

    async def get_user_mem_by_page(self,
                                   user_id: str = DEFAULT_VALUE,
                                   scope_id: str = DEFAULT_VALUE,
                                   page_size: int = 10,
                                   page_idx: int = 0) -> list[MemInfo]:
        """
        List user memories with pagination support.

        Retrieves memories in chronological order, suitable for displaying
        conversation history or memory browsing interfaces.

        Args:
            user_id: User identifier to search within
            scope_id: Unique identifier for the scope
            page_size: Number of memories per page
            page_idx: Page index

        Returns:
            List of memory information
        """
        if not self.search_manager:
            raise ValueError("Search manager is not initialized.")
        search_data = await self.search_manager.list_user_mem(user_id=user_id, group_id=scope_id,
                                                              nums=page_size, pages=page_idx)

        if not search_data:
            return []

        mem_results: list[MemInfo] = [
            MemInfo(
                mem_id=item["id"],
                content=item["mem"],
                type=item.get("mem_type", MemoryType.USER_PROFILE)
            )
            for item in search_data
        ]
        return mem_results

    async def update_user_variable(self,
                                   variables: dict[str, str],
                                   user_id: str = DEFAULT_VALUE,
                                   scope_id: str = DEFAULT_VALUE
                                   ):
        """
        Update user variables.

        Args:
            variables: variable name to value pairs
            user_id: User identifier to search within
            scope_id: Unique identifier for the scope
        """
        lock = DistributedLock(self.kv_store, f"user/{user_id}")
        async with lock:
            if not self.variable_manager:
                raise ValueError("Variable manager is not initialized.")
            for name, value in variables.items():
                await self.variable_manager.update_user_variable(
                    user_id=user_id,
                    group_id=scope_id,
                    var_name=name,
                    var_mem=value
                )

    async def delete_user_variable(self,
                                   names: list[str],
                                   user_id: str = DEFAULT_VALUE,
                                   scope_id: str = DEFAULT_VALUE):
        """
        Delete user variables.

        Args:
            names: Name of the variables to delete
            user_id: User identifier to search within
            scope_id: Unique identifier for the scope
        """
        lock = DistributedLock(self.kv_store, f"user/{user_id}")
        async with lock:
            if not self.variable_manager:
                raise ValueError("Variable manager is not initialized.")
            for name in names:
                await self.variable_manager.delete_user_variable(user_id=user_id, group_id=scope_id, var_name=name)
            return True

    @staticmethod
    def _get_llm_from_config(model_config: ModelRequestConfig,
                             model_client_config: ModelClientConfig):
        return Model(model_config=model_config, model_client_config=model_client_config)

    def _get_group_config(self, group_id: str) -> MemoryScopeConfig:
        if group_id not in self._scope_config.keys():
            return MemoryScopeConfig()
        return self._scope_config[group_id]

    def _get_group_llm(self, group_id: str) -> Tuple[str, Model] | None:
        if group_id not in self._scope_llm.keys():
            return self._base_llm
        return self._scope_llm[group_id]

    def _check_messages(self, messages: list[BaseMessage]) -> Tuple[bool, list[BaseMessage]]:
        out_messages = []
        has_human_msg = False
        human_message: UserMessage = UserMessage()
        for msg in messages:
            if msg.role == human_message.role:
                out_messages.append(msg)
                has_human_msg = True
                continue
            msg.content = msg.content[:self._sys_mem_config.input_msg_max_len]
            out_messages.append(msg)

        return has_human_msg, out_messages

    async def _get_history_messages(self,
                                    user_id: str,
                                    group_id: str,
                                    session_id: str,
                                    history_window_size: int
                                    ) -> list[BaseMessage]:
        threshold = history_window_size
        if not self.message_manager:
            return []
        history_messages_tuple = await self.message_manager.get(
            user_id=user_id,
            group_id=group_id,
            session_id=session_id,
            message_len=threshold
        )
        history_messages = []
        human_message: UserMessage = UserMessage()
        for msg, _ in history_messages_tuple:
            if msg.role == human_message.role:
                history_messages.append(msg)
                continue
            msg.content = msg.content[:self._sys_mem_config.input_msg_max_len]
            history_messages.append(msg)
        return history_messages
