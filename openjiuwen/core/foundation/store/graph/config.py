# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
Graph Store Configuration

Configuration models for graph store settings
"""

import os.path
import socket
from typing import Any, Dict, Mapping, Type, Union

from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_core import PydanticCustomError

from openjiuwen.core.common.logging import store_logger
from openjiuwen.core.foundation.store.base_embedding import Embedding, EmbeddingConfig

from .database_config import GraphStoreIndexConfig, GraphStoreStorageConfig


class GraphConfig(BaseModel):
    """Configuration of Graph Store"""

    uri: str
    name: str = Field(default="")
    user: str = Field(default="")
    password: str = Field(default="")
    token: str = Field(default="")
    backend: str = Field(default="milvus")
    timeout: Union[int, float] = Field(default=15.0, gt=0)
    extras: dict = Field(default_factory=dict)
    worker_threads: int = Field(default=30, ge=0)
    embed_dim: int = Field(default=512, ge=32)
    embed_batch_size: int = Field(default=10, ge=1)
    embedding_cls: Type[Embedding]
    embedding_config: EmbeddingConfig
    db_storage_config: GraphStoreStorageConfig = Field(default_factory=GraphStoreStorageConfig)
    db_embed_config: GraphStoreIndexConfig = Field(default_factory=GraphStoreIndexConfig)
    wipe_at_startup: bool = Field(default=False)
    request_max_retries: int = Field(
        default=5, description="Max number of retries for sending embedding / chat completion requests"
    )
    request_retry_wait: float = Field(
        default=0.1, description="Wait between retries for sending embedding / chat completion requests (second) "
    )

    @field_validator("extras", mode="before")
    @classmethod
    def check_extras(cls, value: Union[Any, Dict]):
        """Check the extras field"""
        if isinstance(value, Mapping) and all(isinstance(k, str) for k in value.keys()):
            return value
        raise PydanticCustomError(
            "extras_not_valid_dict",
            "Extras must be a dictionary with string keys.",
            context=dict(extras=value),
        )

    @model_validator(mode="after")
    def check_validity(self):
        """Check if configuration is valid"""
        uri_is_file_path = "://" not in self.uri
        if uri_is_file_path:
            file_dir = os.path.dirname(self.uri)
            if isinstance(file_dir, str) and file_dir.strip("."):
                try:
                    os.makedirs(file_dir, exist_ok=True)
                except OSError:
                    store_logger.warning("Failed to create parent directory for graph db uri: %r", file_dir)
        else:
            try:
                cleaned_uri = self.uri.split("//")[-1]
                with socket.create_connection(cleaned_uri.split(":"), timeout=self.timeout):
                    return self
            except Exception as e:
                store_logger.error(
                    "Graph DB config uri did not respond within %g seconds: %r",
                    self.timeout,
                    e,
                )
        return self
