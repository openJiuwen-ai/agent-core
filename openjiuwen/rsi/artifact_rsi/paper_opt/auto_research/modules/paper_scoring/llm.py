"""Direct structured completions via OpenJiuwen Model / LLMComponent.

No DeepAgent, tools, rails, or ReAct loop: one prompt in, JSON out, host validation.
"""

from __future__ import annotations

import base64
import json
import os
import re
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, TypeVar

from pydantic import BaseModel

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.common.env import load_project_dotenv
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.paper_scoring.schemas import (
    FigureAsset,
    LLMCallMeta,
    PaperScoringSettings,
)

T = TypeVar("T", bound=BaseModel)

CompleteFn = Callable[..., Any]

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)
_VISION_ERROR_RE = re.compile(
    r"image|vision|multimodal|does not support|unsupported.*content",
    re.IGNORECASE,
)


class StructuredCompletionError(RuntimeError):
    """JSON parse or schema validation failed after retries."""


class VisionModelError(RuntimeError):
    """Configured model rejected image inputs."""


def extract_json_payload(text: str) -> Any:
    stripped = text.strip()
    fenced = _FENCE_RE.search(stripped)
    if fenced:
        stripped = fenced.group(1).strip()
    decoder = json.JSONDecoder()
    try:
        return decoder.raw_decode(stripped)[0]
    except json.JSONDecodeError:
        start = stripped.find("{")
        if start < 0:
            start = stripped.find("[")
        if start < 0:
            raise
        return decoder.raw_decode(stripped[start:])[0]


def _response_text(response: Any) -> str:
    if response is None:
        return ""
    if isinstance(response, str):
        return response
    if isinstance(response, dict):
        for key in ("output", "content", "text", "message"):
            value = response.get(key)
            if isinstance(value, str):
                return value
            if isinstance(value, dict):
                inner = _response_text(value)
                if inner:
                    return inner
            if isinstance(value, list):
                texts = [_response_text(item) for item in value]
                joined = "".join(part for part in texts if part)
                if joined:
                    return joined
        return json.dumps(response)
    for attr in ("output", "content", "text"):
        value = getattr(response, attr, None)
        if isinstance(value, str):
            return value
    return str(response)


def _usage_tokens(response: Any) -> tuple[int | None, int | None]:
    usage = None
    if isinstance(response, dict):
        usage = response.get("usage")
    else:
        usage = getattr(response, "usage", None)
    if usage is None:
        return None, None
    if isinstance(usage, dict):
        prompt = usage.get("prompt_tokens") or usage.get("input_tokens")
        completion = usage.get("completion_tokens") or usage.get("output_tokens")
        return (
            int(prompt) if prompt is not None else None,
            int(completion) if completion is not None else None,
        )
    prompt = getattr(usage, "prompt_tokens", None) or getattr(usage, "input_tokens", None)
    completion = getattr(usage, "completion_tokens", None) or getattr(
        usage, "output_tokens", None
    )
    return (
        int(prompt) if prompt is not None else None,
        int(completion) if completion is not None else None,
    )


def _cfg_str(oj: dict[str, Any], key: str) -> str | None:
    value = oj.get(key)
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() == "default":
        return None
    return text


def _cfg_or(oj: dict[str, Any], key: str, default: Any) -> Any:
    value = oj.get(key)
    return default if value is None else value


def figure_message_parts(figures: Sequence[FigureAsset]) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = []
    for figure in figures:
        encoded = base64.b64encode(figure.png_bytes).decode("ascii")
        caption = figure.caption or figure.source_path
        parts.append(
            {
                "type": "text",
                "text": (
                    f"Figure {figure.figure_id} (section {figure.canonical_section}, "
                    f"source={figure.source_path}): {caption}"
                ),
            }
        )
        parts.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{encoded}"},
            }
        )
    return parts


def build_user_content(
    user: str, figures: Sequence[FigureAsset] | None
) -> str | list[dict[str, Any]]:
    if not figures:
        return user
    return [{"type": "text", "text": user}, *figure_message_parts(figures)]


def build_model_from_config(config: dict[str, Any], *, temperature: float, timeout: int):
    from openjiuwen.core.foundation.llm import Model
    from openjiuwen.core.foundation.llm.schema.config import (
        ModelClientConfig,
        ModelRequestConfig,
    )

    load_project_dotenv()
    oj = dict(config.get("openjiuwen") or {})
    module_cfg = dict(config.get("paper_scoring") or {})
    api_key_env = oj.get("api_key_env", "API_KEY")
    api_key = os.getenv(api_key_env, "").strip() or os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            f"missing model API credentials; set environment variable {api_key_env}"
        )
    api_base = (
        os.getenv("API_BASE")
        or _cfg_str(oj, "base_url")
        or "https://api.openai.com/v1"
    )
    model_name = (
        os.getenv("MODEL_NAME")
        or _cfg_str(module_cfg, "model")
        or _cfg_str(oj, "model")
        or "gpt-4.1-mini"
    )
    provider = (
        os.getenv("MODEL_PROVIDER")
        or _cfg_str(oj, "provider")
        or "OpenAI"
    )
    return Model(
        model_client_config=ModelClientConfig(
            client_provider=provider,
            api_key=api_key,
            api_base=api_base,
            timeout=int(timeout),
            verify_ssl=bool(_cfg_or(oj, "verify_ssl", False)),
        ),
        model_config=ModelRequestConfig(
            model_name=model_name,
            temperature=float(temperature),
            top_p=float(_cfg_or(oj, "top_p", 0.9)),
        ),
    )


