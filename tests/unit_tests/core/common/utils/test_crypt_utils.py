# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import threading

import pytest
from unittest.mock import MagicMock

from openjiuwen.core.common.security.crypt_utils import (
    AesGcmCrypt,
    BaseCrypt,
    CryptUtils,
)
from openjiuwen.core.common.utils.singleton import Singleton


@pytest.fixture(autouse=True)
def _clean_global_state():
    Singleton._instances.pop(AesGcmCrypt, None)
    CryptUtils._CRYPT_REGISTRY.clear()
    yield
    Singleton._instances.pop(AesGcmCrypt, None)
    CryptUtils._CRYPT_REGISTRY.clear()


_VALID_KEY = b"0123456789abcdef0123456789abcdef"
_OTHER_KEY = b"abcdef0123456789abcdef0123456789"


class TestAesGcmCrypt:

    def test_encrypt_decrypt_roundtrip(self):
        crypt = AesGcmCrypt()
        plaintext = "hello, world! 你好世界"
        encrypted = crypt.encrypt(_VALID_KEY, plaintext)
        assert encrypted != plaintext
        assert crypt.decrypt(_VALID_KEY, encrypted) == plaintext

    def test_encrypt_produces_different_ciphertext(self):
        crypt = AesGcmCrypt()
        e1 = crypt.encrypt(_VALID_KEY, "same text")
        e2 = crypt.encrypt(_VALID_KEY, "same text")
        assert e1 != e2

    def test_invalid_key_length(self):
        crypt = AesGcmCrypt()
        with pytest.raises(Exception, match="Key must be 32 bytes"):
            crypt.encrypt(b"short", "test")

    def test_decrypt_invalid_key_length(self):
        crypt = AesGcmCrypt()
        with pytest.raises(Exception, match="Key must be 32 bytes"):
            crypt.decrypt(b"short", "some_ciphertext")

    def test_decrypt_garbage_returns_error(self):
        crypt = AesGcmCrypt()
        with pytest.raises(Exception):
            crypt.decrypt(_VALID_KEY, "not_valid_ciphertext")

    def test_empty_string_encrypt_decrypt(self):
        crypt = AesGcmCrypt()
        encrypted = crypt.encrypt(_VALID_KEY, "")
        assert crypt.decrypt(_VALID_KEY, encrypted) == ""

    def test_decrypt_too_short(self):
        crypt = AesGcmCrypt()
        with pytest.raises(Exception, match="Ciphertext too short"):
            crypt.decrypt(_VALID_KEY, "ab")

    def test_encrypt_with_different_keys(self):
        crypt = AesGcmCrypt()
        encrypted = crypt.encrypt(_VALID_KEY, "cross key test")
        with pytest.raises(Exception):
            crypt.decrypt(_OTHER_KEY, encrypted)

    def test_decrypt_with_correct_key(self):
        crypt = AesGcmCrypt()
        encrypted = crypt.encrypt(_OTHER_KEY, "key specific")
        assert crypt.decrypt(_OTHER_KEY, encrypted) == "key specific"


class TestCryptRegistry:

    def test_register_and_get(self):
        crypt = AesGcmCrypt()
        CryptUtils.register_crypt("test", crypt)
        assert CryptUtils.get_crypt("test") is crypt

    def test_register_non_bascrypt_raises(self):
        with pytest.raises(Exception, match="must be a BaseCrypt"):
            CryptUtils.register_crypt("bad", "not_a_crypt")

    def test_unregister(self):
        crypt = AesGcmCrypt()
        CryptUtils.register_crypt("test", crypt)
        CryptUtils.unregister_crypt("test")
        assert CryptUtils.get_crypt("test") is None

    def test_get_crypt_none(self):
        assert CryptUtils.get_crypt("nonexistent") is None

    def test_register_multiple(self):
        c1 = AesGcmCrypt()
        c2 = AesGcmCrypt()
        CryptUtils.register_crypt("c1", c1)
        CryptUtils.register_crypt("c2", c2)
        assert CryptUtils.get_crypt("c1") is c1
        assert CryptUtils.get_crypt("c2") is c2

    def test_overwrite_registration(self):
        c1 = AesGcmCrypt()
        c2 = AesGcmCrypt()
        CryptUtils.register_crypt("same", c1)
        CryptUtils.register_crypt("same", c2)
        assert CryptUtils.get_crypt("same") is c2

    def test_concurrent_register_unregister(self):
        errors = []

        def register_worker(name, idx):
            try:
                mock_crypt = MagicMock(spec=BaseCrypt)
                CryptUtils.register_crypt(name, mock_crypt)
            except Exception as e:
                errors.append(e)

        def unregister_worker(name):
            try:
                CryptUtils.unregister_crypt(name)
            except Exception as e:
                errors.append(e)

        threads = []
        for i in range(5):
            threads.append(threading.Thread(target=register_worker, args=("concurrent_test", i)))
            threads.append(threading.Thread(target=unregister_worker, args=("concurrent_test",)))
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        result = CryptUtils.get_crypt("concurrent_test")
        assert result is None or isinstance(result, BaseCrypt)

    def test_singleton_concurrent_creation(self):
        results = []
        errors = []

        def create_worker():
            try:
                instance = AesGcmCrypt()
                results.append(instance)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=create_worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert all(r is results[0] for r in results)


class TestInitCrypt:

    def test_init_crypt(self):
        crypt = AesGcmCrypt()
        CryptUtils.register_crypt("aes_gcm", crypt)
        assert CryptUtils.get_crypt("aes_gcm") is crypt
