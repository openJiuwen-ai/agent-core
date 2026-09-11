"""Structured ``skill_index`` adapter over Symphony's retriever tree."""

from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import json
import re
import secrets
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Iterable, Mapping, Sequence

from openjiuwen.core.foundation.tool import LocalFunction, Tool, ToolCard, ToolExposure
from openjiuwen.symphony.retrieval.search.runtime.lexical import LexicalDocument, LexicalIndex, compile_matcher

from .models import SkillRecord, sanitize_model_text
from .skillfs import DirectoryEntry, SkillDirectoryView, SkillFS
from .toolkit import SKILL_INDEX_TOOL_NAME, IncrementalNoticeSession, SkillDCICommandResult

_MIN_OUTPUT_CHARS = 512
_MAX_OUTPUT_CHARS = 48_000
_MAX_LINES = 5_000
_SKILL_DESCRIPTION_CHARS = 180
_DEFAULT_SEARCH_PAGE_SIZE = 10
_MAX_CURSORS = 32
_CURSOR_FOOTER_CHARS = 96
_OPERATIONS = ("list", "search", "read")
_MODEL_OPERATIONS = ("list", "search")
_LIST_VIEWS = ("names", "details", "tree")
_SEARCH_MATCHES = ("content", "name", "path")
_SEARCH_RESULTS = ("files", "matches")
_READ_MODES = ("full", "head", "range")
_PIPELINE_OPERATIONS = ("limit", "slice", "filter", "count")
_OUTPUT_MODES = ("entries", "count")
_TOOL_ID_DOMAIN = b"openjiuwen.skill-index-tool.v1\0"
_SHORTENED = "[skill_index output shortened to fit output budget]"
_SKILL_INDEX_DEFAULTS: dict[str, Any] = {
    "view": "names",
    "recursive": False,
    "directory_entry": False,
    "directories_only": False,
    "query": None,
    "queries": None,
    "per_query_limit": None,
    "match": "content",
    "result": "files",
    "case_insensitive": True,
    "fixed_strings": False,
    "max_depth": None,
    "read_mode": "full",
    "line_count": None,
    "start_line": None,
    "end_line": None,
    "pipeline": None,
    "output_mode": "entries",
    "max_output_chars": None,
    "disable_output_truncation": False,
}


@dataclass(frozen=True)
class _Row:
    text: str
    worker_id: str = ""


@dataclass(frozen=True)
class _Execution:
    rows: tuple[_Row, ...]
    total_count: int
    complete: bool


@dataclass(frozen=True)
class _CursorState:
    operation: str
    category: str | None
    query: str | tuple[str, ...] | None
    offset: int
    page_size: int
    fingerprint: str


def _tool_card(tool_id: str) -> ToolCard:
    return ToolCard(
        id=tool_id,
        name=SKILL_INDEX_TOOL_NAME,
        description=(
            "Read-only Skill catalogue, not a filesystem tool. [category] is a virtual group, "
            "not a disk directory or Skill; counts include descendants. [skill] names a Skill; "
            "path is its real SKILL.md for file tools, not an input here. "
            "Calls do not change a working directory. Search returns ranked text matches, not verified capabilities. "
            "No query translation."
        ),
        input_params={
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": list(_MODEL_OPERATIONS),
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
            "required": ["operation"],
            "additionalProperties": False,
        },
        exposure=ToolExposure.DIRECT,
        parallel_safe=False,
        stateless=False,
    )


