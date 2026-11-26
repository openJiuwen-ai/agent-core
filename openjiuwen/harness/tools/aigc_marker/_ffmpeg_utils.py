# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Shared ffmpeg/ffprobe utilities for media AIGC decorators."""

import json
import os
import shutil
import struct
import subprocess
from pathlib import Path

from openjiuwen.harness.tools.aigc_marker._common import parse_aigc_json


class FfmpegHelper:
    """Helper for finding ffmpeg/ffprobe and running metadata operations.

    ffmpeg/ffprobe paths are resolved lazily so that importing this module
    (or constructing the helper) does not fail when ffmpeg is not installed.
    Only actual media operations trigger the lookup.
    """

    def __init__(self):
        self._ffmpeg_path = None
        self._ffprobe_path = None

    _INPUT_FORMATS = {
        ".wav": "wav",
        ".mp3": "mp3",
        ".ogg": "ogg",
        ".flac": "flac",
        ".m4a": "mov",
        ".mp4": "mov",
        ".flv": "flv",
        ".mkv": "matroska",
        ".avi": "avi",
        ".png": "png_pipe",
    }

    @staticmethod
    def input_args(path: str) -> list[str]:
        """Open files with their specific demuxer instead of probing playlists."""
        format_name = FfmpegHelper._INPUT_FORMATS[Path(path).suffix.lower()]
        return ["-protocol_whitelist", "file", "-f", format_name, "-i", path]

    @property
    def ffmpeg_path(self) -> str:
        if self._ffmpeg_path is None:
            self._ffmpeg_path = self._find_binary("ffmpeg")
        return self._ffmpeg_path

    @property
    def ffprobe_path(self) -> str | None:
        if self._ffprobe_path is None:
            self._ffprobe_path = self._find_binary("ffprobe", required=False)
        return self._ffprobe_path

    @staticmethod
    def _find_binary(name: str, required: bool = True) -> str | None:
        """Resolve media tools from injected paths or PATH."""
        env_name = "CLAW_FFMPEG_EXE" if name == "ffmpeg" else "CLAW_FFPROBE_EXE"
        injected = os.environ.get(env_name, "").strip().strip('"')
        if injected and os.path.isfile(injected):
            return injected
        path = shutil.which(name)
        if path:
            return path
        if not required:
            return None
        raise RuntimeError(f"{name} not found. Configure {env_name} or install {name} on PATH.")

    def has_aigc_mark(self, file_path: str) -> bool:
        """Check if media file already has AIGC metadata."""
        return self.get_aigc_data(file_path) is not None

    def get_aigc_data(self, file_path: str) -> dict | None:
        """Read and parse the AIGC metadata tag from a media file.

        Returns the parsed dict if found and valid, None otherwise.
        Handles WAV specially (top-level RIFF chunk + legacy LIST/INFO).
        """
        ext = os.path.splitext(file_path)[1].lower()
        if ext == ".wav":
            return self._wav_get_aigc_data(file_path)
        try:
            if not self.ffprobe_path:
                return self._get_aigc_data_with_ffmpeg(file_path)
            cmd = [
                self.ffprobe_path,
                "-show_format",
                "-show_streams",
                "-print_format",
                "json",
                "-v",
                "quiet",
                *FfmpegHelper.input_args(file_path),
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            data = json.loads(result.stdout)
            for container in (data.get("format", {}), *data.get("streams", [])):
                for key, value in container.get("tags", {}).items():
                    if key.casefold() == "aigc":
                        metadata = parse_aigc_json(value)
                        if metadata is not None:
                            return metadata
            return None
        except (subprocess.CalledProcessError, json.JSONDecodeError, KeyError):
            return None

    def _get_aigc_data_with_ffmpeg(self, file_path: str) -> dict | None:
        """Read container metadata with ffmpeg when ffprobe is unavailable."""
        try:
            result = subprocess.run(
                [
                    self.ffmpeg_path,
                    "-v",
                    "error",
                    *FfmpegHelper.input_args(file_path),
                    "-f",
                    "ffmetadata",
                    "-",
                ],
                capture_output=True,
                text=True,
                check=True,
                encoding="utf-8",
                errors="replace",
            )
            for line in result.stdout.splitlines():
                key, separator, value = line.partition("=")
                if separator and key.strip().casefold() == "aigc":
                    return parse_aigc_json(value.strip())
        except (OSError, subprocess.CalledProcessError):
            return None
        return None

    @staticmethod
    def _wav_get_aigc_data(file_path: str) -> dict | None:
        """Read AIGC metadata from a WAV file.

        Detects both top-level 'AIGC' RIFF chunk and legacy LIST/INFO layout.
        """
        try:
            with open(file_path, "rb") as f:
                header = f.read(12)
                if len(header) < 12 or header[:4] != b"RIFF" or header[8:12] != b"WAVE":
                    return None
                while True:
                    ch_header = f.read(8)
                    if len(ch_header) < 8:
                        return None
                    chunk_id = ch_header[:4]
                    chunk_size = struct.unpack("<I", ch_header[4:8])[0]
                    padded = chunk_size + (chunk_size % 2)
                    if chunk_id == b"AIGC":
                        value = f.read(chunk_size).decode("utf-8", errors="ignore").rstrip("\x00")
                        return parse_aigc_json(value)
                    if chunk_id == b"LIST":
                        body = f.read(padded)
                        if len(body) < padded:
                            return None
                        if body[:4] == b"INFO":
                            i = 4
                            while i + 8 <= len(body):
                                sub_key, sub_size = struct.unpack_from("<4sI", body, i)
                                value_start = i + 8
                                value_end = value_start + sub_size
                                if sub_key == b"AIGC":
                                    value = body[value_start:value_end].decode("utf-8", errors="ignore").rstrip("\x00")
                                    return parse_aigc_json(value)
                                i = value_end + (sub_size % 2)
                        continue
                    f.seek(padded, 1)
        except OSError:
            return None

    def run_ffmpeg_metadata(self, file_path: str, signature: str, extra_args: list = None) -> None:
        """Run ffmpeg with -metadata AIGC=signature and optional extra args, using temp file + atomic replace."""
        base, ext = os.path.splitext(file_path)
        tmp_path = base + ".aigc.tmp" + ext
        try:
            cmd = [
                self.ffmpeg_path,
                "-y",
                *FfmpegHelper.input_args(file_path),
            ]
            if extra_args:
                cmd.extend(extra_args)
            cmd.extend(
                [
                    "-metadata",
                    f"AIGC={signature}",
                    "-c",
                    "copy",
                    tmp_path,
                ]
            )
            subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True,
                encoding="utf-8",
            )
            os.replace(tmp_path, file_path)
        except subprocess.CalledProcessError as e:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise RuntimeError(f"ffmpeg failed to add AIGC metadata: {e.stderr}") from e
        except OSError as e:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise RuntimeError(f"Failed to replace media file with AIGC-marked version: {e}") from e
