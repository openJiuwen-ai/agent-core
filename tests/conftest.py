import asyncio
import threading

import pytest

from openjiuwen.core.context_engine import ContextEngine


@pytest.fixture(autouse=True)
def _restore_context_processor_registry():
    """Keep context processor overrides isolated to the test that activates them."""
    processor_map = dict(ContextEngine._PROCESSOR_MAP)
    try:
        yield
    finally:
        ContextEngine._PROCESSOR_MAP.clear()
        ContextEngine._PROCESSOR_MAP.update(processor_map)


@pytest.fixture(autouse=True)
def _usable_current_event_loop(request):
    """Leave every sync test a usable current event loop.

    Sync tests and production code call ``asyncio.get_event_loop()`` on the
    main thread; that raises ``RuntimeError: There is no current event loop``
    once any earlier test in the same worker leaves the current loop unset
    (e.g. a shutdown path ending in ``asyncio.set_event_loop(None)``).
    Provide a fresh loop per sync test and restore a fresh one afterwards so
    a single polluting test cannot break every later sync test in the worker.
    Async tests are skipped: pytest-asyncio manages their loop itself.
    """
    if request.node.get_closest_marker("asyncio"):
        yield
        return
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        yield
    finally:
        asyncio.set_event_loop(asyncio.new_event_loop())
        if not loop.is_closed():
            loop.close()
