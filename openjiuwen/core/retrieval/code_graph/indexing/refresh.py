# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Incremental refresh of an existing ``CodeGraphIndex``."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from openjiuwen.core.common.logging import retrieval_logger as logger
from openjiuwen.core.retrieval.code_graph.indexing.builder import (
    definition_documents,
    extract_one_file,
    resolve_relations,
    text_documents,
)
from openjiuwen.core.retrieval.code_graph.indexing.language_registry import language_from_path
from openjiuwen.core.retrieval.code_graph.models import (
    CodeGraphConfig,
    CodeGraphIndex,
    SymbolKind,
)
from openjiuwen.core.retrieval.code_graph.query.lexical import (
    LexicalDocument,
    LexicalIndexBuilder,
    update_documents,
)
from openjiuwen.core.retrieval.code_graph.snapshot import compute_snapshot


@dataclass
class RefreshResult:
    """What one incremental refresh actually changed."""

    updated: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    revision: int = 0

    @property
    def changed(self) -> bool:
        return bool(self.updated or self.removed)

    @property
    def stale(self) -> bool:
        return bool(self.failed)

    def to_dict(self) -> dict[str, object]:
        return {
            "updated": list(self.updated),
            "removed": list(self.removed),
            "unchanged": list(self.unchanged),
            "skipped": list(self.skipped),
            "failed": list(self.failed),
            "stale": self.stale,
            "revision": self.revision,
            "warnings": list(self.warnings),
        }


def refresh_index_files(
    index: CodeGraphIndex,
    paths: list[str],
    config: CodeGraphConfig | None = None,
) -> RefreshResult:
    """Re-parse ``paths`` in place and re-resolve the edges they can affect.

    Only the named files are read from disk; the rest of the graph is rebuilt
    from cached per-file extraction results. A file that vanished is removed, a
    new file is added, and a file that failed to parse is recorded in
    ``failed`` so callers can report a stale slice instead of serving the old
    snapshot as if it were current.
    """
    cfg = config or CodeGraphConfig()
    root = Path(index.repo_root).resolve()
    result = RefreshResult(revision=index.revision)
    dropped_files: set[str] = set()
    added_documents: list[tuple[LexicalDocument, list[str]]] = []

    for rel in _normalize(root, paths, result):
        absolute = root / rel
        if not absolute.is_file():
            if rel in index.extracted or rel in index.file_hashes:
                index.drop_file(rel)
                dropped_files.add(rel)
                result.removed.append(rel)
            else:
                result.skipped.append(rel)
            continue
        if language_from_path(absolute) is None:
            documents = _refresh_text_file(index, root, rel, cfg, result)
            if documents is not None:
                dropped_files.add(rel)
                added_documents.extend(documents)
            continue
        try:
            parsed = extract_one_file(absolute, rel, cfg)
        except Exception as exc:  # noqa: BLE001 — a bad parse must not kill the session
            logger.warning("code_graph refresh failed for %s: %s", rel, exc)
            result.failed.append(rel)
            continue
        if parsed is None or parsed.oversized or parsed.extracted is None:
            result.skipped.append(rel)
            if parsed is not None and parsed.oversized:
                result.warnings.append(f"skipped oversized file {rel}")
            continue
        if index.file_hashes.get(rel) == parsed.content_hash and rel in index.extracted:
            result.unchanged.append(rel)
            continue
        index.drop_file(rel)
        dropped_files.add(rel)
        index.extracted[rel] = parsed.extracted
        index.file_hashes[rel] = parsed.content_hash
        for symbol in parsed.extracted.symbols:
            index.add_symbol(symbol)
        if cfg.index_definition_bodies:
            for symbol in parsed.extracted.symbols:
                added_documents.extend(definition_documents(symbol, parsed.text))
        result.updated.append(rel)

    index.stale_files = sorted(set(index.stale_files) - set(result.updated) | set(result.failed))
    if result.changed:
        resolve_relations(index)
        index.file_count = len({sym.file for sym in index.symbols.values() if sym.kind == SymbolKind.FILE})
        index.lexical = update_documents(
            index.lexical if index.lexical is not None else LexicalIndexBuilder().freeze(),
            dropped_files=dropped_files,
            added=added_documents,
        )
        index.revision += 1
    # The index must claim the working tree's snapshot even when nothing changed
    # (an unparsed doc, an unchanged body): otherwise the next query sees a moved
    # snapshot, rebuilds the whole repository, and throws away the session index
    # — the exact cost this refresh exists to avoid. Files it could not parse are
    # reported through ``stale_files`` instead.
    index.snapshot = compute_snapshot(root)
    result.revision = index.revision
    return result


def _normalize(root: Path, paths: list[str], result: RefreshResult) -> list[str]:
    """Repo-relative posix paths, de-duplicated, outside-repo entries rejected."""
    seen: list[str] = []
    for raw in paths:
        if not raw:
            continue
        candidate = Path(raw)
        absolute = candidate if candidate.is_absolute() else root / candidate
        try:
            rel = absolute.resolve().relative_to(root).as_posix()
        except ValueError:
            result.warnings.append(f"path outside repository ignored: {raw}")
            continue
        if rel not in seen:
            seen.append(rel)
    return seen


def _refresh_text_file(
    index: CodeGraphIndex,
    root: Path,
    rel: str,
    config: CodeGraphConfig,
    result: RefreshResult,
) -> list[tuple[LexicalDocument, list[str]]] | None:
    """Re-chunk a non-code text file, or ``None`` if it is not indexed at all."""
    if not config.index_text_files:
        result.skipped.append(rel)
        return None
    suffix = Path(rel).suffix.lower()
    if suffix not in {ext.lower() for ext in config.text_file_extensions}:
        result.skipped.append(rel)
        return None
    try:
        raw = (root / rel).read_bytes()
    except OSError as exc:
        logger.warning("code_graph refresh failed for %s: %s", rel, exc)
        result.failed.append(rel)
        return None
    if len(raw) > config.max_file_bytes:
        result.skipped.append(rel)
        result.warnings.append(f"skipped oversized file {rel}")
        return None
    result.updated.append(rel)
    return text_documents(rel, raw.decode("utf-8", errors="replace"), config)
