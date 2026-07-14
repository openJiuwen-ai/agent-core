# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Embedding providers for memory system."""

import os
from abc import ABC, abstractmethod
from typing import List, Optional
from openjiuwen.core.foundation.store.base_embedding import EmbeddingConfig


class EmbeddingProvider(ABC):
    """Base class for embedding providers."""

    id: str = "base"
    model: str = "base"
    dims: int = 0

    @property
    def config_fingerprint(self) -> str:
        """Stable fingerprint of the configuration that affects embedding vectors.

        Used to detect config changes (base_url / api_key / model) and to key
        the embedding cache. Subclasses should override to incorporate the
        fields that actually influence the produced vectors.
        """
        return f"{self.id}:{self.model}"

    @abstractmethod
    async def embed_query(self, text: str) -> List[float]:
        """Generate embedding for a query."""
        pass

    @abstractmethod
    async def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings for multiple documents."""
        pass


class OpenAICompatibleEmbeddingProvider(EmbeddingProvider):
    """OpenAI-compatible embedding provider (supports DashScope, OpenAI, etc.)."""

    id: str = "openai_compatible"

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        base_url: Optional[str] = None
    ):
        self.api_key = api_key
        self.model = model
        self.base_url = self.normalize_base_url(base_url)
        self.dims = 1024
        self._client = None

    @staticmethod
    def normalize_base_url(base_url: Optional[str]) -> Optional[str]:
        """Normalize base_url so equivalent endpoints share one form.

        - strip whitespace
        - drop a trailing "/embeddings" (the request path appends it back)
        - drop a trailing slash
        e.g. "https://x.com/embeddings" and "https://x.com/" both -> "https://x.com"
        """
        if not base_url:
            return base_url
        url = base_url.strip()
        # collapse any number of trailing slashes BEFORE the /embeddings
        # suffix check, so "https://x.com/embeddings/" also normalizes
        # (otherwise endswith("/embeddings") misses the trailing slash).
        while url.endswith("/"):
            url = url[:-1]
        if url.endswith("/embeddings"):
            url = url.rsplit("/embeddings", 1)[0]
        return url

    @property
    def config_fingerprint(self) -> str:
        """Fingerprint covering all fields that affect embedding vectors.

        Includes normalized base_url and a truncated sha256 of api_key (never
        store the raw key). Changing endpoint / key / model yields a different
        fingerprint, which:
          - makes _should_full_reindex detect the change, and
          - isolates embedding_cache entries per (base_url, api_key, model).
        """
        import hashlib
        api_key_hash = hashlib.sha256((self.api_key or "").encode()).hexdigest()[:16]
        return f"{self.id}:{self.model or ''}:{self.base_url or ''}:{api_key_hash}"
    
    def _get_client(self):
        """Get or create HTTP client."""
        if self._client is None:
            import httpx
            self._client = httpx.AsyncClient(timeout=60.0)
        return self._client
    
    async def embed_query(self, text: str) -> List[float]:
        """Generate embedding for a query."""
        embeddings = await self.embed_documents([text])
        return embeddings[0] if embeddings else []
    
    async def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings for multiple documents."""
        if not self.api_key:
            raise ValueError(
                "Embedding API key not configured. "
                "Set EMBED_API_KEY environment variable or provide embedding_config parameter."
            )
        
        client = self._get_client()
        
        response = await client.post(
            f"{self.base_url}/embeddings",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            },
            json={
                "model": self.model,
                "input": texts,
                "encoding_format": "float"
            }
        )
        
        if response.status_code != 200:
            raise RuntimeError(f"Embedding API failed: {response.text}")
        
        data = response.json()
        embeddings = []
        for item in sorted(data.get("data", []), key=lambda x: x.get("index", 0)):
            embedding = item.get("embedding", [])
            embeddings.append(embedding)
        
        if embeddings:
            self.dims = len(embeddings[0])
        
        return embeddings


async def create_embedding_provider(
    model: Optional[str] = None,
    embedding_config=None,
) -> EmbeddingProvider:
    """Create embedding provider based on configuration.

    Args:
        model: Model name
        embedding_config: EmbeddingConfig instance with api_key, base_url, model_name

    Returns:
        Embedding provider instance

    Raises:
        ValueError: If embedding_config is missing or has no api_key.
    """
    if embedding_config is None:
        raise ValueError(
            "Embedding provider not configured. "
            "Provide embedding_config parameter."
        )

    api_key = embedding_config.api_key
    base_url = embedding_config.base_url
    model_name = embedding_config.model_name

    if not api_key:
        raise ValueError(
            "Embedding API key not configured. "
            "Set EMBEDDING_API_KEY environment variable or provide embedding_config parameter."
        )

    return OpenAICompatibleEmbeddingProvider(
        api_key=api_key,
        model=model_name,
        base_url=base_url
    )


def resolve_embedding_config_from_env(
    model_name: Optional[str] = None,
    fallback_base_url: Optional[str] = None,
    fallback_api_key: Optional[str] = None,
) -> Optional[EmbeddingConfig]:
    """
        Build EmbeddingConfig from environment variables.
        Reads EMBEDDING_MODEL_NAME, EMBEDDING_BASE_URL and EMBEDDING_API_KEY.
    """

    model_name = os.getenv("EMBEDDING_MODEL_NAME", model_name)
    base_url = os.getenv("EMBEDDING_BASE_URL", fallback_base_url)
    api_key = os.getenv("EMBEDDING_API_KEY", fallback_api_key)
    if model_name and base_url and api_key:
        return EmbeddingConfig(model_name=model_name, base_url=base_url, api_key=api_key)
    return None