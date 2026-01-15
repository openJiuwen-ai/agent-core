# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
"""
JSON file parser test cases
"""

import json
import os
import tempfile
from unittest.mock import patch

import pytest

from openjiuwen.core.retrieval import JSONParser


class TestJSONParser:
    """JSON file parser tests"""

    @staticmethod
    def test_init():
        """Test initialization"""
        parser = JSONParser()
        assert parser is not None

    @pytest.mark.asyncio
    async def test_parse_json_success(self):
        """Test parsing JSON file successfully"""
        parser = JSONParser()

        # Create temporary JSON file
        json_data = {
            "name": "test",
            "value": 123,
            "items": ["item1", "item2"],
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(json_data, f)
            temp_path = f.name

        try:
            documents = await parser.parse(temp_path, doc_id="doc_1")
            assert len(documents) == 1
            assert documents[0].id_ == "doc_1"
            # JSON should be formatted as string
            assert "test" in documents[0].text
            assert "123" in documents[0].text
        finally:
            os.unlink(temp_path)

    @pytest.mark.asyncio
    async def test_parse_json_empty_object(self):
        """Test parsing empty JSON object"""
        parser = JSONParser()

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump({}, f)
            temp_path = f.name

        try:
            documents = await parser.parse(temp_path, doc_id="doc_1")
            assert len(documents) == 1
            assert documents[0].text == "{}"
        finally:
            os.unlink(temp_path)

    @pytest.mark.asyncio
    async def test_parse_json_array(self):
        """Test parsing JSON array"""
        parser = JSONParser()

        json_data = [1, 2, 3, "test"]

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(json_data, f)
            temp_path = f.name

        try:
            documents = await parser.parse(temp_path, doc_id="doc_1")
            assert len(documents) == 1
            assert "1" in documents[0].text
            assert "test" in documents[0].text
        finally:
            os.unlink(temp_path)

    @pytest.mark.asyncio
    async def test_parse_json_invalid_format(self):
        """Test parsing invalid JSON format"""
        parser = JSONParser()

        # Create invalid JSON file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            f.write("{ invalid json }")
            temp_path = f.name

        try:
            documents = await parser.parse(temp_path, doc_id="doc_1")
            # Should return original content (JSON parsing failed)
            assert len(documents) == 1
            assert "invalid json" in documents[0].text
        finally:
            os.unlink(temp_path)

    @pytest.mark.asyncio
    async def test_parse_json_file_not_found(self):
        """Test parsing non-existent file"""
        parser = JSONParser()
        documents = await parser.parse("nonexistent.json", doc_id="doc_1")
        # Should return empty list (exception is caught)
        assert len(documents) == 0

    @pytest.mark.asyncio
    async def test_parse_json_with_exception(self):
        """Test exception during parsing"""
        parser = JSONParser()

        with patch("aiofiles.open") as mock_open:
            mock_open.side_effect = Exception("File read error")

            with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
                temp_path = f.name

            try:
                documents = await parser.parse(temp_path, doc_id="doc_1")
                # Should return empty list (exception is caught)
                assert len(documents) == 0
            finally:
                if os.path.exists(temp_path):
                    os.unlink(temp_path)

    @pytest.mark.asyncio
    async def test_parse_json_with_unicode(self):
        """Test parsing JSON containing Unicode characters"""
        parser = JSONParser()

        json_data = {
            "name": "测试",
            "description": "这是一个测试",
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(json_data, f, ensure_ascii=False)
            temp_path = f.name

        try:
            documents = await parser.parse(temp_path, doc_id="doc_1")
            assert len(documents) == 1
            # Should preserve Unicode characters
            assert "测试" in documents[0].text
        finally:
            os.unlink(temp_path)

    @pytest.mark.asyncio
    async def test_parse_json_formatted_output(self):
        """Test JSON formatted output"""
        parser = JSONParser()

        json_data = {"key": "value", "nested": {"inner": "data"}}

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(json_data, f)
            temp_path = f.name

        try:
            documents = await parser.parse(temp_path, doc_id="doc_1")
            assert len(documents) == 1
            # Should be formatted as indented JSON
            parsed_text = documents[0].text
            # Verify contains indentation (formatted JSON should have newlines)
            assert "\n" in parsed_text or "  " in parsed_text
        finally:
            os.unlink(temp_path)
