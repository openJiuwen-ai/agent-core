# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from abc import ABC, abstractmethod
from typing import List, Optional, AsyncIterator, Union, Dict, Any

from openjiuwen.core.common.exception.status_code import StatusCode

from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.common.logging import logger
from openjiuwen.core.common.security.user_config import UserConfig
from openjiuwen.core.foundation.tool import ToolInfo
from openjiuwen.core.foundation.llm1.schema.config import ModelConfig, ModelClientConfig
from openjiuwen.core.foundation.llm1.schema.message import BaseMessage, AssistantMessage, ToolMessage
from openjiuwen.core.foundation.llm1.schema.message_chunk import AssistantMessageChunk
from openjiuwen.core.foundation.llm1.output_parsers.output_parser import BaseOutputParser


class BaseModelClient(ABC):
    """LLM Model Client Abstract Base Class

    All Model Client implementations must inherit from this class and implement the abstract methods.
    """

    def __init__(self, model_config: ModelConfig, model_client_config: ModelClientConfig):
        """Initialize Model Client

        Args:
            model_config: Model parameter configuration (temperature, top_p, model_name, etc.)
            config: Client configuration (api_key, api_base, timeout, etc.)
        """
        self.model_config = model_config
        self.model_client_config = model_client_config
        self._validate_config()

    def _get_client_name(self) -> str:
        """Get client name for error messages (subclasses can override)
        
        Returns:
            Client name string
        """
        return self.__class__.__name__

    def _validate_config(self):
        """Validate configuration parameters (subclasses can optionally override)"""
        client_name = self._get_client_name()
        
        if not self.model_client_config.api_key:
            raise JiuWenBaseException(StatusCode.LLM_SERVICE_CONFIG_ERROR.code,
                                      StatusCode.LLM_SERVICE_CONFIG_ERROR.errmsg.format(
                                          error_msg=f"model client config api_key is required for {client_name}."))
        if not self.model_client_config.api_base:
            raise JiuWenBaseException(StatusCode.LLM_SERVICE_CONFIG_ERROR.code,
                                      StatusCode.LLM_SERVICE_CONFIG_ERROR.errmsg.format(
                                          error_msg=f"model client config api_base is required for {client_name}."))

        if self.model_client_config.verify_ssl is not None and not isinstance(self.model_client_config.verify_ssl,
                                                                              bool):
            raise JiuWenBaseException(StatusCode.LLM_SERVICE_CONFIG_ERROR.code,
                                      StatusCode.LLM_SERVICE_CONFIG_ERROR.errmsg.format(
                                          error_msg="model client config verify_ssl must be a boolean type."))

        if self.model_client_config.verify_ssl is True and self.model_client_config.ssl_cert is None:
            raise JiuWenBaseException(StatusCode.LLM_SERVICE_CONFIG_ERROR.code,
                                      StatusCode.LLM_SERVICE_CONFIG_ERROR.errmsg.format(
                                          error_msg="model client config ssl_cert is required when verify_ssl is True."))

    def _convert_messages_to_dict(self, messages: Union[str, List[BaseMessage], List[dict]]) -> List[dict]:
        """Convert messages to specific API format

        Args:
            messages: String or list of BaseMessage

        Returns:
            List[dict]: Converted message list
        """
        """Convert to OpenAI format: [{"role": "user", "content": "..."}]

           Args:
               messages: 
                    String or list of BaseMessage

               Returns:
                    Message list in OpenAI API format
        """
        # If it's a string, convert to user message
        if not messages:
            raise JiuWenBaseException(StatusCode.LLM_SERVICE_CALL_MODEL_PARAM_ERROR.code,
                                      StatusCode.LLM_SERVICE_CALL_MODEL_PARAM_ERROR.errmsg.format(
                                          error_msg="The message sent to the llm cannot be empty."))
        if isinstance(messages, str):
            return [{"role": "user", "content": messages}]

        if all(isinstance(item, dict) for item in messages):
            return messages

        # Convert BaseMessage list
        result = []
        for msg in messages:
            msg_dict = {"role": msg.role, "content": msg.content}

            # Add optional fields
            if msg.name:
                msg_dict["name"] = msg.name

            # Handle tool_calls for AssistantMessage
            if isinstance(msg, AssistantMessage) and msg.tool_calls:
                tool_calls_list = []
                for tc in msg.tool_calls:
                    tool_calls_list.append({
                        "id": tc.id,
                        "type": tc.type,
                        "function": {
                            "name": tc.name,
                            "arguments": tc.arguments
                        }
                    })
                msg_dict["tool_calls"] = tool_calls_list

            # Handle tool_call_id for ToolMessage
            if isinstance(msg, ToolMessage):
                msg_dict["tool_call_id"] = msg.tool_call_id

            result.append(msg_dict)

        return result

    def _convert_tools_to_dict(self, tools: Union[List[ToolInfo], List[dict], None]) -> Optional[List[dict]]:
        """Convert to OpenAI format: [{"type": "function", "function": {...}}]

        Args:
            tools: List of ToolInfo or None

        Returns:
            Tool list in OpenAI API format
        """
        if not tools:
            return None

        if all(isinstance(item, dict) for item in tools):
            return tools

        result = []
        for tool in tools:
            # Handle parameters (could be dict or BaseModel)
            if hasattr(tool.parameters, 'model_dump'):
                # If it's a Pydantic BaseModel
                parameters = tool.parameters.model_dump()
            else:
                # If it's already a dict
                parameters = tool.parameters

            tool_dict = {
                "type": tool.type,
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": parameters
                }
            }
            result.append(tool_dict)

        return result

    def _build_request_params(
            self,
            messages: Union[str, List[BaseMessage], List[dict]],
            tools: Union[List[ToolInfo], List[dict], None],
            temperature: Optional[float],
            top_p: Optional[float],
            model: Optional[str],
            stop: Union[Optional[str], None],
            max_tokens: Optional[int],
            stream: bool,
            **kwargs
    ) -> Dict[str, Any]:
        """Build OpenAI-compatible chat completion request parameters.

        Note:
            Most OpenAI-compatible providers use the same request payload. Subclasses can call
            this method and then apply provider-specific adjustments (e.g. additional fields).
        """
        if model is None and self.model_config.model_name is None:
            raise JiuWenBaseException(
                StatusCode.LLM_SERVICE_MODEL_CONFIG_ERROR.code,
                StatusCode.LLM_SERVICE_MODEL_CONFIG_ERROR.errmsg.format(error_msg="The model cannot be None.")
            )

        # Convert message format
        messages_dict = self._convert_messages_to_dict(messages)

        # Build basic parameters
        params: Dict[str, Any] = {
            "model": model if model else self.model_config.model_name,
            "messages": messages_dict,
            "stream": stream,
        }

        # Add temperature: prioritize parameter, otherwise use model_config, only add when not None
        final_temperature = temperature if temperature is not None else self.model_config.temperature
        if final_temperature is not None:
            params["temperature"] = final_temperature

        # Add top_p: prioritize parameter, otherwise use model_config, only add when not None
        final_top_p = top_p if top_p is not None else self.model_config.top_p
        if final_top_p is not None:
            params["top_p"] = final_top_p

        # Add max_tokens: prioritize parameter, otherwise use model_config, only add when not None
        final_max_tokens = max_tokens if max_tokens is not None else self.model_config.max_tokens
        if final_max_tokens is not None:
            params["max_tokens"] = final_max_tokens

        # Add stop: prioritize parameter, otherwise use model_config, only add when not None
        final_stop = stop if stop is not None else self.model_config.stop
        if final_stop is not None:
            params["stop"] = final_stop

        # Add tools
        tools_dict = self._convert_tools_to_dict(tools)
        if tools_dict:
            params["tools"] = tools_dict
            params["tool_choice"] = "auto"

        # Add other parameters (filter out internal parameters)
        # parser and output_parser are for internal use and should not be passed to model API
        internal_params = {"parser", "output_parser"}
        filtered_kwargs = {k: v for k, v in kwargs.items() if k not in internal_params}
        params.update(filtered_kwargs)

        # Logging
        client_name = self._get_client_name()
        if UserConfig.is_sensitive():
            logger.info(f"Before request {client_name} chat model, request params is ready.")
        else:
            logger.info(f"Before request {client_name} chat model, request params is ready. params:  {params}")

        return params

    @abstractmethod
    async def ainvoke(
            self,
            messages: Union[str, List[BaseMessage], List[dict]],
            tools: Union[List[ToolInfo], List[dict], None] = None,
            temperature: Optional[float] = None,
            top_p: Optional[float] = None,
            model: str = None,
            max_tokens: Optional[int] = None,
            stop: Union[Optional[str], None] = None,
            output_parser: Optional[BaseOutputParser] = None,
            **kwargs
    ) -> AssistantMessage:
        """Asynchronously invoke LLM

        Args:
            :param output_parser:
            :param model:
            :param stop:
            :param temperature:
            :param tools:
            :param messages:
            :param top_p:
            :param max_tokens:
            **kwargs: Additional parameters

        Returns:
            AssistantMessage: Model response
        """
        pass

    @abstractmethod
    async def astream(
            self,
            messages: Union[str, List[BaseMessage], List[dict]],
            tools: Union[List[ToolInfo], List[dict], None] = None,
            temperature: Optional[float] = None,
            top_p: Optional[float] = None,
            model: str = None,
            max_tokens: Optional[int] = None,
            stop: Union[Optional[str], None] = None,
            output_parser: Optional[BaseOutputParser] = None,
            **kwargs
    ) -> AsyncIterator[AssistantMessageChunk]:
        """Asynchronously stream invoke LLM

        Args:
            :param output_parser:
            :param model:
            :param stop:
            :param temperature:
            :param tools:
            :param messages:
            :param top_p:
            :param max_tokens:
            **kwargs: Additional parameters

        Yields:
            AssistantMessageChunk: Streaming response chunk
        """
        pass
