# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Model config loading helpers for member optimization agents."""

from __future__ import annotations

import json
import os
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

_ENV_VAR_RE = re.compile(r"\$\{(\w+)}")


def load_model_config_ref(model_config_ref: str) -> dict[str, Any]:
    """Load a YAML/JSON model config and expand ``${VAR}`` placeholders."""
    path = Path(model_config_ref).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"model_config_ref not found: {model_config_ref}")
    with open(path, encoding="utf-8") as file:
        if path.suffix.lower() in {".yaml", ".yml"}:
            data = yaml.safe_load(file) or {}
        else:
            data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError(f"model_config_ref must decode to a mapping: {path}")
    return _expand_env_vars(data)


def build_deep_agent_from_model_config(config_data: dict[str, Any]) -> Any:
    """Build a DeepAgent from a loaded member-optimizer model config."""
    from openjiuwen.agent_teams.schema.deep_agent_spec import TeamModelConfig
    from openjiuwen.harness.deep_agent import DeepAgent
    from openjiuwen.harness.schema.config import DeepAgentConfig
    from openjiuwen.harness.workspace.workspace import Workspace

    model = None
    if "model" in config_data:
        model = TeamModelConfig.model_validate(without_inner_sdk_retries(config_data["model"])).build()

    workspace = Workspace(root_path="./", language="en")
    if "workspace" in config_data and isinstance(config_data["workspace"], dict):
        workspace_config = config_data["workspace"]
        workspace = Workspace(
            root_path=workspace_config.get("root_path", "./"),
            language=workspace_config.get("language", "en"),
        )

    return DeepAgent(
        config=DeepAgentConfig(
            model=model,
            workspace=workspace,
        ),
    )


def without_inner_sdk_retries(config_data: dict[str, Any]) -> dict[str, Any]:
    """Return a model config copy with SDK-level retries disabled.

    Auto coordinating harness owns its retry budget through
    ``run_model_call_with_retries``. Letting the OpenAI-compatible SDK retry
    internally makes a single visible attempt block for many timeout windows, so
    ACH-loaded model configs always enter the lower client with
    ``max_retries=0``.
    """
    data = deepcopy(config_data)
    if not isinstance(data, dict):
        return data

    _disable_client_retries(data)
    model_data = data.get("model")
    if isinstance(model_data, dict):
        _disable_client_retries(model_data)
    return data


def _disable_client_retries(model_data: dict[str, Any]) -> None:
    client_data = model_data.get("model_client_config")
    if isinstance(client_data, dict):
        client_data["max_retries"] = 0


def _expand_env_vars(value: Any) -> Any:
    if isinstance(value, str):
        return _ENV_VAR_RE.sub(
            lambda match: os.environ.get(match.group(1), match.group(0)),
            value,
        )
    if isinstance(value, dict):
        return {key: _expand_env_vars(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand_env_vars(item) for item in value]
    return value
