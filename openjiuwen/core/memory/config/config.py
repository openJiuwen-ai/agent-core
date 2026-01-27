# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from pydantic import BaseModel, Field, field_validator
from openjiuwen.core.common.schema.param import Param
from openjiuwen.core.memory.common.crypto import AES_KEY_LENGTH
from openjiuwen.core.foundation.llm.schema.config import ModelClientConfig, ModelRequestConfig
from openjiuwen.core.retrieval.common.config import EmbeddingConfig
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error


class MemoryEngineConfig(BaseModel):
    default_model_cfg: ModelRequestConfig = Field(default=None)
    default_model_client_cfg: ModelClientConfig = Field(default=None)
    input_msg_max_len: int = Field(default=8192)  # max length of input message
    crypto_key: bytes = Field(default=b'')  # aes key, length must be 32, not enable encrypt memory if empty

    @field_validator('crypto_key')
    @classmethod
    def check_crypto_key(cls, v: bytes) -> bytes:
        if len(v) == 0:
            return b''

        if len(v) == AES_KEY_LENGTH:
            return v

        raise build_error(
            StatusCode.MEMORY_SET_CONFIG_EXECUTION_ERROR,
            config_type="crypto_key",
            error_msg=f"crypto_key must be empty or {AES_KEY_LENGTH} bytes length",
        )


class SummaryConfig(BaseModel):
    max_token: int = Field(default=128)
    fraction: float = Field(default=0.3)
    threshold: int = Field(default=0)

    @field_validator('max_token')
    @classmethod
    def check_max_token(cls, v: int) -> int:
        if v > 0:
            return v
        raise build_error(
            StatusCode.MEMORY_SET_CONFIG_EXECUTION_ERROR,
            config_type="max_token",
            error_msg=f"max_token must be positive, current value is {v}",
        )

    @field_validator('fraction')
    @classmethod
    def check_fraction(cls, v: float) -> float:
        if v < 0 or v > 1:
            raise build_error(
                StatusCode.MEMORY_SET_CONFIG_EXECUTION_ERROR,
                config_type="fraction",
                error_msg=f"fraction must be between 0 and 1, current value is {v}",
            )

        return v

    @field_validator('threshold')
    @classmethod
    def check_threshold(cls, v: int) -> int:
        if v < 0:
            raise build_error(
                StatusCode.MEMORY_SET_CONFIG_EXECUTION_ERROR,
                config_type="threshold",
                error_msg=f"threshold must be positive, current value is {v}",
            )
        return v


class MemoryScopeConfig(BaseModel):
    model_cfg: ModelRequestConfig = Field(default=None)
    model_client_cfg: ModelClientConfig = Field(default=None)
    embedding_cfg: EmbeddingConfig = Field(default=None)


class AgentMemoryConfig(BaseModel):
    mem_variables: list[Param] = Field(default_factory=list)  # memory variables config
    enable_long_term_mem: bool = Field(default=True)  # enable long term memory or not
    summary_config: SummaryConfig = Field(default_factory=SummaryConfig)  # summary config
