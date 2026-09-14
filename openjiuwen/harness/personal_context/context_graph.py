"""Read the published Context graph from PersonalContext-managed files."""

from __future__ import annotations

import math
import os
import re
from collections import Counter
from collections.abc import Sequence
from pathlib import Path, PurePosixPath
from typing import NoReturn
from urllib.parse import unquote, urlsplit

from openjiuwen.harness.personal_context.source_metadata import read_source_metadata
from openjiuwen.harness.personal_context.status_codes import StatusCode, build_error

_MARKDOWN_LINK = re.compile(r"(?<!!)\[[^\]\r\n]*\]\(([^)\r\n]+)\)")
_SOURCE_ID = re.compile(r"^src_[0-9a-f]{32}$")
_MAX_GRAPH_FILES = 10_000
_MAX_GRAPH_FILE_BYTES = 2 * 1024 * 1024
_MAX_GRAPH_PATH_CHARS = 1_024
_SEARCH_TOKEN = re.compile(r"[a-z0-9_]+|[\u3400-\u4dbf\u4e00-\u9fff]+")
_HEADING = re.compile(r"^(#{1,3})\s+(.+?)\s*$")
_COLLAPSED_WHITESPACE = re.compile(r"\s+")
_FIELD_WEIGHTS = {"title": 4.0, "path": 3.0, "headings": 2.0, "body": 1.0}
_BM25_K1 = 1.2
_BM25_B = 0.75
_MAX_SEARCH_RESULTS = 10
_MAX_SNIPPET_CHARS = 240

_SearchPage = tuple[str, str, str, dict[str, Counter[str]], dict[str, int]]


def _graph_error(message: str, *, cause: BaseException | None = None) -> NoReturn:
    raise build_error(StatusCode.CONTEXT_PROACTIVE_FILE_EXECUTION_ERROR, msg=message, cause=cause) from None


def _check_graph_path(root: Path, path: Path) -> None:
    try:
        relative = path.relative_to(root)
    except ValueError:
        _graph_error("PersonalContext graph path escaped its managed root")
    if len(relative.as_posix()) > _MAX_GRAPH_PATH_CHARS:
        _graph_error("PersonalContext graph path exceeds the safety limit")
    current = path
    while True:
        if current.is_symlink():
            _graph_error("PersonalContext graph path must not traverse a symlink")
        if current == root:
            return
        current = current.parent


def _read_graph_bytes(root: Path, path: Path) -> bytes:
    _check_graph_path(root, path)
    if not path.is_file():
        _graph_error("PersonalContext graph file is missing")
    try:
        size = path.stat().st_size
        if size > _MAX_GRAPH_FILE_BYTES:
            _graph_error("PersonalContext graph file exceeds the safety limit")
        return path.read_bytes()
    except OSError as exc:
        _graph_error("PersonalContext graph file could not be read", cause=exc)


def _read_graph_text(root: Path, path: Path) -> str:
    try:
        return _read_graph_bytes(root, path).decode("utf-8")
    except UnicodeError as exc:
        _graph_error("PersonalContext graph file is not valid UTF-8", cause=exc)


def _graph_target(
    context_root: Path,
    source_root: Path,
    page: Path,
    raw_target: str,
    *,
    page_ids: dict[str, str],
) -> tuple[str, dict[str, object] | None] | None:
    target = raw_target.strip()
    if target.startswith("<"):
        closing = target.find(">")
        if closing < 0:
            return None
        target = target[1:closing].strip()
    else:
        target = target.split(maxsplit=1)[0].strip("\"'")
    if not target:
        return None
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc or not parsed.path:
        return None
    path_value = unquote(parsed.path)
    if Path(path_value).is_absolute() or Path(path_value).suffix.casefold() != ".md":
        return None
    try:
        target_path = Path(os.path.abspath(page.parent / path_value))
    except OSError:
        return None
    try:
        relative = target_path.relative_to(Path(os.path.abspath(context_root))).as_posix()
    except ValueError:
        try:
            source_relative = target_path.relative_to(Path(os.path.abspath(source_root)))
        except ValueError:
            return None
        if len(source_relative.parts) != 1 or _SOURCE_ID.fullmatch(source_relative.stem) is None:
            _graph_error("PersonalContext atomic source metadata path is invalid")
        metadata = read_source_metadata(target_path)
        source_id = str(metadata["source_id"])
        node_id = f"source:{source_id}"
        return node_id, {
            "id": node_id,
            "kind": "source",
            "subkind": "source.0",
            "label": str(metadata["title"]),
            "path": f"{source_id}.md",
            "service_id": str(metadata["service"]),
        }
    page_id = page_ids.get(relative)
    return (page_id, None) if page_id is not None else None


