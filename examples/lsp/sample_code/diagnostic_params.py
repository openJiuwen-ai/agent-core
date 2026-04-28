# -*- coding: utf-8 -*-
"""Shared constants and helpers for LSP diagnostic demos."""

from __future__ import annotations

import asyncio
import sys

from openjiuwen.harness.lsp import get_pending_lsp_diagnostics

# ============================================================
# Demo filenames (created under SAMPLE_CODE/, deleted after demo)
# ============================================================

# Used by diagnostic_lsp_demo.py (direct LSP manager demo)
BUGGY_FILENAME = "_lsp_diag_demo.py"

# Used by deep_agent_lsp_demo.py (Demo 9 — agent + after_tool_call)
DEMO9_FILENAME = "_lsp_demo9_diag.py"

# ============================================================
# Sample code templates (format with filename=...)
# ============================================================

# Demo 9: single file with two type errors
DEMO9_CODE_WITH_ERRORS = """\
# {filename} — contains intentional type errors

x: int = "not_an_integer"  # Error: str not assignable to int


def add(a: int, b: int) -> int:
    return a + b


result: str = add(1, 2)    # Error: int not assignable to str
"""

# diagnostic_lsp_demo v1: variable/return-type mismatches
CODE_V1 = """\
# {filename} — v1: intentional type-assignment errors
x: int = "not_an_integer"           # Error: str not assignable to int
y: str = 999                        # Error: int not assignable to str


def add_numbers(a: int, b: int) -> int:
    return a + b


result: str = add_numbers(1, 2)     # Error: int not assignable to str
"""

# diagnostic_lsp_demo v2: different errors in function body (cross-round dedup test)
CODE_V2 = """\
# {filename} — v2: different errors inside function body
from typing import List


def sum_items(items: List[int]) -> int:
    total: str = 0              # Error: int not assignable to str
    for item in items:
        total = total + item    # Error: unsupported operand types str + int
    return total                # Error: str not assignable to int


value: int = sum_items("oops")  # Error: str not assignable to List[int]
"""

# diagnostic_lsp_demo v3: all errors fixed
CODE_CLEAN = """\
# {filename} — v3: all errors fixed
from typing import List


def sum_items(items: List[int]) -> int:
    total: int = 0
    for item in items:
        total = total + item
    return total


value: int = sum_items([1, 2, 3])
"""

# ============================================================
# Print helpers
# ============================================================

def safe_print(*args, **kwargs) -> None:
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    safe = tuple(str(a).encode(encoding, errors="replace").decode(encoding) for a in args)
    kwargs.setdefault("flush", True)
    print(*safe, **kwargs)


Psep = lambda: print("=" * 70, flush=True)
Pline = lambda: print("-" * 70, flush=True)


def show_code(code: str) -> None:
    for i, line in enumerate(code.rstrip().splitlines(), 1):
        safe_print(f"  {i:3d} | {line}")


# ============================================================
# Diagnostic helpers
# ============================================================

async def wait_for_diagnostics(
    timeout: float = 30.0,
    poll_interval: float = 0.5,
    initial_delay: float = 3.0,
) -> list:
    """轮询 LspDiagnosticRegistry，直到有结果或超时。

    initial_delay: 首次轮询前的等待秒数。用于 after_tool_call 场景（fire-and-forget +
    pyright 冷启动需要 2-5 秒）。直接调用 open_file/change_file 时传 0.0。
    """
    if initial_delay > 0:
        print(".", end="", flush=True)
        await asyncio.sleep(initial_delay)

    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while True:
        result = get_pending_lsp_diagnostics()
        if result:
            print(flush=True)
            return result
        remaining = deadline - loop.time()
        if remaining <= 0:
            print(flush=True)
            return []
        print(".", end="", flush=True)
        await asyncio.sleep(min(poll_interval, remaining))


def print_diagnostics(label: str, diagnostics: list) -> None:
    """格式化打印诊断结果。"""
    _SEV = {1: "Error", 2: "Warning", 3: "Info", 4: "Hint"}
    if not diagnostics:
        safe_print(f"[{label}] 无新诊断 — 文件已干净，或诊断已在上一轮送达")
        return
    total = sum(len(f.diagnostics) for f in diagnostics)
    safe_print(f"[{label}] 收到 {total} 条诊断，涉及 {len(diagnostics)} 个文件：")
    for f in diagnostics:
        fname = f.uri.split("/")[-1]
        safe_print(f"  文件: {fname}  [LSP 服务器: {f.server_name}]")
        for d in f.diagnostics:
            sev = _SEV.get(d.severity, f"S{d.severity}")
            line = d.range.get("start", {}).get("line", 0) + 1
            char = d.range.get("start", {}).get("character", 0) + 1
            code = f" ({d.code})" if d.code else ""
            safe_print(f"    [{sev}] L{line}:{char}{code}  {d.message}")
