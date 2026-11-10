"""Structured capability discovery over Symphony's live directories."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from itertools import zip_longest
from pathlib import PurePosixPath
from typing import Any, Iterable, Sequence

from openjiuwen.core.foundation.tool import LocalFunction, Tool, ToolCard, ToolExposure
from openjiuwen.symphony.retrieval.search.runtime.lexical import LexicalDocument, LexicalIndex

from .models import SkillRecord, sanitize_model_text
from .skillfs import DirectoryEntry, SkillDirectoryView, SkillFS
from .toolkit import CAPABILITY_SEARCH_TOOL_NAME, IncrementalNoticeSession, SkillDCICommandResult

_MIN_OUTPUT_CHARS = 512
_MAX_OUTPUT_CHARS = 48_000
_SKILL_DESCRIPTION_CHARS = 180
_MAX_CURSORS = 32
_CURSOR_FOOTER_CHARS = 96
_CAPABILITY_TYPES = ("skill", "expert", "connector", "mcp")
_OPERATIONS = ("list", "search")
_MAX_TOP_K = 50
_TOOL_ID_DOMAIN = b"openjiuwen.skill-index-tool.v1\0"


@dataclass(frozen=True)
class _Row:
    text: str
    worker_id: str = ""


@dataclass(frozen=True)
class _CursorState:
    operation: str
    category: str | None
    queries: tuple[str, ...]
    offset: int
    page_size: int
    fingerprint: str


def _tool_card(tool_id: str) -> ToolCard:
    return ToolCard(
        id=tool_id,
        name=CAPABILITY_SEARCH_TOOL_NAME,
        description=(
            "Read-only search for capabilities available in this session. Select type=skill, expert, "
            "connector, or mcp. For Skills, list categories or search names, descriptions and SKILL.md, "
            "and copy [next] to continue. Other types accept one search query. Use Skill IDs with skill_tool, "
            "expert references with spawn_expert, and tool_search to inspect deferred tools."
        ),
        input_params={
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": list(_CAPABILITY_TYPES), "default": "skill"},
                "top_k": {"type": "integer", "minimum": 1, "maximum": _MAX_TOP_K},
                "operation": {
                    "type": "string",
                    "enum": list(_OPERATIONS),
                    "default": "search",
                    "description": (
                        "list: direct subcategories and Skills. search: names, aliases, descriptions "
                        "and SKILL.md including descendants; not other package files."
                    ),
                },
                "category": {
                    "type": "string",
                    "minLength": 1,
                    "description": (
                        "Scope: returned category name or A > B chain, never a disk path. "
                        "Omit for root/global on each call."
                    ),
                },
                "query": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1, "maxLength": 512},
                    "minItems": 1,
                    "maxItems": 8,
                    "description": (
                        'Search only. Array even for one query: ["PDF OCR"]. '
                        "Keep task-specific terms; use the language of Skill descriptions (often English). "
                        "Independent queries, interleaved and deduplicated. "
                        "Terms need not all match; no Skill matches yields category hints."
                    ),
                },
                "cursor": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 128,
                    "description": "Copy [next] unchanged. To change query/category, omit cursor.",
                },
            },
            "required": [],
            "additionalProperties": False,
        },
        exposure=ToolExposure.DIRECT,
        parallel_safe=False,
        stateless=False,
    )


class _DirectoryFunction(LocalFunction):
    """Normalize search strings and enforce the model-facing argument schema."""

    async def invoke(self, inputs: dict[str, Any], **kwargs: Any) -> Any:
        if isinstance(inputs, dict):
            unexpected = inputs.keys() - self.card.input_params["properties"].keys()
            if unexpected:
                raise ValueError(f"Unknown capability_search arguments: {', '.join(sorted(unexpected))}")
            inputs = dict(inputs)
            category = inputs.get("category")
            if isinstance(category, str) and not category.strip():
                inputs.pop("category")
            query = inputs.get("query")
            operation = inputs.get("operation", "search")
            if operation == "search" and isinstance(query, str):
                inputs["query"] = [query]
            elif operation == "list" and query == []:
                inputs.pop("query")
        return await super().invoke(inputs, **kwargs)


class InstalledSkillsDirectoryToolkit:
    """Expose direct typed operations over one live Symphony directory view."""

    def __init__(
        self,
        environment: SkillFS,
        *,
        session_scope: str,
        incremental_notice_max_chars: int = 4_000,
        search_catalog: Callable[[str, str, int], Awaitable[dict[str, Any]]] | None = None,
    ) -> None:
        if not isinstance(environment, SkillFS):
            raise TypeError("environment must be a SkillFS")
        self._environment = environment
        self._default_max_output_chars = environment.settings.output.max_chars
        if not _MIN_OUTPUT_CHARS <= self._default_max_output_chars <= _MAX_OUTPUT_CHARS:
            raise ValueError("settings.max_output_chars must be between 512 and 48000")
        scope = str(session_scope or "").strip()
        if not scope:
            raise ValueError("session_scope must be non-empty")
        digest = hashlib.sha256(_TOOL_ID_DOMAIN + scope.encode()).hexdigest()
        self._tool_id = f"{CAPABILITY_SEARCH_TOOL_NAME}__{digest}"
        self._search_catalog = search_catalog
        self._notice = IncrementalNoticeSession(
            scope,
            environment.selection_cards(refresh=False),
            max_chars=incremental_notice_max_chars,
        )
        self._observed_skill_ids: set[str] = set()
        self._cursors: dict[str, _CursorState] = {}
        self._lock = asyncio.Lock()
        self._closed = False

    @property
    def environment(self) -> SkillFS:
        return self._environment

    @property
    def tool_id(self) -> str:
        return self._tool_id

    async def capability_search(
        self,
        query: str | list[str] | None = None,
        type: str = "skill",
        operation: str = "search",
        category: str | None = None,
        cursor: str | None = None,
        top_k: int | None = None,
    ) -> SkillDCICommandResult | dict[str, Any]:
        """Select the Skill directory or one of the host's live capability catalogs."""
        kind = _enum(type, _CAPABILITY_TYPES, "type")
        operation = _enum(operation, _OPERATIONS, "operation")
        if top_k is not None and (
            isinstance(top_k, bool) or not isinstance(top_k, int) or not 1 <= top_k <= _MAX_TOP_K
        ):
            raise ValueError(f"top_k must be an integer from 1 to {_MAX_TOP_K}")
        async with self._lock:
            if self._closed:
                raise RuntimeError("capability_search toolkit is closed")
            if kind == "skill":
                if cursor is not None:
                    if top_k is not None:
                        raise ValueError("cursor cannot be combined with top_k")
                    return await asyncio.to_thread(self._continue, operation, cursor, category, query)
                if operation == "list" and query is not None:
                    raise ValueError("query is valid only for search")
                queries = _queries(query) if operation == "search" else ()
                return await asyncio.to_thread(self._execute, operation, category, queries, top_k)
            if operation != "search" or category is not None or cursor is not None:
                raise ValueError("list, category, and cursor apply only to type=skill")
            terms = _queries(query)
            if len(terms) != 1:
                raise ValueError("Search one query at a time for expert, connector, or mcp")
            if self._search_catalog is None:
                return {"matches": []}
            return await self._search_catalog(kind, terms[0], top_k or 10)

    def get_tools(self) -> list[Tool]:
        return [_DirectoryFunction(card=_tool_card(self._tool_id), func=self.capability_search)]

    async def aclose(self) -> None:
        async with self._lock:
            self._closed = True
            self._cursors.clear()

    def close(self) -> None:
        self._closed = True
        self._cursors.clear()

    def _continue(
        self,
        operation: str,
        cursor: str,
        category: str | None,
        query: str | list[str] | None,
    ) -> SkillDCICommandResult:
        token = _nonempty(cursor, "cursor")
        if len(token) > 128:
            raise ValueError("cursor must not exceed 128 characters")
        state = self._cursors.get(token)
        if state is None:
            raise ValueError("Unknown or expired Skill catalog cursor")
        if operation != state.operation:
            raise ValueError(f"cursor belongs to operation={state.operation}")
        if category is not None and category != state.category:
            raise ValueError("cursor category differs from the original request; omit cursor to change category")
        if query is not None and _queries(query) != state.queries:
            raise ValueError("cursor query differs from the original request; omit cursor to start a new search")
        self._cursors.pop(token)
        return self._execute(
            operation, state.category, state.queries, state.page_size,
            offset=state.offset, expected_fingerprint=state.fingerprint,
        )

    def _execute(
        self,
        operation: str,
        category: str | None,
        queries: tuple[str, ...],
        limit: int | None,
        *,
        offset: int = 0,
        expected_fingerprint: str | None = None,
    ) -> SkillDCICommandResult:
        view = self._environment.directory
        artifact = self._environment.artifact
        if expected_fingerprint is not None and artifact.fingerprint != expected_fingerprint:
            raise ValueError("Skill index changed; start a new list or search request")
        path = _category_path(view, category)
        settings = self._environment.settings
        maximum = settings.output.max_results
        if operation == "list":
            all_rows = tuple(_list_row(entry, view) for entry in view.children(path))
            maximum = min(maximum, settings.output.max_list_entries)
        else:
            all_rows = self._search(view, path, queries)
        page_size = min(limit or maximum, maximum)
        rows = all_rows[offset:offset + page_size]
        previously_shown = operation == "search" and all(
            not row.worker_id or row.worker_id in self._observed_skill_ids for row in rows
        )
        budget = self._default_max_output_chars
        fit_budget = budget - _CURSOR_FOOTER_CHARS if rows else budget
        content, observed, shortened, shown_count = _fit_rows(
            rows, operation=operation, previously_shown=previously_shown, budget=fit_budget,
        )
        if not rows:
            content = _append_note(content, _empty_message(operation, category), budget)
        next_cursor = None
        if offset + shown_count < len(all_rows):
            next_cursor = self._save_cursor(_CursorState(
                operation=operation, category=category, queries=queries, offset=offset + shown_count,
                page_size=page_size, fingerprint=artifact.fingerprint,
            ))
            footer = "[next] " + json.dumps({"operation": operation, "cursor": next_cursor}, separators=(",", ":"))
            content = _append_note(content, footer, budget)
        elif shortened:
            content = _append_note(content, "[truncated] Candidate details shortened.", budget)
        cards = {
            item.worker_id: {"name": item.worker_id, "description": item.description or item.name}
            for item in artifact.items
        }
        content = self._notice.append(content, cards, output_budget=budget)
        self._observed_skill_ids.update(observed)
        diagnostics = {
            "operation": operation,
            "output": content,
            "observed_skill_ids": observed,
            "error": False,
            "ok": True,
            "truncated": shortened,
            "truncation_reason": "max_output_chars" if shortened else None,
            "total_count": len(all_rows),
            "shown_count": shown_count,
            "result_complete": next_cursor is None and not shortened,
            "next_cursor": next_cursor,
            "effective_max_output_chars": budget,
            "skillfs_layout": artifact.layout,
            "candidate_count": len(artifact.items),
            "estimated_candidate_tokens": _candidate_tokens(artifact.items),
            "candidate_budget_tokens": settings.prompt.candidate_budget_tokens,
            "index_state": artifact.index_state,
            "runtime": "Symphony.CapabilitySearch",
        }
        return SkillDCICommandResult(content, detailed_output=diagnostics, model_content=content)

    def _save_cursor(self, state: _CursorState) -> str:
        token = secrets.token_urlsafe(12)
        while token in self._cursors:
            token = secrets.token_urlsafe(12)
        self._cursors[token] = state
        while len(self._cursors) > _MAX_CURSORS:
            self._cursors.pop(next(iter(self._cursors)))
        return token

    def _search(self, view: SkillDirectoryView, path: str, queries: tuple[str, ...]) -> tuple[_Row, ...]:
        records = view.scoped_records((path,))
        groups: list[tuple[SkillRecord | DirectoryEntry, ...]] = []
        queries_by_id: dict[str, list[str]] = {}
        for query in queries:
            identifiers = self._environment.search_content(
                records, query, case_insensitive=True, fixed_strings=False, term_mode=True,
            )
            matches = tuple(view.record_by_id[identifier] for identifier in identifiers)
            group = matches or self._search_categories(view, path, query)
            groups.append(group)
            for item in group:
                queries_by_id.setdefault(_search_identity(item), []).append(query)

        rows: dict[str, _Row] = {}
        for rank in zip_longest(*groups):
            for item in rank:
                if item is None:
                    continue
                identity = _search_identity(item)
                if identity in rows:
                    continue
                if isinstance(item, DirectoryEntry):
                    rows[identity] = _category_row(item, view, description_limit=300)
                else:
                    snippet = self._environment.content_match_snippet(item, tuple(queries_by_id[identity]))
                    rows[identity] = _search_row(item, view, _safe_match_snippet(snippet))
        return tuple(rows.values())

    @staticmethod
    def _search_categories(view: SkillDirectoryView, path: str, query: str) -> tuple[DirectoryEntry, ...]:
        entries = {
            entry.path: entry
            for entry in view.tree_entries((path,), directories_only=True)
            if entry.path != "/"
        }
        index = LexicalIndex(
            tuple(
                LexicalDocument(path, entry.label, _directory_description(entry.description, 300))
                for path, entry in entries.items()
            )
        )
        return tuple(entries[hit.key] for hit in index.search_terms(query))


