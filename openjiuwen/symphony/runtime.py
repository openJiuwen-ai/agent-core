"""Top-level Symphony runtime composition."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Awaitable, Callable, Sequence

from openjiuwen.core.foundation.llm import Model
from openjiuwen.symphony.orchestration import OrchestrationConfig, OrchestrationService, PrepareArtifactHook
from openjiuwen.symphony.orchestration.model import ModelResponseObserver
from openjiuwen.symphony.shared.fingerprint import Fingerprint


class SymphonyRuntime:
    """Compose explicitly injected Symphony services for an application runtime."""

    def __init__(
        self,
        *,
        graph_artifact_root: str | Path,
        capability_provider: Sequence[Any] | Callable[[], Sequence[Any] | Awaitable[Sequence[Any]]],
        model: Model | None,
        model_response_observer: ModelResponseObserver | None = None,
        orchestration_config: OrchestrationConfig | None = None,
        source_snapshot: dict[str, Any] | Callable[[Sequence[Fingerprint]], dict[str, Any]] | None = None,
        graph_config: dict[str, Any] | None = None,
        prepare_artifact: PrepareArtifactHook | None = None,
    ) -> None:
        self.orchestration = OrchestrationService(
            graph_artifact_root=graph_artifact_root,
            capability_provider=capability_provider,
            model=model,
            model_response_observer=model_response_observer,
            config=orchestration_config,
            source_snapshot=source_snapshot,
            graph_config=graph_config,
            prepare_artifact=prepare_artifact,
        )
