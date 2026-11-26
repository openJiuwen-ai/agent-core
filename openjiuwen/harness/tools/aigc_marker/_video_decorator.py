# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Video AIGC decorator - adds hidden AIGC metadata to video files."""

import json
import os
import re
import struct
import subprocess
import tempfile

from openjiuwen.core.common.logging import agent_logger
from openjiuwen.harness.tools.aigc_marker._common import is_aigc_complete
from openjiuwen.harness.tools.aigc_marker._ffmpeg_utils import FfmpegHelper


class VideoAigcDecorator:
    """Video AIGC decorator - adds hidden AIGC mark to video files (mp4, flv, mkv, avi)."""

    def __init__(self):
        self.name = "video_aigc_decorator"
        self.ffmpeg = FfmpegHelper()
        # Resolve bundled font path at construction time
        try:
            from openjiuwen.harness.tools.aigc_marker._fonts import get_font_path

            self._font_path = get_font_path()
        except Exception:
            self._font_path = ""

    def decorate(self, file_path: str, fallback_signature: str, add_visible_mark: bool = True):
        """Add AIGC mark to video file.

        If add_visible_mark is True, burns a visible 'AI生成' watermark at bottom-right
        AND writes implicit AIGC metadata in a single ffmpeg pass (MP4/MKV/FLV) or
        two-step pipeline (AVI).
        """
        ext = os.path.splitext(file_path)[1].lower()
        existing = self.ffmpeg.get_aigc_data(file_path)
        if existing and is_aigc_complete(existing):
            agent_logger.debug(f"  [Video/{ext}] AIGC mark complete, skipping")
            return
        try:
            aigc_signature = fallback_signature
            if add_visible_mark:
                self._burn_visible_and_mark(file_path, aigc_signature)
                agent_logger.debug(f"  [Video/{ext}] AIGC mark (visible+implicit) added successfully")
                return
            self._add_aigc_mark(file_path, aigc_signature)
            agent_logger.debug(f"  [Video/{ext}] AIGC mark (implicit only) added successfully")
        except Exception as e:
            raise RuntimeError(f"Video AIGC marking failed for {ext}: {e}") from e

    def _add_aigc_mark(self, file_path: str, signature: str) -> None:
        """Write AIGC metadata to video file."""
        ext = os.path.splitext(file_path)[1].lower()
        if ext == ".avi":
            self._add_aigc_mark_avi(file_path, signature)
        elif ext == ".mp4":
            self.ffmpeg.run_ffmpeg_metadata(file_path, signature, extra_args=["-movflags", "use_metadata_tags"])
        else:
            self.ffmpeg.run_ffmpeg_metadata(file_path, signature)

    def _probe_dimensions(self, file_path: str) -> tuple:
        """Return (width, height) of first video stream via ffprobe."""
        if self.ffmpeg.ffprobe_path:
            cmd = [
                self.ffmpeg.ffprobe_path,
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_streams",
                "-select_streams",
                "v:0",
                *FfmpegHelper.input_args(file_path),
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            data = json.loads(result.stdout)
            streams = data.get("streams", [])
            if streams and streams[0].get("width") and streams[0].get("height"):
                return int(streams[0]["width"]), int(streams[0]["height"])

        result = subprocess.run(
            [self.ffmpeg.ffmpeg_path, "-hide_banner", *FfmpegHelper.input_args(file_path)],
            capture_output=True,
            text=True,
            check=False,
            encoding="utf-8",
            errors="replace",
        )
        for line in result.stderr.splitlines():
            if "Video:" not in line:
                continue
            match = re.search(r"(?<![\d:])(\d{2,5})x(\d{2,5})(?![\d:])", line)
            if match:
                return int(match.group(1)), int(match.group(2))
        raise RuntimeError(f"Unable to determine video dimensions: {file_path}")

    @staticmethod
    def _generate_watermark_image(width: int, height: int, font_path: str) -> tuple:
        """Generate a PNG watermark image with 'AI生成' text using PIL.

        This avoids ffmpeg drawtext not rendering CJK glyphs in some builds.
        Returns (temp_file_path, margin) where margin is the computed offset.
        """
        from PIL import Image, ImageDraw, ImageFont

        short_edge = min(width, height)
        fontsize = max(int(short_edge * 0.05), 10)
        margin = max(int(fontsize * 1.0), 20)
        pad = int(fontsize * 0.15)

        font = ImageFont.truetype(font_path, fontsize)

        # Measure text
        dummy_draw = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
        text_bbox = dummy_draw.textbbox((0, 0), "AI生成", font=font)
        tw = text_bbox[2] - text_bbox[0]
        th = text_bbox[3] - text_bbox[1]

        # Create watermark image with padding
        iw = tw + pad * 2 + margin
        ih = th + pad * 2 + margin
        img = Image.new("RGBA", (iw, ih), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        # Draw rounded background box
        draw.rounded_rectangle(
            [(0, 0), (iw - margin, ih - margin)],
            radius=int(fontsize * 0.2),
            fill=(0, 0, 0, 100),
        )

        # Draw text
        draw.text(
            (pad, pad - text_bbox[1]),
            "AI生成",
            font=font,
            fill=(255, 255, 255, 230),
        )

        # Save to temp file
        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".png", prefix="aigc_wm_")
        os.close(tmp_fd)
        img.save(tmp_path, "PNG")
        return tmp_path, margin

    def _has_rotation_metadata(self, file_path: str) -> int:
        """Check if video has rotation metadata. Returns rotation degrees (90/180/270) or 0."""
        try:
            if self.ffmpeg.ffprobe_path:
                cmd = [
                    self.ffmpeg.ffprobe_path,
                    "-v",
                    "quiet",
                    "-print_format",
                    "json",
                    "-show_streams",
                    "-select_streams",
                    "v:0",
                    *FfmpegHelper.input_args(file_path),
                ]
                result = subprocess.run(cmd, capture_output=True, text=True, check=True)
                data = json.loads(result.stdout)
                for stream in data.get("streams", []):
                    for sd in stream.get("side_data_list", []):
                        rotation = sd.get("rotation")
                        if rotation is not None and rotation != 0:
                            return int(rotation)
            else:
                result = subprocess.run(
                    [self.ffmpeg.ffmpeg_path, "-hide_banner", *FfmpegHelper.input_args(file_path)],
                    capture_output=True,
                    text=True,
                    check=False,
                    encoding="utf-8",
                    errors="replace",
                )
                match = re.search(r"rotation of\s+(-?\d+(?:\.\d+)?)\s+degrees", result.stderr)
                if match:
                    return int(round(float(match.group(1))))
        except Exception:
            agent_logger.debug("  [Video] rotation metadata unavailable; continuing without rotation")
            return 0
        return 0

    def _ffmpeg_overlay_watermark(self, file_path: str, signature: str, margin: int, extra_args: list = None) -> None:
        """Single-pass ffmpeg: overlay watermark image + write AIGC metadata.

        Handles rotation metadata: if the video has rotation metadata (e.g. -90° for
        portrait videos captured on phones), we physically rotate the pixels so the
        watermark appears at the correct corner with correct text orientation.
        """
        base, ext = os.path.splitext(file_path)
        tmp_path = base + ".aigc.tmp" + ext
        tmp_wm = None
        try:
            rotation = self._has_rotation_metadata(file_path)

            if rotation == -90 or rotation == 270:
                # Portrait video with rotation metadata: physically rotate pixels
                # to portrait, then overlay watermark at bottom-right
                width, height = self._probe_dimensions(file_path)
                tmp_wm, _ = self._generate_watermark_image(height, width, self._font_path)

                cmd = [
                    self.ffmpeg.ffmpeg_path,
                    "-y",
                    "-display_rotation",
                    "0",
                    *FfmpegHelper.input_args(file_path),
                    *FfmpegHelper.input_args(tmp_wm),
                    "-filter_complex",
                    f"[0:v]transpose=1[rot];[rot][1:v]overlay=W-w-{margin}:H-h-{margin}",
                    "-c:v",
                    "libx264",
                    "-b:v",
                    "1M",
                    "-c:a",
                    "copy",
                    "-metadata",
                    f"AIGC={signature}",
                    "-metadata:s:v:0",
                    "rotate=0",
                ]
            else:
                width, height = self._probe_dimensions(file_path)
                tmp_wm, _ = self._generate_watermark_image(width, height, self._font_path)

                cmd = [
                    self.ffmpeg.ffmpeg_path,
                    "-y",
                    *FfmpegHelper.input_args(file_path),
                    *FfmpegHelper.input_args(tmp_wm),
                    "-filter_complex",
                    f"[0:v][1:v]overlay=W-w-{margin}:H-h-{margin}",
                    "-c:v",
                    "libx264",
                    "-b:v",
                    "1M",
                    "-c:a",
                    "copy",
                    "-metadata",
                    f"AIGC={signature}",
                ]
            if extra_args:
                cmd.extend(extra_args)
            cmd.append(tmp_path)
            subprocess.run(cmd, capture_output=True, text=True, check=True, encoding="utf-8")
            os.replace(tmp_path, file_path)
        except subprocess.CalledProcessError as e:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise RuntimeError(f"ffmpeg overlay failed: {e.stderr}") from e
        except OSError as e:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise RuntimeError(f"Failed to replace media file: {e}") from e
        finally:
            if tmp_wm and os.path.exists(tmp_wm):
                try:
                    os.remove(tmp_wm)
                except OSError:
                    pass

    def _burn_visible_and_mark(self, file_path: str, signature: str) -> None:
        """Burn visible watermark + write implicit AIGC metadata in one pass.

        Uses PIL+overlay instead of ffmpeg drawtext because some ffmpeg builds
        cannot render CJK characters with drawtext.
        """
        ext = os.path.splitext(file_path)[1].lower()
        if not self._font_path:
            raise RuntimeError("Bundled font not found; cannot add visible watermark")

        width, height = self._probe_dimensions(file_path)
        font_size = max(int(min(width, height) * 0.05), 10)
        margin = max(font_size, 20)

        if ext == ".mp4":
            self._ffmpeg_overlay_watermark(
                file_path,
                signature,
                margin,
                extra_args=["-movflags", "use_metadata_tags"],
            )
        elif ext in (".mkv", ".flv"):
            self._ffmpeg_overlay_watermark(
                file_path,
                signature,
                margin,
            )
        elif ext == ".avi":
            base, _ = os.path.splitext(file_path)
            tmp_path = base + ".aigc.tmp.avi"
            tmp_wm = None
            try:
                tmp_wm, margin = self._generate_watermark_image(width, height, self._font_path)
                cmd = [
                    self.ffmpeg.ffmpeg_path,
                    "-y",
                    *FfmpegHelper.input_args(file_path),
                    *FfmpegHelper.input_args(tmp_wm),
                    "-filter_complex",
                    f"overlay=W-w-{margin}:H-h-{margin}",
                    "-c:v",
                    "mpeg4",
                    "-q:v",
                    "5",
                    "-c:a",
                    "copy",
                    tmp_path,
                ]
                subprocess.run(cmd, capture_output=True, text=True, check=True, encoding="utf-8")
                self._add_aigc_mark_avi(tmp_path, signature)
                os.replace(tmp_path, file_path)
            except subprocess.CalledProcessError as e:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
                raise RuntimeError(f"ffmpeg overlay failed for AVI: {e.stderr}") from e
            except OSError as e:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
                raise RuntimeError(f"Failed to replace media file: {e}") from e
            finally:
                if tmp_wm and os.path.exists(tmp_wm):
                    try:
                        os.remove(tmp_wm)
                    except OSError:
                        pass
        else:
            raise NotImplementedError(f"_burn_visible_and_mark not implemented for {ext}")

    @staticmethod
    def _add_aigc_mark_avi(file_path: str, signature: str) -> None:
        """Write AIGC metadata to AVI by injecting a RIFF LIST/INFO chunk.

        ffmpeg's -metadata with -c copy drops arbitrary keys in RIFF/AVI.
        We inject a custom LIST/INFO chunk directly so ffprobe can read 'AIGC'.
        """
        base, ext = os.path.splitext(file_path)
        tmp_path = base + ".aigc.tmp" + ext

        try:
            with open(file_path, "rb") as f:
                data = bytearray(f.read())

            if data[:4] != b"RIFF" or data[8:12] != b"AVI ":
                raise RuntimeError("Not a valid AVI file")

            key_bytes = b"AIGC"
            # RIFF INFO strings are conventionally null-terminated
            content = signature.encode("utf-8") + b"\x00"
            if len(content) % 2 == 1:
                content += b"\x00"

            # Rebuild the file, stripping any existing LIST/INFO chunk that contains AIGC
            new_data = bytearray(data[:12])  # keep RIFF header
            movi_chunk_end = None
            idx = 12
            while idx < len(data):
                chunk_id, chunk_size = struct.unpack_from("<4sI", data, idx)
                padded_size = chunk_size + (chunk_size % 2)
                payload_start = idx + 8
                chunk_end = payload_start + padded_size

                if chunk_id == b"LIST" and data.startswith(b"movi", payload_start):
                    movi_chunk_end = chunk_end
                    new_data.extend(data[idx:chunk_end])
                elif chunk_id == b"LIST" and data.startswith(b"INFO", payload_start):
                    # Scan this INFO list for the AIGC key
                    info_idx = payload_start + 4
                    info_end = chunk_end
                    has_aigc = False
                    while info_idx < info_end:
                        info_key, info_val_size = struct.unpack_from("<4sI", data, info_idx)
                        if info_key == key_bytes:
                            has_aigc = True
                            break
                        info_idx += 8 + info_val_size + (info_val_size % 2)
                    if not has_aigc:
                        new_data.extend(data[idx:chunk_end])
                else:
                    new_data.extend(data[idx:chunk_end])
                idx = chunk_end

            # Build the INFO chunk
            field_chunk = key_bytes + struct.pack("<I", len(content)) + content
            list_content = b"INFO" + field_chunk
            info_chunk = b"LIST" + struct.pack("<I", len(list_content)) + list_content

            if movi_chunk_end is not None:
                # Insert before movi for compatibility
                new_data = new_data[:movi_chunk_end] + info_chunk + new_data[movi_chunk_end:]
            else:
                # Append at end if no movi found
                new_data.extend(info_chunk)

            # Update RIFF size
            new_data[4:8] = struct.pack("<I", len(new_data) - 8)

            with open(tmp_path, "wb") as f:
                f.write(new_data)
            os.replace(tmp_path, file_path)
        except OSError as e:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise RuntimeError(f"Failed to write AIGC-marked AVI file: {e}") from e
