#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import List, Dict, Any, Iterator, AsyncIterator
from pydantic import BaseModel

from jiuwen.core.utils.llm.base import BaseChatModel
from jiuwen.core.utils.llm.messages import AIMessage
from jiuwen.core.utils.llm.messages_chunk import AIMessageChunk
from jiuwen.core.utils.llm.model_utils.default_model import OpenAIChatModel


class OpenAILLM(BaseModel, BaseChatModel):
    _openai_model: OpenAIChatModel = None

    def __init__(self,
                 api_key: str, api_base: str, max_retrie: int = 3, timeout: int = 60, **kwargs):
        super().__init__(api_key=api_key, api_base=api_base, max_retrie=max_retrie, timeout=timeout)
        self._openai_model = OpenAIChatModel(api_key=api_key, api_base=api_base,
                                               max_retrie=max_retrie, timeout=timeout)
        self._should_close_session = True

    def model_provider(self) -> str:
        return "siliconflow"

    def _invoke(self, model_name: str, messages: List[Dict], tools: List[Dict] = None, temperature: float = 0.1,
                top_p: float = 0.1, **kwargs: Any) -> AIMessage:
        return self._openai_model._invoke(
            model_name=model_name, messages=messages, tools=tools,
            temperature=temperature, top_p=top_p, **kwargs)

    async def _ainvoke(self, model_name: str, messages: List[Dict], tools: List[Dict] = None, temperature: float = 0.1,
                       top_p: float = 0.1, **kwargs: Any) -> AIMessage:
        return await self._openai_model._ainvoke(
            model_name=model_name, messages=messages, tools=tools,
            temperature=temperature, top_p=top_p, **kwargs)

    def _stream(self, model_name: str, messages: List[Dict], tools: List[Dict] = None, temperature: float = 0.1,
                top_p: float = 0.1, **kwargs: Any) -> Iterator[AIMessageChunk]:
        return self._openai_model._stream(
            model_name=model_name, messages=messages, tools=tools,
            temperature=temperature, top_p=top_p, **kwargs)

    async def _astream(self, model_name:str, messages: List[Dict], tools: List[Dict] = None, temperature:float = 0.1,
               top_p:float = 0.1, **kwargs: Any) -> AsyncIterator[
        AIMessageChunk]:
        async for chunk in self._openai_model._astream(
            model_name=model_name, messages=messages, tools=tools,
            temperature=temperature, top_p=top_p, **kwargs):
            yield chunk
