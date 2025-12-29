#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import pytest
from openjiuwen.core.memory.common.crypto import encrypt, decrypt


class TestCrypto:
    @staticmethod
    def test_encrypt():
        test_key = b'1234567890abcdef1234567890123456'
        test_data = "hello, 我叫张三, xixi"
        encrypt_data, nonce, tag = encrypt(test_key, test_data)
        decrypt_data = decrypt(test_key, encrypt_data, nonce, tag)
        assert decrypt_data == test_data

    @staticmethod
    def test_key_error():
        test_key = b'1234567890abcedfgg'
        test_data = "你好, 我叫李四"
        with pytest.raises(ValueError):
            encrypt(test_key, test_data)
