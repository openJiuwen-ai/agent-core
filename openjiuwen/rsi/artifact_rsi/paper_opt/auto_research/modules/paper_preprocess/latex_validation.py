"""Standalone validation and structural parsing of a local LaTeX paper folder."""

from __future__ import annotations

import re
from pathlib import Path

from .schemas import LatexPaperDocument, LatexValidationError, PaperSection

_INPUT_RE = re.compile(r"\\(?:input|include)\s*\{([^}]+)\}")
_GRAPHICS_RE = re.compile(r"\\includegraphics(?:\s*\[[^]]*\])?\s*\{([^}]+)\}")
_BIB_RE = re.compile(r"\\bibliography\s*\{([^}]+)\}")
_CITE_RE = re.compile(r"\\cite[a-zA-Z*]*\s*(?:\[[^]]*\]\s*)?\{([^}]+)\}")
_TITLE_RE = re.compile(r"\\title\s*\{(.+?)\}", re.DOTALL)
_ABSTRACT_RE = re.compile(r"\\begin\s*\{abstract\}(.+?)\\end\s*\{abstract\}", re.DOTALL)
_SECTION_RE = re.compile(r"\\(?:sub)*section\*?\s*\{([^}]+)\}")
_COMMAND_RE = re.compile(r"\\(?:[a-zA-Z]+\*?|.)\s*(?:\[[^]]*\])?\s*(?:\{([^{}]*)\})?")
_WS_RE = re.compile(r"\s+")
_FIGURE_EXTENSIONS = (".pdf", ".png", ".jpg", ".jpeg", ".eps", ".svg")


def _strip_comments(text: str) -> str:
    return re.sub(r"(?<!\\)%[^\n]*", "", text)


def _clean_tex(text: str) -> str:
    text = _strip_comments(text).replace("~", " ").replace("\\_", "_").replace("\\%", "%")
    text = _COMMAND_RE.sub(lambda match: match.group(1) or " ", text)
    return _WS_RE.sub(" ", text).strip()


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _tex_reference(raw: str, parent: Path, root: Path) -> Path | None:
    candidate = (parent / raw.strip()).resolve()
    if candidate.suffix.lower() != ".tex":
        candidate = candidate.with_suffix(".tex")
    return candidate if _inside(candidate, root) else None


def _expand(path: Path, root: Path, errors: list[str], stack: set[Path], files: list[Path]) -> str:
    resolved = path.resolve()
    if resolved in stack:
        errors.append(f"cyclic LaTeX include detected: {path.relative_to(root)}")
        return ""
    stack.add(resolved)
    files.append(resolved)
    try:
        text = _strip_comments(path.read_text(encoding="utf-8", errors="replace"))
    except OSError as exc:
        errors.append(f"cannot read {path.relative_to(root)}: {exc}")
        stack.remove(resolved)
        return ""

    def replace(match: re.Match[str]) -> str:
        raw = match.group(1).strip()
        child = _tex_reference(raw, path.parent, root)
        if child is None:
            errors.append(f"LaTeX include escapes paper directory: {raw}")
            return ""
        if not child.is_file():
            errors.append(f"missing LaTeX include: {raw}")
            return ""
        return "\n" + _expand(child, root, errors, stack, files) + "\n"

    expanded = _INPUT_RE.sub(replace, text)
    stack.remove(resolved)
    return expanded


def _sections(text: str) -> list[PaperSection]:
    matches = list(_SECTION_RE.finditer(text))
    output: list[PaperSection] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        content = _clean_tex(text[match.end() : end])
        if content:
            output.append(PaperSection(title=_clean_tex(match.group(1)), content=content))
    return output


