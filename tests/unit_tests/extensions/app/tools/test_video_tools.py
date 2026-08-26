# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Unit tests for openjiuwen.extensions.app.tools.video_tools."""

from unittest.mock import AsyncMock, patch

import pytest

from openjiuwen.extensions.app.tools import video_tools


class TestExtractYoutubeId:
    @pytest.mark.parametrize(
        "url",
        [
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://youtu.be/dQw4w9WgXcQ?si=abc",
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=xyz",
            "https://www.youtube.com/shorts/dQw4w9WgXcQ",
            "https://www.youtube.com/embed/dQw4w9WgXcQ",
            "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ",
        ],
    )
    def test_extracts_id_from_known_url_shapes(self, url):
        assert video_tools._extract_youtube_id(url) == "dQw4w9WgXcQ"

    def test_returns_none_for_non_youtube_url(self):
        assert video_tools._extract_youtube_id("https://example.com/not-youtube") is None


class TestFetchVideoSource:
    @pytest.mark.asyncio
    async def test_youtube_url_resolves_without_fetching_the_page(self):
        with patch.object(video_tools, "_extract_page_video", AsyncMock()) as mock_extract:
            result = await video_tools.fetch_video_source.invoke(
                {"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}
            )
        mock_extract.assert_not_called()
        assert result["kind"] == "youtube"
        assert result["embed_url"] == "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ?rel=0"

    @pytest.mark.asyncio
    async def test_direct_video_url_found_on_page(self):
        with patch.object(
            video_tools, "_extract_page_video", AsyncMock(return_value="https://example.com/clip.mp4")
        ):
            result = await video_tools.fetch_video_source.invoke({"url": "https://example.com/page"})
        assert result == {"url": "https://example.com/page", "kind": "direct", "video_url": "https://example.com/clip.mp4"}

    @pytest.mark.asyncio
    async def test_no_video_found_returns_null_kind(self):
        with patch.object(video_tools, "_extract_page_video", AsyncMock(return_value=None)):
            result = await video_tools.fetch_video_source.invoke({"url": "https://example.com/page"})
        assert result["kind"] is None
        assert result["video_url"] is None

    @pytest.mark.asyncio
    async def test_retries_transient_failures_up_to_the_cap(self):
        mock_fetch = AsyncMock(
            side_effect=[
                video_tools._RetryableFetchError("timeout"),
                video_tools._RetryableFetchError("timeout"),
                "https://example.com/clip.mp4",
            ]
        )
        with patch.object(video_tools, "_fetch_page_video_once", mock_fetch), patch("asyncio.sleep", AsyncMock()):
            result = await video_tools.fetch_video_source.invoke({"url": "https://example.com/page"})
        assert result["video_url"] == "https://example.com/clip.mp4"
        assert mock_fetch.await_count == 3

    @pytest.mark.asyncio
    async def test_gives_up_after_max_attempts_and_reports_error(self):
        mock_fetch = AsyncMock(side_effect=video_tools._RetryableFetchError("still down"))
        with patch.object(video_tools, "_fetch_page_video_once", mock_fetch), patch("asyncio.sleep", AsyncMock()):
            result = await video_tools.fetch_video_source.invoke({"url": "https://example.com/page"})
        assert result["video_url"] is None
        assert "still down" in result["error"]
        assert mock_fetch.await_count == video_tools.MAX_VIDEO_FETCH_ATTEMPTS


class TestShowVideoClips:
    @pytest.mark.asyncio
    async def test_renders_youtube_and_direct_clips(self):
        result = await video_tools.show_video_clips.invoke(
            {
                "title": "Melaka clips",
                "clips": [
                    {
                        "caption": "Clip A",
                        "kind": "youtube",
                        "embed_url": "https://www.youtube-nocookie.com/embed/abc",
                    },
                    {"caption": "Clip B", "kind": "direct", "video_url": "https://example.com/b.mp4"},
                ],
            }
        )
        assert "Clip A" in result["text"]
        assert "Clip B" in result["text"]
        components = {c["id"]: c for c in result["genui"][1]["updateComponents"]["components"]}
        assert components["clip0Media"]["component"] == "YouTubeWeb"
        assert components["clip1Media"]["component"] == "Video"

    @pytest.mark.asyncio
    async def test_no_resolved_clips_returns_no_genui(self):
        result = await video_tools.show_video_clips.invoke(
            {"title": "Melaka clips", "clips": [{"caption": "Unresolved", "kind": "youtube"}]}
        )
        assert result["genui"] == []
