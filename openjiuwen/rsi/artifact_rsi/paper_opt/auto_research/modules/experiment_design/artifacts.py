"""Living design summary: versioned claims, localized section patches."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.common.metrics import infer_baseline_names
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.common.workspace import (
    code_agent_instruction_path,
    ensure_design_dir,
    experiment_design_path,
    to_project_relative,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.experiment_design.schemas import (
    ClaimOutcome,
    ClosedClaim,
    CodeAgentInstruction,
    DesignStatus,
    EvaluationFeedback,
    ExperimentDesignDraft,
    ExperimentPlan,
    FeedbackVerdict,
    MetricDefinition,
    NewClaim,
    RecoveryMode,
    SectionUpdates,
    design_session_id,
    format_metric_claim_text,
    format_utc,
    is_metric_slot,
    metric_claim_slot,
    metric_name_from_slot,
    normalize_claim_slot,
    outcome_from_verdict,
    parse_utc_datetime,
    status_from_verdict,
    utc_now,
)

SCHEMA_VERSION = 5

DESIGN_REQUIRED_HEADINGS = (
    "# Experiment Design",
    "## Objective and Hypothesis",
    "## Research Grounding",
    "## Current Experiment",
    "## Decision Metrics",
    "## Evaluation and Revision Log",
)

LOG_HEADING = "## Evaluation and Revision Log"
DECISION_METRICS_HEADING = "## Decision Metrics"
CURRENT_EXPERIMENT_HEADING = "## Current Experiment"
RESEARCH_GROUNDING_HEADING = "## Research Grounding"

# Document-level ``##`` sections only. Freeform experiment bodies may contain
# their own ``##`` / ``###`` headings; those must not end a living-summary section.
TOP_LEVEL_SECTION_HEADINGS = (
    "## Objective and Hypothesis",
    RESEARCH_GROUNDING_HEADING,
    CURRENT_EXPERIMENT_HEADING,
    DECISION_METRICS_HEADING,
    "## Expected Outcomes",  # legacy schema ≤4
    "## Reproducibility and Output Contract",  # legacy schema ≤4
    "## Risks and Assumptions",  # legacy schema ≤4
    LOG_HEADING,
)

_H2_LINE_RE = re.compile(r"(?m)^## (?!#)")

INSTRUCTION_REQUIRED_HEADINGS = (
    "# Code Agent Instruction",
    "## Goal",
    "## Read First",
    "## Scope",
    "## Required Work",
    "## Constraints",
    "## Validation",
    "## Expected Outputs",
    "## Completion Report",
)

_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n?", re.DOTALL)
_LOG_ENTRY_RE = re.compile(r"(?m)^### Revision (\d+)\b")
_CLAIM_LINE_RE = re.compile(
    r"^- (?P<label>Objective|Hypothesis|Metric \S+) "
    r"(?P<index>\d+) \[(?P<tag>[^\]]+)\]: (?P<text>.*)$"
)
_BULLET_ITEM_RE = re.compile(r"(?m)^- (.+)$")

CORE_SLOT_LABELS: dict[str, str] = {
    "objective": "Objective",
    "hypothesis": "Hypothesis",
}


def slot_label(slot: str) -> str:
    normalized = normalize_claim_slot(slot)
    if normalized in CORE_SLOT_LABELS:
        return CORE_SLOT_LABELS[normalized]
    return f"Metric {metric_name_from_slot(normalized)}"


def label_to_slot(label: str) -> str:
    if label in CORE_SLOT_LABELS.values():
        for slot, value in CORE_SLOT_LABELS.items():
            if value == label:
                return slot
    if label.startswith("Metric "):
        return metric_claim_slot(label[len("Metric ") :])
    raise ValueError(f"unknown claim label: {label!r}")


@dataclass
class DesignFrontmatter:
    schema_version: int
    run_id: str
    revision: int
    status: DesignStatus
    design_session_id: str
    branch: str | None
    created_at: datetime
    updated_at: datetime
    latest_code_session_id: str | None = None
    latest_evaluator_session_id: str | None = None
    latest_evaluated_at: datetime | None = None
    research_paths: list[str] = field(default_factory=list)
    latest_result_paths: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "revision": self.revision,
            "status": self.status,
            "design_session_id": self.design_session_id,
            "branch": self.branch,
            "created_at": format_utc(self.created_at),
            "updated_at": format_utc(self.updated_at),
            "latest_code_session_id": self.latest_code_session_id,
            "latest_evaluator_session_id": self.latest_evaluator_session_id,
            "latest_evaluated_at": format_utc(self.latest_evaluated_at),
            "research_paths": list(self.research_paths),
            "latest_result_paths": list(self.latest_result_paths),
        }


@dataclass
class ParsedDesignDocument:
    frontmatter: DesignFrontmatter
    body: str
    revision_log: str

    @property
    def current_sections(self) -> str:
        idx = self.body.find(LOG_HEADING)
        if idx < 0:
            raise ValueError("design document missing Evaluation and Revision Log")
        return self.body[:idx].rstrip() + "\n"


def split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    match = _FRONTMATTER_RE.match(text)
    if not match:
        raise ValueError("markdown is missing YAML frontmatter")
    meta = yaml.safe_load(match.group(1)) or {}
    if not isinstance(meta, dict):
        raise TypeError("frontmatter must be a YAML mapping")
    body = text[match.end() :]
    return meta, body.lstrip("\n") if body.startswith("\n") else body


def _frontmatter_from_dict(data: dict[str, Any]) -> DesignFrontmatter:
    created = parse_utc_datetime(data.get("created_at"))
    updated = parse_utc_datetime(data.get("updated_at"))
    if created is None or updated is None:
        raise ValueError("frontmatter requires created_at and updated_at")
    return DesignFrontmatter(
        schema_version=int(data.get("schema_version", SCHEMA_VERSION)),
        run_id=str(data["run_id"]),
        revision=int(data["revision"]),
        status=data["status"],
        design_session_id=str(data["design_session_id"]),
        branch=data.get("branch"),
        created_at=created,
        updated_at=updated,
        latest_code_session_id=data.get("latest_code_session_id"),
        latest_evaluator_session_id=data.get("latest_evaluator_session_id"),
        latest_evaluated_at=parse_utc_datetime(data.get("latest_evaluated_at")),
        research_paths=list(data.get("research_paths") or []),
        latest_result_paths=list(data.get("latest_result_paths") or []),
    )


def parse_design_document(text: str) -> ParsedDesignDocument:
    meta, body = split_frontmatter(text)
    frontmatter = _frontmatter_from_dict(meta)
    log_idx = body.find(LOG_HEADING)
    if log_idx < 0:
        raise ValueError("design document missing Evaluation and Revision Log")
    log = body[log_idx + len(LOG_HEADING) :].lstrip("\n")
    return ParsedDesignDocument(frontmatter=frontmatter, body=body, revision_log=log)


def _render_frontmatter(data: dict[str, Any]) -> str:
    dumped = yaml.safe_dump(
        data,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    ).rstrip()
    return f"---\n{dumped}\n---\n"


def _bullet_list(items: list[str]) -> str:
    if not items:
        return "- (none)\n"
    return "".join(f"- {item}\n" for item in items)


def _numbered_list(items: list[str]) -> str:
    return "".join(f"{i}. {item}\n" for i, item in enumerate(items, start=1))


def format_experiment_tag(
    *,
    experiment_index: int,
    outcome: ClaimOutcome,
    feedback: EvaluationFeedback,
) -> str:
    paths = ", ".join(feedback.result_paths) or "[]"
    return (
        f"Experiment {experiment_index}: {outcome} | "
        f"branch=`{feedback.branch}` | "
        f"code=`{feedback.code_session_id}` | "
        f"results=`{paths}`"
    )


def format_claim_line(*, slot: str, index: int, tag: str, text: str) -> str:
    label = slot_label(slot)
    return f"- {label} {index} [{tag}]: {text.strip()}\n"


def render_claim_list(slot: str, entries: list[tuple[int, str, str]]) -> str:
    """entries: (index, tag, text)."""
    label = slot_label(slot)
    lines = [f"**{label}:**\n"]
    for index, tag, text in entries:
        lines.append(format_claim_line(slot=slot, index=index, tag=tag, text=text))
    return "".join(lines) + "\n"


def extract_claim_entries(body: str, slot: str) -> list[tuple[int, str, str]]:
    label = slot_label(slot)
    entries: list[tuple[int, str, str]] = []
    for line in body.splitlines():
        match = _CLAIM_LINE_RE.match(line.strip())
        if not match or match.group("label") != label:
            continue
        entries.append(
            (
                int(match.group("index")),
                match.group("tag").strip(),
                match.group("text").strip(),
            )
        )
    return entries


def list_metric_slots_in_body(body: str) -> list[str]:
    slots: list[str] = []
    seen: set[str] = set()
    for line in body.splitlines():
        match = _CLAIM_LINE_RE.match(line.strip())
        if not match:
            continue
        label = match.group("label")
        if not label.startswith("Metric "):
            continue
        slot = label_to_slot(label)
        if slot not in seen:
            seen.add(slot)
            slots.append(slot)
    return slots


def current_claim_text(body: str, slot: str) -> str | None:
    entries = extract_claim_entries(body, slot)
    for _index, tag, text in reversed(entries):
        if tag == "current":
            return text
    return None


def latest_claim_text(body: str, slot: str) -> str | None:
    entries = extract_claim_entries(body, slot)
    return entries[-1][2] if entries else None


def replace_claim_block(body: str, slot: str, entries: list[tuple[int, str, str]]) -> str:
    label = slot_label(slot)
    header = f"**{label}:**"
    start = body.find(header)
    if start < 0:
        raise ValueError(f"living summary missing claim header {header}")
    # End at next bold claim header or ## heading.
    after = start + len(header)
    rest = body[after:]
    end_rel = len(rest)
    for match in re.finditer(r"(?m)^(?:\*\*[^*]+\*\*|## )", rest):
        if match.start() == 0:
            continue
        end_rel = match.start()
        break
    new_block = render_claim_list(slot, entries)
    return body[:start] + new_block + rest[end_rel:].lstrip("\n")


def ensure_claim_block(body: str, slot: str) -> str:
    """Ensure a claim header exists (metric blocks are inserted under Decision Metrics)."""
    label = slot_label(slot)
    header = f"**{label}:**"
    if header in body:
        return body
    if not is_metric_slot(slot):
        raise ValueError(f"cannot auto-create non-metric claim block for {slot!r}")
    if DECISION_METRICS_HEADING not in body:
        idx = body.find(LOG_HEADING)
        if idx < 0:
            raise ValueError("cannot insert Decision Metrics: missing revision log")
        insertion = (
            f"{DECISION_METRICS_HEADING}\n\n"
            "Falsifiable decision thresholds. Each metric is versioned like "
            "hypotheses; outcomes are tagged after experiments.\n\n"
        )
        body = body[:idx] + insertion + body[idx:]
    start, end = _section_bounds(body, DECISION_METRICS_HEADING)
    inner = body[start + len(DECISION_METRICS_HEADING) : end].rstrip() + "\n\n"
    inner += render_claim_list(slot, [])
    return (
        body[: start + len(DECISION_METRICS_HEADING)]
        + "\n\n"
        + inner.lstrip("\n")
        + body[end:].lstrip("\n")
    )


def close_current_claim(
    body: str,
    *,
    slot: str,
    experiment_index: int,
    outcome: ClaimOutcome,
    feedback: EvaluationFeedback,
    note: str = "",
) -> str:
    body = ensure_claim_block(body, slot) if is_metric_slot(slot) else body
    entries = extract_claim_entries(body, slot)
    if not entries:
        raise ValueError(f"no claims to close for slot={slot}")
    updated: list[tuple[int, str, str]] = []
    closed = False
    tag = format_experiment_tag(
        experiment_index=experiment_index,
        outcome=outcome,
        feedback=feedback,
    )
    if note.strip():
        tag = f"{tag} | note={note.strip()}"
    for index, existing_tag, text in entries:
        if existing_tag == "current" and not closed:
            updated.append((index, tag, text))
            closed = True
        else:
            updated.append((index, existing_tag, text))
    if not closed:
        index, _tag, text = updated[-1]
        updated[-1] = (index, tag, text)
    return replace_claim_block(body, slot, updated)


def append_current_claim(body: str, *, slot: str, text: str) -> str:
    if is_metric_slot(slot) and f"**{slot_label(slot)}:**" not in body:
        body = ensure_claim_block(body, slot)
        # Empty block from ensure — seed with the new current entry.
        return replace_claim_block(body, slot, [(0, "current", text.strip())])
    entries = extract_claim_entries(body, slot)
    next_index = (max((e[0] for e in entries), default=-1)) + 1
    normalized: list[tuple[int, str, str]] = []
    for index, tag, existing in entries:
        if tag == "current":
            normalized.append((index, "superseded", existing))
        else:
            normalized.append((index, tag, existing))
    normalized.append((next_index, "current", text.strip()))
    return replace_claim_block(body, slot, normalized)


def _section_bounds(body: str, heading: str) -> tuple[int, int]:
    """Return [start, end) for a top-level ``##`` section.

    The end is the next *document-level* heading from
    ``TOP_LEVEL_SECTION_HEADINGS``, not the next arbitrary ``##`` line. Freeform
    ``experiment`` markdown often uses ``##`` subsections; treating those as
    section ends previously duplicated Current Experiment on update.
    """
    # Match only at line start so inline mentions like ``## Decision Metrics``
    # inside a paragraph do not count as a section heading.
    pattern = re.compile(rf"(?m)^{re.escape(heading)}\s*$")
    match = pattern.search(body)
    if match is None:
        raise ValueError(f"missing section heading: {heading}")
    start = match.start()
    after = match.end()
    end = len(body)
    for top in TOP_LEVEL_SECTION_HEADINGS:
        if top == heading:
            continue
        next_match = re.search(rf"(?m)^{re.escape(top)}\s*$", body[after:])
        if next_match is not None:
            candidate = after + next_match.start()
            end = min(end, candidate)
    return start, end


def replace_section_body(body: str, heading: str, new_inner: str) -> str:
    start, end = _section_bounds(body, heading)
    inner = new_inner.strip("\n") + "\n\n"
    return body[: start + len(heading)] + "\n\n" + inner + body[end:].lstrip("\n")


def read_section_inner(body: str, heading: str) -> str:
    start, end = _section_bounds(body, heading)
    return body[start + len(heading) : end].strip("\n")


def _demote_experiment_h2(text: str) -> str:
    """Rewrite leading ``## `` lines to ``### `` so they stay inside Current Experiment."""
    return _H2_LINE_RE.sub("### ", text)


