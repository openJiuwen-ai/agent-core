"""Extract a Python traceback from subprocess stderr/stdout."""

from __future__ import annotations

import re

_ERROR_TREE_LIMIT = 4000
_EXCEPTION_BANNER_CHARS = 240
_TRACEBACK_MARK = "Traceback (most recent call last):"
_FILE_FRAME_RE = re.compile(r'File "([^"]+)", line (\d+)')
_EXCEPTION_LINE_RE = re.compile(
    r"^([A-Za-z_][\w.]*(?:Error|Exception|Warning|Exit|Interrupt|Failure)): ?(.*)$"
)
_SDK_LOG_TYPE_RE = re.compile(
    r"\| (?:llm|common|tool|interface|performance|prompt_builder) \|"
)


def _is_sdk_noise_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return True
    if "reasoning.encrypted" in stripped or "gAAAA" in stripped:
        return True
    if "ChatCompletion(" in stripped:
        return True
    if _SDK_LOG_TYPE_RE.search(stripped):
        return True
    if '"event_type"' in stripped and '"module_type"' in stripped:
        return True
    return len(stripped) > 800 and stripped[:1] in "{["


def python_error_tree(text: str, *, limit: int = _ERROR_TREE_LIMIT) -> str:
    """Last Python traceback in ``text``, SDK lines stripped. Empty if none."""
    cleaned = (text or "").strip()
    if not cleaned:
        return ""
    idx = cleaned.rfind(_TRACEBACK_MARK)
    if idx < 0:
        return ""
    kept: list[str] = []
    seen_exception = False
    for line in cleaned[idx:].splitlines():
        stripped = line.strip()
        if seen_exception:
            if not stripped:
                break
            if stripped.startswith("--- ") and stripped.endswith(" ---"):
                break
            if _is_sdk_noise_line(line):
                break
            if line[:1] in " \t" or _EXCEPTION_LINE_RE.match(stripped):
                kept.append(line.rstrip())
                continue
            break
        if _is_sdk_noise_line(line):
            continue
        kept.append(line.rstrip())
        if _EXCEPTION_LINE_RE.match(stripped):
            seen_exception = True
    while kept and not kept[-1].strip():
        kept.pop()
    tree = "\n".join(kept).strip()
    if not tree:
        return ""
    if len(tree) <= limit:
        return tree
    prefix = "...(truncated; see full log on disk)...\n"
    return prefix + tree[-(limit - len(prefix)) :]


def exception_banner(tree: str, *, limit: int = _EXCEPTION_BANNER_CHARS) -> str:
    """Exception type, message, and last file:line — for the manager query."""
    cleaned = (tree or "").strip()
    if not cleaned:
        return ""
    exception = ""
    frame = ""
    for line in cleaned.splitlines():
        stripped = line.strip()
        match = _EXCEPTION_LINE_RE.match(stripped)
        if match:
            exception = stripped
            continue
        file_match = _FILE_FRAME_RE.search(line)
        if file_match:
            path = file_match.group(1).replace("\\", "/").rsplit("/", 1)[-1]
            frame = f"{path}:{file_match.group(2)}"
    if exception and frame:
        banner = f"{exception} in {frame}"
    else:
        banner = exception or frame or cleaned.splitlines()[-1].strip()
    if len(banner) <= limit:
        return banner
    return banner[: limit - 1] + "…"
