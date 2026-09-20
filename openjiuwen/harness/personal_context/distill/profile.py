"""Account-level distilled profiles under PersonalContext Home im/profiles/."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from openjiuwen.harness.personal_context.distill.merge import merge_markdown

PERSONA_FILENAME = "persona.md"
WORK_FILENAME = "work.md"
META_FILENAME = "meta.json"
CURRENT_FILENAME = "current.json"

_MAX_PERSONA_CHARS = 12000
_MAX_WORK_CHARS = 12000


def profiles_root(home: str) -> Path:
    return Path(home) / "im" / "profiles"


def version_dir(home: str, job_id: str) -> Path:
    return profiles_root(home) / "versions" / job_id


def current_json_path(home: str) -> Path:
    return profiles_root(home) / CURRENT_FILENAME


def load_text(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def _clip(text: str, limit: int) -> str:
    value = (text or "").strip()
    if len(value) <= limit:
        return value + ("\n" if value and not value.endswith("\n") else "")
    clipped = value[: max(0, limit - 20)].rstrip()
    return clipped + "\n\n…（已截断）\n"


def _read_current_job_id(home: str) -> str | None:
    path = current_json_path(home)
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    job_id = str(raw.get("job_id") or "").strip()
    return job_id or None


def load_current_baseline(home: str) -> tuple[str, str]:
    """Load persona/work markdown for the version pointed by current.json (read-only)."""
    job_id = _read_current_job_id(home)
    if not job_id:
        return "", ""
    root = version_dir(home, job_id)
    if not root.is_dir():
        return "", ""
    return load_text(root / PERSONA_FILENAME), load_text(root / WORK_FILENAME)


def publish_distilled(
    home: str,
    job_id: str,
    *,
    persona_md: str,
    work_md: str,
    meta: dict[str, Any],
    merge_with_existing: bool = True,
) -> Path:
    """Write one version under im/profiles/versions/<job_id>/. Does not touch current.json."""
    key = str(job_id or "").strip()
    if not key:
        raise ValueError("job_id is required")
    root = version_dir(home, key)
    root.mkdir(parents=True, exist_ok=True)

    if merge_with_existing:
        existing_persona, existing_work = load_current_baseline(home)
        persona_md = merge_markdown(existing_persona, persona_md)
        work_md = merge_markdown(existing_work, work_md)

    persona_md = _clip(persona_md, _MAX_PERSONA_CHARS)
    work_md = _clip(work_md, _MAX_WORK_CHARS)

    (root / PERSONA_FILENAME).write_text(persona_md, encoding="utf-8")
    (root / WORK_FILENAME).write_text(work_md, encoding="utf-8")
    payload = dict(meta)
    payload.setdefault("job_id", key)
    (root / META_FILENAME).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return root
