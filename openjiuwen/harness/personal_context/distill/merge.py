"""Merge previous distilled markdown with new candidates."""

from __future__ import annotations

import re

_HEADING_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)


def _sections(markdown: str) -> dict[str, str]:
    text = (markdown or "").strip()
    if not text:
        return {}
    matches = list(_HEADING_RE.finditer(text))
    if not matches:
        return {"": text}
    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        title = match.group(1).strip()
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        sections[title] = body
    return sections


def _render(sections: dict[str, str]) -> str:
    if not sections:
        return ""
    parts: list[str] = []
    for title, body in sections.items():
        if title:
            parts.append(f"## {title}")
        if body:
            parts.append(body)
        parts.append("")
    return "\n".join(parts).strip() + "\n"


def merge_markdown(existing: str, candidate: str) -> str:
    """
    Section-level merge:
    - same heading + same body → keep existing
    - same heading + different body → keep existing, append candidate under 待确认
    - new heading → append
    """
    old_sections = _sections(existing)
    new_sections = _sections(candidate)
    if not old_sections:
        return (candidate or "").strip() + ("\n" if candidate and not candidate.endswith("\n") else "")
    if not new_sections:
        return (existing or "").strip() + ("\n" if existing and not existing.endswith("\n") else "")

    merged = dict(old_sections)
    pending: list[str] = []
    for title, body in new_sections.items():
        if title not in merged:
            merged[title] = body
            continue
        if merged[title].strip() == body.strip():
            continue
        pending.append(f"### {title or '未命名'}\n{body}".strip())

    if pending:
        extra = "\n\n".join(pending)
        if "待确认" in merged:
            merged["待确认"] = f"{merged['待确认'].strip()}\n\n{extra}".strip()
        else:
            merged["待确认"] = extra
    return _render(merged)
