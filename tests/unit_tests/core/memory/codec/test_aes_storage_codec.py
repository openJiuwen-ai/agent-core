# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import pytest
from unittest.mock import MagicMock

from openjiuwen.core.common.security.crypt_utils import (
    AesGcmCrypt,
    CryptUtils,
)
from openjiuwen.core.common.utils.singleton import Singleton
from openjiuwen.core.memory.codec.aes_storage_codec import AesStorageCodec


_VALID_KEY = b"0123456789abcdef0123456789abcdef"
_OTHER_KEY = b"abcdef0123456789abcdef0123456789"


@pytest.fixture(autouse=True)
def _clean_crypt_registry():
    Singleton._instances.pop(AesGcmCrypt, None)
    CryptUtils._CRYPT_REGISTRY.clear()
    yield
    Singleton._instances.pop(AesGcmCrypt, None)
    CryptUtils._CRYPT_REGISTRY.clear()


def _register_aes_gcm():
    crypt = AesGcmCrypt()
    CryptUtils.register_crypt(CryptUtils.AES_GCM_CRYPT_NAME, crypt)
    return crypt


class TestAesStorageCodecBasic:
    def test_encode_decode_roundtrip(self):
        _register_aes_gcm()
        codec = AesStorageCodec(_VALID_KEY)
        plaintext = "Hello, Memory!"
        encrypted = codec.encode(plaintext)
        assert encrypted != plaintext
        decrypted = codec.decode(encrypted)
        assert decrypted == plaintext

    def test_encode_decode_unicode(self):
        _register_aes_gcm()
        codec = AesStorageCodec(_VALID_KEY)
        plaintext = "中文测试 🎉 émojis"
        encrypted = codec.encode(plaintext)
        decrypted = codec.decode(encrypted)
        assert decrypted == plaintext

    def test_encode_long_text(self):
        _register_aes_gcm()
        codec = AesStorageCodec(_VALID_KEY)
        plaintext = "A" * 10000
        encrypted = codec.encode(plaintext)
        decrypted = codec.decode(encrypted)
        assert decrypted == plaintext

    def test_encode_without_key(self):
        codec = AesStorageCodec(b"")
        plaintext = "visible data"
        result = codec.encode(plaintext)
        assert result == plaintext

    def test_decode_without_key(self):
        codec = AesStorageCodec(b"")
        ciphertext = "some ciphertext"
        result = codec.decode(ciphertext)
        assert result == ciphertext

    def test_encode_empty_string(self):
        _register_aes_gcm()
        codec = AesStorageCodec(_VALID_KEY)
        assert codec.encode("") == ""

    def test_decode_empty_string(self):
        _register_aes_gcm()
        codec = AesStorageCodec(_VALID_KEY)
        assert codec.decode("") == ""

    def test_encode_without_crypt_registered(self):
        codec = AesStorageCodec(_VALID_KEY)
        plaintext = "fallback test"
        result = codec.encode(plaintext)
        assert result == plaintext

    def test_decode_without_crypt_registered(self):
        codec = AesStorageCodec(_VALID_KEY)
        ciphertext = "some ciphertext"
        result = codec.decode(ciphertext)
        assert result == ciphertext

    def test_different_keys_incompatible(self):
        _register_aes_gcm()
        codec_a = AesStorageCodec(_VALID_KEY)
        codec_b = AesStorageCodec(_OTHER_KEY)
        plaintext = "secret message"
        encrypted = codec_a.encode(plaintext)
        result = codec_b.decode(encrypted)
        assert result == encrypted

    def test_encode_produces_different_output(self):
        _register_aes_gcm()
        codec = AesStorageCodec(_VALID_KEY)
        plaintext = "same text"
        enc1 = codec.encode(plaintext)
        enc2 = codec.encode(plaintext)
        assert enc1 != enc2
        assert codec.decode(enc1) == plaintext
        assert codec.decode(enc2) == plaintext

    def test_encode_output_is_hex_string(self):
        _register_aes_gcm()
        codec = AesStorageCodec(_VALID_KEY)
        plaintext = "test"
        encrypted = codec.encode(plaintext)
        assert all(c in "0123456789abcdef" for c in encrypted)


class TestAesStorageCodecExceptionFallback:
    def test_encode_encrypt_exception_fallback(self):
        crypt = _register_aes_gcm()
        original_encrypt = crypt.encrypt
        crypt.encrypt = MagicMock(side_effect=RuntimeError("encrypt failure"))
        try:
            codec = AesStorageCodec(_VALID_KEY)
            plaintext = "fallback on error"
            result = codec.encode(plaintext)
            assert result == plaintext
        finally:
            crypt.encrypt = original_encrypt

    def test_decode_decrypt_exception_fallback(self):
        crypt = _register_aes_gcm()
        original_decrypt = crypt.decrypt
        crypt.decrypt = MagicMock(side_effect=RuntimeError("decrypt failure"))
        try:
            codec = AesStorageCodec(_VALID_KEY)
            ciphertext = "some hex ciphertext"
            result = codec.decode(ciphertext)
            assert result == ciphertext
        finally:
            crypt.decrypt = original_decrypt