def _local_assets(files: list[Path], root: Path, errors: list[str]) -> tuple[list[Path], list[Path], set[str]]:
    figures: list[Path] = []
    bibliographies: list[Path] = []
    citations: set[str] = set()
    for source in dict.fromkeys(files):
        try:
            text = _strip_comments(source.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        for raw in _GRAPHICS_RE.findall(text):
            base = (source.parent / raw.strip()).resolve()
            candidates = [base] if base.suffix else [base.with_suffix(ext) for ext in _FIGURE_EXTENSIONS]
            existing = next((item for item in candidates if _inside(item, root) and item.is_file()), None)
            if existing is None:
                errors.append(f"missing or unsafe figure referenced by \\includegraphics: {raw.strip()}")
            elif existing not in figures:
                figures.append(existing)
        for group in _BIB_RE.findall(text):
            for raw in group.split(","):
                candidate = (source.parent / raw.strip()).resolve()
                if candidate.suffix.lower() != ".bib":
                    candidate = candidate.with_suffix(".bib")
                if not _inside(candidate, root):
                    errors.append(f"bibliography escapes paper directory: {raw.strip()}")
                elif not candidate.is_file():
                    errors.append(f"missing bibliography: {raw.strip()}")
                elif candidate not in bibliographies:
                    bibliographies.append(candidate)
        citations.update(key.strip() for group in _CITE_RE.findall(text) for key in group.split(",") if key.strip())
    return figures, bibliographies, citations


def _bib_keys(paths: list[Path]) -> set[str]:
    keys: set[str] = set()
    for path in paths:
        text = path.read_text(encoding="utf-8", errors="replace")
        keys.update(re.findall(r"@\w+\s*\{\s*([^,\s]+)", text))
    return keys


def validate_latex_paper(paper_dir: str | Path) -> LatexPaperDocument:
    """Validate a self-contained paper and return a readable, expanded document.

    This public function is deliberately independent from paper evidence extraction.
    """
    root = Path(paper_dir).resolve()
    if not root.is_dir():
        raise LatexValidationError([f"paper directory does not exist: {paper_dir}"])
    main = root / "main.tex"
    if not main.is_file():
        raise LatexValidationError([f"missing required main.tex in {root}"])

    errors: list[str] = []
    files: list[Path] = []
    expanded = _expand(main, root, errors, set(), files)
    if not re.search(r"\\documentclass(?:\s*\[[^]]*\])?\s*\{[^}]+\}", expanded):
        errors.append("main.tex is missing \\documentclass")
    if "\\begin{document}" not in expanded:
        errors.append("main.tex is missing \\begin{document}")
    if "\\end{document}" not in expanded:
        errors.append("main.tex is missing \\end{document}")
    title_match, abstract_match = _TITLE_RE.search(expanded), _ABSTRACT_RE.search(expanded)
    title = _clean_tex(title_match.group(1)) if title_match else ""
    abstract = _clean_tex(abstract_match.group(1)) if abstract_match else ""
    sections = _sections(expanded)
    if not title:
        errors.append("paper is missing a non-empty \\title{...}")
    if not abstract:
        errors.append("paper is missing a non-empty abstract environment")
    if not sections:
        errors.append("paper has no non-empty \\section{...} body")
    headings = [section.title.lower() for section in sections]
    if not any(any(word in title for word in ("experiment", "result", "evaluation")) for title in headings):
        errors.append("paper needs an experiment, evaluation, or results section")
    if not any(any(word in title for word in ("conclusion", "discussion")) for title in headings):
        errors.append("paper needs a conclusion or discussion section")

    figures, bibliographies, citations = _local_assets(files, root, errors)
    if citations and not bibliographies:
        errors.append("paper contains citations but no local \\bibliography{...} file")
    if citations and bibliographies:
        missing = sorted(citations - _bib_keys(bibliographies))
        if missing:
            errors.append("citation keys absent from bibliography: " + ", ".join(missing))
    if errors:
        raise LatexValidationError(errors)
    return LatexPaperDocument(
        paper_dir=str(root), main_tex_path=str(main), expanded_tex=expanded, title=title,
        abstract=abstract, sections=sections, bibliography_paths=[str(item) for item in bibliographies],
        figure_paths=[str(item) for item in figures], source_files=[str(item) for item in dict.fromkeys(files)],
    )
