"""Paths into the experiments/ workspace (see experiments/README.md for the convention)."""

from __future__ import annotations

from pathlib import Path

EXPERIMENTS_ROOT = Path("experiments")

_PROJECT_ROOT: Path | None = None


def set_project_root(root: str | Path | None) -> None:
    """Override the resolved project root (primarily for tests)."""
    global _PROJECT_ROOT
    _PROJECT_ROOT = None if root is None else Path(root).resolve()


def project_root() -> Path:
    """Resolved repository root.

    Defaults to the directory that contains ``auto_research/`` and ``experiments/``,
    walking up from this file. Tests may override via :func:`set_project_root`.
    """
    if _PROJECT_ROOT is not None:
        return _PROJECT_ROOT
    here = Path(__file__).resolve()
    for candidate in here.parents:
        if (candidate / "auto_research").is_dir() and (candidate / "experiments").is_dir():
            return candidate
    return here.parents[2]


def workspace_dir(run_id: str) -> Path:
    """Root folder for a given run: experiments/<run_id>/."""
    return project_root() / EXPERIMENTS_ROOT / run_id


def design_dir(run_id: str) -> Path:
    return workspace_dir(run_id) / "design"


def design_report_path(run_id: str) -> Path:
    """The experiment design report `experiment_design` must persist, and
    `code_implementation` reads, at experiments/<run_id>/design/report.md.
    """
    return design_dir(run_id) / "report.md"


def agent_workspace_dir(run_id: str) -> Path:
    """Scratch space for the coding DeepAgent itself (harness bookkeeping,
    intermediate drafts) — distinct from generated_code_dir, which holds only
    the curated, execution-ready deliverable the agent promotes into it.
    """
    return workspace_dir(run_id) / "agent_workspace"


def generated_code_dir(run_id: str) -> Path:
    return workspace_dir(run_id) / "generated_code"


def logs_dir(run_id: str) -> Path:
    """Canonical latest logs: experiments/<run_id>/logs/. Manager runs also keep
    per-attempt copies under modules/experiment_execution/.
    """
    return workspace_dir(run_id) / "logs"


def results_dir(run_id: str) -> Path:
    """Canonical latest metrics: experiments/<run_id>/results/. Manager runs also
    keep per-attempt copies under modules/experiment_execution/.
    """
    return workspace_dir(run_id) / "results"


def modules_dir(run_id: str) -> Path:
    """Per-module attempt artifacts: experiments/<run_id>/modules/."""
    return workspace_dir(run_id) / "modules"


def openjiuwen_log_dir(run_id: str) -> Path:
    """OpenJiuwen SDK rotating logs for this run."""
    return workspace_dir(run_id) / "openjiuwen"


def pipeline_log_path(run_id: str) -> Path:
    return manager_dir(run_id) / "pipeline.log"


def module_attempt_dirname(round_index: int, attempt: int) -> str:
    """Windows-safe folder name; never embed report_id (it contains ':')."""
    return f"round_{int(round_index):03d}_attempt_{int(attempt):03d}"


def module_attempt_dir(run_id: str, module: str, round_index: int, attempt: int) -> Path:
    safe_module = str(module).replace(":", "_").replace("/", "_").replace("\\", "_")
    return modules_dir(run_id) / safe_module / module_attempt_dirname(round_index, attempt)


def ensure_module_attempt_dir(
    run_id: str, module: str, round_index: int, attempt: int
) -> Path:
    path = module_attempt_dir(run_id, module, round_index, attempt)
    path.mkdir(parents=True, exist_ok=True)
    return path


def agent_trace_path(run_id: str, module: str, round_index: int, attempt: int) -> Path:
    return module_attempt_dir(run_id, module, round_index, attempt) / "agent_trace.jsonl"


def find_harness_run_dirs(run_id: str) -> list[Path]:
    """Generated-agent run folders (``runs/run_*``) if the harness created any."""
    roots = [
        generated_code_dir(run_id) / "runs",
        agent_workspace_dir(run_id) / "output" / "runs",
        agent_workspace_dir(run_id) / "runs",
    ]
    found: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        for child in sorted(root.iterdir()):
            if child.is_dir() and child.name.startswith("run"):
                found.append(child)
    return found


def reflection_dir(run_id: str) -> Path:
    return workspace_dir(run_id) / "reflection"


