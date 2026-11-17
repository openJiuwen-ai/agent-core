"""Domain-gated revision text for one scored deliverable.

The suggestion reads the saved score, it does not score again. A personal-assistant
result stays with attribution. Only a result whose single selected guide is game
receives this text.
"""

from __future__ import annotations

import json
from pathlib import Path

DEFAULT_DOMAINS = ["game"]

PROMPT = """Write the next revision of this delivered work from the recorded score only.
Do not invent a new scoring rubric, a new requirement, or a check that is not in the record.
Unknown and unscored claims stay unknown. A met claim is not a defect.
Return one JSON object: {"preserve": ["..."], "improve": ["..."], "explore": ["..."]}.
Each list may be empty. Each string is one concrete change or strength a person can verify.
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
    """Markdown the existing diagnosis card can place in the composer."""
    zh = str(language).lower().startswith("zh")
    groups = (
        ("保留" if zh else "Preserve", advice.get("preserve") or []),
        ("改善" if zh else "Improve", advice.get("improve") or []),
        ("探索" if zh else "Explore", advice.get("explore") or []),
    )
    parts = []
    for heading, items in groups:
        lines = [str(item).strip() for item in items if str(item).strip()]
        if lines:
            parts.append(heading + "\n" + "\n".join(f"- {line}" for line in lines))
    if not parts:
        raise ValueError("suggestion rendered no text")
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
    if not isinstance(advice, dict) or any(
            key not in advice or not isinstance(advice[key], list)
            for key in ("preserve", "improve", "explore")):
        raise ValueError("suggestion must return preserve, improve and explore lists")
    return render(advice, language)
