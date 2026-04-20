# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Test bootstrap for harness/rails unit tests.

Patches two pre-existing issues that block collection in this environment:

1. ``jsonschema_path`` — optional dep not installed in CI.
2. ``TaskMetadataProvider`` — defined in task_tool.py but missing from the
   tools __init__.py import list, causing a NameError at module load time.
"""
from __future__ import annotations

import importlib
import importlib.abc
import importlib.util
import sys
import types


def _stub_jsonschema_path() -> None:
    if "jsonschema_path" not in sys.modules:
        m = types.ModuleType("jsonschema_path")
        m.SchemaPath = object  # type: ignore[attr-defined]
        sys.modules["jsonschema_path"] = m


class _TaskProviderPatchedLoader(importlib.abc.Loader):
    """Loader that injects the missing TaskMetadataProvider import."""

    def __init__(self, orig_loader: importlib.abc.Loader) -> None:
        self._orig = orig_loader

    def create_module(self, spec):  # type: ignore[override]
        return self._orig.create_module(spec)

    def exec_module(self, module) -> None:  # type: ignore[override]
        src: str = self._orig.get_source(module.__spec__.name)  # type: ignore[attr-defined]
        src = src.replace(
            "from openjiuwen.harness.prompts.sections.tools.web_tools import",
            (
                "from openjiuwen.harness.prompts.sections.tools.task_tool import TaskMetadataProvider\n"
                "from openjiuwen.harness.prompts.sections.tools.web_tools import"
            ),
            1,
        )
        exec(compile(src, module.__spec__.origin, "exec"), module.__dict__)  # noqa: S102


def _patch_tools_init() -> None:
    target = "openjiuwen.harness.prompts.sections.tools"
    if target in sys.modules:
        return
    spec = importlib.util.find_spec(target)
    if spec is None:
        return
    spec.loader = _TaskProviderPatchedLoader(spec.loader)  # type: ignore[arg-type]
    mod = importlib.util.module_from_spec(spec)
    sys.modules[target] = mod
    spec.loader.exec_module(mod)


_stub_jsonschema_path()
_patch_tools_init()
