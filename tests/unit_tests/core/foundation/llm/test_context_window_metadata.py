from openjiuwen.core.foundation.llm import ModelClientConfig, ModelRequestConfig, UserMessage
from openjiuwen.core.foundation.llm.model_clients.openai_model_client import OpenAIModelClient


def test_context_window_is_formal_model_metadata_and_not_provider_param():
    model_config = ModelRequestConfig(model="test-model", context_window=131072)
    client = OpenAIModelClient(
        model_config=model_config,
        model_client_config=ModelClientConfig(
            client_provider="OpenAI",
            api_key="test-key",
            api_base="http://localhost",
        ),
    )

    request_params = client._build_request_params(
        messages=[UserMessage(content="hello")],
        tools=None,
        temperature=None,
        top_p=None,
        model=None,
        stop=None,
        max_tokens=None,
        stream=False,
    )

    assert model_config.context_window == 131072
    assert request_params.get("context_window") is None
