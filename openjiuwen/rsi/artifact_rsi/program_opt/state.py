# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Nine search events into three task events, and onto disk on the way.

The contract asks two things of a provider that the search does not do by
itself. It must speak `status` / `progress` / `node`, where the search speaks in
selections, expansions, evaluations and merges. And it must answer `read_state`,
`read_report` and `get_tree` after a restart, where the search only streams.

Both are answered here, in that order: **persist, then emit**. A consumer told
about a node it cannot then read back is worse than one told a moment later.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

from openjiuwen.rsi.events import EngineEvent, EventNode, EventProgress
from openjiuwen.rsi.schema import (
    ArtifactRef,
    EngineReport,
    EngineState,
    RsiChange,
    RsiStatus,
    RsiTreeNode,
    TreeResponse,
)

STATE_FILE = "state.json"
REPORT_FILE = "report.json"
NODES_FILE = "nodes.json"

#: `run_dir` for a task, remembered so the read-only queries can find it.
#:
#: The contract hands `run_dir` to `run` but not to `read_state`, `read_report`
#: or `get_tree` — they get a `task_id` and nothing else. Rather than invent a
#: directory layout AgentServer does not know about, each run records where it
#: put itself, and the queries look it up.
_DIRECTORIES: dict[str, Path] = {}


def register_run_dir(task_id: str, run_dir: Path) -> None:
    _DIRECTORIES[task_id] = run_dir


def run_dir_for(task_id: str) -> Optional[Path]:
    directory = _DIRECTORIES.get(task_id)
    if directory is not None:
        return directory
    # A restart empties the map. `SCIENCE_AGENT_RSI_RUNS` names the parent every
    # task directory sits under, which is what lets a query answer after one.
    root = os.environ.get("SCIENCE_AGENT_RSI_RUNS")
    if root:
        candidate = Path(root) / task_id
        if candidate.is_dir():
            return candidate
    return None


