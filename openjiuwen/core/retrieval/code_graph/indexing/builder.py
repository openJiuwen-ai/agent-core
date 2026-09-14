# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Walk a repository and build a ``CodeGraphIndex``."""

from __future__ import annotations

import fnmatch
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

from openjiuwen.core.common.logging import retrieval_logger as logger
from openjiuwen.core.retrieval.code_graph.indexing.language_registry import (
    language_from_path,
)
from openjiuwen.core.retrieval.code_graph.indexing.parser import parse_source
from openjiuwen.core.retrieval.code_graph.indexing.symbol_extractor import (
    ExtractedFile,
    PendingCall,
    PendingImport,
    PendingInherit,
    extract_file,
)
from openjiuwen.core.retrieval.code_graph.models import (
    CLASS_LIKE_KINDS,
    RESOLUTION_CONFIDENCE,
    SEARCHABLE_SYMBOL_KINDS,
    CallResolution,
    CodeGraphConfig,
    CodeGraphIndex,
    Relation,
    RelationEvidence,
    RelationKind,
    SymbolKind,
    UnresolvedCall,
)
from openjiuwen.core.retrieval.code_graph.query.lexical import (
    CORPUS_DEFINITION,
    CORPUS_TEXT,
    LexicalDocument,
    LexicalIndexBuilder,
    tokenize,
)
from openjiuwen.core.retrieval.code_graph.query.search_code import definition_doc_id
from openjiuwen.core.retrieval.code_graph.snapshot import compute_snapshot


def build_index(repo_root: str | Path, config: CodeGraphConfig | None = None) -> CodeGraphIndex:
    """Parse ``repo_root`` and return a queryable in-memory index."""
    cfg = config or CodeGraphConfig()
    root = Path(repo_root).resolve()
    index = CodeGraphIndex(
        repo_root=str(root),
        snapshot=compute_snapshot(root),
        config_hash=cfg.config_hash(),
    )
    files = list(_iter_source_files(root, cfg))
    if len(files) > cfg.max_files:
        index.warnings.append(
            f"repository has {len(files)} source files; indexing the first {cfg.max_files}"
        )
        files = files[: cfg.max_files]

    sources: dict[str, str] = {}
    for path in files:
        rel = path.relative_to(root).as_posix()
        parsed = extract_one_file(path, rel, cfg)
        if parsed is None:
            continue
        if parsed.oversized:
            index.warnings.append(f"skipped oversized file {rel}")
            continue
        if parsed.extracted is None:
            continue
        sources[rel] = parsed.text
        index.extracted[rel] = parsed.extracted
        index.file_hashes[rel] = parsed.content_hash
        for symbol in parsed.extracted.symbols:
            index.add_symbol(symbol)

    resolve_relations(index)
    index.file_count = len({sym.file for sym in index.symbols.values() if sym.kind == SymbolKind.FILE})
    index.lexical = _build_lexical_index(index, sources, root, cfg)
    return index


@dataclass
class ParsedFile:
    """One file's parse result, or the reason it produced nothing."""

    rel_path: str
    text: str = ""
    content_hash: str = ""
    extracted: ExtractedFile | None = None
    oversized: bool = False


def extract_one_file(path: Path, rel: str, config: CodeGraphConfig) -> ParsedFile | None:
    """Parse a single file. ``None`` means "not an indexable source file"."""
    language = language_from_path(path)
    if language is None:
        return None
    try:
        source = path.read_bytes()
    except OSError as exc:
        logger.warning("code_graph skip unreadable %s: %s", rel, exc)
        return None
    if len(source) > config.max_file_bytes:
        return ParsedFile(rel_path=rel, oversized=True)
    tree = parse_source(path, source)
    return ParsedFile(
        rel_path=rel,
        text=source.decode("utf-8", errors="replace"),
        content_hash=hashlib.sha256(source).hexdigest()[:16],
        extracted=extract_file(
            rel_path=rel,
            language=language,
            tree=tree,
            max_depth=config.max_ast_depth,
        ),
    )


def resolve_relations(index: CodeGraphIndex) -> None:
    """Rebuild every edge from the per-file extraction results.

    Edges are rebuilt as a whole rather than per file because resolution is
    repo-wide: whether a call in file A resolves depends on what names file B
    defines and imports. New containers are assigned instead of mutated so a
    session fork sharing the base containers is never corrupted.
    """
    index.relations = []
    index.outgoing = {}
    index.incoming = {}
    index.edge_evidence = {}
    index.unresolved_calls = []
    extracted = list(index.extracted.values())
    for item in extracted:
        for parent_id, child_id in item.contains:
            index.add_relation(
                Relation(source_id=parent_id, kind=RelationKind.CONTAINS, target_id=child_id)
            )
    _resolve_inherits(index, extracted)
    # Imports resolve first: an explicit import is stronger call-target evidence
    # than a repo-wide unique name.
    import_targets = _resolve_imports(index, extracted)
    _resolve_calls(index, extracted, import_targets)


