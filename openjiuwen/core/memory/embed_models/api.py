#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import Any
import aiohttp
from openjiuwen.core.common.logging import logger
from openjiuwen.core.memory.embed_models.base import EmbedModel
from openjiuwen.core.common.security.url_utils import UrlUtils


class APIEmbedModel(EmbedModel):
    """
    - payload: {"model": <model_name>, "input": <text or list>}（attach kwargs）
    - headers: default application/json，optional Authorization: Bearer <api_key>
    Supports:
      {"embedding": [...]}
      {"embeddings": [...]}
      {"data": [{"embedding": [...]}, ...]}
    """

    def __init__(
        self,
        model_name: str,
        base_url: str,
        timeout: int = 60,
        max_retries: int = 3,
        api_key: str | None = None,
        extra_headers: dict | None = None,
    ):
        self.model_name = model_name
        self.api_key = api_key
        self.api_url = base_url
        self.timeout = timeout
        self.max_attempts = max_retries
        self._headers = {"Content-Type": "application/json"}
        if self.api_key:
            self._headers["Authorization"] = f"Bearer {self.api_key}"
        if extra_headers:
            self._headers.update(extra_headers)
        self._session: aiohttp.ClientSession | None = None

    async def _fetch_session(self) -> aiohttp.ClientSession:
        """Return an active aiohttp session, recreate on demand."""
        session = getattr(self, "_session", None)
        proxy_url = UrlUtils.get_global_proxy_url(self.api_url)
        if not session or session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.timeout),
                headers=self._headers,
                proxy=proxy_url
            )
        return self._session

    async def embed_query(self, text: str, **kwargs: Any) -> list[float]:
        if not text.strip():
            raise ValueError("Empty text provided for embedding")
        embs = await self._get_embeddings(text, **kwargs)
        return embs[0]

    async def _get_embeddings(self, text: str | list[str], **kwargs):
        payload = {"model": self.model_name, "input": text, **kwargs}
        for attempt in range(self.max_attempts):
            try:
                session = await self._fetch_session()
                async with session.post(self.api_url, json=payload) as resp:
                    result = await resp.json()
                if "embedding" in result:
                    return [result["embedding"]]
                if "embeddings" in result:
                    return result["embeddings"]
                if "data" in result:
                    return [d["embedding"] for d in result["data"]]

                raise ValueError(f"Invalid embedding response: {result}")

            except Exception as e:
                logger.warning(
                    f"Embedding request failed (attempt {attempt+1}/{self.max_attempts}): {e}"
                )
                if attempt == self.max_attempts - 1:
                    raise

        raise RuntimeError("Unreachable")

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
