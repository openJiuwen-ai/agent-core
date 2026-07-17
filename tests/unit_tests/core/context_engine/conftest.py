import pytest

from openjiuwen.core.context_engine import ContextEngine


@pytest.fixture(autouse=True)
def _isolate_offload_cwd(tmp_path, monkeypatch):
    """Run every test in this package from a temp dir.

    Offload paths are relative ("memory/offloads/<session>/...", see
    ``processor/base.py``), so a processor with no workspace configured writes
    them under the current directory -- which for a test run is the repo root.
    ``monkeypatch.chdir`` restores the original directory at teardown.
    """
    monkeypatch.chdir(tmp_path)


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
