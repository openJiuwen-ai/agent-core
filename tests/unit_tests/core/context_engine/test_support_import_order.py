# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Check imports in fresh interpreters, without pytest's warmed module cache."""

import subprocess
import sys

import pytest


@pytest.mark.parametrize("first_import", [
    "from openjiuwen.harness.tools import BashTool",
    "from openjiuwen.core.single_agent import AgentCard, ReActAgent",
    "from openjiuwen.core.context_engine import ContextEngine",
    "from openjiuwen.core.context_engine.processor.forked.compressor.support import ForkedAgent",
    "from openjiuwen.core.context_engine.processor.forked.compressor.support.forked_agent import ForkedAgent",
])
def test_compressor_support_import_order(first_import):
    script = first_import + """
from openjiuwen.harness.tools import BashTool
from openjiuwen.core.single_agent import AgentCard, ReActAgent
from openjiuwen.core.context_engine.processor.forked.compressor import support
from openjiuwen.core.context_engine.processor.forked.compressor.support import (
    CompressionExecutor, CompressionResult, ForkedAgent,
)
from openjiuwen.core.context_engine.processor.forked.compressor.support.forked_agent import (
    ForkedAgent as DirectForkedAgent,
)
assert ForkedAgent is DirectForkedAgent is support.ForkedAgent
assert issubclass(ForkedAgent, CompressionExecutor)
assert set(support.__all__) == {"CompressionExecutor", "CompressionResult", "ForkedAgent"}
namespace = {}
exec("from openjiuwen.core.context_engine.processor.forked.compressor.support import *", namespace)
assert namespace["ForkedAgent"] is ForkedAgent
try:
    support.nonexistent_export
except AttributeError:
    pass
else:
    raise AssertionError("unknown exports must raise AttributeError")
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
