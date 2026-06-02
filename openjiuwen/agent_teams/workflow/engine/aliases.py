# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Runtime mapping of import name(s) onto the SwarmFlow facade.

There is **no** physical ``swarmflow`` package. The name a workflow script
imports (``from swarmflow import ...``, or a custom ``import_as`` name) is mapped
onto :mod:`workflow.engine.facade` in ``sys.modules`` only while it is needed —
during the import of the script module — then removed.

``sys.modules`` is process-global, so when several runs execute concurrently in
one event loop (``asyncio.gather`` over ``run_workflow``) their installs must
*compose* rather than clobber each other's save/restore. Installs are therefore
**reference-counted**: the first installer snapshots the prior state and the last
remover restores it. Under asyncio's single thread these dict updates are atomic
between awaits, so no lock is needed.
"""
from __future__ import annotations

import sys
import types
from contextlib import contextmanager
from typing import Any

_refs: dict[str, int] = {}
_saved: dict[str, tuple[bool, Any]] = {}


def _install(modname: str, module: Any) -> None:
    if _refs.get(modname, 0) == 0:
        _saved[modname] = (modname in sys.modules, sys.modules.get(modname))
        sys.modules[modname] = module
    _refs[modname] = _refs.get(modname, 0) + 1


def _remove(modname: str) -> None:
    _refs[modname] -= 1
    if _refs[modname] == 0:
        existed, prev = _saved.pop(modname)
        del _refs[modname]
        if existed:
            sys.modules[modname] = prev
        else:
            sys.modules.pop(modname, None)


@contextmanager
def facade_aliases(names: list[str]):
    """Map each name in *names* (and any dotted-parent packages) onto the facade.

    Reference-counted, so nesting and concurrency compose safely.
    """
    from . import facade  # lazy: avoids an import cycle at module load

    installed: list[str] = []  # in install order; removed in reverse
    try:
        for name in names:
            parts = name.split(".")
            for i in range(1, len(parts)):  # synthesize dotted parents as packages
                parent = ".".join(parts[:i])
                mod = sys.modules.get(parent) or types.ModuleType(parent)
                _install(parent, mod)
                installed.append(parent)
            _install(name, facade)
            installed.append(name)
            if "." in name:  # `import a.b.c` reads c as an attr of package a.b
                setattr(sys.modules[name.rsplit(".", 1)[0]], parts[-1], facade)
        yield
    finally:
        for modname in reversed(installed):
            _remove(modname)