def node_id_for(task_id: str, index: int) -> str:
    """A node's stable id.

    The search numbers nodes by insertion and the contract wants a stable
    string. Built from the two together so the same node keeps the same id
    across events, `get_tree` and the report — which is the contract's own
    requirement — and so an id from one task can never name a node in another.
    """
    return f"artifact:{task_id}:node:{index}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _write_atomically(path: Path, payload: Any) -> None:
    """Replace a snapshot without ever showing half of one.

    Readers arrive whenever they like — AgentServer answering a query, a person
    looking at the directory — and a truncated file is worse than a stale one.
    Never raises: a snapshot is a convenience, and a full disk must not end a
    task that is otherwise working.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, default=_plain), encoding="utf-8")
        os.replace(temporary, path)
    except Exception:  # noqa: BLE001 - never worth failing a task over
        pass


def _plain(value: Any) -> Any:
    if hasattr(value, "__dataclass_fields__"):
        return {name: getattr(value, name) for name in value.__dataclass_fields__}
    return str(value)


@dataclass
class ProgramRunState:
    """One task's durable state, and the projection that keeps it current."""

    task_id: str
    run_dir: Path
    total_iterations: int
    status: RsiStatus = "created"
    iteration: int = 0
    score: Optional[float] = None
    baseline: Optional[float] = None
    best_node_id: Optional[str] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    #: `node index -> the node as the contract wants it`. Held whole because a
    #: node arrives in three parts and only the last one completes it.
    nodes: dict[int, RsiTreeNode] = field(default_factory=dict)
    artifacts: dict[str, ArtifactRef] = field(default_factory=dict)
    #: What a search that stopped at a node boundary folds to. `terminate` and
    #: `pause` share the whole stop mechanism — the same flag, polled at the
    #: same boundary — and differ only here: `terminated` is a terminal status
    #: and `paused` is the one `resume` accepts.
    stopped_status: str = "terminated" 
    _summary: Optional[str] = None

    def __post_init__(self) -> None:
        register_run_dir(self.task_id, self.run_dir)

    # -- lifecycle -------------------------------------------------------------

    def start(self) -> None:
        self.status = "running"
        self._persist()

    def fail(self, code: str, message: str) -> None:
        self.status = "failed"
        self.error_code = code
        self.error_message = message
        self._persist()

    def finish(self) -> None:
        if self.status not in ("failed", "terminated", "paused"):
            self.status = "completed"
        self._persist()

    # -- the projection --------------------------------------------------------

    def absorb(self, record: dict[str, Any]) -> Iterator[EngineEvent]:
        """One search event in, zero or more contract events out.

        Zero is the common case: `selected` and `evaluated` change a node that is
        not finished yet, and emitting a partial node would break the contract's
        rule that `EventNode` carries a complete one.
        """
        event = record.get("event") or {}
        kind = event.get("type")

        if kind == "seeded":
            yield from self._seeded(event)
        elif kind == "selected":
            self._selected(event)
        elif kind == "expanded":
            self._expanded(event)
        elif kind == "evaluated":
            self._evaluated(event)
        elif kind == "merged":
            yield from self._merged(event)
        elif kind == "search_finished":
            self._finished(event)

    def _program_extra(
        self,
        *,
        logical_kind: str,
        candidate_index: Optional[int],
        parent_index: Optional[int],
        artifact_id: Optional[str],
        evaluation: Optional[dict[str, Any]],
        error: Optional[dict[str, Any]] = None,
        puct: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """`extra["program"]`, in exactly the contract's nine keys.

        Every key is always present — the contract's tables define null
        semantics per field, and a reader that must first probe which keys
        exist has been handed a different structure per node.
        """
        ref = self.artifacts.get(artifact_id or "")
        return {"program": {
            "logical_kind": logical_kind,
            "candidate_index": candidate_index,
            "source_ref": ref.sha256 and f"sha256:{ref.sha256}" if ref else None,
            "program_path": ref.path if ref else None,
            "parent_index": parent_index,
            "evaluation": evaluation,
            "puct": puct,
            "artifacts": [ref] if ref else [],
            "error": error,
        }}

    @staticmethod
    def _update_program(node: RsiTreeNode, **changes: Any) -> dict[str, Any]:
        extra = dict(node.extra)
        program = dict(extra.get("program") or {})
        program.update(changes)
        extra["program"] = program
        return extra

    def _seeded(self, event: dict[str, Any]) -> Iterator[EngineEvent]:
        self.baseline = event.get("baselineScore")
        self.score = self.baseline
        node = RsiTreeNode(
            node_id=node_id_for(self.task_id, 0),
            iteration=0,
            parent_id=None,
            type="root",
            adopted=True,
            score=self.baseline,
            summary="the starting program",
            snapshot_artifact_id=(seed_artifact := self._artifact(0, event.get("codeHash"))),
            reason=None,
            failure_class=None,
            changes=[],
            extra=self._program_extra(
                logical_kind="root", candidate_index=None, parent_index=None,
                artifact_id=seed_artifact,
                evaluation={"valid": True, "gate": self.baseline},
            ),
        )
        self.nodes[0] = node
        self.best_node_id = node.node_id
        self._persist()
        yield EventNode(node=node)

    def _selected(self, event: dict[str, Any]) -> None:
        """Visit counts and selection scores, filed under `extra["program"].puct`.

        Quiet but durable: no node event goes out — a selection changes no
        node's identity — yet the update is persisted, because `tree.get`
        reads the file, and an in-memory-only visit count would leave the
        contract's "get and delta use the same node" true in shape but stale
        in content until the next merge happened to flush it.
        """
        for entry in event.get("ancestorVisits") or []:
            node = self.nodes.get(int(entry.get("nodeIndex", -1)))
            if node is None:
                continue
            puct = dict((node.extra.get("program") or {}).get("puct") or {})
            puct["visits"] = int(entry.get("visits") or 0)
            self.nodes[int(entry["nodeIndex"])] = _with(
                node, extra=self._update_program(node, puct=puct))
        chosen = self.nodes.get(int(event.get("nodeIndex", -1)))
        if chosen is not None:
            puct = dict((chosen.extra.get("program") or {}).get("puct") or {})
            for key, field_name in (("rank", "rankScore"), ("value", "puct")):
                if event.get(field_name) is not None:
                    puct[key] = event.get(field_name)
            if puct:
                self.nodes[int(event.get("nodeIndex", 0))] = _with(
                    chosen, extra=self._update_program(chosen, puct=puct))
        self._persist()

    def _expanded(self, event: dict[str, Any]) -> None:
        index = int(event.get("nodeIndex", 0))
        parent = event.get("parentIndex")
        valid = bool(event.get("valid"))
        failure_class = None if valid else classify_failure(event.get("error"))
        artifact_id = self._artifact(index, event.get("codeHash"))
        promise = event.get("promise")
        self.nodes[index] = RsiTreeNode(
            node_id=node_id_for(self.task_id, index),
            iteration=int(event.get("iteration") or index),
            parent_id=None if parent is None else node_id_for(self.task_id, int(parent)),
            type="candidate",
            adopted=False,
            score=event.get("score"),
            summary=event.get("changeSummary"),
            snapshot_artifact_id=artifact_id,
            reason=event.get("error"),
            failure_class=failure_class,
            changes=_changes_from(event.get("changeSummary")),
            extra=self._program_extra(
                logical_kind="candidate",
                candidate_index=index,
                parent_index=None if parent is None else int(parent),
                artifact_id=artifact_id,
                evaluation={"valid": valid, "score": event.get("score")},
                error=None if valid else {
                    "message": event.get("error"), "class": failure_class,
                },
                # The model's own rating of its candidate feeds the search's
                # selection prior, which is where the contract files it.
                puct={"prior": promise} if promise is not None else None,
            ),
        )
        self.iteration = max(self.iteration, int(event.get("iteration") or 0))

    def _evaluated(self, event: dict[str, Any]) -> None:
        index = int(event.get("nodeIndex", 0))
        node = self.nodes.get(index)
        if node is None:
            return
        evaluation = dict((node.extra.get("program") or {}).get("evaluation") or {})
        evaluation.update({
            "criteria": event.get("criteria"),
            "gate": event.get("gateScore"),
            "rollout": event.get("rolloutScore"),
            "reward": event.get("reward"),
        })
        self.nodes[index] = _with(
            node, score=event.get("reward"),
            extra=self._update_program(node, evaluation=evaluation),
        )

    def _merged(self, event: dict[str, Any]) -> Iterator[EngineEvent]:
        index = int(event.get("nodeIndex", 0))
        node = self.nodes.get(index)
        if node is None:
            return
        accepted = bool(event.get("accepted"))
        node = _with(
            node,
            adopted=accepted,
            type="adopted" if accepted else "rejected",
            # The candidate's own error wins over the merger's verdict when it
            # has one. "it did not run" is the merger restating the outcome;
            # "ModuleNotFoundError: no module named 'torch'" is the only line
            # anyone can act on, and there is one field to carry it.
            reason=node.reason or event.get("reason"),
            failure_class=node.failure_class or _class_of_category(event.get("category")),
        )
        node = _with(node, extra=self._update_program(node, logical_kind=node.type))
        self.nodes[index] = node
        if accepted:
            self.best_node_id = node.node_id
            if node.score is not None:
                self.score = node.score
        # Persisted before the event goes out: a consumer that hears about a node
        # and then cannot read it back has been told something untrue.
        self._persist()
        yield EventNode(node=node)
        yield EventProgress(
            iteration=self.iteration,
            total_iterations=self.total_iterations,
            score=self.score,
            baseline=self.baseline,
        )

    def _finished(self, event: dict[str, Any]) -> None:
        status = str(event.get("status") or "")
        if status == "failed":
            self.status = "failed"
            self.error_code = self.error_code or "SEARCH_FAILED"
        elif status == "stopped":
            self.status = self.stopped_status
        else:
            self.status = "completed"
        best = event.get("bestNodeIndex")
        if best is not None:
            self.best_node_id = node_id_for(self.task_id, int(best))
        planned = event.get("expansionsPlanned")
        made = event.get("candidates")
        reason = event.get("stopReason")
        if planned and made is not None and int(made) < int(planned):
            self._summary = (
                f"planned {planned} expansions, made {made}"
                + (f"; it stopped because {reason}" if reason else "")
            )
        self._persist()

    # -- artifacts -------------------------------------------------------------

    def _artifact(self, index: int, code_hash: Any) -> Optional[str]:
        digest = str(code_hash or "").removeprefix("sha256:")
        if not digest:
            return None
        artifact_id = f"A-program:{self.task_id}:{digest[:16]}"
        if artifact_id in self.artifacts:
            # Two nodes arrived at the same program. One artifact, and its
            # `node_id` can only name one of them — the first, which is where
            # this program actually appeared. Overwriting made the field mean
            # "the most recent node that happened to repeat it", which is not a
            # fact anyone asked for.
            return artifact_id
        self.artifacts[artifact_id] = ArtifactRef(
            artifact_id=artifact_id,
            node_id=node_id_for(self.task_id, index),
            # A directory, because a candidate is a file tree and one file is
            # only its commonest shape. Named without a suffix for the same
            # reason: `candidate-3.py` would be a lie about a package.
            name=f"candidate-{index}",
            kind="program_snapshot",
            path=str(self.run_dir / "candidates" / digest),
            sha256=digest,
            # AgentServer projects the URL; a provider-local path is not one.
            download_url=None,
        )
        return artifact_id

    # -- persistence -----------------------------------------------------------

    def to_engine_state(self) -> EngineState:
        return EngineState(
            task_id=self.task_id,
            status=self.status,
            iteration=self.iteration,
            best_node_id=self.best_node_id,
            total_iterations=self.total_iterations,
            score=self.score,
            baseline=self.baseline,
            updated_at=_utc_now(),
            error_code=self.error_code,
            error_message=self.error_message,
        )

    def to_report(self) -> EngineReport:
        return EngineReport(
            task_id=self.task_id,
            status=self.status,
            best_node_id=self.best_node_id,
            artifact_index=list(self.artifacts.values()),
            summary=self._summary,
        )

    def _persist(self) -> None:
        _write_atomically(self.run_dir / STATE_FILE, self.to_engine_state())
        _write_atomically(self.run_dir / REPORT_FILE, self.to_report())
        _write_atomically(self.run_dir / NODES_FILE, {
            "nodes": [self.nodes[index] for index in sorted(self.nodes)],
        })


def _with(node: RsiTreeNode, **changes: Any) -> RsiTreeNode:
    """`dataclasses.replace` for a slotted frozen node."""
    data = {name: getattr(node, name) for name in node.__dataclass_fields__}
    data.update(changes)
    return RsiTreeNode(**data)


def _changes_from(summary: Any) -> list[RsiChange]:
    """The node's one-line summary as the contract's change list.

    Degraded on purpose. The contract wants `{group, operation, function,
    target, summary}` and the search has a sentence the model wrote about its
    own edit — there is no structure to recover. Asking the model for one would
    lengthen every mutation reply for a field nothing has yet been shown to
    read; when something does, the prompt is where to add it.
    """
    text = str(summary or "").strip()
    if not text:
        return []
    return [RsiChange(group="program", operation="modify", function=None, target=None,
                      summary=text)]


#: Free-text failures, grouped so a reader can count them.
#:
#: The search reports what the evaluator said, which is a sentence. The contract
#: wants a class. Matched on substrings rather than parsed: the text comes from
#: an evaluator the drafting agent wrote, so there is no format to rely on, and
#: a wrong guess here costs a label rather than a decision.
_FAILURE_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("import_error", ("importerror", "modulenotfound", "cannot import", "import failed")),
    ("timeout", ("timeout", "timed out", "exceeded", "budget")),
    ("syntax_error", ("syntaxerror", "indentationerror", "unexpected eof")),
    ("empty_reply", ("returned nothing", "empty reply", "no candidate")),
    ("contract_mismatch", ("has no", "not callable", "missing", "does not define")),
    ("wrong_output", ("not lossless", "does not match", "came out wrong", "mismatch")),
)


