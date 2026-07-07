# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Thin OpenAI-chat-completions-compatible VLM client for ReKep constraint generation.

Kept deliberately independent from agent-core's own ``Model``/LLM plumbing:
this call happens once per sub_task inside
``So101RekepExecutor.execute()``, is billed/rate-limited separately from the
DeepAgent's main model, and often benefits from a different model choice
(e.g. a cheaper/faster vision model than the main agent uses). Defaults to
OpenRouter's endpoint (matching the reference implementation this was ported
from) but works with any OpenAI-compatible endpoint via ``base_url``.
"""

from __future__ import annotations

import base64
import io
from typing import Optional

import numpy as np
from PIL import Image

_DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"


class VlmClient:
    """OpenAI-SDK-compatible client used only by ReKep-style constraint generation."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = _DEFAULT_BASE_URL,
        jpeg_quality: int = 90,
    ) -> None:
        try:
            from openai import OpenAI
        except ImportError as e:
            raise ImportError(
                f"openai package is not installed; run `pip install 'openjiuwen[robotic-arm-so101-rekep]'` ({e})"
            ) from e
        if not api_key:
            raise ValueError("vlm api_key is required")

        self._model = model
        self._jpeg_quality = jpeg_quality
        self._client = OpenAI(api_key=api_key, base_url=base_url)

    def query(self, prompt: str, image: Optional[np.ndarray] = None, max_tokens: int = 3000) -> str:
        """Send ``prompt`` (+ optional RGB image) to the VLM; returns the raw text reply."""
        content: list[dict] = []
        if image is not None:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{self._encode_image(image)}"},
                }
            )
        content.append({"type": "text", "text": prompt})

        response = self._client.chat.completions.create(
            model=self._model,
            messages=[{"role": "user", "content": content}],
            max_tokens=max_tokens,
        )
        return response.choices[0].message.content or ""

    def _encode_image(self, image: np.ndarray) -> str:
        buf = io.BytesIO()
        Image.fromarray(image).save(buf, format="JPEG", quality=self._jpeg_quality)
        return base64.b64encode(buf.getvalue()).decode("utf-8")


__all__ = ["VlmClient"]
