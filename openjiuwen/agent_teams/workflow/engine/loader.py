# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Loader: turn a ``.py`` workflow file into a runnable module.

A workflow file is **ordinary, valid Python** in the one SwarmFlow format:

* a top-level ``META = {...}`` *pure-literal* dict (name/description/phases), and
* ``async def run(args): ...`` as the entrypoint, which imports the primitives it
  needs (``from swarmflow import agent, parallel, ...``).

The loader:

1. parses the source,
2. statically extracts ``META`` via :func:`ast.literal_eval` — which *enforces*
   the "pure literal" rule (it rejects names/calls/f-strings/concatenation),
3. requires a top-level ``async def run``,
4. lints for determinism hazards (``time``/``random``/``datetime``/``uuid``) and
   the closure footgun (``lambda: agent(...)`` inside a comprehension),
5. imports the file as a real module (via importlib) so classes get a proper
   ``__module__``/``sys.modules`` entry — pydantic, dataclasses, etc. resolve
   correctly.

The runner then just reads ``META`` and ``await``\\s ``run(args)``.
"""
from __future__ import annotations

import ast
import importlib.util
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import EngineError, MetaError

_BANNED_ATTRS = {"now", "today", "utcnow"}  # datetime.*.now(), etc.
_BANNED_MODULE_CALLS = {
    "time": {"time", "monotonic", "perf_counter", "time_ns", "monotonic_ns"},
    "random": None,  # any attribute on `random`
    "uuid": None,
}
_THUNK_CALLEES = {"agent", "workflow"}


@dataclass
class LoadedWorkflow:
    meta: dict
    path: str
    module: Any  # the imported workflow module (exposes `run`)
    warnings: list[str] = field(default_factory=list)


def extract_workflow_meta(source: str, filename: str = "<swarmflow>") -> dict:
    """Extract a workflow's ``META`` from source text without importing it.

    Same static AST + :func:`ast.literal_eval` extraction as
    :func:`load_workflow_meta`, but reads from an in-memory source string.
    Lets a caller route an inline script to its journal directory (from the
    ``META`` name) before it is materialised to disk.

    Args:
        source: The swarmflow script source.
        filename: Label used in error messages and the AST ``filename``.

    Returns:
        The ``META`` dict literal.

    Raises:
        MetaError: If ``META`` is missing or not a pure dict literal.
    """
    tree = ast.parse(source, filename=filename)
    return _extract_meta(tree, filename)


def load_workflow_meta(path: str) -> dict:
    """Extract a workflow's ``META`` without importing the script.

    Parses the source and statically reads the top-level ``META`` literal
    (AST + :func:`ast.literal_eval` only, no ``importlib`` import). Lets a
    caller learn the workflow's name cheaply before the full
    :func:`load_workflow_source` import runs during execution.

    Args:
        path: Path to the ``.py`` swarmflow script.

    Returns:
        The ``META`` dict literal.

    Raises:
        MetaError: If ``META`` is missing or not a pure dict literal.
    """
    return extract_workflow_meta(Path(path).read_text(encoding="utf-8"), path)


def load_workflow_source(path: str) -> LoadedWorkflow:
    src = Path(path).read_text(encoding="utf-8")
    tree = ast.parse(src, filename=path)
    meta = _extract_meta(tree, path)
    if not any(
        isinstance(node, ast.AsyncFunctionDef) and node.name == "run" for node in tree.body
    ):
        raise EngineError(
            f"{path}: a workflow must define a top-level `async def run(args)` "
            f"(the SwarmFlow entrypoint)."
        )
    warnings = _lint(tree, path)
    return LoadedWorkflow(meta=meta, path=path, module=_import_module(path), warnings=warnings)


def _import_module(path: str):
    """Import a workflow file as a fresh, real module (re-imported each load).

    The script's ``from swarmflow import ...`` resolves because the facade is
    registered in ``sys.modules`` under that name at facade import time; there
    is no on-disk ``swarmflow`` package. Importing the facade here ensures that
    registration has run before the script's imports execute.
    """
    from . import facade  # noqa: F401  # lazy: triggers _register_aliases, avoids a cycle

    name = "wf_flow__" + re.sub(r"\W", "_", str(Path(path).resolve()))
    sys.modules.pop(name, None)  # ensure a fresh import (re-runs top-level)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise MetaError(f"{path}: cannot import as a module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _extract_meta(tree: ast.Module, path: str) -> dict:
    for node in tree.body:  # top level only
        target_name, value = None, None
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(
            node.targets[0], ast.Name
        ):
            target_name, value = node.targets[0].id, node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target_name, value = node.target.id, node.value
        if target_name == "META" and value is not None:
            try:
                literal = ast.literal_eval(value)  # rejects non-literals
            except Exception as e:
                raise MetaError(
                    f"{path}:{getattr(value, 'lineno', '?')}: `META` must be a pure "
                    f"literal (no names/calls/concatenation): {e}"
                ) from e
            if not isinstance(literal, dict):
                raise MetaError(f"{path}: `META` must be a dict literal")
            return literal
    raise MetaError(f"{path}: no top-level `META = {{...}}` found")


def _lint(tree: ast.Module, path: str) -> list[str]:
    warnings: list[str] = []

    # 1) determinism hazards
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            attr = node.func.attr
            val = node.func.value
            base = val.id if isinstance(val, ast.Name) else None
            if base in _BANNED_MODULE_CALLS:
                allowed = _BANNED_MODULE_CALLS[base]
                if allowed is None or attr in allowed:
                    warnings.append(
                        f"{path}:{node.lineno}: `{base}.{attr}(...)` breaks determinism "
                        f"(resume relies on a pure script). Pass values via args/agent results."
                    )
            elif attr in _BANNED_ATTRS:
                warnings.append(
                    f"{path}:{node.lineno}: `*.{attr}(...)` (wall-clock) breaks determinism."
                )

    # 2) closure footgun: bare lambda calling agent/workflow inside a comprehension
    for comp in ast.walk(tree):
        if isinstance(comp, (ast.ListComp, ast.GeneratorExp, ast.SetComp)):
            for inner in ast.walk(comp):
                is_bare_lambda = (
                    isinstance(inner, ast.Lambda)
                    and not inner.args.defaults
                    and not inner.args.kw_defaults
                )
                if is_bare_lambda and _calls_thunk_target(inner.body):
                    warnings.append(
                        f"{path}:{inner.lineno}: `lambda: agent(...)` in a comprehension "
                        f"late-binds the loop var. Use `lambda x=x: agent(...)` or `map_parallel`."
                    )
    return warnings


def _calls_thunk_target(node: ast.AST) -> bool:
    for n in ast.walk(node):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in _THUNK_CALLEES:
            return True
    return False