def _read_context_pages(home: Path) -> dict[str, tuple[Path, str]]:
    context_root = home / "workspace" / "context"
    description = context_root / "description.md"
    if not context_root.is_dir() or context_root.is_symlink():
        return {}
    if not description.is_file() or description.is_symlink():
        return {}
    description_text = _read_graph_text(context_root, description)
    if not description_text.strip():
        return {}
    pages: dict[str, tuple[Path, str]] = {}
    file_count = 0
    for path in context_root.rglob("*"):
        _check_graph_path(context_root, path)
        if path.is_file() and path.suffix.casefold() == ".md":
            file_count += 1
            if file_count > _MAX_GRAPH_FILES:
                _graph_error("PersonalContext Context file count exceeds the safety limit")
            relative = path.relative_to(context_root).as_posix()
            text = description_text if path == description else _read_graph_text(context_root, path)
            pages[relative] = (path, text)
    return pages


def build_context_graph(home: Path) -> dict[str, object]:
    """Build the stable Context graph payload without starting PersonalContext."""

    context_root = home / "workspace" / "context"
    source_root = home / "workspace" / "source-meta"
    pages = _read_context_pages(home)
    if not pages:
        return {"context_ready": False, "nodes": [], "edges": []}
    nodes: dict[str, dict[str, object]] = {}
    page_ids: dict[str, str] = {}
    for relative in sorted(pages):
        relative_path = PurePosixPath(relative)
        node_id = f"page:{relative}"
        page_ids[relative] = node_id
        is_directory = relative_path.name == "description.md"
        directory_depth = len(relative_path.parent.parts)
        nodes[node_id] = {
            "id": node_id,
            "kind": "directory" if is_directory else "document",
            "subkind": f"directory.{directory_depth}" if is_directory else "document.0",
            "label": relative_path.name,
            "path": relative,
            "service_id": None,
        }
    edges: set[tuple[str, str, str]] = set()
    for relative, node_id in page_ids.items():
        relative_path = PurePosixPath(relative)
        if relative == "description.md":
            continue
        directory = relative_path.parent
        parent_directory = directory.parent if relative_path.name == "description.md" else directory
        parent_description = (parent_directory / "description.md").as_posix()
        parent_id = page_ids.get(parent_description)
        if parent_id is not None:
            edges.add((parent_id, node_id, "contains"))
    for relative, (page, text) in pages.items():
        kind = "navigates_to" if page.name == "description.md" else "links_to"
        for match in _MARKDOWN_LINK.finditer(text):
            target = _graph_target(
                context_root,
                source_root,
                page,
                match.group(1),
                page_ids=page_ids,
            )
            if target is not None:
                target_id, source_node = target
                if source_node is not None:
                    nodes[target_id] = source_node
                edges.add((page_ids[relative], target_id, kind))
    return {
        "context_ready": True,
        "nodes": sorted(nodes.values(), key=lambda node: (str(node["kind"]), str(node["id"]))),
        "edges": [
            {"source": source, "target": target, "kind": kind}
            for source, target, kind in sorted(edges, key=lambda item: (item[2], item[0], item[1]))
        ],
    }


def _tokenize(text: str) -> tuple[str, ...]:
    tokens: list[str] = []
    for match in _SEARCH_TOKEN.finditer(text.casefold()):
        token = match.group(0)
        if token.isascii():
            tokens.append(token)
            continue
        tokens.extend(token)
        tokens.extend(token[index] + token[index + 1] for index in range(len(token) - 1))
    return tuple(tokens)


def _page_heading_fields(relative: str, text: str) -> tuple[str, str]:
    h1 = ""
    subheadings: list[str] = []
    for line in text.splitlines():
        match = _HEADING.match(line)
        if match is None:
            continue
        heading = match.group(2).rstrip("#").strip()
        if len(match.group(1)) == 1 and not h1:
            h1 = heading
        elif len(match.group(1)) in {2, 3}:
            subheadings.append(heading)
    filename = PurePosixPath(relative).stem
    return h1 or filename, " ".join(subheadings)


def _search_page(relative: str, text: str) -> _SearchPage:
    title, subheadings = _page_heading_fields(relative, text)
    fields = {
        "title": _tokenize(f"{PurePosixPath(relative).stem} {title}"),
        "path": _tokenize(relative.replace("/", " ")),
        "headings": _tokenize(subheadings),
        "body": _tokenize(text),
    }
    return (
        relative,
        title,
        text,
        {name: Counter(tokens) for name, tokens in fields.items()},
        {name: len(tokens) for name, tokens in fields.items()},
    )


def _average_field_lengths(pages: Sequence[_SearchPage]) -> dict[str, float]:
    return {field: max(1.0, sum(page[4][field] for page in pages) / len(pages)) for field in _FIELD_WEIGHTS}


