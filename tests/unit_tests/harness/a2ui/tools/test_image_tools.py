# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Unit tests for openjiuwen.harness.a2ui.tools.image_tools."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from openjiuwen.harness.a2ui.tools import image_tools


def _config_get(values):
    return lambda key, default=None: values.get(key, default)


class TestFetchPageImage:
    @pytest.mark.asyncio
    async def test_returns_image_url_on_first_success(self):
        with patch.object(image_tools, "_fetch_page_once", AsyncMock(return_value="https://example.com/img.jpg")):
            result = await image_tools.fetch_page_image.invoke({"url": "https://example.com"})
        assert result == {"url": "https://example.com", "image_url": "https://example.com/img.jpg"}

    @pytest.mark.asyncio
    async def test_returns_none_image_without_retry_when_page_has_no_image(self):
        mock_fetch = AsyncMock(return_value=None)
        with patch.object(image_tools, "_fetch_page_once", mock_fetch):
            result = await image_tools.fetch_page_image.invoke({"url": "https://example.com"})
        assert result["image_url"] is None
        mock_fetch.assert_awaited_once()  # a clean "no image" result is not retried

    @pytest.mark.asyncio
    async def test_retries_transient_failures_up_to_the_cap(self):
        mock_fetch = AsyncMock(
            side_effect=[
                image_tools._RetryableFetchError("timeout"),
                image_tools._RetryableFetchError("timeout"),
                "https://example.com/img.jpg",
            ]
        )
        with patch.object(image_tools, "_fetch_page_once", mock_fetch), patch("asyncio.sleep", AsyncMock()):
            result = await image_tools.fetch_page_image.invoke({"url": "https://example.com"})
        assert result["image_url"] == "https://example.com/img.jpg"
        assert mock_fetch.await_count == 3

    @pytest.mark.asyncio
    async def test_gives_up_after_max_attempts_and_reports_error(self):
        mock_fetch = AsyncMock(side_effect=image_tools._RetryableFetchError("still down"))
        with patch.object(image_tools, "_fetch_page_once", mock_fetch), patch("asyncio.sleep", AsyncMock()):
            result = await image_tools.fetch_page_image.invoke({"url": "https://example.com"})
        assert result["image_url"] is None
        assert "still down" in result["error"]
        assert mock_fetch.await_count == image_tools.MAX_IMAGE_FETCH_ATTEMPTS


class TestSearchImages:
    @pytest.mark.asyncio
    async def test_returns_error_when_api_key_not_configured(self):
        with patch.object(image_tools.config, "get", side_effect=_config_get({})):
            result = await image_tools.search_images.invoke({"query": "Shanghai skyline"})
        assert result["images"] == []
        assert "SERPAPI_API_KEY" in result["error"]

    @pytest.mark.asyncio
    async def test_prefers_thumbnail_over_original(self):
        # `thumbnail` (a small, fast, reliably-hotlinkable Google-served image) is
        # what actually gets embedded -- `original` often points straight at a
        # multi-megabyte source file that's slow or blocked when hotlinked.
        body = json.dumps(
            {
                "images_results": [
                    {
                        "original": "https://example.com/full-res.jpg",
                        "thumbnail": "https://encrypted-tbn0.gstatic.com/images?q=abc",
                        "title": "The Bund",
                        "source": "example.com",
                    }
                ]
            }
        ).encode("utf-8")
        mock_request = AsyncMock(return_value=(200, {"Content-Type": "application/json"}, body, "url", False))
        with (
            patch.object(image_tools.config, "get", side_effect=_config_get({"SERPAPI_API_KEY": "test-key"})),
            patch.object(image_tools._http, "request", mock_request),
        ):
            result = await image_tools.search_images.invoke({"query": "The Bund"})
        assert result["images"][0]["image_url"] == "https://encrypted-tbn0.gstatic.com/images?q=abc"

    @pytest.mark.asyncio
    async def test_falls_back_to_original_when_no_thumbnail(self):
        body = json.dumps(
            {
                "images_results": [
                    {"original": "https://example.com/a.jpg", "title": "The Bund", "source": "example.com"},
                    {"original": "https://example.com/b.jpg", "title": "Yu Garden", "source": "example.com"},
                ]
            }
        ).encode("utf-8")
        mock_request = AsyncMock(return_value=(200, {"Content-Type": "application/json"}, body, "url", False))
        with (
            patch.object(image_tools.config, "get", side_effect=_config_get({"SERPAPI_API_KEY": "test-key"})),
            patch.object(image_tools._http, "request", mock_request),
        ):
            result = await image_tools.search_images.invoke({"query": "Shanghai skyline"})
        assert result["query"] == "Shanghai skyline"
        assert result["images"] == [
            {"image_url": "https://example.com/a.jpg", "title": "The Bund", "source": "example.com"},
            {"image_url": "https://example.com/b.jpg", "title": "Yu Garden", "source": "example.com"},
        ]

    @pytest.mark.asyncio
    async def test_caps_results_at_max_results(self):
        items = [{"original": f"https://example.com/{i}.jpg", "title": str(i)} for i in range(10)]
        body = json.dumps({"images_results": items}).encode("utf-8")
        mock_request = AsyncMock(return_value=(200, {"Content-Type": "application/json"}, body, "url", False))
        with (
            patch.object(image_tools.config, "get", side_effect=_config_get({"SERPAPI_API_KEY": "test-key"})),
            patch.object(image_tools._http, "request", mock_request),
        ):
            result = await image_tools.search_images.invoke({"query": "q", "max_results": 3})
        assert len(result["images"]) == 3

    @pytest.mark.asyncio
    async def test_http_error_returns_error_field(self):
        mock_request = AsyncMock(return_value=(500, {}, b"", "url", False))
        with (
            patch.object(image_tools.config, "get", side_effect=_config_get({"SERPAPI_API_KEY": "test-key"})),
            patch.object(image_tools._http, "request", mock_request),
        ):
            result = await image_tools.search_images.invoke({"query": "q"})
        assert result["images"] == []
        assert "500" in result["error"]

    @pytest.mark.asyncio
    async def test_optional_filters_are_translated_into_request_params(self):
        body = json.dumps({"images_results": []}).encode("utf-8")
        mock_request = AsyncMock(return_value=(200, {"Content-Type": "application/json"}, body, "url", False))
        with (
            patch.object(image_tools.config, "get", side_effect=_config_get({"SERPAPI_API_KEY": "test-key"})),
            patch.object(image_tools._http, "request", mock_request),
        ):
            await image_tools.search_images.invoke(
                {"query": "q", "aspect_ratio": "wide", "size": "large", "image_color": "bw", "image_type": "photo"}
            )
        requested_url = mock_request.await_args.args[2]
        assert "imgar=w" in requested_url
        assert "imgsz=l" in requested_url
        assert "image_color=bw" in requested_url
        assert "image_type=photo" in requested_url
