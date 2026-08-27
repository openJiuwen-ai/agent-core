# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Code Graph data models.

These types describe source-code symbols and relations. They are independent of
the workflow graph in ``openjiuwen.core.graph`` and of the generic knowledge
graph in ``openjiuwen.core.retrieval.graph_knowledge_base``.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Mapping, Sequence

if TYPE_CHECKING:
    from openjiuwen.core.retrieval.code_graph.indexing.symbol_extractor import ExtractedFile
    from openjiuwen.core.retrieval.code_graph.query.lexical import LexicalIndex


class SymbolKind(StrEnum):
    """Semantic kind of a code entity."""

    FILE = "file"
    MODULE = "module"
    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"
    INTERFACE = "interface"
    STRUCT = "struct"
    TRAIT = "trait"
    VARIABLE = "variable"


class RelationKind(StrEnum):
    """Directed relation between two symbols.

    Query APIs accept both forward names (``calls``) and inverse names
    (``called_by``). Only forward kinds are stored on the index.
    """

    CONTAINS = "contains"
    CONTAINED_BY = "contained_by"
    INHERITS = "inherits"
    INHERITED_BY = "inherited_by"
    CALLS = "calls"
    CALLED_BY = "called_by"
    IMPORTS = "imports"
    IMPORTED_BY = "imported_by"


FORWARD_RELATIONS: frozenset[RelationKind] = frozenset(
    {
        RelationKind.CONTAINS,
        RelationKind.INHERITS,
        RelationKind.CALLS,
        RelationKind.IMPORTS,
    }
)

INVERSE_RELATIONS: Mapping[RelationKind, RelationKind] = {
    RelationKind.CONTAINS: RelationKind.CONTAINED_BY,
    RelationKind.CONTAINED_BY: RelationKind.CONTAINS,
    RelationKind.INHERITS: RelationKind.INHERITED_BY,
    RelationKind.INHERITED_BY: RelationKind.INHERITS,
    RelationKind.CALLS: RelationKind.CALLED_BY,
    RelationKind.CALLED_BY: RelationKind.CALLS,
    RelationKind.IMPORTS: RelationKind.IMPORTED_BY,
    RelationKind.IMPORTED_BY: RelationKind.IMPORTS,
}

DEFAULT_EXCLUDE_DIRS: tuple[str, ...] = (
    ".git",
    ".hg",
    ".svn",
    ".idea",
    ".vscode",
    ".cursor",
    ".claude",
    ".venv",
    "venv",
    "node_modules",
    "bower_components",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".tox",
    ".nox",
    ".ruff_cache",
    ".hypothesis",
    ".nyc_output",
    ".ipynb_checkpoints",
    ".cache",
    ".code_graph_cache",
    ".parcel-cache",
    ".turbo",
    ".next",
    ".nuxt",
    ".output",
    ".svelte-kit",
    "dist",
    "build",
    "_build",
    "_site",
    "target",
    "vendor",
    "eggs",
    "htmlcov",
    "coverage",
    "__snapshots__",
    "playwright-report",
    ".worktrees",
    "_worktrees",
)

# Directory names that are not a fixed basename (``eggs`` vs ``foo.egg-info``).
# Walks prune these as directories; ``exclude_globs`` only matches files.
DEFAULT_EXCLUDE_DIR_GLOBS: tuple[str, ...] = ("*.egg-info",)

SEARCHABLE_SYMBOL_KINDS: frozenset[SymbolKind] = frozenset(
    {
        SymbolKind.CLASS,
        SymbolKind.FUNCTION,
        SymbolKind.METHOD,
        SymbolKind.INTERFACE,
        SymbolKind.STRUCT,
        SymbolKind.TRAIT,
        SymbolKind.MODULE,
    }
)

CLASS_LIKE_KINDS: frozenset[SymbolKind] = frozenset(
    {
        SymbolKind.CLASS,
        SymbolKind.INTERFACE,
        SymbolKind.STRUCT,
        SymbolKind.TRAIT,
    }
)

TEXT_FILE_EXTENSIONS: tuple[str, ...] = (".md", ".rst", ".txt")
LEXICAL_TOKENIZER_VERSION = "ident-camel-v1"

# Bump whenever index contents or stored relation fields change shape. It feeds
# ``CodeGraphConfig.config_hash()``, so an older on-disk index can never be
# loaded into a newer reader.
INDEX_SCHEMA_VERSION = 8


class CallResolution(StrEnum):
    """How a call edge target was determined, ordered most to least certain."""

    EXACT = "exact"
    SAME_CLASS = "same_class"
    RECEIVER_TYPE = "receiver_type"
    LOCAL_ASSIGNMENT = "local_assignment"
    IMPORTED = "imported"
    SAME_FILE = "same_file"
    UNIQUE = "unique"
    UNRESOLVED = "unresolved"


