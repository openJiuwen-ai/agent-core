# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from typing import Optional, List, Union

from openjiuwen.core.foundation.llm.schema.message import UserMessage, BaseMessage
from openjiuwen.core.foundation.llm.model_clients.openai_model_client import OpenAIModelClient
from openjiuwen.core.foundation.llm.schema.config import ModelClientConfig, ModelRequestConfig, ProviderType
from openjiuwen.core.foundation.llm.schema.generation_response import (
    ImageGenerationResponse,
    AudioGenerationResponse,
    VideoGenerationResponse
)


class DeepSeekModelClient(OpenAIModelClient):
    """DeepSeek Model Client"""
    __client_name__ = ProviderType.DeepSeek.value

    def __init__(self, model_config: ModelRequestConfig, model_client_config: ModelClientConfig):
        super().__init__(model_config, model_client_config)

    def _get_client_name(self) -> str:
        """Get client name."""
        return "DeepSeek client"

    @classmethod
    def _convert_messages_to_dict(cls, messages: Union[str, List[BaseMessage], List[dict]]) -> List[dict]:
        new_messages = super()._convert_messages_to_dict(messages=messages)
        for msg in new_messages:
            if msg.get("role") == "assistant" and msg.get("tool_calls") and "reasoning_content" not in msg:
                msg["reasoning_content"] = ""
            if msg.get("role") == "assistant" and "reasoning_content" not in msg:
                msg["reasoning_content"] = ""
        return new_messages



    async def generate_image(
            self,
            messages: List[UserMessage],
            *,
            model: Optional[str] = None,
            size: Optional[str] = "1664*928",
            negative_prompt: Optional[str] = None,
            n: Optional[int] = 1,
            prompt_extend: bool = True,
            watermark: bool = False,
            seed: int = 0,
            **kwargs
    ) -> ImageGenerationResponse:
        pass

    async def generate_speech(
            self,
            messages: List[UserMessage],
            *,
            model: Optional[str] = None,
            voice: Optional[str] = "Cherry",
            language_type: Optional[str] = "Auto",
            **kwargs
    ) -> AudioGenerationResponse:
        pass

    async def generate_video(
            self,
            messages: List[UserMessage],
            *,
            img_url: Optional[str] = None,
            audio_url: Optional[str] = None,
            model: Optional[str] = None,
            size: Optional[str] = None,
            resolution: Optional[str] = None,
            duration: Optional[int] = 5,
            prompt_extend: bool = True,
            watermark: bool = False,
            negative_prompt: Optional[str] = None,
            seed: Optional[int] = None,
            **kwargs
    ) -> VideoGenerationResponse:
        """Generate video using DashScope video generation API
        """
        pass
