# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
API Embedding Model Implementation

Universal HTTP embedding client implementation.
"""

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from itertools import chain
from typing import Any, List, Optional

import requests

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import logger
from openjiuwen.core.common.security.ssl_utils import SslUtils
from openjiuwen.core.foundation.store.base_embedding import Embedding, EmbeddingConfig
from openjiuwen.core.retrieval.common.callbacks import BaseCallback
from openjiuwen.core.session import get_current_session
from openjiuwen.core.session.tracer.handler import TracerHandlerName


class APIEmbedding(Embedding):
    """
    Universal HTTP embedding client:
    - payload: {"model": <model_name>, "input": <text or list>} (can attach kwargs)
    - headers: default application/json, optional Authorization: Bearer <api_key>
    - returns support one of the following formats:
        {"embedding": [...]}
        {"embeddings": [...]}
        {"data": [{"embedding": [...]}, ...]}
    """

    _EMBEDDING_SSL_VERIFY = "EMBEDDING_SSL_VERIFY"
    _EMBEDDING_SSL_CERT = "EMBEDDING_SSL_CERT"

    def __init__(
        self,
        config: EmbeddingConfig,
        timeout: int = 60,
        max_retries: int = 3,
        extra_headers: Optional[dict] = None,
        max_batch_size: int = 8,
        max_concurrent: int = 50,
    ):
        self.config = config
        self.model_name = config.model_name
        self.api_key = config.api_key
        self.max_batch_size = max_batch_size
        self.api_url = config.base_url or ""
        self.timeout = timeout
        self.max_retries = max_retries
        self.limiter = asyncio.Semaphore(max_concurrent)
        self._headers = {"Content-Type": "application/json"}
        if self.api_key:
            self._headers["Authorization"] = f"Bearer {self.api_key}"
        if extra_headers:
            self._headers.update(extra_headers)

        # Setup SSL configuration for requests
        # - verify=True: use system default CA certificates (backward compatible)
        # - verify=False: disable SSL verification (set EMBEDDING_SSL_VERIFY=false)
        # - verify=path: use custom CA certificate file (set EMBEDDING_SSL_CERT=/path)
        url_is_https = self.api_url.startswith("https://") if self.api_url else False
        if url_is_https:
            is_ssl_verify_off = SslUtils._bool_env(self._EMBEDDING_SSL_VERIFY, ["false"])
            ssl_cert = os.getenv(self._EMBEDDING_SSL_CERT)

            # If SSL verification is disabled, use False
            if is_ssl_verify_off:
                self._verify_ssl = False
            elif ssl_cert:
                # Custom certificate provided: use certificate file path
                # requests library supports file path in verify parameter
                self._verify_ssl = ssl_cert
            else:
                # No env vars set: use default behavior (system CA certificates)
                # This maintains backward compatibility
                self._verify_ssl = True
        else:
            # HTTP URL: no SSL verification needed
            self._verify_ssl = False

        # Cache dimension
        self._dimension: Optional[int] = None

        # For synchronized concurrency
        self._max_concurrent = max_concurrent
        self._executor: Optional[ThreadPoolExecutor] = None

    def __del__(self):
        """Custom destructor to shutdown thread pool executor at delete"""
        if getattr(self, "_executor", None) is not None:
            try:
                self._executor.shutdown(wait=False, cancel_futures=True)
            except Exception:
                self._executor = None

    @property
    def dimension(self) -> int:
        """Return embedding dimension.

        Uses sync method to get dimension, safe to call from any context.
        """
        if self._dimension is not None:
            return self._dimension

        # Use sync method to get dimension
        embedding = self.embed_query_sync("test")
        self._dimension = len(embedding)
        logger.debug(f"Determined embedding dimension: {self._dimension}")
        return self._dimension

    @property
    def executor(self) -> ThreadPoolExecutor:
        """Return thread pool executor for concurrent sync requests"""
        if self._executor is None:
            self._executor = ThreadPoolExecutor(max_workers=self._max_concurrent, thread_name_prefix="openjiuwen_embed")
        return self._executor

    @staticmethod
    def validate_embed_docs(texts: List[str | Any], kwargs: dict) -> list[str]:
        """Validate input for batch embedding request and return a list of non-empty documents"""
        if not texts:
            raise build_error(StatusCode.RETRIEVAL_EMBEDDING_INPUT_INVALID, error_msg="Empty texts list provided")
        callback_cls = kwargs.get("callback_cls", BaseCallback)
        if not isinstance(callback_cls, type) or not issubclass(callback_cls, BaseCallback):
            raise build_error(
                StatusCode.RETRIEVAL_EMBEDDING_CALLBACK_INVALID,
                error_msg=(
                    f"callback_cls in APIEmbedding.embed_documents must be a subclass of "
                    f"BaseCallback, got {type(callback_cls)}"
                ),
            )

        non_empty = [t for t in texts if t.strip()]
        if len(non_empty) != len(texts):
            raise build_error(
                StatusCode.RETRIEVAL_EMBEDDING_INPUT_INVALID,
                error_msg=f"{len(texts) - len(non_empty)} chunks are empty while embedding",
            )
        if not non_empty:
            raise build_error(
                StatusCode.RETRIEVAL_EMBEDDING_INPUT_INVALID, error_msg="All texts are empty after filtering"
            )
        return non_empty

    async def embed_query(self, text: str, **kwargs) -> List[float]:
        if not text.strip():
            raise build_error(
                StatusCode.RETRIEVAL_EMBEDDING_INPUT_INVALID, error_msg="Empty text provided for embedding"
            )
        embeddings = await self._get_embeddings(text, **kwargs)
        return embeddings[0]

    def embed_query_sync(self, text: str, **kwargs) -> List[float]:
        """Embed a single query text (sync version)."""
        if not text.strip():
            raise build_error(
                StatusCode.RETRIEVAL_EMBEDDING_INPUT_INVALID, error_msg="Empty text provided for embedding"
            )
        embeddings = self._get_embeddings_sync(text, **kwargs)
        return embeddings[0]

    async def embed_documents(
        self,
        texts: List[str],
        batch_size: Optional[int] = None,
        **kwargs,
    ) -> List[List[float]]:
        non_empty = self.validate_embed_docs(texts, kwargs)

        # Respect caller batch_size but never exceed configured max_batch_size
        bsz = batch_size or self.max_batch_size or 1
        if self.max_batch_size:
            bsz = min(bsz, self.max_batch_size)

        indices = list(range(0, len(non_empty), bsz))
        callback_obj = kwargs.get("callback_cls", BaseCallback)(seq=indices)

        async def process_batch(i: int) -> List[List[float]]:
            """Process a single batch with semaphore for concurrency control."""
            async with self.limiter:
                j = i + bsz
                batch = non_empty[i:j]
                embeddings = await self._get_embeddings(batch, **kwargs)
                callback_obj(start_idx=i, end_idx=j, batch=batch)
                return embeddings

        # Create and run tasks for all batches concurrently
        tasks = [process_batch(i) for i in indices]
        results = await asyncio.gather(*tasks)
        all_embeddings = list(chain.from_iterable(results))

        return all_embeddings

    def embed_documents_sync(
        self,
        texts: List[str],
        batch_size: Optional[int] = None,
        **kwargs,
    ) -> List[List[float]]:
        """Embed document texts"""
        non_empty = self.validate_embed_docs(texts, kwargs)

        # Respect caller batch_size but never exceed configured max_batch_size
        bsz = batch_size or self.max_batch_size or 1
        if self.max_batch_size:
            bsz = min(bsz, self.max_batch_size)

        indices = list(range(0, len(non_empty), bsz))
        callback_obj = kwargs.get("callback_cls", BaseCallback)(seq=indices)

        tasks = {}
        results = [None] * len(non_empty)

        for i in indices:
            j = i + bsz
            tasks[self.executor.submit(self._get_embeddings_sync, non_empty[i:j], **kwargs)] = i

        for task in as_completed(tasks):
            i = tasks.get(task)
            j = i + bsz
            results[i:j] = batch = task.result()
            callback_obj(start_idx=i, end_idx=j, batch=batch)

        return results

    # ------------------------------------------------------------------
    # Embedding trace events (GenAI semconv "embeddings" operation)
    # ------------------------------------------------------------------

    def _resolve_trace_tracer(self):
        """Session tracer for embedding trace events, or None.

        Embedding calls run under a component/agent session context (set by
        ``with_session_for_class``); outside one (e.g. background ingestion)
        there is no tracer to report to and events are skipped.
        """
        try:
            session = get_current_session()
        except Exception:  # noqa: BLE001 - tracing must never break embedding
            return None
        if session is None:
            return None
        try:
            tracer = session.tracer()
        except Exception:  # noqa: BLE001
            return None
        if tracer is not None and tracer.agent_handler_valid():
            return tracer
        return None

    async def _trace_embedding_trigger(self, tracer, event_name: str, span, **payload):
        """Fire one tracer event; failures never propagate into the business flow."""
        try:
            await tracer.trigger(TracerHandlerName.TRACE_AGENT.value, event_name, span=span, **payload)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"embedding trace event '{event_name}' failed: {e}")

    async def _trace_embedding_start(self, tracer, text: str | List[str]):
        """Open the embedding trace span; returns the tracer span handle."""
        span = tracer.tracer_agent_span_manager.create_agent_span()
        inputs: dict[str, Any] = {"text": text} if isinstance(text, str) else {"text_count": len(text)}
        if self._dimension is not None:
            inputs["dimension"] = self._dimension
        await self._trace_embedding_trigger(
            tracer,
            "on_embedding_start",
            span,
            inputs=inputs,
            instance_info={"class_name": type(self).__name__, "model_name": self.model_name},
        )
        return span

    async def _trace_embedding_end(self, tracer, span, result: dict, embeddings: List[List[float]]):
        """Close the embedding span with the service-side facts from the response."""
        usage = result.get("usage") if isinstance(result, dict) else None
        usage_input_tokens = None
        if isinstance(usage, dict):
            usage_input_tokens = usage.get("prompt_tokens")
            if usage_input_tokens is None:
                usage_input_tokens = usage.get("input_tokens")
        outputs: dict[str, Any] = {"vector_count": len(embeddings)}
        if self._dimension is not None:
            outputs["dimension"] = self._dimension
        if isinstance(result, dict) and result.get("model"):
            outputs["response_model"] = str(result["model"])
        if isinstance(usage_input_tokens, int):
            outputs["usage_input_tokens"] = usage_input_tokens
        await self._trace_embedding_trigger(tracer, "on_embedding_end", span, outputs=outputs)

    async def _get_embeddings(self, text: str | List[str], **kwargs) -> List[List[float]]:
        """Get embedding vectors"""
        kwargs.pop("callback_cls", None)

        trace_tracer = self._resolve_trace_tracer()
        trace_span = None
        if trace_tracer is not None:
            trace_span = await self._trace_embedding_start(trace_tracer, text)

        payload = {"model": self.model_name, "input": text, **kwargs}

        try:
            for attempt in range(self.max_retries):
                try:
                    resp = await asyncio.to_thread(
                        requests.post,
                        self.api_url,
                        json=payload,
                        headers=self._headers,
                        timeout=self.timeout,
                        verify=self._verify_ssl,
                    )
                    resp.raise_for_status()
                    result = resp.json()
                    if "embedding" in result:
                        emb = result["embedding"]
                        if isinstance(emb[0], list):
                            embeddings = emb
                        else:
                            embeddings = [emb]
                    elif "embeddings" in result:
                        embeddings = result["embeddings"]
                    elif "data" in result and isinstance(result["data"], list):
                        embeddings = []
                        for item in result["data"]:
                            if "embedding" in item:
                                embeddings.append(item["embedding"])
                        if not embeddings:
                            raise build_error(
                                StatusCode.RETRIEVAL_EMBEDDING_RESPONSE_INVALID,
                                error_msg=f"No embeddings field found in data items: {result}",
                            )
                    else:
                        raise build_error(
                            StatusCode.RETRIEVAL_EMBEDDING_RESPONSE_INVALID,
                            error_msg=f"No embeddings in response: {result}",
                        )

                    # If dimension not yet determined, get from result and cache
                    if self._dimension is None and embeddings and embeddings[0]:
                        self._dimension = len(embeddings[0])
                        logger.debug(f"Determined embedding dimension: {self._dimension}")

                    if trace_tracer is not None and trace_span is not None:
                        await self._trace_embedding_end(trace_tracer, trace_span, result, embeddings)
                    return embeddings
                except requests.exceptions.RequestException as e:
                    if attempt == self.max_retries - 1:
                        raise build_error(
                            StatusCode.RETRIEVAL_EMBEDDING_REQUEST_CALL_FAILED,
                            error_msg=f"Failed to get embedding after {self.max_retries} attempts",
                            cause=e,
                        ) from e
                    logger.warning(
                        "Embedding request failed (attempt %s/%s): %s",
                        attempt + 1,
                        self.max_retries,
                        e,
                    )
            raise build_error(
                StatusCode.RETRIEVAL_EMBEDDING_UNREACHABLE_CALL_FAILED, error_msg="Unreachable code in _get_embeddings"
            )
        except Exception as e:
            if trace_tracer is not None and trace_span is not None:
                await self._trace_embedding_trigger(trace_tracer, "on_embedding_error", trace_span, error=e)
            raise

    def _get_embeddings_sync(self, text: str | List[str], **kwargs) -> List[List[float]]:
        """Get embedding vectors (sync version)."""
        kwargs.pop("callback_cls", None)

        payload = {"model": self.model_name, "input": text, **kwargs}

        for attempt in range(self.max_retries):
            try:
                resp = requests.post(
                    self.api_url,
                    json=payload,
                    headers=self._headers,
                    timeout=self.timeout,
                    verify=self._verify_ssl,
                )
                resp.raise_for_status()
                result = resp.json()
                if "embedding" in result:
                    emb = result["embedding"]
                    if isinstance(emb[0], list):
                        embeddings = emb
                    else:
                        embeddings = [emb]
                elif "embeddings" in result:
                    embeddings = result["embeddings"]
                elif "data" in result and isinstance(result["data"], list):
                    embeddings = []
                    for item in result["data"]:
                        if "embedding" in item:
                            embeddings.append(item["embedding"])
                    if not embeddings:
                        raise build_error(
                            StatusCode.RETRIEVAL_EMBEDDING_RESPONSE_INVALID,
                            error_msg=f"No embeddings field found in data items: {result}",
                        )
                else:
                    raise build_error(
                        StatusCode.RETRIEVAL_EMBEDDING_RESPONSE_INVALID,
                        error_msg=f"No embeddings in response: {result}",
                    )

                # Cache dimension if not yet determined
                if self._dimension is None and embeddings and embeddings[0]:
                    self._dimension = len(embeddings[0])
                    logger.debug(f"Determined embedding dimension: {self._dimension}")

                return embeddings
            except requests.exceptions.RequestException as e:
                if attempt == self.max_retries - 1:
                    raise build_error(
                        StatusCode.RETRIEVAL_EMBEDDING_REQUEST_CALL_FAILED,
                        error_msg=f"Failed to get embedding after {self.max_retries} attempts",
                        cause=e,
                    ) from e
                logger.warning(
                    "Embedding request failed (attempt %s/%s): %s",
                    attempt + 1,
                    self.max_retries,
                    e,
                )
        raise build_error(
            StatusCode.RETRIEVAL_EMBEDDING_UNREACHABLE_CALL_FAILED, error_msg="Unreachable code in _get_embeddings_sync"
        )
