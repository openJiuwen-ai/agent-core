import asyncio
import sys
import warnings
from types import SimpleNamespace

import pytest

from openjiuwen.core.context_engine import ContextEngine
from openjiuwen.core.runner.resources_manager.tool_manager import ToolMgr

_BROWSER_TOOLS_MODULE = "openjiuwen.harness.tools.browser_move.playwright_runtime.browser_tools"


@pytest.fixture(autouse=True)
def _restore_mcp_client_factory():
    """Keep browser_move's MCP client patch isolated to the test that installs it.

    ``ensure_browser_runtime_client_patch`` rebinds ``ToolMgr._create_client`` for the
    whole process and latches itself, so any test that starts a browser runtime makes
    every later ``add_mcp_server`` build browser_move client subclasses instead of the
    core ones.
    """
    create_client = ToolMgr.__dict__["_create_client"]
    try:
        yield
    finally:
        if ToolMgr.__dict__["_create_client"] is not create_client:
            ToolMgr._create_client = create_client
            browser_tools = sys.modules.get(_BROWSER_TOOLS_MODULE)
            if browser_tools is not None:
                browser_tools._OPENJIUWEN_CLIENTS_PATCHED = False


@pytest.fixture(autouse=True)
def _ensure_main_thread_event_loop():
    """Keep a usable current event loop in the main thread for sync tests.

    pytest-asyncio runs async tests through ``asyncio.Runner``, whose
    ``close()`` teardown calls ``asyncio.set_event_loop(None)``. Sync tests
    scheduled after an async test on the same xdist worker then fail with
    "RuntimeError: There is no current event loop in thread 'MainThread'"
    when they build graph vertices (``Vertex.__init__`` creates an
    ``asyncio.Future``). Restore a plain loop when the current one is gone;
    async tests manage their own loop via pytest-asyncio fixtures.
    """
    with warnings.catch_warnings():
        # 隐式创建分支在 3.10+ 会发 DeprecationWarning，这里仅探测不依赖它
        warnings.simplefilter("ignore", DeprecationWarning)
        try:
            asyncio.get_event_loop_policy().get_event_loop()
        except RuntimeError:
            asyncio.set_event_loop(asyncio.new_event_loop())
    yield


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