def render_current_experiment_inner(draft: ExperimentDesignDraft) -> str:
    """Render the freeform Current Experiment section body."""
    text = _demote_experiment_h2(draft.experiment.strip())
    pointer = (
        "\n\nVersioned thresholds live under `## Decision Metrics` "
        f"({', '.join(m.name for m in draft.metrics)})."
    )
    if "`## Decision Metrics`" in text or "Decision Metrics" in text:
        return text + "\n"
    return text + pointer + "\n"


def render_decision_metrics_section(metrics: list[MetricDefinition]) -> str:
    blocks = [
        (
            f"{DECISION_METRICS_HEADING}\n\n"
            "Falsifiable decision thresholds. Each metric is versioned like hypotheses; "
            "outcomes are tagged after experiments.\n\n"
        )
    ]
    for metric in metrics:
        slot = metric_claim_slot(metric.name)
        blocks.append(
            render_claim_list(
                slot,
                [(0, "current", format_metric_claim_text(metric))],
            )
        )
    return "".join(blocks)


def render_living_summary_body(draft: ExperimentDesignDraft) -> str:
    return (
        "# Experiment Design\n\n"
        "## Objective and Hypothesis\n\n"
        f"{render_claim_list('objective', [(0, 'current', draft.objective)])}"
        f"{render_claim_list('hypothesis', [(0, 'current', draft.hypothesis)])}"
        f"{RESEARCH_GROUNDING_HEADING}\n\n"
        f"{_bullet_list(draft.grounding)}\n"
        f"{CURRENT_EXPERIMENT_HEADING}\n\n"
        f"{render_current_experiment_inner(draft)}"
        f"{render_decision_metrics_section(draft.metrics)}"
    )