def _list_row(entry: DirectoryEntry, directory: SkillDirectoryView) -> _Row:
    if entry.kind == "dir":
        return _category_row(entry, directory, description_limit=240)
    record = directory.record_by_id[entry.worker_id]
    category = _skill_category(directory, entry.worker_id)
    detail = f"  desc: {_compact(entry.description, _SKILL_DESCRIPTION_CHARS)}" if entry.description else ""
    return _Row(
        f"- [skill] {entry.worker_id}  category: {category}  path: {_skill_path(record)}{detail}",
        entry.worker_id,
    )


def _search_row(record: SkillRecord, directory: SkillDirectoryView, snippet: str) -> _Row:
    description = _compact(record.description or record.name, _SKILL_DESCRIPTION_CHARS)
    fields = [
        f"- [skill] {record.worker_id}",
        f"category: {_skill_category(directory, record.worker_id)}",
    ]
    if snippet:
        fields.append(f"match: {snippet}")
    fields.extend((f"desc: {description}", f"path: {_skill_path(record)}"))
    return _Row("  ".join(fields), record.worker_id)


def _category_row(entry: DirectoryEntry, directory: SkillDirectoryView, *, description_limit: int) -> _Row:
    description = _directory_description(entry.description, description_limit)
    count = directory.skill_count(entry.path)
    detail = f"desc: Contains {count} skill{'s' if count != 1 else ''}. {description}".rstrip()
    return _Row(f"- [category] {_category_from_path(directory, entry.path)}  {detail}")


