import asyncio
import warnings

import pytest

from openjiuwen.core.context_engine import ContextEngine


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
def _restore_context_processor_registry():
    """Keep context processor overrides isolated to the test that activates them."""
    processor_map = dict(ContextEngine._PROCESSOR_MAP)
    try:
        yield
    finally:
        ContextEngine._PROCESSOR_MAP.clear()
        ContextEngine._PROCESSOR_MAP.update(processor_map)
