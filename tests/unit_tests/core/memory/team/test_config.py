# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for TeamMemoryConfig and resolve_embedding_config."""

from __future__ import annotations

from unittest.mock import patch

from openjiuwen.core.foundation.store.base_embedding import EmbeddingConfig
from openjiuwen.core.memory.team.config import TeamMemoryConfig, resolve_embedding_config


def test_team_memory_config_excluded_fields_absent_from_model_dump():
    emb = EmbeddingConfig(model_name="m", base_url="http://localhost")
    cfg = TeamMemoryConfig(
        enabled=True,
        embedding_config=emb,
        parent_workspace_path="/parent/ws",
        team_memory_dir="/team/mem",
    )
    flat = cfg.model_dump()
    assert "embedding_config" not in flat
    assert "parent_workspace_path" not in flat
    assert "team_memory_dir" not in flat

    json_flat = cfg.model_dump(mode="json")
    assert "embedding_config" not in json_flat
    assert "parent_workspace_path" not in json_flat
    assert "team_memory_dir" not in json_flat


def test_resolve_embedding_config_prefers_config_over_env():
    mock_env = EmbeddingConfig(model_name="env", base_url="http://env")
    explicit = EmbeddingConfig(model_name="explicit", base_url="http://explicit")
    cfg = TeamMemoryConfig(embedding_config=explicit)

    with patch(
        "openjiuwen.core.memory.team.config.resolve_embedding_config_from_env",
        return_value=mock_env,
    ) as mock_env_fn:
        out = resolve_embedding_config(cfg)
        assert out is explicit
        mock_env_fn.assert_not_called()


def test_resolve_embedding_config_falls_back_to_env_when_no_embedding_in_config():
    mock_env = EmbeddingConfig(model_name="env", base_url="http://env")

    with patch(
        "openjiuwen.core.memory.team.config.resolve_embedding_config_from_env",
        return_value=mock_env,
    ) as mock_env_fn:
        assert resolve_embedding_config(None) is mock_env
        mock_env_fn.assert_called_once()
        mock_env_fn.reset_mock()

        assert resolve_embedding_config(TeamMemoryConfig()) is mock_env
        mock_env_fn.assert_called_once()
