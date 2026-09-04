from pathlib import Path

from video_memory.data.frame_index import build_frame_index, parse_frame_path


def test_parse_frame_path() -> None:
    frame = parse_frame_path(Path("000956_17950813557789989352_21.png"))
    assert frame is not None
    assert frame.global_frame_id == 956
    assert frame.event_id == "17950813557789989352"
    assert frame.local_frame_id == 21
    assert frame.frame_key == "17950813557789989352_21"
    assert frame.time_id == 956
    assert frame.modality == "png"


def test_build_frame_index(tmp_path: Path) -> None:
    (tmp_path / "000001_123_0.png").write_bytes(b"fake")
    (tmp_path / "000002_123_1.txt").write_text("hello", encoding="utf-8")
    index = build_frame_index(tmp_path)
    assert len(index) == 2
    assert index.require("123_1").modality == "txt"