class _DirectoryFunction(LocalFunction):
    """Explain unsupported inputs and normalize unambiguous legacy queries."""

    async def invoke(self, inputs: dict[str, Any], **kwargs: Any) -> Any:
        if isinstance(inputs, dict):
            unexpected = inputs.keys() - self.card.input_params["properties"].keys()
            if unexpected:
                raise ValueError(
                    f"Unknown skill_index arguments: {', '.join(sorted(unexpected))}. "
                    "Only operation, category, query, cursor are accepted. "
                    'Use {"operation":"list","category":"A > B"} or '
                    '{"operation":"search","query":["keywords"],"category":"A > B"}; '
                    "omit category for root/global. Disk paths and globs belong to file tools. "
                    "For the next page, copy [next] unchanged."
                )
            inputs = dict(inputs)
            category = inputs.get("category")
            if isinstance(category, str) and not category.strip():
                inputs.pop("category")
            query = inputs.get("query")
            if inputs.get("operation") == "search" and isinstance(query, str):
                inputs["query"] = [query]
            elif inputs.get("operation") == "list" and query == []:
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
        **_: Any,
    ) -> None:
        if not isinstance(environment, SkillFS):
            raise TypeError("environment must be a SkillFS")
        self._environment = environment
        self._default_max_output_chars = environment.settings.max_output_chars
        if not _MIN_OUTPUT_CHARS <= self._default_max_output_chars <= _MAX_OUTPUT_CHARS:
            raise ValueError("settings.max_output_chars must be between 512 and 48000")
        scope = str(session_scope or "").strip()
        if not scope:
            raise ValueError("session_scope must be non-empty")
        digest = hashlib.sha256(_TOOL_ID_DOMAIN + scope.encode()).hexdigest()
        self._tool_id = f"{SKILL_INDEX_TOOL_NAME}__{digest}"
        self._notice = IncrementalNoticeSession(
            scope,
            environment.selection_cards(refresh=False),
            max_chars=incremental_notice_max_chars,
        )
        self._observed_meta_paths: dict[str, str] = {}
        self._cursors: dict[str, _CursorState] = {}
        self._lock = asyncio.Lock()
        self._closed = False

    @property
    def environment(self) -> SkillFS:
        return self._environment

    @property
    def tool_id(self) -> str:
        return self._tool_id

    async def skill_index(
        self,
        operation: str,
        category: str | None = None,
        query: str | list[str] | None = None,
        skills: list[str] | None = None,
        cursor: str | None = None,
        **values: Any,
    ) -> SkillDCICommandResult:
        """Run one serialized structured directory operation."""

        limit = values.pop("limit", None)
        paths = values.pop("paths", None)
        unexpected = set(values).difference(_SKILL_INDEX_DEFAULTS)
        if unexpected:
            name = sorted(unexpected)[0]
            raise TypeError(f"skill_index() got an unexpected keyword argument '{name}'")
        if cursor is not None:
            if any(value is not None for value in (skills, limit, paths)) or values:
                raise ValueError("cursor cannot be combined with advanced arguments")
            async with self._lock:
                if self._closed:
                    raise RuntimeError("skill_index toolkit is closed")
                return await asyncio.to_thread(self._continue, operation, cursor, category, query)
        simple_request = paths is None and not values and skills is None
        if category is not None and paths is not None:
            raise ValueError("category and paths are mutually exclusive")
        arguments = {
            "operation": operation,
            "skills": skills,
            "paths": paths,
            **_SKILL_INDEX_DEFAULTS,
            **values,
        }
        arguments["query"] = query
        arguments["category"] = category
        if simple_request:
            _apply_simple_defaults(arguments, limit)
        elif limit is not None:
            raise ValueError("limit cannot be combined with advanced arguments")
        async with self._lock:
            if self._closed:
                raise RuntimeError("skill_index toolkit is closed")
            return await asyncio.to_thread(self._execute, arguments)

    def get_tools(self) -> list[Tool]:
        tool = _DirectoryFunction(
            card=_tool_card(self._tool_id),
            func=self.skill_index,
        )
        return [tool]

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
        operation = _enum(operation, _MODEL_OPERATIONS, "operation")
        token = _nonempty(cursor, "cursor")
        if len(token) > 128:
            raise ValueError("cursor must not exceed 128 characters")
        state = self._cursors.get(token)
        if state is None:
            raise ValueError("Unknown or expired skill_index cursor")
        if operation != state.operation:
            raise ValueError(f"cursor belongs to operation={state.operation}")
        if category is not None and category != state.category:
            raise ValueError("cursor category differs from the original request; omit cursor to change category")
        original_queries = state.query if isinstance(state.query, tuple) else (state.query,)
        if query is not None and _queries(query, None) != original_queries:
            raise ValueError("cursor query differs from the original request; omit cursor to start a new search")
        self._cursors.pop(token)
        arguments = {
            "operation": state.operation,
            "skills": None,
            "paths": None,
            **_SKILL_INDEX_DEFAULTS,
            "category": state.category,
            "query": list(state.query) if isinstance(state.query, tuple) else state.query,
            "_page_size": state.page_size,
            "_page_offset": state.offset,
            "_cursor_fingerprint": state.fingerprint,
        }
        if state.operation == "list":
            arguments["view"] = "details"
        return self._execute(arguments)

    def _execute(self, arguments: dict[str, Any]) -> SkillDCICommandResult:
        operation = _enum(arguments["operation"], _OPERATIONS, "operation")
        view = self._environment.directory
        category = arguments.pop("category", None)
        page_size = arguments.pop("_page_size", None)
        page_offset = arguments.pop("_page_offset", 0)
        expected_fingerprint = arguments.pop("_cursor_fingerprint", None)
        arguments["_simple_page"] = page_size is not None
        artifact = self._environment.artifact
        if expected_fingerprint is not None and artifact.fingerprint != expected_fingerprint:
            raise ValueError("Skill index changed; start a new list or search request")
        if operation == "read":
            if category is not None:
                raise ValueError("category is valid only for list and search")
        else:
            arguments["paths"] = list(_category_paths(view, category, arguments["paths"]))
        pipeline = _validate_request(operation, arguments)
        paths = _paths(arguments["paths"], required=False)
        budget = _output_budget(
            arguments["max_output_chars"],
            disable=arguments["disable_output_truncation"],
            default=self._default_max_output_chars,
        )
        if operation == "list":
            execution = self._list(view, paths, arguments)
        elif operation == "search":
            execution = self._search(view, paths, arguments)
        else:
            skills = self._read_skills(view, arguments["skills"], arguments["paths"])
            execution = self._read(view, skills, arguments)

        rows, pipeline_complete, count_value, transformed_count = _apply_pipeline(
            execution.rows,
            pipeline,
            output_mode=arguments["output_mode"],
        )
        complete = execution.complete and pipeline_complete
        all_rows = rows
        if page_size == 0:
            # Show the branch choices together; Skill candidate pages stay bounded.
            page_size = 10 if any(row.worker_id for row in rows) else max(1, len(rows))
        if page_size is not None:
            page_end = page_offset + page_size
            rows = all_rows[page_offset:page_end]
            complete = complete and page_offset + len(rows) >= len(all_rows)
        result_entry_count = (
            count_value
            if count_value is not None
            else transformed_count
            if transformed_count is not None
            else _row_count(rows)
            if pipeline and pipeline_complete
            else execution.total_count
        )
        summary = {
            "operation": operation,
            "returned_skill_count": len({row.worker_id for row in rows if row.worker_id}),
            "returned_category_count": sum(row.text.lstrip().startswith("- [category]") for row in rows),
            "previously_shown": (
                operation == "search"
                and page_size is not None
                and {row.worker_id for row in rows if row.worker_id}.issubset(self._observed_meta_paths.values())
            ),
        }
        reserve = 0
        if page_size is not None and rows:
            reserve += _CURSOR_FOOTER_CHARS
        fit_budget = None if budget is None else max(_MIN_OUTPUT_CHARS // 2, budget - reserve)
        fitted, model_content, observed, shortened, shown_count = _fit_rows(
            rows,
            summary=summary,
            budget=fit_budget,
            count_value=count_value,
            show_shortened_marker=page_size is None,
        )
        if not fitted and not pipeline:
            empty = _empty_message(operation, category)
            if budget is None:
                fitted = empty
                model_content = f"{model_content}\n\n{empty}"
            else:
                remaining = budget - len(model_content) - 2
                if remaining > 3:
                    fitted = _compact(empty, remaining)
                    model_content = f"{model_content}\n\n{fitted}"
        next_cursor: str | None = None
        has_more = page_size is not None and page_offset + shown_count < len(all_rows)
        if has_more:
            next_cursor = self._save_cursor(
                _CursorState(
                    operation=operation,
                    category=category,
                    query=_cursor_query(arguments),
                    offset=page_offset + shown_count,
                    page_size=page_size,
                    fingerprint=artifact.fingerprint,
                )
            )
            footer = "[next] " + json.dumps({"operation": operation, "cursor": next_cursor}, separators=(",", ":"))
            fitted = _append_note(fitted, footer, budget)
            model_content = _append_note(model_content, footer, budget)
        elif page_size is not None and shortened:
            note = "[truncated] Candidate details shortened."
            fitted = _append_note(fitted, note, budget)
            model_content = _append_note(model_content, note, budget)
        if page_size is not None:
            fitted = model_content
        cards = {
            item.worker_id: {"name": item.worker_id, "description": item.description or item.name}
            for item in artifact.items
        }
        delivered_model, reminder = self._notice.append(model_content, cards, output_budget=budget)
        if reminder:
            fitted = f"{fitted}\n\n{reminder}" if fitted else reminder
            model_content = delivered_model
        for worker_id in observed:
            try:
                self._observed_meta_paths[view.normalize_path(view.metadata_path(worker_id))] = worker_id
            except ValueError:
                continue
        diagnostics = {
            "operation": operation,
            "output": fitted,
            "observed_skill_ids": observed,
            "error": False,
            "ok": True,
            "truncated": shortened,
            "truncation_reason": "max_output_chars" if shortened else None,
            "total_count": result_entry_count,
            "shown_count": shown_count,
            "result_complete": not has_more and complete and not shortened,
            "next_cursor": next_cursor,
            "disable_output_truncation": bool(arguments["disable_output_truncation"]),
            "effective_max_output_chars": budget,
            "skillfs_layout": artifact.layout,
            "candidate_count": len(artifact.items),
            "estimated_candidate_tokens": _candidate_tokens(artifact.items),
            "candidate_budget_tokens": self._environment.settings.candidate_budget_tokens,
            "index_state": artifact.index_state,
            "runtime": "Symphony.SkillIndex",
        }
        return SkillDCICommandResult(fitted, detailed_output=diagnostics, model_content=model_content)

    def _save_cursor(self, state: _CursorState) -> str:
        token = secrets.token_urlsafe(12)
        while token in self._cursors:
            token = secrets.token_urlsafe(12)
        self._cursors[token] = state
        while len(self._cursors) > _MAX_CURSORS:
            self._cursors.pop(next(iter(self._cursors)))
        return token

    @staticmethod
    def _list(view: SkillDirectoryView, paths: tuple[str, ...], arguments: Mapping[str, Any]) -> _Execution:
        list_view = _enum(arguments["view"], _LIST_VIEWS, "view")
        recursive = _boolean(arguments["recursive"], "recursive") or list_view == "tree"
        max_depth = _positive(arguments["max_depth"], "max_depth", optional=True)
        directories_only = _boolean(arguments["directories_only"], "directories_only")
        entries = (
            view.tree_entries(paths, max_depth=max_depth, directories_only=directories_only)
            if list_view == "tree"
            else view.entries(
                paths,
                recursive=recursive,
                max_depth=max_depth,
                directories_only=directories_only,
                directory_entry=_boolean(arguments["directory_entry"], "directory_entry"),
            )
        )
        rows = tuple(_list_row(entry, view=list_view, directory=view) for entry in entries)
        return _Execution(rows, len(entries), True)

    def _search(self, view: SkillDirectoryView, paths: tuple[str, ...], arguments: Mapping[str, Any]) -> _Execution:
        match = _enum(arguments["match"], _SEARCH_MATCHES, "match")
        result = _enum(arguments["result"], _SEARCH_RESULTS, "result")
        case_insensitive = _boolean(arguments["case_insensitive"], "case_insensitive")
        fixed_strings = _boolean(arguments["fixed_strings"], "fixed_strings")
        max_depth = _positive(arguments["max_depth"], "max_depth", optional=True)
        scoped_records = view.scoped_records(paths, max_depth=max_depth) if match == "content" else ()
        scoped_entries = view.searchable_entries(paths, max_depth=max_depth) if match != "content" else ()
        queries = _queries(arguments["query"], arguments["queries"])
        per_query_limit = _positive(arguments["per_query_limit"], "per_query_limit", optional=True)
        if per_query_limit is not None and per_query_limit > 10:
            raise ValueError("per_query_limit must not exceed 10")
        simple_page = bool(arguments.get("_simple_page"))
        if len(queries) > 1 and not simple_page:
            limit = per_query_limit or 5
        else:
            limit = per_query_limit
        selected_by_id: dict[str, SkillRecord | DirectoryEntry] = {}
        query_indexes_by_id: dict[str, tuple[int, ...]] = {}
        query_counts: list[int] = []
        selected_groups: list[tuple[SkillRecord | DirectoryEntry, ...]] = []
        all_matches: set[str] = set()
        for query_index, current_query in enumerate(queries, start=1):
            if match == "content":
                matches = self._search_content_one(
                    scoped_records,
                    current_query,
                    case_insensitive=case_insensitive,
                    fixed_strings=fixed_strings,
                    term_mode=simple_page,
                )
                if simple_page and not matches:
                    matches = self._search_categories(view, paths, current_query, max_depth=max_depth)
            else:
                matches = self._search_entry_one(
                    view,
                    scoped_entries,
                    current_query,
                    match=match,
                    case_insensitive=case_insensitive,
                )
            identities = tuple(_search_identity(item) for item in matches)
            all_matches.update(identities)
            selected = matches if limit is None else matches[:limit]
            selected_groups.append(tuple(selected))
            query_counts.append(len(selected))
            for item in selected:
                identity = _search_identity(item)
                if identity not in selected_by_id:
                    selected_by_id[identity] = item
                    query_indexes_by_id[identity] = ()
                if len(queries) > 1:
                    query_indexes_by_id[identity] = (*query_indexes_by_id[identity], query_index)

        order: list[str] = []
        if simple_page and len(selected_groups) > 1:
            for rank in range(max((len(group) for group in selected_groups), default=0)):
                for group in selected_groups:
                    if rank < len(group):
                        order.append(_search_identity(group[rank]))
        else:
            order.extend(_search_identity(item) for group in selected_groups for item in group)
        order = list(dict.fromkeys(order))
        rows: list[_Row] = []
        for identity in order:
            item = selected_by_id[identity]
            indexes = query_indexes_by_id[identity]
            if isinstance(item, DirectoryEntry):
                if item.kind == "dir":
                    rows.append(_search_directory_row(item, view, () if simple_page else indexes))
                else:
                    record = view.record_by_id[item.worker_id]
                    rows.append(_search_row(record, view, indexes))
                continue
            if result == "files":
                matched_queries = tuple(queries[index - 1] for index in indexes) or queries
                snippet = self._environment.content_match_snippet(item, matched_queries) if simple_page else ""
                if snippet:
                    snippet = _safe_match_snippet(snippet)
                rows.append(_search_row(item, view, () if simple_page else indexes, snippet))
                continue
            matched_queries = tuple(queries[index - 1] for index in indexes) or queries
            snippets = self._matched_snippets(
                item,
                matched_queries,
                match=match,
                case_insensitive=case_insensitive,
                fixed_strings=fixed_strings,
                skill_path=item.skill_file,
                category_text=view.category_text(item.worker_id),
            )
            for snippet in snippets:
                rows.append(_search_row(item, view, indexes, snippet))
        complete = limit is None or all(count < limit for count in query_counts)
        total_count = len(rows) if result == "matches" else len(all_matches)
        return _Execution(tuple(rows), total_count, complete)

    @staticmethod
    def _search_categories(
        view: SkillDirectoryView,
        paths: tuple[str, ...],
        query: str,
        *,
        max_depth: int | None,
    ) -> tuple[DirectoryEntry, ...]:
        entries = {
            entry.path: entry
            for entry in view.tree_entries(paths, max_depth=max_depth, directories_only=True)
            if entry.path != "/"
        }
        index = LexicalIndex(
            tuple(
                LexicalDocument(path, entry.label, _directory_description(entry.description, 300))
                for path, entry in entries.items()
            )
        )
        return tuple(entries[hit.key] for hit in index.search_terms(query))

    def _search_content_one(
        self,
        records: Sequence[SkillRecord],
        query: str,
        *,
        case_insensitive: bool,
        fixed_strings: bool,
        term_mode: bool,
    ) -> tuple[SkillRecord, ...]:
        identifiers = self._environment.search_content(
            records,
            query,
            case_insensitive=case_insensitive,
            fixed_strings=fixed_strings,
            term_mode=term_mode,
        )
        by_id = {record.worker_id: record for record in records}
        return tuple(by_id[worker_id] for worker_id in identifiers)

    @staticmethod
    def _search_entry_one(
        view: SkillDirectoryView,
        entries: Sequence[DirectoryEntry],
        query: str,
        *,
        match: str,
        case_insensitive: bool,
    ) -> tuple[DirectoryEntry, ...]:
        value = query.casefold() if case_insensitive else query
        wildcard = any(character in value for character in "*?[")
        matched: list[tuple[int, DirectoryEntry]] = []
        for entry in entries:
            if entry.kind == "skill":
                record = view.record_by_id[entry.worker_id]
                candidates = (
                    (PurePosixPath(entry.path).name, record.worker_id, record.name)
                    if match == "name"
                    else (record.skill_file,)
                )
            else:
                candidates = (PurePosixPath(entry.path).name or ".", entry.label) if match == "name" else (entry.path,)
            compared = tuple(candidate.casefold() if case_insensitive else candidate for candidate in candidates)
            pattern = value if wildcard else f"*{value}*"
            if not any(fnmatch.fnmatchcase(candidate, pattern) for candidate in compared):
                continue
            rank = 3 if wildcard else min(_literal_match_rank(candidate, value) for candidate in compared)
            matched.append((rank, entry))
        matched.sort(
            key=lambda item: (
                item[0],
                item[1].path.casefold(),
                item[1].path,
                item[1].kind,
            )
        )
        return tuple(entry for _, entry in matched)

    def _matched_snippets(
        self,
        record: SkillRecord,
        queries: Sequence[str],
        *,
        match: str,
        case_insensitive: bool,
        fixed_strings: bool,
        skill_path: str,
        category_text: str,
    ) -> tuple[str, ...]:
        if match != "content":
            return ()
        matchers = tuple(
            compile_matcher(
                query,
                case_insensitive=case_insensitive,
                fixed_strings=fixed_strings,
            )
            for query in queries
        )

        def matches(value: str) -> bool:
            return any(matcher(value) for matcher in matchers)

        fields = (
            ("id", record.worker_id),
            ("name", record.name),
            ("alias", "\n".join(record.aliases)),
            ("description", record.description),
            ("body", self._environment.read_body(record)),
            ("category", category_text),
            ("path", skill_path),
        )
        snippets: list[str] = []
        seen_snippets: set[str] = set()
        for label, value in fields:
            for line_number, line in enumerate(str(value or "").splitlines() or (str(value or ""),), start=1):
                if not matches(line):
                    continue
                safe = _compact(sanitize_model_text(line), 180)
                if safe:
                    location = f"{label}:{line_number}" if label == "body" else label
                    snippet = f"{location}: {safe}"
                    if snippet not in seen_snippets:
                        seen_snippets.add(snippet)
                        snippets.append(snippet)
        if snippets:
            return tuple(snippets)
        # A regular expression may match across line boundaries. Return a
        # bounded real excerpt instead of reducing that evidence to a field name.
        for label, value in fields:
            if matches(value):
                safe = _compact(sanitize_model_text(value), 180)
                if safe:
                    return (f"{label}: {safe}",)
        return ()

    def _read_skills(
        self,
        view: SkillDirectoryView,
        skills: Any,
        legacy_paths: Any,
    ) -> tuple[str, ...]:
        if skills is not None and legacy_paths is not None:
            raise ValueError("skills and paths are mutually exclusive")
        if legacy_paths is not None:
            resolved: list[str] = []
            for path in _paths(legacy_paths, required=True):
                worker_id = self._observed_meta_paths.get(view.normalize_path(path))
                if worker_id is None:
                    raise ValueError("read accepts only Skills returned by an earlier skill_index result")
                resolved.append(worker_id)
            return tuple(dict.fromkeys(resolved))
        return _identifiers(skills, "skills", required=True)

    @staticmethod
    def _read(
        view: SkillDirectoryView,
        skills: tuple[str, ...],
        arguments: Mapping[str, Any],
    ) -> _Execution:
        mode = _enum(arguments["read_mode"], _READ_MODES, "read_mode")
        rows: list[_Row] = []
        for skill in skills:
            record = view.record_by_id.get(skill)
            if record is None:
                raise ValueError(f"Unknown Skill: {skill}")
            content = _metadata_card(record)
            lines = content.splitlines()
            if mode == "head":
                lines = lines[: _positive(arguments["line_count"], "line_count", optional=True) or 10]
            elif mode == "range":
                start = _positive(arguments["start_line"], "start_line")
                end = _positive(arguments["end_line"], "end_line")
                if end < start:
                    raise ValueError("end_line must be greater than or equal to start_line")
                lines = lines[slice(start - 1, end)]
            rows.append(_Row("\n".join(lines), record.worker_id))
        return _Execution(tuple(rows), len(rows), True)


def _list_row(entry: DirectoryEntry, *, view: str, directory: SkillDirectoryView) -> _Row:
    indent = "  " * entry.depth if view == "tree" else ""
    if entry.kind == "dir":
        description = _directory_description(entry.description, 240)
        count = directory.skill_count(entry.path)
        detail = f"  desc: Contains {count} skill{'s' if count != 1 else ''}. {description}".rstrip()
        if view == "names":
            detail = ""
        return _Row(f"{indent}- [category] {_category_from_path(directory, entry.path)}{detail}")
    record = directory.record_by_id[entry.worker_id]
    category = _skill_category(directory, entry.worker_id)
    detail = (
        f"  desc: {_compact(entry.description, _SKILL_DESCRIPTION_CHARS)}"
        if view != "names" and entry.description
        else ""
    )
    return _Row(
        f"{indent}- [skill] {entry.worker_id}  category: {category}  path: {_skill_path(record)}{detail}",
        entry.worker_id,
    )


def _search_row(
    record: SkillRecord,
    directory: SkillDirectoryView,
    indexes: tuple[int, ...],
    snippet: str = "",
) -> _Row:
    description = _compact(record.description or record.name, _SKILL_DESCRIPTION_CHARS)
    fields = [
        f"- [skill] {record.worker_id}",
        f"category: {_skill_category(directory, record.worker_id)}",
    ]
    if indexes:
        fields.append(f"matches: {', '.join(map(str, indexes))}")
    if snippet:
        fields.append(f"match: {snippet}")
    fields.extend((f"desc: {description}", f"path: {_skill_path(record)}"))
    return _Row(
        "  ".join(fields),
        record.worker_id,
    )


def _search_directory_row(
    entry: DirectoryEntry,
    directory: SkillDirectoryView,
    indexes: tuple[int, ...],
) -> _Row:
    mapping = f"  matches: {', '.join(map(str, indexes))}" if indexes else ""
    description = _directory_description(entry.description, 300)
    count = directory.skill_count(entry.path)
    detail = f"  desc: Contains {count} skill{'s' if count != 1 else ''}. {description}".rstrip()
    return _Row(f"- [category] {_category_from_path(directory, entry.path)}{mapping}{detail}")


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


def _metadata_card(record: SkillRecord) -> str:
    lines = [
        f"# {record.name or record.worker_id}",
        "",
        f"- Description: {record.description or record.name or record.worker_id}",
        f"- Source: {record.source or 'local'}",
    ]
    if record.version:
        lines.append(f"- Version: {record.version}")
    if record.author:
        lines.append(f"- Author: {record.author}")
    return "\n".join(lines)


def _apply_pipeline(
    rows: Sequence[_Row],
    pipeline: list[dict[str, Any]] | None,
    *,
    output_mode: str,
) -> tuple[tuple[_Row, ...], bool, int | None, int | None]:
    mode = _enum(output_mode, _OUTPUT_MODES, "output_mode")
    current = list(rows)
    complete = True
    count_value: int | None = None
    count_seen = False
    if pipeline:
        if not isinstance(pipeline, list):
            raise TypeError("pipeline must be an array")
        current = [_Row(line, row.worker_id) for row in current for line in (row.text.splitlines() or [""])]
        for raw_stage in pipeline:
            if not isinstance(raw_stage, Mapping):
                raise TypeError("pipeline stages must be objects")
            stage = _enum(raw_stage.get("operation"), _PIPELINE_OPERATIONS, "pipeline operation")
            if stage == "limit":
                count_value = None
                limit = _positive(raw_stage.get("lines"), "pipeline lines")
                complete = complete and len(current) <= limit
                current = current[:limit]
            elif stage == "slice":
                count_value = None
                start = _positive(raw_stage.get("start_line"), "pipeline start_line")
                end = _positive(raw_stage.get("end_line"), "pipeline end_line")
                if end < start:
                    raise ValueError("pipeline end_line must be >= start_line")
                complete = complete and start == 1 and end >= len(current)
                current = current[slice(start - 1, end)]
            elif stage == "filter":
                count_value = None
                query = str(raw_stage.get("query") or "")
                if not query:
                    raise ValueError("filter query must be non-empty")
                if len(query) > 512:
                    raise ValueError("filter query must not exceed 512 characters")
                invert = bool(raw_stage.get("invert"))
                case_insensitive = bool(raw_stage.get("case_insensitive"))
                matcher = compile_matcher(
                    query,
                    case_insensitive=case_insensitive,
                    fixed_strings=bool(raw_stage.get("fixed_strings")),
                )

                current = [row for row in current if matcher(row.text) != invert]
            else:
                count_value = _row_count(current)
                count_seen = True
                current = [_Row(str(count_value))]
    if mode == "count":
        count_value = _row_count(current)
        count_seen = True
        current = [_Row(str(count_value))]
    transformed_count = _row_count(current) if count_seen and count_value is None else None
    return tuple(current), complete, count_value, transformed_count


def _fit_rows(
    rows: Sequence[_Row],
    *,
    summary: dict[str, Any],
    budget: int | None,
    count_value: int | None,
    show_shortened_marker: bool = True,
) -> tuple[str, str, list[str], bool, int]:
    header = _bounded_summary_line(summary, budget)
    selected: list[_Row] = []
    shortened = False
    for row in rows:
        body = "\n".join([*(item.text for item in selected), row.text])
        candidate = f"{header}\n\n{body}"
        if budget is not None and len(candidate) > budget:
            shortened = True
            break
        selected.append(row)
    while True:
        shown_count = count_value if count_value is not None and selected else len(selected)
        final_summary = {
            **summary,
            "operation": summary.get("operation"),
            "returned_skill_count": len({row.worker_id for row in selected if row.worker_id}),
            "returned_category_count": sum(row.text.lstrip().startswith("- [category]") for row in selected),
        }
        header = _bounded_summary_line(final_summary, budget)
        body = "\n".join(row.text for row in selected)
        candidate = f"{header}\n\n{body}" if body else header
        if budget is None or len(candidate) <= budget or not selected:
            break
        selected.pop()
        shortened = True
    if shortened and not selected:
        if rows and budget is not None:
            fallback_summary = {
                **summary,
                "operation": summary.get("operation"),
                "returned_skill_count": int(bool(rows[0].worker_id)),
                "returned_category_count": int(rows[0].text.lstrip().startswith("- [category]")),
            }
            header = _bounded_summary_line(fallback_summary, budget)
            available = budget - len(header) - 2
            if available > 3:
                selected.append(_Row(_compact(rows[0].text, available), rows[0].worker_id))
    body_lines = [row.text for row in selected]
    if shortened and show_shortened_marker:
        if budget is None or len("\n".join([header, "", *body_lines, _SHORTENED])) <= budget:
            body_lines.append(_SHORTENED)
    body = "\n".join(body_lines)
    model = f"{header}\n\n{body}" if body else header
    observed = list(dict.fromkeys(row.worker_id for row in selected if row.worker_id))
    shown_count = count_value if count_value is not None and selected else len(selected)
    return body, model, observed, shortened, shown_count


def _row_count(rows: Sequence[_Row]) -> int:
    return sum(max(1, len(row.text.splitlines())) for row in rows)


def _summary_line(summary: Mapping[str, Any]) -> str:
    if summary.get("returned_skill_count") and not summary.get("returned_category_count"):
        return "## Skills — all names previously shown" if summary.get("previously_shown") else "## Skills"
    if summary.get("returned_category_count") and not summary.get("returned_skill_count"):
        return (
            "## Category hints — matching groups, not Skills"
            if summary.get("operation") == "search"
            else "## Categories"
        )
    if summary.get("returned_category_count"):
        return "## Skills and category hints" if summary.get("operation") == "search" else "## Entries"
    if summary.get("operation") == "search":
        return "## Skills"
    return "## Results"


def _bounded_summary_line(payload: Mapping[str, Any], budget: int | None) -> str:
    line = _summary_line(payload)
    return line if budget is None else _compact(line, budget)


def _queries(query: Any, queries: Any) -> tuple[str, ...]:
    if query is not None and queries is not None:
        raise ValueError("query and queries are mutually exclusive")
    if isinstance(query, str) and query.lstrip().startswith("["):
        try:
            decoded = json.loads(query)
        except json.JSONDecodeError:
            pass
        else:
            if isinstance(decoded, list) and all(isinstance(item, str) for item in decoded):
                query = decoded
    batch = query if isinstance(query, list) else queries
    if batch is not None:
        minimum = 1 if isinstance(query, list) else 2
        name = "query" if isinstance(query, list) else "queries"
        if not isinstance(batch, list) or not minimum <= len(batch) <= 8:
            raise ValueError(f"{name} must contain {minimum}-8 strings")
        values = tuple(dict.fromkeys(_bounded_query(item, f"{name} item") for item in batch))
        if len(values) < minimum:
            raise ValueError(f"{name} must contain at least {minimum} distinct value(s)")
        return values
    return (_bounded_query(query, "query"),)


def _cursor_query(arguments: Mapping[str, Any]) -> str | tuple[str, ...] | None:
    if arguments.get("operation") != "search":
        return None
    values = _queries(arguments.get("query"), arguments.get("queries"))
    return values[0] if len(values) == 1 else values


def _apply_simple_defaults(arguments: dict[str, Any], limit: Any) -> None:
    operation = _enum(arguments["operation"], _OPERATIONS, "operation")
    result_limit = _positive(limit, "limit", optional=True)
    result_limit = result_limit or 10
    maximum = 10
    if result_limit > maximum:
        raise ValueError(f"limit must not exceed {maximum} for {operation}")
    if operation == "list":
        if arguments.get("query") is not None or arguments.get("skills") is not None:
            raise ValueError("list accepts only category and limit")
        arguments["view"] = "details"
        arguments["_page_size"] = result_limit if limit is not None else 0
        arguments["_page_offset"] = 0
    elif operation == "search":
        if arguments.get("skills") is not None:
            raise ValueError("skills are valid only for read")
        queries = _queries(arguments.get("query"), None)
        arguments["_page_size"] = (
            result_limit if limit is not None else min(20, _DEFAULT_SEARCH_PAGE_SIZE * len(queries))
        )
        arguments["_page_offset"] = 0
    else:
        if arguments.get("query") is not None:
            raise ValueError("query is valid only for search")


def _category_paths(
    directory: SkillDirectoryView,
    category: Any,
    legacy_paths: Any,
) -> tuple[str, ...]:
    if category is None:
        return _paths(legacy_paths, required=False)
    if legacy_paths is not None:
        raise ValueError("category and paths are mutually exclusive")
    value = _nonempty(category, "category")
    if value in {"/", "ROOT"}:
        return ("/",)

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
    return (matches[0],)


def _validate_request(operation: str, arguments: Mapping[str, Any]) -> list[dict[str, Any]] | None:
    list_fields = (
        arguments.get("view") != "names"
        or arguments.get("recursive") is not False
        or arguments.get("directory_entry") is not False
        or arguments.get("directories_only") is not False
    )
    search_fields = (
        arguments.get("query") is not None
        or arguments.get("queries") is not None
        or arguments.get("per_query_limit") is not None
        or arguments.get("match") != "content"
        or arguments.get("result") != "files"
        or arguments.get("case_insensitive") is not True
        or arguments.get("fixed_strings") is not False
    )
    read_fields = (
        arguments.get("read_mode") != "full"
        or arguments.get("line_count") is not None
        or arguments.get("start_line") is not None
        or arguments.get("end_line") is not None
    )
    if operation != "list" and list_fields:
        raise ValueError("view, recursive, directory_entry, and directories_only are only valid for list")
    if operation != "search" and search_fields:
        raise ValueError("query, queries, per_query_limit, match, result, and search flags are only valid for search")
    if operation != "read" and read_fields:
        raise ValueError("read_mode and line range fields are only valid for read")
    if operation == "read" and arguments.get("skills") is None and arguments.get("paths") is None:
        raise ValueError("skills are required for read")
    if operation != "read" and arguments.get("skills") is not None:
        raise ValueError("skills are valid only for read")
    if operation == "read" and arguments.get("skills") is not None and arguments.get("paths") is not None:
        raise ValueError("skills and paths are mutually exclusive")
    if operation == "list":
        view = _enum(arguments.get("view"), _LIST_VIEWS, "view")
        if view == "tree":
            if arguments.get("recursive") is not False or arguments.get("directory_entry") is not False:
                raise ValueError("recursive and directory_entry are not valid with tree view")
        elif arguments.get("max_depth") is not None or arguments.get("directories_only") is not False:
            raise ValueError("max_depth and directories_only require tree view")
    elif operation == "search":
        match = arguments.get("match")
        if match != "content" and arguments.get("result") != "files":
            raise ValueError("result is only valid for content search")
        if match != "content" and arguments.get("fixed_strings") is not False:
            raise ValueError("fixed_strings is only valid for content search")
    else:
        if arguments.get("max_depth") is not None:
            raise ValueError("max_depth is only valid for list and search")
        read_mode = _enum(arguments.get("read_mode"), _READ_MODES, "read_mode")
        line_count = arguments.get("line_count")
        start_line = arguments.get("start_line")
        end_line = arguments.get("end_line")
        if read_mode == "full" and any(value is not None for value in (line_count, start_line, end_line)):
            raise ValueError("line_count/start_line/end_line require head or range read_mode")
        if read_mode == "head" and (start_line is not None or end_line is not None):
            raise ValueError("start_line/end_line are valid only for range read_mode")
        if read_mode == "range" and line_count is not None:
            raise ValueError("line_count is valid only for head read_mode")
        if read_mode == "range" and (start_line is None or end_line is None):
            raise ValueError("start_line and end_line are required for range read_mode")

    pipeline = arguments.get("pipeline")
    output_mode = _enum(arguments.get("output_mode"), _OUTPUT_MODES, "output_mode")
    if output_mode == "count":
        if operation == "read":
            raise ValueError("count output_mode is valid only for list and search")
        if pipeline:
            raise ValueError("count output_mode cannot be combined with pipeline")
    batch_queries = arguments.get("queries") is not None
    if batch_queries and output_mode != "entries":
        raise ValueError("queries require output_mode=entries")
    if pipeline is None:
        return None
    if not isinstance(pipeline, list):
        raise TypeError("pipeline must be an array")
    allowed_stage_fields = {
        "operation",
        "query",
        "lines",
        "start_line",
        "end_line",
        "case_insensitive",
        "invert",
        "fixed_strings",
    }
    normalized: list[dict[str, Any]] = []
    for index, raw_stage in enumerate(pipeline):
        if not isinstance(raw_stage, Mapping):
            raise TypeError("pipeline stages must be objects")
        unexpected = set(raw_stage) - allowed_stage_fields
        if unexpected:
            raise ValueError(f"pipeline[{index}] has unsupported fields: {', '.join(sorted(unexpected))}")
        stage = _enum(raw_stage.get("operation"), _PIPELINE_OPERATIONS, f"pipeline[{index}].operation")
        query = raw_stage.get("query")
        if query is not None:
            query = _bounded_query(query, f"pipeline[{index}].query")
        lines = _positive(raw_stage.get("lines"), f"pipeline[{index}].lines", optional=True)
        if lines is not None and lines > _MAX_LINES:
            raise ValueError(f"pipeline[{index}].lines must not exceed {_MAX_LINES}")
        start = _positive(raw_stage.get("start_line"), f"pipeline[{index}].start_line", optional=True)
        end = _positive(raw_stage.get("end_line"), f"pipeline[{index}].end_line", optional=True)
        insensitive = _boolean(raw_stage.get("case_insensitive", False), f"pipeline[{index}].case_insensitive")
        invert = _boolean(raw_stage.get("invert", False), f"pipeline[{index}].invert")
        fixed = _boolean(raw_stage.get("fixed_strings", False), f"pipeline[{index}].fixed_strings")
        if stage == "limit":
            if lines is None:
                raise ValueError(f"pipeline[{index}].lines is required for limit")
            if any((query is not None, start is not None, end is not None, insensitive, invert, fixed)):
                raise ValueError(f"pipeline[{index}] has fields not valid for limit")
            normalized.append({"operation": stage, "lines": lines})
        elif stage == "slice":
            if start is None or end is None:
                raise ValueError(f"pipeline[{index}] slice requires start_line and end_line")
            if end < start:
                raise ValueError(f"pipeline[{index}].end_line must be >= start_line")
            if any((query is not None, lines is not None, insensitive, invert, fixed)):
                raise ValueError(f"pipeline[{index}] has fields not valid for slice")
            normalized.append({"operation": stage, "start_line": start, "end_line": end})
        elif stage == "filter":
            if query is None:
                raise ValueError(f"pipeline[{index}].query is required for filter")
            if lines is not None or start is not None or end is not None:
                raise ValueError(f"pipeline[{index}] has fields not valid for filter")
            normalized.append(
                {
                    "operation": stage,
                    "query": query,
                    "case_insensitive": insensitive,
                    "invert": invert,
                    "fixed_strings": fixed,
                }
            )
        else:
            if any(
                (query is not None, lines is not None, start is not None, end is not None, insensitive, invert, fixed)
            ):
                raise ValueError(f"pipeline[{index}] count takes no other fields")
            normalized.append({"operation": stage})
    per_query_limit = arguments.get("per_query_limit")
    if batch_queries:
        if not normalized:
            return None
        if len(normalized) != 1 or normalized[0].get("operation") != "limit":
            raise ValueError("queries support only one limit pipeline stage")
        alias_limit = int(normalized[0]["lines"])
        if alias_limit > 10:
            raise ValueError("pipeline[0].lines must not exceed 10 for queries")
        if per_query_limit not in {None, alias_limit}:
            raise ValueError("per_query_limit conflicts with pipeline limit")
        if isinstance(arguments, dict):
            arguments["per_query_limit"] = alias_limit
        return None
    if per_query_limit is None:
        return normalized
    if not normalized:
        return None
    limit_stages = [stage for stage in normalized if isinstance(stage, Mapping) and stage.get("operation") == "limit"]
    if len(normalized) != 1 or len(limit_stages) != 1 or limit_stages[0].get("lines") != per_query_limit:
        raise ValueError("per_query_limit conflicts with pipeline limit")
    return None


def _literal_match_rank(candidate: str, query: str) -> int:
    if candidate == query:
        return 0
    if candidate.startswith(query):
        return 1
    return 2


def _bounded_query(value: Any, name: str) -> str:
    query = _nonempty(value, name)
    if "\0" in query:
        raise ValueError(f"{name} must not contain a null byte")
    if len(query) > 512:
        raise ValueError(f"{name} must not exceed 512 characters")
    return query


def _paths(value: Any, *, required: bool) -> tuple[str, ...]:
    if value is None:
        if required:
            raise ValueError("paths are required for read")
        return ("/",)
    if not isinstance(value, list) or not value:
        raise ValueError("paths must be a non-empty array")
    if len(value) > 32:
        raise ValueError("paths must contain at most 32 items")
    paths = tuple(dict.fromkeys(_nonempty(item, "path") for item in value))
    if any("\0" in path for path in paths):
        raise ValueError("paths must not contain null bytes")
    if any(len(path) > 4_096 for path in paths):
        raise ValueError("paths must not exceed 4096 characters")
    return paths


def _identifiers(values: Any, name: str, *, required: bool) -> tuple[str, ...]:
    if values is None:
        if required:
            raise ValueError(f"{name} are required")
        return ()
    if not isinstance(values, list) or not values:
        raise ValueError(f"{name} must be a non-empty array")
    if len(values) > 32:
        raise ValueError(f"{name} must contain at most 32 items")
    identifiers = tuple(dict.fromkeys(_nonempty(item, name) for item in values))
    if any("\0" in identifier for identifier in identifiers):
        raise ValueError(f"{name} must not contain null bytes")
    if any(len(identifier) > 512 for identifier in identifiers):
        raise ValueError(f"{name} must not exceed 512 characters")
    return identifiers


def _output_budget(value: Any, *, disable: Any, default: int) -> int | None:
    if not isinstance(disable, bool):
        raise TypeError("disable_output_truncation must be a boolean")
    if disable:
        return None
    if value is None:
        return default
    parsed = _positive(value, "max_output_chars")
    if not _MIN_OUTPUT_CHARS <= parsed <= _MAX_OUTPUT_CHARS:
        raise ValueError("max_output_chars must be between 512 and 48000")
    return parsed


def _enum(value: Any, allowed: Iterable[str], name: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise ValueError(f"{name} must be one of: {', '.join(allowed)}")
    return value


def _positive(value: Any, name: str, *, optional: bool = False) -> int | None:
    if value is None and optional:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _boolean(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"{name} must be a boolean")
    return value


def _nonempty(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _compact(value: str, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else f"{text[: limit - 3].rstrip()}..."


def _append_note(content: str, note: str, budget: int | None) -> str:
    if not content:
        return note if budget is None else _compact(note, budget)
    if budget is None:
        return f"{content}\n\n{note}"
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


__all__ = ["InstalledSkillsDirectoryToolkit", "SKILL_INDEX_TOOL_NAME"]