def render_creation_log_entry(
    *,
    revision: int,
    design_session: str,
    branch: str | None,
    timestamp: datetime,
    follow_up_reasoning: str,
) -> str:
    return (
        f"### Revision {revision}\n\n"
        f"- **Verdict:** created\n"
        f"- **Branch:** {branch or 'null'}\n"
        f"- **Design session:** {design_session}\n"
        f"- **Code session:** null\n"
        f"- **Evaluator session:** null\n"
        f"- **Timestamp:** {format_utc(timestamp)}\n"
        f"- **Observed metrics:** {{}}\n"
        f"- **Result paths:** []\n"
        f"- **Evaluator summary:** Initial living summary created.\n"
        f"- **What changed:** Opened Objective/Hypothesis/Decision Metrics 0 [current].\n"
        f"- **Why this follow-up:** {follow_up_reasoning}\n"
    )


def render_evaluation_log_entry(
    *,
    revision: int,
    feedback: EvaluationFeedback,
    design_session: str,
    what_changed: str,
    follow_up_reasoning: str,
) -> str:
    metrics = ", ".join(f"{k}={v}" for k, v in feedback.observed_metrics.items()) or "{}"
    result_paths = ", ".join(feedback.result_paths) or "[]"
    outcome = outcome_from_verdict(feedback.verdict)
    follow_up = follow_up_reasoning
    if feedback.verdict in ("accept", "stop"):
        follow_up = "No further experiment; terminal evaluator verdict."
    return (
        f"### Revision {revision}\n\n"
        f"- **Verdict:** {feedback.verdict}\n"
        f"- **Execution outcome:** {outcome}\n"
        f"- **Branch:** {feedback.branch}\n"
        f"- **Design session:** {design_session}\n"
        f"- **Code session:** {feedback.code_session_id}\n"
        f"- **Evaluator session:** {feedback.evaluator_session_id}\n"
        f"- **Timestamp:** {format_utc(feedback.evaluated_at)}\n"
        f"- **Observed metrics:** {metrics}\n"
        f"- **Result paths:** {result_paths}\n"
        f"- **Evaluator summary:** {feedback.summary}\n"
        f"- **What changed:** {what_changed}\n"
        f"- **Why this follow-up:** {follow_up}\n"
    )


