"""LaTeX source ingestion with section mapping, tables, and figure assets."""

from __future__ import annotations

import hashlib
import io
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.paper_scoring.schemas import (
    EXPERIMENT_FIGURE_SECTIONS,
    BibliographyEntry,
    CanonicalSection,
    FigureAsset,
    PaperDocument,
    PaperScoringSettings,
    PaperSection,
    TableBlock,
)

_INPUT_RE = re.compile(r"\\(?:input|include)\s*\{([^}]+)\}")
_BIBLIOGRAPHY_RE = re.compile(r"\\bibliography\s*\{([^}]+)\}")
_HEADING_CMD_RE = re.compile(
    r"\\(chapter|section|subsection|subsubsection)\s*\*?\s*\{"
)
_GRAPHICS_SUFFIXES = (".png", ".pdf", ".jpg", ".jpeg", ".webp")
_INCLUDEGRAPHICS_RE = re.compile(r"\\includegraphics(?:\s*\[[^\]]*\])?\s*\{([^}]+)\}")
_CAPTION_RE = re.compile(r"\\caption\s*\{")
_LABEL_RE = re.compile(r"\\label\s*\{([^}]+)\}")
_TEXT_MACRO_RE = re.compile(
    r"\\(?:texttt|textbf|textit|emph|textsc|textrm|textsf|mathrm|mathbf)\s*\{"
)
_CITE_RE = re.compile(r"\\cite[a-zA-Z]*\s*\{([^}]+)\}")
_REF_RE = re.compile(r"\\(?:eq)?ref\s*\{([^}]+)\}")
_SIMPLE_ESCAPE_RE = re.compile(r"\\([%&_$#{}])")
_WHITESPACE_RE = re.compile(r"[ \t]+")
_HEADING_PATTERNS: tuple[tuple[re.Pattern[str], CanonicalSection], ...] = (
    (re.compile(r"^abstract$", re.IGNORECASE), "abstract"),
    (re.compile(r"^(?:\d+|[ivxlcdm]+)?[.\s]*introduction$", re.IGNORECASE), "introduction"),
    (re.compile(r"^(?:\d+|[ivxlcdm]+)?[.\s]*related\s*work$", re.IGNORECASE), "related_work"),
    (
        re.compile(
            r"^(?:\d+|[ivxlcdm]+)?[.\s]*(?:method|methods|methodology|approach)$",
            re.IGNORECASE,
        ),
        "method",
    ),
    (
        re.compile(
            r"^(?:\d+|[ivxlcdm]+)?[.\s]*(?:experiments?|experimental(?:\s+setup)?|evaluation)$",
            re.IGNORECASE,
        ),
        "experiments",
    ),
    (re.compile(r"^(?:\d+|[ivxlcdm]+)?[.\s]*results?$", re.IGNORECASE), "results"),
    (
        re.compile(r"^(?:\d+|[ivxlcdm]+)?[.\s]*(?:discussion|conclusion|conclusions)$", re.IGNORECASE),
        "discussion",
    ),
    (re.compile(r"^(?:\d+|[ivxlcdm]+)?[.\s]*limitations?$", re.IGNORECASE), "limitations"),
    (
        re.compile(
            r"^(?:\d+|[ivxlcdm]+)?[.\s]*(?:appendix(?:\s+[a-z0-9]+)?|supplement(?:ary)?(?:\s+material)?)$",
            re.IGNORECASE,
        ),
        "appendix",
    ),
)
_IMAGE_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".pdf": "application/pdf",
}
_DIRECT_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


class LatexIngestError(ValueError):
    """Raised when a LaTeX paper bundle cannot be scored."""


@dataclass(frozen=True)
class LineRecord:
    source: str
    line_no: int
    text: str


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)


def available_input_tokens(settings: PaperScoringSettings) -> int:
    return max(
        1,
        settings.context_window_tokens
        - settings.output_reserve_tokens
        - settings.prompt_overhead_tokens,
    )


def _brace_end(text: str, open_idx: int) -> int:
    depth = 0
    for index in range(open_idx, len(text)):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index + 1
    raise LatexIngestError("unbalanced braces in LaTeX source")


