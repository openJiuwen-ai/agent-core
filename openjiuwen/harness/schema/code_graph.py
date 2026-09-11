# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Request / result protocol and per-run state for Code Graph tools."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal

SCHEMA_VERSION = "1.0"


class CodeGraphProfile(StrEnum):
    """Code Graph capability on the host coding agent.

    ``OFF`` is the original agent (grep / read / edit, no graph tools).
    ``GRAPH`` exposes the find_* retrieval tools on that same agent.
    """

    OFF = "off"
    GRAPH = "graph"


class LocalizationPhase(StrEnum):
    """Sub-phase of Code Graph localization inside a coding agent.

    Submitting context does not end the agent: it moves the run from LOCATING to
    COMMITTED and lets the same agent edit and test. A later refinement returns
    to LOCATING while keeping the same artifact.
    """

    UNBOUND = "unbound"
    LOCATING = "locating"
    COMMITTED = "committed"


# Product graph tools use the find_* name contract.

PROMPT_MODE_PRODUCT = "product"
PROMPT_MODE_LOCATE = "locate"

PROFILE_DEFAULT = CodeGraphProfile.OFF.value

CodeGraphResultStatus = Literal[
    "COMPLETE",
    "PARTIAL",
    "NO_MATCH",
    "STALE",
    "UNAVAILABLE",
    "ERROR",
]


def resolve_code_graph_profile(
    value: Any,
    *,
    default: CodeGraphProfile = CodeGraphProfile.OFF,
) -> CodeGraphProfile:
    """Accept ``off`` / ``graph`` only. Anything else falls back to ``off``."""
    if isinstance(value, CodeGraphProfile):
        return value
    if value is None:
        return default
    if isinstance(value, bool):
        from openjiuwen.core.common.logging import logger

        logger.warning(
            "unknown code_graph profile %r; falling back to %r", value, default.value
        )
        return default
    text = str(value).strip().lower()
    if not text:
        return default
    try:
        return CodeGraphProfile(text)
    except ValueError:
        from openjiuwen.core.common.logging import logger

        logger.warning(
            "unknown code_graph profile %r; falling back to %r", text, default.value
        )
        return default


@dataclass
class CodeGraphRuntime:
    """Public graph handles published on the host coding agent."""

    session_id: str
    repo_root: str
    config: Any
    run_state: Any = None


def bind_code_graph_runtime(
    agent: Any,
    *,
    session_id: str,
    repo_root: str,
    config: Any,
    run_state: Any = None,
) -> CodeGraphRuntime:
    """Attach graph session handles through a public agent attribute."""
    runtime = CodeGraphRuntime(
        session_id=session_id,
        repo_root=repo_root,
        config=config,
        run_state=run_state,
    )
    agent.code_graph_runtime = runtime
    return runtime


@dataclass
class CodeGraphScope:
    include_paths: list[str] = field(default_factory=list)
    exclude_paths: list[str] = field(default_factory=list)


@dataclass
class CodeGraphBudget:
    max_tool_calls: int = 12
    max_locations: int = 20
    max_relation_depth: int = 3


@dataclass(frozen=True)
class GraphQueryPolicy:
    """Per-query bounds for the ``graph`` profile.

    A single query may be truncated or time out; the capability itself is never
    disabled. Stopping the whole task stays with the host agent's iteration,
    token, and time limits, so a locator-style "N graph calls per task" budget
    would only throttle the same run twice.
    """

    default_results: int = 10
    max_results: int = 20
    max_depth: int = 3
    max_nodes: int = 100
    max_paths: int = 20
    timeout_seconds: float = 10.0
    max_payload_chars: int = 30000

    def results(self, requested: Any = None) -> int:
        return self._clamp(requested, self.default_results, self.max_results)

    def depth(self, requested: Any = None) -> int:
        return self._clamp(requested, self.max_depth, self.max_depth)

    def nodes(self, requested: Any = None) -> int:
        return self._clamp(requested, self.max_nodes, self.max_nodes)

    def paths(self, requested: Any = None) -> int:
        return self._clamp(requested, self.max_paths, self.max_paths)

    @staticmethod
    def _clamp(requested: Any, default: int, ceiling: int) -> int:
        try:
            value = int(requested) if requested is not None else default
        except (TypeError, ValueError):
            value = default
        return max(1, min(value, ceiling))


