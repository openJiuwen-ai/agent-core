"""Per-run original-link registration and publication-time target resolution."""

from __future__ import annotations

import hashlib
import html
import json
import ntpath
import os
import posixpath
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from urllib.parse import quote, unquote, urljoin, urlsplit

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.harness.personal_context.source_markdown import normalize_source_links, rewrite_markdown_prose
from openjiuwen.harness.personal_context.source_metadata import (
    normalize_source_locator,
    read_source_metadata,
    source_id_for_locator,
)
from openjiuwen.harness.personal_context.status_codes import StatusCode, build_error

_LINK = re.compile(r"\[([^\]\n]*)\]\(pcs-source-link:([0-9a-f]{32})\)")
_TOKEN_PREFIX = "pcs-source-link:"


def _text(value: str) -> str:
    value = html.escape(value, quote=False)
    for character in "[]`\\*_":
        value = value.replace(character, f"&#{ord(character)};")
    return value.replace(_TOKEN_PREFIX, "pcs&#45;source-link:")


def _error(message: str) -> BaseError:
    return build_error(StatusCode.CONTEXT_PROACTIVE_PIPELINE_EXECUTION_ERROR, error_msg=message)


def _locator_key(locator: str) -> str:
    if re.match(r"^[A-Za-z]:[\\/]", locator) or locator.startswith("\\\\"):
        return ntpath.normcase(ntpath.normpath(locator))
    parsed = urlsplit(locator)
    if parsed.scheme.casefold() in {"http", "https"}:
        return normalize_source_locator(locator)
    if parsed.scheme.casefold() == "file":
        path = unquote(parsed.path)
        if parsed.netloc:
            path = posixpath.join("//", parsed.netloc, path.lstrip("/"))
        if re.match(r"^/[A-Za-z]:/", path):
            path = path[1:]
        return _locator_key(path)
    return posixpath.normpath(locator)


def _resolve_target(origin: str, target: str) -> str:
    target = html.unescape(target)
    target = re.sub(r"\\([!\"#$%&'()*+,\-./:;<=>?@\[\]\\^_`{|}~])", r"\1", target)
    if re.match(r"^[A-Za-z]:[\\/]", target) or target.startswith("\\\\"):
        return _locator_key(target)
    try:
        parsed = urlsplit(target)
        if parsed.scheme and parsed.scheme.casefold() not in {"file", "http", "https"}:
            return ""
        if parsed.scheme:
            return _locator_key(target)
        if urlsplit(origin).scheme.casefold() in {"http", "https"}:
            return _locator_key(urljoin(origin, target))
        path = unquote(parsed.path)
        base = _locator_key(origin)
        if not path:
            return base
        if re.match(r"^[A-Za-z]:[\\/]", base) or base.startswith("\\\\"):
            return _locator_key(ntpath.join(ntpath.dirname(base), path))
        return _locator_key(posixpath.join(posixpath.dirname(base), path))
    except ValueError:
        return ""


def register_source_links(markdown: str, origin: str) -> tuple[str, dict[str, dict[str, str]]]:
    """Register source-position identities, without reading original targets."""
    book: dict[str, dict[str, str]] = {}

    def register(label: str, target: str) -> str:
        record = {
            "source_id": source_id_for_locator(origin),
            "origin": origin,
            "original_target": target,
            "resolved_target": _resolve_target(origin, target),
            "label": label,
        }
        identity = hashlib.sha256(json.dumps(record, sort_keys=True).encode("utf-8")).hexdigest()[:32]
        book[identity] = record
        return f"[原文链接：{_text(label or target)}]({_TOKEN_PREFIX}{identity})"

    return normalize_source_links(markdown, render_link=register), book


def source_link_preview(markdown: str) -> str:
    """Keep labels in bounded summaries without truncating program tokens."""
    return _LINK.sub(lambda match: match.group(1), markdown)


def collect_source_link_book(documents: Sequence[Mapping[str, object]]) -> dict[str, dict[str, str]]:
    """Load program-written records before any Filesystem model can run."""
    book: dict[str, dict[str, str]] = {}
    for document in documents:
        records = document.get("source_links", {})
        if not isinstance(records, dict):
            raise _error("source link book is invalid")
        for identity, record in records.items():
            fields = {"source_id", "origin", "original_target", "resolved_target", "label"}
            if not isinstance(record, dict) or set(record) != fields:
                raise _error("source link record is invalid")
            if not all(isinstance(value, str) for value in record.values()):
                raise _error("source link record values are invalid")
            expected = hashlib.sha256(json.dumps(record, sort_keys=True).encode("utf-8")).hexdigest()[:32]
            if identity != expected or record["source_id"] != source_id_for_locator(record["origin"]):
                raise _error("source link record identity is invalid")
            book[identity] = dict(record)
    return book


def _registered_targets(source_root: Path) -> dict[str, Path | None]:
    targets: dict[str, Path | None] = {}
    for path in source_root.glob("src_*.md"):
        try:
            record = read_source_metadata(path)
            key = _locator_key(str(record["locator"]))
        except (BaseError, ValueError):
            # A source-link target must be usable; this does not repair or bypass
            # validation of the Context's own provenance references.
            continue
        targets[key] = None if key in targets else path
    return targets


def _resolve_page_source_links(
    text: str,
    *,
    page_parent: Path,
    targets: Mapping[str, Path | None],
    book: Mapping[str, Mapping[str, str]],
) -> str:
    def replace(match: re.Match[str]) -> str:
        record = book.get(match.group(2))
        if record is None:
            raise _error("candidate contains an unregistered source link")
        target = targets.get(record["resolved_target"])
        label = _text(record["label"] or record["original_target"])
        if target is not None:
            relative = os.path.relpath(target, page_parent)
            relative = relative.replace("\\", "/")
            fragment = urlsplit(record["original_target"]).fragment
            suffix = f"（原文锚点：{_text(fragment)}）" if fragment else ""
            return f"[{label}](<{relative}>){suffix}"
        original = record["original_target"]
        try:
            parsed = urlsplit(original)
            if parsed.scheme.casefold() in {"http", "https"} and parsed.hostname:
                destination = quote(original, safe=":/?#[]@!$&'*,;=%+-._~")
                return f"[{label}](<{destination}>)"
        except ValueError:
            pass
        return f"{_text(record['label'])}（原文链接：{_text(original)}）"

    def rewrite(prose: str) -> str:
        rewritten = _LINK.sub(replace, prose)
        if _TOKEN_PREFIX in rewritten:
            raise _error("candidate contains a malformed or unregistered source link")
        return rewritten

    return rewrite_markdown_prose(text, rewrite)


def resolve_source_links(
    context_root: Path,
    *,
    final_context_root: Path,
    source_root: Path,
    book: Mapping[str, Mapping[str, str]],
) -> None:
    """Rewrite only registered tokens, then let normal reference validation run."""
    targets = _registered_targets(source_root) if book else {}
    replacements: dict[Path, str] = {}
    for page in context_root.rglob("*.md"):
        text = page.read_text(encoding="utf-8")
        page_parent = (final_context_root / page.relative_to(context_root)).parent
        result = _resolve_page_source_links(
            text,
            page_parent=page_parent,
            targets=targets,
            book=book,
        )
        if result != text:
            replacements[page] = result
    for page, text in replacements.items():
        page.write_text(text, encoding="utf-8")