def _strip_line_comment(line: str) -> str:
    index = 0
    while index < len(line):
        char = line[index]
        if char == "%" and (index == 0 or line[index - 1] != "\\"):
            return line[:index].rstrip()
        index += 1
    return line.rstrip()


def _jail(root: Path, target: Path) -> Path:
    root = root.resolve()
    target = target.resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise LatexIngestError(f"path escapes paper root {root}: {target}") from exc
    return target


def _resolve_graphics(root: Path, spec: str, *, current_source: str) -> Path:
    raw = Path(spec.strip())
    locations: list[Path] = []
    if raw.is_absolute():
        locations.append(raw)
    else:
        locations.append((root / current_source).parent / raw)
        locations.append(root / raw)
    suffixes = ("",) if raw.suffix else ("", *_GRAPHICS_SUFFIXES)
    escaped: Path | None = None
    seen: set[Path] = set()
    for location in locations:
        for suffix in suffixes:
            candidate = location if not suffix else location.with_suffix(suffix)
            if candidate in seen:
                continue
            seen.add(candidate)
            try:
                jailed = _jail(root, candidate)
            except LatexIngestError:
                escaped = candidate
                continue
            if jailed.is_file():
                return jailed
    if escaped is not None:
        raise LatexIngestError(f"path escapes paper root {root}: {escaped}")
    raise LatexIngestError(f"referenced figure not found: {spec}")


def _resolve_include(root: Path, current_dir: Path, spec: str) -> Path:
    candidate = Path(spec.strip())
    if not candidate.suffix:
        candidate = candidate.with_suffix(".tex")
    if candidate.is_absolute():
        path = candidate
    else:
        path = current_dir / candidate
        if not path.is_file():
            path = root / candidate
    return _jail(root, path)


def _expand_tex(path: Path, *, root: Path, stack: tuple[Path, ...] = ()) -> list[LineRecord]:
    resolved = _jail(root, path)
    if resolved in stack:
        cycle = " -> ".join(str(item.relative_to(root).as_posix()) for item in (*stack, resolved))
        raise LatexIngestError(f"cyclic \\input/\\include: {cycle}")
    if not resolved.is_file():
        raise LatexIngestError(f"included file not found: {resolved}")
    relative = resolved.relative_to(root).as_posix()
    records: list[LineRecord] = []
    text = resolved.read_text(encoding="utf-8")
    for line_no, raw in enumerate(text.splitlines(), start=1):
        line = _strip_line_comment(raw)
        if not line.strip():
            records.append(LineRecord(relative, line_no, ""))
            continue
        cursor = 0
        for match in _INPUT_RE.finditer(line):
            prefix = line[cursor : match.start()]
            if prefix.strip():
                records.append(LineRecord(relative, line_no, prefix.rstrip()))
            included = _resolve_include(root, resolved.parent, match.group(1))
            records.extend(_expand_tex(included, root=root, stack=(*stack, resolved)))
            cursor = match.end()
        suffix = line[cursor:]
        if suffix.strip() or cursor == 0:
            records.append(LineRecord(relative, line_no, suffix))
    return records


def _joined(records: list[LineRecord]) -> str:
    return "\n".join(record.text for record in records)


def _document_body(records: list[LineRecord]) -> list[LineRecord]:
    start = next(
        (index for index, record in enumerate(records) if r"\begin{document}" in record.text),
        None,
    )
    end = next(
        (index for index, record in enumerate(records) if r"\end{document}" in record.text),
        None,
    )
    if start is None:
        return records
    stop = len(records) if end is None else end + 1
    cleaned: list[LineRecord] = []
    for record in records[start:stop]:
        text = record.text
        if r"\begin{document}" in text:
            text = text.split(r"\begin{document}", 1)[1]
        if r"\end{document}" in text:
            text = text.split(r"\end{document}", 1)[0]
        cleaned.append(LineRecord(record.source, record.line_no, text))
    return cleaned


def _canonical_heading(title: str) -> CanonicalSection:
    stripped = re.sub(r"\s+", " ", title).strip().strip(":")
    stripped = re.sub(r"^[0-9ivxlcdm]+\s+", "", stripped, flags=re.IGNORECASE)
    for pattern, canonical in _HEADING_PATTERNS:
        if pattern.match(stripped):
            return canonical
    return "other"


