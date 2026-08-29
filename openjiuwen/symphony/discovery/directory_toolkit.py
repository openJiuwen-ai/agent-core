"""Structured ``skill_index`` adapter over Symphony's retriever tree."""

from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import json
import re
import weakref
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Iterable, Mapping, Sequence

from openjiuwen.core.foundation.tool import LocalFunction, Tool, ToolCard, ToolExposure
from openjiuwen.symphony.retrieval.search.runtime.lexical import compile_matcher
from .models import SkillRecord, sanitize_model_text
from .skillfs import DirectoryEntry, SkillFS, SkillDirectoryView
from .toolkit import IncrementalNoticeSession, SKILL_INDEX_TOOL_NAME, SkillDCICommandResult


_MIN_OUTPUT_CHARS = 512
_MAX_OUTPUT_CHARS = 48_000
_MAX_LINES = 5_000
_OPERATIONS = ("list", "search", "read")
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
    scope: tuple[str, ...]
    complete: bool
    query_counts: tuple[int, ...] = ()


def _tool_card(tool_id: str, *, default_max_output_chars: int) -> ToolCard:
    return ToolCard(
        id=tool_id,
        name=SKILL_INDEX_TOOL_NAME,
        description=(
            "List, search, and read the read-only directory of installed Skills. "
            "This tool applies only to the Skill catalog, never to project, workspace, "
            "or system files; use filesystem tools or Bash for those. Use `/` for a "
            "catalog-wide operation and only use narrower paths returned by this tool. "
            "For one discovery need, use `search` with `match=content` and one "
            "high-signal `query` for exact formats, libraries, APIs, methods, or "
            "unknown locations. When a request names two or more independent "
            "capability constraints, prefer one `search` with `queries` (one item "
            "per constraint) and `per_query_limit`; do not split those constraints "
            "across calls or follow content results with a provider-wide name "
            "enumeration. In a content query, join "
            "alternative terms with `|` (for example, `youtube|subtitle|字幕|翻译`); "
            "spaces mean consecutive text, not alternative keywords. Use `list` first when a "
            "relevant classification branch is already visible. Do not mechanically "
            "run both. Refine only to close a concrete evidence gap. Selection and "
            "recommendation requests should normally finish with one search and, "
            "only when its descriptions cannot distinguish the candidates, one "
            "batched metadata read (at most 2 calls). Candidate descriptions in "
            "detailed list and content-search "
            "output are selection evidence. When candidates still need comparison, "
            "read all of their metadata paths in one `read` call; do not call once per "
            "candidate. Content search returns readable `META.md` paths; structured "
            "read accepts only exact metadata paths returned by an earlier result and "
            "rejects `SKILL.md`. A false `result_complete` after a limit only means more "
            "catalog matches may exist; it is not a reason to enumerate them when "
            "visible evidence covers the request. Do not read full `SKILL.md` files "
            "during discovery or selection. "
            "Use the exact observed Skill ID with the Skill execution tool only after a "
            "candidate is selected and the user requests execution. Put a `limit` stage "
            "in multi-row discovery calls (5 per independent need by default; use a "
            "larger value only when the user requests broad or exhaustive discovery). "
            "Omit it only when the user explicitly requests every result. Use "
            "`output_mode=count` when only a total is needed; use a pipeline `count` "
            "stage only after another ordered transformation. A line count is a "
            "candidate count only when the source emits one line per candidate. If "
            "output is shortened, answer from "
            "what was returned and do not automatically retry. Only when an explicit "
            "display request is incomplete should you ask after answering whether to "
            "continue, noting that it may use more context. Set "
            "`disable_output_truncation=true` only after the user explicitly permits "
            "output without truncation or collapsing. Arguments are structured fields, "
            "not shell commands or flags."
        ),
        input_params={
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": list(_OPERATIONS),
                    "description": "Required operation: list, search, or read.",
                },
                "paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "description": (
                        "One or more Skill-directory paths. Defaults to [`/`] for list "
                        "and search; required for read. Read accepts only exact `META.md` "
                        "paths returned by an earlier result. Batch related paths in one call."
                    ),
                },
                "view": {
                    "type": "string",
                    "enum": list(_LIST_VIEWS),
                    "default": "names",
                    "description": (
                        "List only: names for a compact listing, details for descriptions, "
                        "or tree for hierarchical navigation."
                    ),
                },
                "recursive": {
                    "type": "boolean",
                    "default": False,
                    "description": "List names/details recursively. Tree is inherently recursive.",
                },
                "directory_entry": {
                    "type": "boolean",
                    "default": False,
                    "description": "List only: describe each path itself rather than its children.",
                },
                "directories_only": {
                    "type": "boolean",
                    "default": False,
                    "description": "Tree view only: omit Skill leaves and show directories.",
                },
                "query": {
                    "type": "string",
                    "description": (
                        "Search query for one discovery need. Use queries instead when "
                        "the request has multiple independent capability constraints. "
                        "Content search accepts a regular expression unless "
                        "fixed_strings is true. Name search accepts a glob; a plain value "
                        "is treated as a substring. For content search, combine alternative "
                        "terms with `|`, for example `youtube|subtitle|字幕|翻译`; spaces "
                        "mean consecutive text."
                    ),
                },
                "queries": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 2,
                    "maxItems": 8,
                    "description": (
                        "Search only: preferred for 2-8 independent capability "
                        "constraints. Put one constraint in each item so one call "
                        "replaces sequential searches. Results annotate each deduplicated "
                        "candidate with the matching 1-based query indexes. Mutually "
                        "exclusive with query."
                    ),
                },
                "per_query_limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 10,
                    "description": (
                        "Search only: maximum candidates retained for query, or per need "
                        "for queries. Use 5 for ordinary discovery; use 6-10 only for an "
                        "explicitly broad or exhaustive request. Equivalent to a limit "
                        "pipeline stage."
                    ),
                },
                "match": {
                    "type": "string",
                    "enum": list(_SEARCH_MATCHES),
                    "default": "content",
                    "description": ("Search content with ranked retrieval, or glob-match entry names or full paths."),
                },
                "result": {
                    "type": "string",
                    "enum": list(_SEARCH_RESULTS),
                    "default": "files",
                    "description": ("Content search only: return matching candidate files or matching text snippets."),
                },
                "case_insensitive": {
                    "type": "boolean",
                    "default": True,
                    "description": "Search without case sensitivity by default.",
                },
                "fixed_strings": {
                    "type": "boolean",
                    "default": False,
                    "description": "Content search only: interpret query as literal text.",
                },
                "max_depth": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Optional depth bound for tree view or search.",
                },
                "read_mode": {
                    "type": "string",
                    "enum": list(_READ_MODES),
                    "default": "full",
                    "description": "Read full files, their first lines, or an inclusive line range.",
                },
                "line_count": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Read head only: number of leading lines; defaults to 10.",
                },
                "start_line": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Read range only: inclusive first line.",
                },
                "end_line": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Read range only: inclusive last line.",
                },
                "pipeline": {
                    "type": "array",
                    "description": (
                        "Optional ordered output stages. limit uses lines; slice uses "
                        "start_line/end_line; filter uses query and optional matching "
                        "booleans; count takes no other fields."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "operation": {"type": "string", "enum": list(_PIPELINE_OPERATIONS)},
                            "query": {"type": "string"},
                            "lines": {"type": "integer", "minimum": 1, "maximum": _MAX_LINES},
                            "start_line": {"type": "integer", "minimum": 1},
                            "end_line": {"type": "integer", "minimum": 1},
                            "case_insensitive": {"type": "boolean", "default": False},
                            "invert": {"type": "boolean", "default": False},
                            "fixed_strings": {"type": "boolean", "default": False},
                        },
                        "required": ["operation"],
                        "additionalProperties": False,
                    },
                },
                "output_mode": {
                    "type": "string",
                    "enum": list(_OUTPUT_MODES),
                    "default": "entries",
                    "description": (
                        "Return entries, or only their logical line count. Count is an "
                        "output mode of list/search, never an operation. To obtain both "
                        "a total and a bounded page, make count and limited-entry calls "
                        "independently; a line count is a candidate count only when the "
                        "selected source emits one line per candidate."
                    ),
                },
                "max_output_chars": {
                    "type": "integer",
                    "minimum": _MIN_OUTPUT_CHARS,
                    "maximum": _MAX_OUTPUT_CHARS,
                    "description": (
                        "Maximum output characters. Omit to use "
                        f"{default_max_output_chars}; use a limit stage to bound rows."
                    ),
                },
                "disable_output_truncation": {
                    "type": "boolean",
                    "default": False,
                    "description": (
                        "Bypass normal character truncation. Set true only after explicit "
                        "user permission for output without truncation or collapsing."
                    ),
                },
            },
            "required": ["operation"],
            "additionalProperties": False,
        },
        exposure=ToolExposure.DIRECT,
        parallel_safe=False,
        stateless=False,
    )


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
        paths: list[str] | None = None,
        **values: Any,
    ) -> SkillDCICommandResult:
        """Run one serialized structured directory operation."""

        unexpected = set(values).difference(_SKILL_INDEX_DEFAULTS)
        if unexpected:
            name = sorted(unexpected)[0]
            raise TypeError(f"skill_index() got an unexpected keyword argument '{name}'")
        arguments = {"operation": operation, "paths": paths, **_SKILL_INDEX_DEFAULTS, **values}
        async with self._lock:
            if self._closed:
                raise RuntimeError("skill_index toolkit is closed")
            return await asyncio.to_thread(self._execute, arguments)

    def get_tools(self) -> list[Tool]:
        tool = LocalFunction(
            card=_tool_card(self._tool_id, default_max_output_chars=self._default_max_output_chars),
            func=self.skill_index,
        )
        weakref.finalize(tool, self.close)
        return [tool]

    async def aclose(self) -> None:
        async with self._lock:
            self._closed = True

    def close(self) -> None:
        self._closed = True

    def _execute(self, arguments: dict[str, Any]) -> SkillDCICommandResult:
        operation = _enum(arguments["operation"], _OPERATIONS, "operation")
        pipeline = _validate_request(operation, arguments)
        paths = _paths(arguments["paths"], required=operation == "read")
        budget = _output_budget(
            arguments["max_output_chars"],
            disable=arguments["disable_output_truncation"],
            default=self._default_max_output_chars,
        )
        view = self._environment.directory
        artifact = self._environment.artifact
        if operation == "list":
            execution = self._list(view, paths, arguments)
        elif operation == "search":
            execution = self._search(view, paths, arguments)
        else:
            execution = self._read(view, paths, arguments)

        rows, pipeline_complete, count_value, transformed_count = _apply_pipeline(
            execution.rows,
            pipeline,
            output_mode=arguments["output_mode"],
        )
        complete = execution.complete and pipeline_complete
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
            "catalog_skill_count": len(artifact.items),
            "scope": [_compact(path, 160) for path in execution.scope[:2]],
            "scope_count": len(execution.scope),
            "scope_complete": True,
            "result_complete": complete,
            "result_entry_count": result_entry_count,
            "returned_skill_count": len({row.worker_id for row in rows if row.worker_id}),
        }
        if execution.query_counts:
            summary.update(
                query_count=len(execution.query_counts),
                per_query_returned_counts=list(execution.query_counts),
            )
        fitted, model_content, observed, shortened, shown_count = _fit_rows(
            rows,
            summary=summary,
            budget=budget,
            count_value=count_value,
        )
        if not fitted and not pipeline:
            empty = _empty_message(operation)
            if budget is None:
                fitted = empty
                model_content = f"{model_content}\n{empty}"
            else:
                remaining = budget - len(model_content) - 1
                if remaining > 3:
                    fitted = _compact(empty, remaining)
                    model_content = f"{model_content}\n{fitted}"
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
        rows = tuple(_list_row(entry, view=list_view) for entry in entries)
        return _Execution(rows, len(entries), paths, True)

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
        if len(queries) > 1:
            limit = per_query_limit or 5
        else:
            limit = per_query_limit
        selected_by_id: dict[str, SkillRecord | DirectoryEntry] = {}
        query_indexes_by_id: dict[str, tuple[int, ...]] = {}
        query_counts: list[int] = []
        order: list[str] = []
        all_matches: set[str] = set()
        for query_index, current_query in enumerate(queries, start=1):
            matches: Sequence[SkillRecord | DirectoryEntry] = (
                self._search_content_one(
                    scoped_records,
                    current_query,
                    case_insensitive=case_insensitive,
                    fixed_strings=fixed_strings,
                )
                if match == "content"
                else self._search_entry_one(
                    view,
                    scoped_entries,
                    current_query,
                    match=match,
                    case_insensitive=case_insensitive,
                )
            )
            identities = tuple(_search_identity(item) for item in matches)
            all_matches.update(identities)
            selected = matches if limit is None else matches[:limit]
            query_counts.append(len(selected))
            for item in selected:
                identity = _search_identity(item)
                if identity not in selected_by_id:
                    order.append(identity)
                    selected_by_id[identity] = item
                    query_indexes_by_id[identity] = ()
                if len(queries) > 1:
                    query_indexes_by_id[identity] = (*query_indexes_by_id[identity], query_index)

        rows: list[_Row] = []
        for identity in order:
            item = selected_by_id[identity]
            indexes = query_indexes_by_id[identity]
            if isinstance(item, DirectoryEntry):
                if item.kind == "dir":
                    rows.append(_search_directory_row(item, indexes))
                else:
                    record = view.record_by_id[item.worker_id]
                    rows.append(_search_row(record, view.metadata_path(item.worker_id), indexes))
                continue
            metadata_path = view.metadata_path(item.worker_id)
            if result == "files":
                rows.append(_search_row(item, metadata_path, indexes))
                continue
            matched_queries = tuple(queries[index - 1] for index in indexes) or queries
            snippets = self._matched_snippets(
                item,
                matched_queries,
                match=match,
                case_insensitive=case_insensitive,
                fixed_strings=fixed_strings,
                metadata_path=metadata_path,
            )
            for snippet in snippets:
                rows.append(_search_match_row(item, metadata_path, indexes, snippet))
        complete = limit is None or all(count < limit for count in query_counts)
        total_count = len(rows) if result == "matches" else len(all_matches)
        return _Execution(
            tuple(rows),
            total_count,
            paths,
            complete,
            tuple(query_counts) if len(queries) > 1 else (),
        )

    def _search_content_one(
        self,
        records: Sequence[SkillRecord],
        query: str,
        *,
        case_insensitive: bool,
        fixed_strings: bool,
    ) -> tuple[SkillRecord, ...]:
        identifiers = self._environment.search_content(
            records,
            query,
            case_insensitive=case_insensitive,
            fixed_strings=fixed_strings,
        )
        if not identifiers and not fixed_strings:
            fallback = _safe_or_query(query)
            if fallback:
                identifiers = self._environment.search_content(
                    records,
                    fallback,
                    case_insensitive=case_insensitive,
                    fixed_strings=False,
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
                metadata_path = view.metadata_path(entry.worker_id)
                candidates = (
                    (PurePosixPath(entry.path).name, record.worker_id, record.name)
                    if match == "name"
                    else (metadata_path,)
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
        metadata_path: str,
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
            ("description", record.description),
            ("body", self._environment.read_body(record)),
            ("path", metadata_path),
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

    def _read(self, view: SkillDirectoryView, paths: tuple[str, ...], arguments: Mapping[str, Any]) -> _Execution:
        mode = _enum(arguments["read_mode"], _READ_MODES, "read_mode")
        rows: list[_Row] = []
        for path in paths:
            normalized = view.normalize_path(path)
            observed_worker_id = self._observed_meta_paths.get(normalized)
            if observed_worker_id is None:
                raise ValueError("read accepts only exact META.md paths returned by an earlier skill_index result")
            record = view.resolve_metadata_path(normalized)
            if record.worker_id != observed_worker_id:
                raise ValueError("observed Skill metadata path no longer resolves to the same Skill")
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
        return _Execution(tuple(rows), len(rows), paths, True)


def _list_row(entry: DirectoryEntry, *, view: str) -> _Row:
    indent = "  " * entry.depth if view == "tree" else ""
    if entry.kind == "dir":
        detail = f"  desc: {_compact(entry.description, 240)}" if view != "names" and entry.description else ""
        display_path = "/" if entry.path == "/" else f"{entry.path}/"
        return _Row(f"{indent}[dir] {display_path}{detail}")
    meta_path = f"{entry.path}/META.md"
    detail = f"  desc: {_compact(entry.description, 240)}" if view != "names" and entry.description else ""
    return _Row(f"{indent}[skill] {meta_path}{detail}", entry.worker_id)


def _search_row(
    record: SkillRecord,
    path: str,
    indexes: tuple[int, ...],
) -> _Row:
    mapping = f"  matches_queries: {list(indexes)}" if indexes else ""
    return _Row(
        f"[skill] {path}{mapping}  desc: {_compact(record.description or record.name, 300)}",
        record.worker_id,
    )


def _search_match_row(
    record: SkillRecord,
    path: str,
    indexes: tuple[int, ...],
    snippet: str,
) -> _Row:
    mapping = f"  matches_queries: {list(indexes)}" if indexes else ""
    return _Row(
        f"[skill] {path}{mapping}  match: {snippet}  desc: {_compact(record.description or record.name, 300)}",
        record.worker_id,
    )


def _search_directory_row(entry: DirectoryEntry, indexes: tuple[int, ...]) -> _Row:
    mapping = f"  matches_queries: {list(indexes)}" if indexes else ""
    detail = f"  desc: {_compact(entry.description, 300)}" if entry.description else ""
    path = "/" if entry.path == "/" else f"{entry.path}/"
    return _Row(f"[dir] {path}{mapping}{detail}")


def _search_identity(item: SkillRecord | DirectoryEntry) -> str:
    if isinstance(item, SkillRecord):
        return f"skill\0{item.worker_id}"
    return f"{item.kind}\0{item.path}"


def _metadata_card(record: SkillRecord) -> str:
    lines = [
        f"# {record.name or record.worker_id}",
        "",
        f"- Skill ID: `{record.worker_id}`",
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
) -> tuple[str, str, list[str], bool, int]:
    total_count = max(0, int(summary.get("result_entry_count", len(rows))))
    header = _bounded_summary_line(summary, budget)
    selected: list[_Row] = []
    shortened = False
    for row in rows:
        candidate = "\n".join([header, *(item.text for item in selected), row.text])
        if budget is not None and len(candidate) > budget:
            shortened = True
            break
        selected.append(row)
    while True:
        shown_count = count_value if count_value is not None and selected else len(selected)
        final_summary = {
            **summary,
            "result_complete": bool(summary.get("result_complete")) and not shortened,
            "shown_entry_count": shown_count,
            "remaining_entry_count": max(0, total_count - shown_count),
            "returned_skill_count": len({row.worker_id for row in selected if row.worker_id}),
        }
        if shortened:
            final_summary["body_shortened"] = True
        header = _bounded_summary_line(final_summary, budget)
        candidate = "\n".join([header, *(row.text for row in selected)])
        if budget is None or len(candidate) <= budget or not selected:
            break
        selected.pop()
        shortened = True
    if shortened and not selected:
        if rows and budget is not None:
            fallback_summary = {
                **summary,
                "body_shortened": True,
                "result_complete": False,
                "shown_entry_count": 1,
                "remaining_entry_count": max(0, total_count - 1),
                "returned_skill_count": int(bool(rows[0].worker_id)),
            }
            header = _bounded_summary_line(fallback_summary, budget)
            available = budget - len(header) - 1
            if available > 3:
                selected.append(_Row(_compact(rows[0].text, available), rows[0].worker_id))
    body_lines = [row.text for row in selected]
    if shortened and (budget is None or len("\n".join([header, *body_lines, _SHORTENED])) <= budget):
        body_lines.append(_SHORTENED)
    body = "\n".join(body_lines)
    model = f"{header}\n{body}" if body else header
    observed = list(dict.fromkeys(row.worker_id for row in selected if row.worker_id))
    shown_count = count_value if count_value is not None and selected else len(selected)
    return body, model, observed, shortened, shown_count


def _row_count(rows: Sequence[_Row]) -> int:
    return sum(max(1, len(row.text.splitlines())) for row in rows)


def _summary_line(payload: Mapping[str, Any]) -> str:
    return "skill_index_summary=" + json.dumps(dict(payload), ensure_ascii=False, separators=(",", ":"))


def _bounded_summary_line(payload: Mapping[str, Any], budget: int | None) -> str:
    line = _summary_line(payload)
    if budget is None or len(line) <= budget:
        return line
    reduced = dict(payload)
    reduced.pop("scope", None)
    line = _summary_line(reduced)
    if len(line) <= budget:
        return line
    essential: dict[str, Any] = {}
    for key in (
        "operation",
        "catalog_skill_count",
        "result_complete",
        "result_entry_count",
        "shown_entry_count",
        "remaining_entry_count",
    ):
        if key in reduced:
            essential[key] = reduced[key]
    return _summary_line(essential)


def _queries(query: Any, queries: Any) -> tuple[str, ...]:
    if query is not None and queries is not None:
        raise ValueError("query and queries are mutually exclusive")
    if queries is not None:
        if not isinstance(queries, list) or not 2 <= len(queries) <= 8:
            raise ValueError("queries must contain 2-8 strings")
        values = tuple(dict.fromkeys(_bounded_query(item, "queries item") for item in queries))
        if len(values) < 2:
            raise ValueError("queries must contain at least two distinct values")
        return values
    return (_bounded_query(query, "query"),)


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


def _safe_or_query(query: str) -> str | None:
    tokens = query.split()
    if not 2 <= len(tokens) <= 12 or "|" in query:
        return None
    if any(len(token) > 64 or not any(character.isalnum() for character in token) for token in tokens):
        return None
    return "|".join(re.escape(token) for token in tokens)


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


def _candidate_tokens(records: Sequence[SkillRecord]) -> int:
    rendered = "\n".join(f"- {record.worker_id}: {' '.join(record.description.split())}" for record in records)
    return (len(rendered) + 3) // 4


def _empty_message(operation: str) -> str:
    return "No Skill candidates matched the requested catalog scope." if operation == "search" else "No entries."


__all__ = ["InstalledSkillsDirectoryToolkit", "SKILL_INDEX_TOOL_NAME"]
