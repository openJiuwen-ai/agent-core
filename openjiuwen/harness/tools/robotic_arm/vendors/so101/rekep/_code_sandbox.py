# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""AST whitelist validation + restricted ``exec()`` for VLM-generated constraint code.

``constraint_generation.py`` asks a VLM to write Python "constraint functions"
(ReKep-style: ``fn(end_effector, keypoints) -> float``) from an image + task
description, then must run the returned source to obtain callable objects --
an inherent untrusted-code-execution surface (nothing stops a compromised or
confused VLM from echoing back something other than a pure numeric function).

This module fails closed the same way
``openjiuwen/harness/security/shell_ast.py`` does for shell commands: only a
narrow whitelist of statement/expression shapes is allowed, and even a
validation gap can't reach ``open``/``os``/``sys`` because ``exec()`` runs
with a stripped-down ``__builtins__``.
"""

from __future__ import annotations

import ast
import builtins
from typing import Any

_ALLOWED_TOP_LEVEL_NODES = (ast.FunctionDef, ast.Assign, ast.Expr, ast.Pass)
_ALLOWED_FUNCTION_BODY_NODES = (ast.Assign, ast.AugAssign, ast.Return, ast.Expr, ast.Pass)
_DISALLOWED_NODE_TYPES = (
    ast.Import,
    ast.ImportFrom,
    ast.ClassDef,
    ast.With,
    ast.Try,
    ast.Global,
    ast.Nonlocal,
    ast.Lambda,
)
_DISALLOWED_NAMES = frozenset({"exec", "eval", "open", "compile", "__import__", "globals", "locals", "vars"})
_ALLOWED_CALL_NAMES = frozenset(
    {
        "abs",
        "float",
        "int",
        "len",
        "max",
        "min",
        "sum",
        "round",
        "np",
        "numpy",
        "get_grasping_cost_by_keypoint_idx",
        "check_reachability",
    }
)
_ALLOWED_ATTRIBUTE_ROOTS = frozenset({"np", "numpy"})
_SAFE_BUILTIN_NAMES = ("abs", "float", "int", "len", "max", "min", "sum", "round", "True", "False", "None")


def _reject(reason: str) -> None:
    raise ValueError(f"rejected VLM-generated constraint code: {reason}")


def _check_call(node: ast.Call) -> None:
    func = node.func
    if isinstance(func, ast.Name):
        if func.id not in _ALLOWED_CALL_NAMES:
            _reject(f"call to non-whitelisted function '{func.id}'")
        return
    if isinstance(func, ast.Attribute):
        root: ast.expr = func
        while isinstance(root, ast.Attribute):
            root = root.value
        if isinstance(root, ast.Name) and root.id in _ALLOWED_ATTRIBUTE_ROOTS:
            return
        _reject("call via attribute access on a non-whitelisted object")
        return
    _reject("call to a dynamically computed function")


def _check_subtree(node: ast.AST) -> None:
    for child in ast.walk(node):
        if isinstance(child, ast.Attribute) and child.attr.startswith("_"):
            _reject(f"access to dunder/private attribute '{child.attr}'")
        elif isinstance(child, ast.Call):
            _check_call(child)
        elif isinstance(child, _DISALLOWED_NODE_TYPES):
            _reject(f"disallowed syntax: {type(child).__name__}")
        elif isinstance(child, ast.Name) and child.id in _DISALLOWED_NAMES:
            _reject(f"reference to disallowed name '{child.id}'")


def validate_constraint_code(code: str) -> None:
    """Parse ``code`` and reject anything outside the constraint-function whitelist.

    Allowed: top-level function definitions and simple assignments (for
    ``num_stages``/``STAGE_*``/``grasp_keypoints``/etc.); function bodies
    limited to assignment/return/expression statements (no loops, no
    conditionals, no exception handling); calls limited to plain numeric
    builtins, ``np``/``numpy`` and the two helper functions injected by
    ``constraint_generation.py``; no dunder/private attribute access.
    """
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as e:
        _reject(f"code does not parse: {e}")
        return

    for node in tree.body:
        if not isinstance(node, _ALLOWED_TOP_LEVEL_NODES):
            _reject(f"disallowed top-level statement: {type(node).__name__}")
        if isinstance(node, ast.FunctionDef):
            for stmt in node.body:
                if not isinstance(stmt, _ALLOWED_FUNCTION_BODY_NODES):
                    _reject(f"disallowed statement in function body: {type(stmt).__name__}")
        _check_subtree(node)


def safe_exec_constraint_code(code: str, extra_globals: dict[str, Any]) -> dict[str, Any]:
    """Validate then ``exec`` ``code`` with a locked-down ``__builtins__``.

    Even if ``validate_constraint_code`` misses something, ``open``/``os``/
    ``sys``/``__import__`` etc. are simply absent from the resulting globals.
    """
    validate_constraint_code(code)
    safe_builtins = {name: getattr(builtins, name) for name in _SAFE_BUILTIN_NAMES}
    namespace: dict[str, Any] = {"__builtins__": safe_builtins, **extra_globals}
    exec(compile(code, "<rekep_constraint>", "exec"), namespace)  # noqa: S102 -- validated + sandboxed above
    return namespace


__all__ = ["safe_exec_constraint_code", "validate_constraint_code"]
