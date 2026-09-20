"""JSON job / cursor store for distill (account-level; interim until OJ-03 db)."""

from __future__ import annotations

import json
import os
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any


def _distill_root(home: str) -> Path:
    return Path(home) / "im" / "distill"


def _cursor_path(home: str) -> Path:
    return _distill_root(home) / "cursor.json"


def _jobs_dir(home: str) -> Path:
    return _distill_root(home) / "jobs"


def _now_ms() -> int:
    return int(time.time() * 1000)


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as handle:
        handle.write(data)
        temporary = handle.name
    os.replace(temporary, path)


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None
    return raw if isinstance(raw, dict) else None


def begin_job(
    home: str,
    *,
    window_start_ms: int,
    window_end_ms: int,
) -> str:
    job_id = uuid.uuid4().hex
    now = _now_ms()
    payload = {
        "id": job_id,
        "status": "running",
        "window_start_ms": int(window_start_ms),
        "window_end_ms": int(window_end_ms),
        "message_count": None,
        "sampled": False,
        "error": None,
        "created_at_ms": now,
        "finished_at_ms": None,
    }
    _atomic_write_json(_jobs_dir(home) / f"{job_id}.json", payload)
    return job_id


def finish_job(
    home: str,
    job_id: str,
    *,
    status: str,
    message_count: int | None = None,
    sampled: bool = False,
    error: str | None = None,
    covered_through_ms: int | None = None,
) -> None:
    if status not in {"success", "failed"}:
        raise ValueError(f"invalid distill job status: {status}")
    path = _jobs_dir(home) / f"{job_id}.json"
    payload = _read_json(path) or {"id": job_id}
    now = _now_ms()
    payload.update(
        {
            "status": status,
            "message_count": message_count,
            "sampled": bool(sampled),
            "error": error,
            "finished_at_ms": now,
        }
    )
    _atomic_write_json(path, payload)

    if status == "success" and covered_through_ms is not None:
        _atomic_write_json(
            _cursor_path(home),
            {
                "covered_through_ms": int(covered_through_ms),
                "last_success_job_id": job_id,
                "updated_at_ms": now,
            },
        )


def get_cursor_ms(home: str) -> int:
    payload = _read_json(_cursor_path(home))
    if not payload:
        return 0
    return int(payload.get("covered_through_ms") or 0)


def get_job(home: str, job_id: str) -> dict[str, Any] | None:
    return _read_json(_jobs_dir(home) / f"{job_id}.json")
