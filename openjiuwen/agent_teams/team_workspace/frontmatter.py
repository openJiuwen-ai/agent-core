# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""frontmatter read/write primitives for evolvable assets.

Every evolvable workspace file is ``YAML frontmatter + body``. The frontmatter
carries ``kind`` / ``name`` / ``language`` and the evolution control
``baseline_sha256`` (maintained by the assembler). Evolved-ness is judged at
read time by body-hash divergence from the baseline — the ``evolved`` field
in the frontmatter is the write-time initial state only (always False).

Reading degrades gracefully: a file not starting with ``---`` is treated as a
hand-written body (empty meta, always treated as evolved). A malformed YAML
block (parse failure or non-mapping root) raises ``ValueError`` — callers
treat such files as *invalid*: the read side falls back to the framework
default / DB value, the write side rebuilds the baseline.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import yaml

from openjiuwen.core.common.logging import team_logger

_FRONTMATTER_DELIM = "---"


def body_sha256(body: str) -> str:
    """Return the sha256 of a body (the baseline comparison value)."""
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def is_evolved(meta: dict, body: str) -> bool:
    """Return whether ``body`` has diverged from its recorded baseline.

    The single evolution judgement: a file is *evolved* when its
    body hash no longer equals the ``baseline_sha256`` stamped at write time.
    A file without a baseline (hand-written, no frontmatter) is always evolved
    — its body always wins, backward-compatible with hand-edited md. The
    ``evolved`` field in the frontmatter is the write-time initial state only
    (always ``False``); the read side never trusts it.

    Callers (``WorkspaceStore`` write/read protection, ``WorkspaceCache`` lazy
    get, ``WorkspaceAssembler`` baseline seeding) all route through here so
    the three sites cannot drift apart.
    """
    baseline = meta.get("baseline_sha256")
    if baseline is None:
        return True
    return body_sha256(body) != baseline


def read_frontmatter(text: str) -> tuple[dict, str]:
    """Split a file's YAML frontmatter from its body.

    Returns ``(meta, body)`` — the body is byte-faithful (trailing newlines /
    line endings preserved) so ``body_sha256`` round-trips: a body written
    via ``write_frontmatter`` reads back hash-identical. A file not starting
    with ``---`` yields ``({}, text)`` (hand-written body — treated as
    evolved by callers).

    Raises:
        ValueError: when the frontmatter block is malformed (YAML parse
            failure or a non-mapping root) — the file is invalid and callers
            must not treat its body as a workspace value.
    """
    if not text.startswith(_FRONTMATTER_DELIM):
        return {}, text
    lines = text.splitlines()
    if len(lines) < 2:
        return {}, text
    end = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() == _FRONTMATTER_DELIM:
            end = idx
            break
    if end is None:
        return {}, text
    meta_text = "\n".join(lines[1:end])
    # ``splitlines(keepends=True)`` preserves every byte after the closing
    # delimiter (trailing newline included) — ``splitlines()`` alone would
    # drop a body-final ``\n`` and break the sha256 round-trip. (``start`` is
    # pre-computed: ruff/black force spaces around ``:`` in expression slices,
    # which the codecheck G.FMT.04 rule rejects.)
    start = end + 1
    body = "".join(text.splitlines(keepends=True)[start:])
    try:
        meta = yaml.safe_load(meta_text) or {}
    except yaml.YAMLError as exc:
        team_logger.warning("malformed frontmatter YAML: %s", exc)
        raise ValueError(f"malformed frontmatter YAML: {exc}") from exc
    if not isinstance(meta, dict):
        raise ValueError(f"frontmatter root is not a mapping: {type(meta).__name__}")
    return meta, body


def write_frontmatter(meta: dict, body: str) -> str:
    """Serialize ``meta`` + ``body`` into a frontmatter-prefixed document."""
    meta_text = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True).strip()
    return f"{_FRONTMATTER_DELIM}\n{meta_text}\n{_FRONTMATTER_DELIM}\n{body}"


def atomic_write(path: Path, text: str) -> None:
    """Atomically write ``text`` to ``path`` (tmp + rename)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


__all__ = [
    "atomic_write",
    "body_sha256",
    "is_evolved",
    "read_frontmatter",
    "write_frontmatter",
]
