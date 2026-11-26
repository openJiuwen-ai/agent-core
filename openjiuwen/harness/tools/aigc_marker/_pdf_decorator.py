# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""PDF AIGC decorator."""

import io
import json
import os
import tempfile

from pypdf import PdfReader, PdfWriter

from openjiuwen.core.common.logging import agent_logger
from openjiuwen.harness.tools.aigc_marker._common import (
    AIGC_FIELDS,
    canonical_signature,
    is_aigc_complete,
    parse_aigc_json,
)


class PdfAigcDecorator:
    """PDF AIGC decorator - adds hidden AIGC mark to PDF documents."""

    def __init__(self):
        self.name = "pdf_aigc_decorator"

    def decorate(self, file_path: str, fallback_signature: str, add_visible_mark: bool = True):
        """Add AIGC mark to PDF file."""
        existing = self._get_aigc_data(file_path)
        raw = (PdfReader(file_path).metadata or {}).get("/AIGC")
        flat = False
        try:
            flat = isinstance(raw, str) and json.loads(raw) == existing
        except ValueError:
            pass
        metadata_complete = is_aigc_complete(existing) and set(AIGC_FIELDS).issubset(existing or {})
        if flat and metadata_complete:
            if not add_visible_mark or self._has_visible_mark(file_path):
                agent_logger.debug("  [PDF] AIGC mark complete, skipping")
                return
        try:
            aigc_signature = canonical_signature(existing, fallback_signature)

            self._add_aigc_mark(file_path, aigc_signature, add_visible_mark)
            agent_logger.debug("  [PDF] AIGC mark added successfully")
        except Exception as e:
            raise RuntimeError(f"PDF AIGC marking failed: {e}") from e

    @staticmethod
    def _has_visible_mark(file_path):
        reader = PdfReader(file_path)
        return bool(reader.pages) and all("内容由AI生成" in (page.extract_text() or "") for page in reader.pages)

    @staticmethod
    def _get_aigc_data(file_path: str) -> dict | None:
        """Read and parse AIGC metadata from PDF info dictionary."""
        try:
            with open(file_path, "rb") as f:
                reader = PdfReader(f)
                if reader.metadata:
                    for key, value in reader.metadata.items():
                        if key == "/AIGC":
                            return parse_aigc_json(str(value))
        except Exception:
            return None
        return None

    def _add_aigc_mark(self, input_path: str, signature: str, add_visible_mark: bool = True) -> None:
        """Add or update metadata and optional watermark in a PDF file."""
        # Read input PDF
        reader = PdfReader(input_path)
        writer = PdfWriter()

        # Copy all pages
        writer.clone_reader_document_root(reader)

        # Add visible watermark if requested
        if add_visible_mark:
            self._add_visible_watermark(writer)

        writer_metadata = writer.metadata or {}
        preserved_metadata = {}
        for key, value in (reader.metadata or {}).items():
            if key not in writer_metadata:
                preserved_metadata[key] = str(value)
        preserved_metadata["/AIGC"] = signature
        writer.add_metadata(preserved_metadata)

        # Write beside the source and atomically replace it. This is safe on
        # Windows and avoids leaving a truncated PDF after an interrupted write.
        target_dir = os.path.dirname(os.path.abspath(input_path))
        fd, tmp_path = tempfile.mkstemp(prefix=".aigc-", suffix=".pdf", dir=target_dir)
        os.close(fd)
        try:
            with open(tmp_path, "wb") as f:
                writer.write(f)
            os.replace(tmp_path, input_path)
        except Exception as e:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise Exception(f"PDF add metadata failed: {str(e)}") from e

    @staticmethod
    def _add_visible_watermark(writer: PdfWriter):
        """Add visible '内容由AI生成' watermark to PDF pages using reportlab-generated watermark."""
        from openjiuwen.harness.tools.aigc_marker._pdf_watermark import create_watermark_pdf

        watermark_by_size = {}
        temp_paths = []
        try:
            for page in writer.pages:
                if page.rotation:
                    page.transfer_rotation_to_content()
                width = float(page.cropbox.width)
                height = float(page.cropbox.height)
                size_key = (round(width, 3), round(height, 3))
                watermark_page = watermark_by_size.get(size_key)
                if watermark_page is None:
                    fd, watermark_path = tempfile.mkstemp(prefix="aigc-watermark-", suffix=".pdf")
                    os.close(fd)
                    temp_paths.append(watermark_path)
                    create_watermark_pdf(
                        output_path=watermark_path,
                        text="内容由AI生成",
                        font_size=10.5,
                        opacity=1.0,
                        angle=0,
                        color=(0, 0, 0),
                        position="bottom-center",
                        page_size=(width, height),
                    )
                    with open(watermark_path, "rb") as watermark_file:
                        watermark_page = PdfReader(io.BytesIO(watermark_file.read())).pages[0]
                    watermark_by_size[size_key] = watermark_page
                page.merge_translated_page(
                    watermark_page, float(page.cropbox.left), float(page.cropbox.bottom), over=True
                )
        finally:
            for path in temp_paths:
                try:
                    os.remove(path)
                except OSError:
                    pass
