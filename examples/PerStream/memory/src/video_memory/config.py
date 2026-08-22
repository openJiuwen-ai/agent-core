from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class PathConfig:
    frames_dir: Path
    qa_path: Path
    output_dir: Path
    memory_db: Path


@dataclass(frozen=True)
class WindowConfig:
    window_size: int = 8
    stride: int = 8


@dataclass(frozen=True)
class MemoryConfig:
    generation_mode: str = "vision"
    min_ocr_chars: int = 12


@dataclass(frozen=True)
class EntityConfig:
    spacy_model: str = "en_core_web_sm"
    aliases: dict[str, str] = field(default_factory=dict)
    allowed_labels: list[str] = field(default_factory=list)
    conditional_labels: list[str] = field(default_factory=list)
    blocked_labels: list[str] = field(default_factory=list)
    blocklist: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class LLMConfig:
    provider: str = "openrouter"
    model: str = "gpt-4.1"
    base_url: str | None = None
    api_key_env: str | None = None
    http_referer: str | None = None
    app_title: str | None = "Video Memory QA"
    image_detail: str = "low"
    max_retries: int = 2


@dataclass(frozen=True)
class EmbeddingConfig:
    provider: str = "openrouter"
    model: str = "openai/text-embedding-3-small"
    base_url: str | None = "https://openrouter.ai/api/v1"
    api_key_env: str = "OPENROUTER_API_KEY"
    cache_db: Path = Path("outputs/cache/embeddings.sqlite")
    batch_size: int = 64


@dataclass(frozen=True)
class RetrievalConfig:
    node_score_threshold: float = 0.55
    entity_score_threshold: float = 0.5
    propagation_score_threshold: float = 0.4
    max_hops: int = 2
    decay: float = 0.8
    min_k: int = 3
    max_k: int = 20
    final_node_threshold: float = 0.45
    recently_window_size: int = 100


@dataclass(frozen=True)
class TracingConfig:
    enabled: bool = True
    traces_dir: Path = Path("outputs/traces")


@dataclass(frozen=True)
class AppConfig:
    paths: PathConfig
    window: WindowConfig = field(default_factory=WindowConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    entities: EntityConfig = field(default_factory=EntityConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    embedding: EmbeddingConfig = field(default_factory=EmbeddingConfig)
    retrieval: RetrievalConfig = field(default_factory=RetrievalConfig)
    tracing: TracingConfig = field(default_factory=TracingConfig)


def _resolve_path(base_dir: Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else base_dir / path


def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path).resolve()
    base_dir = config_path.parent.parent
    with config_path.open("r", encoding="utf-8") as f:
        raw: dict[str, Any] = yaml.safe_load(f) or {}

    paths_raw = raw.get("paths", {})
    paths = PathConfig(
        frames_dir=_resolve_path(base_dir, paths_raw.get("frames_dir", "decoded_frames_renumbered")),
        qa_path=_resolve_path(base_dir, paths_raw.get("qa_path", "qa.json")),
        output_dir=_resolve_path(base_dir, paths_raw.get("output_dir", "outputs")),
        memory_db=_resolve_path(base_dir, paths_raw.get("memory_db", "outputs/memory/memory.sqlite")),
    )

    window = WindowConfig(**raw.get("window", {}))
    memory = MemoryConfig(**raw.get("memory", {}))
    entities = EntityConfig(**raw.get("entities", {}))
    llm = LLMConfig(**raw.get("llm", {}))
    embedding_raw = raw.get("embedding", {})
    if "cache_db" in embedding_raw:
        embedding_raw = dict(embedding_raw)
        embedding_raw["cache_db"] = _resolve_path(base_dir, embedding_raw["cache_db"])
    embedding = EmbeddingConfig(**embedding_raw)
    retrieval = RetrievalConfig(**raw.get("retrieval", {}))

    tracing_raw = raw.get("tracing", {})
    traces_dir = _resolve_path(base_dir, tracing_raw.get("traces_dir", "outputs/traces"))
    tracing = TracingConfig(
        enabled=bool(tracing_raw.get("enabled", True)),
        traces_dir=traces_dir,
    )

    return AppConfig(
        paths=paths,
        window=window,
        memory=memory,
        entities=entities,
        llm=llm,
        embedding=embedding,
        retrieval=retrieval,
        tracing=tracing,
    )