def _unwrap_text_macros(text: str) -> str:
    changed = True
    while changed:
        changed = False
        for match in _TEXT_MACRO_RE.finditer(text):
            close = _brace_end(text, match.end() - 1)
            inner = text[match.end() : close - 1]
            text = text[: match.start()] + inner + text[close:]
            changed = True
            break
    return text


def _normalize_prose(text: str) -> str:
    text = _unwrap_text_macros(text)
    text = _CITE_RE.sub(lambda match: f"[cite:{match.group(1).replace(' ', '')}]", text)
    text = _REF_RE.sub(lambda match: f"[ref:{match.group(1)}]", text)
    text = _SIMPLE_ESCAPE_RE.sub(r"\1", text)
    text = re.sub(r"\\(?:noindent|centering|hfill|vspace\s*\{[^}]*\}|hspace\s*\{[^}]*\})", "", text)
    text = re.sub(r"\\(?:toprule|midrule|bottomrule|hline)\b", "", text)
    text = re.sub(r"\\(?:item)\b", "-", text)
    text = re.sub(r"\\[a-zA-Z]+\*?", " ", text)
    text = text.replace("{", "").replace("}", "")
    text = _WHITESPACE_RE.sub(" ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


_BLOCK_RE = re.compile(
    r"(\[TABLE [^\]]+\]\n(?:\|[^\n]*\n?)+|\[FIGURE [^\]]+\])",
)


def _normalize_keep_blocks(text: str) -> str:
    parts = _BLOCK_RE.split(text)
    kept: list[str] = []
    for part in parts:
        if part.startswith(("[TABLE", "[FIGURE")):
            kept.append(part.strip())
        else:
            cleaned = _normalize_prose(part)
            if cleaned:
                kept.append(cleaned)
    return "\n\n".join(kept)


def _split_unescaped(text: str, sep: str) -> list[str]:
    parts: list[str] = []
    buf: list[str] = []
    index = 0
    while index < len(text):
        if text.startswith(sep, index) and (index == 0 or text[index - 1] != "\\"):
            parts.append("".join(buf))
            buf = []
            index += len(sep)
            continue
        buf.append(text[index])
        index += 1
    parts.append("".join(buf))
    return parts


def _tabular_to_markdown(tabular: str) -> str:
    inner = tabular
    begin = re.search(r"\\begin\{tabular\}(?:\s*\[[^\]]*\])?\s*\{[^}]*\}", inner)
    if begin:
        inner = inner[begin.end() :]
    end_tab = re.search(r"\\end\{tabular\}", inner)
    if end_tab:
        inner = inner[: end_tab.start()]
    rows = [
        _normalize_prose(row).replace("|", "\\|")
        for row in _split_unescaped(inner, r"\\")
        if _normalize_prose(row)
    ]
    if not rows:
        return ""
    parsed = [[cell.strip() for cell in _split_unescaped(row, "&")] for row in rows]
    width = max(len(row) for row in parsed)
    parsed = [row + [""] * (width - len(row)) for row in parsed]
    header = parsed[0]
    body = parsed[1:] or [[""] * width]
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(["---"] * width) + " |",
    ]
    for row in body:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def _extract_caption(block: str) -> str:
    match = _CAPTION_RE.search(block)
    if not match:
        return ""
    close = _brace_end(block, match.end() - 1)
    return _normalize_prose(block[match.end() : close - 1])


def _extract_label(block: str) -> str | None:
    match = _LABEL_RE.search(block)
    return match.group(1).strip() if match else None


def _find_env(text: str, name: str, start: int = 0) -> tuple[int, int] | None:
    begin = re.search(rf"\\begin\s*\{{{re.escape(name)}\}}", text[start:])
    if not begin:
        return None
    begin_at = start + begin.start()
    end = re.search(rf"\\end\s*\{{{re.escape(name)}\}}", text[begin_at:])
    if not end:
        raise LatexIngestError(f"unclosed \\begin{{{name}}}")
    return begin_at, begin_at + end.end()


