# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for the shared image shrink pipeline."""

import base64
import io
import os

import pytest

pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

from openjiuwen.harness.tools.multimodal.image_shrink import (  # noqa: E402
    build_data_uri,
    detect_mime_type,
    shrink_image_bytes,
    shrink_image_to_data_uri,
)


def _encode(image: Image.Image, image_format: str, **kwargs) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format=image_format, **kwargs)
    return buf.getvalue()


def test_detect_mime_type_prefers_payload_over_suffix() -> None:
    payload = _encode(Image.new("RGB", (8, 8), "red"), "BMP")

    assert detect_mime_type(payload, ".png") == "image/bmp"
    assert detect_mime_type(payload, ".bmp") == "image/bmp"


def test_detect_mime_type_falls_back_to_suffix() -> None:
    assert detect_mime_type(b"not an image", ".png") == "image/png"
    assert detect_mime_type(b"not an image", ".weird") == ""


def test_fitting_image_is_reused_byte_for_byte() -> None:
    payload = _encode(Image.new("RGB", (64, 48), "green"), "PNG")

    assert shrink_image_bytes(payload, "image/png", max_bytes=1_000_000) == (payload, "image/png")


def test_unsupported_format_is_transcoded_to_png() -> None:
    payload = _encode(Image.new("RGB", (64, 48), "blue"), "TIFF")

    fitted = shrink_image_bytes(payload, "image/tiff", max_bytes=1_000_000)

    assert fitted is not None
    data, mime_type = fitted
    assert mime_type == "image/png"
    with Image.open(io.BytesIO(data)) as decoded:
        assert decoded.format == "PNG"


def test_oversized_opaque_png_falls_back_to_jpeg() -> None:
    """照片类 PNG 用 JPEG 保分辨率，而不是一路缩到预算内。"""
    noise = Image.frombytes("RGB", (1200, 900), os.urandom(1200 * 900 * 3))
    payload = _encode(noise, "PNG")

    fitted = shrink_image_bytes(payload, "image/png", max_bytes=60_000, max_dimension=1536)

    assert fitted is not None
    data, mime_type = fitted
    assert mime_type == "image/jpeg"
    assert len(data) <= 60_000
    with Image.open(io.BytesIO(data)) as decoded:
        assert decoded.format == "JPEG"


def test_transparent_png_keeps_png_output() -> None:
    transparent = Image.new("RGBA", (900, 900), (255, 0, 0, 100))
    payload = _encode(transparent, "PNG")

    fitted = shrink_image_bytes(payload, "image/png", max_bytes=60_000)

    assert fitted is not None
    data, mime_type = fitted
    assert mime_type == "image/png"
    with Image.open(io.BytesIO(data)) as decoded:
        assert decoded.format == "PNG"


def test_unfittable_budget_returns_none() -> None:
    noise = Image.frombytes("RGB", (600, 600), os.urandom(600 * 600 * 3))

    assert shrink_image_bytes(_encode(noise, "PNG"), "image/png", max_bytes=10) is None


def test_build_data_uri_round_trips_payload() -> None:
    assert build_data_uri(b"abc", "image/png") == "data:image/png;base64,YWJj"


def test_shrink_image_to_data_uri_writes_data_uri(tmp_path) -> None:
    payload = _encode(Image.new("RGB", (32, 32), "white"), "PNG")
    file_path = tmp_path / "sample.png"
    file_path.write_bytes(payload)

    uri = shrink_image_to_data_uri(str(file_path), max_bytes=1_000_000)

    assert uri.startswith("data:image/png;base64,")
    assert base64.b64decode(uri.split(",", 1)[1]) == payload
    assert shrink_image_to_data_uri(str(tmp_path / "missing.png")) == ""
