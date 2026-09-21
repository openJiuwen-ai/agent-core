"""JSON job / cursor / schedule store for distill (account-level).

Lease and Distill cursor live here until ``im_context.db`` holds stage state.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from filelock import FileLock


def _distill_root(home: str) -> Path:
    return Path(home) / "im" / "distill"


def _cursor_path(home: str) -> Path:
    return _distill_root(home) / "cursor.json"


def _jobs_dir(home: str) -> Path:
    return _distill_root(home) / "jobs"


def _lease_path(home: str) -> Path:
    return _distill_root(home) / "lease.json"


def _lease_lock_path(home: str) -> Path:
    return _distill_root(home) / "lease.lock"


def _schedule_path(home: str) -> Path:
    return _distill_root(home) / "schedule.json"


def _now_ms() -> int:
    return int(time.time() * 1000)


def _lease_file_lock(home: str) -> FileLock:
    root = _distill_root(home)
    root.mkdir(parents=True, exist_ok=True)
    return FileLock(str(_lease_lock_path(home)), timeout=10)


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
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
    atomic_write_json(_jobs_dir(home) / f"{job_id}.json", payload)
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
    atomic_write_json(path, payload)

    if status == "success" and covered_through_ms is not None:
        atomic_write_json(
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


def clear_distill_cursor(home: str) -> None:
    path = _cursor_path(home)
    if path.is_file():
        path.unlink()


def get_job(home: str, job_id: str) -> dict[str, Any] | None:
    return _read_json(_jobs_dir(home) / f"{job_id}.json")


def get_last_attempt_at_ms(home: str) -> int:
    payload = _read_json(_schedule_path(home))
    if not payload:
        return 0
    return int(payload.get("last_attempt_at_ms") or 0)


def set_last_attempt_at_ms(home: str, last_attempt_at_ms: int) -> None:
    atomic_write_json(
        _schedule_path(home),
        {"last_attempt_at_ms": int(last_attempt_at_ms)},
    )


def get_distill_lease(home: str) -> dict[str, Any] | None:
    return _read_json(_lease_path(home))


def clear_distill_lease(home: str) -> None:
    path = _lease_path(home)
    if path.is_file():
        path.unlink()


def recover_expired_distill_lease(home: str, *, now_ms: int) -> bool:
    with _lease_file_lock(home):
        return _recover_expired_distill_lease_unlocked(home, now_ms=now_ms)


def _recover_expired_distill_lease_unlocked(home: str, *, now_ms: int) -> bool:
    payload = get_distill_lease(home)
    if not payload:
        return False
    expires = int(payload.get("lease_expires_at_ms") or 0)
    if expires <= int(now_ms):
        clear_distill_lease(home)
        return True
    return False


def try_claim_distill_lease(
    home: str,
    *,
    now_ms: int,
    lease_ms: int,
) -> str | None:
    """Claim Distill-stage lease for this home; at most one active holder."""
    with _lease_file_lock(home):
        _recover_expired_distill_lease_unlocked(home, now_ms=now_ms)
        existing = get_distill_lease(home)
        if existing is not None:
            return None
        token = uuid.uuid4().hex
        atomic_write_json(
            _lease_path(home),
            {
                "lease_token": token,
                "claimed_at_ms": int(now_ms),
                "lease_expires_at_ms": int(now_ms) + int(lease_ms),
            },
        )
        return token


def renew_distill_lease(
    home: str,
    lease_token: str,
    *,
    now_ms: int,
    lease_ms: int,
) -> bool:
    with _lease_file_lock(home):
        payload = get_distill_lease(home)
        if not payload or str(payload.get("lease_token") or "") != lease_token:
            return False
        atomic_write_json(
            _lease_path(home),
            {
                "lease_token": lease_token,
                "claimed_at_ms": int(payload.get("claimed_at_ms") or now_ms),
                "lease_expires_at_ms": int(now_ms) + int(lease_ms),
            },
        )
        return True


def complete_distill_lease(home: str, lease_token: str) -> bool:
    with _lease_file_lock(home):
        payload = get_distill_lease(home)
        if not payload or str(payload.get("lease_token") or "") != lease_token:
            return False
        clear_distill_lease(home)
        return True
