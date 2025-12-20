#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import List, Dict, Any, Iterator, AsyncIterator, Optional
from pydantic import BaseModel

from openjiuwen.core.utils.llm.base import BaseModelClient
from openjiuwen.core.utils.llm.messages import AIMessage
from openjiuwen.core.utils.llm.messages_chunk import AIMessageChunk
from openjiuwen.core.utils.llm.model_utils.default_model import OpenAIChatModel


class OpenAILLM(BaseModel, BaseModelClient):
    _openai_model: OpenAIChatModel = None

    def __init__(self,
                 api_key: str, api_base: str, max_retries: int = 3, timeout: int = 60, **kwargs):
        super().__init__(api_key=api_key, api_base=api_base, max_retries=max_retries, timeout=timeout)
        self._openai_model = OpenAIChatModel(api_key=api_key, api_base=api_base,
                                             max_retries=max_retries, timeout=timeout, **kwargs)
        self._should_close_session = True

    async def close(self):
        if hasattr(self, '_openai_model') and self._openai_model:
            if hasattr(self._openai_model, 'close'):
                await self._openai_model.close()
            self._openai_model = None

    def model_provider(self) -> str:
        return "openai"

    def _invoke(self, model_name: str, messages: List[Dict], tools: List[Dict] = None,
                temperature: Optional[float] = None, top_p: Optional[float] = None, **kwargs: Any) -> AIMessage:
        return self._openai_model._invoke(
            model_name=model_name, messages=messages, tools=tools,
            temperature=temperature, top_p=top_p, **kwargs)

    async def _ainvoke(self, model_name: str, messages: List[Dict], tools: List[Dict] = None,
                       temperature: Optional[float] = None, top_p: Optional[float] = None,
                       **kwargs: Any) -> AIMessage:
        return await self._openai_model._ainvoke(
            model_name=model_name, messages=messages, tools=tools,
            temperature=temperature, top_p=top_p, **kwargs)

    def _stream(self, model_name: str, messages: List[Dict], tools: List[Dict] = None,
                temperature: Optional[float] = None, top_p: Optional[float] = None,
                **kwargs: Any) -> Iterator[AIMessageChunk]:
        return self._openai_model._stream(
            model_name=model_name, messages=messages, tools=tools,
            temperature=temperature, top_p=top_p, **kwargs)

    async def _astream(self, model_name: str, messages: List[Dict], tools: List[Dict] = None,
                       temperature: Optional[float] = None, top_p: Optional[float] = None,
                       **kwargs: Any) -> AsyncIterator[
        AIMessageChunk]:
        async for chunk in self._openai_model._astream(
            model_name=model_name, messages=messages, tools=tools,
            temperature=temperature, top_p=top_p, **kwargs):
            yield chunk
