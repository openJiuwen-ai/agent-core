from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup, NavigableString, Tag

from openjiuwen.core.context_engine.processor.offloader.rules.common import meets_savings_ratio
from openjiuwen.core.context_engine.processor.offloader.rules.types import (
    ContentType,
    RuleCompressionResult,
    RuleContext,
)


_MAIN_SELECTORS = (
    "article",
    "main",
    '[role="main"]',
    ".article-body",
    ".post-content",
    ".entry-content",
    ".post-body",
    "#content",
    ".main-content",
)
_NOISE_TAGS = ("script", "style", "nav", "footer", "aside", "form", "noscript", "template", "svg")
_NOISE_NAME_RE = re.compile(
    r"(^|[-_])(ad|ads|advert|banner|breadcrumb|cookie|footer|header|menu|nav|newsletter|"
    r"promo|related|share|sidebar|social|sponsor|toolbar)([-_]|$)",
    re.IGNORECASE,
)
_HIDDEN_STYLE_RE = re.compile(r"(?:display\s*:\s*none|visibility\s*:\s*hidden)", re.IGNORECASE)
_BLOCK_TAGS = {
    "article",
    "blockquote",
    "div",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "main",
    "ol",
    "p",
    "pre",
    "section",
    "table",
    "ul",
}


class HtmlCompressor:
    def compress(self, content: str, ctx: RuleContext) -> RuleCompressionResult:
        try:
            soup = _parse_html(content)
        except Exception:
            return _unchanged(content)

        title = _extract_title(soup)
        removed_node_count = _remove_noise(soup)
        main, source = _find_main_content(soup, ctx.html_min_content_chars)
        if main is None:
            return _unchanged(content)

        blocks = _render_container(main)
        if title and not _contains_title(blocks, title):
            blocks.insert(0, f"# {title}")
        blocks, duplicate_count = _deduplicate_blocks(blocks)
        candidate = "\n\n".join(blocks).strip()
        if len(_plain_text(candidate)) < ctx.html_min_content_chars:
            return _unchanged(content)

        details: dict[str, Any] = {
            "main_content_source": source,
            "removed_node_count": removed_node_count,
            "duplicate_block_count": duplicate_count,
            "link_count": len(main.find_all("a")),
            "table_count": len(main.find_all("table")),
            "code_block_count": len(main.find_all("pre")),
            "output_block_count": len(blocks),
        }
        if candidate != content and meets_savings_ratio(content, candidate, ctx):
            return RuleCompressionResult(
                content=candidate,
                content_type=ContentType.HTML,
                modified=True,
                lossy=True,
                details=details,
            )
        return RuleCompressionResult(
            content=content,
            content_type=ContentType.HTML,
            modified=False,
            lossy=False,
            details=details,
        )


def _parse_html(content: str) -> BeautifulSoup:
    try:
        import lxml  # noqa: F401

        return BeautifulSoup(content, "lxml")
    except ImportError:
        return BeautifulSoup(content, "html.parser")


def _extract_title(soup: BeautifulSoup) -> str:
    meta = soup.find("meta", property="og:title")
    if isinstance(meta, Tag):
        value = meta.get("content")
        if isinstance(value, str) and value.strip():
            return _normalize_text(value)
    title = soup.find("title")
    return _normalize_text(title.get_text(" ", strip=True)) if isinstance(title, Tag) else ""


def _remove_noise(soup: BeautifulSoup) -> int:
    removed = 0
    for node in list(soup.find_all(_NOISE_TAGS)):
        node.decompose()
        removed += 1
    for node in list(soup.find_all(True)):
        if node.parent is None:
            continue
        classes = " ".join(str(value) for value in node.get("class", []))
        identifier = str(node.get("id", ""))
        style = str(node.get("style", ""))
        is_hidden = (
            node.has_attr("hidden")
            or str(node.get("aria-hidden", "")).lower() == "true"
            or bool(_HIDDEN_STYLE_RE.search(style))
        )
        if is_hidden or _NOISE_NAME_RE.search(classes) or _NOISE_NAME_RE.search(identifier):
            node.decompose()
            removed += 1
    return removed


def _find_main_content(soup: BeautifulSoup, min_chars: int) -> tuple[Tag | None, str]:
    for selector in _MAIN_SELECTORS:
        node = soup.select_one(selector)
        if isinstance(node, Tag) and _text_length(node) >= min_chars:
            return node, selector

    body = soup.find("body")
    if not isinstance(body, Tag):
        return None, "none"
    candidates = [
        node
        for node in body.find_all(("article", "main", "section", "div"))
        if isinstance(node, Tag) and _text_length(node) >= min_chars
    ]
    if not candidates:
        return (body, "body") if _text_length(body) >= min_chars else (None, "none")
    selected = max(candidates, key=_content_score)
    return selected, f"density:{selected.name}"


