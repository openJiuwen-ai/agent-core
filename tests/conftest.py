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
