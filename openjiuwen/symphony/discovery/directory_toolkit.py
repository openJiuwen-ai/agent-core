"""Structured ``skill_index`` adapter over Symphony's retriever tree."""

from __future__ import annotations

import asyncio
import fnmatch
import hashlib
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
_SKILL_DESCRIPTION_CHARS = 700
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
    scope: tuple[str, ...]
    complete: bool
    query_counts: tuple[int, ...] = ()
    missing_terms: tuple[str, ...] = ()
    note: str = ""


def _tool_card(tool_id: str) -> ToolCard:
    return ToolCard(
        id=tool_id,
        name=SKILL_INDEX_TOOL_NAME,
        description=(
            "Discover installed Skills. `list` the deepest fitting category. `search` once only "
            "if no category fits or a continued list lacks the target; never use variants or "
            "retry. Reuse results. Skip tasks needing no Skill. Load returned names with "
            "`skill_tool` only to execute."
        ),
        input_params={
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": list(_MODEL_OPERATIONS),
                    "description": "List categories or search Skills.",
                },
                "category": {
                    "type": "string",
                    "description": "Full, most specific returned category; omit for all Skills.",
                },
                "query": {
                    "type": "string",
                    "description": "Capability, format, API, or other terms to search for.",
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
        category: str | None = None,
        query: str | None = None,
        skills: list[str] | None = None,
        limit: int | None = None,
        **values: Any,
    ) -> SkillDCICommandResult:
        """Run one serialized structured directory operation."""

        paths = values.pop("paths", None)
        unexpected = set(values).difference(_SKILL_INDEX_DEFAULTS)
        if unexpected:
            name = sorted(unexpected)[0]
            raise TypeError(f"skill_index() got an unexpected keyword argument '{name}'")
        simple_request = paths is None and not values
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
        tool = LocalFunction(
            card=_tool_card(self._tool_id),
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
        view = self._environment.directory
        category = arguments.pop("category", None)
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
        artifact = self._environment.artifact
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
            empty = _empty_message(operation, execution.missing_terms)
            if budget is None:
                fitted = empty
                model_content = f"{model_content}\n{empty}"
            else:
                remaining = budget - len(model_content) - 1
                if remaining > 3:
                    fitted = _compact(empty, remaining)
                    model_content = f"{model_content}\n{fitted}"
        if execution.note:
            fitted = _append_note(fitted, execution.note, budget)
            model_content = _append_note(model_content, execution.note, budget)
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
        rows = tuple(_list_row(entry, view=list_view, directory=view) for entry in entries)
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
        missing_terms: list[str] = []
        order: list[str] = []
        all_matches: set[str] = set()
        for query_index, current_query in enumerate(queries, start=1):
            if match == "content":
                matches, current_missing = self._search_content_one(
                    scoped_records,
                    current_query,
                    case_insensitive=case_insensitive,
                    fixed_strings=fixed_strings,
                )
                missing_terms.extend(current_missing)
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
                    rows.append(_search_directory_row(item, view, indexes))
                else:
                    record = view.record_by_id[item.worker_id]
                    rows.append(_search_row(record, view, indexes))
                continue
            if result == "files":
                rows.append(_search_row(item, view, indexes))
                continue
            matched_queries = tuple(queries[index - 1] for index in indexes) or queries
            snippets = self._matched_snippets(
                item,
                matched_queries,
                match=match,
                case_insensitive=case_insensitive,
                fixed_strings=fixed_strings,
                skill_path=item.skill_file,
            )
            for snippet in snippets:
                rows.append(_search_match_row(item, view, indexes, snippet))
        complete = limit is None or all(count < limit for count in query_counts)
        total_count = len(rows) if result == "matches" else len(all_matches)
        missing = tuple(dict.fromkeys(missing_terms))[:5]
        return _Execution(
            tuple(rows),
            total_count,
            paths,
            complete,
            tuple(query_counts) if len(queries) > 1 else (),
            missing,
            _missing_terms_note(missing) if rows and missing else "",
        )

    def _search_content_one(
        self,
        records: Sequence[SkillRecord],
        query: str,
        *,
        case_insensitive: bool,
        fixed_strings: bool,
    ) -> tuple[tuple[SkillRecord, ...], tuple[str, ...]]:
        identifiers = self._environment.search_content(
            records,
            query,
            case_insensitive=case_insensitive,
            fixed_strings=fixed_strings,
            term_mode=not fixed_strings,
        )
        by_id = {record.worker_id: record for record in records}
        missing = self._environment.missing_content_terms(query) if not fixed_strings else ()
        return tuple(by_id[worker_id] for worker_id in identifiers), missing

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

    def _read(self, view: SkillDirectoryView, skills: tuple[str, ...], arguments: Mapping[str, Any]) -> _Execution:
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
        return _Execution(tuple(rows), len(rows), skills, True)


def _list_row(entry: DirectoryEntry, *, view: str, directory: SkillDirectoryView) -> _Row:
    indent = "  " * entry.depth if view == "tree" else ""
    if entry.kind == "dir":
        description = _directory_description(entry.description, 240)
        detail = f"  desc: {description}" if view != "names" and description else ""
        return _Row(f"{indent}- [category] {_category_from_path(directory, entry.path)}{detail}")
    record = directory.record_by_id[entry.worker_id]
    category = _skill_category(directory, entry.worker_id)
    detail = (
        f"  desc: {_compact(entry.description, _SKILL_DESCRIPTION_CHARS)}"
        if view != "names" and entry.description
        else ""
    )
    return _Row(
        f"{indent}- [skill] {entry.worker_id}  category: {category}  "
        f"path: {_skill_path(record)}{detail}",
        entry.worker_id,
    )


def _search_row(
    record: SkillRecord,
    directory: SkillDirectoryView,
    indexes: tuple[int, ...],
) -> _Row:
    mapping = f"  matches_queries: {list(indexes)}" if indexes else ""
    return _Row(
        f"- [skill] {record.worker_id}  category: {_skill_category(directory, record.worker_id)}  "
        f"path: {_skill_path(record)}{mapping}  "
        f"desc: {_compact(record.description or record.name, _SKILL_DESCRIPTION_CHARS)}",
        record.worker_id,
    )


def _search_match_row(
    record: SkillRecord,
    directory: SkillDirectoryView,
    indexes: tuple[int, ...],
    snippet: str,
) -> _Row:
    mapping = f"  matches_queries: {list(indexes)}" if indexes else ""
    return _Row(
        f"- [skill] {record.worker_id}  category: {_skill_category(directory, record.worker_id)}  "
        f"path: {_skill_path(record)}{mapping}  match: {snippet}  "
        f"desc: {_compact(record.description or record.name, _SKILL_DESCRIPTION_CHARS)}",
        record.worker_id,
    )


def _search_directory_row(
    entry: DirectoryEntry,
    directory: SkillDirectoryView,
    indexes: tuple[int, ...],
) -> _Row:
    mapping = f"  matches_queries: {list(indexes)}" if indexes else ""
    description = _directory_description(entry.description, 300)
    detail = f"  desc: {description}" if description else ""
    return _Row(f"- [category] {_category_from_path(directory, entry.path)}{mapping}{detail}")


def _skill_category(directory: SkillDirectoryView, worker_id: str) -> str:
    path = str(PurePosixPath(directory.record_path_by_id[worker_id]).parent)
    return _category_from_path(directory, path)


def _category_from_path(directory: SkillDirectoryView, path: str) -> str:
    labels: list[str] = []
    while path not in {"", ".", "/"}:
        node = directory.node_by_path.get(path)
        label = node.label if node is not None else PurePosixPath(path).name
        labels.append(sanitize_model_text(label))
        path = str(PurePosixPath(path).parent)
    return " > ".join(reversed(labels)) or "ROOT"


def _skill_path(record: SkillRecord) -> str:
    return sanitize_model_text(record.skill_file)


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


def _summary_line(summary: Mapping[str, Any]) -> str:
    if summary.get("operation") == "list" and not summary.get("result_complete"):
        return "Results continue; search this category once if needed:"
    if summary.get("returned_skill_count"):
        return "Candidates (enough for selection; load with skill_tool only to execute):"
    return "Results:"


def _bounded_summary_line(payload: Mapping[str, Any], budget: int | None) -> str:
    line = _summary_line(payload)
    return line if budget is None else _compact(line, budget)


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


def _apply_simple_defaults(arguments: dict[str, Any], limit: Any) -> None:
    operation = _enum(arguments["operation"], _OPERATIONS, "operation")
    result_limit = _positive(limit, "limit", optional=True)
    result_limit = result_limit or (10 if operation == "list" else 5)
    maximum = 10 if operation == "list" else 5
    if result_limit > maximum:
        raise ValueError(f"limit must not exceed {maximum} for {operation}")
    if operation == "list":
        if arguments.get("query") is not None or arguments.get("skills") is not None:
            raise ValueError("list accepts only category and limit")
        arguments["view"] = "details"
        arguments["pipeline"] = [{"operation": "limit", "lines": result_limit}]
    elif operation == "search":
        if arguments.get("skills") is not None:
            raise ValueError("skills are valid only for read")
        arguments["per_query_limit"] = result_limit
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
        return f"{content}\n{note}"
    remaining = budget - len(content) - 1
    return content if remaining <= 3 else f"{content}\n{_compact(note, remaining)}"


def _candidate_tokens(records: Sequence[SkillRecord]) -> int:
    rendered = "\n".join(f"- {record.worker_id}: {' '.join(record.description.split())}" for record in records)
    return (len(rendered) + 3) // 4


def _empty_message(operation: str, missing_terms: Sequence[str] = ()) -> str:
    if operation != "search":
        return "No entries."
    message = "No matching installed Skill."
    if missing_terms:
        message += f" Metadata lacks: {', '.join(missing_terms[:5])}; do not retry synonyms."
    return message


def _missing_terms_note(missing_terms: Sequence[str]) -> str:
    terms = ", ".join(missing_terms[:5])
    return (
        f"Evidence gap: no installed Skill metadata names {terms}. "
        "More searches cannot prove explicit support."
    )


__all__ = ["InstalledSkillsDirectoryToolkit", "SKILL_INDEX_TOOL_NAME"]
