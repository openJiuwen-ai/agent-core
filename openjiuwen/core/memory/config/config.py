# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from pydantic import BaseModel, Field, field_validator
from openjiuwen.core.memory.common.crypto import AES_KEY_LENGTH


class SysMemConfig(BaseModel):
    record_message: bool = Field(default=True)  # record message or not
    ai_msg_gen_max_len: int = Field(default=256)  # max length of AI message generation memory
    history_window_size_to_gen_mem: int = Field(default=5)  # history window size to generate memory
    crypto_key: bytes = Field(default=b'')  # aes key, length must be 32, not enable encrypt memory if empty

    @field_validator('crypto_key')
    @classmethod
    def check_crypto_key(cls, v: bytes) -> bytes:
        if len(v) == 0:
            return b''

        if len(v) == AES_KEY_LENGTH:
            return v

        raise ValueError(f"Invalid crypto_key, must be empty or {AES_KEY_LENGTH} bytes length")


class MemoryConfig(BaseModel):
    mem_variables: dict[str, str] = Field(default_factory=dict)  # memory variables config
    enable_long_term_mem: bool = Field(default=True)  # enable long term memory or not
