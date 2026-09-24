import inspect
from pathlib import Path

import pytest
import openjiuwen.symphony as symphony
import openjiuwen.symphony.orchestration as orchestration
import openjiuwen.symphony.orchestration.graph as graph


def test_matcher_implementations_are_internal() -> None:
    old_names = {
        "CachedOntologyMatcher",
        "OntologyMatcher",
        "OpenAICompatibleOntologyMatcher",
    }

    for module in (symphony, orchestration):
        assert old_names.isdisjoint(module.__all__)
        assert all(not hasattr(module, name) for name in old_names)
    assert not hasattr(graph, "__all__")
    assert all(not hasattr(graph, name) for name in old_names)


def test_internal_packages_do_not_aggregate_implementations() -> None:
    import openjiuwen.symphony.orchestration.graph.matcher as matcher
    import openjiuwen.symphony.orchestration.planning as planning

    assert not hasattr(graph, "__all__")
    assert not hasattr(matcher, "__all__")
    assert not hasattr(planning, "__all__")
    assert not hasattr(graph, "GraphBuildPipeline")
    assert not hasattr(planning, "FastOneShotPlanner")


def test_removed_internal_modules_and_ambiguous_result_name_are_absent() -> None:
    import openjiuwen.symphony.orchestration.graph.models as graph_models

    package_root = Path(symphony.__file__).parent
    removed_paths = (
        "orchestration/graph/builders.py",
        "orchestration/graph/pipeline.py",
        "orchestration/graph/registry.py",
        "orchestration/graph/matcher/constants.py",
        "orchestration/graph/matcher/openai.py",
        "orchestration/graph/matcher/prompt.py",
        "orchestration/graph/matcher/validation.py",
        "shared/llm_payload.py",
    )

    assert all(not (package_root / relative_path).exists() for relative_path in removed_paths)
    assert hasattr(graph_models, "GraphConstructionResult")
    assert not hasattr(graph_models, "GraphBuildResult")


def test_runtime_and_service_do_not_accept_matcher_injection() -> None:
    assert "matcher" not in inspect.signature(symphony.SymphonyRuntime).parameters
    assert "matcher" not in inspect.signature(orchestration.OrchestrationService).parameters


@pytest.mark.asyncio
async def test_build_and_plan_require_model_but_status_does_not(tmp_path: Path) -> None:
    service = orchestration.OrchestrationService(
        graph_artifact_root=tmp_path,
        capability_provider=[],
        model=None,
    )

    assert service.status().exists is False
    with pytest.raises(ValueError, match="requires a model"):
        await service.build()
    with pytest.raises(ValueError, match="requires a model"):
        await service.plan("compose a capability graph")