def _skill_category(directory: SkillDirectoryView, worker_id: str) -> str:
    path = str(PurePosixPath(directory.record_path_by_id[worker_id]).parent)
    return _category_from_path(directory, path)


def _category_from_path(directory: SkillDirectoryView, path: str) -> str:
    labels: list[str] = []
    while path not in {"", ".", "/"}:
        node = directory.node_by_path.get(path)
        label = node.label if node is not None else PurePosixPath(path).name
        labels.append(_inline_text(label))
        path = str(PurePosixPath(path).parent)
    return " > ".join(reversed(labels)) or "ROOT"


def _skill_path(record: SkillRecord) -> str:
    return _inline_text(record.skill_file)


def _inline_text(value: Any) -> str:
    """Render one field without allowing it to create another Markdown row."""

    return json.dumps(sanitize_model_text(value), ensure_ascii=False)[1:-1]


def _safe_match_snippet(value: str) -> str:
    label, separator, evidence = value.partition(": ")
    if separator and label in {"name", "alias", "description", "body", "category"}:
        return f"{label}: {sanitize_model_text(evidence)}"
    return sanitize_model_text(value)


def _directory_description(value: str, limit: int) -> str:
    """Keep routing evidence while dropping index-construction statistics."""

    text = sanitize_model_text(value)
    select_when = ""
    semantic_lines: list[str] = []
    for paragraph in re.split(r"\n\s*\n", text):
        current: list[str] = []
        for raw_line in paragraph.splitlines():
            line = raw_line.strip()
            lowered = line.casefold()
            if lowered.startswith("select when:"):
                if not select_when:
                    select_when = line.split(":", 1)[1].strip()
                continue
            if lowered.startswith(("covers ", "representative ", "don't select when:")):
                continue
            if line:
                current.append(line)
        if current and not semantic_lines:
            semantic_lines = current

    semantic = _compact(" ".join(semantic_lines), min(limit, 180)) if semantic_lines else ""
    routing = f"Select when: {_compact(select_when, 96)}" if select_when else ""
    return _compact(" ".join(part for part in (semantic, routing) if part), limit)


