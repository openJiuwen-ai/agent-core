# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from abc import abstractmethod, ABC
from typing import Any, Tuple

from openjiuwen.core.common.logging import logger
from openjiuwen.core.utils.llm.base import BaseModelClient
from openjiuwen.core.memory.common.crypto import encrypt, decrypt, NONCE_LENGTH, TAG_LENGTH

from openjiuwen.core.memory.mem_unit.memory_unit import BaseMemoryUnit


class BaseMemoryManager(ABC):
    """
    Simplified abstract base class for memory manager implementations.
    Managing a specific type of memory data.
    """

    NONCE_HEX_LENGTH = NONCE_LENGTH * 2  # hex_length = bytes_length * 2
    TAG_HEX_LENGTH = TAG_LENGTH * 2  # hex_length = bytes_length * 2

    @abstractmethod
    async def add(self, memory: BaseMemoryUnit, llm: Tuple[str, BaseModelClient] | None = None):
        """add memory."""
        pass

    @abstractmethod
    async def update(self, user_id: str, group_id: str, mem_id: str, new_memory: str, **kwargs):
        """update memory by its id."""
        pass

    @abstractmethod
    async def delete(self, user_id: str, group_id: str, mem_id: str, **kwargs):
        """delete memory by its id."""
        pass

    @abstractmethod
    async def delete_by_user_id(self, user_id: str, group_id: str):
        """delete memory by user id and app id."""
        pass

    @abstractmethod
    async def get(self, user_id: str, group_id: str, mem_id: str) -> dict[str, Any] | None:
        """get memory by its id."""
        pass

    @abstractmethod
    async def search(self, user_id: str, group_id: str, query: str, top_k: int, **kwargs):
        """query memory, return top k results"""
        pass

    @staticmethod
    def encrypt_memory_if_needed(key: bytes, plaintext: str) -> str:
        if not key or not plaintext:
            return plaintext

        try:
            encrypt_memory, nonce, tag = encrypt(key=key, plaintext=plaintext)
            return f"{nonce}{tag}{encrypt_memory}"
        except ValueError as e:
            logger.warning(f"Encrypt exception occurred:{str(e)}")
            return ""
        except Exception as e:
            logger.warning(f"Encrypt error occurred:{str(e)}")
            return ""

    @staticmethod
    def decrypt_memory_if_needed(key: bytes, ciphertext: str) -> str:
        if not key or not ciphertext:
            return ciphertext

        nonce_and_tag_len = BaseMemoryManager.NONCE_HEX_LENGTH + BaseMemoryManager.TAG_HEX_LENGTH
        if len(ciphertext) < nonce_and_tag_len:
            logger.warning(f"Decryption error occurred: invalid ciphertext len{len(ciphertext)}")
            return ""

        nonce = ciphertext[0:BaseMemoryManager.NONCE_HEX_LENGTH]
        tag = ciphertext[BaseMemoryManager.NONCE_HEX_LENGTH:nonce_and_tag_len]
        encrypt_memory = ciphertext[nonce_and_tag_len:]
        try:
            return decrypt(key=key, ciphertext=encrypt_memory, nonce=nonce, tag=tag)
        except ValueError as e:
            logger.warning(f"Decrypt exception occurred:{str(e)}")
            return ""
        except Exception as e:
            logger.warning(f"Decrypt error occurred:{str(e)}")
            return ""
