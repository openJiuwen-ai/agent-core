# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Import boundary contracts for shared observability primitives."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_semconv_import_does_not_load_agent_teams() -> None:
    project_root = Path(__file__).resolve().parents[4]
    script = """
import sys
from openjiuwen.extensions.observability import semconv
from openjiuwen.extensions.observability.config import ObservabilityConfig
from openjiuwen.extensions.observability.runtime import ObservabilityRuntime
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

assert semconv.GEN_AI_OPERATION_NAME == "gen_ai.operation.name"
runtime = ObservabilityRuntime()
runtime.initialize(ObservabilityConfig(enabled=True, sample_rate=1.0), span_exporter_override=InMemorySpanExporter())
assert not [name for name in sys.modules if name.startswith("openjiuwen.agent_teams")]
runtime.shutdown()
"""

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=project_root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
