# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import asyncio
import threading
from typing import Any, AsyncIterator, List, Optional, Union

from openjiuwen.core.common.logging import LogEventType, llm_logger
from openjiuwen.core.common.security.ssl_utils import SslUtils
from openjiuwen.core.common.security.url_utils import UrlUtils
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.foundation.llm.model_clients.base_model_client import BaseModelClient
from openjiuwen.core.foundation.llm.headers_helper import build_base_headers, merge_request_headers
from openjiuwen.extensions.external_provider.openai_auth.openai_account_auth import (
    DEFAULT_OPENAI_ACCOUNT_BASE_URL as _DEFAULT_OPENAI_ACCOUNT_BASE_URL,
    OpenAIAccountAuthError,
    OpenAIAccountAuthManager,
)
from openjiuwen.extensions.external_provider.openai_auth.openai_account_models import OpenAIAccountModelCatalog
from openjiuwen.core.foundation.llm.utils.responses_utils import (
    OpenAIAccountResponsesError,
    build_request_body,
)
from openjiuwen.core.foundation.llm.utils.responses_transport import OpenAIAccountResponsesTransport
from openjiuwen.core.foundation.llm.output_parsers.output_parser import BaseOutputParser
from openjiuwen.core.foundation.llm.schema.config import ProviderType
from openjiuwen.core.foundation.llm.schema.generation_response import (
    AudioGenerationResponse,
    ImageGenerationResponse,
    VideoGenerationResponse,
)
from openjiuwen.core.foundation.llm.schema.message import AssistantMessage, BaseMessage, UserMessage
from openjiuwen.core.foundation.llm.schema.message_chunk import AssistantMessageChunk
from openjiuwen.core.foundation.tool import ToolInfo
from openjiuwen.core.runner.callback import trigger
from openjiuwen.core.runner.callback.events import LLMCallEvents


DEFAULT_OPENAI_ACCOUNT_BASE_URL = _DEFAULT_OPENAI_ACCOUNT_BASE_URL
_AUTH_RETRY_STATUS_CODES = {401, 403}


