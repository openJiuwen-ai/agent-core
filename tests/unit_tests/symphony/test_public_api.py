import pytest

from openjiuwen import symphony
from openjiuwen.harness import rails
from openjiuwen.symphony.agent import AgenticSkillRetrievalToolkit
from openjiuwen.symphony.retrieval.common.prompts import INDEXING_YAML, get_prompt
from openjiuwen.symphony.retrieval.build.workflows import BuildConfig, IndexBuilder
from openjiuwen.symphony.retrieval.search.artifacts import load_retriever_index
from openjiuwen.symphony.retrieval.search.runtime.lexical import LexicalIndex


def test_public_import_paths() -> None:
    assert callable(load_retriever_index)
    assert LexicalIndex is not None
    assert symphony.agent.AgenticSkillRetrievalToolkit is AgenticSkillRetrievalToolkit
    for name in ("RequestConfig", "Retriever", "RetrieverConfig"):
        assert not hasattr(symphony.retrieval.search, name)
    for name in ("AgenticRetrievalConfig", "SkillIndexRuntimeConfig"):
        assert not hasattr(symphony.agent, name)
    assert symphony.CombinationCandidate("recipe-1", 1).version == 1
    assert symphony.EvolutionSubmitResult(None).new_candidates == ()
    assert symphony.LLMPackageReviewAgent is not None
    assert not hasattr(symphony, "CapabilityEvidence")
    assert not hasattr(symphony, "PortMapping")
    assert rails.GraphSnapshotProvider is not None
    assert rails.SymphonyEvolutionSubmitCallback is not None


def test_builtin_prompt_resource_is_available() -> None:
    prompt = get_prompt(INDEXING_YAML, "group_discovery")

    assert prompt.strip()


@pytest.mark.parametrize("operation", ["build", "add", "delete"])
def test_index_operations_reject_retired_runtime_config_alias(operation) -> None:
    with pytest.raises(TypeError, match="unexpected keyword argument 'runtime_config'"):
        getattr(IndexBuilder, operation)(runtime_config=BuildConfig())


@pytest.mark.parametrize("option", ["retrieval_config", "visible_skill_names"])
def test_index_toolkit_rejects_retired_browser_inputs(tmp_path, option) -> None:
    with pytest.raises(TypeError, match=f"unexpected keyword argument '{option}'"):
        AgenticSkillRetrievalToolkit(index_root=tmp_path, **{option: None})


def test_index_toolkit_does_not_publish_retired_browser_methods() -> None:
    for name in ("branch_explore", "branch_peek", "render_retrieval_prompt", "update_visible_skills", "close"):
        assert not hasattr(AgenticSkillRetrievalToolkit, name)