def render_experiment_design_markdown(
    *,
    frontmatter: DesignFrontmatter,
    body_without_log: str,
    revision_log: str,
) -> str:
    body = body_without_log.rstrip() + f"\n\n{LOG_HEADING}\n\n" + revision_log.rstrip() + "\n"
    return _render_frontmatter(frontmatter.to_dict()) + "\n" + body


def render_terminal_instruction(draft: ExperimentDesignDraft, verdict: FeedbackVerdict) -> CodeAgentInstruction:
    return CodeAgentInstruction(
        goal=f"No further execution. Evaluator verdict is '{verdict}'.",
        read_first=["experiments/<run_id>/design/experiment_design.md"],
        scope=["Do not modify experiment code or re-run experiments."],
        required_work=[
            "Record the terminal decision in the run logs.",
            "Leave generated_code/ and results/ unchanged.",
        ],
        constraints=[
            "Do not invent new experiments.",
            "Do not edit OpenJiuwen packages.",
        ],
        validation=["Confirm status is terminal in experiment_design.md frontmatter."],
        expected_outputs=["No new result artifacts required."],
        completion_report=[
            f"State that the run is {status_from_verdict(verdict)}.",
            "Point to the latest evaluation log entry.",
        ],
    )


def render_code_agent_instruction_markdown(
    *,
    run_id: str,
    revision: int,
    design_path: str,
    design_session: str,
    branch: str | None,
    updated_at: datetime,
    instruction: CodeAgentInstruction,
) -> str:
    meta = {
        "run_id": run_id,
        "revision": revision,
        "design_path": design_path,
        "design_session_id": design_session,
        "branch": branch,
        "updated_at": format_utc(updated_at),
    }
    body = (
        "# Code Agent Instruction\n\n"
        "## Goal\n\n"
        f"{instruction.goal}\n\n"
        "## Read First\n\n"
        f"{_bullet_list(instruction.read_first)}\n"
        "## Scope\n\n"
        f"{_bullet_list(instruction.scope)}\n"
        "## Required Work\n\n"
        f"{_numbered_list(instruction.required_work)}\n"
        "## Constraints\n\n"
        f"{_bullet_list(instruction.constraints)}\n"
        "## Validation\n\n"
        f"{_numbered_list(instruction.validation)}\n"
        "## Expected Outputs\n\n"
        f"{_bullet_list(instruction.expected_outputs)}\n"
        "## Completion Report\n\n"
        f"{_numbered_list(instruction.completion_report)}\n"
    )
    return _render_frontmatter(meta) + "\n" + body


