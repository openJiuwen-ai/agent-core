from pathlib import Path

from video_memory.schemas import FrameRecord
from video_memory.data.windowing import make_windows


def _frame(i: int) -> FrameRecord:
    return FrameRecord(
        frame_id=f"{i:06d}",
        frame_key=f"e_{i}",
        global_frame_id=i,
        event_id="e",
        local_frame_id=i,
        time_id=i,
        modality="png",
        path=Path(f"{i}.png"),
    )


def test_make_windows() -> None:
    windows = make_windows([_frame(i) for i in range(5)], window_size=2, stride=2)
    assert [window.frame_keys for window in windows] == [["e_0", "e_1"], ["e_2", "e_3"], ["e_4"]]

