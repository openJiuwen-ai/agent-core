"""bigram tokenizer for FTS5.

- CJK: single char + adjacent 2-char (sliding bigram) so 2-char queries match
- ASCII: keep word as-is (lowercased); do NOT bigram (English has word boundaries)
- Separators (whitespace / punctuation) flush both buffers (no cross-punct bigrams)
- Single-pass scan so token order matches source (FTS5 NEAR/phrase need positions)
- Write side: dedup + join with spaces -> stored in FTS ``seg`` column
- Query side: two tiers (strict = all tokens AND; relaxed = drop CJK bigrams)
"""

from __future__ import annotations

import re

# CJK Unified Ideographs + Extension A + Compatibility + Hiragana/Katakana + Hangul
CJK_RE = re.compile(r"[぀-ヿ㐀-䶿一-鿿豈-﫿가-힯]")
# ASCII word chars: keep words like "k8s-prod" intact (hyphen preserved)
ASCII_WORD_CHAR_RE = re.compile(r"[A-Za-z0-9_+#.-]")

# FTS5 special chars that must be escaped inside token literals
FTS5_SPECIAL_CHARS = re.compile(r'["\'\-*:+()]')


def _is_cjk(char: str) -> bool:
    return bool(CJK_RE.match(char))


def tokenize(text: str) -> list[str]:
    """Split ``text`` into indexable tokens (same function for write + query).

    Single-pass scan; switches flush the opposite buffer so order is preserved.
    """
    if not text:
        return []
    tokens: list[str] = []
    cjk_run: list[str] = []
    word_buf: list[str] = []

    def flush_cjk() -> None:
        nonlocal cjk_run
        for i, char in enumerate(cjk_run):
            tokens.append(char)
            if i + 1 < len(cjk_run):
                tokens.append(char + cjk_run[i + 1])
        cjk_run = []

    def flush_word() -> None:
        nonlocal word_buf
        if word_buf:
            tokens.append("".join(word_buf).lower())
            word_buf = []

    for char in text:
        if _is_cjk(char):
            flush_word()
            cjk_run.append(char)
            continue
        if ASCII_WORD_CHAR_RE.match(char):
            flush_cjk()
            word_buf.append(char)
            continue
        flush_word()
        flush_cjk()
    flush_word()
    flush_cjk()
    return tokens


def to_index_segment(text: str) -> str:
    """Write side: text -> string stored in FTS ``seg`` column (dedup, joined)."""
    return " ".join(dict.fromkeys(tokenize(text)))


def to_query_tokens(query: str) -> list[str]:
    """Query side: user input -> deduped token list."""
    return list(dict.fromkeys(tokenize(query)))


def to_query_token_tiers(query: str) -> list[list[str]]:
    """Query side: returns 1 or 2 tiers, strict -> relaxed.

    - strict = all tokens (CJK bigrams included)
    - relaxed = drop CJK bigrams (keep single chars + ASCII words)
    - returns [strict] only when strict == relaxed (e.g. pure ASCII query)
    - returns [] when query has no tokens
    """
    strict = list(dict.fromkeys(tokenize(query)))
    if not strict:
        return []
    relaxed = [t for t in strict if not (len(t) == 2 and _is_cjk(t[0]) and _is_cjk(t[1]))]
    if not relaxed or len(relaxed) == len(strict):
        return [strict]
    return [strict, relaxed]


def escape_fts_token(token: str) -> str:
    """Escape a single token as a quoted FTS5 phrase (handles special chars)."""
    escaped = token.replace('"', '""')
    return f'"{escaped}"'


def build_match_expr(tiers: list[list[str]]) -> str:
    """Build MATCH expression: strict-tier tokens AND-combined (quoted)."""
    if not tiers:
        return ""
    return " AND ".join(escape_fts_token(t) for t in tiers[0])


__all__ = [
    "tokenize",
    "to_index_segment",
    "to_query_tokens",
    "to_query_token_tiers",
    "escape_fts_token",
    "build_match_expr",
]
