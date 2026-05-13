# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import pytest
from unittest.mock import patch, MagicMock

from openjiuwen.core.common.security.crypt_utils import (
    AesGcmCrypt,
    BaseCrypt,
    CryptUtils,
)
from openjiuwen.core.common.utils.singleton import Singleton
from openjiuwen.core.memory.manage.index.base_memory_manager import BaseMemoryManager


@pytest.fixture(autouse=True)
def _clean_global_state():
    Singleton._instances.pop(AesGcmCrypt, None)
    CryptUtils._CRYPT_REGISTRY.clear()
    yield
    Singleton._instances.pop(AesGcmCrypt, None)
    CryptUtils._CRYPT_REGISTRY.clear()


_VALID_KEY = b"0123456789abcdef0123456789abcdef"
_OTHER_KEY = b"abcdef0123456789abcdef0123456789"


class TestMemoryEncryptDecrypt:

    def test_encrypt_decrypt_via_crypt(self):
        crypt = AesGcmCrypt()
        CryptUtils.register_crypt(CryptUtils.AES_GCM_CRYPT_NAME, crypt)

        plaintext = "Hello, Memory!"
        encrypted = BaseMemoryManager.encrypt_memory_if_needed(_VALID_KEY, plaintext)

        assert encrypted != plaintext
        assert all(c in "0123456789abcdef" for c in encrypted)

        decrypted = BaseMemoryManager.decrypt_memory_if_needed(_VALID_KEY, encrypted)
        assert decrypted == plaintext

    def test_encrypt_returns_plaintext_without_key(self):
        plaintext = "visible data"
        result = BaseMemoryManager.encrypt_memory_if_needed(b"", plaintext)
        assert result == plaintext

    def test_decrypt_returns_ciphertext_without_key(self):
        ciphertext = "some ciphertext"
        result = BaseMemoryManager.decrypt_memory_if_needed(b"", ciphertext)
        assert result == ciphertext

    def test_encrypt_empty_string(self):
        crypt = AesGcmCrypt()
        CryptUtils.register_crypt(CryptUtils.AES_GCM_CRYPT_NAME, crypt)
        assert BaseMemoryManager.encrypt_memory_if_needed(_VALID_KEY, "") == ""

    def test_decrypt_empty_string(self):
        assert BaseMemoryManager.decrypt_memory_if_needed(_VALID_KEY, "") == ""

    def test_encrypt_returns_plaintext_without_crypt(self):
        plaintext = "fallback test"
        result = BaseMemoryManager.encrypt_memory_if_needed(_VALID_KEY, plaintext)
        assert result == plaintext

    def test_decrypt_returns_ciphertext_without_crypt(self):
        crypt = AesGcmCrypt()
        CryptUtils.register_crypt(CryptUtils.AES_GCM_CRYPT_NAME, crypt)

        encrypted = BaseMemoryManager.encrypt_memory_if_needed(_VALID_KEY, "test data")
        CryptUtils.unregister_crypt(CryptUtils.AES_GCM_CRYPT_NAME)

        result = BaseMemoryManager.decrypt_memory_if_needed(_VALID_KEY, encrypted)
        assert result == encrypted

    def test_encrypt_unicode(self):
        crypt = AesGcmCrypt()
        CryptUtils.register_crypt(CryptUtils.AES_GCM_CRYPT_NAME, crypt)

        plaintext = "中文测试 🎉 émojis"
        encrypted = BaseMemoryManager.encrypt_memory_if_needed(_VALID_KEY, plaintext)
        decrypted = BaseMemoryManager.decrypt_memory_if_needed(_VALID_KEY, encrypted)
        assert decrypted == plaintext

    def test_decrypt_legacy_ciphertext_with_crypt_registered(self):
        crypt = AesGcmCrypt()
        legacy_ciphertext = crypt.encrypt(_VALID_KEY, "legacy data")

        CryptUtils.register_crypt(CryptUtils.AES_GCM_CRYPT_NAME, crypt)

        decrypted = BaseMemoryManager.decrypt_memory_if_needed(_VALID_KEY, legacy_ciphertext)
        assert decrypted == "legacy data"

    def test_encrypt_no_key_no_crypt(self):
        result = BaseMemoryManager.encrypt_memory_if_needed(b"", "plaintext")
        assert result == "plaintext"

    def test_decrypt_no_key_no_crypt(self):
        result = BaseMemoryManager.decrypt_memory_if_needed(b"", "ciphertext")
        assert result == "ciphertext"

    def test_encrypt_decrypt_with_different_keys_fails(self):
        crypt = AesGcmCrypt()
        CryptUtils.register_crypt(CryptUtils.AES_GCM_CRYPT_NAME, crypt)

        encrypted = BaseMemoryManager.encrypt_memory_if_needed(_VALID_KEY, "secret")

        with pytest.raises(Exception):
            crypt.decrypt(_OTHER_KEY, encrypted)

    @patch("openjiuwen.core.memory.manage.index.base_memory_manager.memory_logger")
    def test_encrypt_exception_returns_plaintext(self, mock_logger):
        mock_crypt = MagicMock(spec=BaseCrypt)
        mock_crypt.encrypt.side_effect = RuntimeError("encrypt boom")
        CryptUtils.register_crypt(CryptUtils.AES_GCM_CRYPT_NAME, mock_crypt)

        plaintext = "safe plaintext"
        result = BaseMemoryManager.encrypt_memory_if_needed(_VALID_KEY, plaintext)
        assert result == plaintext
        mock_logger.warning.assert_called_once()

    @patch("openjiuwen.core.memory.manage.index.base_memory_manager.memory_logger")
    def test_decrypt_exception_returns_ciphertext(self, mock_logger):
        mock_crypt = MagicMock(spec=BaseCrypt)
        mock_crypt.decrypt.side_effect = RuntimeError("decrypt boom")
        CryptUtils.register_crypt(CryptUtils.AES_GCM_CRYPT_NAME, mock_crypt)

        ciphertext = 'aabbccdd' * 10
        result = BaseMemoryManager.decrypt_memory_if_needed(_VALID_KEY, ciphertext)
        assert result == ciphertext
        mock_logger.warning.assert_called()