async def _invoke_model(model: Any, messages: list[dict[str, Any]]) -> Any:
    for name in ("ainvoke", "ainvoke_messages", "acomplete"):
        method = getattr(model, name, None)
        if callable(method):
            return await method(messages)
    invoke = getattr(model, "invoke", None)
    if callable(invoke):
        result = invoke(messages)
        if isinstance(result, Awaitable):
            return await result
        return result
    generate = getattr(model, "generate", None)
    if callable(generate):
        result = generate(messages)
        if isinstance(result, Awaitable):
            return await result
        return result
    raise RuntimeError("OpenJiuwen Model has no invoke/generate method")


class StructuredCompleter:
    """JSON-schema completions with host-side parse and validation."""

    def __init__(
        self,
        config: dict[str, Any],
        *,
        settings: PaperScoringSettings | None = None,
        complete_fn: CompleteFn | None = None,
        model: Any | None = None,
    ):
        self.config = config
        self.settings = settings or PaperScoringSettings.from_config(config)
        self._complete_fn = complete_fn
        self._model = model
        self.calls: list[LLMCallMeta] = []

    async def complete(
        self,
        schema: type[T],
        *,
        system: str,
        user: str,
        temperature: float | None = None,
        images: Sequence[FigureAsset] | None = None,
    ) -> T:
        temp = self.settings.temperature if temperature is None else temperature
        last_error: Exception | None = None
        attempts = self.settings.max_validation_retries + 1
        figures = list(images or [])
        for attempt in range(attempts):
            repair = ""
            if last_error is not None:
                repair = (
                    "\n\nThe previous response was invalid: "
                    f"{last_error}. Return JSON that matches the schema exactly."
                )
            try:
                payload, meta = await self._one_call(
                    schema,
                    system=system,
                    user=user + repair,
                    temperature=temp,
                    images=figures,
                )
                self.calls.append(meta)
                return schema.model_validate(payload)
            except (StructuredCompletionError, ValueError) as exc:
                last_error = exc
                if attempt + 1 >= attempts:
                    raise StructuredCompletionError(
                        f"failed to obtain valid {schema.__name__}: {exc}"
                    ) from exc
        raise StructuredCompletionError(f"failed to obtain valid {schema.__name__}")

    async def _one_call(
        self,
        schema: type[BaseModel],
        *,
        system: str,
        user: str,
        temperature: float,
        images: list[FigureAsset],
    ) -> tuple[Any, LLMCallMeta]:
        schema_json = json.dumps(schema.model_json_schema(), ensure_ascii=False)
        system_with_schema = (
            f"{system}\n\nReturn ONLY JSON conforming to this schema:\n{schema_json}"
        )
        user_content = build_user_content(user, images)
        prompt_chars = len(system_with_schema) + len(user) + sum(
            len(figure.png_bytes) for figure in images
        )
        if self._complete_fn is not None:
            result = self._complete_fn(
                schema=schema,
                system=system_with_schema,
                user=user,
                temperature=temperature,
                images=images,
                user_content=user_content,
            )
            if isinstance(result, Awaitable):
                result = await result
            if isinstance(result, BaseModel):
                payload: Any = result.model_dump()
                response_chars = len(result.model_dump_json())
            elif isinstance(result, str):
                payload = extract_json_payload(result)
                response_chars = len(result)
            else:
                payload = result
                response_chars = len(json.dumps(payload, default=str))
            meta = LLMCallMeta(
                schema_name=schema.__name__,
                temperature=temperature,
                prompt_chars=prompt_chars,
                response_chars=response_chars,
                image_count=len(images),
            )
            return payload, meta

        messages = [
            {"role": "system", "content": system_with_schema},
            {"role": "user", "content": user_content},
        ]
        model = self._model or build_model_from_config(
            self.config,
            temperature=temperature,
            timeout=self.settings.timeout,
        )
        try:
            response = await self._invoke_structured(model, messages, schema, has_images=bool(images))
        except Exception as exc:
            if images and _VISION_ERROR_RE.search(str(exc)):
                raise VisionModelError(
                    "configured model does not accept image input required for paper figures: "
                    f"{exc}"
                ) from exc
            raise
        text = _response_text(response)
        payload = extract_json_payload(text)
        prompt_tokens, completion_tokens = _usage_tokens(response)
        meta = LLMCallMeta(
            schema_name=schema.__name__,
            temperature=temperature,
            prompt_chars=prompt_chars,
            response_chars=len(text),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            image_count=len(images),
        )
        return payload, meta

    async def _invoke_structured(
        self,
        model: Any,
        messages: list[dict[str, Any]],
        schema: type[BaseModel],
        *,
        has_images: bool,
    ) -> Any:
        if has_images:
            return await _invoke_model(model, messages)
        try:
            from openjiuwen.core.workflow import LLMCompConfig, LLMComponent
        except ImportError:
            return await _invoke_model(model, messages)

        try:
            client_config = getattr(model, "model_client_config", None)
            request_config = getattr(model, "model_config", None)
            if client_config is None or request_config is None:
                return await _invoke_model(model, messages)
            llm_config = LLMCompConfig(
                model_client_config=client_config,
                model_config=request_config,
                template_content=messages,
                response_format={"type": "json"},
                output_config=schema.model_json_schema(),
            )
            component = LLMComponent(llm_config)
            invoke = getattr(component, "ainvoke", None) or getattr(component, "invoke", None)
            if invoke is None:
                return await _invoke_model(model, messages)
            content = messages[-1]["content"]
            inputs = {"query": content if isinstance(content, str) else json.dumps(content)}
            result = invoke(inputs)
            if isinstance(result, Awaitable):
                result = await result
            return result
        except Exception:  # noqa: BLE001 - optional JSON-mode path; Model.invoke is the fallback
            return await _invoke_model(model, messages)
