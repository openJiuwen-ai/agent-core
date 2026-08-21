# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import asyncio
import base64
import logging
import mimetypes
from pathlib import Path
from typing import Any, AsyncIterator, Dict, Optional
from urllib.parse import urlparse

from openai import OpenAI

from openjiuwen.core.foundation.tool.base import Tool
from openjiuwen.harness.schema.config import (
    VisionModelConfig,
    is_vision_model_config_complete,
)
from openjiuwen.harness.prompts.tools import build_tool_card
from openjiuwen.harness.tools.base_tool import ToolOutput

logger = logging.getLogger(__name__)

SANDBOX_PATH_MARKER = "home/user"

DEFAULT_OCR_PROMPT = """You are a meticulous OCR assistant.
Extract all visible text from the image.
Preserve structure, line breaks, numbers, symbols, and uncertain text when possible.
If no text is visible, reply with 'No text found'."""

DEFAULT_VQA_PROMPT_TEMPLATE = """You are a careful visual analysis assistant.
Use the image and the OCR result below to answer the user's question accurately.

OCR result:
{ocr_text}

Question:
{question}

Provide a concise but complete answer. If something is uncertain, say so explicitly."""


def _is_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _guess_mime_type(file_path: str) -> str:
    mime_type, _ = mimetypes.guess_type(file_path)
    return mime_type or "image/jpeg"


def _build_image_content(image_path_or_url: str) -> Dict[str, Any]:
    if SANDBOX_PATH_MARKER in image_path_or_url:
        raise ValueError(
            "Vision tools cannot access sandbox-only paths. Use a local path outside the sandbox or an https URL."
        )

    if _is_http_url(image_path_or_url):
        return {
            "type": "image_url",
            "image_url": {
                "url": image_path_or_url,
            },
        }

    image_path = Path(image_path_or_url).expanduser()
    if not image_path.exists() or not image_path.is_file():
        raise FileNotFoundError(
            f"Image path does not exist or is not a file: {image_path_or_url}"
        )

    image_bytes = image_path.read_bytes()
    image_base64 = base64.b64encode(image_bytes).decode("utf-8")
    mime_type = _guess_mime_type(str(image_path))
    return {
        "type": "image_url",
        "image_url": {
            "url": f"data:{mime_type};base64,{image_base64}",
        },
    }


def _extract_response_text(response: Any) -> str:
    message = response.choices[0].message
    content = getattr(message, "content", "")

    if isinstance(content, str):
        return content.strip()

    if isinstance(content, list):
        chunks = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                chunks.append(item.get("text", ""))
            else:
                text = getattr(item, "text", None)
                if text:
                    chunks.append(text)
        return "\n".join(chunk.strip() for chunk in chunks if chunk).strip()

    return str(content).strip()


def _invoke_chat_completion(
    config: VisionModelConfig,
    prompt: str,
    image_content: Dict[str, Any],
) -> tuple[str, dict | None]:
    """Call vision model and return (response_text, usage_dict).

    usage_dict is extracted from the API response for the caller to accumulate.
    """
    if not config.api_key:
        raise ValueError(
            "Vision model config missing api_key."
        )

    client = OpenAI(api_key=config.api_key, base_url=config.base_url)
    response = client.chat.completions.create(
        model=config.model,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    image_content,
                ],
            }
        ],
    )
    # Extract token usage from API response (returns dict, does NOT write to global)
    usage: dict | None = None
    try:
        from openjiuwen.harness.tools.multimodal_telemetry import record_multimodal_token_usage
        usage = record_multimodal_token_usage(response, model_name=config.model, system="openai")
    except Exception as exc:
        logger.warning("[vision] record_multimodal_token_usage failed: %s", exc)
    response_text = _extract_response_text(response)
    if not response_text:
        raise ValueError("Vision model returned empty content.")
    return response_text, usage


def _require_vision_model_config(
    vision_model_config: Optional[VisionModelConfig],
) -> VisionModelConfig:
    if vision_model_config is None:
        raise ValueError(
            "Vision model config is not set. Pass "
            "DeepAgentConfig.vision_model_config or construct "
            "the tool with VisionModelConfig."
        )
    if not vision_model_config.api_key:
        raise ValueError("Vision model config missing api_key.")
    if not vision_model_config.base_url:
        raise ValueError("Vision model config missing base_url.")
    if not vision_model_config.model:
        raise ValueError("Vision model config missing model.")
    return vision_model_config


