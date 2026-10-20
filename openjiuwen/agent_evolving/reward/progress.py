"""A small JSON file the JiuwenSwarm web UI polls to draw a progress card; a no-op outside JiuwenSwarm."""

import contextvars
import json
import time
from pathlib import Path

from .session import latest_session_history


class Progress:
    """A small JSON file the JiuwenSwarm web UI polls to draw a progress card. No file, no-op.

    stages: criteria -> evidence (browser) / judging (text) -> done | failed. A caller that runs more after
    scoring (JiuwenSwarm runs attribution) sets stage "attribution" before finish(); the web card knows it.
    """
    SPAN = {"criteria": (0, 10), "evidence": (10, 80), "judging": (10, 80), "attribution": (80, 95)}

    def __init__(self, path=None, mode=""):
        self.path = Path(path) if path else None
        self.state = {"execution": "running", "mode": mode, "stage": "criteria", "percent": 0, "done": 0,
                      "total": 0, "detail": "", "started_at": time.time(), "updated_at": time.time()}
        self.write()

    @classmethod
    def for_session(cls, mode):
        """<session>/reward/<start ms>.json in the latest JiuwenSwarm session, when there is one."""
        try:
            folder = latest_session_history().parent / "reward"
            folder.mkdir(exist_ok=True)
            return cls(folder / f"{int(time.time() * 1000)}.json", mode)
        except OSError:
            return cls(None, mode)

    def write(self):
        if not self.path:
            return
        self.state["updated_at"] = time.time()
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.state, ensure_ascii=False), encoding="utf-8")
        temporary.replace(self.path)

    def stage(self, stage, total=0, detail=""):
        low, _ = self.SPAN.get(stage, (100, 100))
        self.state.update(stage=stage, total=total, done=0, detail=detail, percent=max(self.state["percent"], low))
        self.write()

    def tick(self, detail=""):
        low, high = self.SPAN.get(self.state["stage"], (100, 100))
        self.state["done"] += 1
        share = self.state["done"] / self.state["total"] if self.state["total"] else 1
        self.state.update(detail=detail, percent=round(low + (high - low) * share))
        self.write()

    async def counted(self, awaitable, detail):
        result = await awaitable
        self.tick(detail)
        return result

    def finish(self, total):
        self.state.update(execution="finished", stage="done", percent=100, detail="", score=total)
        self.write()

    def fail(self, error):
        self.state.update(execution="failed", stage="failed", error=str(error)[:300])
        self.write()


# The run's progress file lives in a context variable, so concurrent runs in one server process (one per
# session) each write their own file.
_current = contextvars.ContextVar("reward_progress", default=Progress())


def current():
    """The progress file of the run in this context (a no-op one until use() is called)."""
    return _current.get()


def use(progress):
    """Make `progress` the current run's progress file in this context and the tasks it starts."""
    _current.set(progress)
    return progress
