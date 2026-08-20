# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Public deterministic Code Graph API (no LLM)."""

from __future__ import annotations

import asyncio
import hashlib
import time
from pathlib import Path
from typing import Sequence

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import retrieval_logger as logger
from openjiuwen.core.retrieval.code_graph.errors import CodeGraphStatus, status_payload
from openjiuwen.core.retrieval.code_graph.indexing.builder import build_index
from openjiuwen.core.retrieval.code_graph.indexing.parser import parser_available, parser_unavailable_reason
from openjiuwen.core.retrieval.code_graph.indexing.refresh import refresh_index_files
from openjiuwen.core.retrieval.code_graph.metrics import record_code_graph_event
from openjiuwen.core.retrieval.code_graph.models import (
    CodeGraphConfig,
    CodeGraphIndex,
    CodeMatch,
    RelatedHit,
)
from openjiuwen.core.retrieval.code_graph.query.expand_file_defs import (
    expand_file_defs as query_expand_file_defs,
)
from openjiuwen.core.retrieval.code_graph.query.expand_inheritance import (
    expand_inheritance as query_expand_inheritance,
)
from openjiuwen.core.retrieval.code_graph.query.analyze_impact import (
    analyze_impact as query_analyze_impact,
)
from openjiuwen.core.retrieval.code_graph.query.expand_related import expand_related as query_expand_related
from openjiuwen.core.retrieval.code_graph.query.failure_path import (
    diagnose_failure_path as query_failure_path,
)
from openjiuwen.core.retrieval.code_graph.query.list_symbols import list_symbols as query_list_symbols
from openjiuwen.core.retrieval.code_graph.query.patch_impact import (
    GraphSlice,
    analyze_patch_impact as query_patch_impact,
    capture_slice,
)
from openjiuwen.core.retrieval.code_graph.query.repo_structure import get_repo_structure as query_repo_structure
from openjiuwen.core.retrieval.code_graph.query.resolve_symbol import (
    resolve_symbol as query_resolve_symbol,
    strip_file_uri,
)
from openjiuwen.core.retrieval.code_graph.query.search_code import search_code as query_search_code
from openjiuwen.core.retrieval.code_graph.query.search_text import (
    corpus_query_stats,
    search_text as query_search_text,
)
from openjiuwen.core.retrieval.code_graph.query.trace_call_chain import (
    TraceLimits,
    trace_call_chain as query_trace_call_chain,
)
from openjiuwen.core.retrieval.code_graph.snapshot import compute_snapshot
from openjiuwen.core.retrieval.code_graph.store.index_store import DiskIndexStore

MAX_READ_LINES = 400
# Neighbouring lines around a definition. The model must not open this to hundreds.
MAX_SYMBOL_CONTEXT = 5
# Class bodies this long are not a locate answer: read members, not the whole class.
LARGE_CLASS_LINES = 80
# How many lines of a large class body to show before pointing at inspect_code_structure.
LARGE_CLASS_PREVIEW_LINES = 40


