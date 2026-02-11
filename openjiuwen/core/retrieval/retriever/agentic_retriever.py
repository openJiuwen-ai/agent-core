# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Agentic Retriever: Adds LLM query rewriting/multi-round fusion on top of graph retrieval.
"""

from __future__ import annotations

import asyncio
from typing import Any, List, Literal, Optional, Tuple

from json_repair import repair_json

from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.logging import logger
from openjiuwen.core.retrieval.common.retrieval_result import RetrievalResult
from openjiuwen.core.retrieval.common.triple_memory import TripleMemory
from openjiuwen.core.retrieval.retriever.base import Retriever
from openjiuwen.core.retrieval.retriever.graph_retriever import GraphRetriever
from openjiuwen.core.retrieval.utils.common import deduplicate
from openjiuwen.core.retrieval.utils.fusion import rrf_fusion

_READ_PROMPT = """
Your task is to find facts that help answer an input question.

You should present these facts as knowledge triples, which are structured as ("subject", "predicate", "object").
Example:
Question: When was Neville A. Stanton's employer founded?
Facts: [["Neville A. Stanton", "employer", "University of Southampton"], ["University of Southampton", "founded in", "1862"]]

Now you are given some documents:
{docs}

Based on these documents and preliminary facts that may be provided below, find additional supporting fact(s) that may help answer the following question.
Note: if the information you are given is insufficient, output only the relevant facts you can find.

Question: {query}
Facts: {facts}

Output the facts as a JSON array of triples, where each triple is an array of [subject, predicate, object].
Example output format: [["subject1", "predicate1", "object1"], ["subject2", "predicate2", "object2"]]
"""

_REWRITE_PROMPT = """
Given a question and its associated retrieved knowledge triples, you
are asked to evaluate if the triples by themselves are sufficient
to formulate an answer to the original question.

You must respond with a JSON object in the following format:

{{
  "sufficient": true/false,
  "next_question": "string or null"
}}

- If the triples are sufficient, set "sufficient" to true and "next_question" to null
- If the triples are not sufficient, set "sufficient" to false and provide a 
suitable next question in the "next_question" field

When the triples are not sufficient, please think about the additional evidence that needs to be found to 
answer the original question, and then provide a suitable next question for retrieving this potential evidence. 
Note that you have access to all the question rewriting steps that have been performed already, if any. 
Please make sure that the next question is different from all the previous questions. Break it down into smaller questions if needed.
As the number of question rewriting steps that have been performed already increases, 
the next question should be more vague, optimising for retrieving at least some evidence that is relevant to the original question.

Here are some examples:

# Example 1:
Original Question: The Sentinelese language is the language of people of one of which islands in the Bay of Bengal ?
Knowledge triples:
(Sentinelese language, Indigenous to, Sentinelese people)
(Bay of Bengal, area, Andaman and Nicobar Islands)

Response:
{{
  "sufficient": true,
  "next_question": null
}}

# Example 2:
Original Question: Who is the coach of the team owned by David Beckham?
Knowledge triples:
(David Beckham, co-owned, Inter Miami CF)
(David Beckham, country of citizenship, United Kingdom)

Response:
{{
  "sufficient": false,
  "next_question": "Who is the coach of Inter Miami CF?"
}}

Now, please carefully consider the following case:

Question History:
Original Question: {query}
{question_rewriting_history}

Knowledge triples:
{triples}

