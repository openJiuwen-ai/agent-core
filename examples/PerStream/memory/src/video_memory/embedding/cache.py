from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path


class EmbeddingCache:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, timeout=30)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS embeddings (
                cache_key TEXT PRIMARY KEY,
                model TEXT NOT NULL,
                text_hash TEXT NOT NULL,
                vector TEXT NOT NULL
            )
            """
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def get(self, model: str, text: str) -> list[float] | None:
        key = _cache_key(model, text)
        row = self.conn.execute("SELECT vector FROM embeddings WHERE cache_key = ?", (key,)).fetchone()
        return list(json.loads(row[0])) if row else None

    def set(self, model: str, text: str, vector: list[float]) -> None:
        text_hash = _text_hash(text)
        self.conn.execute(
            """
            INSERT OR REPLACE INTO embeddings (cache_key, model, text_hash, vector)
            VALUES (?, ?, ?, ?)
            """,
            (_cache_key(model, text), model, text_hash, json.dumps(vector)),
        )
        self.conn.commit()


def _cache_key(model: str, text: str) -> str:
    return f"{model}:{_text_hash(text)}"


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