class OpenAIAccountModelClient(BaseModelClient):
    """OpenAI account backend client.

    This provider intentionally does not require ``api_key`` in the static
    config. Later phases resolve the bearer token from Jiuwen's OpenAI account OAuth
    auth store at request time.
    """

    __client_name__ = [ProviderType.OpenAIAccount.value]

    def _get_client_name(self) -> str:
        return "OpenAI account client"

    def __init__(self, model_config, model_client_config):
        super().__init__(model_config, model_client_config)
        self._base_headers = build_base_headers(custom_headers=model_client_config.custom_headers)
        extra = model_client_config.__pydantic_extra__ or {}
        self._auth_manager = extra.get("openai_account_auth_manager") or OpenAIAccountAuthManager(
            base_url=model_client_config.api_base,
            refresh_timeout_seconds=model_client_config.timeout,
        )
        self._transport_override = extra.get("openai_account_transport")
        self._model_catalog_override = extra.get("openai_account_model_catalog")
        self._model_catalog_transport = extra.get("openai_account_models_transport")
        self._model_catalog_cache_path = extra.get("openai_account_models_cache_path")
        # Cached default transport; created once on first use when no per-call timeout override.
        self._default_transport: Optional[OpenAIAccountResponsesTransport] = None
        self._default_transport_lock = threading.Lock()

    def _validate_config(self):
        """Validate static config while allowing OAuth-based credentials."""
        client_name = self._get_client_name()

        if not self.model_client_config.api_base:
            raise build_error(
                StatusCode.MODEL_SERVICE_CONFIG_ERROR,
                error_msg=f"model client config api_base is required for {client_name}.",
            )

        if self.model_client_config.verify_ssl is not None and not isinstance(
            self.model_client_config.verify_ssl,
            bool,
        ):
            raise build_error(
                StatusCode.MODEL_SERVICE_CONFIG_ERROR,
                error_msg="model client config verify_ssl must be a boolean type.",
            )

    async def invoke(
        self,
        messages: Union[str, List[BaseMessage], List[dict]],
        *,
        tools: Union[List[ToolInfo], List[dict], None] = None,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        model: str = None,
        max_tokens: Optional[int] = None,
        stop: Union[Optional[str], None] = None,
        output_parser: Optional[BaseOutputParser] = None,
        timeout: float = None,
        send_sampling_params: bool = False,
        send_max_output_tokens: bool = False,
        **kwargs,
    ) -> AssistantMessage:
        tracer_record_data = kwargs.pop("tracer_record_data", None)
        request_custom_headers = kwargs.pop("custom_headers", None)
        session_id = kwargs.pop("session_id", None)

        body = self._build_openai_account_request_body(
            messages=messages,
            tools=tools,
            temperature=temperature,
            top_p=top_p,
            model=model,
            max_tokens=max_tokens,
            stop=stop,
            send_sampling_params=send_sampling_params,
            send_max_output_tokens=send_max_output_tokens,
            **kwargs,
        )
        if tracer_record_data:
            await tracer_record_data(llm_params=body)

        await trigger(
            LLMCallEvents.LLM_INPUT,
            model_name=body.get("model"),
            model_provider=self.model_client_config.client_provider,
            messages=messages,
            tools=tools,
            temperature=body.get("temperature"),
            top_p=body.get("top_p"),
            max_tokens=body.get("max_output_tokens"),
            is_stream=False,
        )

        try:
            response = await self._invoke_once(
                body=body,
                timeout=timeout,
                session_id=session_id,
                request_custom_headers=request_custom_headers,
            )
        except Exception as exc:
            await self._emit_error(body, messages, tools, exc, is_stream=False)
            raise self._wrap_model_error(exc, "invoke") from exc

        if output_parser and response.content:
            response.parser_content = await self._parse_content(response.content, output_parser)

        if tracer_record_data:
            await tracer_record_data(llm_response=response)

        await trigger(
            LLMCallEvents.LLM_OUTPUT,
            model_name=body.get("model"),
            model_provider=self.model_client_config.client_provider,
            response=response.content,
            usage=response.usage_metadata,
            tool_calls=response.tool_calls,
            is_stream=False,
        )
        return response

    async def stream(
        self,
        messages: Union[str, List[BaseMessage], List[dict]],
        *,
        tools: Union[List[ToolInfo], List[dict], None] = None,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        model: str = None,
        max_tokens: Optional[int] = None,
        stop: Union[Optional[str], None] = None,
        output_parser: Optional[BaseOutputParser] = None,
        timeout: float = None,
        send_sampling_params: bool = False,
        send_max_output_tokens: bool = False,
        **kwargs,
    ) -> AsyncIterator[AssistantMessageChunk]:
        tracer_record_data = kwargs.pop("tracer_record_data", None)
        request_custom_headers = kwargs.pop("custom_headers", None)
        session_id = kwargs.pop("session_id", None)

        body = self._build_openai_account_request_body(
            messages=messages,
            tools=tools,
            temperature=temperature,
            top_p=top_p,
            model=model,
            max_tokens=max_tokens,
            stop=stop,
            send_sampling_params=send_sampling_params,
            send_max_output_tokens=send_max_output_tokens,
            **kwargs,
        )
        if tracer_record_data:
            await tracer_record_data(llm_params=body)

        await trigger(
            LLMCallEvents.LLM_INPUT,
            model_name=body.get("model"),
            model_provider=self.model_client_config.client_provider,
            messages=messages,
            tools=tools,
            temperature=body.get("temperature"),
            top_p=body.get("top_p"),
            max_tokens=body.get("max_output_tokens"),
            is_stream=True,
        )

        final_message = None
        accumulated_for_parser = ""
        try:
            async for chunk in self._stream_once_with_retry(
                body=body,
                timeout=timeout,
                session_id=session_id,
                request_custom_headers=request_custom_headers,
            ):
                await trigger(
                    LLMCallEvents.LLM_RESPONSE_RECEIVED,
                    model_name=body.get("model"),
                    model_provider=self.model_client_config.client_provider,
                )
                if output_parser and chunk.content:
                    accumulated_for_parser += str(chunk.content)
                    parser_content = await self._try_parse_stream_content(accumulated_for_parser, output_parser)
                    if parser_content is not None:
                        chunk.parser_content = parser_content
                        accumulated_for_parser = ""
                final_message = final_message + chunk if final_message else chunk
                yield chunk
        except Exception as exc:
            await self._emit_error(body, messages, tools, exc, is_stream=True)
            raise self._wrap_model_error(exc, "stream") from exc

        if tracer_record_data:
            await tracer_record_data(llm_response=final_message)

        await trigger(
            LLMCallEvents.LLM_OUTPUT,
            model_name=body.get("model"),
            model_provider=self.model_client_config.client_provider,
            is_stream=True,
            response=final_message.content if final_message else None,
            usage=final_message.usage_metadata if final_message else None,
            tool_calls=final_message.tool_calls if final_message else None,
        )

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
        **kwargs,
    ) -> ImageGenerationResponse:
        raise NotImplementedError("OpenAI account provider does not support image generation.")

    async def generate_speech(
        self,
        messages: List[UserMessage],
        *,
        model: Optional[str] = None,
        voice: Optional[str] = "Cherry",
        language_type: Optional[str] = "Auto",
        **kwargs,
    ) -> AudioGenerationResponse:
        raise NotImplementedError("OpenAI account provider does not support speech generation.")

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
        **kwargs,
    ) -> VideoGenerationResponse:
        raise NotImplementedError("OpenAI account provider does not support video generation.")

    def list_available_models(self, *, force_refresh: bool = False) -> list[str]:
        """List OpenAI account backend models through the OAuth token.

        This method performs synchronous model discovery. Async callers
        should wrap it with ``asyncio.to_thread`` when event-loop blocking
        matters.
        """
        return self._get_model_catalog().list_model_ids(
            auth_manager=self._auth_manager,
            force_refresh=force_refresh,
        )

    def _build_openai_account_request_body(
        self,
        *,
        messages: Union[str, List[BaseMessage], List[dict]],
        tools: Union[List[ToolInfo], List[dict], None],
        temperature: Optional[float],
        top_p: Optional[float],
        model: Optional[str],
        max_tokens: Optional[int],
        stop: Union[Optional[str], None],
        send_sampling_params: bool = False,
        send_max_output_tokens: bool = False,
        **kwargs,
    ) -> dict[str, Any]:
        final_model = model or self.model_config.model_name
        if not final_model:
            raise build_error(StatusCode.MODEL_CONFIG_ERROR, error_msg="The model cannot be empty.")

        final_temperature = (
            temperature if temperature is not None else self.model_config.temperature
        ) if send_sampling_params else None
        final_top_p = (top_p if top_p is not None else self.model_config.top_p) if send_sampling_params else None
        configured_max_tokens = max_tokens if max_tokens is not None else self.model_config.max_tokens
        final_stop = stop if stop is not None else self.model_config.stop

        reasoning = kwargs.pop("reasoning", {"effort": "medium", "summary": "auto"})
        extra_body = kwargs.pop("extra_body", None)
        tool_choice = kwargs.pop("tool_choice", "auto")
        parallel_tool_calls = kwargs.pop("parallel_tool_calls", True)
        include_reasoning = kwargs.pop("include_reasoning_encrypted_content", True)

        request_extra = self.model_config.model_dump(
            exclude={"model_name", "model", "temperature", "top_p", "max_tokens", "stop"},
            exclude_none=True,
        )
        request_extra.update(kwargs)

        return build_request_body(
            model=final_model,
            messages=messages,
            tools=tools,
            temperature=final_temperature,
            top_p=final_top_p,
            max_tokens=configured_max_tokens if send_max_output_tokens else None,
            stop=final_stop,
            reasoning=reasoning,
            include_reasoning_encrypted_content=include_reasoning,
            tool_choice=tool_choice,
            parallel_tool_calls=parallel_tool_calls,
            extra_body={**request_extra, **(extra_body or {})} or None,
        )

    async def _invoke_once(
        self,
        *,
        body: dict[str, Any],
        timeout: Optional[float],
        session_id: Optional[str],
        request_custom_headers: Optional[dict[str, Any]],
    ) -> AssistantMessage:
        access_token = await asyncio.to_thread(self._auth_manager.resolve_access_token)
        try:
            return await self._get_transport(timeout=timeout).create_response(
                body=body,
                access_token=access_token,
                model_name=str(body.get("model") or ""),
                session_id=session_id,
                extra_headers=self._request_headers(request_custom_headers),
            )
        except OpenAIAccountResponsesError as exc:
            if exc.status_code not in _AUTH_RETRY_STATUS_CODES:
                raise
            refreshed_token = await asyncio.to_thread(
                lambda: self._auth_manager.resolve_access_token(force_refresh=True)
            )
            try:
                return await self._get_transport(timeout=timeout).create_response(
                    body=body,
                    access_token=refreshed_token,
                    model_name=str(body.get("model") or ""),
                    session_id=session_id,
                    extra_headers=self._request_headers(request_custom_headers),
                )
            except OpenAIAccountResponsesError as retry_exc:
                if retry_exc.status_code in _AUTH_RETRY_STATUS_CODES:
                    raise self._relogin_required_error(retry_exc) from retry_exc
                raise

    async def _stream_once_with_retry(
        self,
        *,
        body: dict[str, Any],
        timeout: Optional[float],
        session_id: Optional[str],
        request_custom_headers: Optional[dict[str, Any]],
    ) -> AsyncIterator[AssistantMessageChunk]:
        access_token = await asyncio.to_thread(self._auth_manager.resolve_access_token)
        try:
            async for chunk in self._get_transport(timeout=timeout).stream_response(
                body=body,
                access_token=access_token,
                model_name=str(body.get("model") or ""),
                session_id=session_id,
                extra_headers=self._request_headers(request_custom_headers),
            ):
                yield chunk
        except OpenAIAccountResponsesError as exc:
            if exc.status_code not in _AUTH_RETRY_STATUS_CODES:
                raise
            refreshed_token = await asyncio.to_thread(
                lambda: self._auth_manager.resolve_access_token(force_refresh=True)
            )
            try:
                async for chunk in self._get_transport(timeout=timeout).stream_response(
                    body=body,
                    access_token=refreshed_token,
                    model_name=str(body.get("model") or ""),
                    session_id=session_id,
                    extra_headers=self._request_headers(request_custom_headers),
                ):
                    yield chunk
            except OpenAIAccountResponsesError as retry_exc:
                if retry_exc.status_code in _AUTH_RETRY_STATUS_CODES:
                    raise self._relogin_required_error(retry_exc) from retry_exc
                raise

    def _get_transport(self, *, timeout: Optional[float]) -> OpenAIAccountResponsesTransport:
        if self._transport_override is not None:
            return self._transport_override
        effective_timeout = timeout if timeout is not None else self.model_client_config.timeout
        if effective_timeout == self.model_client_config.timeout:
            with self._default_transport_lock:
                if self._default_transport is None:
                    self._default_transport = self._make_transport(timeout=effective_timeout)
            return self._default_transport
        return self._make_transport(timeout=effective_timeout)

    def _make_transport(self, *, timeout: float) -> OpenAIAccountResponsesTransport:
        verify = (
            SslUtils.create_strict_ssl_context(self.model_client_config.ssl_cert)
            if self.model_client_config.verify_ssl
            else False
        )
        return OpenAIAccountResponsesTransport(
            base_url=self.model_client_config.api_base,
            timeout_seconds=timeout,
            verify=verify,
            proxy=UrlUtils.get_global_proxy_url(self.model_client_config.api_base),
            max_retries=self.model_client_config.max_retries,
        )

    def _get_model_catalog(self) -> OpenAIAccountModelCatalog:
        if self._model_catalog_override is not None:
            return self._model_catalog_override

        verify = (
            SslUtils.create_strict_ssl_context(self.model_client_config.ssl_cert)
            if self.model_client_config.verify_ssl
            else False
        )
        return OpenAIAccountModelCatalog(
            base_url=self.model_client_config.api_base,
            cache_path=self._model_catalog_cache_path,
            timeout_seconds=self.model_client_config.timeout,
            transport=self._model_catalog_transport,
            verify=verify,
            proxy=UrlUtils.get_global_proxy_url(self.model_client_config.api_base),
            max_retries=self.model_client_config.max_retries,
        )

    def _request_headers(self, request_custom_headers: Optional[dict[str, Any]]) -> dict[str, str]:
        return merge_request_headers(self._base_headers, request_custom_headers)

    async def _parse_content(self, content: str, output_parser: BaseOutputParser) -> Any:
        try:
            return await output_parser.parse(content)
        except Exception as exc:
            llm_logger.warning(
                "OpenAI account output parser error.",
                event_type=LogEventType.LLM_CALL_ERROR,
                model_name=self.model_config.model_name,
                model_provider=self.model_client_config.client_provider,
                is_stream=False,
                exception=str(exc),
            )
            return None

    async def _try_parse_stream_content(self, content: str, output_parser: BaseOutputParser) -> Any:
        try:
            return await output_parser.parse(content)
        except Exception:
            return None

    async def _emit_error(
        self,
        body: dict[str, Any],
        messages: Union[str, List[BaseMessage], List[dict]],
        tools: Union[List[ToolInfo], List[dict], None],
        error: Exception,
        *,
        is_stream: bool,
    ) -> None:
        await trigger(
            LLMCallEvents.LLM_CALL_ERROR,
            model_name=body.get("model"),
            model_provider=self.model_client_config.client_provider,
            is_stream=is_stream,
            error=error,
        )
        llm_logger.error(
            "OpenAI account API call error.",
            event_type=LogEventType.LLM_CALL_ERROR,
            model_name=body.get("model"),
            model_provider=self.model_client_config.client_provider,
            messages=messages,
            tools=tools,
            is_stream=is_stream,
            exception=f"{type(error).__name__}: {error}",
        )

    @staticmethod
    def _relogin_required_error(error: OpenAIAccountResponsesError) -> OpenAIAccountAuthError:
        return OpenAIAccountAuthError(
            f"OpenAI account authentication failed after token refresh. Please login again: {error}",
            code="openai_account_auth_relogin_required",
            relogin_required=True,
            status_code=error.status_code,
        )

    @staticmethod
    def _wrap_model_error(error: Exception, operation: str) -> Exception:
        if isinstance(error, OpenAIAccountAuthError):
            return build_error(StatusCode.MODEL_SERVICE_CONFIG_ERROR, error_msg=f"OpenAI account auth error: {error}")
        return build_error(StatusCode.MODEL_CALL_FAILED, error_msg=f"OpenAI account API {operation} error: {error}")
