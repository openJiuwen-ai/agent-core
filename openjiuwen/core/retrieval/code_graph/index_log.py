# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Small helpers for Code Graph index logs. Does not change tool/status contracts."""

from __future__ import annotations

from openjiuwen.core.common.logging import retrieval_logger as logger
from openjiuwen.core.retrieval.code_graph.models import (
    SEARCHABLE_SYMBOL_KINDS,
    CodeGraphIndex,
)

SKIPPED_UNREADABLE_PREFIX = "skipped_unreadable="
_SAMPLE_LIMIT = 3


def definition_count(index: CodeGraphIndex) -> int:
    """Searchable symbols. FILE stubs alone are not definitions."""
    return sum(1 for symbol in index.symbols.values() if symbol.kind in SEARCHABLE_SYMBOL_KINDS)


def note_skipped_unreadable(index: CodeGraphIndex, paths: list[str]) -> None:
    """One warning + one log line instead of a WARNING per missing file."""
    if not paths:
        return
    index.warnings = [item for item in index.warnings if not item.startswith(SKIPPED_UNREADABLE_PREFIX)]
    sample = ",".join(paths[:_SAMPLE_LIMIT])
    extra = f" sample={sample}" if sample else ""
    index.warnings.append(f"{SKIPPED_UNREADABLE_PREFIX}{len(paths)}{extra}")
    logger.warning(
        "code_graph skip unreadable count=%s sample=%s repo=%s",
        len(paths),
        sample or "-",
        index.repo_root,
    )
