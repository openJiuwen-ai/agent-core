"""Domain-gated revision text for one scored deliverable.

The suggestion reads the saved score, it does not score again. A personal-assistant
result stays with attribution. Only a result whose single selected guide is game
receives this text.
"""

from __future__ import annotations

import json
from pathlib import Path

DEFAULT_DOMAINS = ["game"]

PROMPT = """Write how the next version of this game is played. The recorded score says what was seen and what is still unknown.
A met claim is already in the game; keep it. An unmet or partial claim is a play problem to solve in the next version.
An unknown claim is not proof the feature is missing. Do not turn unknowns into a test checklist.
The player must be able to see one new choice and its consequence in the opening they already have.
Do not ask only for larger text, captions, or API notes.
Return one JSON object with four nonempty strings:
{"play": "what the player does in the next version",
 "keep": "what stays",
 "how": "how to put that play into the current game",
 "compare": "which scenes to compare with the previous version"}.
"""


def suggestion_policy(config, guides):
    """Enable suggestion only when the single selected guide is an allowed domain."""
    setting = (config or {}).get("suggestion") or {}
    if not isinstance(setting, dict):
        raise TypeError("suggestion must be an object")
    unknown = set(setting) - {"enabled", "domains"}
    if unknown:
        raise ValueError("unknown suggestion fields: " + ", ".join(sorted(unknown)))
    enabled = setting.get("enabled", True)
    domains = setting.get("domains", DEFAULT_DOMAINS)
    if type(enabled) is not bool:
        raise TypeError("suggestion.enabled must be a boolean")
    if not isinstance(domains, list) or any(not isinstance(name, str) or not name.strip() for name in domains):
        raise TypeError("suggestion.domains must be a list of nonempty domain identifiers")
    names = []
    for item in guides or []:
        name = item[0] if isinstance(item, (list, tuple)) and item else item
        if isinstance(name, str) and name not in names:
            names.append(name)
    domain = names[0] if len(names) == 1 else None
    if not enabled:
        reason = "disabled"
    elif domain is None:
        reason = "domain_unknown"
    elif domain not in domains:
        reason = "domain_not_enabled"
    else:
        reason = None
    return {"enabled": enabled, "domain": domain, "domains": list(domains),
            "status": "skipped" if reason else "enabled", "reason": reason}


def write_evidence(directory, result) -> Path:
    """Persist the score the suggestion will read."""
    folder = Path(directory)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "result.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def read_evidence(directory) -> dict:
    record = json.loads((Path(directory) / "result.json").read_text(encoding="utf-8"))
    if not isinstance(record, dict):
        raise ValueError("reward evidence result must be an object")
    return record


def _claims(result):
    rows = []
    for item in result.get("sub_category_items") or []:
        if not isinstance(item, dict):
            continue
        rows.append({
            "rubric": str(item.get("rubric") or ""),
            "status": item.get("status"),
            "comment": str(item.get("comment") or ""),
            "type": item.get("type"),
        })
    return rows


def render(advice, language="zh") -> str:
    """The next-version play text the existing diagnosis card hands to the worker."""
    zh = str(language).lower().startswith("zh")
    fields = (
        ("play", "下一版怎么玩" if zh else "How the next version is played"),
        ("keep", "保留" if zh else "Keep"),
        ("how", "做法" if zh else "How to build it"),
        ("compare", "和上一版比" if zh else "Compare with the previous version"),
    )
    parts = []
    for key, heading in fields:
        text = advice.get(key)
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"suggestion {key} must be nonempty text")
        parts.append("## " + heading + "\n" + text.strip())
    return "\n\n".join(parts)


async def suggest(evidence_dir, model, language="zh") -> str:
    """Read result.json and ask the scoring model for the revision text."""
    result = read_evidence(evidence_dir)
    payload = {
        "task": (result.get("meta") or {}).get("task") or "",
        "summary": (result.get("total") or {}).get("summary") or "",
        "claims": _claims(result),
    }
    advice = await model.json(PROMPT, json.dumps(payload, ensure_ascii=False))
    if not isinstance(advice, dict):
        raise ValueError("suggestion must return play, keep, how and compare")
    return render(advice, language)