def classify_failure(error: Any) -> Optional[str]:
    text = str(error or "").lower()
    if not text.strip():
        return None
    for label, markers in _FAILURE_MARKERS:
        if any(marker in text for marker in markers):
            return label
    return "unclassified"


def _class_of_category(category: Any) -> Optional[str]:
    return {
        "constraint-violated": "gate_violation",
        "candidate-failed": "unclassified",
    }.get(str(category or ""))


# -- reading back ---------------------------------------------------------------


def read_state_file(task_id: str) -> Optional["_StoredState"]:
    payload = _read(task_id, STATE_FILE)
    return _StoredState(payload) if payload else None


def read_report_file(task_id: str) -> Optional[EngineReport]:
    payload = _read(task_id, REPORT_FILE)
    if not payload:
        return None
    return EngineReport(
        task_id=payload.get("task_id", task_id),
        status=payload.get("status", "created"),
        best_node_id=payload.get("best_node_id"),
        artifact_index=[ref for ref in
                        (_artifact_ref_from(raw) for raw in payload.get("artifact_index") or [])
                        if ref is not None],
        summary=payload.get("summary"),
    )


def _artifact_ref_from(raw: Any) -> Optional[ArtifactRef]:
    """One persisted artifact entry, rebuilt field by field.

    Not `ArtifactRef(**raw)`: a report written by an older or newer build may
    carry a key this dataclass no longer has — this branch has removed
    persisted fields twice already — and `**raw` turns that drift into a
    TypeError that breaks every future `read_report` of the task. Unknown keys
    are dropped, missing optional ones default, and an entry that is not a
    mapping at all is skipped rather than allowed to take the report with it.
    """
    if not isinstance(raw, dict):
        return None
    return ArtifactRef(
        artifact_id=str(raw.get("artifact_id") or ""),
        node_id=raw.get("node_id"),
        name=str(raw.get("name") or ""),
        kind=str(raw.get("kind") or "program_snapshot"),
        path=str(raw.get("path") or ""),
        sha256=raw.get("sha256"),
        download_url=raw.get("download_url"),
    )


