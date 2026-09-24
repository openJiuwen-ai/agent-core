from __future__ import annotations

import os
import time

from video_memory.config import EmbeddingConfig
from video_memory.embedding.cache import EmbeddingCache


class APIEmbedder:
    def __init__(self, config: EmbeddingConfig) -> None:
        from openai import OpenAI

        api_key = os.getenv(config.api_key_env)
        if not api_key:
            raise RuntimeError(f"Missing embedding API key. Set {config.api_key_env} first.")

        self.config = config
        self.client = OpenAI(base_url=config.base_url, api_key=api_key)
        self.cache = EmbeddingCache(config.cache_db)

    def close(self) -> None:
        self.cache.close()

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float] | None] = [None] * len(texts)
        missing: list[tuple[int, str]] = []

        for index, text in enumerate(texts):
            cached = self.cache.get(self.config.model, text)
            if cached is None:
                missing.append((index, text))
            else:
                vectors[index] = cached

        for start in range(0, len(missing), self.config.batch_size):
            batch = missing[start : start + self.config.batch_size]
            response = self._embed_batch([text for _, text in batch])
            for (index, text), vector in zip(batch, response, strict=True):
                self.cache.set(self.config.model, text, vector)
                vectors[index] = vector

        return [vector or [] for vector in vectors]

    def embed_text(self, text: str) -> list[float]:
        return self.embed_texts([text])[0]

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = self.client.embeddings.create(model=self.config.model, input=texts)
                return [list(item.embedding) for item in response.data]
            except Exception as exc:  # pragma: no cover - API errors are environment-specific.
                last_error = exc
                if attempt < 2:
                    time.sleep(1.5 * (attempt + 1))
        raise RuntimeError("Embedding API call failed") from last_error


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = sum(a * a for a in left) ** 0.5
    right_norm = sum(b * b for b in right) ** 0.5
    if not left_norm or not right_norm:
        return 0.0
    return dot / (left_norm * right_norm)