async def _call_vision_model(
    image_path_or_url: str,
    prompt: str,
    vision_model_config: Optional[VisionModelConfig],
) -> tuple[str, str, list[dict | None]]:
    """Call vision model, returning (response_text, model_name, usage_list).

    usage_list contains one entry per model call (for accumulation by the caller).
    """
    config = _require_vision_model_config(vision_model_config)
    image_content = await asyncio.to_thread(_build_image_content, image_path_or_url)

    last_error: Exception | None = None
    for attempt in range(1, config.max_retries + 1):
        try:
            response_text, usage = await asyncio.to_thread(
                _invoke_chat_completion,
                config,
                prompt,
                image_content,
            )
            return response_text, config.model, [usage]
        except Exception as exc:  
            last_error = exc
            error_text = str(exc)
            is_retryable = any(code in error_text for code in ("429", "500", "502", "503", "504"))
            if attempt == config.max_retries or not is_retryable:
                break
            await asyncio.sleep(2 ** (attempt - 1))

    if last_error is None:
        raise RuntimeError("Vision model call failed without a captured exception.")
    raise last_error


def _store_aggregated_usage(usages: list[dict | None]) -> None:
    """Aggregate multiple model-call usages and store once per tool invocation."""
    try:
        from openjiuwen.harness.tools.multimodal_telemetry import aggregate_usage, store_multimodal_usage
        store_multimodal_usage(aggregate_usage(usages))
    except Exception as exc:
        logger.warning("[vision] store_aggregated_usage failed: %s", exc)


class ImageOCRTool(Tool):

    def __init__(
        self,
        language: str = "cn",
        vision_model_config: Optional[VisionModelConfig] = None,
        agent_id: Optional[str] = None,
    ):
        super().__init__(build_tool_card("image_ocr", "ImageOCRTool", language, agent_id=agent_id))
        self.vision_model_config = vision_model_config

    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> ToolOutput:
        _ = kwargs
        image_path_or_url = inputs.get("image_path_or_url")
        prompt = inputs.get("prompt") or DEFAULT_OCR_PROMPT

        try:
            text, model, usages = await _call_vision_model(
                image_path_or_url,
                prompt,
                self.vision_model_config,
            )
        except Exception as exc:  # noqa: BLE001
            return ToolOutput(success=False, error=str(exc))

        _store_aggregated_usage(usages)

        return ToolOutput(
            success=True,
            data={
                "text": text,
                "model": model,
            },
        )

    async def stream(self, inputs: Dict[str, Any], **kwargs) -> AsyncIterator[Any]:
        _ = inputs, kwargs
        if False:
            yield None


class VisualQuestionAnsweringTool(Tool):

    def __init__(
        self,
        language: str = "cn",
        vision_model_config: Optional[VisionModelConfig] = None,
        agent_id: Optional[str] = None,
    ):
        super().__init__(
            build_tool_card(
                "visual_question_answering",
                "VisualQuestionAnsweringTool",
                language,
                agent_id=agent_id,
            )
        )
        self.vision_model_config = vision_model_config

    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> ToolOutput:
        _ = kwargs
        image_path_or_url = inputs.get("image_path_or_url")
        question = inputs.get("question")
        include_ocr = inputs.get("include_ocr", True)
        ocr_prompt = inputs.get("ocr_prompt") or DEFAULT_OCR_PROMPT

        try:
            ocr_text = ""
            model = ""
            all_usages: list[dict | None] = []

            if include_ocr:
                ocr_text, model, ocr_usages = await _call_vision_model(
                    image_path_or_url,
                    ocr_prompt,
                    self.vision_model_config,
                )
                all_usages.extend(ocr_usages)

            prompt = (
                DEFAULT_VQA_PROMPT_TEMPLATE.format(ocr_text=ocr_text or "No OCR used", question=question)
                if include_ocr
                else question
            )
            answer, answer_model, vqa_usages = await _call_vision_model(
                image_path_or_url,
                prompt,
                self.vision_model_config,
            )
            model = answer_model or model
            all_usages.extend(vqa_usages)
        except Exception as exc:  # noqa: BLE001
            return ToolOutput(success=False, error=str(exc))

        # Store aggregated usage (OCR + VQA) once per tool invocation
        _store_aggregated_usage(all_usages)

        return ToolOutput(
            success=True,
            data={
                "answer": answer,
                "ocr_text": ocr_text if include_ocr else None,
                "model": model,
            },
        )

    async def stream(self, inputs: Dict[str, Any], **kwargs) -> AsyncIterator[Any]:
        _ = inputs, kwargs
        if False:
            yield None


def create_vision_tools(
    language: str = "cn",
    vision_model_config: Optional[VisionModelConfig] = None,
    agent_id: Optional[str] = None,
) -> list[Tool]:
    if not is_vision_model_config_complete(vision_model_config):
        return []
    return [
        ImageOCRTool(
            language=language,
            vision_model_config=vision_model_config,
            agent_id=agent_id,
        ),
        VisualQuestionAnsweringTool(
            language=language,
            vision_model_config=vision_model_config,
            agent_id=agent_id,
        ),
    ]


__all__ = [
    "ImageOCRTool",
    "VisualQuestionAnsweringTool",
    "create_vision_tools",
]
