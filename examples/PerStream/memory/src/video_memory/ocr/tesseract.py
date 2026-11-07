from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass

from video_memory.data.frame_index import read_frame_text
from video_memory.schemas import FrameRecord


@dataclass(frozen=True)
class OCRFrameText:
    frame_key: str
    time_id: int
    lines: list[str]
    elapsed_ms: int = 0

    @property
    def text(self) -> str:
        return "\n".join(self.lines)

    def to_dict(self) -> dict:
        return {
            "frame_key": self.frame_key,
            "time_id": self.time_id,
            "elapsed_ms": self.elapsed_ms,
            "lines": self.lines,
        }


class TesseractOCR:
    def __init__(self, language: str = "eng", psm: int = 11, dpi: int = 300) -> None:
        self.language = language
        self.psm = psm
        self.dpi = dpi

    def extract(self, frames: list[FrameRecord]) -> dict[str, OCRFrameText]:
        executable = shutil.which("tesseract")
        if executable is None:
            raise RuntimeError("Tesseract OCR requires the tesseract executable, but it was not found.")

        extracted: dict[str, OCRFrameText] = {}
        for frame in frames:
            if frame.modality == "txt":
                lines = _clean_lines(read_frame_text(frame).splitlines())
                extracted[frame.frame_key] = OCRFrameText(frame.frame_key, frame.time_id, lines)
            elif frame.modality == "png":
                extracted[frame.frame_key] = self._extract_png_frame(executable, frame)
        return extracted

    def _extract_png_frame(self, executable: str, frame: FrameRecord) -> OCRFrameText:
        start = time.perf_counter()
        process = subprocess.run(
            [
                executable,
                str(frame.path),
                "stdout",
                "-l",
                self.language,
                "--psm",
                str(self.psm),
                "--dpi",
                str(self.dpi),
            ],
            text=True,
            capture_output=True,
            check=False,
            timeout=60,
        )
        if process.returncode != 0:
            raise RuntimeError(f"Tesseract OCR failed for {frame.path}: {process.stderr.strip()}")

        return OCRFrameText(
            frame_key=frame.frame_key,
            time_id=frame.time_id,
            lines=_clean_lines(process.stdout.splitlines()),
            elapsed_ms=int((time.perf_counter() - start) * 1000),
        )


def _clean_lines(lines: list[str]) -> list[str]:
    cleaned: list[str] = []
    for line in lines:
        text = " ".join(str(line).strip().split())
        if text:
            cleaned.append(text)
    return cleaned