Response (JSON only, no additional text):
"""


class AgenticRetriever(Retriever):
    """A retriever that adds LLM query rewriting and multi-round fusion on top of graph retrieval."""

    def __init__(
        self,
        graph_retriever: GraphRetriever,
        llm_client: Any,
        llm_model_name: Optional[str] = None,
        max_iter: int = 2,
        agent_topk: int = 15,
    ) -> None:
        if graph_retriever is None:
            raise build_error(
                StatusCode.RETRIEVAL_RETRIEVER_GRAPH_RETRIEVER_NOT_FOUND,
                error_msg="graph_retriever is required for AgenticRetriever",
            )
        if llm_client is None:
            raise build_error(
                StatusCode.RETRIEVAL_RETRIEVER_LLM_CLIENT_NOT_FOUND,
                error_msg="llm_client is required for AgenticRetriever",
            )
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

    async def _llm_call_async(self, prompt: str) -> Optional[str]:
        """
        Invoke the LLM.

        Args:
            prompt: Prompt to send to the LLM.

        Returns:
            LLM response string, or None if invocation failed.
        """
        try:
            resp = await self.llm.invoke(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
            )
            return resp.content if hasattr(resp, "content") else str(resp)
        except Exception as e:
            logger.warning("[Agentic] LLM invocation failed: %s", e)
            return None

    async def _rewrite(
        self, query: str, triples_str: str, question_history: Optional[List[str]] = None
    ) -> Optional[str]:
        """
        Analyze if the current facts are sufficient to answer the query, generating a follow-up question if not.

        Args:
            query: Original user question.
            triples_str: String representation of knowledge triples in Triple memory.
            question_history: Question history list.

        Returns:
            Next question to ask if more info is needed, or None if sufficient.
        """
        # Format question rewriting history
        if question_history and len(question_history) > 1:
            history_lines = []
            for i, q in enumerate(question_history[1:], start=1):
                history_lines.append(f"Rewritten Question {i}: {q}")
            question_rewriting_history = "\n".join(history_lines)
        else:
            question_rewriting_history = "(No rewriting steps yet)"

        prompt = _REWRITE_PROMPT.format(
            query=query,
            triples=triples_str,
            question_rewriting_history=question_rewriting_history,
        )
        response = await self._llm_call_async(prompt)
        if response is None:
            return None

        # Parse JSON response
        try:
            response_json = repair_json(response, return_objects=True)
            sufficient = response_json.get("sufficient", False)
            next_question = response_json.get("next_question")

            if sufficient or not next_question:
                return None
            return next_question
        except Exception as e:
            logger.warning("[Agentic] Failed to parse rewrite response as JSON: %s. Response: %s", e, response)
            return None

    async def _read(
        self,
        query: str,
        passages: List[RetrievalResult],
        existing_facts: Optional[List[Tuple[str, ...]]] = None,
    ) -> List[Tuple[str, ...]]:
        """
        Extract knowledge triples from retrieved passages using the LLM.

        Args:
            query: Search query.
            passages: Retrieved passages.
            existing_facts: Optional list of facts (triples).

        Returns:
            List of extracted triples.
        """
        docs = "\n\n".join(p.text for p in passages[:5])
        facts_str = ", ".join(str(fact) for fact in existing_facts) if existing_facts else "None"
        prompt = _READ_PROMPT.format(
            docs=docs,
            query=query,
            facts=facts_str,
        )
        response = await self._llm_call_async(prompt)
        if response is None:
            return []

        try:
            response_json = repair_json(response, return_objects=True)
            triples = [tuple(triple) for triple in response_json if isinstance(triple, list) and len(triple) == 3]
            return triples
        except Exception as e:
            logger.warning("[Agentic] Failed to parse read response as JSON: %s. Response: %s", e, response)
            return []

    async def retrieve(
        self,
        query: str,
        top_k: int = 5,
        score_threshold: Optional[float] = None,
        mode: Optional[Literal["vector", "sparse", "hybrid"]] = None,
        **kwargs,
    ) -> List[RetrievalResult]:
        if top_k is None:
            raise build_error(
                StatusCode.RETRIEVAL_RETRIEVER_TOP_K_NOT_FOUND, error_msg="top_k is required for AgenticRetriever"
            )
        topk = top_k
        resolved_mode: Literal["vector", "sparse", "hybrid"] = mode if mode is not None else self._default_mode
        graph_expansion = kwargs.pop("graph_expansion", False)

        queries = [query]
        history_results: List[List[RetrievalResult]] = []
        memory = TripleMemory()

        for turn in range(1, self.max_iter + 1):
            q = queries[-1]
            logger.info("[Agentic] turn=%d query=%s", turn, q)

            chunk_retriever = self.graph_retriever.get_retriever_for_mode(resolved_mode, is_chunk=True)
            chunk_results = await chunk_retriever.retrieve(
                query=q,
                top_k=top_k,
                score_threshold=score_threshold,
                mode=resolved_mode,
            )

            if graph_expansion:
                proximal_triples = await self._read(q, chunk_results, existing_facts=None)
                linked_triples = await self._link_triples(proximal_triples, resolved_mode)
                logger.debug(
                    "[Agentic] After the first-read in turn=%r we get proximal_triples=%r\n and linked_triples=%r",
                    turn,
                    proximal_triples,
                    [x.text for x in linked_triples],
                )

                chunk_results = await self.graph_retriever.graph_expansion(
                    query=q,
                    chunks=chunk_results,
                    triples=linked_triples,
                    topk=top_k,
                    mode=resolved_mode,
                )

            triples = await self._read(query, chunk_results, existing_facts=memory.memory)

            memory.batch_extend_memory(triples)
            history_results.append(chunk_results)
            logger.debug(
                "[Agentic] After memory expansion, turn=%r memory=%r",
                turn,
                memory.memory,
            )

            if turn >= self.max_iter:
                break

            rewritten = await self._rewrite(query, memory.triples_str, question_history=queries)
            logger.info("[Agentic] rewritten=%s", rewritten)
            if not rewritten:
                logger.info(
                    "[Agentic] stopping at turn=%d as sufficient evidence found from memory-%s.",
                    turn,
                    memory.triples_str,
                )
                break
            queries.append(rewritten)

        ret = await self._link_passages(memory.memory, resolved_mode)
        combined = rrf_fusion(ret + history_results)[:topk]
        logger.info(
            "[Agentic] Agent finished: reference chunks=%d, SearchAgent rounds=%d, chunks after rrf fusion=%d",
            sum(len(g) for g in ret),
            len(history_results),
            len(combined),
        )
        return combined

    async def _link_triples(self, triples: List[Tuple[str, ...]], mode: str) -> List[RetrievalResult]:
        """
        Links proximal triples to triples in the knowledge base.

        Args:
            triples: List of triples.
            mode: Retrieval mode (vector, sparse, hybrid).

        Returns:
            List of unique linked triples.
        """
        tasks = []
        triple_retriever = self.graph_retriever.get_retriever_for_mode(mode, is_chunk=False)
        for triple in triples:
            triple_str = " ".join(triple)
            # Fetch SearchResult instead of RetrievalResult since we need ids for deduplication
            task = triple_retriever.retrieve_search_results(
                query=triple_str,
                top_k=1,
                mode=mode,
            )
            tasks.append(task)
        search_results = await asyncio.gather(*tasks)

        # Flatten and extract the first result if available
        raw_results = [res_list[0] for res_list in search_results if res_list]
        search_results = deduplicate(raw_results, key=lambda node: node.id)

        # Map back to RetrievalResult as required by the Graph Expansion flow
        retrieval_results = []
        for result in search_results:
            retrieval_result = RetrievalResult(
                text=result.text,
                score=result.score,
                metadata=result.metadata,
                doc_id=result.metadata.get("doc_id"),
                chunk_id=result.metadata.get("chunk_id"),
            )
            retrieval_results.append(retrieval_result)
        return retrieval_results

    async def _link_passages(self, triples: List[Tuple[str, ...]], mode: str) -> List[List[RetrievalResult]]:
        """
        Links triples to passages in the knowledge base.

        Args:
            triples: List of triples.
            mode: Retrieval mode (vector, sparse, hybrid).

        Returns:
            List of lists of linked passages.
        """
        chunk_retriever = self.graph_retriever.get_retriever_for_mode(mode, is_chunk=True)
        tasks = []
        for triple in triples:
            triple_str = " ".join(triple)
            task = chunk_retriever.retrieve(
                query=triple_str,
                top_k=5,
                mode=mode,
            )
            tasks.append(task)
        results = await asyncio.gather(*tasks)
        return results

    async def batch_retrieve(
        self,
        queries: List[str],
        top_k: int = 5,
        **kwargs,
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