def _add_section(
    sections: list[PaperSection],
    *,
    index: int,
    name: str,
    canonical: CanonicalSection,
    text: str,
    source: str,
    line_start: int,
    line_end: int,
    in_appendix: bool,
) -> int:
    if in_appendix and canonical != "appendix":
        canonical = "appendix"
    body = text.strip()
    if not body and canonical == "other" and name == "preamble":
        return index
    sections.append(
        PaperSection(
            section_id=f"sec-{index:03d}" if name != "preamble" else "sec-preamble",
            name=name,
            canonical_name=canonical,
            text=body,
            source_path=source,
            line_start=line_start,
            line_end=max(line_start, line_end),
        )
    )
    return index + 1 if name != "preamble" else index


def _parse_bib_file(path: Path) -> list[BibliographyEntry]:
    if not path.is_file():
        raise LatexIngestError(f"bibliography file not found: {path}")
    text = path.read_text(encoding="utf-8")
    entries: list[BibliographyEntry] = []
    for match in re.finditer(r"@\w+\s*\{([^,]+),", text):
        key = match.group(1).strip()
        close = text.find("\n}", match.end())
        block = text[match.start() : close if close >= 0 else match.end() + 400]
        title_m = re.search(r"title\s*=\s*[{\"](.+?)[}\"]", block, re.IGNORECASE | re.DOTALL)
        author_m = re.search(r"author\s*=\s*[{\"](.+?)[}\"]", block, re.IGNORECASE | re.DOTALL)
        year_m = re.search(r"year\s*=\s*[{\"]?(\d{4})", block, re.IGNORECASE)
        entries.append(
            BibliographyEntry(
                key=key,
                title=_normalize_prose(title_m.group(1)) if title_m else "",
                authors=_normalize_prose(author_m.group(1)) if author_m else "",
                year=year_m.group(1) if year_m else "",
            )
        )
    return entries


def _load_image_png(path: Path, *, max_side: int) -> tuple[bytes, int, int]:
    from PIL import Image

    image = Image.open(path)
    image.load()
    if image.mode not in {"RGB", "RGBA"}:
        image = image.convert("RGB")
    width, height = image.size
    longest = max(width, height)
    if longest > max_side:
        scale = max_side / longest
        image = image.resize(
            (max(1, int(width * scale)), max(1, int(height * scale))),
            Image.Resampling.LANCZOS,
        )
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue(), image.width, image.height


def _rasterize_pdf_png(path: Path, *, max_side: int) -> tuple[bytes, int, int]:
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:
        raise LatexIngestError(
            "paper_scoring requires pypdfium2 to rasterize PDF figures"
        ) from exc
    pdf = pdfium.PdfDocument(str(path))
    if len(pdf) < 1:
        raise LatexIngestError(f"PDF figure has no pages: {path}")
    page = pdf[0]
    width, height = page.get_size()
    longest = max(width, height) or 1.0
    scale = min(4.0, max(0.5, max_side / longest))
    bitmap = page.render(scale=scale)
    image = bitmap.to_pil()
    if image.mode not in {"RGB", "RGBA"}:
        image = image.convert("RGB")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue(), image.width, image.height


def _load_figure(
    source: Path,
    *,
    figure_id: str,
    section_id: str,
    canonical: CanonicalSection,
    caption: str,
    label: str | None,
    relative: str,
    max_side: int,
) -> FigureAsset:
    suffix = source.suffix.lower()
    mime = _IMAGE_MIME.get(suffix)
    if mime is None:
        raise LatexIngestError(
            f"unsupported figure type {source.suffix or '(none)'}: {source}"
        )
    if suffix in _DIRECT_IMAGE_SUFFIXES:
        png_bytes, width, height = _load_image_png(source, max_side=max_side)
    else:
        png_bytes, width, height = _rasterize_pdf_png(source, max_side=max_side)
    return FigureAsset(
        figure_id=figure_id,
        section_id=section_id,
        canonical_section=canonical,
        caption=caption,
        label=label,
        source_path=relative,
        mime_type="image/png",
        sha256=hashlib.sha256(png_bytes).hexdigest(),
        width=width,
        height=height,
        png_bytes=png_bytes,
    )


