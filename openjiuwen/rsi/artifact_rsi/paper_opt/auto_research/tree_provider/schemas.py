"""Public wire types for the paper Provider contract
(docs/autoresearch_endpoint.md, docs/autoresearch_module-endpoint.md) plus
this package's internal task/tree state. See
docs/paper_tree_orchestrator_design.md for the design.

The endpoint docs sketch these public types as ``@dataclass(frozen=True)``,
but everything actually persisted to disk in this codebase
(``PersistedManagerState`` and friends in ``modules/manager/schemas.py``)
uses pydantic ``BaseModel`` for JSON round-tripping — this module follows
that established convention instead of the docs' illustrative dataclass
snippets, since ``tree.json``/``state.json``/``artifacts.json`` need real
validated (de)serialization, not just an in-memory shape.
"""

from __future__ import annotations

from typing import Awaitable, Callable, Literal

from pydantic import BaseModel, Field

RsiStatus = Literal[
    "created", "queued", "running", "completed", "failed", "paused", "terminated"
]

NodeOutcome = Literal["success", "failed", "rejected", "pending"]
# ``failed`` remains the machine-level outcome for a node that could not
# produce a usable candidate.  ``pruned`` is the user-facing tree kind for
# that outcome, so a failed node can be skipped without being confused with a
# normally evaluated-but-not-adopted candidate (``rejected``).
NodeLogicalKind = Literal["root", "reporting", "adopted", "rejected", "pruned"]


class RsiUsageTokens(BaseModel):
    input: int = 0
    output: int = 0
    cache_hit: int = 0


class RsiUsage(BaseModel):
    tokens: RsiUsageTokens = Field(default_factory=RsiUsageTokens)
    cost_estimate: float = 0.0
    call_count: int = 0


class RsiChange(BaseModel):
    group: Literal["paper"] = "paper"
    operation: str
    function: str | None = None
    target: str | None = None
    summary: str


class ArtifactRef(BaseModel):
    artifact_id: str
    node_id: str | None = None
    name: str
    kind: str = "paper_snapshot"
    path: str
    sha256: str | None = None
    download_url: str | None = None


class PaperNodeExtra(BaseModel):
    """Goes in ``RsiTreeNode.extra["paper"]``. Field shapes match
    docs/autoresearch_endpoint.md §3.7's ``PaperNodeExtra`` table exactly,
    plus one internal-only field (``node_run_id``) — harmless extra key,
    the same way ``extra`` itself is meant to carry scenario-specific data
    consumers already have to tolerate.
    """

    logical_kind: NodeLogicalKind
    report_id: str | None = None
    round_index: int
    attempt: int
    reporting_index: int | None = None
    input_node_id: str | None = None
    retry_of_node_id: str | None = None
    # "pending" is an internal-only outcome value beyond the docs'
    # success/failed/rejected enum — used only for the short-lived
    # placeholder node an in-flight reporting attempt is upserted as
    # before its final outcome is known (see orchestrator.py's
    # `_run_one_node`), so a NodeStageEvent's node_ref always resolves to
    # a persisted node. Every node a Provider treats as "done" still uses
    # success/failed/rejected.
    outcome: NodeOutcome
    report_path: str | None = None
    sections: list[str] = Field(default_factory=list)
    refs: list[str] = Field(default_factory=list)
    figures: list[str] = Field(default_factory=list)
    revision_count: int | None = None
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    related_report_ids: list[str] = Field(default_factory=list)
    module_reports: list[dict] = Field(default_factory=list)
    handoff: dict | None = None
    # Internal-only: this node's own ManagerRuntime run_id, so orchestrator
    # code never has to re-derive it from node_id string parsing. Not part
    # of the public spec's PaperNodeExtra table.
    node_run_id: str | None = None
    # Internal-only: this node's absolute score from judge.py::score_paper,
    # used to decide whether the *next* node should adopt this one as its
    # new frontier (see orchestrator.py's _node_score/_build_node). Not part
    # of the public spec's PaperNodeExtra table. orchestrator.py also mirrors
    # this value onto the public RsiTreeNode.score field below (previously
    # left null per docs/autoresearch_endpoint.md); _node_score still reads
    # it from here so the comparison logic stays decoupled from that field.
    score_overall: float | None = None
    score_breakdown: dict[str, float] = Field(default_factory=dict)


class RsiTreeNode(BaseModel):
    node_id: str
    iteration: int
    parent_id: str | None
    type: Literal["root", "reporting", "pruned"]
    adopted: bool
    # The candidate's composite paper score (judge.py::score_paper's
    # `overall`). Root carries the uploaded baseline score when the input is
    # a scorable LaTeX source; pending and unscorable nodes remain null.
    score: float | None = None
    summary: str | None = None
    snapshot_artifact_id: str | None = None
    reason: str | None = None
    failure_class: str | None = None
    changes: list[RsiChange] = Field(default_factory=list)
    extra: dict[str, object] = Field(default_factory=dict)

    @property
    def paper_extra(self) -> PaperNodeExtra | None:
        raw = self.extra.get("paper")
        return PaperNodeExtra.model_validate(raw) if raw is not None else None


