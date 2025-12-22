# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import Optional, Union

from pydantic import BaseModel, Field


class ModelClientConfig(BaseModel):
    """ModelClient config"""
    client_id: str = Field(..., description="The ModelClient client ID is a unique identifier used for registration in the Runner")
    client_type: str = Field(..., description="Service provider identification，Enumeration value：OpenAI、SiliconFlow")
    api_key: str = Field(..., description="API key")
    api_base: str = Field(..., description="API base URL")
    timeout: int = Field(default=60, description="Request timeout in seconds")
    max_retries: int = Field(default=3, description="Maximum number of retries")
    verify_ssl: bool = Field(default=True, description="Whether to verify SSL certificates")
    ssl_cert: Optional[str] = Field(default=None, description="Path to SSL certificate file")


class ModelConfig(BaseModel):
    """Model config"""
    model_name: str = Field(default="", alias="model", description="Model name, e.g. gpt-4")
    temperature: float = Field(default=0.95, description="Temperature parameter, controlling the randomness of outputs")
    top_p: float = Field(default=0.1, description="Top-p sampling parameter")
    max_tokens: Optional[int] = Field(default=None, description="Maximum number of tokens to generate")
    stop: Union[Optional[str], None] = Field(default=None, description="Stop sequence")
    model_config = {"extra": "allow"}
