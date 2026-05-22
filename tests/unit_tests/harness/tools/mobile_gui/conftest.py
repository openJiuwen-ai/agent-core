# coding: utf-8
"""Shared fixtures for mobile_gui harness unit tests."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from openjiuwen.core.foundation.llm import UserMessage
from openjiuwen.core.sys_operation.cwd import init_cwd


@pytest.fixture
def skill_workspace(tmp_path: Path) -> tuple[Path, Path]:
    """Workspace root with ``skills/demo`` and ``init_cwd`` applied."""
    workspace = tmp_path / "wk"
    skill_dir = workspace / "skills" / "demo"
    skill_dir.mkdir(parents=True)
    init_cwd(str(workspace))
    return workspace, skill_dir


def image_user_message(
    *,
    resolved_path: str,
    image_b64: str = "QQ==",
    name: str | None = None,
) -> UserMessage:
    """Generic ``read_file`` image user turn before decoration."""
    return UserMessage(
        name=name,
        content=[
            {"type": "text", "text": f"Image loaded from read_file: {resolved_path}"},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
        ],
    )


def fake_message_context(messages: list[Any]) -> SimpleNamespace:
    """Minimal context object for rails that call ``get_messages`` / ``set_messages``."""

    class _MessageContext:
        def __init__(self, items: list[Any]) -> None:
            self._items = list(items)

        def get_messages(self) -> list[Any]:
            return self._items

        def set_messages(self, items: list[Any]) -> None:
            self._items = list(items)

    return SimpleNamespace(context=_MessageContext(messages))
