# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Loader pre-binds the script-facing primitives into every workflow module.

LLM-authored scripts sometimes call ``phase()`` / ``agent()`` without
``from swarmflow import ...``; such a run used to die instantly with
``NameError: name 'phase' is not defined``.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from openjiuwen.agent_teams.workflow.engine import (
    MockBackend,
    load_workflow_source,
    run_workflow,
)
from openjiuwen.agent_teams.workflow.engine import facade
from tests.test_logger import logger as test_logger

_NO_IMPORT_SCRIPT = '''
META = {
    "name": "minimal-two-phase",
    "description": "two phases without importing the primitives",
    "phases": [{"title": "Generate"}, {"title": "Review"}],
}

async def run(args):
    phase("Generate")
    draft = await agent(f"draft {args}", label="drafter")
    phase("Review")
    final = await agent(f"review {draft}", label="reviewer")
    return {"draft": draft, "final": final}
'''

_SHADOW_SCRIPT = '''
META = {"name": "shadow", "description": "script-level name wins"}

def log(message):
    return f"own:{message}"

async def run(args):
    return log("x")
'''


def _write(tmp_path: Path, name: str, src: str) -> str:
    path = tmp_path / name
    path.write_text(src, encoding="utf-8")
    return str(path)


def test_script_without_import_runs_to_completion(tmp_path: Path):
    """A script that skips ``from swarmflow import`` still runs both phases."""
    script = _write(tmp_path, "no_import.py", _NO_IMPORT_SCRIPT)

    result = asyncio.run(run_workflow(script, backend=MockBackend(), args="topic"))

    test_logger.info("no-import workflow result: %s", result)
    assert isinstance(result, dict)
    assert isinstance(result["draft"], str)
    assert isinstance(result["final"], str)


def test_every_script_primitive_is_prebound(tmp_path: Path):
    """Each contract primitive is bound to the facade object in the module namespace."""
    script = _write(tmp_path, "no_import.py", _NO_IMPORT_SCRIPT)

    module = load_workflow_source(script).module

    for primitive in facade.SCRIPT_PRIMITIVES:
        assert getattr(module, primitive) is getattr(facade, primitive), primitive


def test_script_level_definition_shadows_prebound_primitive(tmp_path: Path):
    """A name the script defines itself overrides the pre-bound primitive."""
    script = _write(tmp_path, "shadow.py", _SHADOW_SCRIPT)

    result = asyncio.run(run_workflow(script, backend=MockBackend()))

    assert result == "own:x"