def validate_markdown_headings(text: str, required: tuple[str, ...], *, label: str) -> None:
    for heading in required:
        if heading not in text:
            raise ValueError(f"{label} missing required heading: {heading}")


def validate_design_markdown(text: str) -> ParsedDesignDocument:
    parsed = parse_design_document(text)
    if parsed.frontmatter.schema_version not in (1, 2, 3, 4, SCHEMA_VERSION):
        raise ValueError(
            f"unsupported schema_version {parsed.frontmatter.schema_version}; "
            f"expected 1–{SCHEMA_VERSION}"
        )
    if parsed.frontmatter.schema_version >= 5:
        validate_markdown_headings(text, DESIGN_REQUIRED_HEADINGS, label="experiment_design.md")
    elif parsed.frontmatter.schema_version >= 4:
        validate_markdown_headings(
            text,
            (
                "# Experiment Design",
                "## Objective and Hypothesis",
                "## Research Grounding",
                "## Current Experiment",
                "## Decision Metrics",
                "## Expected Outcomes",
                "## Reproducibility and Output Contract",
                "## Risks and Assumptions",
                "## Evaluation and Revision Log",
            ),
            label="experiment_design.md",
        )
    elif parsed.frontmatter.schema_version >= 3:
        validate_markdown_headings(
            text,
            (
                "# Experiment Design",
                "## Objective and Hypothesis",
                "## Current Experiment",
                "## Evaluation and Revision Log",
            ),
            label="experiment_design.md",
        )
    else:
        validate_markdown_headings(
            text,
            ("# Experiment Design", "## Evaluation and Revision Log"),
            label="experiment_design.md",
        )
    return parsed


def validate_instruction_markdown(text: str) -> None:
    validate_markdown_headings(text, INSTRUCTION_REQUIRED_HEADINGS, label="code_agent_instruction.md")
    split_frontmatter(text)


def extract_revision_log_entries(revision_log: str) -> list[str]:
    matches = list(_LOG_ENTRY_RE.finditer(revision_log))
    if not matches:
        return []
    entries: list[str] = []
    for i, match in enumerate(matches):
        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(revision_log)
        entries.append(revision_log[start:end].rstrip() + "\n")
    return entries


def _atomic_write_pair(
    design_path: Path,
    design_text: str,
    instruction_path: Path,
    instruction_text: str,
) -> None:
    design_path.parent.mkdir(parents=True, exist_ok=True)
    instruction_path.parent.mkdir(parents=True, exist_ok=True)
    design_tmp = design_path.with_suffix(design_path.suffix + ".tmp")
    instruction_tmp = instruction_path.with_suffix(instruction_path.suffix + ".tmp")
    try:
        design_tmp.write_text(design_text, encoding="utf-8")
        instruction_tmp.write_text(instruction_text, encoding="utf-8")
        validate_design_markdown(design_text)
        validate_instruction_markdown(instruction_text)
        design_tmp.replace(design_path)
        instruction_tmp.replace(instruction_path)
    except Exception:
        for tmp in (design_tmp, instruction_tmp):
            if tmp.exists():
                tmp.unlink(missing_ok=True)
        raise


def _instruction_baseline_texts(instruction: CodeAgentInstruction | None) -> list[str]:
    if instruction is None:
        return []
    return [
        instruction.goal,
        *instruction.required_work,
        *instruction.constraints,
        *instruction.scope,
    ]


def merge_plan_baselines(plan: ExperimentPlan, *texts: str) -> ExperimentPlan:
    """Union inferred `--method` baseline names onto an existing plan."""
    extra = infer_baseline_names(*texts)
    if not extra:
        return plan
    merged = list(plan.baselines)
    for name in extra:
        if name not in merged:
            merged.append(name)
    if merged == list(plan.baselines):
        return plan
    return plan.model_copy(update={"baselines": merged})


def build_plan_reference(
    *,
    frontmatter: DesignFrontmatter,
    draft: ExperimentDesignDraft,
    design_path: Path,
    instruction_path: Path,
    recovery_mode: RecoveryMode,
) -> ExperimentPlan:
    instruction = draft.code_agent_instruction
    baselines = infer_baseline_names(
        draft.objective,
        draft.hypothesis,
        draft.experiment,
        *_instruction_baseline_texts(instruction),
    )
    return ExperimentPlan(
        run_id=frontmatter.run_id,
        revision=frontmatter.revision,
        status=frontmatter.status,
        design_session_id=frontmatter.design_session_id,
        design_path=to_project_relative(design_path),
        code_agent_instruction_path=to_project_relative(instruction_path),
        branch=frontmatter.branch,
        created_at=frontmatter.created_at,
        updated_at=frontmatter.updated_at,
        recovery_mode=recovery_mode,
        setup=draft.experiment.strip().splitlines()[0] if draft.experiment.strip() else "",
        variables=[],
        baselines=baselines,
        metrics=[m.name for m in draft.metrics],
        expected_outcomes=draft.hypothesis,
    )


