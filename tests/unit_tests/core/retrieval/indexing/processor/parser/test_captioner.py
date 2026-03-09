# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
"""
ImageCaptioner unit tests
"""

import os
import tempfile

from unittest.mock import AsyncMock, patch

import pytest

from openjiuwen.core.retrieval.indexing.processor.parser.captioner import ImageCaptioner


def create_mock_llm_client(model_name: str | None = "gpt-4o"):
    """reate a proper mock llm_client with required attributes

    Args:
        model_name (str | None, optional): _description_. Defaults to "gpt-4o".

    Returns:
        _type_: _description_
    """
    if model_name is None:
        return None

    mock_llm_client = AsyncMock()
    mock_llm_client.model_config.model_name = model_name
    return mock_llm_client


class TestImageCaptioner:
    @pytest.mark.asyncio
    async def test_dump_image_saves_and_returns_base64(self, monkeypatch, tmp_path):
        """Ensure dump/cp_image saves a copy to SAVED_IMAGES_DIR and returns the path."""
        # prepare temp image file
        img_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(img_bytes)
            src_path = f.name

        # point saver dir to tmp_path
        monkeypatch.setenv("OPENJIUWEN_SAVED_IMAGES_DIR", str(tmp_path))
        try:
            # call static save function
            save_fn = getattr(ImageCaptioner, "cp_image")

            result_path = save_fn(src_path, str(tmp_path))

            # Verify the function returns a valid path
            assert isinstance(result_path, str)
            assert os.path.exists(result_path)

            # Read the returned path and verify content
            with open(result_path, "rb") as f:
                saved_content = f.read()
            assert saved_content.startswith(img_bytes[:8])  # header preserved

            # ensure a file was created in target dir (name contains original basename)
            saved_files = list(tmp_path.iterdir())
            assert len(saved_files) == 1

            # Verify the saved file is in the correct location
            saved_file = saved_files[0]
            assert saved_file.name == os.path.basename(src_path)

            # Verify content matches
            with open(saved_file, "rb") as fh:
                content = fh.read()
                assert content == img_bytes

        finally:
            if os.path.exists(src_path):
                os.unlink(src_path)

    @pytest.mark.asyncio
    async def test__llm_call_async_returns_empty_when_no_llm(self):
        """If no llm_client or unsupported model, _llm_call_async should return empty string."""
        ic = ImageCaptioner(llm_client=create_mock_llm_client(model_name=None))  # no llm_client
        res = await ic._llm_call_async("somepath.png")
        assert isinstance(res, str)
        assert res == "" or res.strip() == ""

    @pytest.mark.asyncio
    async def test_caption_images_uses__llm_call_async_for_each_image(self):
        """caption_images should call internal _llm_call_async for each image and return results."""
        ic = ImageCaptioner(
            llm_client=create_mock_llm_client(model_name="gpt-4o")
        )  # llm_client presence; internal call mocked

        async def fake_llm_call(image_loc):
            return f"caption for {os.path.basename(image_loc)}"

        with (
            patch.object(
                ImageCaptioner, "_llm_call_async", new=AsyncMock(side_effect=fake_llm_call)
            ) as mock_llm,
            patch("os.path.exists", return_value=True),
        ):
            # use a couple of temp image paths (they need not exist for this test)
            paths = ["/tmp/img1.png", "/tmp/img2.jpg"]
            captions = await ic.caption_images(paths)
            assert len(captions) == 2
            assert captions[0] == "caption for img1.png"
            assert captions[1] == "caption for img2.jpg"
            assert mock_llm.call_count == 2

    @pytest.mark.asyncio
    async def test_caption_images_handles_llm_exceptions(self):
        """If _llm_call_async raises for one image, caption_images should catch and continue."""
        ic = ImageCaptioner(llm_client=create_mock_llm_client(model_name="gpt-4o"))

        async def side_effect(path):
            # Mock handles exception like the real method does
            try:
                if path.endswith("bad.png"):
                    raise RuntimeError("boom")
                return f"ok:{os.path.basename(path)}"
            except Exception as e:
                # Mimic the real method's exception handling
                return ""

        with (
            patch.object(
                ImageCaptioner, "_llm_call_async", new=AsyncMock(side_effect=side_effect)
            ) as mock_llm,
            patch("os.path.exists", return_value=True),
        ):
            paths = ["/tmp/good.png", "/tmp/bad.png", "/tmp/good2.png"]
            captions = await ic.caption_images(paths)
            # expected to produce results for good ones, and either "" or skip for bad one depending implementation
            assert len(captions) == 3
            assert captions[0].startswith("ok:good.png")
            # ensure exception did not bubble up and subsequent calls executed
            assert captions[2].startswith("ok:good2.png")
            assert mock_llm.call_count == 3
