# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Bounded local document conversion and its short-lived parser worker.

The worker is executed as a file to avoid importing the agent runtime. It reads
the original document and returns Markdown through a pipe, without a disk copy.
"""

from __future__ import annotations

import contextlib
import csv
import hashlib
import io
import json
import os
import posixpath
import re
import subprocess
import sys
import threading
import time
import zipfile
from collections.abc import Callable, Iterable
from concurrent.futures import Future
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urljoin

OFFICE_EXTENSIONS = frozenset({
    ".docm", ".dotx", ".dotm",
    ".pptx", ".pptm", ".potx", ".potm", ".ppsx", ".ppsm", ".sldx", ".sldm", ".ppam",
    ".xls", ".xlsx", ".xlsm", ".xlsb", ".xlt", ".xltx", ".xltm", ".xla", ".xlam", ".xlw",
    ".odt", ".ott", ".ods", ".ots", ".odp", ".otp",
})
EXTENSIONS = frozenset({".html", ".htm", ".csv", ".tsv"}) | OFFICE_EXTENSIONS
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_ROWS = 10_000
MAX_COLUMNS = 256
MAX_SLIDES = 1_000
CONVERSION_SECONDS = 3.0
_PARSER_STARTS = threading.BoundedSemaphore(32)


def _close_parser(process: subprocess.Popen) -> None:
    with contextlib.suppress(OSError, subprocess.TimeoutExpired):
        if process.poll() is None:
            process.kill()
        process.wait(timeout=0.5)
    if process.stdout is not None:
        with contextlib.suppress(OSError):
            process.stdout.close()


def _discard_parser(launch: Future) -> None:
    with contextlib.suppress(Exception):
        _close_parser(launch.result())


def convert_document(
    path: Path, extension: str, *, max_chars: int, cancelled: Callable[[], bool],
) -> dict[str, object]:
    """Convert off the event loop and terminate parsing on timeout/cancellation."""
    if cancelled():
        raise ValueError("local document conversion cancelled")
    deadline = time.monotonic() + CONVERSION_SECONDS
    if not _PARSER_STARTS.acquire(blocking=False):
        raise ValueError("local document parser creation is busy")
    launch = Future()
    process = None

    def spawn():
        try:
            try:
                created = subprocess.Popen(
                    [str(Path(sys.base_prefix) / "python.exe") if os.name == "nt" else sys.executable,
                     str(Path(__file__)), extension, str(path), str(max_chars), json.dumps(sys.path)],
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
            finally:
                # Release before waking the normal executor's waiter. Only OS
                # creation uses a slot, not parsing or late-handle cleanup.
                _PARSER_STARTS.release()
        except Exception as exc:
            launch.set_exception(exc)
        else:
            launch.set_result(created)

    try:
        threading.Thread(target=spawn, name="pcs-parser-start", daemon=True).start()
    except Exception:
        _PARSER_STARTS.release()
        raise
    try:
        # An OS creation call can outlast cancellation. Its owned daemon takes
        # care of any late handle without occupying the shared tool executor.
        while process is None:
            if cancelled() or time.monotonic() >= deadline:
                raise ValueError("local document conversion cancelled or timed out")
            try:
                process = launch.result(timeout=0.02)
            except TimeoutError:
                if launch.done():
                    process = launch.result()
        while True:
            if cancelled() or time.monotonic() >= deadline:
                raise ValueError("local document conversion cancelled or timed out")
            try:
                output, _ = process.communicate(timeout=0.02)
                break
            except subprocess.TimeoutExpired:
                pass
        result = json.loads(output)
        if process.returncode or not isinstance(result, dict) or not isinstance(result.get("content"), str):
            raise ValueError("local document conversion failed")
        return result
    finally:
        if process is None:
            launch.add_done_callback(_discard_parser)
        else:
            _close_parser(process)


def _text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _cell(value: object) -> str:
    return (
        _text(value).replace("\\", "\\\\").replace("|", "\\|")
        .replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br>")
    )


def _column(index: int) -> str:
    label = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        label = chr(65 + remainder) + label
    return label


def _table(rows: Iterable[tuple[int, list[object]]], *, max_chars: int) -> tuple[str, bool]:
    """Keep source record numbers and bound sparse/wide tables before rendering."""
    parts: list[str] = []
    chunk: list[tuple[int, list[str]]] = []
    size = 0
    truncated = False

    def flush() -> None:
        if not chunk:
            return
        width = max(len(values) for _, values in chunk)
        parts.append(f"\nRows {chunk[0][0]}–{chunk[-1][0]}\n\n")
        parts.append("| Row | " + " | ".join(_column(index + 1) for index in range(width)) + " |\n")
        parts.append("| --- | " + " | ".join("---" for _ in range(width)) + " |\n")
        for number, values in chunk:
            parts.append(f"| {number} | " + " | ".join(values + [""] * (width - len(values))) + " |\n")
        chunk.clear()

    for count, (number, values) in enumerate(rows):
        if count >= MAX_ROWS:
            truncated = True
            break
        if len(values) > MAX_COLUMNS:
            truncated = True
        values = values[:MAX_COLUMNS]
        while values and values[-1] is None:
            values.pop()
        cells = [_cell(value) for value in values]
        if not any(cells):
            continue
        size += sum(len(value) for value in cells) + 4 * len(cells) + 32
        chunk.append((number, cells))
        if size >= max_chars:
            truncated = True
            break
        if len(chunk) == 100:
            flush()
    flush()
    text = "".join(parts)
    return text[:max_chars], truncated or len(text) > max_chars


def _decode(raw: bytes) -> str:
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("gb18030")


def _html(raw: bytes, origin: str) -> tuple[str, bool]:
    from bs4 import BeautifulSoup
    from markdownify import markdownify

    soup = BeautifulSoup(raw, "html.parser")
    for tag in soup(["script", "style", "noscript", "template", "nav", "form", "button"]):
        tag.decompose()
    root = soup.find("article") or soup.find("main") or soup.body or soup
    for tag in root.find_all(["a", "img"]):
        attribute = "href" if tag.name == "a" else "src"
        value = str(tag.get(attribute, ""))
        if value.startswith("data:"):
            tag.replace_with(tag.get("alt", ""))
        elif value:
            tag[attribute] = urljoin(origin, value)
    try:
        return markdownify(str(root), heading_style="ATX", bullets="-", code_language=""), False
    except Exception:
        return root.get_text("\n", strip=True), True


def _check_archive(raw: bytes) -> None:
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        if sum(info.file_size for info in archive.infolist()) > MAX_ARCHIVE_BYTES:
            raise ValueError("decompressed document archive exceeds limit")


def _xlsx(raw: bytes, max_chars: int) -> tuple[str, bool]:
    from openpyxl import load_workbook

    _check_archive(raw)
    book = load_workbook(io.BytesIO(raw), read_only=True, data_only=False, keep_links=False)
    parts: list[str] = []
    size = 0
    truncated = False

    def rows(worksheet):
        records = worksheet.iter_rows(
            max_row=min(worksheet.max_row or MAX_ROWS + 1, MAX_ROWS + 1),
            max_col=min(worksheet.max_column or MAX_COLUMNS, MAX_COLUMNS + 1),
        )
        for number, row in enumerate(records, 1):
            yield number, [cell.value for cell in row]

    try:
        for sheet in book:
            text, limited = _table(rows(sheet), max_chars=max_chars - size)
            if text:
                text = f"\n## Sheet {sheet.title}\n" + text
                parts.append(text)
                size += len(text)
            truncated |= limited or (sheet.max_row or 0) > MAX_ROWS or (sheet.max_column or 0) > MAX_COLUMNS
            if size >= max_chars:
                truncated = True
                break
    finally:
        book.close()
    return "".join(parts), truncated


def _xls(raw: bytes, max_chars: int) -> tuple[str, bool]:
    import xlrd

    book = xlrd.open_workbook(file_contents=raw, on_demand=True, logfile=io.StringIO())
    parts: list[str] = []
    size = 0
    truncated = False

    def rows(worksheet):
        for index in range(min(worksheet.nrows, MAX_ROWS + 1)):
            values = []
            for cell in worksheet.row(index)[:MAX_COLUMNS + 1]:
                value = cell.value
                if cell.ctype == xlrd.XL_CELL_DATE:
                    value = xlrd.xldate_as_datetime(value, book.datemode)
                values.append(value)
            yield index + 1, values

    try:
        for sheet in book.sheets():
            text, limited = _table(rows(sheet), max_chars=max_chars - size)
            if text:
                text = f"\n## Sheet {sheet.name}\n" + text
                parts.append(text)
                size += len(text)
            truncated |= limited or sheet.nrows > MAX_ROWS or sheet.ncols > MAX_COLUMNS
            if size >= max_chars:
                truncated = True
                break
    finally:
        book.release_resources()
    return "".join(parts), truncated


def _pptx(raw: bytes, max_chars: int) -> tuple[str, bool]:
    from pptx import Presentation

    _check_archive(raw)
    presentation = Presentation(io.BytesIO(raw))
    parts: list[str] = []
    size = 0
    truncated = len(presentation.slides) > MAX_SLIDES

    def shapes_text(shapes):
        nonlocal truncated
        for shape in shapes:
            if shape.shape_type == 6:  # MSO_SHAPE_TYPE.GROUP
                yield from shapes_text(shape.shapes)
            elif shape.has_table:
                text, limited = _table(
                    ((index + 1, [cell.text for cell in row.cells]) for index, row in enumerate(shape.table.rows)),
                    max_chars=max_chars,
                )
                truncated |= limited
                yield text
            elif shape.has_text_frame:
                for paragraph in shape.text_frame.paragraphs:
                    if paragraph.text.strip():
                        yield "  " * min(paragraph.level, 8) + "- " + paragraph.text

    for index, slide in enumerate(presentation.slides):
        if index >= MAX_SLIDES:
            break
        texts: list[str] = []
        for text in shapes_text(slide.shapes):
            if text:
                texts.append(text)
                size += len(text) + 2
            if size >= max_chars:
                truncated = True
                break
        if slide.has_notes_slide and slide.notes_slide.notes_text_frame is not None:
            notes = slide.notes_slide.notes_text_frame.text.strip()
            if notes:
                texts.append("### Notes\n\n" + notes)
                size += len(notes) + 12
        if texts:
            text = f"\n## Slide {index + 1}\n\n" + "\n\n".join(texts)
            parts.append(text)
            size += len(f"\n## Slide {index + 1}\n\n")
        if size >= max_chars:
            truncated = True
            break
    return "".join(parts), truncated


def _clean_markdown(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    lines: list[str] = []
    fence: str | None = None
    blanks = 0
    for line in text.split("\n"):
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if marker:
            value = marker[1]
            if fence is None:
                fence = value
            elif value[0] == fence[0] and len(value) >= len(fence):
                fence = None
        if fence is None and not line.strip():
            blanks += 1
            if blanks > 1:
                continue
        else:
            blanks = 0
        lines.append(line)
    return "\n".join(lines).strip()


def _word(raw: bytes, max_chars: int) -> tuple[str, bool]:
    from docx import Document
    from docx.table import Table

    document = Document(io.BytesIO(_office_package(raw, presentation=False, word=True)))
    parts: list[str] = []
    size = 0
    for block in document.iter_inner_content():
        if isinstance(block, Table):
            texts = (" | ".join(cell.text.replace("\n", " ") for cell in row.cells) for row in block.rows)
        else:
            texts = (block.text,)
        for text in texts:
            if not text:
                continue
            if parts:
                text = "\n\n" + text
            parts.append(text[:max(0, max_chars - size)])
            size += len(text)
            if size >= max_chars:
                return "".join(parts), True
    return "".join(parts), False


def _xlsb(raw: bytes, max_chars: int) -> tuple[str, bool]:
    from pyxlsb import open_workbook

    _check_archive(raw)
    parts: list[str] = []
    size = 0
    truncated = False
    with open_workbook(io.BytesIO(raw)) as book:
        for number, name in enumerate(book.sheets, 1):
            with book.get_sheet(number) as sheet:
                if sheet.dimension is None:
                    continue
                if sheet.dimension.c + sheet.dimension.w > 16_384 or sheet.dimension.r + sheet.dimension.h > 1_048_576:
                    raise ValueError("invalid XLSB worksheet dimensions")
                text, limited = _table(
                    ((row[0].r + 1, [cell.v for cell in row[:MAX_COLUMNS + 1]])
                     for row in sheet.rows(sparse=True) if row), max_chars=max_chars - size,
                )
                text = f"\n## Sheet {name}\n" + text
                parts.append(text)
                size += len(text)
                truncated |= limited
                if size >= max_chars:
                    truncated = True
                    break
    return "".join(parts), truncated


def _office_package(raw: bytes, *, presentation: bool, single_slide: bool = False, word: bool = False) -> bytes:
    """Adapt an in-memory OOXML main part for the existing read-only parsers."""
    from lxml import etree

    _check_archive(raw)
    content_type = ("application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"
                    if presentation else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml")
    main = "ppt/presentation.xml" if presentation else "xl/workbook.xml"
    if word:
        content_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
        main = "word/document.xml"
    relation_ns = "http://schemas.openxmlformats.org/package/2006/relationships"
    overrides: dict[str, bytes] = {}
    output = io.BytesIO()
    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)
    with zipfile.ZipFile(io.BytesIO(raw)) as source, zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as target:
        relationships = etree.fromstring(source.read("_rels/.rels"), parser)
        office = next(node for node in relationships if node.get("Type", "").endswith("/officeDocument"))
        if office.get("TargetMode") == "External":
            raise ValueError("external Office main part is not supported")
        original_main = posixpath.normpath(office.get("Target", "").lstrip("/"))
        if single_slide:
            office.set("Target", main)
            overrides["_rels/.rels"] = etree.tostring(relationships)
            overrides[main] = (
                '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                '<p:sldIdLst><p:sldId id="256" r:id="pcs_slide"/></p:sldIdLst></p:presentation>'
            ).encode()
            rels = etree.Element("Relationships", nsmap={None: relation_ns})
            etree.SubElement(rels, "Relationship", Id="pcs_slide",
                             Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide",
                             Target=posixpath.relpath(original_main, "ppt"))
            overrides["ppt/_rels/presentation.xml.rels"] = etree.tostring(rels)
        else:
            main = original_main
        types = etree.fromstring(source.read("[Content_Types].xml"), parser)
        for node in list(types):
            if single_slide and node.get("PartName") == "/" + original_main:
                node.set("ContentType", "application/vnd.openxmlformats-officedocument.presentationml.slide+xml")
            if node.get("PartName") == "/" + main:
                types.remove(node)
        etree.SubElement(types, "{http://schemas.openxmlformats.org/package/2006/content-types}Override",
                         PartName="/" + main, ContentType=content_type)
        overrides["[Content_Types].xml"] = etree.tostring(types)
        for member in source.infolist():
            if member.filename not in overrides:
                target.writestr(member.filename, source.read(member))
        for name, value in overrides.items():
            target.writestr(name, value)
    return output.getvalue()


def _odf_text(node, max_chars: int) -> str:
    text_ns = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"

    def chunks(current):
        if current.text:
            yield current.text
        for child in current:
            if child.tag == text_ns + "s":
                repeat = max(1, int(child.get(text_ns + "c", "1")))
                yield " " * min(repeat, max_chars + 1)
            elif child.tag == text_ns + "line-break":
                yield "\n"
            elif child.tag == text_ns + "tab":
                yield "\t"
            else:
                yield from chunks(child)
            if child.tail:
                yield child.tail

    parts: list[str] = []
    size = 0
    for chunk in chunks(node):
        parts.append(chunk[:max(0, max_chars + 1 - size)])
        size += len(chunk)
        if size > max_chars:
            break
    return "".join(parts)


def _odf_paragraphs(nodes, max_chars: int, separator: str) -> str:
    parts: list[str] = []
    size = 0
    for index, node in enumerate(nodes):
        if index:
            parts.append(separator)
            size += len(separator)
        if size > max_chars:
            break
        text = _odf_text(node, max_chars - size)
        parts.append(text)
        size += len(text)
        if size > max_chars:
            break
    return "".join(parts)


def _odf(raw: bytes, extension: str, max_chars: int) -> tuple[str, bool]:
    from lxml import etree

    _check_archive(raw)
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        root = etree.fromstring(archive.read("content.xml"),
                                etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False))
    ns = {"draw": "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0",
          "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
          "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
          "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0"}
    parts: list[str] = []
    size = 0
    spreadsheet = extension in {".ods", ".ots"}
    text_document = extension in {".odt", ".ott"}
    pages = [root] if text_document else root.findall(".//table:table" if spreadsheet else ".//draw:page", ns)
    truncated = not spreadsheet and not text_document and len(pages) > MAX_SLIDES

    def rows(page, max_row_chars):
        nonlocal truncated
        number = 1
        for row in page.iter("{" + ns["table"] + "}table-row"):
            values: list[object] = []
            remaining = max_row_chars
            for cell in row:
                if len(values) >= MAX_COLUMNS or remaining < 0:
                    truncated = True
                    break
                value = _odf_paragraphs(cell.findall("text:p", ns), remaining, "\n")
                if not value:
                    for key in ("string-value", "date-value", "time-value", "boolean-value", "value"):
                        attribute = cell.get("{" + ns["office"] + "}" + key)
                        if attribute is not None:
                            value = attribute
                            break
                repeat = max(1, int(cell.get("{" + ns["table"] + "}number-columns-repeated", "1")))
                allowed = min(repeat, MAX_COLUMNS - len(values))
                truncated |= repeat > allowed
                for _ in range(allowed):
                    part = value[:max(0, remaining + 1)]
                    values.append(part)
                    remaining -= len(part) + 4
                    if remaining < 0:
                        truncated = True
                        break
            repeat = max(1, int(row.get("{" + ns["table"] + "}number-rows-repeated", "1")))
            for _ in range(min(repeat, MAX_ROWS + 1)):
                yield number, values
                number += 1
            if number > MAX_ROWS + 1:
                break

    for index, page in enumerate(pages if spreadsheet else pages[:MAX_SLIDES], 1):
        if spreadsheet:
            body, limited = _table(rows(page, max(0, max_chars - size)), max_chars=max_chars - size)
            truncated |= limited
            text = f"\n## Sheet {page.get('{' + ns['table'] + '}name', str(index))}\n" + body
        else:
            prefix = "" if text_document else f"\n## Slide {index}\n\n"
            paragraphs = (node for node in page.iter()
                          if node.tag in {"{" + ns["text"] + "}p", "{" + ns["text"] + "}h"})
            text = prefix + _odf_paragraphs(paragraphs, max(0, max_chars - size - len(prefix)), "\n\n")
        parts.append(text[:max(0, max_chars - size)])
        size += len(text)
        if size > max_chars:
            truncated = True
            break
    return "".join(parts), truncated


def render_document(raw: bytes, extension: str, origin: str, max_chars: int) -> dict[str, object]:
    """Render one bounded input without changing the original file or network."""
    degraded = False
    truncated = False
    if extension in {".html", ".htm"}:
        text, degraded = _html(raw, origin)
    elif extension in {".csv", ".tsv"}:
        csv.field_size_limit(20 * 1024 * 1024)
        reader = csv.reader(io.StringIO(_decode(raw), newline=""), delimiter="\t" if extension == ".tsv" else ",")
        text, truncated = _table(((number, list(row)) for number, row in enumerate(reader, 1)), max_chars=max_chars)
    elif extension == ".xlsx":
        text, truncated = _xlsx(raw, max_chars)
    elif extension == ".xls":
        text, truncated = _xls(raw, max_chars)
    elif extension == ".pptx":
        text, truncated = _pptx(raw, max_chars)
    elif extension in {".docm", ".dotx", ".dotm"}:
        text, truncated = _word(raw, max_chars)
    elif extension in {".pptm", ".potx", ".potm", ".ppsx", ".ppsm", ".ppam", ".sldx", ".sldm"}:
        normalized = _office_package(raw, presentation=True, single_slide=extension in {".sldx", ".sldm"})
        text, truncated = _pptx(normalized, max_chars)
    elif extension in {".xlsm", ".xltx", ".xltm", ".xlam"}:
        text, truncated = _xlsx(_office_package(raw, presentation=False), max_chars)
    elif extension in {".xlt", ".xla", ".xlw"}:
        text, truncated = _xls(raw, max_chars)
    elif extension == ".xlsb":
        text, truncated = _xlsb(raw, max_chars)
    elif extension in {".odt", ".ott", ".ods", ".ots", ".odp", ".otp"}:
        text, truncated = _odf(raw, extension, max_chars)
    else:
        raise ValueError("unsupported local document format")
    text = _clean_markdown(text)
    return {
        "content": text[:max_chars], "content_truncated": truncated or len(text) > max_chars,
        "conversion_metadata": {"content_format": "markdown", "converter_version": 1, "conversion_degraded": degraded},
    }


if __name__ == "__main__":
    # The base interpreter must use this venv's installed parsing dependencies.
    sys.path[:] = json.loads(sys.argv[4])
    try:
        source_path = Path(sys.argv[2])
        with source_path.open("rb") as source:
            source_bytes = source.read(20 * 1024 * 1024 + 1)
        if len(source_bytes) > 20 * 1024 * 1024:
            raise ValueError("local document exceeds size limit")
        rendered = render_document(source_bytes, sys.argv[1], source_path.resolve().as_uri(), int(sys.argv[3]))
        rendered["source_hash"] = hashlib.sha256(source_bytes).hexdigest()
    except Exception as exc:
        rendered = {"error": type(exc).__name__}
    # stdout carries a JSON protocol, not application logging.
    json.dump(rendered, sys.stdout, ensure_ascii=True)