def read_tree_file(task_id: str) -> Optional[TreeResponse]:
    payload = _read(task_id, NODES_FILE)
    if not payload:
        return None
    nodes = [node for node in (_node_from(raw) for raw in payload.get("nodes") or [])
             if node is not None]
    by_id = {node.node_id: node for node in nodes}
    return TreeResponse(nodes=nodes, depth=_depth_of(nodes, by_id),
                        iteration=max((node.iteration for node in nodes), default=0))


def _depth_of(nodes: list[RsiTreeNode], by_id: dict[str, RsiTreeNode]) -> int:
    deepest = 0
    for node in nodes:
        depth, cursor = 0, node
        while cursor.parent_id and cursor.parent_id in by_id and depth < len(nodes):
            cursor = by_id[cursor.parent_id]
            depth += 1
        deepest = max(deepest, depth)
    return deepest


def _node_from(raw: dict[str, Any]) -> Optional[RsiTreeNode]:
    """One persisted node, rebuilt field by field.

    Not `RsiTreeNode(**raw)`, for the same reason `_artifact_ref_from` exists:
    a nodes.json written by another build may carry a key this dataclass no
    longer has, and `**raw` turns that drift into a TypeError that takes the
    whole tree with it — `get_tree` is a correctness channel (AgentServer's
    restart compensation reads it), so it degrading to a partial tree beats it
    raising.
    """
    if not isinstance(raw, dict):
        return None
    changes = [
        RsiChange(
            group=str(change.get("group") or "program"),
            operation=str(change.get("operation") or ""),
            function=change.get("function"),
            target=change.get("target"),
            summary=str(change.get("summary") or ""),
        )
        for change in raw.get("changes") or [] if isinstance(change, dict)
    ]
    return RsiTreeNode(
        node_id=str(raw.get("node_id") or ""),
        iteration=int(raw.get("iteration") or 0),
        parent_id=raw.get("parent_id"),
        type=str(raw.get("type") or "candidate"),
        adopted=bool(raw.get("adopted")),
        score=raw.get("score"),
        summary=raw.get("summary"),
        snapshot_artifact_id=raw.get("snapshot_artifact_id"),
        reason=raw.get("reason"),
        failure_class=raw.get("failure_class"),
        changes=changes,
        extra=raw.get("extra") if isinstance(raw.get("extra"), dict) else {},
    )


