"""Characterization tests for YAML config loading.

The path resolution here is easy to get wrong: base_dir is the config file's
*grandparent*, so configs/default.yaml resolves relative paths against the
repository root rather than against configs/.
"""

from pathlib import Path

import pytest
import yaml

from video_memory.config import load_config

ROOT = Path(__file__).resolve().parents[1]


def _write(tmp_path: Path, raw: dict) -> Path:
    config_dir = tmp_path / "configs"
    config_dir.mkdir(parents=True, exist_ok=True)
    path = config_dir / "test.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return path


def test_relative_paths_resolve_against_the_config_grandparent(tmp_path: Path) -> None:
    path = _write(tmp_path, {"paths": {"frames_dir": "frames", "qa_path": "qa.json"}})
    config = load_config(path)

    assert config.paths.frames_dir == tmp_path / "frames"
    assert config.paths.qa_path == tmp_path / "qa.json"


def test_absolute_paths_are_left_alone(tmp_path: Path) -> None:
    absolute = tmp_path / "elsewhere" / "frames"
    path = _write(tmp_path, {"paths": {"frames_dir": str(absolute)}})
    assert load_config(path).paths.frames_dir == absolute


def test_defaults_apply_when_sections_are_missing(tmp_path: Path) -> None:
    config = load_config(_write(tmp_path, {}))

    assert config.paths.frames_dir == tmp_path / "decoded_frames_renumbered"
    assert config.paths.qa_path == tmp_path / "qa.json"
    assert (config.window.window_size, config.window.stride) == (8, 8)
    assert config.memory.generation_mode == "vision"
    assert config.llm.provider == "openrouter"
    assert config.tracing.enabled is True
    assert config.tracing.traces_dir == tmp_path / "outputs" / "traces"


def test_embedding_cache_db_is_resolved_like_the_other_paths(tmp_path: Path) -> None:
    path = _write(tmp_path, {"embedding": {"cache_db": "cache/embeddings.sqlite", "batch_size": 8}})
    config = load_config(path)

    assert config.embedding.cache_db == tmp_path / "cache" / "embeddings.sqlite"
    assert config.embedding.batch_size == 8


def test_an_unknown_key_in_a_section_raises(tmp_path: Path) -> None:
    """The sections are frozen dataclasses, so a typo cannot be ignored."""
    path = _write(tmp_path, {"window": {"window_size": 4, "not_a_field": 1}})
    with pytest.raises(TypeError, match="not_a_field"):
        load_config(path)


def test_the_shipped_default_config_loads() -> None:
    config = load_config(ROOT / "configs" / "default.yaml")

    assert config.paths.frames_dir == ROOT / "decoded_frames_renumbered"
    assert config.paths.qa_path == ROOT / "qa.json"
    assert config.memory.generation_mode == "ocr_llm"
    assert config.llm.provider == "openrouter"
    assert config.entities.spacy_model == "en_core_web_sm"
    assert config.retrieval.max_hops == 2
