from __future__ import annotations

import uuid


def new_run_id(prefix: str = "run") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"
