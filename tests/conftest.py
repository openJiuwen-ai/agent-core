from types import SimpleNamespace

import pytest

from openjiuwen.core.context_engine import ContextEngine


@pytest.fixture(autouse=True)
def _mock_tiktoken_for_unit_tests(monkeypatch, request):
    """Use reversible code-point tokens in UTs instead of downloading BPE vocab."""
    if "unit_tests" not in request.path.parts:
        return

    import tiktoken

    available_names = set(tiktoken.list_encoding_names())
    encoders = {}

    def encode(text, **_kwargs):
        return [ord(char) for char in text]

    def decode(token_ids, **_kwargs):
        return "".join(chr(token_id) for token_id in token_ids)

    def get_encoding(name):
        if name not in available_names:
            raise ValueError(f"Unknown encoding {name}")
        if name not in encoders:
            encoders[name] = SimpleNamespace(name=name, encode=encode, decode=decode)
        return encoders[name]

    def encoding_for_model(model):
        return get_encoding(tiktoken.encoding_name_for_model(model))

    monkeypatch.setattr(tiktoken, "get_encoding", get_encoding)
    monkeypatch.setattr(tiktoken, "encoding_for_model", encoding_for_model)


@pytest.fixture(autouse=True)
def _restore_context_processor_registry():
    """Keep context processor overrides isolated to the test that activates them."""
    processor_map = dict(ContextEngine._PROCESSOR_MAP)
    try:
        yield
    finally:
        ContextEngine._PROCESSOR_MAP.clear()
        ContextEngine._PROCESSOR_MAP.update(processor_map)
