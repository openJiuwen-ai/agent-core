# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from abc import abstractmethod, ABC
from typing import Any

from openjiuwen.core.foundation.llm import Model

from openjiuwen.core.memory.manage.mem_model.memory_unit import BaseMemoryUnit
from openjiuwen.core.common.logging import memory_logger
from openjiuwen.core.common.logging.events import LogEventType


class BaseMemoryManager(ABC):
    """
    Simplified abstract base class for memory manager implementations.
    Managing a specific type of memory data.
    """

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
