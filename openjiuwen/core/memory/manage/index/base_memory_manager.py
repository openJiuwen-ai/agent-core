# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from abc import abstractmethod, ABC
from typing import Any

from openjiuwen.core.foundation.llm import Model

from openjiuwen.core.memory.manage.mem_model.memory_unit import BaseMemoryUnit
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError, build_error, raise_error
from openjiuwen.core.common.logging import memory_logger
from openjiuwen.core.common.logging.events import LogEventType


class BaseMemoryManager(ABC):
    """
    Simplified abstract base class for memory manager implementations.
    Managing a specific type of memory data.
    """

    def _validate_required_params(
        self,
        user_id: str,
        scope_id: str,
        memory_index: Any,
        status_code: StatusCode,
        memory_type: str,
    ) -> None:
        """
        Validate required parameters for memory operations.
        Raises BaseError if any required parameter is missing.

        Args:
            user_id: User identifier, must not be empty
            scope_id: Scope identifier, must not be empty
            memory_index: Memory index instance, must not be None
            status_code: StatusCode to use for error reporting
            memory_type: Memory type for error context
        """
        if not user_id:
            raise build_error(
                status_code,
                memory_type=memory_type,
                error_msg="user_id is required",
            )
        if not scope_id:
            raise build_error(
                status_code,
                memory_type=memory_type,
                error_msg="scope_id is required",
            )
        if not memory_index:
            raise build_error(
                status_code,
                memory_type=memory_type,
                error_msg="memory_index is not initialized",
            )

    def _wrap_exception(
        self,
        e: Exception,
        status_code: StatusCode,
        memory_type: str,
    ) -> None:
        """
        Wrap exception into unified BaseError.
        If the exception is already a BaseError, re-raise it directly.

        Args:
            e: Original exception
            status_code: StatusCode to use for error reporting
            memory_type: Memory type for error context
        """
        if isinstance(e, BaseError):
            raise e
        raise_error(
            status_code,
            memory_type=memory_type,
            error_msg=str(e),
            cause=e,
        )

    @abstractmethod
    async def add_memories(self, user_id: str, scope_id: str, memories: dict[str, list[BaseMemoryUnit]],
                           llm: Model | None = None, **kwargs):
        """add memories in batch."""
        pass

    @abstractmethod
    async def update(self, user_id: str, scope_id: str, mem_id: str, new_memory: str, **kwargs):
        """update memory by its id."""
        pass

    @abstractmethod
    async def delete(self, user_id: str, scope_id: str, mem_id: str, **kwargs):
        """delete memory by its id."""
        pass

    @abstractmethod
    async def delete_by_user_id(self, user_id: str, scope_id: str, **kwargs):
        """delete memory by user id and app id."""
        pass

    @abstractmethod
    async def get(self, user_id: str, scope_id: str, mem_id: str) -> dict[str, Any] | None:
        """get memory by its id."""
        pass

    @abstractmethod
    async def search(self, user_id: str, scope_id: str, query: str, top_k: int, **kwargs):
        """query memory, return top k results"""
        pass

    @staticmethod
    def encrypt_memory_if_needed(key: bytes, plaintext: str) -> str:
        if not key or not plaintext:
            return plaintext

        from openjiuwen.core.common.security.crypt_utils import CryptUtils

        crypt = CryptUtils.get_crypt(CryptUtils.AES_GCM_CRYPT_NAME)
        if not crypt:
            return plaintext

        try:
            return crypt.encrypt(key, plaintext)
        except Exception as e:
            memory_logger.warning(
                "Encrypt error via crypt",
                exception=str(e),
                event_type=LogEventType.MEMORY_PROCESS,
            )
            return plaintext

    @staticmethod
    def decrypt_memory_if_needed(key: bytes, ciphertext: str) -> str:
        if not key or not ciphertext:
            return ciphertext

        from openjiuwen.core.common.security.crypt_utils import CryptUtils

        crypt = CryptUtils.get_crypt(CryptUtils.AES_GCM_CRYPT_NAME)
        if not crypt:
            return ciphertext

        try:
            return crypt.decrypt(key, ciphertext)
        except Exception as e:
            memory_logger.warning(
                "Decrypt error via crypt",
                exception=str(e),
                event_type=LogEventType.MEMORY_PROCESS,
            )
            return ciphertext
