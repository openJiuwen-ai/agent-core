# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Unit tests for JSON and Text response parsers with RFC 6839 and binary JSON support."""

from openjiuwen.core.foundation.tool.service_api.response_parser import (
    JsonResponseParser,
    TextResponseParser,
)


def test_standard_json_content_types():
    """Test standard JSON content types are recognized."""
    parser = JsonResponseParser()
    assert parser.can_parse("application/json", 200) is True
    assert parser.can_parse("text/json", 200) is True
    assert parser.can_parse("text/x-json", 200) is True
    assert parser.can_parse("application/javascript", 200) is True


def test_rfc_6839_plus_json_suffix_types():
    """Test RFC 6839 structured syntax suffix types (+json) are recognized."""
    parser = JsonResponseParser()
    assert parser.can_parse("application/video+json", 200) is True
    assert parser.can_parse("application/hal+json", 200) is True
    assert parser.can_parse("application/ld+json", 200) is True
    assert parser.can_parse("application/schema+json", 200) is True
    assert parser.can_parse("application/problem+json", 200) is True


def test_content_type_case_insensitive():
    """Test content type matching is case-insensitive."""
    parser = JsonResponseParser()
    assert parser.can_parse("APPLICATION/JSON", 200) is True
    assert parser.can_parse("Application/Json", 200) is True
    assert parser.can_parse("APPLICATION/VIDEO+JSON", 200) is True


def test_missing_content_type_with_accept_header():
    """Test fallback to Accept header when Content-Type is missing."""
    parser = JsonResponseParser()
    assert parser.can_parse("", 200, Accept="application/json") is True
    assert parser.can_parse("", 200, Accept="application/ld+json") is True
    assert parser.can_parse(None, 200, Accept="application/json") is True
    assert parser.can_parse("", 200, Accept="text/html") is False
    assert parser.can_parse("", 200) is False


def test_non_json_content_types():
    """Test non-JSON content types are not recognized."""
    parser = JsonResponseParser()
    assert parser.can_parse("text/html", 200) is False
    assert parser.can_parse("text/plain", 200) is False
    assert parser.can_parse("application/xml", 200) is False
    assert parser.can_parse("application/xhtml+xml", 200) is False
    assert parser.can_parse("image/png", 200) is False


def test_parse_standard_json_bytes():
    """Test parsing standard JSON bytes."""
    parser = JsonResponseParser()
    json_bytes = b'{"name": "test", "value": 123}'
    result = parser.parse(json_bytes, encoding="utf-8", **{"Content-Type": "application/json"})
    assert result == {"name": "test", "value": 123}


def test_parse_rfc_6839_json_bytes():
    """Test parsing JSON bytes with RFC 6839 content type."""
    parser = JsonResponseParser()
    json_bytes = b'{"@context": "https://json-ld.org", "name": "test"}'
    result = parser.parse(
        json_bytes,
        encoding="utf-8",
        **{"Content-Type": "application/ld+json"}
    )
    assert result == {"@context": "https://json-ld.org", "name": "test"}


def test_parse_hal_json_bytes():
    """Test parsing HAL+JSON bytes."""
    parser = JsonResponseParser()
    hal_json = b'{"_links": {"self": {"href": "/api/users/123"}}, "id": 123, "name": "test_user"}'
    result = parser.parse(
        hal_json,
        encoding="utf-8",
        **{"Content-Type": "application/hal+json"}
    )
    assert result["id"] == 123
    assert result["name"] == "test_user"
    assert "_links" in result


def test_parse_empty_bytes_json():
    """Test parsing empty bytes returns empty dict."""
    parser = JsonResponseParser()
    result = parser.parse(b"", **{"Content-Type": "application/json"})
    assert result == {}


def test_parse_none_bytes_json():
    """Test parsing None returns empty dict."""
    parser = JsonResponseParser()
    result = parser.parse(None, **{"Content-Type": "application/json"})
    assert result == {}


def test_parse_video_plus_json():
    """Test parsing video+json binary response (the fix scenario)."""
    parser = JsonResponseParser()
    json_bytes = b'{"videoId": "abc123", "duration": 300, "status": "ready"}'
    result = parser.parse(
        json_bytes,
        encoding="utf-8",
        **{"Content-Type": "application/video+json"}
    )
    assert result == {"videoId": "abc123", "duration": 300, "status": "ready"}


def test_standard_text_content_types():
    """Test standard text content types are recognized."""
    parser = TextResponseParser()
    assert parser.can_parse("text/plain", 200) is True
    assert parser.can_parse("text/html", 200) is True
    assert parser.can_parse("text/xml", 200) is True
    assert parser.can_parse("text/css", 200) is True
    assert parser.can_parse("text/csv", 200) is True


def test_generic_text_types():
    """Test generic text/* types are recognized."""
    parser = TextResponseParser()
    assert parser.can_parse("text/markdown", 200) is True
    assert parser.can_parse("text/rtf", 200) is True


def test_xml_content_types():
    """Test XML content types are recognized (non-JSON)."""
    parser = TextResponseParser()
    assert parser.can_parse("application/xml", 200) is True
    assert parser.can_parse("application/xhtml+xml", 200) is True
    assert parser.can_parse("application/json", 200) is False


def test_javascript_content_types():
    """Test JavaScript content types are recognized."""
    parser = TextResponseParser()
    assert parser.can_parse("text/javascript", 200) is True
    assert parser.can_parse("application/javascript", 200) is True


def test_non_text_content_types():
    """Test non-text content types are not recognized."""
    parser = TextResponseParser()
    assert parser.can_parse("image/png", 200) is False
    assert parser.can_parse("application/pdf", 200) is False
    assert parser.can_parse("application/octet-stream", 200) is False


def test_parse_plain_text_bytes():
    """Test parsing plain text bytes."""
    parser = TextResponseParser()
    text_bytes = b'Hello, World!'
    result = parser.parse(text_bytes, **{"Content-Type": "text/plain"})
    assert result == "Hello, World!"


def test_parse_html_bytes():
    """Test parsing HTML bytes."""
    parser = TextResponseParser()
    html_bytes = b'<!DOCTYPE html><html><body><h1>Hello</h1></body></html>'
    result = parser.parse(html_bytes, **{"Content-Type": "text/html"})
    assert result == "<!DOCTYPE html><html><body><h1>Hello</h1></body></html>"


def test_parse_xml_bytes():
    """Test parsing XML bytes."""
    parser = TextResponseParser()
    xml_bytes = b'<?xml version="1.0"?><response><status>ok</status></response>'
    result = parser.parse(xml_bytes, **{"Content-Type": "application/xml"})
    assert result == '<?xml version="1.0"?><response><status>ok</status></response>'


def test_parse_empty_bytes_text():
    """Test parsing empty bytes returns empty string."""
    parser = TextResponseParser()
    result = parser.parse(b"", **{"Content-Type": "text/plain"})
    assert result == ""


def test_parse_none_bytes_text():
    """Test parsing None returns empty string."""
    parser = TextResponseParser()
    result = parser.parse(None, **{"Content-Type": "text/plain"})
    assert result == ""
