# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
MultimodalDocument data model test cases
"""

import base64
import io
import wave
from pathlib import Path

import pytest
from PIL import Image
from pydantic import ValidationError

from openjiuwen.core.retrieval import MultimodalDocument


def tiny_png_base64():
    """Generate a minimal valid PNG image as a base64-encoded data URL.

    Creates a 1x1 pixel PNG image and returns it as a data URL string
    suitable for use in MultimodalDocument tests.

    Returns:
        str: A data URL string in the format "data:image/png;base64,..."
    """
    img = Image.new("RGB", (1, 1), color=(0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def tiny_wav_base64():
    """Generate a minimal valid WAV audio file as a base64-encoded data URL.

    Creates a minimal WAV file with a single silent sample and returns it
    as a data URL string suitable for use in MultimodalDocument tests.

    Returns:
        str: A data URL string in the format "data:audio/wav;base64,..."
    """
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w: wave.Wave_write
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(44100)
        w.writeframes(b"\x00\x00")
    return "data:audio/wav;base64," + base64.b64encode(buf.getvalue()).decode()


def fake_mp4_base64():
    """Generate a fake MP4 video file as a base64-encoded data URL.

    Creates a minimal fake MP4 data string and returns it as a data URL
    suitable for use in MultimodalDocument tests. Note: This is not a
    valid MP4 file, but sufficient for testing data URL format handling.

    Returns:
        str: A data URL string in the format "data:video/mp4;base64,..."
    """
    return "data:video/mp4;base64," + base64.b64encode(b"fake mp4 data").decode()


def all_media():
    """Generate base64-encoded data URLs for all supported media types.

    Returns a dictionary containing data URLs for image, audio, and video
    modalities. Useful for testing MultimodalDocument with multiple content types.

    Returns:
        dict: A dictionary with keys "image", "audio", and "video", each
            containing a base64-encoded data URL string.
    """
    return {
        "image": tiny_png_base64(),
        "audio": tiny_wav_base64(),
        "video": fake_mp4_base64(),
    }


class TestMultimodalDocument:
    """MultimodalDocument data model tests"""

    @staticmethod
    def test_create_multimodal_document():
        """Test creating empty multimodal document"""
        doc = MultimodalDocument()
        assert doc.id_ is not None
        assert doc.metadata == {}
        assert doc.text == ""
        assert isinstance(doc.content, list) and not doc.content

    @staticmethod
    def test_create_multimodal_document_with_id():
        """Test creating multimodal document with ID"""
        doc = MultimodalDocument(id_="test_id")
        assert doc.id_ == "test_id"

    @staticmethod
    def test_create_multimodal_document_with_metadata():
        """Test creating multimodal document with metadata"""
        metadata = {"source": "test", "author": "test_author"}
        doc = MultimodalDocument(metadata=metadata)
        assert doc.metadata == metadata

    @staticmethod
    def test_create_multimodal_document_with_text():
        """Test creating multimodal document with text caption"""
        doc = MultimodalDocument(text="dummy")
        assert doc.text == "dummy"

    @staticmethod
    def test_add_text_field():
        """Test adding text field"""
        doc = MultimodalDocument()
        doc.add_field("text", "Hello world")
        assert len(doc.content) == 1
        assert doc.content[0] == {"type": "text", "text": "Hello world"}

    @staticmethod
    def test_add_multiple_text_fields():
        """Test adding multiple text fields"""
        doc = MultimodalDocument()
        doc.add_field("text", "First text")
        doc.add_field("text", "Second text")
        assert len(doc.content) == 2
        assert doc.content[0]["text"] == "First text"
        assert doc.content[1]["text"] == "Second text"

    @staticmethod
    def test_add_image_field_from_base64():
        """Test adding image field from base64 data"""
        image_data = tiny_png_base64()
        doc = MultimodalDocument()
        doc.add_field("image", data=image_data)
        assert len(doc.content) == 1
        assert doc.content[0]["type"] == "image_url"
        assert doc.content[0]["image_url"]["url"] == image_data
        assert "uuid" in doc.content[0]

    @staticmethod
    def test_add_image_field_from_file(tmp_path):
        """Test adding image field from file path"""
        # Create a temporary image file
        image_file = tmp_path / "test_image.png"
        image_file.write_bytes(b"fake image data")

        doc = MultimodalDocument()
        doc.add_field("image", file_path=image_file)
        assert len(doc.content) == 1
        assert doc.content[0]["type"] == "image_url"
        assert doc.content[0]["image_url"]["url"].startswith("data:image/")
        assert "uuid" in doc.content[0]

    @staticmethod
    def test_add_audio_field_from_base64():
        """Test adding audio field from base64 data"""
        audio_data = tiny_wav_base64()
        doc = MultimodalDocument()
        doc.add_field("audio", data=audio_data)
        assert len(doc.content) == 1
        assert doc.content[0]["type"] == "input_audio"
        assert doc.content[0]["input_audio"]["data"] == audio_data
        assert doc.content[0]["input_audio"]["format"] == "wav"
        assert "uuid" in doc.content[0]

    @staticmethod
    def test_add_audio_field_from_file(tmp_path):
        """Test adding audio field from file path"""
        # Create a temporary audio file
        audio_file = tmp_path / "test_audio.wav"
        audio_file.write_bytes(b"fake audio data")

        doc = MultimodalDocument()
        doc.add_field("audio", file_path=audio_file)
        assert len(doc.content) == 1
        assert doc.content[0]["type"] == "input_audio"
        assert doc.content[0]["input_audio"]["data"].startswith("data:audio/")
        assert "format" in doc.content[0]["input_audio"]
        assert "uuid" in doc.content[0]

    @staticmethod
    def test_add_video_field_from_base64():
        """Test adding video field from base64 data"""
        video_data = fake_mp4_base64()
        doc = MultimodalDocument()
        doc.add_field("video", data=video_data)
        assert len(doc.content) == 1
        assert doc.content[0]["type"] == "video_url"
        assert doc.content[0]["video_url"]["url"] == video_data
        assert "uuid" in doc.content[0]

    @staticmethod
    def test_add_video_field_from_file(tmp_path):
        """Test adding video field from file path"""
        # Create a temporary video file
        video_file = tmp_path / "test_video.mp4"
        video_file.write_bytes(b"fake video data")

        doc = MultimodalDocument()
        doc.add_field("video", file_path=video_file)
        assert len(doc.content) == 1
        assert doc.content[0]["type"] == "video_url"
        assert doc.content[0]["video_url"]["url"].startswith("data:video/")
        assert "uuid" in doc.content[0]

    @staticmethod
    def test_add_field_with_data_id():
        """Test adding field with custom data_id"""
        doc = MultimodalDocument()
        custom_id = "a" * 32  # Valid 32-character UUID
        image_data = tiny_png_base64()
        doc.add_field("image", data=image_data, data_id=custom_id)
        assert doc.content[0]["uuid"] == custom_id

    @staticmethod
    def test_method_chaining():
        """Test method chaining for add_field"""
        image_data = tiny_png_base64()
        doc = (
            MultimodalDocument()
            .add_field("text", "Hello")
            .add_field("text", "World")
            .add_field("image", data=image_data)
        )
        assert len(doc.content) == 3
        assert doc.content[0]["text"] == "Hello"
        assert doc.content[1]["text"] == "World"
        assert doc.content[2]["type"] == "image_url"

    @staticmethod
    def test_mixed_modalities():
        """Test adding multiple different modalities"""
        media = all_media()
        doc = MultimodalDocument()
        doc.add_field("text", "Description")
        doc.add_field("image", data=media["image"])
        doc.add_field("audio", data=media["audio"])
        doc.add_field("video", data=media["video"])

        assert len(doc.content) == 4
        assert doc.content[0]["type"] == "text"
        assert doc.content[1]["type"] == "image_url"
        assert doc.content[2]["type"] == "input_audio"
        assert doc.content[3]["type"] == "video_url"

    @staticmethod
    def test_text_field_no_uuid():
        """Test that text fields don't get UUIDs"""
        doc = MultimodalDocument()
        doc.add_field("text", "Hello")
        assert "uuid" not in doc.content[0]

    @staticmethod
    def test_invalid_kind():
        """Test adding field with invalid kind"""
        doc = MultimodalDocument()
        with pytest.raises(ValidationError) as exc_info:
            doc.add_field("invalid", "test")
        assert "unknown_kind" in str(exc_info.value)

    @staticmethod
    def test_no_data_source_provided():
        """Test error when neither data nor file_path is provided"""
        doc = MultimodalDocument()
        with pytest.raises(ValidationError) as exc_info:
            doc.add_field("image")
        assert "no_image_source_provided" in str(exc_info.value)

    @staticmethod
    def test_both_data_and_file_path_provided(tmp_path):
        """Test error when both data and file_path are provided"""
        doc = MultimodalDocument()
        image_file = tmp_path / "test.png"
        image_file.write_bytes(b"test")
        image_data = tiny_png_base64()

        with pytest.raises(ValidationError) as exc_info:
            doc.add_field("image", data=image_data, file_path=image_file)
        assert "too_many_image_source_provided" in str(exc_info.value)

    @staticmethod
    def test_invalid_data_format():
        """Test error when data doesn't match expected format"""
        doc = MultimodalDocument()
        with pytest.raises(ValidationError) as exc_info:
            doc.add_field("image", data="invalid_data")
        assert "invalid_image_data_provided" in str(exc_info.value)

    @staticmethod
    def test_invalid_file_path_type():
        """Test error when file_path is not a Path object"""
        doc = MultimodalDocument()
        with pytest.raises(ValidationError) as exc_info:
            doc.add_field("image", file_path="not_a_path")
        assert "invalid_image_file_path_provided" in str(exc_info.value)

    @staticmethod
    def test_file_not_found():
        """Test error when file doesn't exist"""
        doc = MultimodalDocument()
        non_existent = Path("/nonexistent/file.png")
        with pytest.raises(ValidationError) as exc_info:
            doc.add_field("image", file_path=non_existent)
        assert "image_path_invalid" in str(exc_info.value)

    @staticmethod
    def test_invalid_data_id_too_long():
        """Test error when data_id is too long"""
        doc = MultimodalDocument()
        invalid_id = "a" * 33  # 33 characters, should be max 32
        image_data = tiny_png_base64()
        with pytest.raises(ValidationError) as exc_info:
            doc.add_field("image", data=image_data, data_id=invalid_id)
        assert "invalid_uuid_provided" in str(exc_info.value)

    @staticmethod
    def test_invalid_data_id_not_string():
        """Test error when data_id is not a string"""
        doc = MultimodalDocument()
        image_data = tiny_png_base64()
        with pytest.raises(ValidationError) as exc_info:
            doc.add_field("image", data=image_data, data_id=12345)
        assert "invalid_uuid_provided" in str(exc_info.value)

    @staticmethod
    def test_text_from_file(tmp_path):
        """Test adding text field from file path"""
        text_file = tmp_path / "test.txt"
        text_file.write_text("Hello from file", encoding="utf-8")

        doc = MultimodalDocument()
        doc.add_field("text", file_path=text_file)
        assert doc.content[0]["text"] == "Hello from file"

    @staticmethod
    def test_audio_format_extraction():
        """Test that audio format is correctly extracted from base64 data"""
        doc = MultimodalDocument()
        # Create MP3 audio data using base64
        mp3_data = "data:audio/mp3;base64," + base64.b64encode(b"fake mp3 data").decode()
        doc.add_field("audio", data=mp3_data)
        assert doc.content[0]["input_audio"]["format"] == "mp3"

    @staticmethod
    def test_forbid_extra_fields():
        """Test that extra fields are forbidden"""
        with pytest.raises(ValidationError):
            MultimodalDocument(extra_field="not_allowed")

    @staticmethod
    def test_content_with_multiple_uuids():
        """Test content property with multiple fields having UUIDs"""
        doc = MultimodalDocument()
        image_data = tiny_png_base64()
        video_data = fake_mp4_base64()
        doc.add_field("image", data=image_data, data_id="a" * 32)
        doc.add_field("video", data=video_data, data_id="b" * 32)

        assert doc.content[0]["uuid"] == "a" * 32
        assert doc.content[1]["uuid"] == "b" * 32
