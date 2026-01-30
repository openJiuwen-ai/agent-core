# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import uuid
from enum import Enum
from typing import Optional, Union, Any, Self

from pydantic import BaseModel, Field
from pydantic.config import ExtraValues

from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.exception.codes import StatusCode


class ProviderType(str, Enum):
    """ModelClientProvider type"""
    OpenAI = "OpenAI"
    SiliconFlow = "SiliconFlow"


class ModelClientConfig(BaseModel):
    """ModelClient config"""
    client_id: str = Field(default_factory=lambda: str(uuid.uuid4()),
        description="The ModelClient client ID is a unique identifier used for registration in the Runner")
    client_provider: Union[ProviderType, str] = Field(
        ...,
        description="Service provider identification, Enumeration value: OpenAI or SiliconFlow"
    )
    api_key: str = Field(..., description="API key")
    api_base: str = Field(..., description="API base URL")
    timeout: float = Field(default=60.0, gt=0, description="Request timeout in seconds (must be greater than 0)")
    max_retries: int = Field(default=3, description="Maximum number of retries")
    verify_ssl: bool = Field(default=True, description="Whether to verify SSL certificates")
    ssl_cert: Optional[str] = Field(default=None, description="Path to SSL certificate file")

    @classmethod
    def model_validate(
        cls,
        obj: Any,
        *,
        strict: bool | None = None,
        extra: ExtraValues | None = None,
        from_attributes: bool | None = None,
        context: Any | None = None,
        by_alias: bool | None = None,
        by_name: bool | None = None,
    ) -> Self:
        cfg = super().model_validate(
            obj,
            strict=strict,
            extra=extra,
            from_attributes=from_attributes,
            context=context,
            by_alias=by_alias,
            by_name=by_name,
        )
        if isinstance(cfg.client_provider, str):
            from openjiuwen.core.foundation.llm.model import _CLIENT_TYPE_REGISTRY
            if cfg.client_provider not in _CLIENT_TYPE_REGISTRY:
                raise build_error(
                    StatusCode.MODEL_CLIENT_CONFIG_INVALID,
                    error_msg=f"client_provider '{cfg.client_provider}' is not registered",
                )
        return cfg


class ModelRequestConfig(BaseModel):
    """Model config"""
    model_name: str = Field(default="", alias="model", description="Model name, e.g. gpt-4")
    temperature: float = Field(default=0.95, description="Temperature parameter, controlling the randomness of outputs")
    top_p: float = Field(default=0.1, description="Top-p sampling parameter")
    max_tokens: Optional[int] = Field(default=None, description="Maximum number of tokens to generate")
    stop: Union[Optional[str], None] = Field(default=None, description="Stop sequence")
    model_config = {"extra": "allow"}
