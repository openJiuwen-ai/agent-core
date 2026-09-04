from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import traceback
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.common.logging import active_artifact_dir
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.common.metrics import (
    validate_metrics_contract,
    validate_smoke_live_path,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.common.workspace import (
    agent_workspace_dir,
    design_dir,
    design_report_path,
    generated_code_dir,
    resolve_project_reference,
    smoke_test_dir,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.code_implementation.grounding import (
    docs_index_path,
    gather_reference_excerpts,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.code_implementation.schemas import (
    CodeImplementationInput,
    CodeImplementationManifest,
    CodeImplementationOutput,
    ImplementedVariant,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.experiment_design.schemas import ExperimentPlan

# Uses OpenJiuwen directly — see docs/code_implementation_design.md for the
# reasoning behind create_code_agent + LspRail/SysOperationRail + task loop.
# Check auto_research/extensions/ for reusable Tool/Rail/Agent classes before
# adding anything new here; this module's own coding agent is told to do the
# same for the code *it* generates (see prompts/system_prompt.md).

_CONVENTIONS_PATH = (
    Path(__file__).resolve().parent.parent
    / "experiment_design"
    / "prompts"
    / "openjiuwen_conventions.md"
)
_EXTENSIONS_REGISTRY_PATH = Path("auto_research/extensions/registry.py")
_PROMPT_TEMPLATE_PATH = Path(__file__).parent / "prompts" / "system_prompt.md"

_ENTRY_POINT = "run.py"
_REQUIREMENTS_FILE = "requirements.txt"
_ASSUMPTIONS_FILE = "ASSUMPTIONS.md"
_OUTPUT_SUBDIR = "output"
_PROMOTION_LOG = "promotion.log"
# Git history stays in agent_workspace/output/. generated_code/ is a runnable
# snapshot, not a nested repo — copying .git then rmtree'ing it fails on
# Windows because object files are read-only (WinError 5).
_PROMOTION_SKIP_NAMES = {".git", "__pycache__"}
# Cap on how much of a failing variant's stderr/stdout gets inlined into
# CodeImplementationManifest.notes — enough to capture a real Python
# traceback's tail (where the actual exception line lives), without letting
# a noisy/verbose failure balloon the manifest. Full output always stays on
# disk under smoke_test_dir regardless of this cap.
_NOTES_TAIL_CHARS = 4000

# Blocks history-rewriting / remote-touching git operations the coding agent
# has no legitimate reason to run in a disposable, no-remote workspace.
# Enforced via GuardedSysOperationRail (see that module for why a subclass is
# needed on Windows) — actual matching is a case-insensitive regex .search()
# against each pipeline sub-command, not a full-string match.
_GIT_DENY_PATTERNS = [
    r"git\s+push",
    r"git\s+reset\s+--hard",
    r"git\s+clean\s+-[a-zA-Z]*f",
    r"git\s+branch\s+-D",
    r"git\s+commit\s+--amend",
    r"git\s+(?:push|commit|merge)\b[^\n]*--no-verify",
    r"git\s+remote\s+(?:add|set-url)",
]
PYTHON_EXE = sys.executable

# A value guaranteed not to be a real --method choice, used to force
# argparse's own "invalid choice: ... (choose from 'a', 'b', 'c')" error —
# see _discover_variant_names below and the discussion in
# docs/code_implementation_design.md of why the host discovers the real
# variant list from the entry point itself rather than trusting a
# separately-tracked list that can silently drift out of sync with what the
# coding agent actually built (observed directly in run-39825ece and
# run-ff1abb51: the agent built every variant the design asked for, but the
# host only ever smoke-tested/promoted whatever a stale short list named).
_INVALID_METHOD_SENTINEL = "__invalid__"
# argparse's choices-list formatting in its own error message has changed
# across Python versions — 3.11 and earlier quote each item
# ("choose from 'a', 'b'"), 3.12+ do not ("choose from a, b"). Split on
# commas and strip optional quotes rather than assuming either form.
_ARGPARSE_CHOICES_RE = re.compile(r"choose from ([^\n)]+)")


@dataclass
class CandidateValidation:
    """Host-side verdict for one staged `output/` candidate."""

    ok: bool
    stage: str = ""
    variant: str = ""
    command: list[str] = field(default_factory=list)
    exit_code: int | None = None
    errors: list[str] = field(default_factory=list)
    fingerprint: str = ""
    stderr_tail: str = ""
    variants: list[ImplementedVariant] = field(default_factory=list)
    failures: dict[str, str] = field(default_factory=dict)
    candidate_hash: str = ""
    cycle: int = 1
    skipped_redundant_smoke: bool = False
    repeated: bool = False
    log_dir: str = ""

    def summary(self) -> str:
        bits = [
            f"cycle={self.cycle}",
            f"ok={self.ok}",
            f"stage={self.stage or '(none)'}",
            f"hash={self.candidate_hash or '(none)'}",
            f"fingerprint={self.fingerprint or '(none)'}",
        ]
        if self.variant:
            bits.append(f"variant={self.variant}")
        if self.exit_code is not None:
            bits.append(f"exit_code={self.exit_code}")
        if self.skipped_redundant_smoke:
            bits.append("skipped_redundant_smoke=true")
        if self.repeated:
            bits.append("repeated=true")
        if self.errors:
            bits.append("errors=" + "; ".join(self.errors[:4]))
        return "; ".join(bits)


def _fingerprint_errors(stage: str, variant: str, errors: list[str]) -> str:
    blob = "|".join([stage, variant, *errors])
    if not blob.strip("|"):
        return ""
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]


def _hash_staged_deliverable(root: Path) -> str:
    digest = hashlib.sha256()
    if not root.exists():
        return digest.hexdigest()[:16]
    files = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if ".git" in path.parts or "__pycache__" in path.parts:
            continue
        if path.suffix == ".pyc":
            continue
        files.append(path)
    files.sort()
    for path in files:
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()[:16]


def _discover_variant_names(code_dir: Path, *, timeout: float = 30) -> list[str]:
    """Ask the entry point's own argparse --method flag what it actually
    supports, instead of trusting a separately-tracked list. The task
    prompt already requires --method be declared via argparse
    choices=[...] (see _build_task_prompt's "Required entry-point
    contract"), so this is discovering ground truth from a contract the
    generated code is already required to satisfy, not inventing a new one.
    Returns [] if the entry point is missing or doesn't expose a
    discoverable choices list — callers must treat that as a failure, not a
    vacuous empty-but-passing smoke test."""
    entry = code_dir / _ENTRY_POINT
    if not entry.is_file():
        return []
    try:
        # Pass the bare filename, not the joined path: with cwd= set below,
        # a *relative* code_dir combined with an already-joined script path
        # gets resolved against the new cwd a second time, doubling the
        # prefix (observed directly: .../generated_code/.../generated_code/
        # run.py — same class of cwd-boundary bug _run_async's own comment
        # already warns about elsewhere in this file). _run_smoke_tests
        # avoids this correctly today by using the same bare _ENTRY_POINT.
        proc = subprocess.run(
            [PYTHON_EXE, _ENTRY_POINT, "--method", _INVALID_METHOD_SENTINEL, "--smoke-test"],
            cwd=code_dir,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return []
    match = _ARGPARSE_CHOICES_RE.search(proc.stderr)
    if not match:
        return []
    items = (item.strip().strip("'\"") for item in match.group(1).split(","))
    return [item for item in items if item]


def _windows_cmd_shim(path: str) -> bool:
    return path.lower().endswith((".cmd", ".bat"))


def _pyright_lsp_command() -> tuple[str, list[str]] | None:
    """Argv that can be Popen'd without a Windows shell (not a .cmd shim).

    OpenJiuwen's default Pyright spawn uses the `pyright` console script; on
    Windows that is often `pyright.cmd`, and CreateProcess on a .cmd without
    `shell=True` fails with WinError 87 — observed as
    `[LSP] Server 'pyright' failed` while the coding agent kept running with
    no diagnostics. Prefer `python -m pyright.langserver` when the module is
    installed; otherwise a non-shim `pyright-langserver` on PATH.
    """
    if importlib.util.find_spec("pyright") is not None:
        return sys.executable, ["-m", "pyright.langserver", "--stdio"]
    found = shutil.which("pyright-langserver")
    if found and not _windows_cmd_shim(found):
        return found, ["--stdio"]
    return None


def _try_lsp_rail(agent_workspace: Path):
    """LspRail if Pyright is spawnable; otherwise None (pyright is optional)."""
    command = _pyright_lsp_command()
    if command is None:
        return None
    from openjiuwen.harness.lsp import InitializeOptions
    from openjiuwen.harness.lsp.types import CustomServerConfig
    from openjiuwen.harness.rails.lsp_rail import LspRail

    executable, args = command
    try:
        return LspRail(
            options=InitializeOptions(
                cwd=str(agent_workspace),
                custom_servers={
                    "pyright": CustomServerConfig(
                        command=executable,
                        args=args,
                        extensions=[".py"],
                        language_id="python",
                    )
                },
            ),
            verbose=True,
        )
    except Exception:  # noqa: BLE001 — a dead rail is worse than no rail
        return None


class CodeImplementationAgent:
    """Turns an ExperimentPlan into a runnable OpenJiuwen codebase under
    experiments/<run_id>/generated_code/, and smoke-tests every variant before
    handing off to experiment_execution. See docs/code_implementation_design.md.

    The coding DeepAgent gets its own scratch workspace
    (experiments/<run_id>/agent_workspace/) separate from the curated
    deliverable — it's told to place final files under an output/
    subdirectory there, which gets promoted (copied) into
    generated_code_dir(run_id) after the run. This keeps harness bookkeeping
    (.agent_history, coding_memory/, etc.) and any of the agent's own scratch
    files out of the folder experiment_execution actually runs.
    """

    def __init__(self, config: dict[str, Any]):
        self.config = config

    def run(self, inputs: CodeImplementationInput) -> CodeImplementationOutput:
        return asyncio.run(self._run_async(inputs))

    async def arun(self, inputs: CodeImplementationInput) -> CodeImplementationOutput:
        return await self._run_async(inputs)

    async def _run_async(self, inputs: CodeImplementationInput) -> CodeImplementationOutput:
        plan = inputs.plan
        design_context = self._gather_design_context(plan)
        # Resolved to absolute paths deliberately: these helpers return paths
        # relative to the current process cwd, and OpenJiuwen's harness does
        # its own ambient cwd/workspace tracking internally (init_cwd) —
        # handing it a relative workspace path let its own workspace-scaffold
        # builder resolve it against an unexpected cwd and nest a duplicate
        # copy of the path inside itself. Same class of bug as the
        # results_dir/logs_dir fix in experiment_execution — always cross a
        # cwd boundary with an absolute path.
        agent_workspace = agent_workspace_dir(plan.run_id).resolve()
        output_dir = agent_workspace / _OUTPUT_SUBDIR
        output_dir.mkdir(parents=True, exist_ok=True)
        code_dir = generated_code_dir(plan.run_id).resolve()

        # Only a last-resort fallback now — see _build_output, which discovers
        # the real variant list from the entry point itself rather than
        # trusting this (plan.baselines is empty today; kept here so a future
        # populated plan.baselines still works as a fallback if discovery
        # can't run at all).
        variant_names = [*plan.baselines, "proposed"]
        task_prompt = inputs.extra_host_instructions + self._build_task_prompt(plan, design_context)
        max_cycles = self._max_validation_cycles()
        conversation_id = f"code_implementation-{plan.run_id}"
        smoke_root = active_artifact_dir(plan.run_id, smoke_test_dir(plan.run_id).resolve())
        smoke_root.mkdir(parents=True, exist_ok=True)

        from openjiuwen.core.runner import Runner
        from openjiuwen.harness.lsp import shutdown_lsp

        agent_message = ""
        validation: CandidateValidation | None = None
        previous_hash = ""
        previous_fingerprint = ""
        fingerprint_hits = 0
        await Runner.start()
        try:
            agent = self._build_coding_agent(agent_workspace, run_id=plan.run_id)
            for cycle in range(1, max_cycles + 1):
                if cycle == 1:
                    query = task_prompt
                else:
                    query = self._build_validation_repair_prompt(validation)
                try:
                    result = await Runner.run_agent(
                        agent,
                        {"query": query, "conversation_id": conversation_id},
                    )
                    agent_message = (
                        str(result.get("output", result)) if isinstance(result, dict) else str(result)
                    )
                except Exception as exc:  # noqa: BLE001 — keep the session for a later cycle
                    agent_message = f"{type(exc).__name__}: {exc}"

                candidate_hash = _hash_staged_deliverable(output_dir)
                cycle_dir = smoke_root / f"cycle_{cycle:03d}"
                cycle_dir.mkdir(parents=True, exist_ok=True)

                unchanged = (
                    cycle > 1
                    and bool(previous_hash)
                    and candidate_hash == previous_hash
                    and bool(previous_fingerprint)
                    and validation is not None
                    and not validation.ok
                )
                if unchanged:
                    validation = replace(
                        validation,
                        cycle=cycle,
                        candidate_hash=candidate_hash,
                        skipped_redundant_smoke=True,
                        errors=[
                            *validation.errors,
                            "unchanged candidate and failure fingerprint; skipped redundant smoke",
                        ],
                    )
                else:
                    validation = self._validate_candidate(
                        plan.run_id,
                        output_dir,
                        cycle=cycle,
                        fallback_names=variant_names,
                        log_dir=cycle_dir,
                    )
                    validation.candidate_hash = candidate_hash
                    if not validation.ok and validation.fingerprint:
                        if (
                            previous_hash
                            and candidate_hash != previous_hash
                            and validation.fingerprint == previous_fingerprint
                        ):
                            fingerprint_hits += 1
                        else:
                            fingerprint_hits = 1
                        validation.repeated = fingerprint_hits >= 2
                    else:
                        fingerprint_hits = 0

                previous_hash = candidate_hash
                previous_fingerprint = validation.fingerprint
                self._write_validation_artifact(cycle_dir, validation)
                self._publish_cycle_artifacts(cycle_dir, smoke_root)
                if validation.ok:
                    break
        finally:
            await shutdown_lsp()
            await Runner.stop()

        if validation is not None and validation.ok:
            try:
                self._promote_output(
                    output_dir,
                    code_dir,
                    log_path=smoke_root / _PROMOTION_LOG,
                )
            except Exception as exc:  # noqa: BLE001
                return self._promotion_failure_output(
                    plan, code_dir, validation, agent_message, exc
                )
            return self._build_output(
                plan,
                code_dir,
                variant_names,
                agent_message,
                validation=validation,
            )
        return self._build_output(
            plan,
            output_dir if output_dir.exists() else code_dir,
            variant_names,
            agent_message,
            validation=validation,
            workspace_dir=str(code_dir),
        )

    # -- promoting the deliverable out of the agent's scratch workspace ------

    @staticmethod
    def _force_rmtree(path: Path) -> None:
        """Delete a tree that may contain read-only Git objects (Windows)."""

        def _unlock_and_retry(func, target, exc):
            error = exc if isinstance(exc, BaseException) else exc[1]
            try:
                os.chmod(target, stat.S_IWRITE)
                func(target)
            except OSError as retry_exc:
                raise error from retry_exc

        if sys.version_info >= (3, 12):
            shutil.rmtree(path, onexc=_unlock_and_retry)
        else:
            shutil.rmtree(
                path,
                onerror=lambda func, target, exc_info: _unlock_and_retry(func, target, exc_info),
            )

    @staticmethod
    def _promotion_ignore(_directory: str, names: list[str]) -> list[str]:
        return [
            name
            for name in names
            if name in _PROMOTION_SKIP_NAMES or name.endswith(".pyc")
        ]

    @staticmethod
    def _file_manifest(root: Path) -> str:
        if not root.exists():
            return "(missing)"
        files = sorted(
            path.relative_to(root).as_posix()
            for path in root.rglob("*")
            if path.is_file() and ".git" not in path.parts
        )
        return ", ".join(files[:80]) or "(empty)"

    @staticmethod
    def _write_promotion_log(log_path: Path | None, lines: list[str]) -> None:
        if log_path is None:
            return
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    @classmethod
    def _copy_deliverable(cls, source_dir: Path, destination_dir: Path) -> list[str]:
        skipped: list[str] = []
        destination_dir.mkdir(parents=True, exist_ok=True)
        for item in source_dir.iterdir():
            if item.name in _PROMOTION_SKIP_NAMES or item.suffix == ".pyc":
                skipped.append(item.name)
                continue
            destination = destination_dir / item.name
            if item.is_dir():
                shutil.copytree(item, destination, ignore=cls._promotion_ignore)
            else:
                shutil.copy2(item, destination)
        return skipped

    @classmethod
    def _promote_output(
        cls, output_dir: Path, code_dir: Path, *, log_path: Path | None = None
    ) -> None:
        """Atomically replace generated_code/ with a passing staged candidate.

        Copies into a temporary sibling, then swaps it into place so a copy
        or replace failure leaves the previous runnable snapshot intact.
        Git history stays in agent_workspace/output/; `.git` and caches are
        skipped.
        """
        lines = [
            "--- promotion ---",
            f"source={output_dir}",
            f"destination={code_dir}",
        ]
        skipped: list[str] = []
        stage = "start"
        parent = code_dir.parent
        parent.mkdir(parents=True, exist_ok=True)
        tmp_dir = parent / f".{code_dir.name}.promoting-{os.getpid()}"
        backup_dir = parent / f".{code_dir.name}.previous-{os.getpid()}"
        try:
            if not output_dir.exists():
                stage = "copy"
                lines.extend(
                    [
                        "stage=copy",
                        "status=failed",
                        "skipped=(none)",
                        "note=source missing; destination left unchanged",
                        "source_files: (missing)",
                        f"destination_files: {cls._file_manifest(code_dir)}",
                    ]
                )
                cls._write_promotion_log(log_path, lines)
                raise FileNotFoundError(f"promotion source missing: {output_dir}")

            stage = "copy"
            if tmp_dir.exists():
                cls._force_rmtree(tmp_dir)
            skipped = cls._copy_deliverable(output_dir, tmp_dir)

            stage = "replace"
            if backup_dir.exists():
                cls._force_rmtree(backup_dir)
            replaced_existing = code_dir.exists()
            if replaced_existing:
                os.replace(code_dir, backup_dir)
            try:
                os.replace(tmp_dir, code_dir)
            except Exception:
                if replaced_existing and backup_dir.exists() and not code_dir.exists():
                    os.replace(backup_dir, code_dir)
                raise
            if backup_dir.exists():
                cls._force_rmtree(backup_dir)

            stage = "done"
            lines.extend(
                [
                    "stage=replace",
                    "status=ok",
                    f"skipped={', '.join(skipped) or '(none)'}",
                    f"source_files: {cls._file_manifest(output_dir)}",
                    f"destination_files: {cls._file_manifest(code_dir)}",
                ]
            )
            cls._write_promotion_log(log_path, lines)
        except Exception as exc:
            lines.extend(
                [
                    f"stage={stage}",
                    "status=failed",
                    f"error_type={type(exc).__name__}",
                    f"error={exc}",
                    f"skipped={', '.join(skipped) or '(none)'}",
                    "--- traceback ---",
                    traceback.format_exc(),
                ]
            )
            cls._write_promotion_log(log_path, lines)
            raise
        finally:
            if tmp_dir.exists() and tmp_dir.resolve() != code_dir.resolve():
                try:
                    cls._force_rmtree(tmp_dir)
                except OSError:
                    pass
            if backup_dir.exists() and backup_dir.resolve() != code_dir.resolve():
                try:
                    cls._force_rmtree(backup_dir)
                except OSError:
                    pass

    # -- design context -----------------------------------------------------

    def _gather_design_context(self, plan: ExperimentPlan) -> str:
        """Assemble what the coding agent needs inlined in the task prompt.

        Prefers ``code_agent_instruction.md`` (the current work order:
        goal, scope, required work, validation). The living summary
        ``experiment_design.md`` is *not* inlined — it grows across
        revisions; the agent reads it on demand via ``design_read_file``.
        Falls back to inlining ``design_path`` when no instruction exists,
        then to the legacy design/report.md stopgap for hand-built plans.
        """
        instruction = self._read_project_file(plan.code_agent_instruction_path)
        if instruction:
            return instruction
        design = self._read_project_file(plan.design_path)
        if design:
            return design

        path = design_report_path(plan.run_id)
        if path.exists():
            return path.read_text(encoding="utf-8")
        path.parent.mkdir(parents=True, exist_ok=True)
        text = self._render_design_report(plan)
        path.write_text(text, encoding="utf-8")
        return text

    def _living_design_note(self, plan: ExperimentPlan) -> str:
        """Pointer to the living summary when the instruction was inlined."""
        if not self._read_project_file(plan.code_agent_instruction_path):
            return ""
        design_path = (plan.design_path or "").strip()
        if not design_path:
            return ""
        return (
            "## Living experiment design (read on demand)\n\n"
            f"The full living summary is at `{design_path}`. It is **not** "
            "inlined here — it grows across revisions (claims, grounding, "
            "Current Experiment). Your workspace `read_file` cannot reach "
            f"`experiments/{plan.run_id}/design/`. Use `design_read_file` "
            "with `file_path` set to that project-relative path, "
            "`design/experiment_design.md`, or `experiment_design.md`. "
            "Read it when this instruction is missing method/variant detail, "
            "decision metrics, or Current Experiment specifics.\n\n"
        )

    @staticmethod
    def _read_project_file(relative_path: str) -> str | None:
        """None if relative_path is empty, escapes the project root, or the
        file doesn't exist — any of which means "nothing usable here", not an
        error; the caller falls back to the legacy path."""
        if not relative_path:
            return None
        try:
            abs_path = resolve_project_reference(relative_path)
        except ValueError:
            return None
        if not abs_path.is_file():
            return None
        return abs_path.read_text(encoding="utf-8")

    @staticmethod
    def _render_design_report(plan: ExperimentPlan) -> str:
        lines = [
            f"# Experiment design report — {plan.run_id}",
            "",
            "## Setup",
            plan.setup,
            "",
            "## Variables",
            *(f"- {v}" for v in plan.variables),
            "",
            "## Baselines",
            *(f"- {b}" for b in plan.baselines),
            "",
            "## Metrics",
            *(f"- {m}" for m in plan.metrics),
            "",
            "## Expected outcomes",
            plan.expected_outcomes,
            "",
        ]
        return "\n".join(lines)

    # -- agent construction --------------------------------------------------

    def _build_coding_agent(self, agent_workspace: Path, *, run_id: str):
        from openjiuwen.core.foundation.llm import init_model
        from openjiuwen.core.single_agent.schema.agent_card import AgentCard
        from openjiuwen.harness.subagents import create_code_agent

        from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.extensions.rails.design_reference_rail import (
            DesignReferenceRail,
        )
        from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.extensions.rails.guarded_sys_operation_rail import (
            GuardedSysOperationRail,
        )
        from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.extensions.rails.observability_rail import (
            with_observability,
        )
        from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.extensions.rails.openjiuwen_reference_rail import (
            OpenJiuwenReferenceRail,
        )

        # bash_deny_patterns (and the tool-layer injection check) are only
        # enforced when this SDK flag is set — without it BashTool/PowerShellTool
        # silently skip the whole permission pipeline. Only set if unset, so an
        # explicit override (e.g. OPENJIUWEN_BASH_STRICT=0 for local debugging)
        # is respected.
        os.environ.setdefault("OPENJIUWEN_BASH_STRICT", "1")

        model = init_model(
            provider=self._setting("provider", "MODEL_PROVIDER", default="OpenAI"),
            model_name=self._setting("model", "MODEL_NAME", default="default"),
            api_key=self._setting("api_key", "API_KEY", required=True, secret=True),
            api_base=self._setting("base_url", "API_BASE", required=True),
            # init_model's own default (60s) is tuned for short completions;
            # this agent's turns can be large multi-file code-writes after a
            # context that's grown from tool output (SDK introspection, LSP
            # diagnostics, etc.), and a timed-out completion surfaces as
            # status="failed" with a useless agent_message like
            # {'error': 'completion_timeout'} — observed directly. Same
            # MODEL_TIMEOUT knob experiment_design already uses (120s there),
            # higher default here since code-gen completions tend to run
            # longer than experiment_design's more structured turns.
            timeout=float(self._setting("timeout", "MODEL_TIMEOUT", default="300")),
        )
        module_cfg = self.config.get("code_implementation", {}) or {}
        design_root = design_dir(run_id)
        design_root.mkdir(parents=True, exist_ok=True)
        rails = with_observability(
            [
                GuardedSysOperationRail(bash_deny_patterns=_GIT_DENY_PATTERNS),
                OpenJiuwenReferenceRail(),
                DesignReferenceRail(design_root=design_root),
            ]
        )
        lsp_rail = _try_lsp_rail(agent_workspace)
        if lsp_rail is not None:
            rails.append(lsp_rail)
        return create_code_agent(
            model,
            card=AgentCard(
                name="experiment_code_implementer",
                description="Implements an experiment design report as a runnable OpenJiuwen codebase.",
            ),
            system_prompt=self._render_system_prompt(),
            rails=rails,
            enable_task_loop=True,
            max_iterations=module_cfg.get("max_iterations"),
            workspace=str(agent_workspace),
            # We don't use the harness's own memory/skills/todo scaffold for
            # this one-shot codegen task — skip it so agent_workspace/ only
            # contains what the agent itself actually writes.
            auto_create_workspace=False,
        )

    def _setting(
        self,
        config_key: str,
        env_key: str,
        *,
        default: str | None = None,
        required: bool = False,
        secret: bool = False,
    ) -> str | None:
        # env wins over configs/*.yaml, not the other way around — a checked-in
        # config file may ship a non-null placeholder (or a stale value from a
        # previous run) that would otherwise silently shadow a real local
        # override. api_key is never read from configs/*.yaml at all — only
        # env, so secrets don't end up committed alongside pipeline config.
        oj_cfg = {} if secret else (self.config.get("openjiuwen", {}) or {})
        value = os.environ.get(env_key) or oj_cfg.get(config_key) or default
        if required and not value:
            raise RuntimeError(
                f"code_implementation needs a model {config_key} — set "
                f"the {env_key} environment variable"
                + ("" if secret else f" or configs['openjiuwen']['{config_key}']") + "."
            )
        return value

    @staticmethod
    def _render_system_prompt() -> str:
        template = _PROMPT_TEMPLATE_PATH.read_text(encoding="utf-8")
        conventions = (
            _CONVENTIONS_PATH.read_text(encoding="utf-8") if _CONVENTIONS_PATH.exists() else ""
        )
        registry = (
            _EXTENSIONS_REGISTRY_PATH.read_text(encoding="utf-8")
            if _EXTENSIONS_REGISTRY_PATH.exists()
            else "(auto_research/extensions/registry.py not found)"
        )
        return template.format(openjiuwen_conventions=conventions, extensions_registry=registry)

    # -- task prompt ---------------------------------------------------------

    def _build_task_prompt(self, plan: ExperimentPlan, design_context: str) -> str:
        docs_index = docs_index_path()
        reference = gather_reference_excerpts(plan)
        reference_block = "\n".join(
            f"- {'[reusable capability]' if is_capability else '[reference/example]'} "
            f"`{path}` — {' '.join(excerpt.split())[:160]}..."
            for path, excerpt, is_capability in reference
        ) or "(no starting-point candidates matched this plan's keywords)"

        living = self._living_design_note(plan)
        return (
            "Implement the following experiment design as a runnable OpenJiuwen codebase.\n\n"
            "## Authority order\n\n"
            "If instructions conflict, obey them in this order: (1) original-task "
            "constraints, (2) the manager repair contract for this attempt, (3) the "
            "generic code-agent guidance in the system prompt and this template.\n\n"
            f"## Code agent instruction\n\n{design_context}\n\n"
            f"{living}"
            "## Local checks vs host validation\n\n"
            "You may run local smoke checks from inside `output/` with whatever "
            "interpreter is available in this workspace. Do **not** spend the session "
            "trying to invoke an absolute host interpreter path from the sandboxed "
            "shell — that path is often blocked, and those local checks are not the "
            "readiness gate. After you stop, the host compiles, smoke-tests, and "
            "validates metrics on `output/` with its own interpreter. Only that host "
            "loop decides readiness. If the host sends a repair request, make a "
            "targeted edit and leave an updated `output/`. Do not document an "
            "outstanding LSP or runtime error in `ASSUMPTIONS.md` and stop.\n\n"
            "## Where to put your files — read this first\n\n"
            f"Your workspace has an `{_OUTPUT_SUBDIR}/` subdirectory. Everything you want kept — "
            f"every file that matters for actually running the experiment — must be written under "
            f"`{_OUTPUT_SUBDIR}/`, not the workspace root. Only what's inside `{_OUTPUT_SUBDIR}/` "
            "gets used afterwards; anything you leave outside it (notes, scratch scripts, etc.) is "
            f"discarded. Keep git history inside `{_OUTPUT_SUBDIR}/` with `git init` / `git commit`; "
            "do not copy files into `generated_code/` — the host promotes a snapshot without `.git`. "
            f"If you run a local check, use `{_OUTPUT_SUBDIR}/` as the working directory "
            f"(e.g. `cd {_OUTPUT_SUBDIR} && python {_ENTRY_POINT} ...`) so you are testing "
            "exactly what the host will validate later — not a version that also sees files "
            "you left elsewhere in your workspace.\n\n"
            "## Variants to implement\n\n"
            "Implement every variant the code-agent instruction above names for comparison — "
            "if that instruction does not list the full method set, read the living summary "
            "with `design_read_file` and use its Current Experiment section. Do not implement "
            "only a subset because a shorter example elsewhere in this prompt might suggest otherwise. "
            "Exactly one of them — the one that embodies the design's actual hypothesis — must "
            "be named `proposed` (the orchestration depends on this literal name downstream), "
            "**even if the design's own prose calls it something else** (e.g. \"Variant C: Full "
            "ACI\" becomes `--method proposed`, not a separate `full_aci` choice that duplicates "
            "it). Every other comparison variant should use the design's own given name as-is "
            "(e.g. `shell_only`, `baseline`) — do not rename them to a generic placeholder, and "
            "do not add `proposed` as an extra alias alongside a variant that already has its "
            "own name; it replaces that name, it doesn't sit next to it. All variants must share "
            "the same data loading and metric "
            "computation code so the comparison across them is fair; dispatch between them with "
            "a `--method <name>` flag on a single entry point rather than duplicating scripts "
            "per variant.\n\n"
            "## Required entry-point contract\n\n"
            f"Write a single entry point at `{_OUTPUT_SUBDIR}/{_ENTRY_POINT}` that accepts:\n"
            "  --method <name>   one of the variant names above, declared via argparse's own "
            "`choices=[...]` on this argument — **required**, not a style preference: the "
            "orchestration discovers which variants exist by passing an invalid --method value "
            "and reading argparse's own \"invalid choice: ... (choose from ...)\" error, so a "
            "custom validator, a plain string check, or swallowing the error yourself will hide "
            "real variants from the host and make them silently never get run or checked.\n"
            "  --smoke-test      run the same invoke/run_method path as a full run on exactly "
            "one item (synthetic context or the first dataset row), call the live model via "
            "API_KEY/API_BASE/MODEL_NAME, write one item record plus model_call_count >= 1, "
            "and exit 0/1 — no full-dataset run. Parser-only stubs and dummy model replies "
            "are invalid.\n"
            "  --output <path>   write a metrics.json to this path\n\n"
            "The host invokes each variant separately as "
            f"`{_ENTRY_POINT} --method <name> --output <name>.metrics.json`. "
            "Do not require `--method all`. Do not refuse a full (non-smoke) "
            "`--method proposed` or `--method <baseline>` run. Each invocation "
            "must write exactly that variant's JSON to `--output`.\n\n"
            f"Metrics to compute, identically across all variants: {', '.join(plan.metrics)}.\n\n"
            "If the non-smoke path fails, still write `--output` as JSON so the host can "
            "diagnose it, and print one stderr line: "
            "`Harness failed at {failure_stage}/{failure_substage}: {detail}`. "
            "The JSON must include:\n"
            "  - `status`: `failed`\n"
            "  - `failure_stage`: one of `dataset_download`, `agent_init`, `tool_call`, "
            "`metrics_write`, `runtime_setup`\n"
            "  - `failure_substage`: short snake_case name of the step that actually failed\n"
            "  - `error_type`: exception class name\n"
            "  - `error_code`: stable token derived from that same cause\n"
            "  - `detail`: human-readable cause with the concrete reason "
            "(HTTP status, exception message, validation rule). A fingerprint hash may be "
            "added, but must not replace the readable cause. Do not invent extra required "
            "environment variables or install-time plugins to explain a dataset/protocol "
            "failure.\n"
            "  - `retryable`: boolean\n"
            "  - `fingerprint`: short stable hash of the cause for dedup\n"
            "  - optional `diagnostics_path`: workspace-relative JSON sidecar with request/"
            "validation metadata (status, URL without secrets, attempt count, "
            "`validation_error`). The sidecar and `detail` must exclude API keys and "
            "protected benchmark item text (prompts, answers, raw dataset rows). Do not "
            "collapse every failure to `{\"error\": \"RuntimeError\"}` with an empty "
            "traceback.\n\n"
            "## Proposed vs baseline\n\n"
            "Follow the experiment design for what each variant should be. A no-tool "
            "**baseline** may be a single live-model completion (`chat.completions` / "
            "equivalent). When the design asks for an agent, prefer an OpenJiuwen "
            "`create_deep_agent` / `ReActAgent` / `Runner` with the tools the design "
            "requires; if the SDK is a poor fit, write plain Python instead of forcing a "
            "mismatched OpenJiuwen class. The host does not reject a harness for using a "
            "one-shot completion on a baseline or for omitting OpenJiuwen on an edge case — "
            "readiness is the smoke test. `--smoke-test` must call the same variant "
            "construction and invoke path as the full run (including solver_prompt / "
            "agent create) with the live model on one item; it may use synthetic "
            "context or the first dataset row, but must not skip construction or "
            "substitute a parser-only stub. The non-smoke path must actually run the "
            "method on the full requested set.\n\n"
            "## OpenJiuwen reference map\n\n"
            + (
                f"Docs table of contents: `{docs_index}` — open it with your "
                "openjiuwen_ref_read_file tool first (path relative to the OpenJiuwen "
                "docs root, e.g. `en/SUMMARY.md`) for how OpenJiuwen APIs actually "
                "work, before writing custom code that reimplements something the SDK "
                "already documents.\n\n"
                if docs_index
                else "\n"
            )
            + "## OpenJiuwen SDK reference — possibly-relevant starting points\n\n"
            "A local keyword search turned up these candidates (may or may not actually be "
            "useful — read the full file/example with your openjiuwen_ref_read_file tool "
            f"before trusting it):\n\n{reference_block}\n\n"
            "SDK-reading **subagents cannot access the OpenJiuwen reference docs**. Their "
            "workspace sandbox hides `openjiuwen_ref_*`. You (the parent) must call "
            "`openjiuwen_ref_read_file`, `openjiuwen_ref_glob`, and "
            "`openjiuwen_ref_list_files` directly.\n\n"
            "**Decision policy:** check the map and the candidates above before writing code "
            "for a given piece of functionality. Reuse a `[reusable capability]` hit only "
            "after actually opening it and confirming it's a genuine, direct fit — not just "
            "related vocabulary. If nothing above fits, including if the candidate list is "
            "empty, write plain Python instead of importing an OpenJiuwen class that only "
            "loosely relates; forcing a mismatched abstraction into the design produces worse "
            "code than a clean custom implementation. `[reference/example]` hits are context "
            "for how OpenJiuwen is used elsewhere, not something to import just because it "
            "showed up in this search.\n\n"
            "## Before you stop\n\n"
            f"1. Optional: from inside `{_OUTPUT_SUBDIR}/`, run a local "
            f"`python {_ENTRY_POINT} --method <name> --smoke-test` check. The host will "
            "re-run every variant with its own interpreter and reject a candidate that "
            "exits non-zero, writes no metrics, fails the metrics contract, or skips "
            "the live 1-item invoke path (empty item records or model_call_count < 1).\n"
            f"2. Inside `{_OUTPUT_SUBDIR}/`, write `{_REQUIREMENTS_FILE}` listing any pip packages "
            "you used beyond the pipeline's own dependencies (pyyaml, pydantic, openjiuwen) — one "
            "package per line.\n"
            f"3. Inside `{_OUTPUT_SUBDIR}/`, write `{_ASSUMPTIONS_FILE}` as a bullet list of the "
            "judgment calls you made to turn the abstract design above into concrete code (library "
            "choices, synthetic data shape, hyperparameter defaults, anything not fully specified "
            "by the report) — including, for each variant, which OpenJiuwen capability (if any) "
            "you reused from the reference map/candidates above and why, or that you checked and "
            "nothing fit so you wrote it directly.\n"
        )

    # -- acceptance gate -------------------------------------------------

    def _max_validation_cycles(self) -> int:
        module_cfg = self.config.get("code_implementation", {}) or {}
        retries = int(module_cfg.get("max_smoke_test_retries", 2) or 0)
        return max(1, retries + 1)

    @staticmethod
    def _tail(text: str, limit: int = _NOTES_TAIL_CHARS) -> str:
        """Last `limit` chars of text — where a traceback's actual exception
        line lives, not its (often long, irrelevant) leading frames."""
        cleaned = text.strip()
        if not cleaned:
            return ""
        if len(cleaned) <= limit:
            return cleaned
        return "...(truncated; see full log on disk)...\n" + cleaned[-limit:]

    @staticmethod
    def _write_validation_artifact(log_dir: Path, validation: CandidateValidation) -> None:
        log_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "ok": validation.ok,
            "stage": validation.stage,
            "variant": validation.variant,
            "cycle": validation.cycle,
            "candidate_hash": validation.candidate_hash,
            "fingerprint": validation.fingerprint,
            "exit_code": validation.exit_code,
            "command": validation.command,
            "errors": validation.errors,
            "failures": validation.failures,
            "repeated": validation.repeated,
            "skipped_redundant_smoke": validation.skipped_redundant_smoke,
            "summary": validation.summary(),
        }
        (log_dir / "validation.json").write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def _publish_cycle_artifacts(cycle_dir: Path, dest_dir: Path) -> None:
        dest_dir.mkdir(parents=True, exist_ok=True)
        if not cycle_dir.exists():
            return
        for path in cycle_dir.iterdir():
            if path.is_file():
                shutil.copy2(path, dest_dir / path.name)

    @staticmethod
    def _failed_validation(
        *,
        stage: str,
        errors: list[str],
        cycle: int,
        log_dir: Path,
        variant: str = "",
        command: list[str] | None = None,
        exit_code: int | None = None,
        stderr_tail: str = "",
        variants: list[ImplementedVariant] | None = None,
        failures: dict[str, str] | None = None,
        candidate_hash: str = "",
    ) -> CandidateValidation:
        return CandidateValidation(
            ok=False,
            stage=stage,
            variant=variant,
            command=list(command or []),
            exit_code=exit_code,
            errors=list(errors),
            fingerprint=_fingerprint_errors(stage, variant, errors),
            stderr_tail=stderr_tail,
            variants=list(variants or []),
            failures=dict(failures or {}),
            candidate_hash=candidate_hash,
            cycle=cycle,
            log_dir=str(log_dir),
        )

    def _validate_candidate(
        self,
        run_id: str,
        code_dir: Path,
        *,
        cycle: int,
        fallback_names: list[str],
        log_dir: Path | None = None,
    ) -> CandidateValidation:
        """Staged host validation of `output/` before any promotion."""
        smoke_dir = log_dir or active_artifact_dir(run_id, smoke_test_dir(run_id))
        smoke_dir.mkdir(parents=True, exist_ok=True)
        candidate_hash = _hash_staged_deliverable(code_dir)

        entry = code_dir / _ENTRY_POINT
        if not entry.is_file():
            return self._failed_validation(
                stage="deliverable",
                errors=[f"missing entry point {_ENTRY_POINT}"],
                cycle=cycle,
                log_dir=smoke_dir,
                candidate_hash=candidate_hash,
            )

        discovered = _discover_variant_names(code_dir)
        variant_names = discovered or list(fallback_names)
        if not variant_names:
            return self._failed_validation(
                stage="deliverable",
                errors=[
                    (
                        f"{_ENTRY_POINT} did not expose a discoverable --method choices list "
                        "and no fallback variant names were provided"
                    )
                ],
                cycle=cycle,
                log_dir=smoke_dir,
                candidate_hash=candidate_hash,
            )

        missing_contract = [
            name
            for name in (_REQUIREMENTS_FILE, _ASSUMPTIONS_FILE)
            if not (code_dir / name).is_file()
        ]
        if missing_contract:
            return self._failed_validation(
                stage="deliverable",
                errors=[f"missing contract file: {name}" for name in missing_contract],
                cycle=cycle,
                log_dir=smoke_dir,
                candidate_hash=candidate_hash,
            )

        static_error = self._compile_staged_python(code_dir)
        if static_error:
            return self._failed_validation(
                stage="static",
                errors=[static_error],
                cycle=cycle,
                log_dir=smoke_dir,
                stderr_tail=self._tail(static_error),
                candidate_hash=candidate_hash,
            )

        return self._run_smoke_and_metrics(
            run_id,
            code_dir,
            variant_names,
            cycle=cycle,
            log_dir=smoke_dir,
            candidate_hash=candidate_hash,
        )

    def _compile_staged_python(self, code_dir: Path) -> str:
        py_files = [
            path
            for path in code_dir.rglob("*.py")
            if path.is_file() and ".git" not in path.parts and "__pycache__" not in path.parts
        ]
        if not py_files:
            return "no Python files to compile"
        module_cfg = self.config.get("code_implementation", {}) or {}
        timeout = module_cfg.get("smoke_test_timeout_seconds", 60)
        command = [PYTHON_EXE, "-m", "compileall", "-q", str(code_dir)]
        try:
            proc = subprocess.run(
                command,
                cwd=code_dir,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            return f"static compile failed: {exc}"
        if proc.returncode == 0:
            return ""
        return self._tail(proc.stderr) or self._tail(proc.stdout) or (
            f"compileall exit_code={proc.returncode}"
        )

    def _run_smoke_and_metrics(
        self,
        run_id: str,
        code_dir: Path,
        variant_names: list[str],
        *,
        cycle: int,
        log_dir: Path,
        candidate_hash: str,
    ) -> CandidateValidation:
        variants = [
            ImplementedVariant(name=name, invocation=[sys.executable, _ENTRY_POINT, "--method", name])
            for name in variant_names
        ]
        module_cfg = self.config.get("code_implementation", {}) or {}
        timeout = module_cfg.get("smoke_test_timeout_seconds")
        failures: dict[str, str] = {}
        first_stage = "smoke"
        first_variant = ""
        first_command: list[str] = []
        first_exit: int | None = None
        first_stderr = ""
        errors: list[str] = []

        for variant in variants:
            metrics_path = log_dir / f"{variant.name}.metrics.json"
            log_path = log_dir / f"{variant.name}.log"
            command = [*variant.invocation, "--smoke-test", "--output", str(metrics_path)]
            try:
                proc = subprocess.run(
                    command,
                    cwd=code_dir,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    check=False,
                )
                log_path.write_text(
                    f"$ {' '.join(command)}\n\n--- stdout ---\n{proc.stdout}\n"
                    f"--- stderr ---\n{proc.stderr}",
                    encoding="utf-8",
                )
            except (subprocess.TimeoutExpired, OSError) as exc:
                log_path.write_text(
                    f"$ {' '.join(command)}\n\n--- execution failed ---\n{exc}",
                    encoding="utf-8",
                )
                detail = f"execution failed: {exc}"
                failures[variant.name] = detail
                errors.append(f"{variant.name}: {detail}")
                if not first_variant:
                    first_stage = "smoke"
                    first_variant = variant.name
                    first_command = command
                    first_stderr = detail
                continue

            diagnostic = self._tail(proc.stderr) or self._tail(proc.stdout)
            if proc.returncode != 0:
                detail = f"exit_code={proc.returncode}\n{diagnostic or '(no output captured)'}"
                failures[variant.name] = detail
                errors.append(f"{variant.name}: exit_code={proc.returncode}")
                if not first_variant:
                    first_stage = "smoke"
                    first_variant = variant.name
                    first_command = command
                    first_exit = proc.returncode
                    first_stderr = diagnostic
                continue

            metrics_state = "present"
            metrics: dict[str, Any] = {}
            if not metrics_path.is_file():
                metrics_state = "missing"
            else:
                try:
                    payload = json.loads(metrics_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError, UnicodeError):
                    metrics_state = "invalid_json"
                    payload = None
                if metrics_state == "present":
                    if not isinstance(payload, dict):
                        metrics_state = "invalid_json"
                    else:
                        metrics = payload

            contract = validate_metrics_contract(
                metrics, expected_method=variant.name, metrics_state=metrics_state
            )
            if contract.ok:
                smoke_path = validate_smoke_live_path(metrics)
                if not smoke_path.ok:
                    contract = smoke_path
            if not contract.ok:
                detail = "; ".join(contract.errors) or contract.reason
                failures[variant.name] = (
                    f"metrics contract failed: {detail}\n{diagnostic or ''}"
                ).strip()
                errors.extend(f"{variant.name}: {item}" for item in contract.errors)
                if not first_variant:
                    first_stage = "metrics"
                    first_variant = variant.name
                    first_command = command
                    first_exit = proc.returncode
                    first_stderr = diagnostic or detail

        if failures:
            return self._failed_validation(
                stage=first_stage,
                errors=errors or [f"smoke test failed for: {', '.join(failures)}"],
                cycle=cycle,
                log_dir=log_dir,
                variant=first_variant,
                command=first_command,
                exit_code=first_exit,
                stderr_tail=first_stderr,
                variants=variants,
                failures=failures,
                candidate_hash=candidate_hash,
            )
        return CandidateValidation(
            ok=True,
            stage="metrics",
            variants=variants,
            candidate_hash=candidate_hash,
            cycle=cycle,
            log_dir=str(log_dir),
        )

    def _run_smoke_tests(
        self, run_id: str, code_dir: Path, variant_names: list[str]
    ) -> tuple[bool, list[ImplementedVariant], dict[str, str]]:
        """Back-compat wrapper around staged smoke+metrics validation."""
        validation = self._validate_candidate(
            run_id, code_dir, cycle=1, fallback_names=variant_names
        )
        return validation.ok, validation.variants, validation.failures

    @staticmethod
    def _build_validation_repair_prompt(validation: CandidateValidation | None) -> str:
        if validation is None:
            return (
                "Host validation failed before a structured result was recorded. "
                "Inspect `output/run.py`, make a targeted repair, and leave an updated "
                "`output/` for the host to re-validate.\n"
            )
        command = " ".join(validation.command) if validation.command else "(not run)"
        errors = "\n".join(f"- {item}" for item in validation.errors) or "- (none)"
        failures = "\n\n".join(
            f"--- {name} ---\n{detail}" for name, detail in validation.failures.items()
        ) or "(none)"
        repeated_note = ""
        if validation.skipped_redundant_smoke:
            repeated_note = (
                "The staged deliverable hash and failure fingerprint are unchanged. "
                "Do not rerun the same check hoping it will change. Edit the failing code.\n\n"
            )
        elif validation.repeated:
            repeated_note = (
                "This failure fingerprint has survived a changed candidate twice. "
                "Isolate the smallest failing SDK call and inspect the exact local "
                "OpenJiuwen reference with `openjiuwen_ref_read_file` before editing again.\n\n"
            )
        return (
            "Host validation of `output/` failed. Do not restart the implementation. "
            "Make a targeted repair and leave an updated `output/`.\n\n"
            f"{repeated_note}"
            f"Failing stage: {validation.stage or '(unknown)'}\n"
            f"Failing variant: {validation.variant or '(none)'}\n"
            f"Host command: {command}\n"
            f"Exit code: {validation.exit_code if validation.exit_code is not None else '(n/a)'}\n"
            f"Failure fingerprint: {validation.fingerprint or '(none)'}\n"
            f"Repeated: {'yes' if validation.repeated else 'no'}\n"
            f"Candidate hash: {validation.candidate_hash or '(none)'}\n"
            f"Cycle: {validation.cycle}\n\n"
            f"Metrics-contract / validation errors:\n{errors}\n\n"
            f"Bounded stderr/traceback:\n{validation.stderr_tail or '(none)'}\n\n"
            f"Per-variant failures:\n{failures}\n"
        )

    @staticmethod
    def _promotion_failure_output(
        plan: ExperimentPlan,
        code_dir: Path,
        validation: CandidateValidation,
        agent_message: str,
        exc: BaseException,
    ) -> CodeImplementationOutput:
        files: list[str] = []
        if code_dir.exists():
            files = sorted(
                str(path.relative_to(code_dir)) for path in code_dir.rglob("*") if path.is_file()
            )
        notes = (
            f"promotion failed after a passing candidate; previous generated_code/ retained.\n"
            f"{validation.summary()}\n"
            f"{type(exc).__name__}: {exc}\n"
            f"{traceback.format_exc()}\n"
            f"Agent's final message: {agent_message}"
        )
        return CodeImplementationOutput(
            implementation=CodeImplementationManifest(
                run_id=plan.run_id,
                workspace_dir=str(code_dir),
                files=files,
                variants=list(validation.variants),
                smoke_test_passed=False,
                status="failed",
                readiness="failed",
                smoke_failures={"promotion": f"{type(exc).__name__}: {exc}"},
                notes=notes,
            )
        )

    def _build_output(
        self,
        plan: ExperimentPlan,
        code_dir: Path,
        variant_names: list[str],
        agent_message: str,
        *,
        validation: CandidateValidation | None = None,
        workspace_dir: str | None = None,
    ) -> CodeImplementationOutput:
        if validation is None:
            validation = self._validate_candidate(
                plan.run_id, code_dir, cycle=1, fallback_names=variant_names
            )
        files: list[str] = []
        if code_dir.exists():
            files = sorted(
                str(path.relative_to(code_dir))
                for path in code_dir.rglob("*")
                if path.is_file() and ".git" not in path.parts
            )
        dependencies = self._read_list_file(code_dir / _REQUIREMENTS_FILE)
        assumptions = self._read_list_file(code_dir / _ASSUMPTIONS_FILE)
        smoke_test_passed = validation.ok
        status = "ready" if smoke_test_passed else "failed"
        failures = dict(validation.failures)
        if status == "ready":
            notes = (
                f"{validation.summary()}\n"
                if validation.cycle > 1 or validation.candidate_hash
                else ""
            )
        else:
            failure_blocks = "\n\n".join(
                f"--- {name} ---\n{detail}" for name, detail in failures.items()
            )
            error_block = "\n".join(f"- {item}" for item in validation.errors)
            log_dir = validation.log_dir or active_artifact_dir(plan.run_id, smoke_test_dir(plan.run_id))
            notes = (
                f"{validation.summary()}\n"
                f"{error_block}\n\n"
                f"{failure_blocks}\n\n"
                f"Full logs under {log_dir}. "
                f"Agent's final message: {agent_message}"
            )
        manifest = CodeImplementationManifest(
            run_id=plan.run_id,
            workspace_dir=workspace_dir or str(code_dir),
            files=files,
            variants=list(validation.variants),
            dependencies=dependencies,
            assumptions=assumptions,
            smoke_test_passed=smoke_test_passed,
            status=status,
            readiness="smoke_ready" if status == "ready" else "failed",
            smoke_failures=failures,
            notes=notes.strip(),
        )
        return CodeImplementationOutput(implementation=manifest)

    @staticmethod
    def _read_list_file(path: Path) -> list[str]:
        if not path.exists():
            return []
        return [
            line.lstrip("-*").strip()
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
