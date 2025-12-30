# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Ollama Embedding Model Implementation

Implementation of Ollama embedding model.
"""
from typing import Any, List, Optional

import requests

from openjiuwen.core.common.logging import logger
from openjiuwen.core.retrieval.embedding.base import Embedding
from openjiuwen.core.retrieval.common.config import EmbeddingConfig


class OllamaEmbedding(Embedding):
    """Ollama embedding model implementation."""

    def __init__(
        self,
        config: EmbeddingConfig,
        hf_tokenizer_name: Optional[str] = None,
        timeout: int = 60,
        max_retries: int = 3,
        extra_headers: Optional[dict] = None,
    ):
        """
        Initialize Ollama embedder.

        Args:
            config: Embedding model configuration
            hf_tokenizer_name: HuggingFace tokenizer name (optional)
            timeout: Request timeout in seconds
            max_retries: Maximum retry count
            extra_headers: Additional request headers
        """
        self.config = config
        self.model_name = config.model_name
        self.base_url = (config.base_url or "").rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.embed_url = f"{self.base_url}/api/embed"
        self._headers = extra_headers or {}
        
        # Initialize tokenizer if provided
        if hf_tokenizer_name:
            try:
                from transformers import AutoTokenizer
                self._tokenizer = AutoTokenizer.from_pretrained(hf_tokenizer_name)
            except ImportError:
                logger.warning("transformers not available, tokenizer disabled")
                self._tokenizer = None
        else:
            self._tokenizer = None

        # Cache dimension
        self._dimension: Optional[int] = None
        
        # Test connection and model availability
        self._verify_model_availability()

    @property
    def tokenizer(self):
        return self._tokenizer

    def _verify_model_availability(self):
        """Verify that Ollama is running and the model is available."""
        try:
            # Check if Ollama is running
            response = requests.get(f"{self.base_url}/api/tags", timeout=5)
            response.raise_for_status()

            # Check if the model is available
            models = response.json().get("models", [])
            model_names = [model["name"] for model in models]

            if self.model_name not in model_names:
                raise ValueError(
                    f"Model '{self.model_name}' not found in available models: {model_names}. "
                    f"Make sure to pull the model first: ollama pull {self.model_name}"
                )
        except requests.exceptions.RequestException as e:
            raise ConnectionError(
                f"Could not connect to Ollama at {self.base_url}. Is Ollama running?"
            ) from e

    @property
    def dimension(self) -> int:
        """Return embedding dimension"""
        if self._dimension is None:
            # Get dimension by embedding a test text
            try:
                import asyncio
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # If event loop is running, use default value
                    self._dimension = 768
                else:
                    test_embedding = loop.run_until_complete(
                        self._get_ollama_embedding("X")
                    )
                    if test_embedding:
                        self._dimension = len(test_embedding[0])
                    else:
                        self._dimension = 768
            except Exception:
                # If failed to get, use default value
                self._dimension = 768
        return self._dimension

    async def embed_query(self, text: str, **kwargs: Any) -> List[float]:
        if not text.strip():
            raise ValueError("Empty text provided for embedding")

        embeddings = await self._get_ollama_embedding(text, **kwargs)
        return embeddings[0]

    async def embed_documents(
        self,
        texts: List[str],
        batch_size: Optional[int] = None,
        **kwargs: Any,
    ) -> List[List[float]]:
        if not texts:
            raise ValueError("Empty texts list provided")

        # Filter out empty texts
        non_empty_texts = [text for text in texts if text.strip()]
        if len(non_empty_texts) != len(texts):
            raise ValueError(
                f"{len(texts) - len(non_empty_texts)} chunks are empty while embedding"
            )

        if not non_empty_texts:
            raise ValueError("All texts are empty after filtering")

        # Process in batches if batch_size is specified
        if batch_size is not None and batch_size > 0:
            all_embeddings = []
            for i in range(0, len(non_empty_texts), batch_size):
                j = i + batch_size
                batch_texts = non_empty_texts[i:j]
                batch_embeddings = await self._get_ollama_embedding(
                    batch_texts, **kwargs
                )
                all_embeddings.extend(batch_embeddings)
            embeddings = all_embeddings
        else:
            embeddings = await self._get_ollama_embedding(non_empty_texts, **kwargs)

        return embeddings

    async def _get_ollama_embedding(
        self, text: str | List[str], **kwargs
    ) -> List[List[float]]:
        """Get ollama embedding"""
        import asyncio
        
        if not text:
            raise ValueError("Empty text or list provided for embedding")

        payload = {
            "model": self.model_name,
            "input": text,
            "truncate": False,
            **kwargs,
        }

        for attempt in range(self.max_retries):
            try:
                response = await asyncio.to_thread(
                    requests.post,
                    self.embed_url,
                    json=payload,
                    headers=self._headers,
                    timeout=self.timeout,
                )
                response.raise_for_status()
                result = response.json()
                if "embeddings" not in result:
                    raise ValueError(f"No embeddings in response: {result}")

                return result["embeddings"]

            except requests.exceptions.RequestException as e:
                if attempt == self.max_retries - 1:
                    raise RuntimeError(
                        f"Failed to get embedding after {self.max_retries} attempts"
                    ) from e
                logger.warning(f"Attempt {attempt + 1} failed, retrying: {e}")

        raise RuntimeError("This should never be reached")
