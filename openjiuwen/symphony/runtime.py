"""Top-level Symphony runtime composition."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Sequence

from openjiuwen.symphony.interfaces import LLMClient, OrchestrationCapabilityProvider
from openjiuwen.symphony.orchestration import OrchestrationConfig, OrchestrationService, PrepareArtifactHook
from openjiuwen.symphony.orchestration.graph import OntologyMatcher
from openjiuwen.symphony.shared.fingerprint import Fingerprint


class SymphonyRuntime:
    """Compose explicitly injected Symphony services for an application runtime."""

    def __init__(
        self,
        *,
        graph_artifact_root: str | Path,
        capability_provider: OrchestrationCapabilityProvider | Sequence[Any],
        llm_client: LLMClient | None,
        orchestration_config: OrchestrationConfig | None = None,
        matcher: OntologyMatcher | None = None,
        source_snapshot: dict[str, Any] | Callable[[Sequence[Fingerprint]], dict[str, Any]] | None = None,
        graph_config: dict[str, Any] | None = None,
        prepare_artifact: PrepareArtifactHook | None = None,
    ) -> None:
        self.orchestration = OrchestrationService(
            graph_artifact_root=graph_artifact_root,
            capability_provider=capability_provider,
            llm_client=llm_client,
            config=orchestration_config,
            matcher=matcher,
            source_snapshot=source_snapshot,
            graph_config=graph_config,
            prepare_artifact=prepare_artifact,
        )
