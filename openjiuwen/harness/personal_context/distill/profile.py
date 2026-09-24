"""Account-level distilled profiles under PersonalContext Home im/profiles/."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from openjiuwen.harness.personal_context.distill.merge import merge_markdown
from openjiuwen.harness.personal_context.distill.store import (
    atomic_write_json,
    clear_distill_cursor,
)

PERSONA_FILENAME = "persona.md"
WORK_FILENAME = "work.md"
META_FILENAME = "meta.json"
CURRENT_FILENAME = "current.json"
OWNER_FILENAME = "owner.md"

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


def _now_ms() -> int:
    return int(time.time() * 1000)


def _version_files_complete(root: Path) -> bool:
    return (
        (root / PERSONA_FILENAME).is_file()
        and (root / WORK_FILENAME).is_file()
        and (root / META_FILENAME).is_file()
    )


def _read_current_pointer(home: str) -> dict[str, Any] | None:
    path = current_json_path(home)
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    job_id = str(raw.get("job_id") or "").strip()
    if not job_id:
        return None
    return raw


def _read_current_job_id(home: str) -> str | None:
    pointer = _read_current_pointer(home)
    if not pointer:
        return None
    job_id = str(pointer.get("job_id") or "").strip()
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


def activate_profile_version(
    home: str,
    job_id: str,
    *,
    source: str = "distill",
) -> dict[str, Any]:
    """Validate versions/<job_id>/ then atomically write current.json."""
    key = str(job_id or "").strip()
    if not key:
        raise ValueError("job_id is required")
    root = version_dir(home, key)
    if not _version_files_complete(root):
        raise ValueError(f"incomplete profile version: {key}")
    pointer = {
        "job_id": key,
        "published_at_ms": _now_ms(),
        "source": str(source or "distill"),
    }
    atomic_write_json(current_json_path(home), pointer)
    return pointer


def resolve_current_profile(home: str) -> dict[str, Any] | None:
    """Resolve the active profile via current.json; return None if missing/incomplete."""
    pointer = _read_current_pointer(home)
    if not pointer:
        return None
    job_id = str(pointer.get("job_id") or "").strip()
    if not job_id:
        return None
    root = version_dir(home, job_id)
    if not _version_files_complete(root):
        return None
    try:
        meta_raw = json.loads((root / META_FILENAME).read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return None
    if not isinstance(meta_raw, dict):
        return None
    return {
        "job_id": job_id,
        "persona_md": load_text(root / PERSONA_FILENAME),
        "work_md": load_text(root / WORK_FILENAME),
        "meta": meta_raw,
        "source": pointer.get("source"),
        "published_at_ms": pointer.get("published_at_ms"),
    }


def save_distilled_profile(
    home: str,
    *,
    persona_md: str,
    work_md: str,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Write a new version from manual edit and activate it; does not touch Distill cursor."""
    job_id = uuid.uuid4().hex
    now = _now_ms()
    payload = dict(meta or {})
    payload["job_id"] = job_id
    payload["source"] = "manual_edit"
    payload["updated_at_ms"] = now
    publish_distilled(
        home,
        job_id,
        persona_md=persona_md,
        work_md=work_md,
        meta=payload,
        merge_with_existing=False,
    )
    activate_profile_version(home, job_id, source="manual_edit")
    resolved = resolve_current_profile(home)
    if resolved is None:
        raise RuntimeError("manual profile save failed to resolve")
    return resolved


def delete_distilled_profile(home: str) -> dict[str, Any]:
    """Remove current.json; keep owner.md and versions; clear Distill cursor."""
    path = current_json_path(home)
    if path.is_file():
        path.unlink()
    clear_distill_cursor(home)
    return {"deleted": True, "cursor_ms": 0}