DEFAULT_GRAPH_QUERY_POLICY = GraphQueryPolicy()
FIND_GRAPH_QUERY_POLICY = GraphQueryPolicy(
    default_results=5,
    max_results=10,
    max_paths=5,
    max_nodes=50,
)


@dataclass
class CodeGraphRequest:
    """Structured request bound from the coding task text."""

    query: str
    schema_version: str = SCHEMA_VERSION
    known_symbols: list[str] = field(default_factory=list)
    scope: CodeGraphScope = field(default_factory=CodeGraphScope)
    requested_relations: list[str] = field(default_factory=list)
    hints: dict[str, Any] = field(default_factory=dict)
    budget: CodeGraphBudget = field(default_factory=CodeGraphBudget)

    @classmethod
    def from_mapping(cls, data: dict[str, Any] | None) -> "CodeGraphRequest":
        payload = dict(data or {})
        scope_raw = payload.get("scope") or {}
        budget_raw = payload.get("budget") or {}
        return cls(
            query=str(payload.get("query") or ""),
            schema_version=str(payload.get("schema_version") or SCHEMA_VERSION),
            known_symbols=list(payload.get("known_symbols") or []),
            scope=CodeGraphScope(
                include_paths=list(scope_raw.get("include_paths") or []),
                exclude_paths=list(scope_raw.get("exclude_paths") or []),
            ),
            requested_relations=list(payload.get("requested_relations") or []),
            hints=dict(payload.get("hints") or {}),
            budget=CodeGraphBudget(
                max_tool_calls=int(budget_raw.get("max_tool_calls") or 12),
                max_locations=int(budget_raw.get("max_locations") or 20),
                max_relation_depth=int(budget_raw.get("max_relation_depth") or 3),
            ),
        )


def default_code_graph_budget() -> CodeGraphBudget:
    return CodeGraphBudget()


def parse_code_graph_task(text: str) -> CodeGraphRequest:
    """Parse TaskTool text as JSON ``CodeGraphRequest`` or as a plain query."""
    stripped = str(text or "").strip()
    if stripped.startswith("{"):
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict) and ("query" in data or "schema_version" in data):
            request = CodeGraphRequest.from_mapping(data)
            if "budget" not in data:
                request.budget = default_code_graph_budget()
            return request
    return CodeGraphRequest(query=stripped, budget=default_code_graph_budget())


def bind_code_graph_query(state: "CodeGraphRunState", text: str) -> CodeGraphRequest:
    """Bind task text onto an existing run state, keeping the state's budget.

    A coding agent receives an issue, not a locator request, so only the query,
    hints, and scope come from the text. Replacing the whole request here is
    what silently reset the graph profile's limits to the locator default.
    """
    request = parse_code_graph_task(text)
    request.budget = state.request.budget
    state.request = request
    state.bound = True
    return request


@dataclass
class CodeGraphLocation:
    symbol_id: str
    file: str
    start_line: int
    end_line: int
    reason: str
    confidence: float = 0.0
    name: str = ""
    kind: str = ""
    evidence_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "symbol_id": self.symbol_id,
            "file": self.file,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "reason": self.reason,
            "confidence": self.confidence,
            "name": self.name,
            "kind": self.kind,
        }
        if self.evidence_id:
            payload["evidence_id"] = self.evidence_id
        return payload


@dataclass
class CodeGraphRelation:
    source: str
    relation: str
    target: str

    def to_dict(self) -> dict[str, Any]:
        return {"source": self.source, "relation": self.relation, "target": self.target}


@dataclass
class CodeGraphResult:
    status: CodeGraphResultStatus
    summary: str
    schema_version: str = SCHEMA_VERSION
    locations: list[CodeGraphLocation] = field(default_factory=list)
    relations: list[CodeGraphRelation] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "status": self.status,
            "summary": self.summary,
            "locations": [item.to_dict() for item in self.locations],
            "relations": [item.to_dict() for item in self.relations],
            "open_questions": list(self.open_questions),
            "warnings": list(self.warnings),
            "stats": dict(self.stats),
        }


