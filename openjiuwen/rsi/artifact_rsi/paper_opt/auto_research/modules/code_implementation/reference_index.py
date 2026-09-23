"""Bounded, in-process index of OpenJiuwen source, examples, and docs.

Search never imports the indexed modules. Python files are parsed with ``ast``.
Results are labeled so a public export outranks an implementation detail, then
an example, then docs. The default scope is source.
"""

from __future__ import annotations

import ast
import fnmatch
import os
import re
from dataclasses import dataclass
from pathlib import Path

_SEARCH_SUFFIXES = {".py", ".md", ".json"}
_MAX_FILE_BYTES = 200_000
_MAX_SNIPPET_CHARS = 240
_MAX_TOTAL_CHARS = 4_000
_MAX_RESULTS = 8
_MAX_QUERY_CHARS = 200
_MAX_REGEX_CHARS = 80
_LABEL_RANK = {
    "public-export": 0,
    "implementation-detail": 1,
    "example": 2,
    "docs": 3,
}
_DEFAULT_SCOPES = ("source",)
_SKIP_DIR_NAMES = {"__pycache__", ".git", ".pytest_cache", "node_modules", "tests"}
_SOURCE_SKIP_TOP = {"rsi"}


class ReferencePathError(ValueError):
    """Raised when a virtual reference path escapes its root."""


@dataclass(frozen=True)
class ReferenceRoots:
    """Filesystem roots behind the virtual namespaces."""

    examples: Path
    source: Path
    docs: Path

    def namespaces(self) -> tuple[tuple[str, Path], ...]:
        return (
            ("examples", self.examples),
            ("source", self.source),
            ("docs", self.docs),
        )


@dataclass(frozen=True)
class SearchHit:
    """One ranked search hit. ``virtual_path`` is what the read tool accepts."""

    virtual_path: str
    start_line: int
    end_line: int
    label: str
    symbol: str
    snippet: str


@dataclass(frozen=True)
class _Entry:
    virtual_path: str
    start_line: int
    end_line: int
    label: str
    symbol: str
    haystack: str
    snippet: str


def repo_root() -> Path:
    """Repository root inferred from this file's location."""
    return Path(__file__).resolve().parents[7]


def default_roots() -> ReferenceRoots:
    """Roots for the checked-out tree."""
    root = repo_root()
    return ReferenceRoots(
        examples=root / "examples",
        source=root / "openjiuwen",
        docs=root / "docs",
    )


def clear_index_cache() -> None:
    """Drop the process-local index. Tests use this after editing a fixture tree."""
    _CACHE.clear()


_CACHE: dict[tuple, tuple[_Entry, ...]] = {}


def resolve_reference_path(raw: str, roots: ReferenceRoots) -> Path:
    """Map a virtual path onto one namespace root and reject escapes.

    ``source/openjiuwen/...`` is rooted at the ``openjiuwen`` package.
    ``docs/...`` and a bare docs-relative path both land in ``roots.docs``.
    """
    text = (raw or "").strip().replace("\\", "/")
    if not text or "\x00" in text:
        raise ReferencePathError("Access denied: empty reference path")
    if text.startswith("/") or (len(text) >= 2 and text[1] == ":"):
        raise ReferencePathError("Access denied: absolute reference paths are not accepted")

    namespace, relative = _split_virtual(text)
    root = dict(roots.namespaces())[namespace]
    return _contain(root, relative, namespace)


def search_reference(
    query: str,
    roots: ReferenceRoots | None = None,
    *,
    scopes: tuple[str, ...] | list[str] | None = None,
    path_glob: str = "",
    max_results: int = _MAX_RESULTS,
    use_regex: bool = False,
    labels: tuple[str, ...] | list[str] | None = None,
) -> list[SearchHit]:
    """Literal or token search. Omitted scopes search source only.

    Regex is accepted only as an explicit bounded option.
    """
    cleaned = (query or "").strip()
    if not cleaned:
        return []
    if len(cleaned) > _MAX_QUERY_CHARS:
        cleaned = cleaned[:_MAX_QUERY_CHARS]
    active = roots or default_roots()
    requested = tuple(str(item).strip() for item in (scopes or ()) if str(item).strip())
    allowed_scopes = set(requested or _DEFAULT_SCOPES)
    allowed_labels = {item for item in (labels or ()) if item}
    pattern = _compile_query(cleaned, use_regex=use_regex)
    if pattern is None:
        return []
    limit = max(1, min(int(max_results or _MAX_RESULTS), 20))
    glob = (path_glob or "").replace("\\", "/").strip()
    ranked: list[tuple[int, int, _Entry]] = []
    for hit in _entries(active):
        namespace = hit.virtual_path.split("/", 1)[0]
        if namespace not in allowed_scopes:
            continue
        if allowed_labels and hit.label not in allowed_labels:
            continue
        if glob and not fnmatch.fnmatch(hit.virtual_path, glob):
            continue
        if pattern.search(hit.haystack) is None:
            continue
        ranked.append((_LABEL_RANK.get(hit.label, 9), -_match_weight(cleaned, hit.haystack), hit))
    ranked.sort(key=lambda item: (item[0], item[1], item[2].virtual_path, item[2].start_line))
    selected: list[SearchHit] = []
    used = 0
    for _, _, hit in ranked:
        if len(selected) >= limit or used >= _MAX_TOTAL_CHARS:
            break
        snippet = _snippet_around(hit.haystack, cleaned, fallback=hit.snippet)
        selected.append(
            SearchHit(
                virtual_path=hit.virtual_path,
                start_line=hit.start_line,
                end_line=hit.end_line,
                label=hit.label,
                symbol=hit.symbol,
                snippet=snippet,
            )
        )
        used += len(snippet)
    return selected


