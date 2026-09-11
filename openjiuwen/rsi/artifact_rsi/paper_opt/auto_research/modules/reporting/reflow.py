"""Reflow a drafted LaTeX section body to one physical line per paragraph.

Ported (near-verbatim, adapted to operate on an in-memory string rather than
a workdir CLI) from spark-to-paper-skills' ``ts-paper-write/scripts/
reflow_tex.py`` (MIT license, Albus White) — fetched and read directly from
its actual source, not reconstructed from a summary. See
docs/paper_writing_design.md's gap analysis and ts-write/SKILL.md's "one
physical line per paragraph" rule, which this makes a deterministic net for
rather than a prompt-only hope: ts-write already asks the model to write
this way, so this only ever needs to fix a soft-wrapped continuation line,
never restructure prose.

Safe by construction: only ever turns the single ``\\n`` between two
CONTINUATION lines into a space, and never touches blank lines (paragraph
breaks), whitespace/alignment-significant environments (equation/align/
tabular/algorithmic/verbatim/tikzpicture/...), a line that starts a new
logical unit (\\section/\\begin/\\end/\\label/\\includegraphics/\\State.../
\\toprule.../a `%` comment/a line ending in a forced break ``\\\\``), or a
line carrying an active (unescaped) ``%``. Because a single newline is just
a space in LaTeX, the compiled PDF is unchanged — idempotent:
``reflow(reflow(x)) == reflow(x)``.
"""

from __future__ import annotations

import re

_VERBATIM_ENVS = {
    "equation",
    "align",
    "alignat",
    "gather",
    "multline",
    "eqnarray",
    "displaymath",
    "split",
    "array",
    "tabular",
    "tabularx",
    "tabular*",
    "matrix",
    "bmatrix",
    "pmatrix",
    "vmatrix",
    "cases",
    "algorithmic",
    "verbatim",
    "Verbatim",
    "lstlisting",
    "minted",
    "tikzpicture",
}
_BEGIN = re.compile(r"\\begin\{([^}]*)\}")
_END = re.compile(r"\\end\{([^}]*)\}")

_STANDALONE = re.compile(
    r"^(?:%"
    r"|\\(?:sub)*section\*?\b|\\paragraph\b"
    r"|\\label\b|\\includegraphics\b|\\centering\b|\\hline\b"
    r"|\\(?:top|mid|bottom)rule\b|\\cmidrule\b"
    r"|\\(?:State|Statex|For|EndFor|While|EndWhile|If|ElsIf|Else|EndIf|Loop|EndLoop|Repeat|Until"
    r"|Require|Ensure|Function|EndFunction|Procedure|EndProcedure|Return|Comment)\b"
    r"|\\par\b|\\noindent\b|\\medskip\b|\\smallskip\b|\\bigskip\b|\\vspace\b|\\hspace\b"
    r"|\\newpage\b|\\clearpage\b|\\vfill\b|\\hfill\b"
    r"|\\begin\b|\\end\b)"
)
_WRAPPABLE = re.compile(r"^(?:\\item\b|\\bibitem\b|\\caption(?:of|setup)?\b)")
_ACTIVE_PCT = re.compile(r"(?<!\\)%")


def _depth_delta(line: str) -> int:
    delta = 0
    for match in _BEGIN.finditer(line):
        if match.group(1) in _VERBATIM_ENVS:
            delta += 1
    for match in _END.finditer(line):
        if match.group(1) in _VERBATIM_ENVS:
            delta -= 1
    return delta


def reflow(text: str) -> str:
    out: list[str] = []
    buf: list[str] = []
    depth = 0

    def flush() -> None:
        if buf:
            out.append(" ".join(buf))
            buf.clear()

    for raw in text.split("\n"):
        line = raw.rstrip()
        stripped = line.strip()

        if depth > 0:
            flush()
            out.append(line)
            depth = max(0, depth + _depth_delta(line))
            continue

        delta = _depth_delta(line)
        if stripped == "":
            flush()
            out.append("")
            continue
        if delta > 0:
            flush()
            out.append(line)
            depth = max(0, depth + delta)
            continue
        if _ACTIVE_PCT.search(line):
            flush()
            out.append(line)
            depth = max(0, depth + delta)
            continue
        if _STANDALONE.match(stripped):
            flush()
            out.append(line)
            depth = max(0, depth + delta)
            continue
        if _WRAPPABLE.match(stripped):
            flush()
            buf.append(stripped)
            if stripped.endswith("\\\\"):
                flush()
            continue
        buf.append(stripped)
        if stripped.endswith("\\\\"):
            flush()
    flush()

    result = "\n".join(out)
    result = re.sub(r"\n{3,}", "\n\n", result).strip("\n")
    return result + "\n" if result else ""