def _search_identity(item: SkillRecord | DirectoryEntry) -> str:
    if isinstance(item, SkillRecord):
        return f"skill\0{item.worker_id}"
    return f"{item.kind}\0{item.path}"


def _fit_rows(
    rows: Sequence[_Row],
    *,
    operation: str,
    previously_shown: bool,
    budget: int,
) -> tuple[str, list[str], bool, int]:
    heading = _summary_line(rows, operation, previously_shown)
    selected: list[_Row] = []
    size = len(heading) + 1
    for row in rows:
        size += len(row.text) + 1
        if size > budget:
            break
        selected.append(row)
    body = "\n".join(row.text for row in selected)
    heading = _summary_line(selected, operation, previously_shown)
    while selected and len(heading) + 2 + len(body) > budget:
        selected.pop()
        body = "\n".join(row.text for row in selected)
        heading = _summary_line(selected, operation, previously_shown)
    shortened = len(selected) < len(rows)
    if rows and not selected:
        row = rows[0]
        heading = _summary_line(rows[:1], operation, previously_shown)
        body = _compact(row.text, budget - len(heading) - 2)
        selected.append(row)
    output = f"{heading}\n\n{body}" if body else heading
    observed = list(dict.fromkeys(row.worker_id for row in selected if row.worker_id))
    return output, observed, shortened, len(selected)


