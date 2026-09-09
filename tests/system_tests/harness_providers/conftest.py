# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Shared fixtures for the protocol harness end-to-end suites.

These suites drive the real ``claude`` / ``codex`` / DSH runtimes through
``openjiuwen.harness_protocol`` and are skipped automatically when the CLI
or its SDK is not installed locally.  Each CLI uses its own default model
configuration; nothing here installs or configures a model.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
from pathlib import Path

import pytest


def _module_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def claude_available() -> bool:
    return shutil.which("claude") is not None and _module_available("claude_agent_sdk")


def codex_available() -> bool:
    return shutil.which("codex") is not None and _module_available("openai_codex")


def dsh_available() -> bool:
    return _module_available("deepseek_harness") and _module_available("deepseek_harness_runtime")


def native_model_available() -> bool:
    return bool(os.environ.get("API_BASE") and os.environ.get("API_KEY") and os.environ.get("MODEL_NAME"))


def dsh_home() -> Path:
    """Return the DSH home the local CLI uses (``DSH_HOME`` or ``~/.dsh``)."""
    return Path(os.environ.get("DSH_HOME") or "~/.dsh").expanduser()


def dsh_default_model() -> dict[str, str]:
    """Read ``agent-default-model`` from the local DSH settings.

    The SDK requires an explicit provider/model pair; the CLI keeps its
    default under ``settings.yaml`` so the e2e suite reuses that choice.
    """
    settings = dsh_home() / "settings.yaml"
    if not settings.is_file():
        return {}
    import yaml

    payload = yaml.safe_load(settings.read_text(encoding="utf-8")) or {}
    default = payload.get("agent-default-model") or {}
    result: dict[str, str] = {}
    for key in ("provider", "model"):
        value = default.get(key)
        if isinstance(value, str) and value:
            result[key] = value
    return result


requires_claude = pytest.mark.skipif(not claude_available(), reason="claude CLI or claude-agent-sdk not available")
requires_codex = pytest.mark.skipif(not codex_available(), reason="codex CLI or openai-codex SDK not available")
requires_dsh = pytest.mark.skipif(not dsh_available(), reason="deepseek-harness SDK/runtime not available")
requires_native_model = pytest.mark.skipif(
    not native_model_available(), reason="API_BASE / API_KEY / MODEL_NAME required for the DeepAgent harness"
)


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    """An isolated working directory the CLI may read and write."""
    target = tmp_path / "work"
    target.mkdir()
    return target
