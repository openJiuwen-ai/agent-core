"""Keep navigation in source documents separate from generated Context links."""

from __future__ import annotations

import html
import re
from collections.abc import Callable
from urllib.parse import urlsplit

_DEFINITION = re.compile(
    r"^ {0,3}\[([^\]\n]+)\]:[ \t]*(?:\n {0,3})?(<[^<>\n]*>|[^\s]+)"
    r'(?:[ \t]+(?:"[^"\n]*"|\'[^\'\n]*\'|\([^\n]*\)))?[ \t]*$',
    re.MULTILINE,
)
_HTML_TAG = re.compile(r'<!--.*?-->|</?[A-Za-z](?:[^<>"\']|"[^"]*"|\'[^\']*\')*>', re.DOTALL)
_HTML_TARGET = re.compile(
    r'\b(?:href|src|poster|action|data|xlink:href|srcset)\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|([^\s>]+))',
    re.IGNORECASE,
)


def _web_target(target: str) -> bool:
    target = html.unescape(target.strip().removeprefix("<").removesuffix(">"))
    target = re.sub(r"\\([!\"#$%&'()*+,\-./:;<=>?@\[\]\\^_`{|}~])", r"\1", target)
    try:
        parsed = urlsplit(target)
        return parsed.scheme.casefold() in {"http", "https"} and bool(parsed.hostname)
    except ValueError:
        return False


def _literal(value: str) -> str:
    escaped = html.escape(value, quote=False)
    for character in "[]`\\*_":
        escaped = escaped.replace(character, f"&#{ord(character)};")
    return escaped


def _label_key(label: str) -> str:
    return " ".join(label.split()).casefold()


def _link_text(label: str, target: str) -> str:
    return f"{_literal(label)}（原文链接：{_literal(target)}）"


def _substring(text: str, start: int, stop: int | None) -> str:
    return text[start:stop]


def _code_end(text: str, start: int) -> int:
    run = re.match(r"`+", text[start:])
    if run is None:
        return start
    length = len(run.group())
    closing = re.search(rf"(?<!`)`{{{length}}}(?!`)", _substring(text, start + length, None))
    return start + length + closing.end() if closing else start


def _balanced_end(text: str, start: int, opening: str, closing: str) -> int:
    depth = 1
    index = start + 1
    while index < len(text):
        character = text[index]
        if character == "\\":
            index += 2
            continue
        if opening == "(":
            delimiter: str | None = None
            if character == "<":
                delimiter = ">"
            elif character in "\"'" and text[index - 1].isspace():
                delimiter = character
            if delimiter is not None:
                end = text.find(delimiter, index + 1)
                if end != -1:
                    index = end + 1
                    continue
        if character == opening:
            depth += 1
        elif character == closing:
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    return start


def _destination(raw: str) -> str:
    value = raw.strip()
    if value.startswith("<") and ">" in value:
        return _substring(value, 1, value.index(">"))
    # An optional title is separated from the destination by whitespace.
    return re.split(r'\s+(?=["\'])', value, maxsplit=1)[0].strip()


def _rewrite_link(
    text: str, start: int, definitions: dict[str, str], render_link: Callable[[str, str], str] | None
) -> tuple[str, int]:
    bracket = start + 1 if text.startswith("![", start) else start
    label_end = _balanced_end(text, bracket, "[", "]")
    if label_end == bracket:
        return text[start], start + 1
    label = _substring(text, bracket + 1, label_end - 1)
    end = label_end
    target = definitions.get(_label_key(label))
    if text.startswith("(", end):
        destination_end = _balanced_end(text, end, "(", ")")
        if destination_end == end:
            return text[start], start + 1
        target = _destination(_substring(text, end + 1, destination_end - 1))
        end = destination_end
    elif text.startswith("[", end):
        reference_end = _balanced_end(text, end, "[", "]")
        if reference_end != end:
            reference = _substring(text, end + 1, reference_end - 1) or label
            target = definitions.get(_label_key(reference))
            end = reference_end
    if target is None:
        return text[start:end], end
    if render_link is not None:
        return render_link(label, target), end
    if _web_target(target):
        # A web link can contain an image whose destination is still local.
        rewritten_label = _rewrite_inline(label, definitions, render_link)
        prefix = _substring(text, start, bracket + 1)
        suffix = _substring(text, label_end - 1, end)
        return prefix + rewritten_label + suffix, end
    return _link_text(label, target), end


