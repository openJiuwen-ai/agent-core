# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Shared helpers for mobile GUI example scripts."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

MOBILE_GUI_DIR = Path(__file__).resolve().parent
REPO_ROOT = MOBILE_GUI_DIR.parent.parent
ENV_FILE = MOBILE_GUI_DIR / ".env"
ENV_EXAMPLE_FILE = MOBILE_GUI_DIR / ".env.example"

_BASE64_PATTERN = re.compile(
    r"(data:image/[^;]+;base64,)([A-Za-z0-9+/=]{10})[A-Za-z0-9+/=]+"
)


class _TruncatingStream:
    """Truncate huge base64 blobs in stdout/stderr."""

    def __init__(self, original: Any) -> None:
        self._original = original

    def write(self, text: str) -> None:
        if isinstance(text, str) and "base64," in text:
            text = _BASE64_PATTERN.sub(r"\1\2...(truncated)", text)
        self._original.write(text)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._original, name)


def configure_import_paths() -> None:
    """Ensure repo root and this examples folder are importable."""
    for path in (REPO_ROOT, MOBILE_GUI_DIR):
        entry = str(path)
        if entry not in sys.path:
            sys.path.insert(0, entry)


def install_truncating_streams() -> None:
    sys.stdout = _TruncatingStream(sys.stdout)
    sys.stderr = _TruncatingStream(sys.stderr)


def load_example_env() -> None:
    """Load env files from least to most specific (later files win)."""
    load_dotenv(REPO_ROOT / ".env")
    load_dotenv(MOBILE_GUI_DIR.parent / ".env")  # legacy examples/.env
    load_dotenv(ENV_FILE)


def ensure_mobile_gui_deps() -> None:
    """Fail fast with install instructions if optional mobile GUI deps are missing."""
    missing: list[str] = []
    try:
        import uiautomator2  # noqa: F401
    except ModuleNotFoundError:
        missing.append("uiautomator2")
    try:
        import PIL  # noqa: F401
    except ModuleNotFoundError:
        missing.append("pillow")
    if not missing:
        return
    print(
        "Mobile GUI examples need extra dependencies (not installed with base openjiuwen).\n"
        "From the repository root, run one of:\n"
        "  uv pip install 'openjiuwen[mobile-gui]' .\n"
        "  pip install 'openjiuwen[mobile-gui]' .\n"
        "Or install the packages directly:\n"
        "  pip install uiautomator2 pillow\n"
        f"Missing: {', '.join(missing)}",
        file=sys.stderr,
    )
    sys.exit(1)


def bootstrap_runtime(*, configure_paths: bool = False) -> None:
    """Common startup for example entrypoints: paths, logs, env, dependency check."""
    if configure_paths:
        configure_import_paths()
    install_truncating_streams()
    load_example_env()
    ensure_mobile_gui_deps()


def env_str(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name)
        if value is not None and value.strip():
            return value.strip()
    return default


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return int(raw)


def require_api_key() -> str:
    key = env_str("API_KEY", "LLM_API_KEY")
    if not key:
        print(
            f"Missing API key. Set API_KEY or LLM_API_KEY in {ENV_FILE} "
            f"(copy from {ENV_EXAMPLE_FILE.name}) or the environment.",
            file=sys.stderr,
        )
        sys.exit(1)
    return key


def build_chat_model():
    """Build :class:`openjiuwen.core.foundation.llm.model.Model` from env."""
    from openjiuwen.core.foundation.llm import init_model

    return init_model(
        provider=env_str("MODEL_PROVIDER", "LLM_PROVIDER", default="OpenAI"),
        model_name=env_str("MODEL_NAME", "LLM_MODEL_NAME", default="gpt-4.1-mini"),
        api_key=require_api_key(),
        api_base=env_str(
            "API_BASE",
            "LLM_API_BASE",
            default="https://api.openai.com/v1",
        ),
        verify_ssl=env_str("LLM_SSL_VERIFY", default="false").lower()
        in ("1", "true", "yes"),
    )


def default_task() -> str:
    return env_str("MOBILE_TASK") or (
        "Find the number of stars and forks for https://github.com/openJiuwen-ai/agent-core."
    )


def device_serial() -> str:
    return env_str("DEVICE_SERIAL", default="emulator-5554")


def print_run_banner(*, serial: str, task: str, **fields: Any) -> None:
    print(f"device_serial={serial!r}")
    print(f"task={task!r}")
    for key, value in fields.items():
        print(f"{key}={value!r}")


def print_agent_result(result: Any) -> None:
    if isinstance(result, dict):
        print("result_type:", result.get("result_type"))
        print("output:\n", result.get("output", result))
    else:
        print(result)


async def run_agent_with_runner(agent: Any, *, query: str, conversation_id: str) -> Any:
    """Start Runner, run one agent turn, and always stop Runner."""
    from openjiuwen.core.runner import Runner

    await Runner.start()
    try:
        await agent.ensure_initialized()
        return await Runner.run_agent(
            agent,
            {"query": query, "conversation_id": conversation_id},
        )
    finally:
        await Runner.stop()
