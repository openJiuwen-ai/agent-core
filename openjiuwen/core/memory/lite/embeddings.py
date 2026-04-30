# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Embedding providers for memory system."""

import os
from abc import ABC, abstractmethod
from typing import List, Optional
from openjiuwen.core.common.logging import memory_logger as logger
from openjiuwen.core.foundation.store.base_embedding import EmbeddingConfig


class EmbeddingProvider(ABC):
    """Base class for embedding providers."""
    
    id: str = "base"
    model: str = "base"
    dims: int = 0
    
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
        self.base_url = base_url
        
        if self.base_url and self.base_url.endswith("/embeddings"):
            self.base_url = self.base_url.rsplit("/embeddings", 1)[0]
        
        self.dims = 1024
        self._client = None
    
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


class MockEmbeddingProvider(EmbeddingProvider):
    """Mock embedding provider for testing."""
    
    id: str = "mock"
    model: str = "mock"
    dims: int = 128
    
    async def embed_query(self, text: str) -> List[float]:
        """Generate mock embedding."""
        import hashlib
        import random
        h = hashlib.md5(text.encode()).hexdigest()
        random.seed(h)
        return [random.uniform(-1, 1) for _ in range(128)]
    
    async def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """Generate mock embeddings."""
        return [await self.embed_query(t) for t in texts]


async def create_embedding_provider(
    provider: str = "auto",
    model: Optional[str] = None,
    fallback: str = "mock",
    embedding_config=None,
) -> EmbeddingProvider:
    """Create embedding provider based on configuration.

    Priority order for configuration:
    1. embedding_config parameter (EmbeddingConfig instance)
    2. Environment variables (EMBED_API_KEY, EMBED_MODEL, EMBED_BASE_URL)

    Args:
        provider: Provider name (openai_compatible, auto, mock)
        model: Model name
        fallback: Fallback provider if auto-detection fails
        embedding_config: Optional EmbeddingConfig instance with api_key, base_url, model_name
    
    Returns:
        Embedding provider instance
    """
    if provider == "mock":
        return MockEmbeddingProvider()
    
    if embedding_config is not None:
        api_key = embedding_config.api_key
        base_url = embedding_config.base_url
        model_name = embedding_config.model_name
    else:
        logger.error("Embedding provider not configured.")
        return
    
    if api_key:
        return OpenAICompatibleEmbeddingProvider(
            api_key=api_key,
            model=model_name,
            base_url=base_url
        )
    
    if fallback == "mock":
        logger.warning("Embedding API key not found, using mock provider")
        return MockEmbeddingProvider()
    
    raise ValueError(
        "Embedding API key not configured. "
        "Set EMBED_API_KEY environment variable or provide embedding_config parameter."
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