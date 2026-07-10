# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""User-level settings and provider-name utilities.

These helpers are shared between the CLI layer and direct SDK usage.
Keeping them in ``core`` lets non-CLI code (programmatic SDK use,
tests, notebooks) read/write ``~/.openjiuwen/settings.json`` and
normalise provider names without importing from ``harness.cli``.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Optional

from openjiuwen.core.common.logging import llm_logger as logger
from openjiuwen.extensions.external_provider.openai_auth.openai_account_auth import OPENAI_ACCOUNT_PROVIDER

#: Default path for the user-level settings file.
SETTINGS_PATH: Path = Path.home() / ".openjiuwen" / "settings.json"


class SettingsJsonError(ValueError):
    """Raised when an existing settings file cannot be safely merged."""


def normalize_provider(provider: str) -> str:
    """Return the canonical provider name for *provider*.

    Strips surrounding whitespace and maps common aliases
    (``openai-account``, ``openai_account``, etc.) to the
    registered ``ProviderType`` string.

    Args:
        provider: Raw provider string from CLI / env / settings.

    Returns:
        Canonical provider name (e.g. ``"OpenAIAccount"``).
    """
    normalized = str(provider or "").strip()
    if normalized.lower().replace("-", "").replace("_", "") == "openaiaccount":
        return OPENAI_ACCOUNT_PROVIDER
    return normalized


def is_openai_account_provider(provider: str) -> bool:
    """Return ``True`` if *provider* resolves to ``OpenAIAccount``."""
    return normalize_provider(provider) == OPENAI_ACCOUNT_PROVIDER


def load_settings_json(path: Optional[Path] = None, *, strict: bool = False) -> dict[str, Any]:
    """Load settings from ``~/.openjiuwen/settings.json``.

    Args:
        path: Override for the settings file path (useful in tests).
        strict: Raise :class:`SettingsJsonError` for unreadable or invalid
            existing files instead of falling back to an empty dict.

    Returns:
        Parsed dict, or ``{}`` on missing / invalid file.
        Never raises by default, except when ``strict=True``.
    """
    p = path or SETTINGS_PATH
    try:
        return _read_settings_json(p)
    except FileNotFoundError:
        return {}
    except SettingsJsonError as exc:
        if strict:
            raise
        logger.warning("Failed to load settings from %s; falling back to empty settings: %s", p, exc)
        return {}


def save_settings_json(
    data: dict[str, Any],
    path: Optional[Path] = None,
) -> Path:
    """Save settings to ``~/.openjiuwen/settings.json``.

    Merges *data* into the existing file (new keys override).

    Args:
        data: Key-value pairs to write.
        path: Override for the settings file path.

    Returns:
        The resolved file path.
    """
    p = path or SETTINGS_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        existing = _read_settings_json(p)
    except FileNotFoundError:
        existing = {}
    existing.update(data)
    _atomic_write_text(p, json.dumps(existing, indent=2, ensure_ascii=False) + "\n")
    return p


def _atomic_write_text(path: Path, text: str) -> None:
    """Atomically replace *path* with UTF-8 *text*."""
    temp_path: Optional[Path] = None
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as temp_file:
        temp_path = Path(temp_file.name)
        temp_file.write(text)
        temp_file.flush()
        os.fsync(temp_file.fileno())

    try:
        os.replace(temp_path, path)
    except OSError:
        temp_path.unlink(missing_ok=True)
        raise


def _read_settings_json(path: Path) -> dict[str, Any]:
    """Read an existing settings file, raising when it cannot be merged safely."""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise SettingsJsonError(str(exc)) from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SettingsJsonError(str(exc)) from exc
    if not isinstance(data, dict):
        raise SettingsJsonError("settings JSON root must be an object")
    return data