def _rewrite_and_extract(
    records: list[LineRecord],
    *,
    root: Path,
    settings: PaperScoringSettings,
) -> tuple[list[PaperSection], list[TableBlock], list[FigureAsset]]:
    text = _joined(records)
    line_starts: list[int] = []
    cursor = 0
    for record in records:
        line_starts.append(cursor)
        cursor += len(record.text) + 1

    def _record_at(index: int) -> LineRecord:
        for start, record in zip(reversed(line_starts), reversed(records), strict=False):
            if index >= start:
                return record
        return records[0]

    sections: list[PaperSection] = []
    tables: list[TableBlock] = []
    figures: list[FigureAsset] = []
    in_appendix = False
    section_index = 0
    buf: list[str] = []
    buf_source = records[0].source if records else "main.tex"
    buf_line = records[0].line_no if records else 1
    pos = 0

    def _flush(*, name: str, canonical: CanonicalSection, end_record: LineRecord) -> None:
        nonlocal section_index, buf, buf_source, buf_line
        body = "\n".join(buf).strip()
        if body or name != "preamble":
            section_index = _add_section(
                sections,
                index=section_index,
                name=name,
                canonical=canonical,
                text=_normalize_keep_blocks(body),
                source=buf_source,
                line_start=buf_line,
                line_end=end_record.line_no,
                in_appendix=in_appendix,
            )
        buf = []

    current_name = "preamble"
    current_canonical: CanonicalSection = "other"
    current_id = "sec-preamble"

    while pos < len(text):
        if text.startswith(r"\appendix", pos):
            end_record = _record_at(pos)
            _flush(name=current_name, canonical=current_canonical, end_record=end_record)
            in_appendix = True
            current_name = "Appendix"
            current_canonical = "appendix"
            current_id = f"sec-{section_index:03d}"
            buf_source = end_record.source
            buf_line = end_record.line_no
            pos += len(r"\appendix")
            continue

        abstract = re.match(r"\\begin\{abstract\}", text[pos:])
        if abstract:
            end_record = _record_at(pos)
            _flush(name=current_name, canonical=current_canonical, end_record=end_record)
            close = re.search(r"\\end\{abstract\}", text[pos:])
            if not close:
                raise LatexIngestError("unclosed abstract environment")
            inner = text[pos + abstract.end() : pos + close.start()]
            sections.append(
                PaperSection(
                    section_id=f"sec-{section_index:03d}",
                    name="Abstract",
                    canonical_name="abstract",
                    text=_normalize_prose(inner),
                    source_path=end_record.source,
                    line_start=end_record.line_no,
                    line_end=_record_at(pos + close.end()).line_no,
                )
            )
            section_index += 1
            current_name = "body"
            current_canonical = "other"
            current_id = f"sec-{section_index:03d}"
            buf_source = _record_at(pos + close.end()).source
            buf_line = _record_at(pos + close.end()).line_no
            pos += close.end()
            continue

        section_match = _HEADING_CMD_RE.match(text[pos:])
        if section_match:
            brace_at = pos + section_match.end() - 1
            close = _brace_end(text, brace_at)
            title = _normalize_prose(text[brace_at + 1 : close - 1])
            end_record = _record_at(pos)
            _flush(name=current_name, canonical=current_canonical, end_record=end_record)
            command = section_match.group(1)
            mapped = _canonical_heading(title)
            if command in {"subsection", "subsubsection"} and mapped == "other":
                if current_canonical == "other" and in_appendix:
                    mapped = "appendix"
                elif current_canonical != "other":
                    mapped = current_canonical
            current_name = title or command
            current_canonical = mapped
            current_id = f"sec-{section_index:03d}"
            buf_source = end_record.source
            buf_line = end_record.line_no
            pos = close
            continue

        table_span = None
        if text.startswith(r"\begin{table}", pos) or text.startswith(r"\begin{table*}", pos):
            env_name = "table*" if text.startswith(r"\begin{table*}", pos) else "table"
            table_span = _find_env(text, env_name, pos)
        tabular_only = None
        if table_span is None and text.startswith(r"\begin{tabular}", pos):
            tabular_only = _find_env(text, "tabular", pos)

        if table_span or tabular_only:
            start, end = table_span or tabular_only
            block = text[start:end]
            caption = _extract_caption(block)
            markdown = _tabular_to_markdown(block)
            rec = _record_at(start)
            section_id = current_id
            table_id = f"tab-{len(tables) + 1:03d}"
            if markdown:
                tables.append(
                    TableBlock(
                        table_id=table_id,
                        section_id=section_id,
                        caption=caption,
                        markdown=markdown,
                        source_path=rec.source,
                        line_start=rec.line_no,
                    )
                )
                label = f"[TABLE {table_id}: {caption or 'untitled'}]\n{markdown}"
            else:
                label = "[TABLE empty]"
            buf.append(label)
            pos = end
            continue

        figure_span = None
        if text.startswith(r"\begin{figure}", pos) or text.startswith(r"\begin{figure*}", pos):
            env_name = "figure*" if text.startswith(r"\begin{figure*}", pos) else "figure"
            figure_span = _find_env(text, env_name, pos)
        include = _INCLUDEGRAPHICS_RE.match(text[pos:])
        if figure_span or include:
            if figure_span:
                start, end = figure_span
                block = text[start:end]
                include_match = _INCLUDEGRAPHICS_RE.search(block)
                caption = _extract_caption(block)
                label = _extract_label(block)
                if include_match is None:
                    rec = _record_at(start)
                    markdown = _tabular_to_markdown(block)
                    if markdown:
                        table_id = f"tab-{len(tables) + 1:03d}"
                        tables.append(
                            TableBlock(
                                table_id=table_id,
                                section_id=current_id,
                                caption=caption,
                                markdown=markdown,
                                source_path=rec.source,
                                line_start=rec.line_no,
                            )
                        )
                        buf.append(f"[TABLE {table_id}: {caption or 'untitled'}]\n{markdown}")
                    else:
                        buf.append(
                            f"[FIGURE caption-only: {caption or label or 'untitled'}]"
                        )
                    pos = end
                    continue
                spec = include_match.group(1).strip()
            else:
                assert include is not None
                start, end = pos, pos + include.end()
                spec = include.group(1).strip()
                caption = ""
                label = None
            rec = _record_at(start)
            source = _resolve_graphics(root, spec, current_source=rec.source)
            if len(figures) >= settings.max_figures:
                raise LatexIngestError(
                    f"paper exceeds max_figures={settings.max_figures}: {source}"
                )
            section_id, canonical = current_id, current_canonical
            figure = _load_figure(
                source,
                figure_id=f"fig-{len(figures) + 1:03d}",
                section_id=section_id,
                canonical=canonical,
                caption=caption,
                label=label,
                relative=source.relative_to(root).as_posix(),
                max_side=settings.figure_max_side,
            )
            figures.append(figure)
            buf.append(
                f"[FIGURE {figure.figure_id}: {caption or source.name} | source={figure.source_path}]"
            )
            pos = end
            continue

        next_break = len(text)
        for token in (
            r"\appendix",
            r"\begin{abstract}",
            r"\subsubsection",
            r"\subsection",
            r"\section",
            r"\chapter",
            r"\begin{table",
            r"\begin{tabular}",
            r"\begin{figure",
            r"\includegraphics",
        ):
            found = text.find(token, pos + 1)
            if found >= 0:
                next_break = min(next_break, found)
        buf.append(text[pos:next_break])
        pos = next_break if next_break > pos else len(text)

    if buf or not sections:
        end_record = records[-1] if records else LineRecord("main.tex", 1, "")
        if current_name == "preamble":
            body = _normalize_keep_blocks("\n".join(buf))
            if body:
                _add_section(
                    sections,
                    index=section_index,
                    name="preamble",
                    canonical="other",
                    text=body,
                    source=buf_source,
                    line_start=buf_line,
                    line_end=end_record.line_no,
                    in_appendix=False,
                )
        else:
            _flush(name=current_name, canonical=current_canonical, end_record=end_record)

    if not sections:
        raise LatexIngestError("LaTeX source produced no scorable sections")

    # Rebind table/figure section ids if they landed in preamble before first section.
    first_real = next(
        (section.section_id for section in sections if section.section_id != "sec-preamble"),
        sections[0].section_id,
    )
    for table in tables:
        if table.section_id == "sec-preamble" and first_real != "sec-preamble":
            table.section_id = first_real
    for figure in figures:
        if figure.section_id == "sec-preamble" and first_real != "sec-preamble":
            figure.section_id = first_real
            figure.canonical_section = next(
                section.canonical_name
                for section in sections
                if section.section_id == first_real
            )

    for table in tables:
        for section in sections:
            if section.section_id == table.section_id and table.table_id not in section.text:
                section.text = (
                    section.text + f"\n\n[TABLE {table.table_id}: {table.caption or 'untitled'}]\n"
                    f"{table.markdown}"
                ).strip()

    return sections, tables, figures