def _rewrite_inline(
    text: str, definitions: dict[str, str], render_link: Callable[[str, str], str] | None = None
) -> str:
    result: list[str] = []
    index = 0
    while index < len(text):
        if text[index] == "\\":
            result.append(_substring(text, index, index + 2))
            index += 2
            continue
        if text[index] == "`":
            end = _code_end(text, index)
            if end != index:
                result.append(text[index:end])
                index = end
                continue
        if text[index] == "[" or text.startswith("![", index):
            replacement, index = _rewrite_link(text, index, definitions, render_link)
            result.append(replacement)
            continue
        if text[index] == "<":
            autolink = re.match(r"<([^<>\s]+)>", text[index:])
            if autolink is not None and re.match(r"[A-Za-z][A-Za-z0-9+.-]*:", autolink.group(1)):
                target = autolink.group(1)
                result.append(
                    render_link("", target)
                    if render_link
                    else (autolink.group() if _web_target(target) else _link_text("", target))
                )
                index += autolink.end()
                continue
            tag = _HTML_TAG.match(text, index)
            if tag is not None:
                raw = tag.group()
                targets = [
                    next(value for value in match.groups() if value is not None) for match in _HTML_TARGET.finditer(raw)
                ]
                result.append(
                    " ".join(render_link(raw, target) for target in targets)
                    if render_link and targets
                    else (_link_text("", raw) if any(not _web_target(value) for value in targets) else raw)
                )
                index = tag.end()
                continue
            if autolink is not None:
                target = autolink.group(1)
                result.append(
                    render_link("", target)
                    if render_link
                    else (autolink.group() if _web_target(target) else _link_text("", target))
                )
                index += autolink.end()
                continue
        result.append(text[index])
        index += 1
    return "".join(result)


def _source_segments(markdown: str) -> list[tuple[bool, str]]:
    """Separate source code blocks before collecting link definitions."""
    segments: list[tuple[bool, str]] = []
    prose: list[str] = []
    fence: str | None = None
    previous_blank = True
    indented_code = False
    in_list = False
    for line in markdown.splitlines(keepends=True):
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if fence is None and re.match(r"^ {0,3}(?:[-+*]|\d+[.)])\s", line):
            in_list = True
        elif line.strip() and not line[0].isspace() and fence is None:
            in_list = False
        indented_code = (
            not in_list and (previous_blank or indented_code) and (line.startswith(("    ", "\t")) or not line.strip())
        )
        protected = fence is not None or marker is not None or indented_code
        if protected:
            if prose:
                segments.append((False, "".join(prose)))
                prose = []
            segments.append((True, line))
            if fence is not None:
                if re.fullmatch(rf" {{0,3}}{re.escape(fence[0])}{{{len(fence)},}}[ \t\r\n]*", line):
                    fence = None
            elif marker is not None:
                fence = marker.group(1)
        else:
            prose.append(line)
        previous_blank = not line.strip()
    if prose:
        segments.append((False, "".join(prose)))
    return segments


def markdown_reference_text(markdown: str) -> str:
    """Return prose for reference validation, excluding literal code examples."""
    result: list[str] = []
    for protected, text in _source_segments(markdown):
        if protected:
            result.append("\n")
            continue
        index = 0
        while index < len(text):
            if text[index] == "`":
                end = _code_end(text, index)
                if end != index:
                    result.append(" ")
                    index = end
                    continue
            result.append(text[index])
            index += 1
    return "".join(result)


def rewrite_markdown_prose(markdown: str, transform: Callable[[str], str]) -> str:
    """Rewrite active prose while retaining literal code examples byte-for-byte."""
    result: list[str] = []
    for protected, text in _source_segments(markdown):
        if protected:
            result.append(text)
            continue
        start = index = 0
        while index < len(text):
            end = _code_end(text, index) if text[index] == "`" else index
            if end != index:
                result.extend((transform(text[start:index]), text[index:end]))
                start = index = end
            else:
                index += 1
        result.append(transform(text[start:]))
    return "".join(result)


def normalize_source_links(markdown: str, *, render_link: Callable[[str, str], str] | None = None) -> str:
    """Render non-web source destinations as text without following any target.

    Only call this on newly ingested source Markdown, never on generated Context
    or registered source references. The publication validator remains unchanged.
    """
    segments = _source_segments(markdown)
    definitions: dict[str, str] = {}
    for protected, text in segments:
        if not protected:
            for match in _DEFINITION.finditer(text):
                definitions.setdefault(_label_key(match.group(1)), _destination(match.group(2)))

    def replace_definition(match: re.Match[str]) -> str:
        target = _destination(match.group(2))
        return match.group() if render_link is None and _web_target(target) else _link_text(match.group(1), target)

    return "".join(
        text if protected else _rewrite_inline(_DEFINITION.sub(replace_definition, text), definitions, render_link)
        for protected, text in segments
    )
