# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from openjiuwen.core.common.security.crypt_utils import CryptUtils
from openjiuwen.core.common.logging import memory_logger
from openjiuwen.core.common.logging.events import LogEventType


class AesStorageCodec:
    """AES-256-GCM storage codec implementing the StorageCodec protocol.

    Injected into :class:`BaseMemoryIndex` subclasses via
    :meth:`BaseMemoryIndex.set_storage_codec` to transparently
    encrypt/decrypt the ``text`` field of memory documents.

    When ``key`` is empty, ``encode`` and ``decode`` pass through
    as plain text, matching the no-codec default behavior.
    """

    def __init__(self, key: bytes):
        self._key = key

    def encode(self, text: str) -> str:
        if not self._key or not text:
            return text

        crypt = CryptUtils.get_crypt(CryptUtils.AES_GCM_CRYPT_NAME)
        if not crypt:
            return text

        try:
            return crypt.encrypt(self._key, text)
        except Exception as e:
            memory_logger.warning(
                "Encrypt error via crypt",
                exception=str(e),
                event_type=LogEventType.MEMORY_PROCESS,
            )
            return text

    def decode(self, data: str) -> str:
        if not self._key or not data:
            return data

        crypt = CryptUtils.get_crypt(CryptUtils.AES_GCM_CRYPT_NAME)
        if not crypt:
            return data

        try:
            return crypt.decrypt(self._key, data)
        except Exception as e:
            memory_logger.warning(
                "Decrypt error via crypt",
                exception=str(e),
                event_type=LogEventType.MEMORY_PROCESS,
            )
            return data