def ingest_latex(
    path: str | Path,
    *,
    paper_id: str = "paper",
    settings: PaperScoringSettings | None = None,
) -> PaperDocument:
    settings = settings or PaperScoringSettings()
    tex_path = Path(path).expanduser().resolve()
    if not tex_path.is_file():
        raise LatexIngestError(f"LaTeX file does not exist: {tex_path}")
    root = tex_path.parent
    records = _expand_tex(tex_path, root=root)
    included = list(dict.fromkeys(record.source for record in records))
    body = _document_body(records)
    if not body:
        raise LatexIngestError(f"empty document body: {tex_path}")

    bibliography: list[BibliographyEntry] = []
    joined = _joined(body)
    bib_match = _BIBLIOGRAPHY_RE.search(joined)
    if bib_match:
        for spec in bib_match.group(1).split(","):
            bib_path = spec.strip()
            if not bib_path.endswith(".bib"):
                bib_path += ".bib"
            bibliography.extend(_parse_bib_file(_jail(root, root / bib_path)))

    sections, tables, figures = _rewrite_and_extract(
        body, root=root, settings=settings
    )
    if bibliography:
        bib_lines = []
        for entry in bibliography:
            detail = entry.title or entry.key
            extra = ", ".join(part for part in (entry.authors, entry.year) if part)
            bib_lines.append(f"- {entry.key}: {detail}" + (f" ({extra})" if extra else ""))
        sections.append(
            PaperSection(
                section_id="sec-bibliography",
                name="Bibliography",
                canonical_name="other",
                text="\n".join(bib_lines),
                source_path=bib_match.group(1) if bib_match else "refs.bib",
                line_start=1,
                line_end=max(1, len(bibliography)),
            )
        )

    full_text = "\n\n".join(
        f"## {section.name} [id={section.section_id} source={section.source_path}:{section.line_start}-{section.line_end}]\n"
        f"{section.text}"
        for section in sections
        if section.text
    )
    if len(full_text) < settings.min_text_chars:
        raise LatexIngestError(f"insufficient LaTeX text: {tex_path}")
    token_estimate = estimate_tokens(full_text)
    budget = available_input_tokens(settings)
    if token_estimate > budget:
        raise LatexIngestError(
            f"paper exceeds context budget ({token_estimate} tokens > {budget}): {tex_path}"
        )

    digest = hashlib.sha256()
    for relative in included:
        digest.update(relative.encode("utf-8"))
        digest.update(sha256_file(root / relative).encode("utf-8"))
    for figure in figures:
        digest.update(figure.source_path.encode("utf-8"))
        digest.update(figure.sha256.encode("utf-8"))
    return PaperDocument(
        paper_id=paper_id,
        tex_path=str(tex_path),
        paper_root=str(root),
        sha256=digest.hexdigest(),
        full_text=full_text,
        sections=sections,
        tables=tables,
        figures=figures,
        bibliography=bibliography,
        included_files=included,
        token_estimate=token_estimate,
    )


def render_paper_for_prompt(paper: PaperDocument, *, label: str | None = None) -> str:
    title = label or "Paper under review"
    blocks = [f"# {title}"]
    for section in paper.sections:
        if not section.text:
            continue
        blocks.append(
            f"## {section.name} [id={section.section_id} "
            f"source={section.source_path}:{section.line_start}-{section.line_end}]\n"
            f"{section.text}"
        )
    return "\n\n".join(blocks)


def select_figures(
    paper: PaperDocument,
    *,
    experiment_only: bool = False,
) -> list[FigureAsset]:
    if not experiment_only:
        return list(paper.figures)
    return [
        figure
        for figure in paper.figures
        if figure.canonical_section in EXPERIMENT_FIGURE_SECTIONS
    ]


def paper_manifest(paper: PaperDocument) -> dict[str, Any]:
    dumped = paper.model_dump(mode="json")
    for figure in dumped.get("figures", []):
        figure.pop("png_bytes", None)
    return dumped
