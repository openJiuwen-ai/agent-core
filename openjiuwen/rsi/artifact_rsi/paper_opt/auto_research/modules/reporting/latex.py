"""LaTeX escaping, assembly, and compilation.

See docs/paper_writing_design.md §5 (escaping) and §8 (compile-and-fix
loop). The host owns compilation directly via ``subprocess`` — there is no
``bash`` tool grant to the model here (see the design doc's "Why not a
DeepAgent"), the same bounded, host-controlled pattern
``code_implementation``'s smoke test already uses for
``python run.py --smoke-test``.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.reporting.sections import section_by_id

_LATEX_SPECIAL_CHARS = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}
_LATEX_SPECIAL_RE = re.compile("|".join(re.escape(ch) for ch in _LATEX_SPECIAL_CHARS))


def escape_latex(text: str) -> str:
    """Escape LaTeX special characters in plain text (variant/metric names,
    titles, etc.) — see docs/paper_writing_design.md §5. Not meant for text
    that already contains LaTeX markup (section bodies the model writes).
    """
    return _LATEX_SPECIAL_RE.sub(lambda m: _LATEX_SPECIAL_CHARS[m.group(0)], text)


# The real, official NeurIPS 2025 style file (assets/neurips_2025.sty,
# fetched verbatim from media.neurips.cc — see docs/paper_writing_design.md's
# gap analysis vs. our old hand-rolled article preamble), used with the
# `preprint` option (non-anonymous, no line numbers — appropriate for an
# internal report, not a blind venue submission per this module's own
# "Audience" decision). It auto-loads natbib and times, so this preamble
# does not load either itself. Package list otherwise mirrors
# spark-to-paper-skills' own neurips_official/main.tex.tmpl.
_PREAMBLE = r"""\documentclass{article}
\usepackage[preprint]{neurips_2025}
\usepackage[utf8]{inputenc}
\usepackage[T1]{fontenc}
\usepackage{hyperref}
\hypersetup{colorlinks=true, linkcolor=black, citecolor=black, urlcolor=blue}
\usepackage{url}
\usepackage{booktabs}
\usepackage{amsfonts}
\usepackage{amsmath}
\usepackage{amssymb}
\usepackage{nicefrac}
\usepackage{microtype}
\usepackage{graphicx}
\usepackage{adjustbox}
\usepackage{algorithm}
\usepackage{algpseudocode}
\begin{document}
"""

_CLOSING = r"""
\bibliographystyle{plainnat}
\bibliography{refs}
\end{document}
"""


_UNSAFE_PLACEHOLDER = "[non-ASCII text omitted]"
# Common smart-typography punctuation that pdflatex's default utf8 inputenc
# renders fine even without extra font packages; everything else outside
# ASCII (CJK, Cyrillic, emoji, ...) has no glyph in the default Latin fonts
# and hard-fails the compile (see docs/paper_writing_design.md §8 — a real
# run choked on a raw Chinese date quoted from task data).
_SAFE_EXTRA_CHARS = set("–—‘’“”…")


def sanitize_for_pdflatex(text: str) -> str:
    """Replace runs of characters pdflatex can't render with a placeholder.
    Safe to run over an already-assembled document: all LaTeX syntax
    (``\\``, ``{``, ``}``, ``%``, ``$``, ...) is ASCII, so this only ever
    touches literal prose/quoted content, never commands.
    """
    out: list[str] = []
    run = False
    for ch in text:
        if ord(ch) < 0x7F or ch in _SAFE_EXTRA_CHARS:
            if run:
                out.append(_UNSAFE_PLACEHOLDER)
                run = False
            out.append(ch)
        else:
            run = True
    if run:
        out.append(_UNSAFE_PLACEHOLDER)
    return "".join(out)


_ABSTRACT_SECTION_ID = "abstract"


# -- deterministic post-processes ported from spark-to-paper-skills'
# ts-paper-latex/scripts/assemble_paper.py (MIT license, Albus White) —
# fetched and read directly from its actual source, not reconstructed from
# a summary. All four are pure string transforms with no LLM involvement,
# same "host owns formatting, model owns content" split this module already
# uses elsewhere (escape_latex, render_results_table). ----------------------


def _brace_end(s: str, open_idx: int) -> int:
    depth = 0
    for i in range(open_idx, len(s)):
        if s[i] == "{":
            depth += 1
        elif s[i] == "}":
            depth -= 1
            if depth == 0:
                return i + 1
    return -1


def merge_adjacent_cites(latex: str) -> str:
    r"""``\cite{a} \cite{b}`` -> ``\cite{a,b}`` so natbib's ``sort&compress``
    (or, for author-year styles, its own adjacency handling) renders them as
    one group instead of two separate citations back to back."""
    cmd = r"\\cite[tp]?\*?"
    pair = re.compile(r"(" + cmd + r")\{([^}]*)\}([~ \t]*)(" + cmd + r")\{([^}]*)\}")

    def merge_keys(a: str, b: str) -> str:
        keys: list[str] = []
        for grp in (a, b):
            for k in grp.split(","):
                k = k.strip()
                if k and k not in keys:
                    keys.append(k)
        return ",".join(keys)

    def sub(m: re.Match[str]) -> str:
        if m.group(1) != m.group(4):
            return m.group(0)
        return m.group(1) + "{" + merge_keys(m.group(2), m.group(5)) + "}"

    out = latex
    for _ in range(20):
        new = pair.sub(sub, out)
        if new == out:
            break
        out = new
    return out


def move_table_captions_above(latex: str) -> str:
    """Place table captions ABOVE the tabular (NeurIPS/most venue
    convention — figure captions stay below, this only touches ``table``/
    ``table*`` floats and inline ``minipage`` tables).
    """

    def fix_float(m: re.Match[str]) -> str:
        begin, ttype, content = m.group(1), m.group(2), m.group(3)
        cm = re.search(r"\\caption\{", content)
        if not cm:
            return m.group(0)
        cb = content.find("{", cm.start())
        ce = _brace_end(content, cb)
        if ce == -1:
            return m.group(0)
        cap = content[cm.start(): ce]
        lab = re.search(r"\\label\{[^}]*\}", content)
        lab_t = lab.group(0) if lab else ""
        body = content[: cm.start()] + content[ce:]
        if lab_t:
            body = body.replace(lab_t, "", 1)
        body = re.sub(r"\n\s*\n\s*\n+", "\n\n", body).strip()
        parts = [cap] + ([lab_t] if lab_t else []) + [body]
        new = "\n".join(parts)
        return m.group(0) if new.strip() == content.strip() else f"{begin}\n{new}\n\\end{{{ttype}}}"

    latex = re.sub(
        r"(\\begin\{(table\*?)\}(?:\[[^\]]*\])?)(.*?)\\end\{\2\}", fix_float, latex, flags=re.DOTALL
    )

    def fix_inline(m: re.Match[str]) -> str:
        block = m.group(0)
        if r"\captionof{table}" not in block:
            return block
        cs = block.find(r"\captionsetup{type=table}")
        co = block.find(r"\captionof{table}")
        cands = [x for x in (cs, co) if x != -1]
        if not cands:
            return block
        start = min(cands)
        brace = block.find("{", co + len(r"\captionof{table}"))
        end = _brace_end(block, brace)
        if end == -1:
            return block
        labm = re.match(r"[ \t]*\n?[ \t]*\\label\{[^}]*\}", block[end:])
        if labm:
            end += labm.end()
        ci = block.find(r"\centering")
        if ci == -1:
            return block
        insert_at = ci + len(r"\centering")
        if block[insert_at:start].strip() == "":
            return block
        cap = block[start:end].strip()
        nb = block[:start] + block[end:]
        ci2 = nb.find(r"\centering") + len(r"\centering")
        nb = nb[:ci2] + "\n" + cap + nb[ci2:]
        return re.sub(r"\n\s*\n\s*\n+", "\n\n", nb)

    latex = re.sub(
        r"\\begin\{minipage\}\{\\columnwidth\}.*?\\end\{minipage\}", fix_inline, latex, flags=re.DOTALL
    )
    return latex


def strip_heading_numbers(s: str) -> str:
    r"""``\subsection{3.1 Foo}`` -> ``\subsection{Foo}`` — LaTeX numbers
    headings itself; a model-written number in the heading text double-numbers it."""
    return re.sub(r"(\\(?:sub)*section\*?\{)\s*\d+(?:\.\d+)*\.?\s+", r"\1", s)


def ensure_table_width(s: str) -> str:
    r"""Safety net: wrap a bare ``\begin{tabular}`` inside a float ``table``/
    ``table*`` in ``\adjustbox{max width=...}`` (idempotent) so a
    wide-metric-count results table can never overflow the page margin."""

    def wrap(m: re.Match[str]) -> str:
        env, ttype = m.group(0), m.group(1)
        if r"\adjustbox" in env:
            return env
        tab = re.search(r"\\begin\{tabular\}.*?\\end\{tabular\}", env, re.S)
        if not tab:
            return env
        width = r"\textwidth" if ttype == "table*" else r"\columnwidth"
        tabular_block = tab.group(0)
        wrapped = f"\\adjustbox{{max width={width}}}{{%\n{tabular_block}}}"
        return env.replace(tabular_block, wrapped, 1)

    return re.sub(r"\\begin\{(table\*?)\}.*?\\end\{\1\}", wrap, s, flags=re.S)


def _process_section(section_id: str, body: str) -> str:
    """Strip whatever heading the model wrote (SKILL.md asks for one, but
    this makes the actual heading text a host guarantee rather than a hope)
    and prepend the canonical title from sections.py, then apply the ported
    post-processes above. Mirrors assemble_paper.py's ``process_section``."""
    body = body.strip()
    body = re.sub(r"^\s*\\section\*?\{[^}]*\}\s*", "", body)
    body = strip_heading_numbers(body)
    body = move_table_captions_above(body)
    body = ensure_table_width(body)
    title = escape_latex(section_by_id(section_id).title)
    return f"\\section{{{title}}}\n{body}\n"