RESOLUTION_CONFIDENCE: Mapping[CallResolution, float] = {
    CallResolution.EXACT: 1.0,
    CallResolution.SAME_CLASS: 0.95,
    CallResolution.RECEIVER_TYPE: 0.9,
    CallResolution.LOCAL_ASSIGNMENT: 0.9,
    CallResolution.IMPORTED: 0.85,
    CallResolution.SAME_FILE: 0.8,
    CallResolution.UNIQUE: 0.6,
    CallResolution.UNRESOLVED: 0.0,
}

DEFAULT_EXCLUDE_GLOBS: tuple[str, ...] = (
    "*.pyc",
    "*.pyo",
    "*.so",
    "*.dylib",
    "*.dll",
    "*.exe",
    "*.bin",
    "*.png",
    "*.jpg",
    "*.jpeg",
    "*.gif",
    "*.webp",
    "*.pdf",
    "*.zip",
    "*.tar",
    "*.gz",
    "*.whl",
    "*.egg",
    "*.class",
    "*.o",
    "*.a",
    "*.min.js",
    "*.min.mjs",
    "*.min.cjs",
    "*.min.css",
    "*.map",
    "*.snap",
    "coverage.xml",
    "*.lcov",
)


@dataclass(frozen=True)
class CodeGraphConfig:
    """Resource limits and cache settings for a Code Graph index."""

    cache_dir: str | None = None
    # Laptop-safe product defaults. A 16GB machine also hosts the LLM client
    # and multiple chats; the graph must not take the rest of RAM or disk.
    # User-facing repo + resource caps. Time is not an admission or wait
    # limit: an admitted repo waits until the new graph is ready.
    max_files: int = 5000
    max_file_bytes: int = 1_048_576
    max_source_bytes: int = 41_943_040
    max_index_size_mb: int = 1024
    max_cache_size_mb: int | None = 2048
    max_process_index_memory_mb: int = 1536
    query_timeout_seconds: float = 10.0
    index_timeout_seconds: float = 90.0
    query_wait_seconds: float | None = None
    first_build_wait_seconds: float | None = None
    # Process RSS (RAM) gate, not disk and not back-calculated from 2000/16MB.
    # 16GB laptop policy: AgentServer + LLM client + up to 3 cached graphs
    # should not take the rest of RAM. Measured product path at the default
    # admission edge (~2000 files / 16MB): one graph ≈ +0.3–0.6GB RSS.
    # Full agent-core (over the 8s package) ≈ +1.2GB. 4GB is "process already
    # too big, do not start another build", not "one graph needs 4GB".
    max_build_rss_mb: int = 4096
    watch_interval_seconds: float = 2.0
    max_ast_depth: int = 12
    max_cached_repos: int = 3
    memory_idle_ttl_seconds: float = 1800.0
    disk_ttl_days: int = 14
    # 0 = always recompute the workspace token before a query. A positive
    # window can skip that walk during a find_* burst, but then a Shell/IDE
    # edit with no mark_dirty looks READY on the old generation.
    freshness_check_interval_ms: int = 0
    incremental_max_files: int = 60
    small_repo_rebuild_seconds: float = 1.0
    max_concurrent_builds: int = 1
    exclude_dirs: tuple[str, ...] = DEFAULT_EXCLUDE_DIRS
    exclude_dir_globs: tuple[str, ...] = DEFAULT_EXCLUDE_DIR_GLOBS
    exclude_globs: tuple[str, ...] = DEFAULT_EXCLUDE_GLOBS
    follow_symlinks: bool = False
    # Retrieval (does not affect the on-disk index hash).
    ban_tests: bool = True
    search_backend: str = "bm25"
    # Lexical index settings (do affect the on-disk index hash).
    index_definition_bodies: bool = True
    index_text_files: bool = False
    text_chunk_chars: int = 1000
    text_chunk_overlap: int = 200
    text_file_extensions: tuple[str, ...] = TEXT_FILE_EXTENSIONS

    def resolved_wait_seconds(self, *, first_build: bool) -> float | None:
        """Optional wait before BUILDING. ``None`` means wait until ready.

        Product yaml does not set a wait. Tests may set the explicit fields.
        A timeout never serves an old generation.
        """
        explicit = self.first_build_wait_seconds if first_build else self.query_wait_seconds
        if explicit is None:
            return None
        return max(0.1, float(explicit))

    def disk_quota_bytes(self) -> int:
        """Directory quota for on-disk checkpoints (not a single-index RSS cap)."""
        mb = self.max_cache_size_mb if self.max_cache_size_mb is not None else self.max_index_size_mb
        return max(1, int(mb)) * 1024 * 1024

    def excludes_dir_name(self, name: str) -> bool:
        """True when a walk should prune this directory name."""
        if name in self.exclude_dirs:
            return True
        return any(fnmatch.fnmatch(name, glob) for glob in self.exclude_dir_globs)

    def config_hash(self) -> str:
        """Stable hash of settings that affect index contents."""
        import hashlib
        import json

        payload = {
            "max_files": self.max_files,
            "max_file_bytes": self.max_file_bytes,
            "max_source_bytes": self.max_source_bytes,
            "max_ast_depth": self.max_ast_depth,
            "exclude_dirs": list(self.exclude_dirs),
            "exclude_dir_globs": list(self.exclude_dir_globs),
            "exclude_globs": list(self.exclude_globs),
            "follow_symlinks": self.follow_symlinks,
            "index_definition_bodies": self.index_definition_bodies,
            "index_text_files": self.index_text_files,
            "text_chunk_chars": self.text_chunk_chars,
            "text_chunk_overlap": self.text_chunk_overlap,
            "text_file_extensions": list(self.text_file_extensions),
            "tokenizer": LEXICAL_TOKENIZER_VERSION,
            "schema": INDEX_SCHEMA_VERSION,
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class Symbol:
    """A source-code entity that can be queried by name or relation."""

    symbol_id: str
    name: str
    kind: SymbolKind
    file: str
    start_line: int
    end_line: int
    qualified_name: str = ""
    language: str = ""
    parent_id: str | None = None
    signature: str = ""

    def to_match(self, score: float = 1.0) -> "CodeMatch":
        return CodeMatch(
            symbol_id=self.symbol_id,
            name=self.name,
            kind=self.kind.value,
            file=self.file,
            start_line=self.start_line,
            end_line=self.end_line,
            score=score,
            qualified_name=self.qualified_name or self.name,
        )


@dataclass(frozen=True)
class RelationEvidence:
    """Where an edge came from, so callers can verify a traversal result."""

    file: str = ""
    start_line: int = 0
    end_line: int = 0
    expression: str = ""
    resolution: str = CallResolution.EXACT.value
    confidence: float = 1.0

    def to_dict(self) -> dict[str, object]:
        return {
            "file": self.file,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "expression": self.expression,
            "resolution": self.resolution,
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class UnresolvedCall:
    """A call site whose target could not be determined without guessing."""

    caller_id: str
    callee_name: str
    expression: str
    file: str
    start_line: int
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {
            "caller_id": self.caller_id,
            "callee_name": self.callee_name,
            "expression": self.expression,
            "file": self.file,
            "start_line": self.start_line,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class Relation:
    """A directed edge stored in the forward direction only."""

    source_id: str
    kind: RelationKind
    target_id: str
    evidence: RelationEvidence | None = None


@dataclass(frozen=True)
class CodeMatch:
    """A search hit returned by ``search_code``."""

    symbol_id: str
    name: str
    kind: str
    file: str
    start_line: int
    end_line: int
    score: float
    qualified_name: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "symbol_id": self.symbol_id,
            "name": self.name,
            "kind": self.kind,
            "file": self.file,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "score": self.score,
            "qualified_name": self.qualified_name,
        }


@dataclass(frozen=True)
class RelatedHit:
    """A neighbor returned by ``expand_related``."""

    symbol_id: str
    name: str
    kind: str
    file: str
    start_line: int
    end_line: int
    relation: str
    depth: int
    qualified_name: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "symbol_id": self.symbol_id,
            "name": self.name,
            "kind": self.kind,
            "file": self.file,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "relation": self.relation,
            "depth": self.depth,
            "qualified_name": self.qualified_name,
        }


def edge_key(source_id: str, kind: RelationKind, target_id: str) -> str:
    """Stable string key for an edge. Pickled, so keep it a plain string."""
    return f"{source_id}|{kind.value}|{target_id}"


@dataclass
class CodeGraphIndex:
    """In-memory code knowledge graph for one repository snapshot."""

    repo_root: str
    snapshot: str
    config_hash: str
    symbols: dict[str, Symbol] = field(default_factory=dict)
    relations: list[Relation] = field(default_factory=list)
    outgoing: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    incoming: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    by_file: dict[str, list[str]] = field(default_factory=dict)
    by_name: dict[str, list[str]] = field(default_factory=dict)
    file_count: int = 0
    warnings: list[str] = field(default_factory=list)
    lexical: "LexicalIndex | None" = None
    schema_version: int = INDEX_SCHEMA_VERSION
    edge_evidence: dict[str, list[RelationEvidence]] = field(default_factory=dict)
    unresolved_calls: list[UnresolvedCall] = field(default_factory=list)
    # Per-file extraction results, kept so one changed file can be re-parsed and
    # the cross-file edges re-resolved without walking the repository again.
    extracted: dict[str, "ExtractedFile"] = field(default_factory=dict)
    file_hashes: dict[str, str] = field(default_factory=dict)
    # Files whose last refresh failed. A query may still answer from the old
    # slice, but it must not claim the slice is current.
    stale_files: list[str] = field(default_factory=list)
    # Incremented by every successful refresh so a caller can tell a refreshed
    # index from the build it started as.
    revision: int = 0

    def copy_for_session(self) -> "CodeGraphIndex":
        """A fork that one repair session can refresh without touching the base.

        Relation containers and the lexical index are shared by reference: every
        refresh rebuilds them into new containers instead of mutating in place,
        so the base index a concurrent session is reading stays intact.
        """
        return CodeGraphIndex(
            repo_root=self.repo_root,
            snapshot=self.snapshot,
            config_hash=self.config_hash,
            symbols=dict(self.symbols),
            relations=self.relations,
            outgoing=self.outgoing,
            incoming=self.incoming,
            by_file={file: list(ids) for file, ids in self.by_file.items()},
            by_name={name: list(ids) for name, ids in self.by_name.items()},
            file_count=self.file_count,
            warnings=list(self.warnings),
            lexical=self.lexical,
            schema_version=self.schema_version,
            edge_evidence=self.edge_evidence,
            unresolved_calls=list(self.unresolved_calls),
            extracted=dict(self.extracted),
            file_hashes=dict(self.file_hashes),
            stale_files=list(self.stale_files),
            revision=self.revision,
        )

    def drop_file(self, file: str) -> list[Symbol]:
        """Forget every symbol of ``file``. Returns what was removed.

        Relations are not touched here: cross-file edges are re-resolved as a
        whole after the changed files are re-parsed, because one file's content
        decides how calls in other files resolve.
        """
        removed = [
            self.symbols.pop(symbol_id)
            for symbol_id in self.by_file.pop(file, [])
            if symbol_id in self.symbols
        ]
        dropped = {symbol.symbol_id for symbol in removed}
        for key, ids in list(self.by_name.items()):
            kept = [symbol_id for symbol_id in ids if symbol_id not in dropped]
            if kept:
                self.by_name[key] = kept
            else:
                self.by_name.pop(key, None)
        self.extracted.pop(file, None)
        self.file_hashes.pop(file, None)
        return removed

    def add_symbol(self, symbol: Symbol) -> None:
        self.symbols[symbol.symbol_id] = symbol
        self.by_file.setdefault(symbol.file, []).append(symbol.symbol_id)
        key = symbol.name.lower()
        self.by_name.setdefault(key, []).append(symbol.symbol_id)
        if symbol.qualified_name:
            qkey = symbol.qualified_name.lower()
            if qkey != key:
                self.by_name.setdefault(qkey, []).append(symbol.symbol_id)

    def add_relation(self, relation: Relation) -> None:
        if relation.kind not in FORWARD_RELATIONS:
            relation = Relation(
                source_id=relation.target_id,
                kind=INVERSE_RELATIONS[relation.kind],
                target_id=relation.source_id,
                evidence=relation.evidence,
            )
        self.relations.append(relation)
        self.outgoing.setdefault(relation.source_id, {}).setdefault(
            relation.kind.value, []
        ).append(relation.target_id)
        inverse = INVERSE_RELATIONS[relation.kind]
        self.incoming.setdefault(relation.target_id, {}).setdefault(
            inverse.value, []
        ).append(relation.source_id)
        if relation.evidence is not None:
            key = edge_key(relation.source_id, relation.kind, relation.target_id)
            self.edge_evidence.setdefault(key, []).append(relation.evidence)

    def evidence_for(
        self,
        source_id: str,
        kind: RelationKind,
        target_id: str,
    ) -> list[RelationEvidence]:
        """Evidence for an edge, accepting either direction of ``kind``."""
        if kind in FORWARD_RELATIONS:
            return list(self.edge_evidence.get(edge_key(source_id, kind, target_id), ()))
        forward = INVERSE_RELATIONS[kind]
        return list(self.edge_evidence.get(edge_key(target_id, forward, source_id), ()))

    def neighbors(
        self,
        symbol_id: str,
        relation: RelationKind,
    ) -> Sequence[str]:
        if relation in FORWARD_RELATIONS:
            return self.outgoing.get(symbol_id, {}).get(relation.value, ())
        return self.incoming.get(symbol_id, {}).get(relation.value, ())
