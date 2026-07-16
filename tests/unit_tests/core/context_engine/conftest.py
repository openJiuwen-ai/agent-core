import pytest

from openjiuwen.core.context_engine import ContextEngine


@pytest.fixture
def refactored_context_processors():
    """Activate refactored processors for one test without leaking registry state."""
    processor_map = dict(ContextEngine._PROCESSOR_MAP)
    from openjiuwen.core.context_engine.processor import forked

    forked.activate()
    try:
        yield
    finally:
        ContextEngine._PROCESSOR_MAP.clear()
        ContextEngine._PROCESSOR_MAP.update(processor_map)