def _rank_bm25f(pages: Sequence[_SearchPage], query_terms: Sequence[str]) -> list[_SearchPage]:
    if not pages or not query_terms:
        return []
    average_lengths = _average_field_lengths(pages)
    document_frequencies = {
        term: sum(1 for candidate in pages if any(candidate[3][field].get(term, 0) > 0 for field in _FIELD_WEIGHTS))
        for term in query_terms
    }
    scored: list[tuple[float, _SearchPage]] = []
    for page in pages:
        score = 0.0
        for term in query_terms:
            document_frequency = document_frequencies[term]
            if document_frequency == 0:
                continue
            weighted_frequency = 0.0
            for field, weight in _FIELD_WEIGHTS.items():
                count = page[3][field].get(term, 0)
                if count == 0:
                    continue
                length_ratio = page[4][field] / average_lengths[field]
                weighted_frequency += weight * count / (1.0 - _BM25_B + _BM25_B * length_ratio)
            if not weighted_frequency:
                continue
            inverse_document_frequency = math.log(
                1.0 + (len(pages) - document_frequency + 0.5) / (document_frequency + 0.5)
            )
            score += inverse_document_frequency * (
                weighted_frequency * (_BM25_K1 + 1.0) / (_BM25_K1 + weighted_frequency)
            )
        if score > 0.0:
            scored.append((score, page))
    scored.sort(key=lambda item: (-item[0], item[1][0].casefold()))
    return [page for _score, page in scored]


def _snippet(text: str, query: str, query_terms: Sequence[str]) -> str:
    collapsed = _COLLAPSED_WHITESPACE.sub(" ", text).strip()
    if len(collapsed) <= _MAX_SNIPPET_CHARS:
        return collapsed
    folded = collapsed.casefold()
    index = folded.find(query.casefold())
    if index < 0:
        positions = [folded.find(term) for term in query_terms]
        index = min((position for position in positions if position >= 0), default=0)
    start = max(0, index - 80)
    end = min(len(collapsed), start + _MAX_SNIPPET_CHARS)
    if end - start < _MAX_SNIPPET_CHARS:
        start = max(0, end - _MAX_SNIPPET_CHARS)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(collapsed) else ""
    return f"{prefix}{collapsed[start:end]}{suffix}"


def read_context_graph_page(home: Path, node_id: str) -> dict[str, object]:
    """Read one published Markdown page selected by its graph node ID."""

    if not isinstance(node_id, str):
        _graph_error("PersonalContext graph page node ID is invalid")
    if node_id.startswith("source:"):
        source_id = node_id.removeprefix("source:")
        if _SOURCE_ID.fullmatch(source_id) is None:
            _graph_error("PersonalContext graph source node ID is invalid")
        source_root = home / "workspace" / "source-meta"
        source_path = source_root / f"{source_id}.md"
        metadata = read_source_metadata(source_path)
        markdown = _read_graph_text(source_root, source_path)
        return {
            "node_id": node_id,
            "title": str(metadata["title"]),
            "path": f"{source_id}.md",
            "markdown": markdown,
        }
    if not node_id.startswith("page:"):
        _graph_error("PersonalContext graph page node ID is invalid")
    relative = node_id.removeprefix("page:")
    if not relative or "\\" in relative:
        _graph_error("PersonalContext graph page node ID is invalid")
    path = PurePosixPath(relative)
    if path.is_absolute() or ".." in path.parts:
        _graph_error("PersonalContext graph page node ID is invalid")
    if path.as_posix() != relative or path.suffix.casefold() != ".md":
        _graph_error("PersonalContext graph page node ID is invalid")

    context_root = home / "workspace" / "context"
    description = context_root / "description.md"
    if not context_root.is_dir() or context_root.is_symlink():
        _graph_error("PersonalContext Context is not ready")
    if not description.is_file() or description.is_symlink():
        _graph_error("PersonalContext Context is not ready")
    if not _read_graph_text(context_root, description).strip():
        _graph_error("PersonalContext Context is not ready")
    markdown = _read_graph_text(context_root, context_root.joinpath(*path.parts))
    title, _headings = _page_heading_fields(relative, markdown)
    return {
        "node_id": node_id,
        "title": title,
        "path": relative,
        "markdown": markdown,
    }


def search_context_graph(home: Path, query: str) -> dict[str, object]:
    """Search published Context Markdown and return graph-compatible page IDs."""

    pages = _read_context_pages(home)
    if not pages:
        return {"results": []}
    query_terms = tuple(dict.fromkeys(_tokenize(query)))
    ranked = _rank_bm25f(
        [_search_page(relative, text) for relative, (_path, text) in pages.items()],
        query_terms,
    )
    return {
        "results": [
            {
                "node_id": f"page:{page[0]}",
                "title": page[1],
                "path": page[0],
                "snippet": _snippet(page[2], query, query_terms),
            }
            for page in ranked[:_MAX_SEARCH_RESULTS]
        ]
    }


__all__ = ["build_context_graph", "read_context_graph_page", "search_context_graph"]