@dataclass
class CodeGraphRunState:
    """Mutable per-invocation state shared by Code Graph tools."""

    request: CodeGraphRequest = field(default_factory=lambda: CodeGraphRequest(query=""))
    candidates: dict[str, dict[str, Any]] = field(default_factory=dict)
    selected: list[CodeGraphLocation] = field(default_factory=list)
    relations: list[CodeGraphRelation] = field(default_factory=list)
    tool_calls: int = 0
    warnings: list[str] = field(default_factory=list)
    finished: bool = False
    result: CodeGraphResult | None = None
    index_snapshot: str = ""
    artifact_id: str = ""
    session_key: str = ""
    query_hashes: set[str] = field(default_factory=set)
    seen_symbol_ids: set[str] = field(default_factory=set)
    empty_gain_streak: int = 0
    bound: bool = False
    profile: str = PROFILE_DEFAULT
    prompt_mode: str = PROMPT_MODE_PRODUCT
    phase: str = LocalizationPhase.UNBOUND.value
    committed_packets: int = 0
    expanded_files: set[str] = field(default_factory=set)
    probed_inheritance: set[str] = field(default_factory=set)
    read_evidence: dict[str, dict[str, Any]] = field(default_factory=dict)
    seen_files: set[str] = field(default_factory=set)
    search_cache: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Locate-exam only. Keys: extra_read, relation_hop, related_seen.
    submit_nudges: set[str] = field(default_factory=set)

    @property
    def is_locate_exam(self) -> bool:
        """ContextBench locate exam: submit spans, do not pin the next hop."""
        return (self.prompt_mode or PROMPT_MODE_PRODUCT).strip().lower() == PROMPT_MODE_LOCATE

    @property
    def skips_locator_budget(self) -> bool:
        """Graph tools on a coding agent are bounded by the agent loop, not a locator cap."""
        return self.profile == CodeGraphProfile.GRAPH.value

    @property
    def terminal_tool_name(self) -> str:
        """Tool that records selected context for this profile, if any."""
        if self.profile != CodeGraphProfile.GRAPH.value:
            return ""
        mode = (self.prompt_mode or PROMPT_MODE_PRODUCT).strip().lower()
        if mode == PROMPT_MODE_LOCATE:
            return "submit_code_context"
        return "select_code_context"

    def remember_payload(self, payload: dict[str, Any]) -> None:
        """Index tool hits so select_code_context can only keep real evidence."""
        evidence_id = str(payload.get("evidence_id") or "")
        if evidence_id:
            self.read_evidence[evidence_id] = payload
        file_name = str(payload.get("file") or "")
        if file_name:
            self.seen_files.add(file_name.replace("\\", "/"))
        top_id = str(payload.get("symbol_id") or "")
        if top_id and file_name:
            self.candidates[top_id] = payload
        for key in ("matches", "symbols", "related", "chunks", "definitions", "focus"):
            items = payload.get(key) or []
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                item_file = str(item.get("file") or "")
                if item_file:
                    self.seen_files.add(item_file.replace("\\", "/"))
                symbol_id = str(item.get("symbol_id") or item.get("doc_id") or "")
                if not symbol_id:
                    continue
                self.candidates[symbol_id] = item
                if key == "related":
                    self.relations.append(
                        CodeGraphRelation(
                            source=str(item.get("source") or ""),
                            relation=str(item.get("relation") or ""),
                            target=str(item.get("symbol_id") or symbol_id),
                        )
                    )
        snapshot = payload.get("index_snapshot")
        if isinstance(snapshot, str) and snapshot:
            self.index_snapshot = snapshot

    def over_budget(self) -> bool:
        return self.tool_calls > max(1, self.request.budget.max_tool_calls)

    def mark_locating(self) -> None:
        """Enter (or re-enter) localization without dropping the artifact."""
        self.phase = LocalizationPhase.LOCATING.value

    def mark_committed(self) -> None:
        self.phase = LocalizationPhase.COMMITTED.value
        self.committed_packets += 1

    @property
    def context_committed(self) -> bool:
        return self.phase == LocalizationPhase.COMMITTED.value

    def note_search(self, query_hash: str, symbol_ids: list[str]) -> bool:
        """Record a search. Return True if this query hash was already seen."""
        if query_hash in self.query_hashes:
            return True
        self.query_hashes.add(query_hash)
        new_ids = [item for item in symbol_ids if item and item not in self.seen_symbol_ids]
        for item in symbol_ids:
            if item:
                self.seen_symbol_ids.add(item)
        if new_ids:
            self.empty_gain_streak = 0
        else:
            self.empty_gain_streak += 1
        return False

    def diminishing_returns(self, streak: int = 3) -> bool:
        return self.empty_gain_streak >= streak