class CodeGraphService:
    """Query façade over a lazily loaded ``CodeGraphIndex``.

    The service does not scan the repository at construction time. Callers
    invoke ``ensure_ready`` (or any query method) to build or load the index.
    """

    def __init__(
        self,
        repo_root: str | Path,
        config: CodeGraphConfig | None = None,
        *,
        index: CodeGraphIndex | None = None,
    ) -> None:
        self.repo_root = Path(repo_root).resolve()
        self.config = config or CodeGraphConfig()
        self._index = index
        self._snapshot: str | None = index.snapshot if index is not None else None
        self._store = (
            DiskIndexStore(self.config.cache_dir, max_size_mb=self.config.max_index_size_mb)
            if self.config.cache_dir
            else None
        )
        self._ready_lock = asyncio.Lock()
        self._ready_flights: dict[str, asyncio.Future[CodeGraphIndex]] = {}
        # A session index is updated by ``refresh_files`` alone. Letting a moved
        # repository snapshot invalidate it would rebuild the whole repository
        # after every edit and discard the refreshes already applied.
        self._session_scoped = False

    @property
    def available(self) -> bool:
        return parser_available()

    def fork_session(self) -> "CodeGraphService":
        """A service whose index one repair session may refresh in place.

        Incremental refresh mutates the index, and the process-level cache hands
        the same index to every caller for a repo, so a session that edits code
        must not write into the copy another session is querying.
        """
        forked = CodeGraphService(
            self.repo_root,
            self.config,
            index=self._index.copy_for_session() if self._index is not None else None,
        )
        # A session index diverges from what the cache key describes, so it must
        # never be written back to disk as that snapshot's index.
        forked._store = None
        forked._session_scoped = True
        return forked

    def cache_key(self, snapshot: str | None = None) -> str:
        snap = snapshot or self._snapshot or compute_snapshot(self.repo_root)
        return f"{self.repo_root.name}-{snap}-{self.config.config_hash()}"

    def current_snapshot(self) -> str:
        return compute_snapshot(self.repo_root)

    def is_stale(self) -> bool:
        if self._index is None or self._snapshot is None:
            return True
        if self._session_scoped:
            return False
        return self._snapshot != self.current_snapshot()

    async def ensure_ready(self) -> CodeGraphIndex:
        """Load or build the index. One build flight per service+snapshot."""
        if not parser_available():
            raise build_error(
                StatusCode.RETRIEVAL_CODE_GRAPH_INIT_FAILED,
                error_msg=parser_unavailable_reason(),
            )
        self._assert_repo_root()
        started = time.perf_counter()
        if self._session_scoped and self._index is not None:
            self._record_index_event("index_memory", started, self._index, cache_hit=True)
            return self._index
        snapshot = self.current_snapshot()
        if self._index is not None and self._snapshot == snapshot:
            self._record_index_event("index_memory", started, self._index, cache_hit=True)
            return self._index
        async with self._ready_lock:
            if self._index is not None and self._snapshot == snapshot:
                self._record_index_event("index_memory", started, self._index, cache_hit=True)
                return self._index
            flight = self._ready_flights.get(snapshot)
            if flight is None or flight.cancelled():
                flight = asyncio.get_running_loop().create_future()
                self._ready_flights[snapshot] = flight
                asyncio.get_running_loop().create_task(self._run_ready_flight(snapshot, started, flight))
        return await asyncio.shield(flight)

    async def _run_ready_flight(
        self,
        snapshot: str,
        started: float,
        flight: asyncio.Future[CodeGraphIndex],
    ) -> None:
        try:
            index = await self._load_or_build(snapshot, started)
            if not flight.done():
                flight.set_result(index)
        except asyncio.CancelledError:
            if not flight.done():
                flight.cancel()
            raise
        except Exception as exc:
            if not flight.done():
                flight.set_exception(exc)
        finally:
            async with self._ready_lock:
                if self._ready_flights.get(snapshot) is flight:
                    self._ready_flights.pop(snapshot, None)

    async def _load_or_build(self, snapshot: str, started: float) -> CodeGraphIndex:
        if self._index is not None and self._snapshot == snapshot:
            self._record_index_event("index_memory", started, self._index, cache_hit=True)
            return self._index
        if self._store is not None:
            cached = self._store.load(self.cache_key(snapshot))
            if cached is not None and cached.snapshot == snapshot:
                self._index = cached
                self._snapshot = snapshot
                self._record_index_event("index_cache_hit", started, cached, cache_hit=True)
                return cached
        try:
            index = await asyncio.wait_for(
                asyncio.to_thread(build_index, self.repo_root, self.config),
                timeout=self.config.index_timeout_seconds,
            )
        except asyncio.TimeoutError:
            record_code_graph_event(
                "index_build",
                (time.perf_counter() - started) * 1000,
                cache_hit=False,
                status="timeout",
                repo_root=str(self.repo_root),
            )
            raise build_error(
                StatusCode.RETRIEVAL_CODE_GRAPH_TIMEOUT,
                timeout=str(self.config.index_timeout_seconds),
                error_msg=f"indexing {self.repo_root} timed out",
            )
        self._index = index
        self._snapshot = index.snapshot
        if self._store is not None:
            await asyncio.to_thread(self._store.save, self.cache_key(index.snapshot), index)
        self._record_index_event("index_build", started, index, cache_hit=False)
        return index

    async def refresh_files(self, paths: Sequence[str]) -> dict[str, object]:
        """Re-index only ``paths`` so post-edit queries see the new code.

        Without this, the first query after an edit sees a changed repository
        snapshot and rebuilds the whole index, which is why post-edit graph
        analysis was never affordable inside a repair loop.
        """
        started = time.perf_counter()
        try:
            index = await self.ensure_ready()
        except Exception as exc:  # noqa: BLE001
            return self._failure_payload(exc)
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(refresh_index_files, index, list(paths), self.config),
                timeout=self.config.index_timeout_seconds,
            )
        except asyncio.TimeoutError:
            index.stale_files = sorted({*index.stale_files, *paths})
            record_code_graph_event(
                "index_refresh",
                (time.perf_counter() - started) * 1000,
                cache_hit=False,
                status="timeout",
                repo_root=str(self.repo_root),
            )
            return status_payload(
                CodeGraphStatus.PARTIAL,
                message=f"refresh timed out; graph is stale for {len(list(paths))} file(s)",
                extra={"stale": True, "stale_files": list(index.stale_files)},
            )
        except Exception as exc:  # noqa: BLE001
            return self._failure_payload(exc)
        self._snapshot = index.snapshot
        record_code_graph_event(
            "index_refresh",
            (time.perf_counter() - started) * 1000,
            cache_hit=False,
            status="stale" if result.stale else "success",
            repo_root=str(self.repo_root),
        )
        status = CodeGraphStatus.PARTIAL if result.stale else CodeGraphStatus.COMPLETE
        return status_payload(
            status,
            message=(
                f"refreshed {len(result.updated)} file(s), removed {len(result.removed)}"
                + (f"; {len(result.failed)} failed" if result.failed else "")
            ),
            extra={**result.to_dict(), "index_snapshot": index.snapshot},
        )

    async def capture_patch_baseline(self, paths: Sequence[str]) -> GraphSlice:
        """Snapshot the graph around ``paths`` before an edit lands."""
        index = await self.ensure_ready()
        return await asyncio.to_thread(capture_slice, index, list(paths))

    async def analyze_patch_impact(
        self,
        before: GraphSlice,
        *,
        max_depth: int = 2,
        max_nodes: int = 60,
        include_tests: bool = True,
        focus_symbol_ids: Sequence[str] | None = None,
    ) -> dict[str, object]:
        """Diff the current graph against a pre-edit slice."""
        try:
            index = await self._ready_for_query()
        except Exception as exc:  # noqa: BLE001
            return self._failure_payload(exc)
        payload = await self._run_query(
            lambda: query_patch_impact(
                index,
                before,
                max_depth=max_depth,
                max_nodes=max_nodes,
                include_tests=include_tests,
                focus_symbol_ids=focus_symbol_ids,
            ),
            op="analyze_patch_impact",
            extra={
                "files": list(before.files),
                "max_depth": max_depth,
                "focus_symbol_ids": list(focus_symbol_ids or ()),
            },
        )
        return (
            payload
            if isinstance(payload, dict)
            else status_payload(
                CodeGraphStatus.ERROR,
                message="analyze_patch_impact returned an unexpected result",
            )
        )

    async def diagnose_failure_path(
        self,
        output: str,
        changed_files: Sequence[str],
    ) -> dict[str, object]:
        """Say whether a failing run can reach the changed files."""
        try:
            index = await self._ready_for_query()
        except Exception as exc:  # noqa: BLE001
            return self._failure_payload(exc)
        payload = await self._run_query(
            lambda: query_failure_path(index, output, list(changed_files)),
            op="diagnose_failure_path",
            extra={"changed_files": list(changed_files)},
        )
        return (
            payload
            if isinstance(payload, dict)
            else status_payload(
                CodeGraphStatus.ERROR,
                message="diagnose_failure_path returned an unexpected result",
            )
        )

    async def search_code(
        self,
        query: str,
        *,
        symbol_kinds: Sequence[str] | None = None,
        path_prefix: str | None = None,
        limit: int = 20,
        include_tests: bool = False,
    ) -> dict[str, object]:
        """Search definition-like symbols. Returns a structured dict."""
        try:
            index = await self._ready_for_query()
        except Exception as exc:  # noqa: BLE001 — converted to structured status
            return self._failure_payload(exc)
        if path_prefix:
            self.resolve_path(path_prefix)
        ban_tests = bool(self.config.ban_tests) and not include_tests
        matches = await self._run_query(
            lambda: query_search_code(
                index,
                query,
                symbol_kinds=symbol_kinds,
                path_prefix=path_prefix,
                limit=limit,
                ban_tests=ban_tests,
                backend=str(self.config.search_backend or "bm25"),
            ),
            op="search_code",
            extra={"query": query, "path_prefix": path_prefix, "limit": limit},
        )
        if isinstance(matches, dict):
            return matches
        status = CodeGraphStatus.NO_MATCH if not matches else CodeGraphStatus.COMPLETE
        return status_payload(
            status,
            message="no matching symbols" if not matches else f"found {len(matches)} symbols",
            extra={
                "matches": [item.to_dict() for item in matches],
                "index_snapshot": index.snapshot,
            },
        )

    async def resolve_symbol(
        self,
        name: str,
        *,
        kind: str | None = None,
        path_hint: str | None = None,
        limit: int = 8,
    ) -> dict[str, object]:
        """Exact symbol lookup. Does not run BM25."""
        try:
            index = await self._ready_for_query()
        except Exception as exc:  # noqa: BLE001
            return self._failure_payload(exc)
        matches = await self._run_query(
            lambda: query_resolve_symbol(
                index,
                name,
                kind=kind,
                path_hint=path_hint,
                limit=limit,
            ),
            op="resolve_symbol",
            extra={"name": name, "symbol_kind": kind, "path_hint": path_hint},
        )
        if isinstance(matches, dict):
            return matches
        if not matches:
            return status_payload(
                CodeGraphStatus.NO_MATCH,
                message="no exact symbol match; try find_code_symbols",
                extra={"matches": [], "index_snapshot": index.snapshot},
            )
        if len(matches) > 1:
            return status_payload(
                CodeGraphStatus.AMBIGUOUS,
                message=(
                    f"{len(matches)} symbols share this name; pass kind or path_hint, "
                    "or call find_code_symbols"
                ),
                extra={
                    "matches": [item.to_dict() for item in matches],
                    "index_snapshot": index.snapshot,
                },
            )
        hit = matches[0]
        actions: list[dict[str, object]] = [
            {
                "tool": "read_symbol",
                "symbol_id": hit.symbol_id,
                "reason": f"read the definition of {hit.name}",
            }
        ]
        if str(hit.kind).lower() in {"class", "module", "interface"}:
            span = max(0, int(hit.end_line) - int(hit.start_line) + 1)
            if span > LARGE_CLASS_LINES:
                actions.insert(
                    0,
                    {
                        "tool": "inspect_code_structure",
                        "parent_symbol": hit.symbol_id,
                        "reason": (
                            f"{hit.name} spans {span} lines; list methods and "
                            "read only the ones that change"
                        ),
                    },
                )
            actions.append(
                {
                    "tool": "find_importers",
                    "symbol_id": hit.symbol_id,
                    "reason": (
                        f"find modules that import {hit.name} "
                        "(registration, frames, transforms)"
                    ),
                }
            )
        return status_payload(
            CodeGraphStatus.COMPLETE,
            message=f"resolved {hit.name}",
            extra={
                "matches": [hit.to_dict()],
                "symbol_id": hit.symbol_id,
                "name": hit.name,
                "kind": hit.kind,
                "file": hit.file,
                "start_line": hit.start_line,
                "end_line": hit.end_line,
                "qualified_name": hit.qualified_name,
                "index_snapshot": index.snapshot,
                "next_actions": actions,
            },
        )

    async def read_symbol(
        self,
        symbol_id: str,
        *,
        context_before: int = 5,
        context_after: int = 5,
    ) -> dict[str, object]:
        """Read one definition span, plus a few neighbouring lines."""
        try:
            index = await self._ready_for_query()
        except Exception as exc:  # noqa: BLE001
            return self._failure_payload(exc)
        needle = strip_file_uri(symbol_id)
        if not needle:
            return status_payload(
                CodeGraphStatus.ERROR,
                message="symbol_id is required",
            )
        symbol = index.symbols.get(needle)
        if symbol is None:
            resolved = await self._run_query(
                lambda: query_resolve_symbol(index, needle, limit=8),
                op="read_symbol_resolve",
                extra={"symbol_id": needle},
            )
            if isinstance(resolved, dict):
                return resolved
            if len(resolved) == 1:
                symbol = index.symbols.get(resolved[0].symbol_id)
            elif len(resolved) > 1:
                return status_payload(
                    CodeGraphStatus.AMBIGUOUS,
                    message="symbol_id is not unique; pass the full symbol_id from resolve_symbol",
                    extra={"matches": [item.to_dict() for item in resolved]},
                )
        if symbol is None:
            return status_payload(
                CodeGraphStatus.NO_MATCH,
                message=f"unknown symbol_id: {needle}",
                extra={"symbol_id": needle},
            )
        before = min(MAX_SYMBOL_CONTEXT, max(0, int(context_before or 0)))
        after = min(MAX_SYMBOL_CONTEXT, max(0, int(context_after or 0)))
        symbol_start = int(symbol.start_line)
        symbol_end = int(symbol.end_line)
        span = max(1, symbol_end - symbol_start + 1)
        large_class = (
            symbol.kind.value in {"class", "module", "interface"} and span > LARGE_CLASS_LINES
        )
        start = max(1, symbol_start - before)
        if large_class:
            # Do not dump a 1000-line class into the prompt or into submit.
            end = min(symbol_end + after, start + LARGE_CLASS_PREVIEW_LINES - 1)
        else:
            end = symbol_end + after
        payload = await self.read_code(symbol.file, start_line=start, end_line=end)
        if not isinstance(payload, dict):
            return status_payload(
                CodeGraphStatus.ERROR,
                message="read_symbol could not load source",
                extra={"symbol_id": symbol.symbol_id},
            )
        payload["symbol_id"] = symbol.symbol_id
        payload["name"] = symbol.name
        payload["kind"] = symbol.kind.value
        payload["qualified_name"] = symbol.qualified_name or symbol.name
        payload["symbol_start_line"] = symbol_start
        payload["symbol_end_line"] = symbol_end
        payload["context_before"] = before
        payload["context_after"] = after
        if large_class:
            payload["large_class"] = True
            payload["submit"] = None
            payload["next_actions"] = [
                {
                    "tool": "inspect_code_structure",
                    "parent_symbol": symbol.symbol_id,
                    "reason": (
                        f"{symbol.name} is {span} lines; list methods, then "
                        "read_symbol on the ones that change"
                    ),
                },
                {
                    "tool": "find_importers",
                    "symbol_id": symbol.symbol_id,
                    "reason": f"find registration / dependents of {symbol.name}",
                },
            ]
            payload["message"] = (
                f"preview of large {symbol.kind.value} {symbol.name} "
                f"({span} lines); do not submit the whole class"
            )
        else:
            payload["submit"] = {
                "symbol_id": symbol.symbol_id,
                "file": symbol.file,
                "start_line": symbol_start,
                "end_line": symbol_end,
            }
            payload["message"] = f"definition of {symbol.name}"
        return payload

    async def list_symbols(
        self,
        *,
        file: str | None = None,
        parent_symbol: str | None = None,
        kinds: Sequence[str] | None = None,
        depth: int = 1,
        limit: int = 100,
    ) -> dict[str, object]:
        try:
            index = await self._ready_for_query()
        except Exception as exc:  # noqa: BLE001
            return self._failure_payload(exc)
        if file:
            self.resolve_path(file)
        symbols = await self._run_query(
            lambda: query_list_symbols(
                index,
                file=file,
                parent_symbol=parent_symbol,
                kinds=kinds,
                depth=depth,
                limit=limit,
            ),
            op="list_symbols",
            extra={"file": file, "parent_symbol": parent_symbol, "depth": depth, "limit": limit},
        )
        if isinstance(symbols, dict):
            return symbols
        status = CodeGraphStatus.NO_MATCH if not symbols else CodeGraphStatus.COMPLETE
        return status_payload(
            status,
            message="no symbols in scope" if not symbols else f"listed {len(symbols)} symbols",
            extra={
                "symbols": [item.to_dict() for item in symbols],
                "index_snapshot": index.snapshot,
            },
        )

    async def expand_related(
        self,
        symbol_id: str,
        *,
        relations: Sequence[str] | None = None,
        depth: int = 1,
        limit: int = 30,
    ) -> dict[str, object]:
        try:
            index = await self._ready_for_query()
        except Exception as exc:  # noqa: BLE001
            return self._failure_payload(exc)
        hits = await self._run_query(
            lambda: query_expand_related(
                index,
                symbol_id,
                relations=relations,
                depth=depth,
                limit=limit,
            ),
            op="expand_related",
            extra={"symbol_id": symbol_id, "depth": depth, "limit": limit},
        )
        if isinstance(hits, dict):
            return hits
        status = CodeGraphStatus.NO_MATCH if not hits else CodeGraphStatus.COMPLETE
        return status_payload(
            status,
            message="no related symbols" if not hits else f"expanded {len(hits)} related symbols",
            extra={
                "related": [item.to_dict() for item in hits],
                "index_snapshot": index.snapshot,
            },
        )

    async def trace_call_chain(
        self,
        symbol_id: str,
        *,
        direction: str = "both",
        max_depth: int = 3,
        max_paths: int = 20,
        max_nodes: int = 200,
        include_tests: bool = False,
    ) -> dict[str, object]:
        """Walk resolved call edges around ``symbol_id`` in both directions."""
        try:
            index = await self._ready_for_query()
        except Exception as exc:  # noqa: BLE001
            return self._failure_payload(exc)
        limits = TraceLimits(
            max_depth=max_depth,
            max_paths=max_paths,
            max_nodes=max_nodes,
            time_budget_seconds=float(self.config.query_timeout_seconds),
        )
        payload = await self._run_query(
            lambda: query_trace_call_chain(
                index,
                symbol_id,
                direction=direction,
                limits=limits,
                include_tests=include_tests,
            ),
            op="trace_call_chain",
            extra={"symbol_id": symbol_id, "direction": direction, "max_depth": max_depth},
        )
        return (
            payload
            if isinstance(payload, dict)
            else status_payload(
                CodeGraphStatus.ERROR,
                message="trace_call_chain returned an unexpected result",
            )
        )

    async def analyze_impact(
        self,
        symbol_id: str,
        *,
        max_depth: int = 3,
        max_nodes: int = 100,
        include_tests: bool = True,
        relations: Sequence[str] | None = None,
    ) -> dict[str, object]:
        """Group the change surface of ``symbol_id`` by responsibility."""
        try:
            index = await self._ready_for_query()
        except Exception as exc:  # noqa: BLE001
            return self._failure_payload(exc)
        payload = await self._run_query(
            lambda: query_analyze_impact(
                index,
                symbol_id,
                max_depth=max_depth,
                max_nodes=max_nodes,
                include_tests=include_tests,
                relations=relations,
            ),
            op="analyze_impact",
            extra={"symbol_id": symbol_id, "max_depth": max_depth, "max_nodes": max_nodes},
        )
        return (
            payload
            if isinstance(payload, dict)
            else status_payload(
                CodeGraphStatus.ERROR,
                message="analyze_impact returned an unexpected result",
            )
        )

    async def search_text(
        self,
        query: str,
        *,
        path_prefix: str | None = None,
        limit: int = 20,
        include_tests: bool = False,
    ) -> dict[str, object]:
        try:
            index = await self._ready_for_query()
        except Exception as exc:  # noqa: BLE001
            return self._failure_payload(exc)
        if path_prefix:
            self.resolve_path(path_prefix)
        ban_tests = bool(self.config.ban_tests) and not include_tests
        chunks = await self._run_query(
            lambda: query_search_text(
                index,
                query,
                path_prefix=path_prefix,
                limit=limit,
                ban_tests=ban_tests,
            ),
            op="search_text",
            extra={"query": query, "path_prefix": path_prefix, "limit": limit},
        )
        if isinstance(chunks, dict):
            return chunks
        status = CodeGraphStatus.NO_MATCH if not chunks else CodeGraphStatus.COMPLETE
        extra: dict[str, object] = {"chunks": chunks, "index_snapshot": index.snapshot}
        if not chunks:
            stats = corpus_query_stats(index, query)
            extra["corpus"] = stats
            absent = stats.get("tokens_absent") or []
            message = (
                "no matching text or definition chunks; "
                f"corpus has {stats.get('text_docs', 0)} text / "
                f"{stats.get('definition_docs', 0)} definition docs"
            )
            if absent:
                message += f"; tokens absent: {', '.join(str(item) for item in absent[:8])}"
        else:
            message = f"found {len(chunks)} text chunks"
        return status_payload(
            status,
            message=message,
            extra=extra,
        )

    async def get_repo_structure(self, query: str = "", *, limit: int = 40) -> dict[str, object]:
        try:
            index = await self._ready_for_query()
        except Exception as exc:  # noqa: BLE001
            return self._failure_payload(exc)
        payload = await self._run_query(
            lambda: query_repo_structure(
                index,
                query,
                limit=limit,
                ban_tests=bool(self.config.ban_tests),
            ),
            op="get_repo_structure",
            extra={"query": query, "limit": limit},
        )
        if isinstance(payload, dict) and payload.get("status"):
            return payload
        tree = payload if isinstance(payload, dict) else {}
        return status_payload(
            CodeGraphStatus.COMPLETE,
            message="repository structure",
            extra={**tree, "index_snapshot": index.snapshot},
        )

    async def read_code(
        self,
        path: str,
        *,
        start_line: int = 1,
        end_line: int | None = None,
    ) -> dict[str, object]:
        try:
            index = await self._ready_for_query()
        except Exception as exc:  # noqa: BLE001
            return self._failure_payload(exc)
        try:
            resolved = self.resolve_path(path)
        except Exception as exc:  # noqa: BLE001
            return self._failure_payload(exc)
        if not resolved.is_file():
            return status_payload(
                CodeGraphStatus.ERROR,
                message=f"file not found: {path}",
                extra={"file": path},
            )
        try:
            raw = resolved.read_bytes()
        except OSError as exc:
            return status_payload(
                CodeGraphStatus.ERROR,
                message=f"cannot read {path}: {exc}",
                extra={"file": path},
            )
        text = raw.decode("utf-8", errors="replace")
        lines = text.splitlines()
        if not lines:
            return status_payload(
                CodeGraphStatus.ERROR,
                message=f"file is empty: {path}",
                extra={"file": path},
            )
        start = max(1, int(start_line or 1))
        requested_end = int(end_line) if end_line is not None else start + MAX_READ_LINES - 1
        end = min(len(lines), max(start, requested_end))
        if end - start + 1 > MAX_READ_LINES:
            end = start + MAX_READ_LINES - 1
        numbered = "\n".join(f"{idx:>6}|{lines[idx - 1]}" for idx in range(start, end + 1))
        rel = resolved.relative_to(self.repo_root).as_posix()
        digest = hashlib.sha256(raw).hexdigest()[:16]
        evidence_id = f"read:{rel}:{start}:{end}:{digest}"
        stale = self.is_stale()
        status = CodeGraphStatus.STALE if stale else CodeGraphStatus.COMPLETE
        return status_payload(
            status,
            message="source excerpt" if not stale else "index snapshot is stale; source still returned",
            extra={
                "file": rel,
                "start_line": start,
                "end_line": end,
                "content": numbered,
                "evidence_id": evidence_id,
                "content_hash": digest,
                "index_snapshot": index.snapshot,
                "line_count": len(lines),
            },
        )

    async def expand_file_defs(
        self,
        file: str,
        query: str = "",
        *,
        limit: int = 30,
    ) -> dict[str, object]:
        try:
            index = await self._ready_for_query()
        except Exception as exc:  # noqa: BLE001
            return self._failure_payload(exc)
        self.resolve_path(file)
        definitions = await self._run_query(
            lambda: query_expand_file_defs(index, file, query, limit=limit),
            op="expand_file_defs",
            extra={"file": file, "query": query, "limit": limit},
        )
        if isinstance(definitions, dict):
            return definitions
        status = CodeGraphStatus.NO_MATCH if not definitions else CodeGraphStatus.COMPLETE
        return status_payload(
            status,
            message="no definitions in file" if not definitions else f"listed {len(definitions)} definitions",
            extra={
                "file": file.replace("\\", "/").lstrip("./"),
                "definitions": definitions,
                "index_snapshot": index.snapshot,
            },
        )

    async def expand_inheritance(self, symbol_id: str, *, limit: int = 30) -> dict[str, object]:
        try:
            index = await self._ready_for_query()
        except Exception as exc:  # noqa: BLE001
            return self._failure_payload(exc)
        result = await self._run_query(
            lambda: query_expand_inheritance(index, symbol_id, limit=limit),
            op="expand_inheritance",
            extra={"symbol_id": symbol_id, "limit": limit},
        )
        if isinstance(result, dict):
            return result
        klass, related = result if isinstance(result, tuple) else (None, [])
        if klass is None:
            return status_payload(
                CodeGraphStatus.NO_MATCH,
                message=f"no class-like symbol for {symbol_id}",
                extra={"symbol_id": symbol_id, "related": [], "index_snapshot": index.snapshot},
            )
        status = CodeGraphStatus.NO_MATCH if not related else CodeGraphStatus.COMPLETE
        return status_payload(
            status,
            message="no inheritance neighbors" if not related else f"expanded {len(related)} inheritance neighbors",
            extra={
                "symbol_id": klass.symbol_id,
                "name": klass.name,
                "kind": klass.kind.value,
                "file": klass.file,
                "related": related,
                "index_snapshot": index.snapshot,
            },
        )

    def resolve_path(self, path: str) -> Path:
        """Resolve ``path`` under the repo root; reject escapes."""
        root = self.repo_root
        candidate = Path(path)
        resolved = candidate.resolve() if candidate.is_absolute() else (root / path).resolve()
        try:
            resolved.relative_to(root)
        except ValueError:
            raise build_error(
                StatusCode.RETRIEVAL_CODE_GRAPH_PATH_INVALID,
                error_msg=f"path {path!r} is outside repo root {str(root)!r}",
            )
        return resolved

    async def _ready_for_query(self) -> CodeGraphIndex:
        if self.is_stale() and self._index is not None:
            logger.info("code_graph index stale for %s; rebuilding", self.repo_root)
        return await self.ensure_ready()

    async def _run_query(
        self,
        fn,
        *,
        op: str = "query",
        extra: dict[str, object] | None = None,
    ) -> list[CodeMatch] | list[RelatedHit] | dict[str, object]:
        started = time.perf_counter()
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(fn),
                timeout=self.config.query_timeout_seconds,
            )
        except asyncio.TimeoutError:
            record_code_graph_event(
                f"query_{op}",
                (time.perf_counter() - started) * 1000,
                status="timeout",
                **(extra or {}),
            )
            return status_payload(
                CodeGraphStatus.ERROR,
                message=f"query timed out after {self.config.query_timeout_seconds}s",
            )
        count = 0
        status = "ok"
        if isinstance(result, dict):
            status = str(result.get("status") or "ok")
            for key in ("matches", "symbols", "related", "chunks", "definitions", "focus"):
                items = result.get(key)
                if isinstance(items, list):
                    count = len(items)
                    break
        elif isinstance(result, list):
            count = len(result)
        record_code_graph_event(
            f"query_{op}",
            (time.perf_counter() - started) * 1000,
            status=status,
            result_count=count,
            **(extra or {}),
        )
        return result

    def _record_index_event(
        self,
        kind: str,
        started: float,
        index: CodeGraphIndex,
        *,
        cache_hit: bool,
    ) -> None:
        duration_ms = (time.perf_counter() - started) * 1000
        record_code_graph_event(
            kind,
            duration_ms,
            cache_hit=cache_hit,
            repo_root=str(self.repo_root),
            snapshot=index.snapshot,
            file_count=index.file_count,
            symbol_count=len(index.symbols),
            relation_count=len(index.relations),
        )
        logger.info(
            "code_graph %s duration_ms=%.1f files=%s symbols=%s relations=%s cache_hit=%s repo=%s",
            kind,
            duration_ms,
            index.file_count,
            len(index.symbols),
            len(index.relations),
            cache_hit,
            self.repo_root,
        )

    def _assert_repo_root(self) -> None:
        if not self.repo_root.exists() or not self.repo_root.is_dir():
            raise build_error(
                StatusCode.RETRIEVAL_CODE_GRAPH_PATH_INVALID,
                error_msg=f"repo root does not exist: {self.repo_root}",
            )

    def _failure_payload(self, exc: Exception) -> dict[str, object]:
        from openjiuwen.core.common.exception.errors import BaseError

        message = str(exc)
        status = CodeGraphStatus.UNAVAILABLE
        if isinstance(exc, BaseError):
            message = exc.message
            code = exc.status.code if exc.status is not None else None
            if code == StatusCode.RETRIEVAL_CODE_GRAPH_PATH_INVALID.code:
                status = CodeGraphStatus.ERROR
            elif code == StatusCode.RETRIEVAL_CODE_GRAPH_TIMEOUT.code:
                status = CodeGraphStatus.ERROR
        logger.warning("code_graph query failed: %s", message)
        return status_payload(status, message=message)
