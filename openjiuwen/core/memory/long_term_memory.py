# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import copy
from datetime import datetime, timedelta
from typing import Tuple
from pydantic import BaseModel, Field

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm.schema.config import ModelRequestConfig, ModelClientConfig
from openjiuwen.core.memory.common.distributed_lock import DistributedLock
from openjiuwen.core.memory.config.config import MemoryEngineConfig, MemoryScopeConfig, MemoryAgentConfig
from openjiuwen.core.memory.generation.generation import Generator
from openjiuwen.core.memory.manage.data_id_manager import DataIdManager
from openjiuwen.core.memory.manage.message_manager import MessageManager, MessageAddRequest
from openjiuwen.core.memory.manage.user_profile_manager import UserProfileManager
from openjiuwen.core.memory.manage.variable_manager import VariableManager
from openjiuwen.core.memory.manage.write_manager import WriteManager
from openjiuwen.core.memory.mem_unit.memory_unit import BaseMemoryUnit, MemoryType
from openjiuwen.core.memory.manage.base_memory_manager import BaseMemoryManager
from openjiuwen.core.memory.search.search_manager.search_manager import SearchManager, SearchParams
from openjiuwen.core.memory.store.base_db_store import BaseDbStore
from openjiuwen.core.memory.store.base_kv_store import BaseKVStore
from openjiuwen.core.memory.store.semantic_store import SemanticStore
from openjiuwen.core.memory.store.message import create_tables
from openjiuwen.core.memory.store.sql_db_store import SqlDbStore
from openjiuwen.core.memory.store.user_mem_store import UserMemStore
from openjiuwen.core.foundation.llm import UserMessage, BaseMessage, Model
from openjiuwen.core.common.utils.singleton import Singleton
from openjiuwen.core.retrieval.embedding.base import Embedding
from openjiuwen.core.retrieval.embedding.api_embedding import APIEmbedding
from openjiuwen.core.retrieval.vector_store.base import VectorStore


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
    SCOPE_CONFIG_KEY: str = "memory_scope_config"

    def __init__(self):
        """
        Initialize the memory engine
        """
        # config
        self._sys_mem_config: MemoryEngineConfig | None = None
        self._scope_config: dict[str, MemoryScopeConfig] = {}
        # store
        self.kv_store: BaseKVStore | None = None
        self.semantic_store: SemanticStore | None = None
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
        # embedding model cache
        self._scope_embedding: dict[str, Embedding] = {}

    async def register_store(self, kv_store: BaseKVStore,
                             vector_store: VectorStore | None = None,
                             db_store: BaseDbStore | None = None,
                             embedding_model: Embedding | None = None):
        """
        Register store instance.

        Args:
            kv_store: Key-value store for fast structured data access
            vector_store: Vector storage for vector-based similarity search
            db_store: Database store for persistent data storage
            embedding_model: Embedding model for semantic search
        """
        if kv_store is None:
            raise ValueError("kv_store is required, cannot be None")

        if vector_store is not None and not isinstance(vector_store, VectorStore):
            raise TypeError("vector_store must be instance of VectorStore")

        if db_store is not None and not isinstance(db_store, BaseDbStore):
            raise TypeError("db_store must be instance of BaseDbStore")

        self.kv_store = kv_store
        self.semantic_store = SemanticStore(vector_store=vector_store)
        self.db_store = db_store

        if self.semantic_store and embedding_model is not None:
            # Only temporarily initialize the embedding model of the semantic_store during the register_store process.
            self.semantic_store.initialize_embedding_model(embedding_model)

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

    async def set_scope_config(self, scope_id: str, memory_scope_config: MemoryScopeConfig) -> bool:
        """
        Set the scope-specific memory configuration and store it in kv_store.

        Args:
            scope_id: The scope identifier.
            memory_scope_config: The scope-specific memory configuration.


        Returns:
            True if the configuration was set successfully, False otherwise.
        """
        if not self._validate_id(scope_id=scope_id):
            logger.error(f"Invalid scope_id format, scope_id={scope_id}")
            return False
        # Create a deep copy of the config to avoid modifying the original
        encrypted_config = copy.deepcopy(memory_scope_config)

        # Encrypt API keys if they exist
        if encrypted_config.model_client_cfg and encrypted_config.model_client_cfg.api_key:
            encrypted_config.model_client_cfg.api_key = BaseMemoryManager.encrypt_memory_if_needed(
                key=self._sys_mem_config.crypto_key,
                plaintext=encrypted_config.model_client_cfg.api_key
            )

        if encrypted_config.embedding_cfg and encrypted_config.embedding_cfg.api_key:
            encrypted_config.embedding_cfg.api_key = BaseMemoryManager.encrypt_memory_if_needed(
                key=self._sys_mem_config.crypto_key,
                plaintext=encrypted_config.embedding_cfg.api_key
            )

        self._scope_config[scope_id] = encrypted_config

        config_key = f"{self.SCOPE_CONFIG_KEY}/{scope_id}"
        config_json = encrypted_config.model_dump_json(by_alias=True)
        await self.kv_store.set(config_key, config_json)

        # Clear cached embedding model for this scope since configuration changed
        if scope_id in self._scope_embedding:
            del self._scope_embedding[scope_id]

        return True

    async def get_scope_config(self, scope_id: str) -> MemoryScopeConfig | None:
        """
        Get the scope-specific memory configuration from kv_store.

        Args:
            scope_id: Unique identifier for the scope

        Returns:
            MemoryScopeConfig: The decrypted memory configuration for the scope, or None if not found
        """
        if not self._validate_id(scope_id=scope_id):
            logger.error(f"Invalid scope_id format, scope_id={scope_id}")
            return None
        config_key = f"{self.SCOPE_CONFIG_KEY}/{scope_id}"
        config_json = await self.kv_store.get(config_key)

        if not config_json:
            return None

        # Parse the JSON into MemoryScopeConfig
        encrypted_config = MemoryScopeConfig.model_validate_json(config_json)

        # Decrypt API keys if they exist
        if encrypted_config.model_client_cfg and encrypted_config.model_client_cfg.api_key:
            encrypted_config.model_client_cfg.api_key = BaseMemoryManager.decrypt_memory_if_needed(
                key=self._sys_mem_config.crypto_key,
                ciphertext=encrypted_config.model_client_cfg.api_key
            )

        if encrypted_config.embedding_cfg and encrypted_config.embedding_cfg.api_key:
            encrypted_config.embedding_cfg.api_key = BaseMemoryManager.decrypt_memory_if_needed(
                key=self._sys_mem_config.crypto_key,
                ciphertext=encrypted_config.embedding_cfg.api_key
            )

        return encrypted_config

    async def delete_scope_config(self, scope_id: str) -> bool:
        """
        Delete the scope-specific memory configuration from kv_store.

        Args:
            scope_id: The scope identifier whose configuration should be deleted.

        Returns:
            True if the configuration was deleted successfully, False otherwise.
        """
        if not self._validate_id(scope_id=scope_id):
            logger.error(f"Invalid scope_id format, scope_id={scope_id}")
            return False
        try:
            config_key = f"{self.SCOPE_CONFIG_KEY}/{scope_id}"
            await self.kv_store.delete(config_key)

            if scope_id in self._scope_config:
                del self._scope_config[scope_id]

            if scope_id in self._scope_embedding:
                del self._scope_embedding[scope_id]

            logger.debug(f"Successfully deleted configuration for scope {scope_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete configuration for scope {scope_id}", exc_info=e)
            return False

    async def delete_mem_by_scope(self, scope_id: str) -> bool:
        """
        Delete all memories associated with a specific scope.

        Args:
            scope_id: The scope identifier whose memories should be deleted.

        Returns:
            True if all memories were deleted successfully, False otherwise.
        """
        if not self._validate_id(scope_id=scope_id):
            logger.error(f"Invalid scope_id format, scope_id={scope_id}")
            return False

        if self.semantic_store:
            try:
                await self.semantic_store.delete_table(scope_id)
            except Exception as e:
                logger.error(f"Failed to delete semantic data for scope {scope_id}", exc_info=e)

        # Use write_manager to delete all memories associated with the scope
        if self.write_manager:
            try:
                await self.write_manager.delete_mem_by_scope_id(scope_id=scope_id)
            except Exception as e:
                logger.error(f"Failed to delete memories by scope id {scope_id}", exc_info=e)

        logger.debug(f"Successfully deleted memories for scope {scope_id}")
        return True

    async def add_messages(
            self,
            messages: list[BaseMessage],
            agent_config: MemoryAgentConfig,
            *,
            user_id: str = DEFAULT_VALUE,
            scope_id: str = DEFAULT_VALUE,
            session_id: str = DEFAULT_VALUE,
            timestamp: datetime | None = None,
            gen_mem: bool = True,
            gen_mem_with_history_msg_num: int = 5
    ):
        if not self._validate_id(scope_id=scope_id):
            logger.error(f"Invalid scope_id format, scope_id={scope_id}")
            return
        msg_id = "-1"
        llm = await self._get_scope_llm(scope_id)
        # Set the correct embedding model for this scope
        await self._set_semantic_store_embedding_model(scope_id)
        # user level distributed lock
        lock = DistributedLock(self.kv_store, f"user/{user_id}")
        async with lock:
            if not llm:
                logger.error("llm is not initialized.")
                return
            history_messages = await self._get_history_messages(
                user_id=user_id,
                scope_id=scope_id,
                session_id=session_id,
                history_window_size=gen_mem_with_history_msg_num)
            # when multi messages, use last msg_id
            if gen_mem:
                for i, msg in enumerate(messages):
                    msg_timestamp = timestamp + timedelta(milliseconds=i)
                    add_req = MessageAddRequest(
                        user_id=user_id,
                        scope_id=scope_id,
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
                logger.debug("Memory engine no need to process messages.")
                return

            all_memory: list[BaseMemoryUnit] = await self.generator.gen_all_memory(
                scope_id=scope_id,
                user_id=user_id,
                messages=messages,
                history_messages=history_messages,
                session_id=session_id,
                config=agent_config,
                base_chat_model=llm,
                message_mem_id=msg_id
            )
            try:
                await self.write_manager.add_mem(mem_units=all_memory, llm=llm)
                logger.debug("Successfully added memory units")
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
            session_id: Optional session identifier for scoping related messages
            num: message num

        Returns:
            Message list in order of writing.
        """
        if not self._validate_id(scope_id):
            logger.error(f"Invalid scope_id format, scope_id={scope_id}")
            return []
        recent_messages_tuple = await self.message_manager.get(
            user_id=user_id,
            scope_id=scope_id,
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
        if not self._validate_id(scope_id):
            logger.error(f"Invalid scope_id format, scope_id={scope_id}")
            return
        # Set the correct embedding model for this scope
        await self._set_semantic_store_embedding_model(scope_id)
        lock = DistributedLock(self.kv_store, f"user/{user_id}")
        async with lock:
            if not self.write_manager:
                raise ValueError("Write manager is not initialized.")
            await self.write_manager.delete_mem_by_id(user_id=user_id, scope_id=scope_id, mem_id=mem_id)

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
        if not self._validate_id(scope_id):
            logger.error(f"Invalid scope_id format, scope_id={scope_id}")
            return
        # Set the correct embedding model for this scope
        await self._set_semantic_store_embedding_model(scope_id)
        lock = DistributedLock(self.kv_store, f"user/{user_id}")
        async with lock:
            if not self.write_manager:
                raise ValueError("Write manager is not initialized.")
            await self.write_manager.delete_mem_by_user_id(user_id=user_id, scope_id=scope_id)

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
        if not self._validate_id(scope_id):
            logger.error(f"Invalid scope_id format, scope_id={scope_id}")
            return
        # Set the correct embedding model for this scope
        await self._set_semantic_store_embedding_model(scope_id)
        lock = DistributedLock(self.kv_store, f"user/{user_id}")
        async with lock:
            if not self.write_manager:
                raise ValueError("Write manager is not initialized.")
            await self.write_manager.update_mem_by_id(user_id=user_id, scope_id=scope_id,
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
        if not self._validate_id(scope_id):
            logger.error(f"Invalid scope_id format, scope_id={scope_id}")
            return {}
        if not self.search_manager:
            raise ValueError("Search manager is not initialized.")
        ret: dict[str, str] = {}
        if names is None:
            return await self.search_manager.get_all_user_variable(user_id=user_id, scope_id=scope_id)
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

        if not self._validate_id(scope_id):
            logger.error(f"Invalid scope_id format, scope_id={scope_id}")
            return []
        # Set the correct embedding model for this scope
        await self._set_semantic_store_embedding_model(scope_id)
        if not self.search_manager:
            raise ValueError("Search Manager is not initialized")
        params = SearchParams(
            query=query,
            scope_id=scope_id,
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
        if not self._validate_id(scope_id):
            logger.error(f"Invalid scope_id format, scope_id={scope_id}")
            return 0
        # Get all user profiles by using get_in_range with a large range
        search_data = await self.search_manager.list_user_mem(user_id=user_id, scope_id=scope_id,
                                                              nums=100, pages=1)
        return len(search_data) if search_data else 0

    async def get_user_mem_by_page(self,
                                   user_id: str = DEFAULT_VALUE,
                                   scope_id: str = DEFAULT_VALUE,
                                   page_size: int = 10,
                                   page_idx: int = 0,
                                   memory_type: MemoryType = MemoryType.UNKNOWN) -> list[MemInfo]:
        """
        List user memories with pagination support.

        Retrieves memories in chronological order, suitable for displaying
        conversation history or memory browsing interfaces.

        Args:
            user_id: User identifier to search within
            scope_id: Unique identifier for the scope
            page_size: Number of memories per page
            page_idx: Page index (0-based)
            memory_type: Memory type to filter. If UNKNOWN, no filtering is applied.

        Returns:
            List of memory information
        """
        if not self._validate_id(scope_id):
            logger.error(f"Invalid scope_id format, scope_id={scope_id}")
            return []
        if not self.search_manager:
            raise ValueError("Search manager is not initialized.")
        search_data = await self.search_manager.list_user_mem(user_id=user_id, scope_id=scope_id,
                                                              nums=page_size, pages=page_idx)

        if not search_data:
            return []

        mem_results: list[MemInfo] = []
        for item in search_data:
            mem_type = item.get("mem_type", MemoryType.USER_PROFILE)
            # Apply filtering if type is not UNKNOWN
            if memory_type == MemoryType.UNKNOWN or mem_type == memory_type:
                mem_results.append(
                    MemInfo(
                        mem_id=item["id"],
                        content=item["mem"],
                        type=mem_type
                    )
                )
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
        if not self._validate_id(scope_id):
            logger.error(f"Invalid scope_id format, scope_id={scope_id}")
            return
        lock = DistributedLock(self.kv_store, f"user/{user_id}")
        async with lock:
            if not self.variable_manager:
                raise ValueError("Variable manager is not initialized.")
            for name, value in variables.items():
                await self.variable_manager.update_user_variable(
                    user_id=user_id,
                    scope_id=scope_id,
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
        if not self._validate_id(scope_id):
            logger.error(f"Invalid scope_id format, scope_id={scope_id}")
            return False
        lock = DistributedLock(self.kv_store, f"user/{user_id}")
        async with lock:
            if not self.variable_manager:
                raise ValueError("Variable manager is not initialized.")
            for name in names:
                await self.variable_manager.delete_user_variable(user_id=user_id, scope_id=scope_id, var_name=name)
            return True

    @staticmethod
    def _get_llm_from_config(model_config: ModelRequestConfig,
                             model_client_config: ModelClientConfig):
        return Model(model_config=model_config, model_client_config=model_client_config)

    async def _get_scope_config(self, scope_id: str) -> MemoryScopeConfig | None:
        """
        Get the scope-specific configuration from memory cache first, then from kv_store if not found.

        Args:
            scope_id: Unique identifier for the scope

        Returns:
            MemoryScopeConfig: scope-specific configuration or None if not found
        """
        # First check if config is in memory cache
        if scope_id in self._scope_config:
            config = self._scope_config[scope_id]

            # Create a copy to avoid modifying the encrypted config in memory
            decrypted_config = copy.deepcopy(config)

            # Decrypt API keys if they exist
            if decrypted_config.model_client_cfg and decrypted_config.model_client_cfg.api_key:
                decrypted_config.model_client_cfg.api_key = BaseMemoryManager.decrypt_memory_if_needed(
                    key=self._sys_mem_config.crypto_key,
                    ciphertext=decrypted_config.model_client_cfg.api_key
                )

            if decrypted_config.embedding_cfg and decrypted_config.embedding_cfg.api_key:
                decrypted_config.embedding_cfg.api_key = BaseMemoryManager.decrypt_memory_if_needed(
                    key=self._sys_mem_config.crypto_key,
                    ciphertext=decrypted_config.embedding_cfg.api_key
                )

            return decrypted_config

        # If not in memory, get from kv_store
        return await self.get_scope_config(scope_id)

    async def _get_scope_embedding_model(self, scope_id: str) -> Embedding | None:
        """
        Get the embedding model for the scope from cache first, then from config if not found.

        Args:
            scope_id: scope/scope identifier

        Returns:
            APIEmbedModel: Embedding model for the scope, or None if no model is available
        """
        # Check if embedding model is already in cache
        if scope_id in self._scope_embedding:
            return self._scope_embedding[scope_id]

        try:
            config = await self._get_scope_config(scope_id)
            if config and config.embedding_cfg:
                # Use APIEmbedding to instantiate the embedding model
                embedding_model = APIEmbedding(config=config.embedding_cfg)
                # Cache the embedding model
                self._scope_embedding[scope_id] = embedding_model
                return embedding_model
        except Exception as e:
            logger.error(f"Failed to get or instantiate embedding model for scope {scope_id}: {str(e)}")

        logger.error(f"No embedding model available for scope {scope_id}")
        return None

    async def _get_scope_llm(self, scope_id: str) -> Tuple[str, Model]:
        """
        Get both LLM and embedding model for the scope with a single kv_store access.
        Note: Embedding model is now set through _set_semantic_store_embedding_model method,
        so this method only returns LLM for backward compatibility.

        Args:
            scope_id: scope/scope identifier

        Returns:
            Tuple[str, Model]: LLM model name and instance
        """
        try:
            config = await self._get_scope_config(scope_id)

            if config and config.model_cfg and config.model_client_cfg:
                llm = (config.model_cfg.model_name,
                       LongTermMemory._get_llm_from_config(config.model_cfg, config.model_client_cfg))
                return llm

            # If the LLM fails to be obtained, try to use the system default configuration.
            elif not self._sys_mem_config:
                pass
            elif not self._sys_mem_config.default_model_client_cfg:
                logger.debug("Default model client config is missing, cannot instantiate LLM")
            elif not self._sys_mem_config.default_model_cfg:
                logger.debug("Default model config is missing, cannot instantiate LLM")
            else:
                llm = (self._sys_mem_config.default_model_cfg.model_name,
                       LongTermMemory._get_llm_from_config(self._sys_mem_config.default_model_cfg,
                                                           self._sys_mem_config.default_model_client_cfg))
                return llm
            return self._base_llm

        except Exception as e:
            logger.error(f"Failed to get scope LLM for scope {scope_id}: {str(e)}")
            # If the LLM fails to be obtained, try to use the system default configuration.
            return self._base_llm

    async def _set_semantic_store_embedding_model(self, scope_id: str):
        """
        Set the embedding model for the semantic store based on the scope_id.

        Args:
            scope_id: Scope identifier
        """
        if not self.semantic_store:
            return

        embedding_model = await self._get_scope_embedding_model(scope_id)
        if embedding_model:
            self.semantic_store.initialize_embedding_model(embedding_model)

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
                                    scope_id: str,
                                    session_id: str,
                                    history_window_size: int
                                    ) -> list[BaseMessage]:
        threshold = history_window_size
        if not self.message_manager:
            return []
        history_messages_tuple = await self.message_manager.get(
            user_id=user_id,
            scope_id=scope_id,
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

    @staticmethod
    def _validate_id(scope_id: str = "") -> bool:
        """
        Validate the scope_id format.

        Args:
            scope_id: Scope identifier

        Returns:
            True if the scope_id is valid, False otherwise.
        """
        if not scope_id:
            logger.error(f"scope_id is invalid: {scope_id}")
            return False
        if "/" in scope_id:
            logger.error(f"scope_id cannot contain separator '/', scope_id={scope_id}")
            return False
        if len(scope_id) > 128:
            logger.error(f"scope_id length exceeds limit (128), scope_id={scope_id}")
            return False
        return True