def _read(task_id: str, name: str) -> Optional[dict[str, Any]]:
    directory = run_dir_for(task_id)
    if directory is None:
        return None
    path = directory / name
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


class _StoredState:
    """A state file, with the one accessor the provider needs off it."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    @property
    def status(self) -> RsiStatus:
        return self._payload.get("status", "created")

    @property
    def best_node_id(self) -> Optional[str]:
        """Read off the report, which is the file that records it.

        The state file carries what a progress bar needs; which node won belongs
        to the report, and duplicating it would give two files that can disagree.
        """
        report = read_report_file(self._payload.get("task_id", ""))
        return report.best_node_id if report else None

    def to_engine_state(self) -> EngineState:
        return EngineState(
            task_id=self._payload.get("task_id", ""),
            status=self.status,
            iteration=int(self._payload.get("iteration") or 0),
            best_node_id=self.best_node_id,
            total_iterations=int(self._payload.get("total_iterations") or 0),
            score=self._payload.get("score"),
            baseline=self._payload.get("baseline"),
            updated_at=str(self._payload.get("updated_at") or ""),
            error_code=self._payload.get("error_code"),
            error_message=self._payload.get("error_message"),
        )


__all__ = [
    "NODES_FILE",
    "REPORT_FILE",
    "STATE_FILE",
    "ProgramRunState",
    "classify_failure",
    "node_id_for",
    "read_report_file",
    "read_state_file",
    "read_tree_file",
    "register_run_dir",
    "run_dir_for",
]
