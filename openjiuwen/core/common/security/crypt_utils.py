# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import secrets
import threading
from abc import ABC, abstractmethod
from typing import Optional

from Crypto.Cipher import AES

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.utils.singleton import Singleton

NONCE_LENGTH = 12
AES_KEY_LENGTH = 32
TAG_LENGTH = 16


class BaseCrypt(ABC):

    @abstractmethod
    def encrypt(self, key: bytes, origin: str) -> str:
        ...

    @abstractmethod
    def decrypt(self, key: bytes, encrypt_str: str) -> str:
        ...


class AesGcmCrypt(BaseCrypt, metaclass=Singleton):

    def __init__(self):
        CryptUtils.register_crypt(CryptUtils.AES_GCM_CRYPT_NAME, self)

    @staticmethod
    def _validate_key(key: bytes) -> None:
        if len(key) != AES_KEY_LENGTH:
            raise build_error(
                StatusCode.COMMON_ENCRYPTION_ERROR,
                error_msg=f"Key must be {AES_KEY_LENGTH} bytes, got {len(key)}",
            )

    def encrypt(self, key: bytes, origin: str) -> str:
        AesGcmCrypt._validate_key(key)
        nonce = secrets.token_bytes(NONCE_LENGTH)
        cipher = AES.new(key=key, mode=AES.MODE_GCM, nonce=nonce, mac_len=TAG_LENGTH)
        cipher_text, tag = cipher.encrypt_and_digest(origin.encode(encoding="utf-8"))
        return f"{nonce.hex()}{tag.hex()}{cipher_text.hex()}"

    def decrypt(self, key: bytes, encrypt_str: str) -> str:
        AesGcmCrypt._validate_key(key)
        nonce_len = NONCE_LENGTH * 2
        tag_len = TAG_LENGTH * 2
        min_len = nonce_len + tag_len
        if len(encrypt_str) < min_len:
            raise build_error(
                StatusCode.COMMON_DECRYPTION_ERROR,
                error_msg=(
                    f"Ciphertext too short: expected at least {min_len} chars, "
                    f"got {len(encrypt_str)}"
                ),
            )
        nonce_hex = encrypt_str[:nonce_len]
        tag_hex = encrypt_str[nonce_len:nonce_len + tag_len]
        ciphertext_hex = encrypt_str[min_len:]
        nonce_bytes = bytes.fromhex(nonce_hex)
        tag_bytes = bytes.fromhex(tag_hex)
        ciphertext_bytes = bytes.fromhex(ciphertext_hex)
        if len(nonce_bytes) != NONCE_LENGTH:
            raise build_error(
                StatusCode.COMMON_DECRYPTION_ERROR,
                error_msg=f"Wrong nonce length: {len(nonce_bytes)}",
            )
        if len(tag_bytes) != TAG_LENGTH:
            raise build_error(
                StatusCode.COMMON_DECRYPTION_ERROR,
                error_msg=f"Wrong tag length: {len(tag_bytes)}, expected {TAG_LENGTH}",
            )
        cipher = AES.new(key=key, mode=AES.MODE_GCM, nonce=nonce_bytes)
        plaintext_bytes = cipher.decrypt_and_verify(
            ciphertext=ciphertext_bytes, received_mac_tag=tag_bytes
        )
        return plaintext_bytes.decode(encoding="utf-8")


class CryptUtils:
    AES_GCM_CRYPT_NAME = "aes_gcm"

    _CRYPT_REGISTRY: dict[str, BaseCrypt] = {}
    _registry_lock = threading.Lock()

    @staticmethod
    def register_crypt(name: str, crypt: BaseCrypt) -> None:
        if not isinstance(crypt, BaseCrypt):
            raise build_error(
                StatusCode.COMMON_ENCRYPTION_ERROR,
                error_msg=f"crypt must be a BaseCrypt instance, got {type(crypt)}",
            )
        with CryptUtils._registry_lock:
            CryptUtils._CRYPT_REGISTRY[name] = crypt

    @staticmethod
    def unregister_crypt(name: str) -> None:
        with CryptUtils._registry_lock:
            CryptUtils._CRYPT_REGISTRY.pop(name, None)

    @staticmethod
    def get_crypt(name: str) -> Optional[BaseCrypt]:
        with CryptUtils._registry_lock:
            return CryptUtils._CRYPT_REGISTRY.get(name)


AesGcmCrypt()
