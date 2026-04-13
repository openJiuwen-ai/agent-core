"""Python LSP server definition (pyright)."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from openjiuwen.harness.lsp.core.types import SpawnHandle
from openjiuwen.harness.lsp.servers.registry import BUILTIN_SERVERS, nearest_root
from openjiuwen.harness.lsp.servers.types import ServerDefinition


def _spawn_python(root: str) -> SpawnHandle | None:
    cmd = "pyright-langserver"
    if not shutil.which(cmd):
        return None

    initialization_options: dict = {}

    potential_venv_paths = [
        os.environ.get("VIRTUAL_ENV"),
        str(Path(root, ".venv")),
        str(Path(root, "venv")),
    ]
    for venv_path in [p for p in potential_venv_paths if p]:
        is_windows = os.name == "nt"
        python_path = (
            Path(venv_path, "Scripts" if is_windows else "bin", "python").as_posix()
        )
        candidate = (
            python_path.replace("/bin/", "/Scripts/")
            if is_windows
            else python_path
        )
        if Path(candidate).exists():
            initialization_options["pythonPath"] = python_path
            break

    return SpawnHandle(
        command=cmd,
        args=["--stdio"],
        initialization_options=initialization_options or None,
    )


python_server = ServerDefinition(
    id="pyright",
    extensions=[".py", ".pyi"],
    language_id="python",
    priority=10,
    find_root=nearest_root(
        include_patterns=[
            "pyproject.toml",
            "setup.py",
            "setup.cfg",
            "requirements.txt",
            "Pipfile",
            "pyrightconfig.json",
        ],
        exclude_patterns=[".git"],
    ),
    spawn=_spawn_python,
)

BUILTIN_SERVERS[python_server.id] = python_server
