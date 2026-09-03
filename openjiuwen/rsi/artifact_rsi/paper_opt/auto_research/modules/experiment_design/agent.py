"""Experiment Design Agent built on OpenJiuwen DeepAgent."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.common.checkpointing import bootstrap_checkpointer
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.common.env import load_project_dotenv
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.common.logging import get_logger
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.common.workspace import (
    ensure_design_dir,
    experiment_design_path,
    project_root,
    resolve_project_reference,
    set_project_root,
    to_project_relative,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.extensions.rails.experiment_design_tools import (
    ExperimentDesignToolsRail,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.extensions.rails.observability_rail import with_observability
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.extensions.tools.submit_experiment_design import (
    SubmitExperimentDesignTool,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.experiment_design.artifacts import (
    load_design_frontmatter,
    revise_design_with_research,
    update_design_artifacts,
    write_initial_design_artifacts,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.experiment_design.schemas import (
    ExperimentDesignFeedbackInput,
    ExperimentDesignInput,
    ExperimentDesignOutput,
    ExperimentDesignResearchRevisionInput,
    RecoveryMode,
    design_session_id,
    design_tool_owner_id,
)

AGENT_CARD_ID = "experiment-design-agent"
AGENT_CARD_NAME = "experiment_design"
_SYSTEM_PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "system.md"

logger = get_logger(__name__)

_DEFAULT_CONTEXT_ENGINE_CONFIG: dict[str, Any] = {
    # Soft budget for compression ratios — not the provider's absolute max.
    "context_window_tokens": 200_000,
    "enable_reload": True,
    "enable_tiktoken_counter": True,
    # Keep false once soft budget is pinned; OpenRouter can otherwise replace
    # the denominator with a much larger model window.
    "enable_openrouter_model_context_window_tokens": False,
    "compression_recall_config": {"enabled": False},
    "enable_context_debug": False,
}

_DEFAULT_CONTEXT_PROCESSORS: dict[str, dict[str, Any]] = {
    # ~10k tokens: offload oversized PDF/tool dumps; reload remains available.
    "MessageSummaryOffloader": {
        "add_message_threshold_ratio": 0.05,
        "ttl_seconds": 300,
        "ttl_context_occupancy_ratio": 0.50,
        "ttl_message_threshold_ratio": 0.15,
        "protected_tool_names": [],
    },
    # ~80k: compress completed prior create/update turns in the persistent session.
    "DialogueCompressor": {
        "trigger_context_ratio": 0.40,
        "min_target_context_ratio": 0.05,
    },
    # ~140k: compress within an unusually long single user turn.
    "CurrentRoundCompressor": {
        "trigger_context_ratio": 0.70,
        "min_target_context_ratio": 0.05,
        "keep_recent_messages": 8,
    },
    # ~170k emergency whole-session compaction.
    "RoundLevelCompressor": {
        "trigger_context_ratio": 0.85,
        "min_target_context_ratio": 0.05,
        "keep_recent_messages": 8,
    },
}


def _load_system_prompt() -> str:
    return _SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")


def _context_runtime(
    raw_config: dict[str, Any] | None,
    *,
    model: Any,
) -> tuple[Any | None, Any | None]:
    """Build OpenJiuwen context configuration and its processor rail."""
    config = dict(raw_config or {})
    if config.pop("enabled", True) is False:
        return None, None

    unknown = set(config) - {"engine", "preset", "processors"}
    if unknown:
        raise ValueError(f"unknown experiment_design.context fields: {sorted(unknown)}")

    engine_overrides = config.get("engine") or {}
    processor_overrides = config.get("processors") or {}
    if not isinstance(engine_overrides, dict):
        raise TypeError("experiment_design.context.engine must be a mapping")
    if not isinstance(processor_overrides, dict):
        raise TypeError("experiment_design.context.processors must be a mapping")

    from openjiuwen.core.context_engine import ContextEngineConfig
    from openjiuwen.harness.rails.context_engineer import ContextProcessorRail

    # Drop explicit nulls so YAML `key: null` does not wipe code defaults.
    engine_config = {
        **_DEFAULT_CONTEXT_ENGINE_CONFIG,
        **{k: v for k, v in engine_overrides.items() if v is not None},
    }
    model_name = getattr(getattr(model, "model_config", None), "model_name", None)
    if model_name and not engine_config.get("model_name"):
        engine_config["model_name"] = model_name
    context_engine_config = ContextEngineConfig(**engine_config)

    processors: list[tuple[str, dict[str, Any]]] = []
    unknown_processors = set(processor_overrides) - set(_DEFAULT_CONTEXT_PROCESSORS)
    if unknown_processors:
        raise ValueError(
            "unknown experiment_design.context processor overrides: "
            f"{sorted(unknown_processors)}"
        )
    for name, defaults in _DEFAULT_CONTEXT_PROCESSORS.items():
        override = processor_overrides.get(name) or {}
        if not isinstance(override, dict):
            raise TypeError(
                f"experiment_design.context.processors.{name} must be a mapping"
            )
        processors.append((name, {**defaults, **override}))

    processor_rail = ContextProcessorRail(
        processors=processors,
        preset=bool(config.get("preset", True)),
    )
    return context_engine_config, processor_rail


def _context_has_messages(context_state: Any) -> bool:
    """Return True when restored session context contains prior messages."""
    if not context_state:
        return False
    if isinstance(context_state, dict):
        for value in context_state.values():
            if isinstance(value, dict):
                messages = value.get("messages")
                if isinstance(messages, list) and messages:
                    return True
            elif isinstance(value, list) and value:
                return True
        messages = context_state.get("messages")
        return isinstance(messages, list) and bool(messages)
    return False


_RESOURCE_SUFFIXES = {".md", ".txt", ".json", ".pdf"}
_SUMMARY_NAME = "research_summary.md"


def _is_hidden(path: Path) -> bool:
    return any(part.startswith(".") for part in path.parts)


def expand_resource_paths(paths: list[str], *, root: Path) -> list[str]:
    """Resolve files/dirs into a stable, deduplicated project-relative file list.

    Directories are scanned recursively for ``.md``, ``.txt``, ``.json``, and
    ``.pdf`` files (hidden paths skipped). Explicit files of any suffix are kept
    as-is after existence checks.
    """
    collected: list[str] = []
    seen: set[str] = set()

    def _add(abs_path: Path) -> None:
        rel = to_project_relative(abs_path, root=root)
        if rel not in seen:
            seen.add(rel)
            collected.append(rel)

    for path in paths:
        abs_path = resolve_project_reference(path, root=root)
        if not abs_path.exists():
            raise FileNotFoundError(f"referenced path does not exist: {path}")
        if abs_path.is_file():
            _add(abs_path)
            continue
        if not abs_path.is_dir():
            raise FileNotFoundError(f"referenced path is not a file or directory: {path}")
        matches = sorted(
            (
                candidate
                for candidate in abs_path.rglob("*")
                if candidate.is_file()
                and candidate.suffix.lower() in _RESOURCE_SUFFIXES
                and not _is_hidden(candidate.relative_to(abs_path))
            ),
            key=lambda p: p.as_posix().lower(),
        )
        if not matches:
            raise FileNotFoundError(
                f"directory has no .md/.txt/.json/.pdf resources: {path}"
            )
        for match in matches:
            _add(match)

    if not collected:
        raise FileNotFoundError("resource_paths expanded to an empty file list")
    return collected


def prefer_research_summary(paths: list[str]) -> list[str]:
    """Move any ``research_summary.md`` entries to the front of the list."""
    summaries = [p for p in paths if Path(p).name == _SUMMARY_NAME]
    others = [p for p in paths if Path(p).name != _SUMMARY_NAME]
    return summaries + others


def _require_existing_paths(paths: list[str], *, root: Path) -> list[str]:
    """Expand dirs and validate that every resulting path exists as a file."""
    return prefer_research_summary(expand_resource_paths(paths, root=root))


def _build_model_from_config(config: dict[str, Any]):
    from openjiuwen.core.foundation.llm import Model
    from openjiuwen.core.foundation.llm.schema.config import (
        ModelClientConfig,
        ModelRequestConfig,
    )

    load_project_dotenv()
    oj = dict(config.get("openjiuwen") or {})
    api_key_env = oj.get("api_key_env", "API_KEY")
    api_key = os.getenv(api_key_env, "").strip() or os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            f"missing model API credentials; set environment variable {api_key_env} "
            f"or add it to {project_root() / '.env'}"
        )

    def _cfg_str(key: str) -> str | None:
        value = oj.get(key)
        if value is None:
            return None
        text = str(value).strip()
        if not text or text.lower() == "default":
            return None
        return text

    def _cfg_or(key: str, default):
        # dict.get(key, default) only falls back when the key is *absent* —
        # an explicit `key: null` in yaml (present, value None) bypasses the
        # default and passes None straight to int()/float()/bool(), which
        # raises. Every raw oj.get(key, default) call below needs this, not
        # just _cfg_str's three string fields (observed crash: timeout).
        value = oj.get(key)
        return default if value is None else value

    api_base = _cfg_str("base_url") or os.getenv("API_BASE") or "https://api.openai.com/v1"
    model_name = _cfg_str("model") or os.getenv("MODEL_NAME") or "gpt-4.1-mini"
    provider = _cfg_str("provider") or os.getenv("MODEL_PROVIDER") or "OpenAI"
    return Model(
        model_client_config=ModelClientConfig(
            client_provider=provider,
            api_key=api_key,
            api_base=api_base,
            timeout=int(_cfg_or("timeout", os.getenv("MODEL_TIMEOUT", "360"))),
            verify_ssl=bool(_cfg_or("verify_ssl", False)),
        ),
        model_config=ModelRequestConfig(
            model_name=model_name,
            temperature=float(_cfg_or("temperature", 0.2)),
            top_p=float(_cfg_or("top_p", 0.9)),
        ),
    )


def _build_create_query(inputs: ExperimentDesignInput, *, request_id: str) -> str:
    resources = "\n".join(f"- {path}" for path in inputs.research.resource_paths)
    branch = inputs.branch or "null"
    summary_hint = ""
    if any(Path(path).name == _SUMMARY_NAME for path in inputs.research.resource_paths):
        summary_hint = (
            f"Read `{_SUMMARY_NAME}` first (listed at the top of RESOURCE PATHS), "
            "then inspect the remaining sources as needed.\n"
        )
    return (
        "MODE: create\n"
        f"RUN_ID: {inputs.run_id}\n"
        f"REQUEST_ID: {request_id}\n"
        f"BRANCH: {branch}\n"
        "TASK: Create the initial experiment design from the research resources below.\n"
        f"{summary_hint}"
        "Use read/search tools to inspect the resource paths. "
        "Call submit_experiment_design exactly once when done.\n\n"
        f"RESOURCE PATHS:\n{resources}\n"
    )


def _build_update_query(
    inputs: ExperimentDesignFeedbackInput,
    *,
    request_id: str,
    design_path: str,
    recovery_mode: RecoveryMode,
    current_revision: int,
) -> str:
    feedback = inputs.feedback
    metrics = "\n".join(
        f"- {key}: {value}" for key, value in feedback.observed_metrics.items()
    ) or "- (none)"
    results = "\n".join(f"- {path}" for path in feedback.result_paths)
    recovery = ""
    if recovery_mode == "artifacts":
        recovery = (
            "RECOVERY: Conversation checkpoint is missing or empty. "
            "Before reasoning, read the full canonical design and revision log at "
            f"{design_path}.\n\n"
        )
    return (
        f"{recovery}"
        "MODE: update\n"
        f"RUN_ID: {inputs.run_id}\n"
        f"REQUEST_ID: {request_id}\n"
        f"CURRENT_REVISION: {current_revision}\n"
        f"CURRENT_DESIGN_PATH: {design_path}\n"
        f"VERDICT: {feedback.verdict}\n"
        f"BRANCH: {feedback.branch}\n"
        f"CODE_SESSION_ID: {feedback.code_session_id}\n"
        f"EVALUATOR_SESSION_ID: {feedback.evaluator_session_id}\n"
        f"EVALUATED_AT: {feedback.evaluated_at.isoformat()}\n"
        "TASK: Update the experiment design from evaluator feedback. "
        "Do not replay prior conversation. Treat this as the next user turn. "
        "Call submit_experiment_design exactly once when done.\n\n"
        f"EVALUATOR SUMMARY:\n{feedback.summary}\n\n"
        f"OBSERVED METRICS:\n{metrics}\n\n"
        f"RESULT PATHS:\n{results}\n"
    )


def _build_revise_research_query(
    inputs: ExperimentDesignResearchRevisionInput,
    *,
    request_id: str,
    design_path: str,
    recovery_mode: RecoveryMode,
    current_revision: int,
    research_paths: list[str],
) -> str:
    resources = "\n".join(f"- {path}" for path in research_paths)
    recovery = ""
    if recovery_mode == "artifacts":
        recovery = (
            "RECOVERY: Conversation checkpoint is missing or empty. "
            "Before reasoning, read the full canonical design and revision log at "
            f"{design_path}.\n\n"
        )
    return (
        f"{recovery}"
        "MODE: revise_research\n"
        f"RUN_ID: {inputs.run_id}\n"
        f"REQUEST_ID: {request_id}\n"
        f"CURRENT_REVISION: {current_revision}\n"
        f"CURRENT_DESIGN_PATH: {design_path}\n"
        "TASK: Incorporate supplemental research into the living design. "
        "Do not treat this as evaluator feedback. Update grounding and the "
        "current experiment only where the new evidence requires it. "
        "Call submit_experiment_design exactly once when done.\n\n"
        f"REASON:\n{inputs.reason}\n\n"
        f"RESOURCE PATHS:\n{resources}\n"
    )


class ExperimentDesignAgent:
    """Turns research briefs / evaluator feedback into Markdown design artifacts."""

    def __init__(
        self,
        config: dict[str, Any],
        *,
        model: Any | None = None,
        agent: Any | None = None,
        project_root_path: str | Path | None = None,
        runner: Any | None = None,
        agent_factory: Callable[..., Any] | None = None,
    ):
        self.config = config
        self._injected_model = model
        self._injected_agent = agent
        self._agent_factory = agent_factory
        self._runner = runner
        self._root = Path(project_root_path).resolve() if project_root_path else project_root()
        set_project_root(self._root)
        self._design_cfg = dict(config.get("experiment_design") or {})
        self._last_recovery_mode: RecoveryMode = "checkpoint"
        self._checkpointer_ready = False

    @property
    def last_recovery_mode(self) -> RecoveryMode:
        return self._last_recovery_mode

    async def _ensure_checkpointer(self) -> None:
        if self._checkpointer_ready:
            return
        await bootstrap_checkpointer(
            self._design_cfg.get("checkpointer"),
            root=self._root,
        )
        self._checkpointer_ready = True

    def _create_deep_agent(self, *, run_id: str, submit_tool: SubmitExperimentDesignTool):
        if self._injected_agent is not None:
            return self._injected_agent
        if self._agent_factory is not None:
            return self._agent_factory(run_id=run_id, submit_tool=submit_tool)

        from openjiuwen.core.single_agent.schema.agent_card import AgentCard
        from openjiuwen.harness import create_deep_agent

        model = self._injected_model or _build_model_from_config(self.config)
        max_iterations = int(self._design_cfg.get("max_iterations", 12))
        skills = self._design_cfg.get("skills") or []
        context_engine_config, context_processor_rail = _context_runtime(
            self._design_cfg.get("context"),
            model=model,
        )
        rails = [ExperimentDesignToolsRail(enable_read_image_multimodal=False)]
        if context_processor_rail is not None:
            rails.insert(0, context_processor_rail)
        rails = with_observability(rails)
        kwargs: dict[str, Any] = {
            "model": model,
            "card": AgentCard(
                id=AGENT_CARD_ID,
                name=AGENT_CARD_NAME,
                description="Designs and revises linear experiment plans.",
            ),
            "tool_owner_id": design_tool_owner_id(run_id),
            "system_prompt": _load_system_prompt(),
            "tools": [submit_tool],
            "rails": rails,
            "enable_task_loop": False,
            "max_iterations": max_iterations,
            "cwd": str(self._root),
            "project_root": str(self._root),
            "restrict_to_work_dir": True,
            "auto_create_workspace": False,
            "language": "en",
        }
        if skills:
            kwargs["skills"] = skills
        if context_engine_config is not None:
            kwargs["context_engine_config"] = context_engine_config
        return create_deep_agent(**kwargs)

    async def _execute_session_turn(
        self,
        *,
        run_id: str,
        request_id: str,
        query_builder: Callable[[RecoveryMode], str],
        submit_tool: SubmitExperimentDesignTool,
        expect_prior_context: bool,
        session_epoch: int = 0,
    ) -> tuple[Any, RecoveryMode]:
        await self._ensure_checkpointer()

        from openjiuwen.core.session.agent import Session

        session_id = design_session_id(run_id, epoch=session_epoch)
        agent = self._create_deep_agent(run_id=run_id, submit_tool=submit_tool)
        session = Session(session_id=session_id, card=getattr(agent, "card", None))
        submit_tool.reset(session_id=session_id, request_id=request_id)
        payload: dict[str, Any] = {"query": "", "conversation_id": session_id}
        recovery: RecoveryMode = "checkpoint"

        try:
            await session.pre_run(inputs=payload)
            has_messages = _context_has_messages(session.get_state("context"))
            if expect_prior_context and not has_messages:
                recovery = "artifacts"
            elif not expect_prior_context and has_messages:
                # Only reachable via acreate(), which has already verified
                # on-disk that no design exists for run_id — the authoritative
                # check. A committed checkpoint session with no design file
                # means a prior create() attempt got through the LLM turn
                # (and so committed on post_run) but then failed *after*
                # _execute_session_turn returned, before any artifact was
                # written — e.g. a draft that fails validation in
                # write_initial_design_artifacts. Refusing here would
                # permanently strand run_id: create() keeps hitting this
                # guard, and update() has no on-disk design to update from
                # either. Log and continue against the stale session instead
                # — the model even benefits from seeing its own prior failed
                # attempt in history.
                logger.warning(
                    "experiment_design create for run_id=%r found a committed "
                    "checkpoint session with no design file on disk (likely a "
                    "prior create attempt that failed after the LLM turn "
                    "completed); continuing against the existing session "
                    "instead of refusing.",
                    run_id,
                )

            payload["query"] = query_builder(recovery)
            if self._runner is not None:
                await self._runner(agent, payload, session=session)
            else:
                from openjiuwen.core.runner import Runner

                await Runner.run_agent(agent, payload, session=session)
        finally:
            try:
                await session.post_run()
            except Exception:  # noqa: BLE001, S110 - best-effort commit/close
                pass

        return (
            submit_tool.require_submission(session_id=session_id, request_id=request_id),
            recovery,
        )

    async def acreate(self, inputs: ExperimentDesignInput) -> ExperimentDesignOutput:
        resource_paths = _require_existing_paths(inputs.research.resource_paths, root=self._root)
        ensure_design_dir(inputs.run_id)
        if experiment_design_path(inputs.run_id).exists():
            raise RuntimeError(
                f"design already exists for run_id={inputs.run_id!r}; use update instead"
            )

        request_id = f"create:{inputs.run_id}:0"
        create_inputs = inputs.model_copy(
            update={
                "research": inputs.research.model_copy(
                    update={"resource_paths": resource_paths}
                )
            }
        )
        submit_tool = SubmitExperimentDesignTool()
        draft, recovery = await self._execute_session_turn(
            run_id=inputs.run_id,
            request_id=request_id,
            query_builder=lambda _recovery: _build_create_query(
                create_inputs, request_id=request_id
            ),
            submit_tool=submit_tool,
            expect_prior_context=False,
            session_epoch=inputs.session_epoch,
        )
        plan = write_initial_design_artifacts(
            run_id=inputs.run_id,
            draft=draft,
            research_paths=resource_paths,
            branch=inputs.branch,
            recovery_mode=recovery,
        )
        self._last_recovery_mode = recovery
        return ExperimentDesignOutput(plan=plan)

    async def aupdate(self, inputs: ExperimentDesignFeedbackInput) -> ExperimentDesignOutput:
        design_path = experiment_design_path(inputs.run_id)
        if not design_path.is_file():
            raise FileNotFoundError(f"current design not found for run_id={inputs.run_id!r}")

        frontmatter = load_design_frontmatter(inputs.run_id)
        if frontmatter.run_id != inputs.run_id:
            raise ValueError(
                f"run_id mismatch: input={inputs.run_id!r} frontmatter={frontmatter.run_id!r}"
            )
        if frontmatter.status in ("accepted", "stopped") and not inputs.allow_after_terminal:
            raise ValueError(
                f"refusing to update terminal design status={frontmatter.status!r}"
            )

        result_paths = _require_existing_paths(inputs.feedback.result_paths, root=self._root)
        feedback = inputs.feedback.model_copy(update={"result_paths": result_paths})
        next_revision = frontmatter.revision + 1
        request_id = f"feedback:{inputs.run_id}:{next_revision}"
        design_rel = to_project_relative(design_path, root=self._root)
        feedback_input = ExperimentDesignFeedbackInput(
            run_id=inputs.run_id,
            feedback=feedback,
            allow_after_terminal=inputs.allow_after_terminal,
        )
        submit_tool = SubmitExperimentDesignTool()
        draft, recovery = await self._execute_session_turn(
            run_id=inputs.run_id,
            request_id=request_id,
            query_builder=lambda mode: _build_update_query(
                feedback_input,
                request_id=request_id,
                design_path=design_rel,
                recovery_mode=mode,
                current_revision=frontmatter.revision,
            ),
            submit_tool=submit_tool,
            expect_prior_context=True,
            session_epoch=inputs.session_epoch,
        )
        plan = update_design_artifacts(
            run_id=inputs.run_id,
            draft=draft,
            feedback=feedback,
            recovery_mode=recovery,
            allow_after_terminal=inputs.allow_after_terminal,
        )
        self._last_recovery_mode = recovery
        return ExperimentDesignOutput(plan=plan)

    async def arevise_research(
        self, inputs: ExperimentDesignResearchRevisionInput
    ) -> ExperimentDesignOutput:
        design_path = experiment_design_path(inputs.run_id)
        if not design_path.is_file():
            raise FileNotFoundError(f"current design not found for run_id={inputs.run_id!r}")

        frontmatter = load_design_frontmatter(inputs.run_id)
        if frontmatter.status in ("accepted", "stopped"):
            raise ValueError(
                f"refusing to revise terminal design status={frontmatter.status!r}"
            )
        extra_paths = _require_existing_paths(inputs.additional_research_paths, root=self._root)
        merged_paths = prefer_research_summary(
            list(dict.fromkeys([*frontmatter.research_paths, *extra_paths]))
        )
        next_revision = frontmatter.revision + 1
        request_id = f"research:{inputs.run_id}:{next_revision}"
        design_rel = to_project_relative(design_path, root=self._root)
        revision_input = ExperimentDesignResearchRevisionInput(
            run_id=inputs.run_id,
            additional_research_paths=extra_paths,
            reason=inputs.reason,
        )
        submit_tool = SubmitExperimentDesignTool()
        draft, recovery = await self._execute_session_turn(
            run_id=inputs.run_id,
            request_id=request_id,
            query_builder=lambda mode: _build_revise_research_query(
                revision_input,
                request_id=request_id,
                design_path=design_rel,
                recovery_mode=mode,
                current_revision=frontmatter.revision,
                research_paths=merged_paths,
            ),
            submit_tool=submit_tool,
            expect_prior_context=True,
            session_epoch=inputs.session_epoch,
        )
        plan = revise_design_with_research(
            run_id=inputs.run_id,
            draft=draft,
            additional_research_paths=extra_paths,
            reason=inputs.reason,
            recovery_mode=recovery,
        )
        self._last_recovery_mode = recovery
        return ExperimentDesignOutput(plan=plan)

    def create(self, inputs: ExperimentDesignInput) -> ExperimentDesignOutput:
        return asyncio.run(self.acreate(inputs))

    def update(self, inputs: ExperimentDesignFeedbackInput) -> ExperimentDesignOutput:
        return asyncio.run(self.aupdate(inputs))

    def revise_research(
        self, inputs: ExperimentDesignResearchRevisionInput
    ) -> ExperimentDesignOutput:
        return asyncio.run(self.arevise_research(inputs))

    def run(self, inputs: ExperimentDesignInput) -> ExperimentDesignOutput:
        """Pipeline compatibility entry point (create mode)."""
        return self.create(inputs)
