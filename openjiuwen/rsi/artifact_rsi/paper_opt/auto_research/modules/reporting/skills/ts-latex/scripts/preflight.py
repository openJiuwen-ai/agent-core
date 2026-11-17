#!/usr/bin/env python
"""Check that the host LaTeX/MiKTeX installation is usable.

This script is intended for the macOS/Windows installation or first-run
workflow after MiKTeX has been provisioned. It only checks the installation;
it never downloads or installs a TeX distribution.

Usage: python preflight.py [latex-bin-dir]
"""

from __future__ import annotations

import contextlib
import io
import json
import logging
import sys


def _write_json(payload: dict[str, object]) -> None:
    """Keep the CLI stdout contract machine-readable through logging."""

    logging.info(json.dumps(payload))


def main() -> int:
    # Importing the host package registers optional parsers and can emit
    # informational lines. Keep this CLI's stdout a machine-readable JSON
    # contract for installers and first-run checks.
    with contextlib.redirect_stdout(io.StringIO()):
        from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.reporting.latex_runtime import (
            LatexRuntimeError,
            preflight_latex_runtime,
        )

    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        stream=sys.stdout,
        force=True,
    )

    latex_bin_dir = sys.argv[1] if len(sys.argv) > 1 else None
    try:
        runtime = preflight_latex_runtime(latex_bin_dir)
    except LatexRuntimeError as exc:
        _write_json({"ready": False, "error": str(exc)})
        return 1

    _write_json(
        {
            "ready": True,
            "latexmk": str(runtime.latexmk) if runtime.latexmk else None,
            "pdflatex": str(runtime.pdflatex) if runtime.pdflatex else None,
            "bin_dir": str(runtime.bin_dir) if runtime.bin_dir else None,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
