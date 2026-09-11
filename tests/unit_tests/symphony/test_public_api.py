from openjiuwen import symphony
from openjiuwen.harness import rails
from openjiuwen.symphony.agent import AgenticSkillRetrievalToolkit
from openjiuwen.symphony.retrieval.common.prompts import INDEXING_YAML, get_prompt
from openjiuwen.symphony.retrieval.search import RequestConfig, Retriever, RetrieverConfig


def test_public_import_paths() -> None:
    assert symphony.retrieval.search.Retriever is Retriever
    assert symphony.agent.AgenticSkillRetrievalToolkit is AgenticSkillRetrievalToolkit
    assert RetrieverConfig().top_k == 10
    assert RequestConfig(top_k=3).top_k == 3
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