def _iter_source_files(root: Path, config: CodeGraphConfig) -> list[Path]:
    skip_dirs = set(config.exclude_dirs)
    gitignore = _load_gitignore(root)
    collected: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=config.follow_symlinks):
        dirnames[:] = [
            name
            for name in dirnames
            if name not in skip_dirs and not name.endswith(".egg-info")
        ]
        current = Path(dirpath)
        for name in filenames:
            path = current / name
            rel = path.relative_to(root).as_posix()
            if language_from_path(path) is None:
                continue
            if any(fnmatch.fnmatch(name, glob) for glob in config.exclude_globs):
                continue
            if gitignore and _is_ignored(rel, gitignore):
                continue
            collected.append(path)
    collected.sort()
    return collected


def _load_gitignore(root: Path) -> list[str]:
    path = root / ".gitignore"
    if not path.is_file():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    patterns: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("!"):
            continue
        patterns.append(stripped.rstrip("/"))
    return patterns


def _is_ignored(rel: str, patterns: list[str]) -> bool:
    name = rel.rsplit("/", 1)[-1]
    for pattern in patterns:
        if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(name, pattern):
            return True
        if pattern.endswith("/**") and rel.startswith(pattern[:-3]):
            return True
        if "/" not in pattern and fnmatch.fnmatch(rel, f"**/{pattern}"):
            return True
    return False


def _resolve_inherits(index: CodeGraphIndex, extracted: list[ExtractedFile]) -> None:
    class_index: dict[str, list[str]] = {}
    for symbol in index.symbols.values():
        if symbol.kind in {SymbolKind.CLASS, SymbolKind.INTERFACE, SymbolKind.STRUCT, SymbolKind.TRAIT}:
            class_index.setdefault(symbol.name, []).append(symbol.symbol_id)

    pending: list[PendingInherit] = []
    for item in extracted:
        pending.extend(item.inherits)

    for edge in pending:
        candidates = class_index.get(edge.base_name, [])
        if not candidates:
            continue
        same_file = [cid for cid in candidates if cid.startswith(f"{edge.file}::")]
        if len(same_file) == 1:
            chosen, resolution = same_file[0], CallResolution.SAME_FILE
        elif len(candidates) == 1:
            chosen, resolution = candidates[0], CallResolution.UNIQUE
        else:
            continue
        subclass = index.symbols.get(edge.subclass_id)
        index.add_relation(
            Relation(
                source_id=edge.subclass_id,
                kind=RelationKind.INHERITS,
                target_id=chosen,
                evidence=RelationEvidence(
                    file=edge.file,
                    start_line=subclass.start_line if subclass is not None else 0,
                    end_line=subclass.start_line if subclass is not None else 0,
                    expression=edge.base_name,
                    resolution=resolution.value,
                    confidence=RESOLUTION_CONFIDENCE[resolution],
                ),
            )
        )


def _resolve_calls(
    index: CodeGraphIndex,
    extracted: list[ExtractedFile],
    import_targets: dict[str, dict[str, str]],
) -> None:
    """Resolve call sites to targets, recording how each edge was decided.

    An ambiguous call site produces no edge. It is appended to
    ``index.unresolved_calls`` so query tools can report the gap instead of
    presenting a guess as a graph fact.
    """
    pending: list[PendingCall] = []
    for item in extracted:
        pending.extend(item.calls)

    class_methods = _class_method_map(index)
    class_ids_by_name = _class_ids_by_name(index)
    local_types = _local_assignment_types(index, extracted, class_ids_by_name)

    for call in pending:
        target, resolution = _resolve_call_target(
            index,
            call,
            import_targets=import_targets,
            class_methods=class_methods,
            class_ids_by_name=class_ids_by_name,
            local_types=local_types.get(call.caller_id, {}),
        )
        if target is None:
            index.unresolved_calls.append(
                UnresolvedCall(
                    caller_id=call.caller_id,
                    callee_name=call.callee_name,
                    expression=call.callee_expression,
                    file=call.file,
                    start_line=call.start_line,
                    reason=f"no unambiguous target for {call.callee_name!r}",
                )
            )
            continue
        if target == call.caller_id:
            # Direct self-recursion adds no reachable neighbor; traversal guards
            # cycles anyway, and a self loop would pollute expand_related output.
            continue
        index.add_relation(
            Relation(
                source_id=call.caller_id,
                kind=RelationKind.CALLS,
                target_id=target,
                evidence=RelationEvidence(
                    file=call.file,
                    start_line=call.start_line,
                    end_line=call.end_line or call.start_line,
                    expression=call.callee_expression,
                    resolution=resolution.value,
                    confidence=RESOLUTION_CONFIDENCE[resolution],
                ),
            )
        )


