import os
import shutil
from pathlib import Path

import pytest

from openjiuwen.core.context_engine import ContextEngine


@pytest.fixture(scope="session", autouse=True)
def _offline_tiktoken_cache(tmp_path_factory):
    """Use the checked-in cl100k_base BPE file instead of CI network access."""
    source_dir = Path(__file__).resolve().parent / "resources" / "tiktoken_cache"
    cache_dir = tmp_path_factory.mktemp("tiktoken-cache")
    source = source_dir / "9b5ad71b2ce5302211f9c61530b329a4922fc6a4"
    shutil.copyfile(source, cache_dir / source.name)
    previous = os.environ.get("TIKTOKEN_CACHE_DIR")
    os.environ["TIKTOKEN_CACHE_DIR"] = str(cache_dir)
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("TIKTOKEN_CACHE_DIR", None)
        else:
            os.environ["TIKTOKEN_CACHE_DIR"] = previous


@pytest.fixture(autouse=True)
def _restore_context_processor_registry():
    """Keep context processor overrides isolated to the test that activates them."""
    processor_map = dict(ContextEngine._PROCESSOR_MAP)
    try:
        yield
    finally:
        ContextEngine._PROCESSOR_MAP.clear()
        ContextEngine._PROCESSOR_MAP.update(processor_map)
