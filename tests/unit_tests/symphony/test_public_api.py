from openjiuwen import symphony
from openjiuwen.symphony.agent import AgenticSkillRetrievalToolkit
from openjiuwen.symphony.retrieval.common.prompts import INDEXING_YAML, get_prompt
from openjiuwen.symphony.retrieval.search import RequestConfig, Retriever, RetrieverConfig


def test_public_import_paths() -> None:
    assert symphony.retrieval.search.Retriever is Retriever
    assert symphony.agent.AgenticSkillRetrievalToolkit is AgenticSkillRetrievalToolkit
    assert RetrieverConfig().top_k == 10
    assert RequestConfig(top_k=3).top_k == 3


def test_builtin_prompt_resource_is_available() -> None:
    prompt = get_prompt(INDEXING_YAML, "group_discovery")

    assert prompt.strip()
