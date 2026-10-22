"""Key vocabulary and chord syntax; no native calls or semantic permission grants."""

MODIFIERS = frozenset({"cmd", "ctrl", "alt", "shift"})
SUPPORTED_KEYS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789`-=[]\\;',./") | frozenset(
    {
        "return",
        "tab",
        "escape",
        "space",
        "backspace",
        "delete",
        "left",
        "right",
        "up",
        "down",
        "home",
        "end",
        "pageup",
        "pagedown",
    }
    | {f"f{number}" for number in range(1, 21)}
)
KEY_ALIASES = {
    "command": "cmd",
    "meta": "cmd",
    "⌘": "cmd",
    "control": "ctrl",
    "option": "alt",
    "enter": "return",
    "esc": "escape",
    "arrowleft": "left",
    "arrowright": "right",
    "arrowup": "up",
    "arrowdown": "down",
}
SHORTCUT_GUIDANCE = (
    'Use a keys array, e.g. ["cmd","a"] or ["cmd","s"]. '
    "Use zero to four distinct modifiers (cmd, ctrl, alt, shift), then one key: "
    "a-z, 0-9, US unshifted punctuation, return, tab, escape, space, backspace, delete, "
    "left/right/up/down, home/end, pageup/pagedown or f1-f20. "
    "Meta/Command means cmd; Control means ctrl; Option means alt. "
    "Key names are US ANSI physical positions; use type for Unicode text. "
    "delete is forward delete; backspace is backward delete."
)


def normalize_keys(value: object) -> object:
    """Normalize only explicit spellings; leave types/order/duplicates for strict validation."""
    if not isinstance(value, list):
        return value
    return [KEY_ALIASES.get(key.lower(), key.lower()) if isinstance(key, str) else key for key in value]


def validate_keys(keys: list[str]) -> None:
    """Reject unsupported, duplicate, misplaced or modifier-only keys before input."""
    if not keys or len(keys) > 5 or any(not isinstance(key, str) for key in keys):
        raise ValueError("unsupported shortcut; " + SHORTCUT_GUIDANCE)
    if keys[-1] not in SUPPORTED_KEYS or any(key not in MODIFIERS for key in keys[:-1]):
        raise ValueError("unsupported shortcut; " + SHORTCUT_GUIDANCE)
    if len(set(keys[:-1])) != len(keys) - 1:
        raise ValueError("duplicate shortcut modifier; " + SHORTCUT_GUIDANCE)


def validate_modifiers(modifiers: list[str]) -> None:
    """Validate explicit mouse modifiers without translating their OS meaning."""
    if len(modifiers) > 4 or any(key not in MODIFIERS for key in modifiers):
        raise ValueError("modifiers must contain only cmd, ctrl, alt or shift")
    if len(set(modifiers)) != len(modifiers):
        raise ValueError("duplicate mouse modifier")