def _content_score(node: Tag) -> int:
    text_length = _text_length(node)
    link_length = sum(len(link.get_text(" ", strip=True)) for link in node.find_all("a"))
    structural_bonus = 30 * len(node.find_all(("p", "pre", "table", "li"), recursive=True))
    return text_length - 2 * link_length + structural_bonus


def _text_length(node: Tag) -> int:
    return len(_normalize_text(node.get_text(" ", strip=True)))


def _render_container(node: Tag) -> list[str]:
    blocks: list[str] = []
    for child in node.children:
        if isinstance(child, NavigableString):
            text = _normalize_text(str(child))
            if text:
                blocks.append(text)
        elif isinstance(child, Tag):
            blocks.extend(_render_tag(child))
    return [block for block in blocks if block.strip()]


def _render_tag(node: Tag) -> list[str]:
    name = node.name.lower()
    if name in {"h1", "h2", "h3", "h4", "h5", "h6"}:
        text = _inline_text(node)
        return [f"{'#' * int(name[1])} {text}"] if text else []
    if name == "p":
        text = _inline_text(node)
        return [text] if text else []
    if name in {"ul", "ol"}:
        ordered = name == "ol"
        items = []
        for index, item in enumerate(node.find_all("li", recursive=False), 1):
            text = _inline_text(item)
            if text:
                items.append(f"{index}. {text}" if ordered else f"- {text}")
        return ["\n".join(items)] if items else []
    if name == "blockquote":
        text = _inline_text(node)
        return ["\n".join(f"> {line}" for line in text.splitlines())] if text else []
    if name == "pre":
        code = node.get_text("\n", strip=False).strip("\n")
        return [f"```\n{code}\n```"] if code.strip() else []
    if name == "table":
        table = _render_table(node)
        return [table] if table else []
    if name == "hr":
        return ["---"]
    if name in {"br"}:
        return []
    if name in {"article", "main", "section", "div", "body"}:
        return _render_container(node)
    if any(isinstance(child, Tag) and child.name.lower() in _BLOCK_TAGS for child in node.children):
        return _render_container(node)
    text = _inline_text(node)
    return [text] if text else []


def _inline_text(node: Tag) -> str:
    parts: list[str] = []
    for child in node.children:
        if isinstance(child, NavigableString):
            parts.append(str(child))
            continue
        if not isinstance(child, Tag):
            continue
        name = child.name.lower()
        if name == "a":
            label = _normalize_text(child.get_text(" ", strip=True))
            href = child.get("href")
            parts.append(f"[{label}]({href})" if label and isinstance(href, str) and href.strip() else label)
        elif name == "code" and node.name.lower() != "pre":
            code = _normalize_text(child.get_text(" ", strip=True))
            parts.append(f"`{code}`" if code else "")
        elif name == "br":
            parts.append("\n")
        else:
            parts.append(_inline_text(child))
    return _normalize_inline("".join(parts))


def _render_table(table: Tag) -> str:
    rows: list[list[str]] = []
    for row in table.find_all("tr"):
        cells = [
            _normalize_text(cell.get_text(" ", strip=True)).replace("|", r"\|")
            for cell in row.find_all(("th", "td"), recursive=False)
        ]
        if cells:
            rows.append(cells)
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    normalized = [row + [""] * (width - len(row)) for row in rows]
    header = normalized[0]
    lines = [
        f"| {' | '.join(header)} |",
        f"| {' | '.join('---' for _ in range(width))} |",
    ]
    lines.extend(f"| {' | '.join(row)} |" for row in normalized[1:])
    return "\n".join(lines)


def _deduplicate_blocks(blocks: list[str]) -> tuple[list[str], int]:
    seen: set[str] = set()
    kept: list[str] = []
    duplicates = 0
    for block in blocks:
        key = _normalize_text(block).lower()
        if not key:
            continue
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        kept.append(block.strip())
    return kept, duplicates


def _contains_title(blocks: list[str], title: str) -> bool:
    normalized = _normalize_text(title).lower()
    return any(_normalize_text(block.lstrip("# ")).lower() == normalized for block in blocks)


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _normalize_inline(text: str) -> str:
    lines = [_normalize_text(line) for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def _plain_text(markdown: str) -> str:
    return re.sub(r"[#>*_`\[\]()|\-]", " ", markdown)


def _unchanged(content: str) -> RuleCompressionResult:
    return RuleCompressionResult(
        content=content,
        content_type=ContentType.HTML,
        modified=False,
    )