def _split_virtual(text: str) -> tuple[str, str]:
    lowered = text.lower().lstrip("/")
    for prefix in ("assets/openjiuwen/docs/", "assets/openjiuwen/", "docs/"):
        if lowered.startswith(prefix):
            return "docs", text[len(prefix):]
    if lowered in ("assets/openjiuwen", "assets/openjiuwen/docs", "docs"):
        return "docs", ""
    if lowered == "recipes" or lowered.startswith("recipes/"):
        raise ReferencePathError("Access denied: recipes are not a reference namespace")
    if lowered == "examples" or lowered.startswith("examples/"):
        return "examples", text.split("/", 1)[1] if "/" in text else ""
    if lowered == "source" or lowered.startswith("source/"):
        relative = text.split("/", 1)[1] if "/" in text else ""
        if relative.lower().startswith("openjiuwen/"):
            relative = relative.split("/", 1)[1]
        elif relative.lower() == "openjiuwen":
            relative = ""
        return "source", relative
    return "docs", text


def _contain(root: Path, relative: str, namespace: str) -> Path:
    if not root.exists():
        raise ReferencePathError(f"Access denied: namespace {namespace} is not available")
    parts: list[str] = []
    for part in Path(relative).parts if relative else ():
        if part in (".", ""):
            continue
        if part == "..":
            raise ReferencePathError("Access denied: path traversal is not allowed")
        parts.append(part)
    if namespace == "source" and parts and parts[0] in _SOURCE_SKIP_TOP:
        raise ReferencePathError("Access denied: that source tree is not in the reference index")
    if any(part in {".git", "__pycache__"} or part.endswith(".pyc") for part in parts):
        raise ReferencePathError("Access denied: caches and git metadata are not reference material")
    root_resolved = root.resolve()
    candidate = root.joinpath(*parts)
    if candidate.is_symlink() and not _is_relative_to(candidate.resolve(), root_resolved):
        raise ReferencePathError("Access denied: symlink escapes the reference root")
    resolved = candidate.resolve()
    if not _is_relative_to(resolved, root_resolved):
        raise ReferencePathError("Access denied: path escapes the reference root")
    return resolved


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _compile_query(query: str, *, use_regex: bool) -> re.Pattern[str] | None:
    if use_regex:
        if len(query) > _MAX_REGEX_CHARS:
            return None
        try:
            return re.compile(query, re.IGNORECASE)
        except re.error:
            return None
    tokens = [re.escape(token) for token in re.findall(r"[A-Za-z0-9_./-]+", query) if len(token) >= 2]
    if not tokens:
        tokens = [re.escape(query)]
    return re.compile("|".join(tokens), re.IGNORECASE)


def _match_weight(query: str, blob: str) -> int:
    weight = 0
    folded = query.lower()
    if folded and folded in blob:
        weight += 5
    for token in re.findall(r"[a-z0-9_./-]+", folded):
        if len(token) >= 2 and token in blob:
            weight += 1
    return weight


def _entries(roots: ReferenceRoots) -> tuple[_Entry, ...]:
    stamp = _stamp(roots)
    cached = _CACHE.get(stamp)
    if cached is not None:
        return cached
    entries: list[_Entry] = []
    for namespace, root in roots.namespaces():
        if not root.is_dir():
            continue
        for path in _iter_files(root, namespace):
            entries.extend(_index_file(path, root, namespace))
    frozen = tuple(entries)
    _CACHE.clear()
    _CACHE[stamp] = frozen
    return frozen


def _stamp(roots: ReferenceRoots) -> tuple:
    rows: list[tuple] = []
    for namespace, root in roots.namespaces():
        if not root.is_dir():
            rows.append((namespace, str(root), 0, 0))
            continue
        for path in _iter_files(root, namespace):
            try:
                stat = path.stat()
            except OSError:
                continue
            rows.append((namespace, str(path), stat.st_mtime_ns, stat.st_size))
    return tuple(rows)


