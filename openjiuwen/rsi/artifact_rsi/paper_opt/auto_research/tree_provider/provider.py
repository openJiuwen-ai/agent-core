"""PaperArtifactProviderImpl — the Provider surface from
docs/autoresearch_endpoint.md, implemented as a thin wrapper over
PaperTreeOrchestrator + TaskStorage. See
docs/paper_tree_orchestrator_design.md "Provider surface".

NOTE: the Provider Protocol's read methods (`read_state`/`read_report`/
`get_tree`/`locate_artifact`) take only `task_id`, not `run_dir` — so this
implementation keeps an in-memory `task_id -> run_dir` registry populated
by `run()`. That registry does not survive a process restart; recovering
it (e.g. from an AgentServer-side task table) is the same open problem as
docs/paper_tree_orchestrator_design.md "Risks" §3 ("outer-task resume"),
not solved here.
"""

from __future__ import annotations

import os

from openjiuwen.rsi.artifact_rsi.request import ArtifactEngineRequest
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.tree_provider.orchestrator import PaperTreeOrchestrator
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.tree_provider.projection import (
    project_engine_report,
    project_engine_state,
    project_tree_response,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.tree_provider.schemas import (
    ArtifactRef,
    ArtifactValidationResult,
    EngineReport,
    EngineResult,
    EngineState,
    OnEvent,
    TreeResponse,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.tree_provider.storage import TaskStorage


class PaperArtifactProviderImpl:
    """artifact_type = "paper". Single-process implementation: one active
    `PaperTreeOrchestrator` per `task_id`, held for this process's
    lifetime. A multi-worker-process AgentServer deployment needs an
    out-of-process task registry instead — out of scope here."""

    artifact_type = "paper"

    def __init__(self) -> None:
        self._orchestrators: dict[str, PaperTreeOrchestrator] = {}
        self._run_dirs: dict[str, str] = {}

    def validate_input(self, artifact_path: str | None) -> ArtifactValidationResult:
        if artifact_path is None:
            return ArtifactValidationResult(valid=True, errors=[])
        if not os.path.exists(artifact_path):
            return ArtifactValidationResult(
                valid=False,
                errors=[
                    {
                        "code": "ARTIFACT_PATH_NOT_FOUND",
                        "message": f"{artifact_path!r} does not exist",
                    }
                ],
            )
        return ArtifactValidationResult(valid=True, errors=[])

    async def run(
        self,
        request: ArtifactEngineRequest,
        on_event: OnEvent | None = None,
    ) -> EngineResult:
        orchestrator = PaperTreeOrchestrator(
            task_id=request.task_id,
            run_dir=request.run_dir,
            max_iterations=request.max_iterations,
            optimization_instruction=request.optimization_instruction,
            artifact_path=request.artifact_path,
            model=request.model,
            on_event=on_event,
        )
        self._orchestrators[request.task_id] = orchestrator
        self._run_dirs[request.task_id] = request.run_dir
        state = await orchestrator.start()
        return EngineResult(
            task_id=request.task_id,
            status=state.status,
            final_node_id=state.best_node_id,
        )

    async def pause(self, task_id: str, on_event: OnEvent | None = None) -> EngineResult:
        return EngineResult(
            task_id=task_id,
            status="running",
            error_code="SCENARIO_NOT_SUPPORTED",
            error_message="paper scenario does not support pause",
        )

    async def resume(
        self,
        request: ArtifactEngineRequest,
        on_event: OnEvent | None = None,
    ) -> EngineResult:
        return EngineResult(
            task_id=request.task_id,
            status="running",
            error_code="SCENARIO_NOT_SUPPORTED",
            error_message="paper scenario does not support resume",
        )

    def read_state(self, task_id: str) -> EngineState:
        state = self._require_task_state(task_id)
        return project_engine_state(state)

    def read_report(self, task_id: str) -> EngineReport:
        storage = self._storage_for(task_id)
        state = self._require_task_state(task_id)
        artifacts = list(storage.load_artifacts().values())
        return project_engine_report(state, artifact_index=artifacts)

    def get_tree(self, task_id: str) -> TreeResponse:
        storage = self._storage_for(task_id)
        state = self._require_task_state(task_id)
        return project_tree_response(state, storage.load_tree())

    def locate_artifact(self, task_id: str, artifact_id: str | None = None) -> ArtifactRef:
        storage = self._storage_for(task_id)
        index = storage.load_artifacts()
        if artifact_id is not None:
            ref = index.get(artifact_id)
            if ref is None:
                raise KeyError(f"no artifact {artifact_id!r} for task_id={task_id!r}")
            return ref
        state = self._require_task_state(task_id)
        if state.best_node_id is None:
            raise KeyError(f"no final artifact for task_id={task_id!r}")
        for ref in index.values():
            if ref.node_id == state.best_node_id:
                return ref
        raise KeyError(f"best node {state.best_node_id!r} has no artifact for task_id={task_id!r}")

    async def terminate(self, task_id: str, on_event: OnEvent | None = None) -> EngineResult:
        orchestrator = self._orchestrators.get(task_id)
        if orchestrator is not None:
            await orchestrator.terminate()
        state = self._storage_for(task_id).load_task_state()
        return EngineResult(
            task_id=task_id,
            status="terminated",
            final_node_id=state.best_node_id if state else None,
        )

    # -- helpers --------------------------------------------------------------
    def _storage_for(self, task_id: str) -> TaskStorage:
        run_dir = self._run_dirs.get(task_id)
        if run_dir is None:
            raise KeyError(
                f"unknown task_id={task_id!r}: this in-process provider only knows "
                "about tasks started via run() in the current process"
            )
        return TaskStorage(run_dir)

    def _require_task_state(self, task_id: str):
        state = self._storage_for(task_id).load_task_state()
        if state is None:
            raise KeyError(f"no state for task_id={task_id!r}")
        return state