def _migrate_legacy_to_living_body(
    existing: ParsedDesignDocument,
    draft: ExperimentDesignDraft,
) -> str:
    """One-time: rebuild a schema-3 living body from the submitted draft."""
    return render_living_summary_body(draft)


def _extract_bullet_texts(section_inner: str) -> list[str]:
    return [m.group(1).strip() for m in _BULLET_ITEM_RE.finditer(section_inner)]


def _merge_append_bullets(old_inner: str, new_items: list[str]) -> str:
    """Append-only merge: keep prior bullets, add unseen new ones in order."""
    existing = _extract_bullet_texts(old_inner)
    seen = set(existing)
    merged = list(existing)
    for item in new_items:
        text = item.strip()
        if text and text not in seen:
            merged.append(text)
            seen.add(text)
    return _bullet_list(merged).rstrip()


def _apply_section_updates(
    body: str,
    draft: ExperimentDesignDraft,
    updates: SectionUpdates | None,
) -> tuple[str, list[str]]:
    """Patch only changed sections. Returns (body, list of changed section names)."""
    changed: list[str] = []
    effective = updates
    if effective is None:
        effective = SectionUpdates(
            grounding=draft.grounding,
            experiment=draft.experiment,
        )

    if effective.grounding is not None:
        old = read_section_inner(body, RESEARCH_GROUNDING_HEADING)
        new_inner = _merge_append_bullets(old, effective.grounding)
        if old.strip() != new_inner.strip():
            body = replace_section_body(body, RESEARCH_GROUNDING_HEADING, new_inner)
            changed.append("grounding")

    if effective.experiment is not None:
        merged = draft.model_copy(update={"experiment": effective.experiment})
        new_inner = render_current_experiment_inner(merged).rstrip()
        old = read_section_inner(body, CURRENT_EXPERIMENT_HEADING)
        if old.strip() != new_inner.strip():
            body = replace_section_body(body, CURRENT_EXPERIMENT_HEADING, new_inner)
            changed.append("experiment")

    return body, changed


def _resolve_claim_updates(
    body: str,
    draft: ExperimentDesignDraft,
    feedback: EvaluationFeedback,
) -> tuple[list[ClosedClaim], list[NewClaim]]:
    outcome = outcome_from_verdict(feedback.verdict)
    closed = list(draft.closed_claims)
    new_claims = list(draft.new_claims)

    if not closed:
        closed = [
            ClosedClaim(slot="objective", outcome=outcome),
            ClosedClaim(slot="hypothesis", outcome=outcome),
        ]
        for slot in list_metric_slots_in_body(body):
            if current_claim_text(body, slot) is not None:
                closed.append(ClosedClaim(slot=slot, outcome=outcome))

    if not new_claims and feedback.verdict == "continue":
        new_claims = [
            NewClaim(slot="objective", text=draft.objective),
            NewClaim(slot="hypothesis", text=draft.hypothesis),
        ]
        for metric in draft.metrics:
            new_claims.append(
                NewClaim(
                    slot=metric_claim_slot(metric.name),
                    text=format_metric_claim_text(metric),
                )
            )
    return closed, new_claims


def apply_living_summary_update(
    body: str,
    *,
    draft: ExperimentDesignDraft,
    feedback: EvaluationFeedback,
    experiment_index: int,
) -> tuple[str, str]:
    """Apply claim closes/appends and section patches. Returns (body, what_changed)."""
    if DECISION_METRICS_HEADING not in body and draft.metrics:
        idx = body.find(LOG_HEADING)
        if idx < 0:
            raise ValueError("cannot migrate Decision Metrics: missing revision log")
        body = body[:idx] + render_decision_metrics_section(draft.metrics) + body[idx:]

    closed, new_claims = _resolve_claim_updates(body, draft, feedback)
    notes: list[str] = []

    for item in closed:
        outcome = item.outcome or outcome_from_verdict(feedback.verdict)
        if current_claim_text(body, item.slot) is None and not extract_claim_entries(
            body, item.slot
        ):
            continue
        body = close_current_claim(
            body,
            slot=item.slot,
            experiment_index=experiment_index,
            outcome=outcome,
            feedback=feedback,
            note=item.note,
        )
        notes.append(f"closed {slot_label(item.slot)} ({outcome})")

    for item in new_claims:
        latest = latest_claim_text(body, item.slot)
        if latest is not None and latest.strip() == item.text.strip():
            continue
        body = append_current_claim(body, slot=item.slot, text=item.text)
        notes.append(
            f"appended {slot_label(item.slot)} "
            f"{extract_claim_entries(body, item.slot)[-1][0]} [current]"
        )

    body, section_changed = _apply_section_updates(body, draft, draft.section_updates)
    notes.extend(f"updated {name}" for name in section_changed)
    what = "; ".join(notes) if notes else "no living-summary body changes"
    return body, what


