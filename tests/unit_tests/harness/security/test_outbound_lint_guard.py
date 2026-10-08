# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Harness tools must reach the network through the host exit, not raw HTTP clients."""

from __future__ import annotations

import re
from pathlib import Path

import openjiuwen

_TOOLS_DIR = Path(openjiuwen.__file__).resolve().parent / "harness" / "tools"
_RAW_HTTP = re.compile(
    r"\b(requests|httpx)\.(get|post|put|patch|delete|head|request|Session|Client|AsyncClient)\b"
    r"|aiohttp\.ClientSession\("
    r"|\burlopen\("
)
_RAW_HTTP_ALLOWLIST = frozenset({
    # The web tools' transport; every hop goes through outbound.async_client.
    "web/_http.py",
    # Pre-existing modules outside net_guard's tool scope.
    "browser_move/drivers/managed_browser.py",
    "browser_move/playwright_runtime/service.py",
    "multimodal/audio.py",
})


def test_harness_tools_do_not_use_raw_http_clients():
    offenders = []
    for path in _TOOLS_DIR.rglob("*.py"):
        rel = path.relative_to(_TOOLS_DIR).as_posix()
        if rel in _RAW_HTTP_ALLOWLIST:
            continue
        if _RAW_HTTP.search(path.read_text(encoding="utf-8")):
            offenders.append(rel)
    assert offenders == [], (
        "use openjiuwen.harness.security.outbound (sync_client / async_client) "
        f"instead of raw HTTP clients: {offenders}"
    )


def test_web_transport_routes_through_host_exit():
    source = (_TOOLS_DIR / "web" / "_http.py").read_text(encoding="utf-8")
    assert "outbound_http.request(" in source
    assert "session.request(" not in source
