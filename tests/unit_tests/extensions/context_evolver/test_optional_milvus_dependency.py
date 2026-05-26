# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Tests for running context_evolver without optional Milvus dependencies."""

import os
import subprocess
import sys
from pathlib import Path


def test_context_evolver_import_and_auto_persistence_work_without_pymilvus() -> None:
    """JSON-only runtimes should not fail at import time when pymilvus is absent."""
    repo_root = Path(__file__).resolve().parents[4]
    code = """
import builtins

real_import = builtins.__import__

def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
    if name == "pymilvus" or name.startswith("pymilvus."):
        raise ModuleNotFoundError("No module named 'pymilvus'")
    return real_import(name, globals, locals, fromlist, level)

builtins.__import__ = fake_import

from openjiuwen.extensions.context_evolver.core.persistence import MemoryPersistenceHelper

helper = MemoryPersistenceHelper(persist_type="auto")
assert helper._resolve_backend() == "json"
"""
    env = os.environ.copy()
    pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(repo_root) if not pythonpath else f"{repo_root}{os.pathsep}{pythonpath}"

    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        env=env,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
