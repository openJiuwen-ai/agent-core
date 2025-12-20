#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import asyncio
import json
from abc import abstractmethod
from typing import List, Any, Union, Dict, Optional, AsyncIterator, Iterator
from pydantic import BaseModel, Field, field_validator

from jiuwen.core.utils.llm.messages import BaseMessage, ToolInfo, AIMessage
from jiuwen.core.utils.llm.messages_chunk import BaseMessageChunk, AIMessageChunk


class BaseChatModel:
    def __init__(self, api_key:str, api_base:str, max_retrie: int=3, timeout: int=60, **kwargs):
        self.api_key = api_key
        self.api_base = api_base
        self.max_retrie = max_retrie
        self.timeout = timeout

    def invoke(self, model_name:str, messages: Union[List[BaseMessage], List[Dict], str],
               tools: Union[List[ToolInfo], List[Dict]] = None, temperature:float=0.1,
               top_p:float = 0.1, **kwargs: Any):
        try:
            return self._invoke(model_name=model_name, messages=self._cover_messages_format(messages),
                                tools=self._cover_tool_format(tools),
                                temperature=temperature, top_p=top_p, **kwargs)
        except NotImplementedError:
            return asyncio.run(self.ainvoke(model_name=model_name, messages=self._cover_messages_format(messages),
                                tools=self._cover_tool_format(tools),
                                temperature=temperature, top_p=top_p, **kwargs))

    async def ainvoke(self, model_name:str, messages: Union[List[BaseMessage], List[Dict], str],
               tools: Union[List[ToolInfo], List[Dict]] = None, temperature:float=0.1,
               top_p:float=0.1, **kwargs: Any):
        try:
            return await self._ainvoke(model_name=model_name, messages=self._cover_messages_format(messages),
                                tools=self._cover_tool_format(tools),
                                temperature=temperature, top_p=top_p, **kwargs)
        except NotImplementedError:
            return self._invoke(model_name=model_name, messages=self._cover_messages_format(messages),
                                tools=self._cover_tool_format(tools),
                                temperature=temperature, top_p=top_p, **kwargs)

    def stream(self, model_name:str, messages: Union[List[BaseMessage], List[Dict], str],
               tools: Union[List[ToolInfo], List[Dict]] = None, temperature:float = 0.1,
               top_p:float = 0.1, **kwargs: Any):
        try:
            for chunk in self._stream(model_name=model_name, messages=self._cover_messages_format(messages),
                                tools=self._cover_tool_format(tools),
                                temperature=temperature, top_p=top_p, **kwargs):
                yield chunk
        except NotImplementedError:
            async def async_gen_wrapper():
                async for chunk in self._astream(model_name=model_name, messages=self._cover_messages_format(messages),
                                tools=self._cover_tool_format(tools),
                                temperature=temperature, top_p=top_p, **kwargs):
                    yield chunk

            loop = asyncio.new_event_loop()
            try:
                gen = async_gen_wrapper()
                while True:
                    try:
                        chunk = loop.run_until_complete(gen.__anext__())
                        yield chunk
                    except StopAsyncIteration:
                        break
            finally:
                loop.close()


    async def astream(self, model_name:str, messages: Union[List[BaseMessage], List[Dict], str],
               tools: Union[List[ToolInfo], List[Dict]] = None, temperature:float = 0.1,
               top_p:float = 0.1, **kwargs: Any)-> AsyncIterator[BaseMessageChunk]:
        try:
            async for chunk in self._astream(model_name=model_name, messages=self._cover_messages_format(messages),
                                tools=self._cover_tool_format(tools),
                                temperature=temperature, top_p=top_p, **kwargs):
                yield chunk
        except NotImplementedError:
            for chunk in self._stream(model_name=model_name, messages=self._cover_messages_format(messages),
                                tools=self._cover_tool_format(tools),
                                temperature=temperature, top_p=top_p, **kwargs):
                yield chunk

    @abstractmethod
    def _invoke(self, model_name:str, messages: List[Dict], tools: List[Dict] = None,  temperature:float = 0.1,
               top_p:float = 0.1, **kwargs: Any) -> AIMessage:
        raise NotImplementedError("BaseChatModel _invoke not implemented")

    @abstractmethod
    async def _ainvoke(self, model_name:str, messages: List[Dict], tools: List[Dict] = None, temperature:float = 0.1,
               top_p:float = 0.1, **kwargs: Any) -> AIMessage:
        raise NotImplementedError("BaseChatModel _ainvoke not implemented")

    @abstractmethod
    def _stream(self, model_name:str, messages: List[Dict], tools: List[Dict] = None, temperature:float = 0.1,
               top_p:float = 0.1, **kwargs: Any) -> Iterator[AIMessageChunk]:
        raise NotImplementedError("BaseChatModel _stream not implemented")

    @abstractmethod
    async def _astream(self, model_name:str, messages: List[Dict], tools: List[Dict] = None, temperature:float = 0.1,
               top_p:float = 0.1, **kwargs: Any) -> AsyncIterator[
        AIMessageChunk]:
        raise NotImplementedError("BaseChatModel _astream not implemented")

    @abstractmethod
    def model_provider(self):
        pass

    def _cover_tool_format(self, tools: Union[List[ToolInfo], List[Dict]]):
        if not tools:
            return []

        if all(isinstance(item, Dict) for item in tools):
            return tools
        else:
            return [json.loads(tool.model_dump_json()) for tool in tools]

    def clean_tools(self, tools):
        """
        Remove non-standard fields (such as "results") from each dictionary in the tool list, and retain only the OpenAI format.
        """
        cleaned = []
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            cleaned_tool = {
                "type": tool.get("type", "function"),
                "function": {
                    "name": tool["function"]["name"],
                    "description": tool["function"]["description"],
                    "parameters": tool["function"]["parameters"]
                }
            }
            cleaned.append(cleaned_tool)
        return cleaned

    def _cover_messages_format(self, messages: Union[List[BaseMessage], List[Dict], str]):
        if not messages:
            return [{"role": "user", "content": ""}]

        if isinstance(messages, str):
            return [{"role": "user", "content": messages}]
        else:
            if all(isinstance(item, Dict) for item in messages):
                return messages
            return [item.model_dump(exclude_none=True) for item in messages]

    def post_process(self, model_output):
        pass

    def pre_process(self, model_output):
        pass


class BaseModelInfo(BaseModel):
    api_key: Optional[str] = Field(default="", alias="api_key")
    api_base: Optional[str] = Field(default="", alias="api_base")
    model_name: str = Field(default="", alias="model")
    temperature: float = Field(default=0.95)
    top_p: float = Field(default=0.1)
    streaming: bool = Field(default=False, alias="stream")
    timeout: float = Field(default=60.0)

    @field_validator('model_name', mode='before')
    @classmethod
    def handle_model_name(cls, v, values):
        if not v and 'model' in values.data:
            return values.data['model']
        return v

    class Config:
        populate_by_name = True
        extra = "forbid"
