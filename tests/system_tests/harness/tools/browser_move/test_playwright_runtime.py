#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""System test for the browser_move runtime flow."""

from __future__ import annotations

import asyncio
import os
from typing import Any, Dict

import pytest

from openjiuwen.harness.tools.browser_move.playwright_runtime.config import (
    MISSING_API_KEY_MESSAGE,
    build_runtime_settings,
    load_repo_dotenv,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.runtime import BrowserAgentRuntime


def _system_tests_enabled() -> bool:
    return (os.getenv("RUN_BROWSER_MOVE_SYSTEM_TESTS") or "").strip().lower() in {"1", "true", "yes", "on"}


pytestmark = pytest.mark.skipif(
    not _system_tests_enabled(),
    reason="Set RUN_BROWSER_MOVE_SYSTEM_TESTS=1 to run browser_move system tests.",
)


async def _run_live_check(query: str, session_id: str, cancel_after_s: float = 0.0) -> Dict[str, Any]:
    settings = build_runtime_settings()
    if not settings.api_key:
        raise RuntimeError(f"Missing API key for runtime example. {MISSING_API_KEY_MESSAGE}")

    runtime = BrowserAgentRuntime(
        provider=settings.provider,
        api_key=settings.api_key,
        api_base=settings.api_base,
        model_name=settings.model_name,
        mcp_cfg=settings.mcp_cfg,
        guardrails=settings.guardrails,
    )

    try:
        await runtime.ensure_started()
        if cancel_after_s > 0:
            request_task = asyncio.create_task(
                runtime.run_browser_task(task=query, session_id=session_id)
            )
            await asyncio.sleep(cancel_after_s)
            await runtime.cancel_run(session_id=session_id)
            result = await request_task
        else:
            result = await runtime.run_browser_task(task=query, session_id=session_id)
        return {
            "ok": bool(result.get("ok", False)),
            "mode": "live",
            "session_id": result.get("session_id"),
            "request_id": result.get("request_id"),
            "final": result.get("final"),
            "error": result.get("error"),
        }
    finally:
        await runtime.shutdown()


def test_playwright_runtime_end_to_end() -> None:
    load_repo_dotenv(override=True)
    result = asyncio.run(
        _run_live_check(
            query="Go to https://example.com and return the page title.",
            session_id="system-test-browser-runtime",
        )
    )

    assert result["ok"] is True
    assert result["mode"] == "live"
    assert result["session_id"] == "system-test-browser-runtime"
    assert result["error"] is None
    assert str(result["final"]).strip()
