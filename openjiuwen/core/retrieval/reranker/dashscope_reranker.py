# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
Dashscope Reranker Model Implementation

Reranker client for Alibaba DashScope, supports async invoke unlike dashscope library.
"""

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.foundation.store.base_reranker import Document
from openjiuwen.core.retrieval.common.document import MultimodalDocument
from openjiuwen.core.retrieval.reranker.standard_reranker import StandardReranker


class DashscopeReranker(StandardReranker):
    """
    Dashscope reranker client, supports text-rerank API of dashscope (which is multimodal despite the name)
    """

    end_point = "/services/rerank/text-rerank/text-rerank"

    def _request_params(self, **kwargs: dict) -> dict:
        documents = kwargs["documents"]
        instruct = kwargs.pop("instruct", None)
        parameters = dict(return_documents=False, top_n=kwargs.get("top_n", len(documents)))
        if instruct and isinstance(instruct, str):
            parameters["instruct"] = instruct
        return {
            "model": self.model_name,
            "input": dict(query=kwargs["query"], documents=documents),
            "parameters": parameters,
        }

    def _assemble_params(
        self, query: str | Document, doc: list[str | Document], instruct: bool | str, kwargs: dict
    ) -> tuple[dict, dict]:
        if isinstance(query, MultimodalDocument):
            query = query.dashscope_input
        documents = None
        if isinstance(doc, list):
            if all(isinstance(d, (str, Document)) for d in doc):
                has_multimodal = False
                documents = []
                for d in doc:
                    if isinstance(d, MultimodalDocument):
                        documents.append(d.dashscope_input)
                        has_multimodal = True
                    elif isinstance(d, Document):
                        documents.append(d.text)
                    else:
                        documents.append(d)
                if has_multimodal:
                    documents = [{"text": d} if isinstance(d, str) else d for d in documents]
        if documents is None:
            raise build_error(
                StatusCode.RETRIEVAL_RERANKER_INPUT_INVALID,
                error_msg="input to reranker must be either list[str | Document]",
            )
        headers = self._request_headers()
        params = self._request_params(query=query, documents=documents, top_n=len(documents), instruct=instruct)
        params["parameters"].update(kwargs)
        return headers, params
