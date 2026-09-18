from __future__ import annotations

import re
from pathlib import Path

from video_memory.schemas import FrameRecord

FRAME_RE = re.compile(r"^(?P<global>\d+)_(?P<event>\d+)_(?P<local>\d+)\.(?P<ext>png|txt)$")


class FrameIndex:
    def __init__(self, frames: list[FrameRecord]) -> None:
        self.frames = sorted(frames, key=lambda frame: frame.time_id)
        self.by_key = {frame.frame_key: frame for frame in self.frames}
        self.by_id = {frame.frame_id: frame for frame in self.frames}

    def __len__(self) -> int:
        return len(self.frames)

    def get(self, frame_key: str) -> FrameRecord | None:
        return self.by_key.get(frame_key)

    def require(self, frame_key: str) -> FrameRecord:
        frame = self.get(frame_key)
        if frame is None:
            raise KeyError(f"Unknown frame key: {frame_key}")
        return frame

    def subset(self, frame_keys: list[str]) -> list[FrameRecord]:
        return [self.require(key) for key in frame_keys if key in self.by_key]

    def time_for_key(self, frame_key: str | None) -> int | None:
        if frame_key is None:
            return None
        frame = self.get(frame_key)
        return frame.time_id if frame else None

    def min_time_id(self) -> int:
        return self.frames[0].time_id if self.frames else 0

    def max_time_id(self) -> int:
        return self.frames[-1].time_id if self.frames else 0


def parse_frame_path(path: Path) -> FrameRecord | None:
    match = FRAME_RE.match(path.name)
    if not match:
        return None

    global_frame_id = int(match.group("global"))
    event_id = match.group("event")
    local_frame_id = int(match.group("local"))
    ext = match.group("ext")
    frame_key = f"{event_id}_{local_frame_id}"

    return FrameRecord(
        frame_id=f"{global_frame_id:06d}",
        frame_key=frame_key,
        global_frame_id=global_frame_id,
        event_id=event_id,
        local_frame_id=local_frame_id,
        time_id=global_frame_id,
        modality=ext,  # type: ignore[arg-type]
        path=path,
    )


def build_frame_index(frames_dir: str | Path) -> FrameIndex:
    root = Path(frames_dir)
    if not root.exists():
        raise FileNotFoundError(f"Frames directory does not exist: {root}")

    frames: list[FrameRecord] = []
    for path in root.iterdir():
        if not path.is_file():
            continue
        frame = parse_frame_path(path)
        if frame is not None:
            frames.append(frame)

    if not frames:
        raise ValueError(f"No frame files found in {root}")

    return FrameIndex(frames)


def read_frame_text(frame: FrameRecord) -> str:
    if frame.modality != "txt":
        raise ValueError(f"Frame is not text: {frame.frame_key}")
    return frame.path.read_text(encoding="utf-8", errors="replace")