def assemble_document(
    *,
    title: str,
    section_bodies: dict[str, str],
    document_order: tuple[str, ...],
    keywords: str | None = None,
) -> str:
    """Concatenate the preamble, title, each section's already-generated
    LaTeX body (document_order — see sections.py), and the bibliography
    commands. Sections not present in section_bodies are skipped rather
    than erroring, so a partial draft can still be assembled for
    inspection when a section failed.

    "abstract" is special-cased into a real ``\\begin{abstract}`` block
    rather than dumped in as a plain section body: ts-write writes it as
    prose only (no ``\\section{}`` heading — see its SKILL.md), so without
    this it would otherwise render as an unnumbered blob of text with no
    visual distinction from the rest of the paper. A proper abstract
    environment is one of the cheapest, highest-value "looks like a real
    paper" fixes available (docs/paper_writing_design.md's gap analysis).

    ``keywords`` (optional, ts-plan's keywords.txt — see agent.py) renders
    as a plain "Keywords:" line right after the abstract, host-formatted
    the same way the title already is; omitted entirely if not given or
    empty, same "degrade, don't fabricate" stance every optional field in
    this pipeline already takes.
    """
    parts = [_PREAMBLE, f"\\title{{{escape_latex(title)}}}\n\\author{{OpenJiuwen Team}}\n\\maketitle\n"]
    abstract_body = section_bodies.get(_ABSTRACT_SECTION_ID)
    if abstract_body:
        parts.append(f"\\begin{{abstract}}\n{abstract_body.strip()}\n\\end{{abstract}}\n")
    if keywords:
        kw_list = [k.strip() for k in keywords.split(",") if k.strip()]
        if kw_list:
            kw_line = ", ".join(escape_latex(k) for k in kw_list)
            parts.append(f"\\noindent\\textbf{{Keywords:}} {kw_line}\\par\\bigskip\n")
    for section_id in document_order:
        if section_id == _ABSTRACT_SECTION_ID:
            continue
        body = section_bodies.get(section_id)
        if body:
            parts.append(_process_section(section_id, body))
    parts.append(_CLOSING)
    document = merge_adjacent_cites("\n".join(parts))
    return sanitize_for_pdflatex(document)