def write_initial_design_artifacts(
    *,
    run_id: str,
    draft: ExperimentDesignDraft,
    research_paths: list[str],
    branch: str | None = None,
    created_at: datetime | None = None,
    recovery_mode: RecoveryMode = "checkpoint",
) -> ExperimentPlan:
    ensure_design_dir(run_id)
    now = created_at or utc_now()
    session_id = design_session_id(run_id)
    frontmatter = DesignFrontmatter(
        schema_version=SCHEMA_VERSION,
        run_id=run_id,
        revision=0,
        status="draft",
        design_session_id=session_id,
        branch=branch,
        created_at=now,
        updated_at=now,
        research_paths=list(research_paths),
        latest_result_paths=[],
    )
    log = render_creation_log_entry(
        revision=0,
        design_session=session_id,
        branch=branch,
        timestamp=now,
        follow_up_reasoning=(
            "Execute the code-agent instruction for this draft; revise only after "
            "evaluator feedback."
        ),
    )
    body = render_living_summary_body(draft)
    design_text = render_experiment_design_markdown(
        frontmatter=frontmatter,
        body_without_log=body,
        revision_log=log,
    )
    design_path = experiment_design_path(run_id)
    instruction_path = code_agent_instruction_path(run_id)
    instruction_text = render_code_agent_instruction_markdown(
        run_id=run_id,
        revision=0,
        design_path=to_project_relative(design_path),
        design_session=session_id,
        branch=branch,
        updated_at=now,
        instruction=draft.code_agent_instruction,
    )
    _atomic_write_pair(design_path, design_text, instruction_path, instruction_text)
    return build_plan_reference(
        frontmatter=frontmatter,
        draft=draft,
        design_path=design_path,
        instruction_path=instruction_path,
        recovery_mode=recovery_mode,
    )


def update_design_artifacts(
    *,
    run_id: str,
    draft: ExperimentDesignDraft,
    feedback: EvaluationFeedback,
    what_changed: str | None = None,
    recovery_mode: RecoveryMode = "checkpoint",
    allow_after_terminal: bool = False,
) -> ExperimentPlan:
    design_path = experiment_design_path(run_id)
    instruction_path = code_agent_instruction_path(run_id)
    if not design_path.is_file():
        raise FileNotFoundError(f"current design not found: {design_path}")

    existing = validate_design_markdown(design_path.read_text(encoding="utf-8"))
    if existing.frontmatter.run_id != run_id:
        raise ValueError(
            f"run_id mismatch: input={run_id!r} frontmatter={existing.frontmatter.run_id!r}"
        )
    expected_session = design_session_id(run_id)
    if existing.frontmatter.design_session_id != expected_session:
        raise ValueError(
            "design_session_id mismatch: "
            f"expected={expected_session!r} found={existing.frontmatter.design_session_id!r}"
        )
    if existing.frontmatter.status in ("accepted", "stopped") and not allow_after_terminal:
        raise ValueError(
            f"refusing to update terminal design status={existing.frontmatter.status!r}"
        )

    next_revision = existing.frontmatter.revision + 1
    status = status_from_verdict(feedback.verdict)
    now = utc_now()
    instruction_payload = draft.code_agent_instruction
    follow_up = feedback.summary
    if feedback.verdict in ("accept", "stop"):
        instruction_payload = render_terminal_instruction(draft, feedback.verdict)
        follow_up = "No further experiment; terminal evaluator verdict."

    frontmatter = DesignFrontmatter(
        schema_version=SCHEMA_VERSION,
        run_id=run_id,
        revision=next_revision,
        status=status,
        design_session_id=existing.frontmatter.design_session_id,
        branch=feedback.branch,
        created_at=existing.frontmatter.created_at,
        updated_at=now,
        latest_code_session_id=feedback.code_session_id,
        latest_evaluator_session_id=feedback.evaluator_session_id,
        latest_evaluated_at=feedback.evaluated_at,
        research_paths=list(existing.frontmatter.research_paths),
        latest_result_paths=list(feedback.result_paths),
    )

    if existing.frontmatter.schema_version >= 3 and "## Current Experiment" in existing.body:
        living_body = existing.current_sections
    else:
        living_body = _migrate_legacy_to_living_body(existing, draft)

    living_body, auto_what = apply_living_summary_update(
        living_body,
        draft=draft,
        feedback=feedback,
        experiment_index=existing.frontmatter.revision,
    )
    previous_log = existing.revision_log.rstrip() + "\n\n"
    new_entry = render_evaluation_log_entry(
        revision=next_revision,
        feedback=feedback,
        design_session=frontmatter.design_session_id,
        what_changed=what_changed or auto_what,
        follow_up_reasoning=follow_up,
    )
    design_text = render_experiment_design_markdown(
        frontmatter=frontmatter,
        body_without_log=living_body,
        revision_log=previous_log + new_entry,
    )
    instruction_text = render_code_agent_instruction_markdown(
        run_id=run_id,
        revision=next_revision,
        design_path=to_project_relative(design_path),
        design_session=frontmatter.design_session_id,
        branch=feedback.branch,
        updated_at=now,
        instruction=instruction_payload,
    )
    _atomic_write_pair(design_path, design_text, instruction_path, instruction_text)
    return build_plan_reference(
        frontmatter=frontmatter,
        draft=draft,
        design_path=design_path,
        instruction_path=instruction_path,
        recovery_mode=recovery_mode,
    )


