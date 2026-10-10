"""Native Gemini text/tool client for openJiuwen's public client registry.

Call register_google_client() before constructing a Google ModelClientConfig.
Requires google-genai; no OpenAI-compatible endpoint or tool execution in the SDK.
"""

import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any, NoReturn
from uuid import uuid4

import httpx
from google import genai
from google.genai import types

from openjiuwen.core.common.clients import get_client_registry
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.security.ssl_utils import SslUtils
from openjiuwen.core.foundation.llm.model_clients.base_model_client import BaseModelClient
from openjiuwen.core.foundation.llm.output_parsers.output_parser import BaseOutputParser
from openjiuwen.core.foundation.llm.schema.message import AssistantMessage, BaseMessage, UsageMetadata
from openjiuwen.core.foundation.llm.schema.message_chunk import AssistantMessageChunk
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.runner.callback import trigger
from openjiuwen.core.runner.callback.events import LLMCallEvents

_PARTS = "google_content_parts"


def _text_parts(content):
    """Convert the supported text input shapes to native parts."""
    if isinstance(content, str):
        return [types.Part(text=content)] if content else []
    parts = []
    for item in content or []:
        if isinstance(item, str):
            parts.append(types.Part(text=item))
        elif item.get("type") == "text":
            parts.append(types.Part(text=item["text"]))
        else:
            raise build_error(
                StatusCode.MODEL_INVOKE_PARAM_ERROR, error_msg="Google text client accepts only text content"
            )
    return parts


def _contents(messages):  # pylint: disable=too-many-locals
    """Build native history while preserving signed assistant parts."""
    if not messages:
        raise build_error(StatusCode.MODEL_INVOKE_PARAM_ERROR, error_msg="Google messages cannot be empty")
    if isinstance(messages, str):
        messages = [{"role": "user", "content": messages}]
    contents: list[types.Content] = []
    system: list[types.Part] = []
    calls: dict[str, dict[str, Any]] = {}
    for message in messages:
        msg = message if isinstance(message, dict) else message.model_dump()
        role = msg["role"]
        if role == "system":
            system.extend(_text_parts(msg.get("content", "")))
            continue
        if role == "tool":
            call = calls.get(msg.get("tool_call_id"))
            if call is None:
                raise build_error(
                    StatusCode.MODEL_INVOKE_PARAM_ERROR,
                    error_msg="Google tool result requires a matching preceding tool call",
                )
            parts = [
                types.Part(
                    function_response=types.FunctionResponse(
                        name=call["name"], id=call.get("provider_id"), response={"result": msg.get("content", "")}
                    )
                )
            ]
            role = "user"
        elif role in ("assistant", "user"):
            parts = _text_parts(msg.get("content", ""))
            preserved = (msg.get("metadata") or {}).get(_PARTS)
            if role == "assistant":
                if preserved is not None:
                    parts = [types.Part.model_validate(part) for part in preserved]
                native_calls = [part.function_call for part in parts if part.function_call]
                for index, call in enumerate(msg.get("tool_calls") or []):
                    fn = call.get("function", call)
                    provider_id = native_calls[index].id if index < len(native_calls) else None
                    calls[call["id"]] = {"name": fn["name"], "provider_id": provider_id}
                    if preserved is None:
                        arguments = fn.get("arguments") or "{}"
                        try:
                            args = json.loads(arguments)
                        except ValueError as error:
                            raise build_error(
                                StatusCode.MODEL_INVOKE_PARAM_ERROR,
                                cause=error,
                                error_msg="Google function arguments must be JSON",
                            ) from error
                        parts.append(types.Part(function_call=types.FunctionCall(name=fn["name"], args=args)))
                role = "model"
        else:
            raise build_error(StatusCode.MODEL_INVOKE_PARAM_ERROR, error_msg=f"Unsupported Google message role: {role}")
        if not parts:
            continue
        # Parallel results form a single user turn, in the tool-call order.
        if contents and contents[-1].role == role:
            contents[-1].parts.extend(parts)
        else:
            contents.append(types.Content(role=role, parts=parts))
    return contents, system or None