def _summary_line(rows: Sequence[_Row], operation: str, previously_shown: bool) -> str:
    has_skills = any(row.worker_id for row in rows)
    has_categories = any(not row.worker_id for row in rows)
    if has_skills and not has_categories:
        return "## Skills — all names previously shown" if previously_shown else "## Skills"
    if has_categories and not has_skills:
        return "## Category hints — matching groups, not Skills" if operation == "search" else "## Categories"
    if has_categories:
        return "## Skills and category hints" if operation == "search" else "## Entries"
    return "## Skills" if operation == "search" else "## Results"


def _queries(query: str | list[str] | None) -> tuple[str, ...]:
    if isinstance(query, list):
        if not 1 <= len(query) <= 8:
            raise ValueError("query must contain 1-8 strings")
        return tuple(dict.fromkeys(_bounded_query(item, "query item") for item in query))
    return (_bounded_query(query, "query"),)


def _category_path(directory: SkillDirectoryView, category: str | None) -> str:
    if category is None:
        return "/"
    value = _nonempty(category, "category")
    if value in {"/", "ROOT"}:
        return "/"
    normalized = " > ".join(part.strip() for part in value.split(">") if part.strip())
    matches = [
        path
        for path in directory.node_by_path
        if path != "/" and _category_from_path(directory, path).casefold() == normalized.casefold()
    ]
    if not matches and ">" not in normalized:
        matches = [
            path
            for path, node in directory.node_by_path.items()
            if path != "/" and str(node.label).strip().casefold() == normalized.casefold()
        ]
    if not matches:
        raise ValueError(f"Unknown Skill category: {value}")
    if len(matches) > 1:
        raise ValueError(f"Ambiguous Skill category; use its full category chain: {value}")
    return matches[0]


def _bounded_query(value: Any, name: str) -> str:
    query = _nonempty(value, name)
    if "\0" in query:
        raise ValueError(f"{name} must not contain a null byte")
    if len(query) > 512:
        raise ValueError(f"{name} must not exceed 512 characters")
    return query


def _enum(value: Any, allowed: Iterable[str], name: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise ValueError(f"{name} must be one of: {', '.join(allowed)}")
    return value


def _nonempty(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _compact(value: str, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else f"{text[: limit - 3].rstrip()}..."


def _append_note(content: str, note: str, budget: int) -> str:
    remaining = budget - len(content) - 2
    if remaining <= 3:
        return content
    rendered = note if len(note) <= remaining else _compact(note, remaining)
    return f"{content}\n\n{rendered}"


def _candidate_tokens(records: Sequence[SkillRecord]) -> int:
    rendered = "\n".join(f"- {record.worker_id}: {' '.join(record.description.split())}" for record in records)
    return (len(rendered) + 3) // 4


def _empty_message(operation: str, category: str | None = None) -> str:
    if operation != "search":
        return "No entries."
    if category:
        return f"No matching Skills found in `{sanitize_model_text(category)}`."
    return "No matching installed Skills found."


__all__ = ["InstalledSkillsDirectoryToolkit"]
