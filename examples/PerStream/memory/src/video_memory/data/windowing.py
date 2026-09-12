from __future__ import annotations

from video_memory.schemas import FrameRecord, FrameWindow


def make_windows(frames: list[FrameRecord], window_size: int, stride: int) -> list[FrameWindow]:
    if window_size <= 0:
        raise ValueError("window_size must be positive")
    if stride <= 0:
        raise ValueError("stride must be positive")

    ordered = sorted(frames, key=lambda frame: frame.time_id)
    windows: list[FrameWindow] = []
    start = 0
    window_num = 0

    while start < len(ordered):
        chunk = ordered[start : start + window_size]
        if not chunk:
            break
        windows.append(
            FrameWindow(
                window_id=f"window_{window_num:06d}",
                frame_keys=[frame.frame_key for frame in chunk],
                start_time_id=chunk[0].time_id,
                end_time_id=chunk[-1].time_id,
            )
        )
        window_num += 1
        start += stride

    return windows

