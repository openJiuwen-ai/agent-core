#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from abc import ABCMeta, abstractmethod
from typing import Any
import asyncio


class EmbedModel(metaclass=ABCMeta):

    @abstractmethod
    async def embed_query(self, text: str, **kwargs: Any) -> list[float]:
        """Embed a single query."""
        pass

    async def embed_queries(self, texts: list[str], **kwargs: Any) -> list[list[float]]:
        """Embed queries (default: map over embed_query)."""
        tasks = [self.embed_query(x, **kwargs) for x in texts]
        return await asyncio.gather(*tasks)

    async def get_embedding_dimension(self) -> int:
        return len(await self.embed_query("X"))
