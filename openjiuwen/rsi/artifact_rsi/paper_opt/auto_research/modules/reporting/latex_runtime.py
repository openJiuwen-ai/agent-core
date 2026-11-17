"""Host-side discovery and readiness checks for a LaTeX installation.

The reporting pipeline does not install TeX packages or distributions at
runtime.  It discovers a host installation instead, which keeps the normal
paper run deterministic and lets the product installer provision MiKTeX (or
MacTeX) once for the user.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

_ENGINE_NAMES = ("latexmk", "pdflatex")


class LatexRuntimeError(RuntimeError):
    """Raised when the host has no usable LaTeX compiler."""


@dataclass(frozen=True)
class LatexRuntime:
    """Resolved LaTeX executables and the directories searched for them."""

    latexmk: Path | None
    pdflatex: Path | None
    search_dirs: tuple[Path, ...]

    @property
    def available(self) -> bool:
        return self.latexmk is not None or self.pdflatex is not None

    @property
    def bin_dir(self) -> Path | None:
        for executable in (self.latexmk, self.pdflatex):
            if executable is not None:
                return executable.parent
        return None

    def with_environment(self, environ: Mapping[str, str] | None = None) -> dict[str, str]:
        """Return a child environment with the resolved tool directories first.

        A copy is always returned. In particular, the default call must not
        mutate ``os.environ`` because reporting tasks may run concurrently in
        the same long-lived worker process.
        """

        target = dict(os.environ if environ is None else environ)
        current_path = target.get("PATH", "")
        prefixes = [str(path) for path in self.search_dirs if path.is_dir()]
        if self.bin_dir is not None and self.bin_dir.is_dir():
            prefixes.insert(0, str(self.bin_dir))

        entries: list[str] = []
        seen: set[str] = set()
        for entry in [*prefixes, *current_path.split(os.pathsep)]:
            if not entry:
                continue
            key = os.path.normcase(os.path.abspath(entry))
            if key in seen:
                continue
            seen.add(key)
            entries.append(entry)
        if entries:
            target["PATH"] = os.pathsep.join(entries)
        if self.bin_dir is not None:
            # Keep the actual resolved directory in sync with PATH.  This is
            # consumed by ts-latex/scripts/compile.py and is more useful than
            # preserving a stale user-provided value.
            target["LATEX_BIN_DIR"] = str(self.bin_dir)
        return target

    def summary(self) -> str:
        return (
            f"latexmk={self.latexmk or 'missing'}, "
            f"pdflatex={self.pdflatex or 'missing'}, "
            f"bin_dir={self.bin_dir or 'unknown'}"
        )


def _as_directory(value: str | os.PathLike[str] | None) -> Path | None:
    if value is None:
        return None
    text = os.path.expandvars(os.fspath(value)).strip()
    if not text:
        return None
    candidate = Path(text).expanduser()
    return candidate.parent if candidate.is_file() else candidate


def _default_bin_dirs(environ: Mapping[str, str]) -> tuple[Path, ...]:
    home = Path(environ.get("USERPROFILE") or environ.get("HOME") or str(Path.home())).expanduser()
    if os.name == "nt":
        candidates: list[Path] = [home / "bin"]
        local_app_data = environ.get("LOCALAPPDATA")
        if local_app_data:
            candidates.append(Path(local_app_data) / "Programs/MiKTeX/miktex/bin/x64")
        for key in ("ProgramW6432", "ProgramFiles"):
            program_files = environ.get(key)
            if program_files:
                candidates.append(Path(program_files) / "MiKTeX/miktex/bin/x64")
        return tuple(candidates)

    if sys.platform == "darwin":
        return (
            home / "bin",  # MiKTeX private install
            Path("/Library/TeX/texbin"),  # MacTeX
            Path("/opt/homebrew/bin"),
            Path("/usr/local/bin"),
        )

    return (home / "bin", Path("/usr/local/bin"), Path("/usr/bin"))


def _unique_paths(paths: list[Path]) -> tuple[Path, ...]:
    result: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        normalized = os.path.normcase(os.path.abspath(str(path)))
        if normalized in seen:
            continue
        seen.add(normalized)
        result.append(path)
    return tuple(result)


def discover_latex_runtime(
    latex_bin_dir: str | os.PathLike[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> LatexRuntime:
    """Find ``latexmk`` and/or ``pdflatex`` using explicit and host paths.

    ``LATEX_BIN_DIR`` is the primary escape hatch for a custom installation;
    ``MIKTEX_BIN_DIR`` is accepted as an equivalent deployment-friendly name.
    """

    env = os.environ if environ is None else environ
    explicit = latex_bin_dir or env.get("LATEX_BIN_DIR") or env.get("MIKTEX_BIN_DIR")
    search_dirs: list[Path] = []
    explicit_dir = _as_directory(explicit)
    if explicit_dir is not None:
        search_dirs.append(explicit_dir)
    search_dirs.extend(Path(entry) for entry in env.get("PATH", "").split(os.pathsep) if entry)
    search_dirs.extend(_default_bin_dirs(env))
    resolved_dirs = _unique_paths(search_dirs)
    search_path = os.pathsep.join(str(path) for path in resolved_dirs)

    found: dict[str, Path | None] = {}
    for name in _ENGINE_NAMES:
        located = shutil.which(name, path=search_path or None)
        found[name] = Path(located) if located else None

    found_dirs = [path.parent for path in found.values() if path is not None]
    return LatexRuntime(
        latexmk=found["latexmk"],
        pdflatex=found["pdflatex"],
        search_dirs=_unique_paths([*resolved_dirs, *found_dirs]),
    )


def preflight_latex_runtime(
    latex_bin_dir: str | os.PathLike[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    timeout_seconds: float = 10.0,
) -> LatexRuntime:
    """Resolve and execute one compiler before starting an LLM report run."""

    runtime = discover_latex_runtime(latex_bin_dir, environ=environ)
    if not runtime.available:
        raise LatexRuntimeError(
            "No LaTeX compiler was found. Install MiKTeX or MacTeX, ensure "
            "latexmk or pdflatex is on PATH, or set LATEX_BIN_DIR."
        )

    child_env = runtime.with_environment(dict(os.environ if environ is None else environ))
    errors: list[str] = []
    for name, executable in (("latexmk", runtime.latexmk), ("pdflatex", runtime.pdflatex)):
        if executable is None:
            continue
        try:
            completed = subprocess.run(
                [str(executable), "--version"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=max(0.1, float(timeout_seconds)),
                check=False,
                env=child_env,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            errors.append(f"{name}: {exc}")
            continue
        if completed.returncode == 0:
            return runtime
        errors.append(f"{name}: exited with status {completed.returncode}")

    detail = "; ".join(errors) if errors else "the compiler did not start"
    raise LatexRuntimeError(f"LaTeX compiler preflight failed ({runtime.summary()}): {detail}")


def configure_latex_environment(
    runtime: LatexRuntime | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> LatexRuntime | None:
    """Resolve a runtime without changing the current process environment.

    Call ``runtime.with_environment()`` at the subprocess boundary when the
    returned environment is needed.
    """

    resolved = runtime or discover_latex_runtime(environ=environ)
    return resolved


__all__ = [
    "LatexRuntime",
    "LatexRuntimeError",
    "configure_latex_environment",
    "discover_latex_runtime",
    "preflight_latex_runtime",
]