def _resolve_call_target(
    index: CodeGraphIndex,
    call: PendingCall,
    *,
    import_targets: dict[str, dict[str, str]],
    class_methods: dict[str, dict[str, str]],
    class_ids_by_name: dict[str, list[str]],
    local_types: dict[str, str] | None = None,
) -> tuple[str | None, CallResolution]:
    name = call.callee_name
    if not name:
        return None, CallResolution.UNRESOLVED
    if name in index.symbols:
        return name, CallResolution.EXACT

    receiver = (call.receiver or "").strip()
    if call.caller_class_id and receiver in {"", "self", "cls", "this"}:
        hit = _method_on_class(index, call.caller_class_id, name, class_methods)
        if hit is not None:
            return hit, CallResolution.SAME_CLASS

    if receiver:
        local_class = (local_types or {}).get(receiver)
        if local_class:
            hit = _method_on_class(index, local_class, name, class_methods)
            if hit is not None:
                return hit, CallResolution.LOCAL_ASSIGNMENT
        # ``Alpha().run()`` and ``pkg.Alpha.run()`` both name the class Alpha.
        base = receiver.rsplit(".", 1)[-1].split("(", 1)[0].strip()
        candidates = class_ids_by_name.get(base, [])
        if len(candidates) == 1:
            hit = _method_on_class(index, candidates[0], name, class_methods)
            if hit is not None:
                return hit, CallResolution.RECEIVER_TYPE

    imported = import_targets.get(call.file, {}).get(name)
    if imported is not None and imported in index.symbols:
        return imported, CallResolution.IMPORTED

    pool = _callable_ids_by_name(index, name)
    if not pool:
        return None, CallResolution.UNRESOLVED
    same_file = [sid for sid in pool if index.symbols[sid].file == call.file]
    if len(same_file) == 1:
        return same_file[0], CallResolution.SAME_FILE
    if len(same_file) > 1:
        exact = [sid for sid in same_file if index.symbols[sid].name == name]
        if len(exact) == 1:
            return exact[0], CallResolution.SAME_FILE
        return None, CallResolution.UNRESOLVED
    if len(pool) == 1:
        return pool[0], CallResolution.UNIQUE
    # Several same-named callables in other files are distinct targets. Picking
    # one would be a guess, so the call site stays unresolved.
    return None, CallResolution.UNRESOLVED


def _callable_ids_by_name(index: CodeGraphIndex, name: str) -> list[str]:
    callable_kinds = {SymbolKind.FUNCTION, SymbolKind.METHOD, SymbolKind.CLASS}
    return [
        sid
        for sid in index.by_name.get(name.lower(), [])
        if sid in index.symbols and index.symbols[sid].kind in callable_kinds
    ]


def _class_method_map(index: CodeGraphIndex) -> dict[str, dict[str, str]]:
    """class symbol id -> {member name: member symbol id}."""
    mapping: dict[str, dict[str, str]] = {}
    for symbol in index.symbols.values():
        parent_id = symbol.parent_id
        if not parent_id:
            continue
        parent = index.symbols.get(parent_id)
        if parent is None or parent.kind not in CLASS_LIKE_KINDS:
            continue
        mapping.setdefault(parent_id, {}).setdefault(symbol.name, symbol.symbol_id)
    return mapping


def _class_ids_by_name(index: CodeGraphIndex) -> dict[str, list[str]]:
    mapping: dict[str, list[str]] = {}
    for symbol in index.symbols.values():
        if symbol.kind in CLASS_LIKE_KINDS:
            mapping.setdefault(symbol.name, []).append(symbol.symbol_id)
    return mapping


def _local_assignment_types(
    index: CodeGraphIndex,
    extracted: list[ExtractedFile],
    class_ids_by_name: dict[str, list[str]],
) -> dict[str, dict[str, str]]:
    """caller_id -> {local name: class symbol id} from constructor assignments.

    Ambiguous class names are skipped so an intermediate variable never becomes
    a guessed hop.
    """
    mapping: dict[str, dict[str, str]] = {}
    for item in extracted:
        for binding in item.assignments:
            candidates = class_ids_by_name.get(binding.type_name, [])
            if len(candidates) != 1:
                continue
            mapping.setdefault(binding.caller_id, {})[binding.name] = candidates[0]
    return mapping