class TestFullEncryptionWorkflow:

    def test_full_encryption_workflow(self):
        crypt = AesGcmCrypt()
        CryptUtils.register_crypt(CryptUtils.AES_GCM_CRYPT_NAME, crypt)

        mem_plaintext = "important memory data"
        encrypted = BaseMemoryManager.encrypt_memory_if_needed(_VALID_KEY, mem_plaintext)
        assert encrypted != mem_plaintext

        decrypted = BaseMemoryManager.decrypt_memory_if_needed(_VALID_KEY, encrypted)
        assert decrypted == mem_plaintext

        config_plaintext = "config secret"
        raw_cipher = crypt.encrypt(_VALID_KEY, config_plaintext)
        assert crypt.decrypt(_VALID_KEY, raw_cipher) == config_plaintext

    def test_key_based_toggle(self):
        crypt = AesGcmCrypt()
        CryptUtils.register_crypt(CryptUtils.AES_GCM_CRYPT_NAME, crypt)

        plaintext = "toggle test"
        encrypted = BaseMemoryManager.encrypt_memory_if_needed(_VALID_KEY, plaintext)
        assert encrypted != plaintext

        decrypted = BaseMemoryManager.decrypt_memory_if_needed(_VALID_KEY, encrypted)
        assert decrypted == plaintext

        result = BaseMemoryManager.encrypt_memory_if_needed(b"", "new data")
        assert result == "new data"

        still_decrypted = BaseMemoryManager.decrypt_memory_if_needed(_VALID_KEY, encrypted)
        assert still_decrypted == plaintext

        new_encrypted = BaseMemoryManager.encrypt_memory_if_needed(_VALID_KEY, "more data")
        new_decrypted = BaseMemoryManager.decrypt_memory_if_needed(_VALID_KEY, new_encrypted)
        assert new_decrypted == "more data"

    def test_legacy_data_compatibility(self):
        crypt = AesGcmCrypt()
        CryptUtils.register_crypt(CryptUtils.AES_GCM_CRYPT_NAME, crypt)

        legacy_plaintext = "old format data"
        legacy_ciphertext = crypt.encrypt(_VALID_KEY, legacy_plaintext)

        legacy_decrypted = BaseMemoryManager.decrypt_memory_if_needed(_VALID_KEY, legacy_ciphertext)
        assert legacy_decrypted == legacy_plaintext

        new_plaintext = "new format data"
        new_encrypted = BaseMemoryManager.encrypt_memory_if_needed(_VALID_KEY, new_plaintext)

        new_decrypted = BaseMemoryManager.decrypt_memory_if_needed(_VALID_KEY, new_encrypted)
        assert new_decrypted == new_plaintext

        assert new_encrypted != new_plaintext
        assert legacy_ciphertext.startswith('{"') is False
