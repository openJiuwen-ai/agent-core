# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Agentic Retriever: Adds LLM query rewriting/multi-round fusion on top of graph retrieval.
"""

from __future__ import annotations

import asyncio
from typing import Any, List, Optional, Literal

from openjiuwen.core.common.logging import logger

from openjiuwen.core.retrieval.retriever.base import Retriever
from openjiuwen.core.retrieval.common.retrieval_result import RetrievalResult
from openjiuwen.core.retrieval.utils.fusion import rrf_fusion
from openjiuwen.core.retrieval.retriever.graph_retriever import GraphRetriever


class AgenticRetriever(Retriever):
    """A retriever that adds LLM query rewriting and multi-round fusion on top of graph retrieval."""

    def __init__(
        self,
        graph_retriever: GraphRetriever,
        llm_client: Any,
        llm_model_name: Optional[str] = None,
        max_iter: int = 3,
        agent_topk: int = 15,
    ) -> None:
        if graph_retriever is None:
            raise ValueError("graph_retriever is required for AgenticRetriever")
        if llm_client is None:
            raise ValueError("llm_client is required for AgenticRetriever")
        self.graph_retriever = graph_retriever
        self.llm = llm_client
        self.llm_model_name = llm_model_name
        self.max_iter = int(max_iter)
        self.agent_topk = int(agent_topk)
        self._default_top_k = None  # top_k must be provided by caller
        index_type = getattr(graph_retriever, "index_type", None) or "hybrid"
        if index_type == "vector":
            self._default_mode: Literal["vector", "sparse", "hybrid"] = "vector"
        elif index_type == "bm25":
            self._default_mode = "sparse"
        else:
            self._default_mode = "hybrid"

    def _log(self, msg: str, *args) -> None:
        logger.debug(msg, *args)

    def _llm_call(self, prompt: str) -> str:
        resp = self.llm.invoke(
            model_name=self.llm_model_name,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
        )
        return resp.content if hasattr(resp, "content") else str(resp)

    async def _llm_call_async(self, prompt: str) -> str:
        if hasattr(self.llm, "ainvoke"):
            resp = await self.llm.ainvoke(
                model_name=self.llm_model_name,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
            )
            return resp.content if hasattr(resp, "content") else str(resp)
        return await asyncio.to_thread(self._llm_call, prompt)

    async def _rewrite(self, query: str, passages: List[RetrievalResult]) -> Optional[str]:
        context = "\n\n".join(p.text for p in passages[:5])
        prompt = (
            "You are a retrieval assistant. Please rewrite the query based on the retrieved passages "
            "to make it more specific or correct errors. "
            "If no rewriting is needed, return the original query. Output only the rewritten query.\n\n"
            f"Original query: {query}\n"
            f"Retrieved passages:\n{context}"
        )
        rewritten = (await self._llm_call_async(prompt)).strip()
        return rewritten or None

    async def retrieve(
        self,
        query: str,
        top_k: int = 5,
        score_threshold: Optional[float] = None,
        mode: Optional[Literal["vector", "sparse", "hybrid"]] = None,
        **kwargs: Any,
    ) -> List[RetrievalResult]:
        if top_k is None:
            raise ValueError("top_k is required for AgenticRetriever")
        topk = top_k
        resolved_mode: Literal["vector", "sparse", "hybrid"] = (
            mode if mode is not None else self._default_mode
        )

        queries = [query]
        history_results: List[List[RetrievalResult]] = []

        for turn in range(1, self.max_iter + 1):
            q = queries[-1]
            logger.info("[Agentic] turn=%d query=%s", turn, q)
            chunks = await self.graph_retriever.retrieve(
                q,
                top_k=self.agent_topk,
                score_threshold=score_threshold,
                mode=resolved_mode,
                **kwargs,
            )
            history_results.append(chunks)

            if turn >= self.max_iter:
                break

            rewritten = await self._rewrite(q, chunks)
            logger.info("[Agentic] rewritten=%s", rewritten)
            if not rewritten or rewritten == q:
                break
            queries.append(rewritten)

        fused = rrf_fusion(history_results, k=60) if len(history_results) > 1 else history_results[0]
        return fused[:topk]

    async def batch_retrieve(
        self,
        queries: List[str],
        top_k: int = 5,
        **kwargs: Any,
    ) -> List[List[RetrievalResult]]:
        tasks = [self.retrieve(query, top_k=top_k, **kwargs) for query in queries]
        return await asyncio.gather(*tasks)

    async def close(self) -> None:
        import inspect

        if hasattr(self.graph_retriever, "close"):
            close_fn = self.graph_retriever.close
            if inspect.iscoroutinefunction(close_fn):
                await close_fn()
            else:
                close_fn()
