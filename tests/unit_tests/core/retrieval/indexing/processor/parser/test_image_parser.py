# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
"""
Image file parser test cases
"""

import os
import tempfile
from unittest.mock import MagicMock, patch, AsyncMock

import pytest

from openjiuwen.core.retrieval import ImageParser


class TestImageParser:
    """Image file parser tests"""

    @staticmethod
    def test_init():
        """Test initialization"""
        parser = ImageParser()
        assert parser is not None

    @pytest.mark.asyncio
    async def test_parse_image_success(self):
        """Test parsing image file successfully (single caption)"""
        parser = ImageParser()

        # Prepare a temporary image file
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(b"\x89PNG\r\n\x1a\n")  # minimal PNG header bytes
            temp_path = f.name

        try:
            saved_path = "/images/saved_cat.png"
            caption = "A cat sitting on a mat"
            with patch(
                "openjiuwen.core.retrieval.indexing.processor.parser.image_parser.ImageCaptioner"
            ) as mock_captioner_cls:
                mock_inst = MagicMock()
                mock_inst.cp_image.return_value = saved_path
                mock_inst.caption_images = AsyncMock(return_value=[caption])
                mock_captioner_cls.return_value = mock_inst

                documents = await parser.parse(temp_path, doc_id="doc_img_1")
                assert len(documents) == 1
                assert documents[0].id_ == "doc_img_1"
                assert caption in documents[0].text
                assert documents[0].metadata.get("image_path") == saved_path
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)

    @pytest.mark.asyncio
    async def test_parse_image_exposes_image_path_in_metadata(self):
        """Test that parsed image document exposes image_path in metadata for the knowledge base."""
        parser = ImageParser()
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            f.write(b"\xff\xd8\xff")
            temp_path = f.name
        try:
            expected_image_path = "/kb/images/photo.jpg"
            with patch(
                "openjiuwen.core.retrieval.indexing.processor.parser.image_parser.ImageCaptioner"
            ) as mock_captioner_cls:
                mock_inst = MagicMock()
                mock_inst.cp_image.return_value = expected_image_path
                mock_inst.caption_images = AsyncMock(return_value=["A photo"])
                mock_captioner_cls.return_value = mock_inst

                documents = await parser.parse(temp_path, doc_id="img_doc")
                assert len(documents) == 1
                assert documents[0].metadata == {"image_path": expected_image_path}
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)

    @pytest.mark.asyncio
    async def test_parse_image_multiple_captions(self):
        """Test parsing image file that yields multiple captions"""
        parser = ImageParser()

        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            f.write(b"\xff\xd8\xff")  # minimal JPEG header bytes
            temp_path = f.name

        try:
            saved_path = "/images/dog.jpg"
            captions = ["A dog", "Running in a park"]
            with patch(
                "openjiuwen.core.retrieval.indexing.processor.parser.image_parser.ImageCaptioner"
            ) as mock_captioner_cls:
                mock_inst = MagicMock()
                mock_inst.cp_image.return_value = saved_path
                mock_inst.caption_images = AsyncMock(return_value=captions)
                mock_captioner_cls.return_value = mock_inst

                documents = await parser.parse(temp_path, doc_id="doc_img_2")
                assert len(documents) == 1
                assert documents[0].id_ == "doc_img_2"
                assert documents[0].metadata.get("image_path") == saved_path
                for c in captions:
                    assert c in documents[0].text
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)

    @pytest.mark.asyncio
    async def test_parse_image_file_not_found(self):
        """Test parsing non-existent file"""
        parser = ImageParser()
        documents = await parser.parse("nonexistent_image.png", doc_id="doc_img_nf")
        # Should return empty list (exception is caught)
        assert len(documents) == 0

    @pytest.mark.asyncio
    async def test_parse_image_with_exception(self):
        """When captioning raises, we still return one document with image_path and empty text."""
        parser = ImageParser()

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(b"\x89PNG\r\n\x1a\n")
            temp_path = f.name

        try:
            saved_path = "/images/tmp.png"
            with patch(
                "openjiuwen.core.retrieval.indexing.processor.parser.image_parser.ImageCaptioner"
            ) as mock_captioner_cls:
                mock_inst = MagicMock()
                mock_inst.cp_image.return_value = saved_path
                mock_inst.caption_images = AsyncMock(side_effect=Exception("Captioning error"))
                mock_captioner_cls.return_value = mock_inst

                documents = await parser.parse(temp_path, doc_id="doc_img_exc")
                assert len(documents) == 1
                assert documents[0].text == ""
                assert documents[0].metadata.get("image_path") == saved_path
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)
