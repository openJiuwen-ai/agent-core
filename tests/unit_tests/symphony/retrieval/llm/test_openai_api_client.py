"""Provider truncation is a request failure, not a valid empty selection."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from openjiuwen.symphony.retrieval.llm import LLMRequestError, OpenAICompatibleClient


class ResponseStream:
    def __init__(self, chunks):
        self.chunks = chunks
        self.consumed = 0
        self.closed = False

    def __iter__(self):
        for chunk in self.chunks:
            self.consumed += 1
            yield chunk

    def close(self):
        self.closed = True


def client_returning(response, **kwargs):
    sdk = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=Mock(return_value=response))))
    return OpenAICompatibleClient(sdk, **kwargs)


def stream_chunk(content="", finish_reason=None, usage=None):
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content=content), finish_reason=finish_reason)],
        usage=usage,
    )


@pytest.mark.parametrize("content", ["", "ZX"])
@pytest.mark.parametrize("mapping", [False, True])
def test_completion_rejects_truncated_empty_or_parseable_content(content, mapping):
    choice = {"message": {"content": content}, "finish_reason": "length"}
    response = {"choices": [choice]} if mapping else SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason="length")],
    )
    with pytest.raises(LLMRequestError, match="finish_reason=length"):
        client_returning(response).complete("test", [])


@pytest.mark.parametrize("finish_reason", [None, "stop"])
def test_completion_allows_valid_empty_content(finish_reason):
    response = {"choices": [{"message": {"content": ""}, "finish_reason": finish_reason}]}
    assert client_returning(response).complete("test", []) == [""]


def test_completion_rejects_any_truncated_choice():
    response = {"choices": [
        {"message": {"content": "ZX"}, "finish_reason": "stop"},
        {"message": {"content": "QJ"}, "finish_reason": "length"},
    ]}
    with pytest.raises(LLMRequestError, match="finish_reason=length"):
        client_returning(response).complete("test", [], n=2)


@pytest.mark.parametrize("content", ["", "ZX"])
def test_stream_rejects_truncation_after_delivering_usage_and_closing(content):
    usage = {"completion_tokens": 9, "prompt_tokens": 100, "total_tokens": 109}
    stream = ResponseStream([
        stream_chunk(content),
        stream_chunk(finish_reason="length"),
        SimpleNamespace(choices=[], usage=usage),
    ])
    logger = Mock()
    client = client_returning(stream, log_io=True, logger=logger)
    received = []
    with pytest.raises(LLMRequestError, match="finish_reason=length"):
        for chunk in client.stream_complete("test", []):
            received.append(chunk)
    assert "".join(received) == content
    assert received[-1].usage == usage
    assert stream.consumed == 3
    assert stream.closed
    assert "'completion_tokens': 9" in logger.info.call_args.args[0]


def test_stream_reads_mapping_finish_metadata():
    stream = ResponseStream([{"choices": [{"finish_reason": "length"}]}])
    with pytest.raises(LLMRequestError, match="finish_reason=length"):
        list(client_returning(stream).stream_complete("test", []))
    assert stream.closed


@pytest.mark.parametrize("finish_reason", [None, "stop"])
def test_stream_allows_valid_empty_content(finish_reason):
    stream = ResponseStream([stream_chunk(finish_reason=finish_reason)])
    assert list(client_returning(stream).stream_complete("test", [])) == []
    assert stream.closed


def test_caller_early_stop_does_not_report_unobserved_truncation():
    stream = ResponseStream([stream_chunk("ZX"), stream_chunk(finish_reason="length")])
    policy = SimpleNamespace(should_abort=lambda text: text == "ZX")
    assert list(client_returning(stream).stream_complete("test", [], early_stop=policy)) == ["ZX"]
    assert stream.consumed == 1
    assert stream.closed


def test_observed_provider_truncation_is_not_hidden_by_early_stop():
    stream = ResponseStream([stream_chunk("ZX", finish_reason="length")])
    policy = SimpleNamespace(should_abort=lambda text: text == "ZX")
    with pytest.raises(LLMRequestError, match="finish_reason=length"):
        list(client_returning(stream).stream_complete("test", [], early_stop=policy))
    assert stream.closed
