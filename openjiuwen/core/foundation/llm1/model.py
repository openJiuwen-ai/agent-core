# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import Union, List, Optional, AsyncIterator, Type, Dict

from openjiuwen.core.common.exception.status_code import StatusCode

from openjiuwen.core.common.exception.exception import JiuWenBaseException

from openjiuwen.core.foundation.llm1.schema.message import BaseMessage, AssistantMessage
from openjiuwen.core.foundation.llm1.schema.message_chunk import AssistantMessageChunk
from openjiuwen.core.foundation.tool import ToolInfo
from openjiuwen.core.foundation.llm1.schema.config import ModelRequestConfig, ModelClientConfig
from openjiuwen.core.foundation.llm1.output_parsers.output_parser import BaseOutputParser
from openjiuwen.core.foundation.llm1.model_clients.base_model_client import BaseModelClient
from openjiuwen.core.foundation.llm1.model_clients.openai_model_client import OpenAIModelClient
from openjiuwen.core.foundation.llm1.model_clients.siliconflow_model_client import SiliconFlowModelClient

_CLIENT_TYPE_REGISTRY: Dict[str, Type[BaseModelClient]] = {
    "OpenAI": OpenAIModelClient,
    "SiliconFlow": SiliconFlowModelClient,
}


class Model:
    """Unified LLM invocation entry point

    Responsibilities:
    1. Get/create ModelClient instance based on client_id or configuration
    2. Delegate to ModelClient to execute actual LLM calls
    3. Provide unified interface (ainvoke, astream)

    Usage:

    Method 1: Dynamic creation (pass configuration)
        model = Model(model_config, client_config)
        response = await model.ainvoke("Hello")
    """

    def __init__(
            self,
            model_client_config: Optional[ModelClientConfig],
            model_config: ModelRequestConfig = None,
    ):
        """Initialize Model instance

        Args:
            model_config: Model parameter configuration
            model_client_config: Client configuration
        """
        self.model_config = model_config
        self.model_client_config = model_client_config
        self._client: Optional[BaseModelClient] = None

        if model_client_config is not None:
            self._client = self._create_model_client(model_client_config)
        else:
            raise JiuWenBaseException(StatusCode.MODEL_SERVICE_CONFIG_ERROR.code,
                                      StatusCode.MODEL_SERVICE_CONFIG_ERROR.errmsg.format(
                                          error_msg="model client config is none."))

    def _create_model_client(self, client_config: ModelClientConfig) -> BaseModelClient:
        """Create corresponding ModelClient instance based on client_type
        
        Args:
            client_config: Client configuration
            
        Returns:
            BaseModelClient: ModelClient instance
            
        Raises:
            ValueError: When client_provider is not supported
        """
        if client_config.client_provider is None:
            raise JiuWenBaseException(StatusCode.MODEL_SERVICE_CONFIG_ERROR.code,
                                      StatusCode.MODEL_SERVICE_CONFIG_ERROR.errmsg.format(
                                          error_msg="model client config client_provider is none."))
        if client_config.client_id is None:
            raise JiuWenBaseException(StatusCode.MODEL_SERVICE_CONFIG_ERROR.code,
                                      StatusCode.MODEL_SERVICE_CONFIG_ERROR.errmsg.format(
                                          error_msg="model client config client_id is none."))
        client_provider = client_config.client_provider

        client_class = _CLIENT_TYPE_REGISTRY.get(client_provider)

        if client_class is None:
            supported_types = ", ".join(_CLIENT_TYPE_REGISTRY.keys())

            raise JiuWenBaseException(StatusCode.MODEL_SERVICE_CONFIG_ERROR.code,
                                      StatusCode.MODEL_SERVICE_CONFIG_ERROR.errmsg.format(
                                          error_msg=f"Unsupported client_type: '{client_provider}'. "
                                          f"Supported types: {supported_types}"))

        return client_class(self.model_config, client_config)

    async def invoke(
            self,
            messages: Union[str, List[BaseMessage], List[dict]],
            *,
            tools: Union[List[ToolInfo], List[dict], None] = None,
            temperature: Optional[float] = None,
            top_p: Optional[float] = None,
            max_tokens: Optional[int] = None,
            stop: Union[Optional[str], None] = None,
            model: str = None,
            output_parser: Optional[BaseOutputParser] = None,
            timeout: float = None,
            **kwargs
    ) -> AssistantMessage:
        """Asynchronous LLM invocation

        Args:
            :param output_parser:
            :param model:
            :param stop:
            :param temperature:
            :param tools:
            :param messages:
            :param top_p:
            :param max_tokens:
            :param timeout:
            **kwargs: Other parameters

        Returns:
            AssistantMessage
        """
        return await self._client.invoke(
            messages=messages,
            stop=stop,
            model=model,
            tools=tools,
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,
            output_parser=output_parser,
            timeout=timeout,
            **kwargs
        )

    async def stream(
            self,
            messages: Union[str, List[BaseMessage], List[dict]],
            *,
            tools: Union[List[ToolInfo], List[dict], None] = None,
            temperature: Optional[float] = None,
            top_p: Optional[float] = None,
            max_tokens: Optional[int] = None,
            stop: Union[Optional[str], None] = None,
            model: str = None,
            output_parser: Optional[BaseOutputParser] = None,
            timeout: float = None,
            **kwargs
    ) -> AsyncIterator[AssistantMessageChunk]:
        """Asynchronous streaming LLM invocation

        Args:
            :param output_parser:
            :param model:
            :param stop:
            :param temperature:
            :param tools:
            :param messages:
            :param top_p:
            :param max_tokens:
            :param timeout:
            **kwargs: Other parameters

        Yields:
            AssistantMessageChunk
        """
        async for chunk in self._client.stream(
                messages=messages,
                stop=stop,
                model=model,
                tools=tools,
                temperature=temperature,
                top_p=top_p,
                max_tokens=max_tokens,
                output_parser=output_parser,
                timeout=timeout,
                **kwargs
        ):
            yield chunk