def render_research_revision_log_entry(
    *,
    revision: int,
    reason: str,
    design_session: str,
    research_paths: list[str],
    what_changed: str,
    timestamp: datetime,
) -> str:
    paths = ", ".join(research_paths) or "[]"
    return (
        f"### Revision {revision}\n\n"
        f"- **Verdict:** research_revision\n"
        f"- **Branch:** null\n"
        f"- **Design session:** {design_session}\n"
        f"- **Code session:** null\n"
        f"- **Evaluator session:** null\n"
        f"- **Timestamp:** {format_utc(timestamp)}\n"
        f"- **Observed metrics:** {{}}\n"
        f"- **Result paths:** {paths}\n"
        f"- **Evaluator summary:** Supplemental research incorporated.\n"
        f"- **What changed:** {what_changed}\n"
        f"- **Why this follow-up:** {reason}\n"
    )


def revise_design_with_research(
    *,
    run_id: str,
    draft: ExperimentDesignDraft,
    additional_research_paths: list[str],
    reason: str,
    recovery_mode: RecoveryMode = "checkpoint",
) -> ExperimentPlan:
    """Update an existing design after new survey evidence, without closing claims."""
    design_path = experiment_design_path(run_id)
    instruction_path = code_agent_instruction_path(run_id)
    if not design_path.is_file():
        raise FileNotFoundError(f"current design not found: {design_path}")

    existing = validate_design_markdown(design_path.read_text(encoding="utf-8"))
    if existing.frontmatter.run_id != run_id:
        raise ValueError(
            f"run_id mismatch: input={run_id!r} frontmatter={existing.frontmatter.run_id!r}"
        )
    if existing.frontmatter.status in ("accepted", "stopped"):
        raise ValueError(
            f"refusing to revise terminal design status={existing.frontmatter.status!r}"
        )

    next_revision = existing.frontmatter.revision + 1
    now = utc_now()
    merged_paths: list[str] = []
    for path in [*existing.frontmatter.research_paths, *additional_research_paths]:
        if path not in merged_paths:
            merged_paths.append(path)

    status = existing.frontmatter.status
    if status == "draft":
        status = "continue"

    frontmatter = DesignFrontmatter(
        schema_version=SCHEMA_VERSION,
        run_id=run_id,
        revision=next_revision,
        status=status,
        design_session_id=existing.frontmatter.design_session_id,
        branch=existing.frontmatter.branch,
        created_at=existing.frontmatter.created_at,
        updated_at=now,
        latest_code_session_id=existing.frontmatter.latest_code_session_id,
        latest_evaluator_session_id=existing.frontmatter.latest_evaluator_session_id,
        latest_evaluated_at=existing.frontmatter.latest_evaluated_at,
        research_paths=merged_paths,
        latest_result_paths=list(existing.frontmatter.latest_result_paths),
    )

    if existing.frontmatter.schema_version >= 3 and "## Current Experiment" in existing.body:
        living_body = existing.current_sections
    else:
        living_body = _migrate_legacy_to_living_body(existing, draft)

    living_body, section_changed = _apply_section_updates(
        living_body, draft, draft.section_updates
    )
    notes = [f"updated {name}" for name in section_changed]
    notes.append(f"merged {len(additional_research_paths)} research path(s)")
    what = "; ".join(notes) if notes else "incorporated supplemental research"

    previous_log = existing.revision_log.rstrip() + "\n\n"
    new_entry = render_research_revision_log_entry(
        revision=next_revision,
        reason=reason,
        design_session=frontmatter.design_session_id,
        research_paths=additional_research_paths,
        what_changed=what,
        timestamp=now,
    )
    design_text = render_experiment_design_markdown(
        frontmatter=frontmatter,
        body_without_log=living_body,
        revision_log=previous_log + new_entry,
    )
    instruction_text = render_code_agent_instruction_markdown(
        run_id=run_id,
        revision=next_revision,
        design_path=to_project_relative(design_path),
        design_session=frontmatter.design_session_id,
        branch=frontmatter.branch,
        updated_at=now,
        instruction=draft.code_agent_instruction,
    )
    _atomic_write_pair(design_path, design_text, instruction_path, instruction_text)
    return build_plan_reference(
        frontmatter=frontmatter,
        draft=draft,
        design_path=design_path,
        instruction_path=instruction_path,
        recovery_mode=recovery_mode,
    )


def load_design_frontmatter(run_id: str) -> DesignFrontmatter:
    path = experiment_design_path(run_id)
    if not path.is_file():
        raise FileNotFoundError(f"current design not found: {path}")
    return validate_design_markdown(path.read_text(encoding="utf-8")).frontmatter


# Backward-compatible aliases used by older tests/imports.
def outcome_label_from_verdict(verdict: FeedbackVerdict) -> str:
    return outcome_from_verdict(verdict)


def render_current_design_sections(draft: ExperimentDesignDraft) -> str:
    return render_living_summary_body(draft)