def _method_on_class(
    index: CodeGraphIndex,
    class_id: str,
    name: str,
    class_methods: dict[str, dict[str, str]],
    *,
    max_depth: int = 5,
) -> str | None:
    """Find ``name`` on ``class_id`` or on a resolved base class."""
    queue: list[tuple[str, int]] = [(class_id, 0)]
    seen: set[str] = set()
    while queue:
        current, depth = queue.pop(0)
        if current in seen or depth > max_depth:
            continue
        seen.add(current)
        hit = class_methods.get(current, {}).get(name)
        if hit is not None:
            return hit
        for base_id in index.neighbors(current, RelationKind.INHERITS):
            queue.append((base_id, depth + 1))
    return None


def _resolve_imports(
    index: CodeGraphIndex,
    extracted: list[ExtractedFile],
) -> dict[str, dict[str, str]]:
    """Add IMPORTS edges and return ``{file: {imported name: symbol id}}``."""
    pending: list[PendingImport] = []
    for item in extracted:
        pending.extend(item.imports)

    file_ids = {
        symbol.file: symbol.symbol_id
        for symbol in index.symbols.values()
        if symbol.kind == SymbolKind.FILE
    }
    import_targets: dict[str, dict[str, str]] = {}
    for item in pending:
        target_file = _resolve_module_file(item.module_path, file_ids)
        if target_file is None:
            continue
        index.add_relation(
            Relation(
                source_id=item.file_id,
                kind=RelationKind.IMPORTS,
                target_id=target_file,
                evidence=RelationEvidence(
                    file=item.file,
                    expression=item.module_path,
                    resolution=CallResolution.EXACT.value,
                    confidence=RESOLUTION_CONFIDENCE[CallResolution.EXACT],
                ),
            )
        )
        for name in item.names:
            local = name.rsplit(".", 1)[-1]
            target = _resolve_name(index, local, preferred_file=target_file)
            if target:
                import_targets.setdefault(item.file, {}).setdefault(local, target)
                index.add_relation(
                    Relation(
                        source_id=item.file_id,
                        kind=RelationKind.IMPORTS,
                        target_id=target,
                        evidence=RelationEvidence(
                            file=item.file,
                            expression=f"{item.module_path}.{local}",
                            resolution=CallResolution.IMPORTED.value,
                            confidence=RESOLUTION_CONFIDENCE[CallResolution.IMPORTED],
                        ),
                    )
                )
    return import_targets


def _resolve_module_file(module_path: str, file_ids: dict[str, str]) -> str | None:
    slashed = (module_path or "").replace(".", "/").replace("\\", "/").strip("/")
    bases = [slashed]
    raw = (module_path or "").replace("\\", "/").strip("/")
    if raw and raw not in bases:
        bases.append(raw)
    for base in bases:
        candidates = (
            f"{base}.py",
            f"{base}/__init__.py",
            f"{base}.ts",
            f"{base}.js",
            f"{base}.go",
            f"{base}.java",
            f"{base}.rs",
        )
        for candidate in candidates:
            if candidate in file_ids:
                return candidate
        suffix = f"/{base}.py"
        matches = [path for path in file_ids if path.endswith(suffix) or path == f"{base}.py"]
        if len(matches) == 1:
            return matches[0]
    return None


def _resolve_name(index: CodeGraphIndex, name: str, *, preferred_file: str) -> str | None:
    ids = index.by_name.get(name.lower(), [])
    if not ids:
        return None
    callable_kinds = {SymbolKind.FUNCTION, SymbolKind.METHOD, SymbolKind.CLASS}
    filtered = [
        sid
        for sid in ids
        if index.symbols[sid].kind in callable_kinds or index.symbols[sid].kind == SymbolKind.FILE
    ]
    pool = filtered or ids
    same_file = [sid for sid in pool if index.symbols[sid].file == preferred_file]
    if len(same_file) == 1:
        return same_file[0]
    if len(same_file) > 1:
        # Prefer the exact qualified suffix inside the same file.
        exact = [sid for sid in same_file if index.symbols[sid].name == name]
        if len(exact) == 1:
            return exact[0]
        return None
    unique_files = {index.symbols[sid].file for sid in pool}
    if len(pool) == 1 or len(unique_files) == 1:
        return pool[0]
    return None


