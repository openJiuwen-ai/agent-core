from __future__ import annotations


PAIRS = {"(": ")", "[": "]", "{": "}"}


def is_balanced(text: str) -> bool:
    stack: list[str] = []
    for char in text:
        if char in PAIRS:
            stack.append(char)
        elif char in PAIRS.values():
            if not stack:
                return False
            if PAIRS[stack[-1]] != char:
                return False
    return not stack