def render_results_table(rows: list[tuple[str, dict[str, str]]], metric_order: list[str]) -> str:
    """Deterministically render the results as a LaTeX ``tabular`` — host
    builds the table, the model writes prose around it, mirroring
    ``reporting._build_task_prompt``'s host-rendered variant list
    (docs/paper_writing_design.md §5). ``rows`` is
    ``[(escaped_variant_name, {escaped_metric_name: formatted_value})]``.
    """
    if not rows or not metric_order:
        return ""
    header = " & ".join(["Variant", *metric_order]) + r" \\"
    lines = [
        r"\begin{table}[h]",
        r"\centering",
        f"\\begin{{tabular}}{{l{'r' * len(metric_order)}}}",
        r"\toprule",
        header,
        r"\midrule",
    ]
    for name, metrics in rows:
        cells = [metrics.get(metric, "--") for metric in metric_order]
        lines.append(" & ".join([name, *cells]) + r" \\")
    lines.extend([r"\bottomrule", r"\end{tabular}", r"\caption{Results by variant.}", r"\end{table}"])
    return "\n".join(lines)


@dataclass
class CompileResult:
    success: bool
    pdf_path: Path | None
    log_tail: str


_ERROR_LINE_RE = re.compile(r"^! (.+)$", re.MULTILINE)


