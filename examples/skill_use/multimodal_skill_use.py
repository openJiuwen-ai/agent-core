#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Run DeepAgent with workspace skills and multimodal hints (hint mode).

Uses ``create_deep_agent`` and seeds skills from ``examples/mobile_gui/skills/``.
Set LLM credentials via environment variables (same names as ``skill_use.py``).

Run from repository root::

    uv run python examples/skill_use/run_deep_agent_with_multimodal_skills.py
"""
import logging
import os
import asyncio
import re
import shutil
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm import init_model
from openjiuwen.core.runner import Runner
from openjiuwen.harness.factory import create_deep_agent
from openjiuwen.harness.rails import SkillUseRail

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MOBILE_GUI_DIR = _REPO_ROOT / "examples" / "mobile_gui"
_SKILL_SOURCES = (
    ("github-com", _MOBILE_GUI_DIR / "skills" / "github-com"),
    ("scheduling", _MOBILE_GUI_DIR / "skills" / "scheduling"),
)
_DEFAULT_QUERY = (
    "Use the github-com skill. Read SKILL.md with skill_tool, then summarize what "
    "the reference images show about the mobile GitHub landing page layout. If images "
    "are only linked in markdown, call read_file on paths under skills/github-com/images/."
)
_SYSTEM_PROMPT = (
    "You are a helpful DeepAgent with filesystem and skill tools.\n"
    "When a task mentions a skill by name, call skill_tool to load its SKILL.md first.\n"
    "Skills live under the workspace skills/ directory."
)

_TRUNCATE_PLACEHOLDER = "[truncated]"
_DATA_URL_BASE64 = re.compile(
    r"(data:[^;]+;base64,)(?:[A-Za-z0-9+/=]{80,})"
)
_BYTES_MODE_CONTENT = re.compile(
    r'"content"\s*:\s*"(?:b\')?(?:[^"\\]|\\.)*\'?"\s*,\s*"mode"\s*:\s*"bytes"'
)


def _truncate_base64_in_text(text: str) -> str:
    """Replace data URLs and bytes-mode file content; leave other log text intact."""
    if not isinstance(text, str) or not text:
        return text
    text = _DATA_URL_BASE64.sub(rf"\1{_TRUNCATE_PLACEHOLDER}", text)
    if '"mode": "bytes"' in text or '"mode":"bytes"' in text:
        text = _BYTES_MODE_CONTENT.sub(
            f'"content": "{_TRUNCATE_PLACEHOLDER}", "mode": "bytes"',
            text,
        )
    return text


class _TruncatingStream:
    """Truncate huge binary/base64 blobs in stdout/stderr."""

    def __init__(self, original: Any) -> None:
        self._original = original

    def write(self, text: str) -> None:
        if isinstance(text, str):
            text = _truncate_base64_in_text(text)
        self._original.write(text)

    def flush(self) -> None:
        flush = getattr(self._original, "flush", None)
        if callable(flush):
            flush()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._original, name)


class _TruncatingLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return _truncate_base64_in_text(super().format(record))


class _TruncatingLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.msg
        if isinstance(msg, str) and msg:
            record.msg = _truncate_base64_in_text(msg)
        return True


_TRUNCATING_FILTER = _TruncatingLogFilter()


def _stream_original(stream: Any) -> Any:
    return getattr(stream, "_original", stream)


def _is_console_stream_handler(handler: logging.Handler) -> bool:
    if type(handler) is not logging.StreamHandler:
        return False
    stream = getattr(handler, "stream", None)
    if stream is None:
        return False
    original = _stream_original(stream)
    return original in (sys.__stdout__, sys.__stderr__)


def _attach_wrapped_stream(handler: logging.StreamHandler) -> None:
    original = _stream_original(getattr(handler, "stream", None))
    if original is sys.__stderr__:
        handler.stream = sys.stderr
    else:
        handler.stream = sys.stdout


def _patch_logger_handlers(log: logging.Logger) -> None:
    formatter = _TruncatingLogFormatter()
    for handler in log.handlers:
        if not _is_console_stream_handler(handler):
            continue
        _attach_wrapped_stream(handler)
        handler.setFormatter(formatter)
        if not any(isinstance(f, _TruncatingLogFilter) for f in handler.filters):
            handler.addFilter(_TRUNCATING_FILTER)


def _patch_logging_stream_handlers() -> None:
    _patch_logger_handlers(logging.getLogger())
    manager = logging.root.manager
    for log in manager.loggerDict.values():
        if isinstance(log, logging.Logger):
            _patch_logger_handlers(log)


def _install_truncating_streams() -> None:
    if not isinstance(sys.stdout, _TruncatingStream):
        sys.stdout = _TruncatingStream(sys.stdout)
    if not isinstance(sys.stderr, _TruncatingStream):
        sys.stderr = _TruncatingStream(sys.stderr)
    _patch_logging_stream_handlers()


def _seed_workspace_skills(workspace_root: str) -> None:
    skills_root = Path(workspace_root) / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)
    for dirname, src in _SKILL_SOURCES:
        if not src.is_dir():
            raise FileNotFoundError(f"Missing skill directory: {src}")
        shutil.copytree(src, skills_root / dirname, dirs_exist_ok=True)


def _env(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name)
        if value is not None and value.strip():
            return value.strip()
    return default


def _load_env_files() -> None:
    # Repo-root .env first, then optional example-local overrides (later wins).
    load_dotenv(_REPO_ROOT / ".env")
    load_dotenv(Path(__file__).resolve().parent / ".env")


async def main() -> None:
    _load_env_files()
    _install_truncating_streams()

    api_base = _env("API_BASE", "LLM_API_BASE")
    api_key = _env("API_KEY", "LLM_API_KEY")
    model_name = _env("MODEL_NAME", "LLM_MODEL_NAME")
    model_provider = _env("MODEL_PROVIDER", "LLM_PROVIDER", "PROVIDER", default="OpenAI")
    verify_ssl = _env("LLM_SSL_VERIFY", "SSL_VERIFY", default="false")
    if not api_key:
        raise SystemExit(
            "Missing API key. Set API_KEY (or LLM_API_KEY) in "
            f"{_REPO_ROOT / '.env'} or examples/skill_use/.env"
        )
    max_iterations = int(os.getenv("MAX_ITERATIONS", "25"))
    multimodal_mode = os.getenv("MULTIMODAL_SKILL_MODE", "hint").lower()
    if multimodal_mode not in {"hint", "attach", "branch"}:
        multimodal_mode = "hint"
    query = os.getenv("DEEP_AGENT_TASK", _DEFAULT_QUERY)

    model = init_model(
        provider=model_provider,
        model_name=model_name,
        api_key=api_key,
        api_base=api_base,
        verify_ssl=str(verify_ssl).lower() in ("1", "true", "yes"),
    )

    conversation_id = f"deep_multimodal_skills_{uuid.uuid4().hex[:12]}"
    with tempfile.TemporaryDirectory(prefix="deep-agent-skills-ws-") as workspace_root:
        _seed_workspace_skills(workspace_root)
        skills_dir = str(Path(workspace_root) / "skills")
        agent = create_deep_agent(
            model=model,
            system_prompt=_SYSTEM_PROMPT,
            workspace=workspace_root,
            language="en",
            enable_skill_discovery=True,
            max_iterations=max_iterations,
            restrict_to_work_dir=True,
            rails=[
                SkillUseRail(
                    skills_dir=skills_dir,
                    skill_mode="all",
                    multimodal_skill_mode=multimodal_mode,
                )
            ],
        )
        await agent.ensure_initialized()
        _install_truncating_streams()
        result = await Runner.run_agent(
            agent,
            {"query": query, "conversation_id": conversation_id},
        )

    output = result.get("output", result) if isinstance(result, dict) else result
    if isinstance(output, str):
        output = _truncate_base64_in_text(output)
    logger.info(output)


if __name__ == "__main__":
    asyncio.run(main())