def _build_lexical_index(
    index: CodeGraphIndex,
    sources: dict[str, str],
    root: Path,
    config: CodeGraphConfig,
):
    builder = LexicalIndexBuilder()
    if config.index_definition_bodies:
        for symbol in index.symbols.values():
            for document, tokens in definition_documents(symbol, sources.get(symbol.file, "")):
                builder.add(document, tokens)
    if config.index_text_files:
        for rel, text in _iter_text_documents(root, config):
            for document, tokens in text_documents(rel, text, config):
                builder.add(document, tokens)
    return builder.freeze()


def definition_documents(symbol, source: str) -> list[tuple[LexicalDocument, list[str]]]:
    """Lexical documents contributed by one symbol. Empty for non-searchable kinds."""
    if symbol.kind not in SEARCHABLE_SYMBOL_KINDS:
        return []
    body = _line_slice(source, symbol.start_line, symbol.end_line)
    parts: list[str] = []
    for part in (
        symbol.qualified_name,
        symbol.name,
        symbol.kind.value,
        symbol.file,
        symbol.signature,
        body,
    ):
        if part:
            parts.append(part)
    text = " ".join(parts)
    tokens = tokenize(text)
    if not tokens:
        return []
    return [
        (
            LexicalDocument(
                doc_id=definition_doc_id(symbol.symbol_id),
                corpus=CORPUS_DEFINITION,
                file=symbol.file,
                start_line=symbol.start_line,
                end_line=symbol.end_line,
                symbol_id=symbol.symbol_id,
                name=symbol.name,
                kind=symbol.kind.value,
                qualified_name=symbol.qualified_name or symbol.name,
            ),
            tokens,
        )
    ]


def text_documents(
    rel: str,
    text: str,
    config: CodeGraphConfig,
) -> list[tuple[LexicalDocument, list[str]]]:
    """Chunked lexical documents for one non-code text file."""
    documents: list[tuple[LexicalDocument, list[str]]] = []
    for start_line, end_line, chunk, idx in _chunk_text(
        text, config.text_chunk_chars, config.text_chunk_overlap
    ):
        tokens = tokenize(chunk)
        if not tokens:
            continue
        documents.append(
            (
                LexicalDocument(
                    doc_id=f"txt:{rel}:{start_line}:{end_line}:{idx}",
                    corpus=CORPUS_TEXT,
                    file=rel,
                    start_line=start_line,
                    end_line=end_line,
                ),
                tokens,
            )
        )
    return documents


def _line_slice(source: str, start_line: int, end_line: int) -> str:
    if not source:
        return ""
    lines = source.splitlines()
    start = max(1, int(start_line or 1))
    end = min(len(lines), max(start, int(end_line or start)))
    start_idx = start - 1
    return "\n".join(lines[start_idx:end])


def _chunk_text(text: str, size: int, overlap: int) -> list[tuple[int, int, str, int]]:
    if not text or size <= 0:
        return []
    chunks: list[tuple[int, int, str, int]] = []
    start = 0
    n = len(text)
    idx = 0
    step = max(1, size - max(0, overlap))
    while start < n:
        end = min(n, start + size)
        piece = text[start:end]
        start_line = text.count("\n", 0, start) + 1
        end_line = start_line + piece.count("\n")
        chunks.append((start_line, end_line, piece, idx))
        idx += 1
        if end >= n:
            break
        start += step
    return chunks


def _iter_text_documents(root: Path, config: CodeGraphConfig) -> list[tuple[str, str]]:
    skip_dirs = set(config.exclude_dirs)
    gitignore = _load_gitignore(root)
    extensions = {ext.lower() for ext in config.text_file_extensions}
    collected: list[tuple[str, str]] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=config.follow_symlinks):
        dirnames[:] = [
            name
            for name in dirnames
            if name not in skip_dirs and not name.endswith(".egg-info")
        ]
        current = Path(dirpath)
        for name in filenames:
            path = current / name
            suffix = path.suffix.lower()
            if suffix not in extensions:
                continue
            if language_from_path(path) is not None:
                continue
            rel = path.relative_to(root).as_posix()
            if any(fnmatch.fnmatch(name, glob) for glob in config.exclude_globs):
                continue
            if gitignore and _is_ignored(rel, gitignore):
                continue
            try:
                raw = path.read_bytes()
            except OSError:
                continue
            if len(raw) > config.max_file_bytes:
                continue
            collected.append((rel, raw.decode("utf-8", errors="replace")))
    collected.sort(key=lambda item: item[0])
    return collected
