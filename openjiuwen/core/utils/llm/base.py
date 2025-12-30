# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import asyncio
from abc import abstractmethod
from typing import List, Any, Union, Dict, Optional, AsyncIterator, Iterator
from pydantic import BaseModel, Field, field_validator, ConfigDict

from openjiuwen.core.utils.llm.messages import BaseMessage, AIMessage
from openjiuwen.core.utils.tool.schema import ToolInfo, Parameters
from openjiuwen.core.utils.llm.messages_chunk import BaseMessageChunk, AIMessageChunk


class BaseModelClient:
    def __init__(self, api_key: str, api_base: str, max_retries: int = 3, timeout: int = 60, **kwargs):
        self.api_key = api_key
        self.api_base = api_base
        self.max_retries = max_retries
        self.timeout = timeout
        self.extra_params_config = kwargs

    def invoke(self, model_name: str, messages: Union[List[BaseMessage], List[Dict], str],
               tools: Union[List[ToolInfo], List[Dict]] = None, temperature: Optional[float] = None,
               top_p: Optional[float] = None, **kwargs: Any):
        try:
            return self._invoke(model_name=model_name, messages=self._convert_messages_format(messages),
                                tools=self._convert_tool_info_format(tools),
                                temperature=temperature, top_p=top_p, **kwargs)
        except NotImplementedError:
            return asyncio.run(self.ainvoke(model_name=model_name, messages=self._convert_messages_format(messages),
                                            tools=self._convert_tool_info_format(tools),
                                            temperature=temperature, top_p=top_p, **kwargs))

    async def ainvoke(self, model_name: str, messages: Union[List[BaseMessage], List[Dict], str],
               tools: Union[List[ToolInfo], List[Dict]] = None, temperature: Optional[float] = None,
               top_p: Optional[float] = None, **kwargs: Any):
        try:
            return await self._ainvoke(model_name=model_name, messages=self._convert_messages_format(messages),
                                       tools=self._convert_tool_info_format(tools),
                                       temperature=temperature, top_p=top_p, **kwargs)
        except NotImplementedError:
            return self._invoke(model_name=model_name, messages=self._convert_messages_format(messages),
                                tools=self._convert_tool_info_format(tools),
                                temperature=temperature, top_p=top_p, **kwargs)

    def stream(self, model_name: str, messages: Union[List[BaseMessage], List[Dict], str],
               tools: Union[List[ToolInfo], List[Dict]] = None, temperature: Optional[float] = None,
               top_p: Optional[float] = None, **kwargs: Any):
        try:
            for chunk in self._stream(model_name=model_name, messages=self._convert_messages_format(messages),
                                tools=self._convert_tool_info_format(tools),
                                temperature=temperature, top_p=top_p, **kwargs):
                yield chunk
        except NotImplementedError:
            async def async_gen_wrapper():
                async for chunk in self._astream(model_name=model_name,
                                messages=self._convert_messages_format(messages),
                                tools=self._convert_tool_info_format(tools),
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


    async def astream(self, model_name: str, messages: Union[List[BaseMessage], List[Dict], str],
               tools: Union[List[ToolInfo], List[Dict]] = None, temperature: Optional[float] = None,
               top_p: Optional[float] = None, **kwargs: Any) -> AsyncIterator[BaseMessageChunk]:
        try:
            async for chunk in self._astream(model_name=model_name, messages=self._convert_messages_format(messages),
                                tools=self._convert_tool_info_format(tools),
                                temperature=temperature, top_p=top_p, **kwargs):
                yield chunk
        except NotImplementedError:
            for chunk in self._stream(model_name=model_name, messages=self._convert_messages_format(messages),
                                tools=self._convert_tool_info_format(tools),
                                temperature=temperature, top_p=top_p, **kwargs):
                yield chunk

    @abstractmethod
    def _invoke(self, model_name: str, messages: List[Dict], tools: List[Dict] = None,
                temperature: Optional[float] = None, top_p: Optional[float] = None, **kwargs: Any) -> AIMessage:
        raise NotImplementedError("BaseModelClient _invoke not implemented")

    @abstractmethod
    async def _ainvoke(self, model_name: str, messages: List[Dict], tools: List[Dict] = None,
                       temperature: Optional[float] = None, top_p: Optional[float] = None, **kwargs: Any) -> AIMessage:
        raise NotImplementedError("BaseModelClient _ainvoke not implemented")

    @abstractmethod
    def _stream(self, model_name: str, messages: List[Dict], tools: List[Dict] = None,
                temperature: Optional[float] = None, top_p: Optional[float] = None,
                **kwargs: Any) -> Iterator[AIMessageChunk]:
        raise NotImplementedError("BaseModelClient _stream not implemented")

    @abstractmethod
    async def _astream(self, model_name: str, messages: List[Dict], tools: List[Dict] = None,
                       temperature: Optional[float] = None, top_p: Optional[float] = None,
                       **kwargs: Any) -> AsyncIterator[
        AIMessageChunk]:
        raise NotImplementedError("BaseModelClient _astream not implemented")

    @abstractmethod
    def model_provider(self):
        pass

    def _convert_tool_info_format(self, tools: Union[List[ToolInfo], List[Dict]]):
        if not tools:
            return []

        if all(isinstance(item, Dict) for item in tools):
            return tools
        else:
            return [self._convert_tool_info_to_dict(self.convert_to_tool_info(
                self._convert_tool_info_to_dict(tool))) for tool in tools]

    @staticmethod
    def clean_tools(tools):
        """
        Remove non-standard fields (such as "results") from each dictionary in the tool list,
        and retain only the OpenAI format.
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

    @staticmethod
    def _convert_tool_info_to_dict(tool: ToolInfo):
        return {
            "type": tool.type,
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters.model_dump() if tool.parameters else {}
            }
        }

    # switch ToolInfo
    @staticmethod
    def convert_to_tool_info(tool_dict):
        """convert_to_tool_info"""
        func = tool_dict.get('function', {})

        params_dict = func.get('parameters', {})
        parameters = None
        if params_dict:
            properties = params_dict.get('properties', {})
            cleaned_properties = {}
            for key, value in properties.items():
                cleaned_value = {k: v for k, v in value.items() if k != 'required'}
                cleaned_properties[key] = cleaned_value

            parameters = Parameters(
                type=params_dict.get('type', 'object'),
                properties=cleaned_properties,
                required=params_dict.get('required', [])
            )

        tool_info = ToolInfo(
            type=tool_dict.get('type', 'function'),
            name=func.get('name', ''),
            description=func.get('description', ''),
            parameters=parameters
        )

        return tool_info

    @staticmethod
    def _convert_messages_format(messages: Union[List[BaseMessage], List[Dict], str]):
        if not messages:
            return [{"role": "user", "content": ""}]

        if isinstance(messages, str):
            return [{"role": "user", "content": messages}]
        if all(isinstance(item, Dict) for item in messages):
            return messages
        result = []
        for item in messages:
            item_dict = item.model_dump(exclude_none=True)
            if item.role == "assistant":
                if "usage_metadata" in item_dict:
                    item_dict.pop("usage_metadata")
            result.append(item_dict)
        return result

    def post_process(self, model_output):
        pass

    def pre_process(self, model_output):
        pass

    def _update_model_params(self, temperature, top_p, **kwargs):
        result = {}
        if temperature is not None:
            result["temperature"] = temperature
        if top_p is not None:
            result["top_p"] = top_p
        for key, value in kwargs.items():
            if key not in result and value is not None:
                result[key] = value
        for key, value in self.extra_params_config.items():
            if key not in result and value is not None:
                result[key] = value
        return result


class BaseModelInfo(BaseModel):
    api_key: str = Field(min_length=1)
    api_base: str = Field(min_length=1)
    model_name: str = Field(default="", alias="model")
    temperature: float = Field(default=0.95)
    top_p: float = Field(default=0.1)
    streaming: bool = Field(default=False, alias="stream")
    timeout: int = Field(default=60, gt=0)
    model_config = ConfigDict(extra='allow')

    @field_validator('model_name', mode='before')
    @classmethod
    def handle_model_name(cls, v, values):
        if not v and 'model' in values.data:
            return values.data['model']
        return v
