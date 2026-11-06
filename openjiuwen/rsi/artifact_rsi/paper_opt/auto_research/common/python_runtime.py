"""Host-side discovery of a real Python interpreter for the coding agent's
shell tool.

This one exists because of an observed failure: on Windows, a bare ``python``/``py``
typed by the coding agent in its bash tool can resolve to the OS's own
"App Execution Alias" placeholder under
``%LOCALAPPDATA%\\Microsoft\\WindowsApps\\`` instead of a real interpreter --
running it just prints a "Python was not found; run without arguments to
install from the Microsoft Store..." message. Observed directly across
multiple runs: the coding agent then burns its own retry budget re-locating
a real interpreter from scratch (walking AppData/Program Files) instead of
ever writing the required deliverable.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

_CANDIDATE_NAMES = ("python", "python3") if sys.platform != "win32" else ("python.exe", "python3.exe")
_VERSION_RE = re.compile(r"Python 3\.\d")
_WINDOWS_APPS_MARKER = os.path.normcase(os.path.join("Microsoft", "WindowsApps"))


@dataclass(frozen=True)
class PythonRuntime:
    """Resolved Python executable and the directories searched for it."""

    python: Path | None
    search_dirs: tuple[Path, ...]

    @property
    def available(self) -> bool:
        return self.python is not None

    @property
    def bin_dir(self) -> Path | None:
        return self.python.parent if self.python is not None else None

    def with_environment(self, environ: Mapping[str, str] | None = None) -> dict[str, str]:
        """Return a child environment with the resolved directory first on PATH.

        A copy is always returned; the default call must not mutate
        ``os.environ`` because multiple tasks can run concurrently in the
        same long-lived worker process.
        """
        target = dict(os.environ if environ is None else environ)
        if self.bin_dir is None:
            return target
        current_path = target.get("PATH", "")
        entries: list[str] = []
        seen: set[str] = set()
        for entry in [str(self.bin_dir), *current_path.split(os.pathsep)]:
            if not entry:
                continue
            key = os.path.normcase(os.path.abspath(entry))
            if key in seen:
                continue
            seen.add(key)
            entries.append(entry)
        target["PATH"] = os.pathsep.join(entries)
        target["PYTHON_BIN_DIR"] = str(self.bin_dir)
        target["PYTHON_EXE"] = str(self.python)
        return target

    def summary(self) -> str:
        return f"python={self.python or 'missing'}"


def _is_windows_apps_alias(path: Path) -> bool:
    """True for the Windows App Execution Alias placeholder directory.

    A bare ``python``/``python3`` under here is a real file (so plain
    existence checks pass) but running it just prints a Microsoft Store
    prompt -- it must never be treated as a usable interpreter regardless
    of what PATH says.
    """
    return _WINDOWS_APPS_MARKER in os.path.normcase(str(path))


def _default_bin_dirs(environ: Mapping[str, str]) -> tuple[Path, ...]:
    if sys.platform != "win32":
        home = Path(environ.get("HOME") or str(Path.home())).expanduser()
        return (home / ".local/bin", Path("/usr/local/bin"), Path("/usr/bin"))

    candidates: list[Path] = []
    local_app_data = environ.get("LOCALAPPDATA")
    if local_app_data:
        candidates.extend(sorted(Path(local_app_data, "Programs/Python").glob("Python3*"), reverse=True))
    for key in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)"):
        program_files = environ.get(key)
        if program_files:
            candidates.extend(sorted(Path(program_files).glob("Python3*"), reverse=True))
    return tuple(candidates)


def _unique_paths(paths: list[Path]) -> tuple[Path, ...]:
    result: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        if _is_windows_apps_alias(path):
            continue
        normalized = os.path.normcase(os.path.abspath(str(path)))
        if normalized in seen:
            continue
        seen.add(normalized)
        result.append(path)
    return tuple(result)


def _verify_interpreter(candidate: Path, *, timeout_seconds: float) -> bool:
    """Functional check: does this candidate actually behave like Python 3?

    Existence alone cannot distinguish a real interpreter from the Windows
    App Execution Alias placeholder -- that placeholder is a genuine file
    too. Running --version is the only reliable signal: a real interpreter
    prints "Python 3.x.y"; the placeholder prints a Microsoft Store prompt.
    """
    try:
        completed = subprocess.run(
            [str(candidate), "--version"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=max(0.1, float(timeout_seconds)),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    combined = f"{completed.stdout}\n{completed.stderr}"
    return bool(_VERSION_RE.search(combined))


def discover_python_runtime(
    python_bin_dir: str | os.PathLike[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    verify_timeout_seconds: float = 10.0,
) -> PythonRuntime:
    """Find a real Python 3 interpreter, explicitly excluding and verifying
    against the Windows App Execution Alias placeholder.

    ``PYTHON_BIN_DIR`` is the primary escape hatch for a custom installation.
    Search order: explicit override, current PATH (minus WindowsApps), then
    well-known per-platform install directories. The first candidate that
    passes the functional --version check wins.
    """
    env = os.environ if environ is None else environ
    explicit = python_bin_dir or env.get("PYTHON_BIN_DIR")
    search_dirs: list[Path] = []
    if explicit:
        text = os.path.expandvars(os.fspath(explicit)).strip()
        if text:
            candidate_dir = Path(text).expanduser()
            search_dirs.append(candidate_dir.parent if candidate_dir.is_file() else candidate_dir)
    search_dirs.extend(Path(entry) for entry in env.get("PATH", "").split(os.pathsep) if entry)
    search_dirs.extend(_default_bin_dirs(env))
    resolved_dirs = _unique_paths(search_dirs)
    search_path = os.pathsep.join(str(path) for path in resolved_dirs)

    for name in _CANDIDATE_NAMES:
        located = shutil.which(name, path=search_path or None)
        if not located:
            continue
        candidate = Path(located)
        if _is_windows_apps_alias(candidate):
            continue
        if _verify_interpreter(candidate, timeout_seconds=verify_timeout_seconds):
            return PythonRuntime(python=candidate, search_dirs=resolved_dirs)

    return PythonRuntime(python=None, search_dirs=resolved_dirs)


def ensure_on_path(runtime: PythonRuntime) -> None:
    """Idempotently prepend the resolved interpreter's directory to this
    process's PATH, and set PYTHON_EXE / PYTHON_BIN_DIR.

    Deliberately mutates ``os.environ`` (unlike ``with_environment()``,
    which returns a copy). Unlike LATEX_BIN_DIR -- which can legitimately
    differ per task/config -- there is only one correct system Python per
    host, so sharing this across tasks running concurrently in the same
    worker process is safe: it only ever adds a directory that has already
    been functionally verified, never removes or overrides anything.
    """
    if not runtime.available or runtime.bin_dir is None:
        return
    bin_dir = str(runtime.bin_dir)
    existing = os.environ.get("PATH", "")
    normalized_existing = {os.path.normcase(os.path.abspath(p)) for p in existing.split(os.pathsep) if p}
    if os.path.normcase(os.path.abspath(bin_dir)) not in normalized_existing:
        os.environ["PATH"] = os.pathsep.join([bin_dir, existing]) if existing else bin_dir
    os.environ.setdefault("PYTHON_EXE", str(runtime.python))
    os.environ.setdefault("PYTHON_BIN_DIR", bin_dir)


__all__ = [
    "PythonRuntime",
    "discover_python_runtime",
    "ensure_on_path",
]
