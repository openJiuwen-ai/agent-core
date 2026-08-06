from __future__ import annotations


def text_tokens(*values: str) -> set[str]:
    tokens: set[str] = set()
    for value in values:
        for token in str(value or "").replace("-", " ").replace("_", " ").replace(".", " ").split():
            cleaned = "".join(ch for ch in token.strip().lower() if ch.isalnum())
            if cleaned:
                tokens.add(cleaned)
    return tokens


def slug_term(value: str, fallback: str = "node") -> str:
    raw = "".join(ch.lower() if ch.isalnum() else "-" for ch in str(value or ""))
    compact = "-".join(part for part in raw.split("-") if part)
    return compact or fallback


def join_cid(parent: str, child: str) -> str:
    return f"{parent}.{child}" if parent else child


def parent_cid(cid: str) -> str:
    return cid.rsplit(".", 1)[0] if "." in cid else ""


def unique_child_cid(*, parent: str, segment: str, used: set[str]) -> str:
    candidate = join_cid(parent, segment)
    if candidate not in used:
        return candidate
    index = 2
    while True:
        candidate = join_cid(parent, f"{segment}-{index}")
        if candidate not in used:
            return candidate
        index += 1
