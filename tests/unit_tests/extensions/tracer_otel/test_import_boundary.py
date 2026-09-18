# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Import-boundary tests for the optional tracer_otel extension."""

import subprocess
import sys
from pathlib import Path


def test_package_import_does_not_require_opentelemetry_sdk():
    project_root = Path(__file__).parents[4]
    script = """
import importlib.abc
import sys


class BlockOpenTelemetrySdk(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname == "opentelemetry.sdk" or fullname.startswith("opentelemetry.sdk."):
            raise ModuleNotFoundError("blocked optional dependency")
        return None


sys.meta_path.insert(0, BlockOpenTelemetrySdk())
import openjiuwen.extensions.tracer_otel
"""

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
