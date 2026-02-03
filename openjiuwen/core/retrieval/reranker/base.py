# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
Reranker Model Abstract Base Class

Provides a unified interface for reranker models.
"""

from abc import ABC, abstractmethod

from openjiuwen.core.retrieval.common.document import Document


class Reranker(ABC):
    """Reranker model abstract base class"""

    @abstractmethod
    async def rerank(
        self, query: str, doc: list[str | Document], instruct: bool | str = True, **kwargs
    ) -> dict[str, float]:
        """
        Rerank documents and return a mapping from document to relevance score
            query: query string
            doc: list of documents to rerank
            instruct: whether to provide instruction to reranker, pass in a string for custom instruction
            **kwargs: extra arguments
        """

    @abstractmethod
    def rerank_sync(
        self, query: str, doc: list[str | Document], instruct: bool | str = True, **kwargs
    ) -> dict[str, float]:
        """
        Rerank documents and return a mapping from document to relevance score
            query: query string
            doc: list of documents to rerank
            instruct: whether to provide instruction to reranker, pass in a string for custom instruction
            **kwargs: extra arguments
        """

    def _request_headers(self, **kwargs: dict) -> dict:
        ...

    def _request_params(self, **kwargs: dict) -> dict:
        ...

    def _parse_response(self, response_data: dict, doc: list[str | Document]) -> dict[str, float]:
        ...