# Display-only labels for RsiTreeNode.failure_class, keyed by the exact
# strings orchestrator.py writes. Not exhaustive by design: an unrecognized
# failure_class (including None, for an adopted node) falls back to the raw
# `reason` untouched in friendly_failure_reason() below rather than raising
# or guessing at a translation.
_FAILURE_CLASS_LABELS: dict[str, str] = {
    "pipeline_blocked": "研究流程判断当前方向已无法继续推进，提前终止本轮尝试",
    "pipeline_failed": "本轮研究流程执行异常，提前终止本轮尝试",
    "pipeline_incomplete": "本轮尝试超出调度轮次预算，提前终止本轮尝试",
    "scoring_error": "论文评分服务异常，本轮暂无法完成评分",
    "rejected_by_score": "本轮论文质量未超过当前最佳版本，未被采纳",
    "terminated": "任务被手动终止",
    "crashed": "系统出现意外错误，本轮尝试提前终止",
}


def friendly_failure_reason(node: RsiTreeNode) -> str | None:
    """Display-facing version of `node.reason` for a Provider consumer
    (frontend/EventNode/TreeResponse) -- prefixes a plain-language category
    label ahead of the existing technical detail, never replaces it.

    Deliberately NOT used to overwrite the stored `reason`: that field also
    feeds `PaperTaskState.last_reason` -> the *next* round's seed prompt
    (see orchestrator.py's `_run_one_node`/`seed.py::build_node_seed`), and
    the model needs the technical detail there, not a friendly summary.
    Callers apply this only at the boundary where a node is about to leave
    the orchestrator/storage layer for a Provider consumer.
    """
    if node.reason is None:
        return None
    label = _FAILURE_CLASS_LABELS.get(node.failure_class or "")
    if label is None:
        return node.reason
    return f"{label}（详情：{node.reason}）"


class TreeResponse(BaseModel):
    nodes: list[RsiTreeNode]
    depth: int
    iteration: int


class EngineResult(BaseModel):
    task_id: str
    status: RsiStatus
    final_node_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None


class EngineState(BaseModel):
    task_id: str
    status: RsiStatus
    iteration: int
    total_iterations: int
    # Added 2026-09-02 per docs/autoresearch_module-endpoint.md's updated
    # EngineState shape — should stay aligned with EngineReport.best_node_id.
    best_node_id: str | None = None
    score: float | None = None
    baseline: float | None = None
    usage: RsiUsage | None = None
    updated_at: str
    error_code: str | None = None
    error_message: str | None = None


class EngineReport(BaseModel):
    task_id: str
    status: RsiStatus
    best_node_id: str | None = None
    usage: RsiUsage | None = None
    artifact_index: list[ArtifactRef] = Field(default_factory=list)
    summary: str | None = None
    best_score: float | None = None
    baseline: float | None = None


class ArtifactValidationResult(BaseModel):
    valid: bool
    errors: list[dict[str, str]] = Field(default_factory=list)


class EventStatus(BaseModel):
    event_type: Literal["status"] = "status"
    status: RsiStatus


class EventProgress(BaseModel):
    event_type: Literal["progress"] = "progress"
    iteration: int
    total_iterations: int
    score: float | None = None
    baseline: float | None = None
    usage: RsiUsage | None = None


class EventNode(BaseModel):
    event_type: Literal["node"] = "node"
    node: RsiTreeNode


class NodeStageEvent(BaseModel):
    """A subprocess-stage transition for an *existing* tree node — does not
    create a node, just updates its in-progress stage description. Added
    2026-09-02 per docs/autoresearch_module-endpoint.md's updated event
    contract (mirrors ``openjiuwen.rsi.events.NodeStageEvent``).
    """

    event_type: Literal["node.stage"] = "node.stage"
    node_ref: str
    stage: dict[str, str]
    note: str | None = None


EngineEvent = EventStatus | EventProgress | EventNode | NodeStageEvent
OnEvent = Callable[[EngineEvent], Awaitable[None]]


class PaperTaskState(BaseModel):
    """Internal, task-level state — one per tree (one `task_id`). Persisted
    as this package's own `state.json`, distinct from any node's own
    ManagerRuntime `PersistedManagerState`.
    """

    task_id: str
    run_dir: str
    status: RsiStatus = "created"
    max_iterations: int
    optimization_instruction: str | None = None
    artifact_path: str | None = None
    # Public-facing: the public EngineState/EngineReport.best_node_id --
    # docs/autoresearch_endpoint.md §3.5/3.6 require this to be null until a
    # *reporting* node has actually been adopted. Root (type="root", never
    # has an artifact) must never be assigned here.
    best_node_id: str | None = None
    # Current best composite score and the score used as the task baseline.
    # For an uploaded LaTeX paper, baseline is scored before the first
    # candidate. For instruction-only tasks, the first adopted candidate is
    # the baseline by design.
    score: float | None = None
    baseline: float | None = None
    usage: RsiUsage | None = None
    # Internal-only: root or the most recently adopted reporting node --
    # the parent/comparison baseline _frontier_node() resolves for the
    # *next* round. Starts at root (see _ensure_root_node) and is what lets
    # round 1 chain its parent_id to root without leaking root into the
    # public best_node_id above.
    frontier_node_id: str | None = None
    # Nodes created so far, excluding the root node — see
    # docs/paper_tree_orchestrator_design.md "confirmed design decisions" §2.
    node_count: int = 0
    # Consecutive failed/rejected attempts against the current frontier;
    # resets to 0 the moment a node is adopted. Drives PaperNodeExtra.attempt.
    attempts_since_last_adoption: int = 0
    # The most recent failure/rejection reason, folded into the next
    # attempt's seed prompt (node-lifecycle step 2) — cleared on adoption.
    last_reason: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    updated_at: str = ""