def extract_compile_errors(log_text: str, max_errors: int = 5) -> list[str]:
    """Pull LaTeX's own ``! <error>`` lines out of a compiler log — the
    span fed back to the model for a targeted repair (docs/paper_writing_design.md §8).
    """
    return _ERROR_LINE_RE.findall(log_text)[:max_errors]


_LOG_TAIL_CHARS = 4000
# TeX toolchains commonly ship as latexmk (preferred: reruns for you until
# references settle) with a bare pdflatex fallback for minimal installs.
_COMPILE_COMMANDS: tuple[tuple[str, ...], ...] = (
    ("latexmk", "-pdf", "-interaction=nonstopmode", "-halt-on-error"),
    ("pdflatex", "-interaction=nonstopmode", "-halt-on-error"),
)


def compile_document(tex_path: Path, *, timeout_seconds: int = 300) -> CompileResult:
    """Run latexmk (falling back to pdflatex) as a bounded, host-controlled
    subprocess. Never raises for a missing toolchain or a compile error —
    both are reported in ``CompileResult`` for the caller's bounded repair
    loop (docs/paper_writing_design.md §8) to act on.
    """
    workdir = tex_path.parent
    toolchain_missing = True
    for command in _COMPILE_COMMANDS:
        try:
            proc = subprocess.run(
                [*command, tex_path.name],
                cwd=workdir,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                encoding="utf-8",
                check=False,  # non-zero exit is inspected below, not exceptional
                errors="replace"
            )
        except FileNotFoundError:
            continue
        except subprocess.TimeoutExpired:
            return CompileResult(
                success=False,
                pdf_path=None,
                log_tail=f"compile timed out after {timeout_seconds}s running {command[0]}",
            )
        toolchain_missing = False
        pdf_path = tex_path.with_suffix(".pdf")
        log_tail = ((proc.stdout or "") + (proc.stderr or ""))[-_LOG_TAIL_CHARS:]
        if proc.returncode == 0 and pdf_path.is_file():
            return CompileResult(success=True, pdf_path=pdf_path, log_tail=log_tail)
        # If the command ran but failed, try the next fallback
        continue
    if toolchain_missing:
        return CompileResult(
            success=False,
            pdf_path=None,
            log_tail="no LaTeX toolchain found (latexmk/pdflatex not on PATH)",
        )
    return CompileResult(success=False, pdf_path=None, log_tail="compile failed for an unknown reason")
