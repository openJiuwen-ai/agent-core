"""Structured capability discovery over Symphony's live directories."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import astuple, dataclass, replace
from itertools import islice
from pathlib import PurePosixPath
from types import MethodType
from typing import TYPE_CHECKING, Any, Iterable, Sequence, get_args

from openjiuwen.core.foundation.tool import LocalFunction, Tool, ToolCard, ToolExposure
from openjiuwen.symphony.config import CapabilityType
from openjiuwen.symphony.retrieval.search.runtime.lexical import LexicalDocument, LexicalIndex

from .capability_search import (
    CapabilityCandidate, capability_row, fingerprint_text, interleave_rankings, rank_capabilities, rank_queries,
)
from .models import SkillRecord, sanitize_model_text
from .skillfs import DirectoryEntry, SkillDirectoryView, SkillFS, SkillFSArtifact, SkillIndexSnapshot, _build_live_tree
from .toolkit import CAPABILITY_SEARCH_TOOL_NAME, IncrementalNoticeSession, SkillDCICommandResult

if TYPE_CHECKING:
    from openjiuwen.symphony.tree_engine import TreeEngine

_MIN_OUTPUT_CHARS = 512
_MAX_OUTPUT_CHARS = 48_000
_SKILL_DESCRIPTION_CHARS = 180
_MAX_CURSORS = 32
_CURSOR_FOOTER_CHARS = 96
_CAPABILITY_TYPES = get_args(CapabilityType)
_OPERATIONS = ("list", "search")
_MAX_TOP_K = 50
_TOOL_ID_DOMAIN = b"openjiuwen.skill-index-tool.v1\0"


@dataclass(frozen=True)
class _Row:
    text: str
    worker_id: str = ""


@dataclass(frozen=True)
class _PageRequest:
    kinds: tuple[str, ...]
    operation: str
    category: str | None
    queries: tuple[str, ...]
    page_size: int
    offset: int = 0
    fingerprints: tuple[str, ...] = ()


def _tool_card(tool_id: str) -> ToolCard:
    return ToolCard(
        id=tool_id,
        name=CAPABILITY_SEARCH_TOOL_NAME,
        description=(
            "Read-only discovery of session-visible Skills, experts and MCP services. "
            "Search or list all three by default, or select one or more types. Results share one page. "
            "Search names and descriptions. Skill search also reads SKILL.md. "
            "Continue with the returned cursor. Use Skill IDs with skill_tool, "
            "expert references with spawn_expert, and tool_search to inspect deferred tools."
        ),
        input_params={
            "type": "object",
            "properties": {
                "type": {
                    "type": "array", "items": {"type": "string", "enum": list(_CAPABILITY_TYPES)},
                    "minItems": 1, "maxItems": len(_CAPABILITY_TYPES),
                    "description": (
                        'Select distinct types, e.g. ["expert","mcp"]. Omit for all three. '
                        "A cursor remembers the original selection and order."
                    ),
                },
                "top_k": {
                    "type": "integer", "minimum": 1, "maximum": _MAX_TOP_K,
                    "description": "Maximum total entries on this page across the selected types.",
                },
                "operation": {
                    "type": "string",
                    "enum": list(_OPERATIONS),
                    "description": (
                        "Defaults to search on a new request and to the original operation on continuation. "
                        "list: direct subcategories and entries. search: names and descriptions including descendants. "
                        "MCP also searches available actions "
                        "and Skill search reads SKILL.md."
                    ),
                },
                "category": {
                    "type": "string",
                    "minLength": 1,
                    "description": (
                        "Scope: returned category name or A > B chain, never a disk path. "
                        "Omit for a global search or to keep the cursor's scope."
                    ),
                },
                "query": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1, "maxLength": 512},
                    "minItems": 1,
                    "maxItems": 8,
                    "description": (
                        'Search only. Array even for one query: ["PDF OCR"]. '
                        "Use task-specific terms in the language of the catalog descriptions. "
                        "All types accept up to eight independent queries with interleaved, deduplicated results. "
                        "MCP results group available actions by service. Skill search can return category hints."
                    ),
                },
                "cursor": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 128,
                    "description": "Continue the same page sequence. To change the search, omit cursor.",
                },
            },
            "required": [],
            "additionalProperties": False,
        },
        exposure=ToolExposure.DIRECT,
        parallel_safe=False,
        stateless=False,
    )


class CapabilitySearchTool(LocalFunction):
    """Normalize search strings and enforce the model-facing argument schema."""

    def is_bound_to(self, retrieval: TreeEngine, environment: SkillFS, session_scope: str) -> bool:
        """Verify the built-in read-only executor against its live host binding."""
        method = self._func
        if type(self) is not CapabilitySearchTool or type(method) is not MethodType:
            return False
        owner = method.__self__
        return (
            method.__func__ is _CapabilityDirectory.search
            and type(owner) is _CapabilityDirectory
            and owner is retrieval._directory
            and owner.environment is environment
            and owner.session_scope == session_scope
            and not owner._closed
            and vars(_CapabilityDirectory).keys().isdisjoint(vars(owner))
        )

    async def invoke(self, inputs: dict[str, Any], **kwargs: Any) -> Any:
        if isinstance(inputs, dict):
            unexpected = inputs.keys() - self.card.input_params["properties"].keys()
            if unexpected:
                raise ValueError(f"Unknown capability_search arguments: {', '.join(sorted(unexpected))}")
            inputs = dict(inputs)
            if "type" in inputs:
                inputs["type"] = list(_selected_types(inputs["type"]))
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


class _CapabilityDirectory:
    """Keep search cursors, observations and host callbacks scoped to one binding."""

    def __init__(
        self,
        environment: SkillFS,
        *,
        session_scope: str,
        incremental_notice_max_chars: int = 4_000,
        catalog_candidates: Callable[[str], Awaitable[Sequence[CapabilityCandidate]]] | None = None,
        expert_snapshot: Callable[[], SkillIndexSnapshot | None] | None = None,
    ) -> None:
        if not isinstance(environment, SkillFS):
            raise TypeError("environment must be a SkillFS")
        self._environment = environment
        self._catalog_candidates = catalog_candidates
        self._expert_snapshot = expert_snapshot
        self._default_max_output_chars = environment.settings.output.max_chars
        if not _MIN_OUTPUT_CHARS <= self._default_max_output_chars <= _MAX_OUTPUT_CHARS:
            raise ValueError("settings.max_output_chars must be between 512 and 48000")
        scope = str(session_scope or "").strip()
        if not scope:
            raise ValueError("session_scope must be non-empty")
        self.session_scope = scope
        digest = hashlib.sha256(_TOOL_ID_DOMAIN + scope.encode()).hexdigest()
        self._tool_id = f"{CAPABILITY_SEARCH_TOOL_NAME}__{digest}"
        self._notice = IncrementalNoticeSession(
            scope,
            environment.selection_cards(refresh=False),
            max_chars=incremental_notice_max_chars,
        )
        self._observed_skill_ids: set[str] = set()
        self._cursors: dict[str, _PageRequest] = {}
        self._lock = asyncio.Lock()
        self._closed = False

    @property
    def environment(self) -> SkillFS:
        return self._environment

    async def search(
        self,
        query: str | list[str] | None = None,
        type: str | list[str] | tuple[str, ...] | None = None,
        operation: str | None = None,
        category: str | None = None,
        cursor: str | None = None,
        top_k: int | None = None,
    ) -> SkillDCICommandResult | dict[str, Any]:
        """Search the selected live catalogs, defaulting to all three on a new request."""
        if self._closed:
            raise RuntimeError("capability search binding is closed")
        kinds = _selected_types(type) if type is not None else None
        if operation is not None:
            operation = _enum(operation, _OPERATIONS, "operation")
        if top_k is not None and (
            isinstance(top_k, bool) or not isinstance(top_k, int) or not 1 <= top_k <= _MAX_TOP_K
        ):
            raise ValueError(f"top_k must be an integer from 1 to {_MAX_TOP_K}")
        async with self._lock:
            if self._closed:
                raise RuntimeError("capability search binding is closed")
            page = self._page_request(kinds, operation, category, query, cursor, top_k)
            catalogs = {}
            if self._catalog_candidates is not None:
                for kind in page.kinds:
                    if kind != "skill":
                        catalogs[kind] = await self._catalog_candidates(kind)
            if cursor is not None:
                self._cursors.pop(cursor.strip())
            return await asyncio.to_thread(self._read_page, page, catalogs)

    def get_tools(self) -> list[Tool]:
        if self._closed:
            raise RuntimeError("capability search binding is closed")
        return [CapabilitySearchTool(card=_tool_card(self._tool_id), func=self.search)]

    async def aclose(self) -> None:
        async with self._lock:
            self._closed = True
            self._cursors.clear()

    def close(self) -> None:
        self._closed = True
        if not self._lock.locked():
            self._cursors.clear()

    def _page_request(
        self, kinds: tuple[str, ...] | None, operation: str | None, category: str | None, query: str | list[str] | None,
        cursor: str | None, top_k: int | None,
    ) -> _PageRequest:
        if operation == "list" and query is not None:
            raise ValueError("query is valid only for search")
        if cursor is not None:
            if top_k is not None:
                raise ValueError("cursor cannot be combined with top_k")
            return self._resume_cursor(kinds, operation, cursor, category, query)
        operation = operation or "search"
        output = self._environment.settings.output
        maximum = min(output.max_results, output.max_list_entries) if operation == "list" else output.max_results
        terms = _queries(query) if operation == "search" else ()
        return _PageRequest(kinds or _CAPABILITY_TYPES, operation, category, terms, min(top_k or maximum, maximum))

    def _resume_cursor(
        self, kinds: tuple[str, ...] | None, operation: str | None, cursor: str, category: str | None,
        query: str | list[str] | None,
    ) -> _PageRequest:
        token = _nonempty(cursor, "cursor")
        if len(token) > 128:
            raise ValueError("cursor must not exceed 128 characters")
        state = self._cursors.get(token)
        if state is None:
            raise ValueError("Unknown or expired capability catalog cursor")
        if kinds is not None and kinds != state.kinds:
            raise ValueError(f"cursor belongs to type={','.join(state.kinds)}")
        if operation is not None and operation != state.operation:
            raise ValueError(f"cursor belongs to operation={state.operation}")
        if category is not None and category != state.category:
            raise ValueError("cursor category differs from the original request; omit cursor to change category")
        if query is not None and _queries(query) != state.queries:
            raise ValueError("cursor query differs from the original request; omit cursor to start a new search")
        return state

    def _read_page(
        self, page: _PageRequest, catalogs: dict[str, Sequence[CapabilityCandidate]],
    ) -> SkillDCICommandResult | dict[str, Any]:
        groups = []
        fingerprints = []
        total = 0
        for index, kind in enumerate(page.kinds):
            if kind == "skill":
                view = self._environment.directory
                by_id, fingerprint = {}, self._environment.artifact.fingerprint
            else:
                snapshot = self._expert_snapshot() if kind == "expert" and self._expert_snapshot is not None else None
                view, by_id, fingerprint = _catalog_view(kind, catalogs.get(kind, ()), snapshot)
            if page.fingerprints and fingerprint != page.fingerprints[index]:
                label = {"skill": "Skill index", "expert": "Expert catalog", "mcp": "MCP catalog"}[kind]
                raise ValueError(f"{label} changed; start a new list or search request")
            fingerprints.append(fingerprint)
            path = _category_path(view, page.category, label="Skill" if kind == "skill" else kind)
            if path is not None:
                rows, count = self._rows_in_scope(page, kind, path, view, by_id)
                groups.append(rows)
                total += count
        if not groups:
            label = "Skill" if page.kinds == ("skill",) else "/".join(page.kinds)
            raise ValueError(f"Unknown {label} category: {page.category}")
        page = replace(page, fingerprints=tuple(fingerprints))
        window = list(islice(interleave_rankings(groups), page.offset, page.offset + page.page_size))
        if page.kinds == ("skill",):
            display = [_skill_display(row, page.operation) for row in window]
            return self._render_skills(page, display, total, self._environment.artifact)
        return self._render_catalog(page, window, total)

    def _rows_in_scope(
        self, page: _PageRequest, kind: str, path: str, view: SkillDirectoryView,
        by_id: dict[str, CapabilityCandidate],
    ) -> tuple[Iterable[dict[str, Any]], int]:
        if page.operation == "list":
            entries = view.children(path)
            return (_list_row(kind, entry, view, by_id) for entry in entries), len(entries)
        records = view.scoped_records((path,))
        if kind == "skill":
            rows = self._search_skills(view, path, records, page.queries)
        else:
            rows = _catalog_search_rows(view, records, by_id, page.queries)
        return rows, len(rows)

    def _render_catalog(self, page: _PageRequest, rows: list[dict[str, Any]], total: int) -> dict[str, Any]:
        if len(page.kinds) > 1:
            rows = _fit_catalog_rows(rows, self._default_max_output_chars)
        categories = [row for row in rows if row["type"] == "category"]
        result: dict[str, Any] = {
            "matches": [row for row in rows if row["type"] != "category"],
            "next_cursor": self._next_cursor(page, len(rows), total),
        }
        if page.operation == "list" or categories:
            result["categories"] = categories
        if "skill" in page.kinds:
            self._observed_skill_ids.update(row["id"] for row in result["matches"] if row["type"] == "skill")
            remaining = self._default_max_output_chars - len(json.dumps(result, ensure_ascii=False)) - 20
            notice = self._notice.append("", self._environment.selection_cards(refresh=False), output_budget=remaining)
            if notice:
                result["skill_notice"] = notice
        return result

    def _render_skills(
        self, page: _PageRequest, rows: Sequence[_Row], total: int, artifact: SkillFSArtifact,
    ) -> SkillDCICommandResult:
        previously_shown = page.operation == "search" and all(
            not row.worker_id or row.worker_id in self._observed_skill_ids for row in rows
        )
        budget = self._default_max_output_chars
        fit_budget = budget - _CURSOR_FOOTER_CHARS if rows else budget
        content, observed, shortened, shown_count = _fit_rows(
            rows, operation=page.operation, previously_shown=previously_shown, budget=fit_budget,
        )
        if not rows:
            content = _append_note(content, _empty_message(page.operation, page.category), budget)
        next_cursor = self._next_cursor(page, shown_count, total)
        if next_cursor is not None:
            args = {"type": ["skill"], "operation": page.operation, "cursor": next_cursor}
            footer = "[next] " + json.dumps(args, separators=(",", ":"))
            content = _append_note(content, footer, budget)
        elif shortened:
            content = _append_note(content, "[truncated] Candidate details shortened.", budget)
        content = self._notice.append(content, self._environment.selection_cards(refresh=False), output_budget=budget)
        self._observed_skill_ids.update(observed)
        diagnostics = {
            "operation": page.operation,
            "output": content,
            "observed_skill_ids": observed,
            "error": False,
            "ok": True,
            "truncated": shortened,
            "truncation_reason": "max_output_chars" if shortened else None,
            "total_count": total,
            "shown_count": shown_count,
            "result_complete": next_cursor is None and not shortened,
            "next_cursor": next_cursor,
            "effective_max_output_chars": budget,
            "skillfs_layout": artifact.layout,
            "candidate_count": len(artifact.items),
            "estimated_candidate_tokens": _candidate_tokens(artifact.items),
            "candidate_budget_tokens": self._environment.settings.prompt.candidate_budget_tokens,
            "index_state": artifact.index_state,
            "runtime": "Symphony.CapabilitySearch",
        }
        return SkillDCICommandResult(content, detailed_output=diagnostics, model_content=content)

    def _next_cursor(self, page: _PageRequest, shown: int, total: int) -> str | None:
        next_offset = page.offset + shown
        if next_offset >= total:
            return None
        token = secrets.token_urlsafe(12)
        while token in self._cursors:
            token = secrets.token_urlsafe(12)
        self._cursors[token] = replace(page, offset=next_offset)
        while len(self._cursors) > _MAX_CURSORS:
            self._cursors.pop(next(iter(self._cursors)))
        return token

    def _search_skills(
        self, view: SkillDirectoryView, path: str, records: Sequence[SkillRecord], queries: tuple[str, ...],
    ) -> list[dict[str, Any]]:
        queries_by_id: dict[str, list[str]] = {}

        def rank(query: str) -> Sequence[SkillRecord | DirectoryEntry]:
            identifiers = self._environment.search_content(
                records, query, case_insensitive=True, fixed_strings=False, term_mode=True,
            )
            matches = tuple(view.record_by_id[identifier] for identifier in identifiers)
            group = matches or self._search_categories(view, path, query)
            for item in group:
                queries_by_id.setdefault(_search_identity(item), []).append(query)
            return group

        rows = []
        for item in rank_queries(queries, rank, _search_identity):
            if isinstance(item, DirectoryEntry):
                rows.append(_category_row("skill", item, view, description_limit=300))
            else:
                snippet = self._environment.content_match_snippet(item, tuple(queries_by_id[_search_identity(item)]))
                rows.append(_skill_row(item, view, snippet=_safe_match_snippet(snippet)))
        return rows

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


def _catalog_view(
    kind: str, candidates: Sequence[CapabilityCandidate], snapshot: SkillIndexSnapshot | None,
) -> tuple[SkillDirectoryView, dict[str, CapabilityCandidate], str]:
    by_id = {
        candidate.descriptor.capability_id: candidate
        for candidate in sorted(candidates, key=lambda item: item.descriptor.capability_id)
        if candidate.descriptor.available and candidate.descriptor.capability_type == kind
    }
    revision = []
    for identifier, candidate in by_id.items():
        descriptor = candidate.descriptor
        revision.append((
            identifier, descriptor.content_hash, descriptor.name, descriptor.classification, descriptor.tags,
            descriptor.semantic_content, candidate.aliases, str(candidate.manifest_path or ""),
            fingerprint_text(candidate.fingerprint, descriptor.content_hash, descriptor.description),
            [astuple(action) for action in candidate.actions],
        ))
    fingerprint = hashlib.sha256(json.dumps((
        snapshot.taxonomy_fingerprint if snapshot is not None else "", revision,
    )).encode()).hexdigest()
    items = tuple(SkillRecord(identifier, candidate.descriptor.name, candidate.descriptor.description, "")
                  for identifier, candidate in by_id.items())
    categories = {key: item.descriptor.classification for key, item in by_id.items()} if kind == "mcp" else None
    root, catalog = _build_live_tree(
        items, snapshot, item_label="experts" if kind == "expert" else "MCP services", categories_by_id=categories,
    )
    view = SkillDirectoryView(root=root, records=items, catalog_by_payload=catalog)
    if snapshot is not None:
        for identifier, candidate in by_id.items():
            by_id[identifier] = replace(candidate, descriptor=candidate.descriptor.model_copy(update={
                "classification": _skill_category(view, identifier),
            }))
    return view, by_id, fingerprint


def _list_row(
    kind: str, entry: DirectoryEntry, view: SkillDirectoryView, by_id: dict[str, CapabilityCandidate],
) -> dict[str, Any]:
    if entry.kind == "dir":
        return _category_row(kind, entry, view, description_limit=240)
    if kind == "skill":
        return _skill_row(view.record_by_id[entry.worker_id], view)
    return {**capability_row(by_id[entry.worker_id]), "category": _skill_category(view, entry.worker_id)}


def _catalog_search_rows(
    view: SkillDirectoryView, records: Sequence[SkillRecord], by_id: dict[str, CapabilityCandidate],
    queries: tuple[str, ...],
) -> list[dict[str, Any]]:
    rows = rank_capabilities([by_id[record.worker_id] for record in records], queries)
    for row in rows:
        row["category"] = _skill_category(view, row["id"])
    return rows


def _skill_row(record: SkillRecord, directory: SkillDirectoryView, *, snippet: str = "") -> dict[str, Any]:
    row = {
        "type": "skill", "id": record.worker_id, "name": record.name,
        "description": _compact(record.description or record.name, _SKILL_DESCRIPTION_CHARS),
        "category": _skill_category(directory, record.worker_id), "path": _skill_path(record),
    }
    if snippet:
        row["match"] = snippet
    return row


def _category_row(
    kind: str, entry: DirectoryEntry, directory: SkillDirectoryView, *, description_limit: int,
) -> dict[str, Any]:
    row = {
        "type": "category", "capability_type": kind, "name": _inline_text(entry.label),
        "category": _category_from_path(directory, entry.path), "count": directory.skill_count(entry.path),
    }
    if kind == "skill":
        row["description"] = _directory_description(entry.description, description_limit)
    return row


def _skill_display(row: dict[str, Any], operation: str) -> _Row:
    if row["type"] == "category":
        count = row["count"]
        detail = f"desc: Contains {count} skill{'s' if count != 1 else ''}. {row['description']}".rstrip()
        return _Row(f"- [category] {row['category']}  {detail}")
    fields = [f"- [skill] {row['id']}", f"category: {row['category']}"]
    description, path = f"desc: {row['description']}", f"path: {row['path']}"
    if operation == "list":
        fields.extend((path, description))
    else:
        if row.get("match"):
            fields.append(f"match: {row['match']}")
        fields.extend((description, path))
    return _Row("  ".join(fields), row["id"])


def _fit_catalog_rows(rows: list[dict[str, Any]], budget: int) -> list[dict[str, Any]]:
    """Fit a mixed page to the output budget, preserving at least the first row's identity."""
    used = _CURSOR_FOOTER_CHARS
    for index, row in enumerate(rows):
        used += len(json.dumps(row, ensure_ascii=False)) + 2
        if used > budget:
            if index:
                return rows[:index]
            keys = ("type", "capability_type", "category", "count") if row["type"] == "category" else ("type", "id")
            compact = {key: row[key] for key in keys}
            compact["details_truncated"] = True
            return [compact]
    return rows


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


def _selected_types(value: str | list[str] | tuple[str, ...]) -> tuple[str, ...]:
    if isinstance(value, str):
        value = (value,)
    if not isinstance(value, (list, tuple)) or not 1 <= len(value) <= len(_CAPABILITY_TYPES):
        raise ValueError("type must contain 1-3 distinct capability types")
    kinds = tuple(_enum(kind, _CAPABILITY_TYPES, "type") for kind in value)
    if len(set(kinds)) != len(kinds):
        raise ValueError("type must contain distinct capability types")
    return kinds


def _category_path(directory: SkillDirectoryView, category: str | None, *, label: str) -> str | None:
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
    if len(matches) > 1:
        raise ValueError(f"Ambiguous {label} category; use its full category chain: {value}")
    return matches[0] if matches else None


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


__all__ = ["CapabilitySearchTool"]
