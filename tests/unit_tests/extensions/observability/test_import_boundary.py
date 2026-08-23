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
from openjiuwen.extensions.observability.setup import init_observability, shutdown_observability
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

assert semconv.GEN_AI_OPERATION_NAME == "gen_ai.operation.name"
init_observability(ObservabilityConfig(enabled=True, sample_rate=1.0), span_exporter_override=InMemorySpanExporter())
assert not [name for name in sys.modules if name.startswith("openjiuwen.agent_teams")]
shutdown_observability()
"""

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=project_root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_rails_import_without_opentelemetry() -> None:
    """The optional ``observability`` extra must not gate harness rail imports.

    OpenTelemetry ships with the ``observability`` extra only, so importing
    ``openjiuwen.harness.rails`` must survive its absence: span capture
    degrades to inert processors with a warning instead of an ImportError.
    """
    project_root = Path(__file__).resolve().parents[4]
    script = """
import importlib.abc
import sys


class _BlockOpentelemetry(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "opentelemetry" or fullname.startswith("opentelemetry."):
            raise ImportError(f"blocked for test: {fullname}")
        return None


sys.meta_path.insert(0, _BlockOpentelemetry())

import openjiuwen.harness.rails  # noqa: F401
from openjiuwen.agent_evolving.trajectory.processor import TrajectorySpanProcessor
from openjiuwen.extensions.observability.span_context import ActiveSpanTracker

assert not [name for name in sys.modules if name.startswith("opentelemetry")]

processor = TrajectorySpanProcessor()
subscription = processor.subscribe(include_span_categories={"llm"})
assert processor.drain(subscription) == (None, ())
ActiveSpanTracker()
"""

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=project_root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    combined = result.stdout + result.stderr
    assert "trajectory span capture is disabled" in combined
    assert "observability span tracking is disabled" in combined
