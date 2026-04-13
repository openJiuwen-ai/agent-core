"""Python LSP server definition (pyright)."""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

from openjiuwen.harness.lsp.core.types import SpawnHandle
from openjiuwen.harness.lsp.servers.registry import BUILTIN_SERVERS, nearest_root
from openjiuwen.harness.lsp.servers.types import ServerDefinition


def _resolve_pyright_command() -> tuple[str, list[str]] | None:
    """Resolve pyright-langserver to actual executable.

    On Windows, pyright-langserver is a .CMD file that wraps a node.js script.
    We use 'npm list -g --depth=0 pyright' to get the global npm prefix, then
    construct 'node <prefix>/node_modules/pyright/langserver.index.js --stdio'.
    """
    import subprocess

    try:
        npm_path = shutil.which("npm")
        if not npm_path:
            return None
        result = subprocess.run(
            [npm_path, "list", "-g", "--depth=0", "pyright"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            lines = result.stdout.splitlines()
            # Output format:
            #   C:\Users\...\npm
            #   `-- pyright@1.1.408
            # The prefix is the line above the pyright@ line
            prefix = None
            for i, line in enumerate(lines):
                if "pyright@" in line:
                    if i > 0:
                        prefix = lines[i - 1].strip()
                    break

            if prefix:
                js_path = os.path.join(prefix, "node_modules", "pyright", "langserver.index.js")
                if os.path.exists(js_path):
                    node_path = shutil.which("node")
                    if node_path:
                        return (node_path, [js_path, "--stdio"])
    except Exception as exc:
        logger.warning("_resolve_pyright_command: failed to resolve via npm, trying .CMD fallback: %s", exc)

    # Fallback: try to parse the .CMD file
    raw = shutil.which("pyright-langserver")
    if not raw:
        return None

    if not raw.upper().endswith(".CMD"):
        return (raw, ["--stdio"])

    try:
        content = Path(raw).read_text(encoding="utf-8")
    except Exception:
        return None

    script_line = [line for line in content.splitlines() if "node_modules" in line and ".js" in line]
    if not script_line:
        return None

    line = script_line[0].strip()
    parts = line.split('"')
    if len(parts) < 2:
        return None

    js_path = parts[1]
    cmd_dir = str(Path(raw).parent)
    if js_path.startswith("%dp0%\\") or js_path.startswith("%dp0%/"):
        js_path = js_path.replace("%dp0%", cmd_dir)
    elif not Path(js_path).is_absolute():
        js_path = os.path.join(cmd_dir, js_path)

    node_path = shutil.which("node")
    if not node_path:
        return None

    return (node_path, [js_path, "--stdio"])


def _spawn_python(root: str) -> SpawnHandle | None:
    cmd_and_args = _resolve_pyright_command()
    if not cmd_and_args:
        return None

    command, args = cmd_and_args

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
        command=command,
        args=args,
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
