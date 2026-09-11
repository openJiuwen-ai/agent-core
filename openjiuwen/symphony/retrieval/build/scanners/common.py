from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any


SKILL_FILENAMES = ("SKILL.md", "skill.md", "Skill.md")


def find_skill_file(skill_dir: Path) -> Path | None:
    return next(
        (skill_dir / name for name in SKILL_FILENAMES if (skill_dir / name).is_file()),
        None,
    )


def parse_frontmatter(content: str, *, fallback_to_simple: bool = True) -> tuple[dict[str, Any], str]:
    if not content.startswith("---"):
        return {}, content

    end_match = re.search(r"\n---\s*(?:\n|$)", content[3:])
    if not end_match:
        return {}, content

    frontmatter_end = end_match.start() + 3
    body_start = end_match.end() + 3
    frontmatter_str = content[3:frontmatter_end]
    body = content[body_start:]
    parsed = _safe_load_frontmatter(frontmatter_str.strip(), fallback_to_simple=fallback_to_simple)
    return parsed, body


def read_skill_file(
    path: Path,
    *,
    errors: str = "strict",
    fallback_to_simple: bool = True,
) -> tuple[dict[str, Any], str, str]:
    raw = path.read_bytes()
    metadata, body = parse_frontmatter(
        raw.decode("utf-8", errors=errors),
        fallback_to_simple=fallback_to_simple,
    )
    return metadata, body, hashlib.sha256(raw).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def first_paragraph(body: str, *, limit: int | None = None) -> str:
    for block in re.split(r"\n\s*\n", str(body or "").strip()):
        cleaned = " ".join(line.strip() for line in block.splitlines()).strip()
        if cleaned:
            return cleaned if limit is None else cleaned[:limit]
    return ""


def clean_first_paragraph(body: str, *, limit: int = 500) -> str:
    text = str(body or "").strip()
    if not text:
        return ""
    first_para = text.split("\n\n")[0]
    first_para = re.sub(r"^#+\s*", "", first_para)
    first_para = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", first_para)
    return first_para[:limit].strip()


def read_text_if_exists(path: Path) -> str:
    if not path.exists() or not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def _safe_load_frontmatter(text: str, *, fallback_to_simple: bool = True) -> dict[str, Any]:
    fallback = _parse_simple_frontmatter if fallback_to_simple else lambda _: {}
    try:
        import yaml
    except ModuleNotFoundError:
        return fallback(text)

    try:
        payload = yaml.safe_load(text) or {}
    except Exception:
        return fallback(text)
    if not isinstance(payload, dict):
        return fallback(text)
    return payload


def _parse_simple_frontmatter(text: str) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    lines = text.splitlines()
    index = 0
    last_key: str | None = None

    while index < len(lines):
        raw_line = lines[index]
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            index += 1
            continue
        if raw_line.startswith((" ", "\t")):
            index += 1
            continue
        if ":" not in raw_line:
            if last_key is not None and isinstance(payload.get(last_key), str):
                payload[last_key] = f"{payload[last_key]}\n{stripped}".strip()
            index += 1
            continue

        key, value = raw_line.split(":", 1)
        clean_key = key.strip()
        clean_value = value.strip()

        if clean_value in {"|", "|-", "|+", ">", ">-", ">+"}:
            block_lines: list[str] = []
            index += 1
            while index < len(lines):
                block_line = lines[index]
                if not block_line.strip():
                    block_lines.append("")
                    index += 1
                    continue
                if not block_line.startswith((" ", "\t")):
                    break
                block_lines.append(block_line.lstrip(" \t"))
                index += 1
            payload[clean_key] = "\n".join(block_lines).strip()
            last_key = clean_key
            continue

        payload[clean_key] = _parse_simple_scalar(clean_value)
        last_key = clean_key
        index += 1

    return payload


def _parse_simple_scalar(value: str) -> str:
    text = str(value or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
        return text[1:-1]
    return text