def reflection_path(run_id: str, revision: int) -> Path:
    return reflection_dir(run_id) / f"revision-{revision}.md"


def report_path(run_id: str) -> Path:
    """The final reporting artifact — experiments/<run_id>/report.md."""
    return workspace_dir(run_id) / "report.md"


def paper_workspace_dir(run_id: str) -> Path:
    """Scratch + output space for the optional paper_writing module —
    sections/, figures/, refs.bib, and the compiled main.pdf all live here.
    Each invocation overwrites this directory fully rather than resuming a
    partial prior draft — see docs/paper_writing_design.md §9.
    """
    return workspace_dir(run_id) / "paper"


def paper_sections_dir(run_id: str) -> Path:
    return paper_workspace_dir(run_id) / "sections"


def paper_figures_dir(run_id: str) -> Path:
    return paper_workspace_dir(run_id) / "figures"


def paper_refs_bib_path(run_id: str) -> Path:
    return paper_workspace_dir(run_id) / "refs.bib"


def paper_tex_path(run_id: str) -> Path:
    return paper_workspace_dir(run_id) / "main.tex"


def paper_output_path(run_id: str) -> Path:
    return paper_workspace_dir(run_id) / "main.pdf"


def paper_scoring_dir(run_id: str) -> Path:
    """Where auto_research.modules.paper_scoring.PaperScorer writes its own
    ingestion.json/rubric.*.json/audit.json/scoresheet.json for this run's
    paper — distinct from paper_workspace_dir, which holds the paper
    itself, not its evaluation artifacts.
    """
    return workspace_dir(run_id) / "paper_scoring"


def smoke_test_dir(run_id: str) -> Path:
    """code_implementation's own acceptance-gate artifacts (per-variant smoke
    test stdout/stderr + whatever metrics.json got written) — kept on disk
    for debugging/reflection, distinct from logs_dir/results_dir, which are
    experiment_execution's real-run outputs.
    """
    return workspace_dir(run_id) / "smoke_test"


def experiment_design_path(run_id: str) -> Path:
    return design_dir(run_id) / "experiment_design.md"


def code_agent_instruction_path(run_id: str) -> Path:
    return design_dir(run_id) / "code_agent_instruction.md"


def ensure_design_dir(run_id: str) -> Path:
    path = design_dir(run_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def manager_dir(run_id: str) -> Path:
    return workspace_dir(run_id) / "manager"


def manager_state_path(run_id: str) -> Path:
    return manager_dir(run_id) / "state.json"


def manager_events_path(run_id: str) -> Path:
    return manager_dir(run_id) / "events.jsonl"


def manager_rounds_path(run_id: str) -> Path:
    return manager_dir(run_id) / "rounds.jsonl"


def manager_report_path(run_id: str) -> Path:
    return manager_dir(run_id) / "report.json"


def manager_pause_path(run_id: str) -> Path:
    return manager_dir(run_id) / "PAUSE.md"


def manager_user_followup_path(run_id: str) -> Path:
    return manager_dir(run_id) / "user_followup.md"


def manager_round_dir(run_id: str, round_index: int) -> Path:
    return manager_dir(run_id) / "rounds" / f"round_{round_index:03d}"


def ensure_manager_dir(run_id: str) -> Path:
    path = manager_dir(run_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def resolve_project_reference(path: str | Path, *, root: Path | None = None) -> Path:
    """Resolve a project-relative path and reject traversal / absolute paths."""
    base = (root or project_root()).resolve()
    raw = str(path).strip().replace("\\", "/")
    if not raw:
        raise ValueError("path must be non-empty")
    candidate = Path(raw)
    if candidate.is_absolute():
        raise ValueError(f"absolute paths are not allowed: {path!r}")
    if ".." in Path(raw).parts:
        raise ValueError(f"path traversal is not allowed: {path!r}")
    resolved = (base / raw).resolve()
    try:
        resolved.relative_to(base)
    except ValueError as exc:
        raise ValueError(f"path escapes project root: {path!r}") from exc
    return resolved


def to_project_relative(path: str | Path, *, root: Path | None = None) -> str:
    """Return a POSIX-style path relative to the project root."""
    base = (root or project_root()).resolve()
    resolved = Path(path)
    if not resolved.is_absolute():
        resolved = resolve_project_reference(resolved, root=base)
    else:
        resolved = resolved.resolve()
    return resolved.relative_to(base).as_posix()