def _iter_files(root: Path, namespace: str):
    for dirpath, dirnames, filenames in os.walk(root):
        current = Path(dirpath)
        try:
            relative = current.relative_to(root)
        except ValueError:
            dirnames[:] = []
            continue
        if current.is_symlink() and not _is_relative_to(current.resolve(), root.resolve()):
            dirnames[:] = []
            continue
        kept: list[str] = []
        for name in dirnames:
            if name in _SKIP_DIR_NAMES or name.startswith("."):
                continue
            if namespace == "source" and relative == Path(".") and name in _SOURCE_SKIP_TOP:
                continue
            child = current / name
            if child.is_symlink() and not _is_relative_to(child.resolve(), root.resolve()):
                continue
            kept.append(name)
        dirnames[:] = kept
        for name in filenames:
            if Path(name).suffix.lower() not in _SEARCH_SUFFIXES:
                continue
            path = current / name
            try:
                if path.stat().st_size > _MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            yield path


def _index_file(path: Path, root: Path, namespace: str) -> list[_Entry]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    virtual = _virtual_path(path, root, namespace)
    label = _file_label(namespace)
    haystack = f"{path.stem}\n{virtual}\n{text}".lower()
    hits = [
        _Entry(
            virtual_path=virtual,
            start_line=1,
            end_line=max(1, len(text.splitlines())),
            label=label,
            symbol=path.stem,
            haystack=haystack,
            snippet=_clip(text),
        )
    ]
    if path.suffix.lower() != ".py" or namespace == "docs":
        return hits
    exported = _exported_names(text)
    symbols = _symbols(text)
    if symbols:
        hits[0] = _Entry(
            virtual_path=virtual,
            start_line=1,
            end_line=hits[0].end_line,
            label=label,
            symbol=path.stem,
            haystack=f"{path.stem}\n{virtual}".lower(),
            snippet=hits[0].snippet,
        )
    for symbol in symbols:
        symbol_label = label
        if namespace == "source":
            public = symbol.name in exported if exported else not symbol.name.startswith("_")
            symbol_label = "public-export" if public else "implementation-detail"
        symbol_text = f"{symbol.name}\n{symbol.snippet}"
        hits.append(
            _Entry(
                virtual_path=virtual,
                start_line=symbol.line,
                end_line=symbol.end_line,
                label=symbol_label,
                symbol=symbol.name,
                haystack=f"{symbol.name}\n{virtual}\n{symbol_text}".lower(),
                snippet=_clip(symbol.snippet),
            )
        )
    return hits


def _virtual_path(path: Path, root: Path, namespace: str) -> str:
    relative = path.relative_to(root).as_posix()
    if namespace == "source":
        return f"source/openjiuwen/{relative}"
    return f"{namespace}/{relative}"


def _file_label(namespace: str) -> str:
    if namespace == "examples":
        return "example"
    if namespace == "docs":
        return "docs"
    return "implementation-detail"


@dataclass(frozen=True)
class _Symbol:
    name: str
    line: int
    end_line: int
    snippet: str


def _exported_names(source: str) -> set[str]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    names: set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        targets = [target for target in node.targets if isinstance(target, ast.Name)]
        if not any(target.id == "__all__" for target in targets):
            continue
        if not isinstance(node.value, (ast.List, ast.Tuple)):
            continue
        for elt in node.value.elts:
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                names.add(elt.value)
    return names


def _symbols(source: str) -> list[_Symbol]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    lines = source.splitlines()
    found: list[_Symbol] = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        end_line = int(getattr(node, "end_lineno", None) or node.lineno)
        signature = _signature(node)
        doc = ast.get_docstring(node) or ""
        body = "\n".join(lines[node.lineno - 1: end_line])
        preview_lines = lines[node.lineno - 1: min(end_line, node.lineno + 8)]
        preview = "\n".join(part for part in (signature, doc, "\n".join(preview_lines)) if part)
        found.append(
            _Symbol(
                name=node.name,
                line=node.lineno,
                end_line=end_line,
                snippet=body or preview,
            )
        )
    return found


def _signature(node: ast.AST) -> str:
    if isinstance(node, ast.ClassDef):
        return f"class {node.name}"
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
        try:
            args = ast.unparse(node.args)
        except (AttributeError, TypeError):
            args = "..."
        return f"{prefix} {node.name}({args})"
    return ""


def _clip(text: str) -> str:
    cleaned = " ".join((text or "").split())
    if len(cleaned) <= _MAX_SNIPPET_CHARS:
        return cleaned
    return cleaned[: _MAX_SNIPPET_CHARS - 1] + "…"


def _snippet_around(haystack: str, query: str, *, fallback: str) -> str:
    folded = haystack
    tokens = [token for token in re.findall(r"[a-z0-9_./-]+", query.lower()) if len(token) >= 2]
    at = -1
    for token in tokens:
        at = folded.find(token)
        if at >= 0:
            break
    if at < 0:
        return _clip(fallback)
    start = max(0, at - 80)
    return _clip(haystack[start: start + _MAX_SNIPPET_CHARS])