class GoogleModelClient(BaseModelClient):
    """Gemini Developer API generateContent / streamGenerateContent adapter."""

    __client_name__ = "Google"

    @asynccontextmanager
    async def _client(self, request_timeout, headers):
        """Own both SDK transports and translate provider errors at the boundary."""
        try:
            with self._sdk_client(request_timeout, headers) as sdk:
                async with sdk.aio as client:
                    yield client
        except Exception as error:
            await trigger(
                LLMCallEvents.LLM_CALL_ERROR,
                model_name=self.model_config.model_name,
                model_provider="Google",
                error=error,
            )
            raise build_error(
                StatusCode.MODEL_CALL_FAILED,
                cause=error,
                error_msg=f"Google native API failed: {type(error).__name__} (code={getattr(error, 'code', None)})",
            ) from error

    def _sdk_client(self, timeout=None, custom_headers=None):
        """Create request-owned SDK transports with the configured HTTP options."""
        config = self.model_client_config
        verify = SslUtils.create_strict_ssl_context(config.ssl_cert) if config.verify_ssl else False
        headers = {**(config.custom_headers or {}), **(custom_headers or {})}
        return genai.Client(
            api_key=config.api_key,
            vertexai=False,
            http_options=types.HttpOptions(
                base_url=config.api_base.rstrip("/"),
                timeout=int((timeout if timeout is not None else config.timeout) * 1000),
                retry_options=types.HttpRetryOptions(attempts=1),
                headers=headers or None,
                client_args={"verify": verify},
                async_client_args={"transport": httpx.AsyncHTTPTransport(verify=verify)},
            ),
        )

    def _request(  # pylint: disable=too-many-locals
        self, messages, tools=None, model=None, temperature=None, top_p=None, max_tokens=None, stop=None, **kwargs
    ):
        """Map openJiuwen defaults and call overrides to Google request fields."""
        contents, system = _contents(messages)
        config = self.model_config
        values = dict(config.model_extra or {})
        # Model adds these internal context fields to every request.
        for key in ("request_purpose", "context_operation_id", "agent_hint"):
            kwargs.pop(key, None)
        values.update(kwargs)
        for field, value in (
            ("temperature", temperature),
            ("top_p", top_p),
            ("max_tokens", max_tokens),
            ("stop", stop),
        ):
            selected = value if value is not None else getattr(config, field)
            if selected is not None:
                native_field = {"max_tokens": "max_output_tokens", "stop": "stop_sequences"}.get(field, field)
                values[native_field] = [selected] if field == "stop" and isinstance(selected, str) else selected
        values["system_instruction"] = types.Content(parts=system) if system else None
        values["automatic_function_calling"] = types.AutomaticFunctionCallingConfig(disable=True)
        if tools:
            declarations = []
            for tool in self._convert_tools_to_dict(tools):
                fn = tool.get("function", tool)
                declarations.append(
                    types.FunctionDeclaration(
                        name=fn["name"], description=fn.get("description"), parameters_json_schema=fn.get("parameters")
                    )
                )
            values["tools"] = [types.Tool(function_declarations=declarations)]
        choice = values.pop("tool_choice", None)
        if choice is not None:
            modes = {
                "auto": types.FunctionCallingConfigMode.AUTO,
                "required": types.FunctionCallingConfigMode.ANY,
                "none": types.FunctionCallingConfigMode.NONE,
            }
            if isinstance(choice, str):
                calling = types.FunctionCallingConfig(mode=modes[choice])
            else:
                calling = types.FunctionCallingConfig(
                    mode=types.FunctionCallingConfigMode.ANY, allowed_function_names=[choice["function"]["name"]]
                )
            values["tool_config"] = types.ToolConfig(function_calling_config=calling)
        try:
            generation = types.GenerateContentConfig(**values)
        except ValueError as error:
            raise build_error(
                StatusCode.MODEL_INVOKE_PARAM_ERROR, cause=error, error_msg="Invalid Google generation configuration"
            ) from error
        return dict(model=model or config.model_name, contents=contents, config=generation)

    @staticmethod
    def _response(response, model, chunk=False, offset=0, replay=None):  # pylint: disable=too-many-locals
        """Normalize usage and display text, retaining every signed replay part."""
        candidate = (response.candidates or [None])[0]
        parts = candidate.content.parts if candidate and candidate.content else []
        parts = parts or []
        content = "".join(part.text for part in parts if part.text and not part.thought)
        reasoning = "".join(part.text for part in parts if part.text and part.thought)
        calls: list[ToolCall] = []
        for part in parts:
            call = part.function_call
            if call:
                calls.append(
                    ToolCall(
                        id=call.id or f"google_{uuid4().hex}",
                        type="function",
                        name=call.name,
                        arguments=json.dumps(call.args or {}),
                        index=offset + len(calls),
                    )
                )
        native_parts = [part.model_dump(mode="json", exclude_none=True) for part in parts]
        if replay is not None:
            replay.extend(native_parts)
            native_parts = list(replay)
        usage = response.usage_metadata
        metadata = None
        if usage is not None:
            thoughts = usage.thoughts_token_count or 0
            cache = usage.cached_content_token_count
            metadata = UsageMetadata(
                model_name=response.model_version or model,
                input_tokens=usage.prompt_token_count or 0,
                output_tokens=(usage.candidates_token_count or 0) + thoughts,
                total_tokens=usage.total_token_count or 0,
                reasoning_tokens=thoughts,
                cache_tokens=cache or 0,
                cache_read_tokens=cache,
                cache_authoritative=cache is not None,
                cache_status="observed" if cache is not None else None,
                cache_source="provider_usage" if cache is not None else None,
            )
        native_reason = candidate.finish_reason.value if candidate and candidate.finish_reason else None
        blocked = response.prompt_feedback.block_reason if response.prompt_feedback else None
        finish = {"STOP": "stop", "MAX_TOKENS": "length"}.get(native_reason, native_reason or "null")
        if native_reason == "STOP" and (calls or any("function_call" in part for part in native_parts)):
            finish = "tool_calls"
        if blocked:
            finish = blocked.value
        cls = AssistantMessageChunk if chunk else AssistantMessage
        return cls(
            content=content,
            reasoning_content=reasoning or None,
            tool_calls=calls or None,
            usage_metadata=metadata,
            finish_reason=finish,
            metadata={_PARTS: native_parts} if native_parts else {},
            response_id=response.response_id,
            response_model=response.model_version,
            provider_metadata={"finish_reason": native_reason} if native_reason else {},
        )

    async def invoke(
        self,
        messages: str | list[BaseMessage] | list[dict],
        *,
        output_parser: BaseOutputParser | None = None,
        timeout: float | None = None,  # noqa: ASYNC109 - BaseModelClient's SDK timeout override
        **kwargs: Any,
    ) -> AssistantMessage:
        """Invoke one native Google completion."""
        tracer = kwargs.pop("tracer_record_data", None)
        headers = kwargs.pop("custom_headers", None)
        request = self._request(messages, **kwargs)
        if tracer:
            await tracer(llm_params=request)
        await trigger(
            LLMCallEvents.LLM_INPUT,
            model_name=request["model"],
            model_provider="Google",
            messages=request["contents"],
            tools=request["config"].tools,
        )
        async with self._client(timeout, headers) as client:
            response = await client.models.generate_content(**request)
        result = self._response(response, request["model"])
        if output_parser and result.content:
            result.parser_content = await output_parser.parse(result.content)
        if tracer:
            await tracer(llm_response=result)
        await trigger(
            LLMCallEvents.LLM_OUTPUT,
            model_name=request["model"],
            model_provider="Google",
            response=result.content,
            usage=result.usage_metadata,
            tool_calls=result.tool_calls,
        )
        return result

    # The abstract base types stream as a coroutine; implementations yield chunks.
    async def stream(  # type: ignore[override]
        self,
        messages: str | list[BaseMessage] | list[dict],
        *,
        output_parser: BaseOutputParser | None = None,
        timeout: float | None = None,  # noqa: ASYNC109 - BaseModelClient's SDK timeout override
        **kwargs: Any,
    ) -> AsyncGenerator[AssistantMessageChunk, None]:
        """Yield native Google completion chunks with replay metadata."""
        tracer = kwargs.pop("tracer_record_data", None)
        headers = kwargs.pop("custom_headers", None)
        request = self._request(messages, **kwargs)
        if tracer:
            await tracer(llm_params=request)
        await trigger(
            LLMCallEvents.LLM_INPUT,
            model_name=request["model"],
            model_provider="Google",
            messages=request["contents"],
            tools=request["config"].tools,
        )
        replay: list[dict[str, Any]] = []
        text, offset = "", 0
        async with self._client(timeout, headers) as client:
            stream = await client.models.generate_content_stream(**request)
            try:
                async for response in stream:
                    result = self._response(response, request["model"], chunk=True, offset=offset, replay=replay)
                    offset += len(result.tool_calls or [])
                    text += result.content
                    if output_parser and text:
                        result.parser_content = await output_parser.parse(text)
                    await trigger(
                        LLMCallEvents.LLM_RESPONSE_RECEIVED, model_name=request["model"], model_provider="Google"
                    )
                    yield result
            finally:
                await stream.aclose()

    async def generate_image(self, messages, **kwargs) -> NoReturn:
        """Image generation is outside this text client."""
        raise build_error(StatusCode.MODEL_CALL_FAILED, error_msg="Google text client does not generate images")

    async def generate_speech(self, messages, **kwargs) -> NoReturn:
        """Speech generation is outside this text client."""
        raise build_error(StatusCode.MODEL_CALL_FAILED, error_msg="Google text client does not generate speech")

    async def generate_video(self, messages, **kwargs) -> NoReturn:
        """Video generation is outside this text client."""
        raise build_error(StatusCode.MODEL_CALL_FAILED, error_msg="Google text client does not generate video")


def register_google_client() -> None:
    """Register the native Google provider before config validation (safe to repeat)."""
    registry = get_client_registry()
    if "llm_Google" not in registry.list_clients():
        registry.register_class(GoogleModelClient)
