# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for agent_teams.timefmt time-context rendering."""

import re

import pytest

from openjiuwen.agent_teams.i18n import get_language, set_language
from openjiuwen.agent_teams.timefmt import _relative_key_and_value, format_time_context

# Fixed anchor (2023-11-14 22:13:20 UTC) so absolute-time assertions are stable.
_NOW_MS = 1_700_000_000_000

_SECOND = 1000
_MINUTE = 60 * _SECOND
_HOUR = 60 * _MINUTE
_DAY = 24 * _HOUR


@pytest.fixture
def restore_language():
    """Restore the process language after a test mutates it."""
    previous = get_language()
    yield
    set_language(previous)


@pytest.mark.level0
def test_relative_key_and_value_buckets():
    """Bucket selection is pure numeric logic, independent of language."""
    assert _relative_key_and_value(-5 * _SECOND) == ("time.just_now", None)
    assert _relative_key_and_value(0) == ("time.just_now", None)
    assert _relative_key_and_value(9 * _SECOND) == ("time.just_now", None)
    assert _relative_key_and_value(10 * _SECOND) == ("time.seconds_ago", 10)
    assert _relative_key_and_value(59 * _SECOND) == ("time.seconds_ago", 59)
    assert _relative_key_and_value(_MINUTE) == ("time.minutes_ago", 1)
    assert _relative_key_and_value(3599 * _SECOND) == ("time.minutes_ago", 59)
    assert _relative_key_and_value(_HOUR) == ("time.hours_ago", 1)
    assert _relative_key_and_value(86399 * _SECOND) == ("time.hours_ago", 23)
    assert _relative_key_and_value(_DAY) == ("time.days_ago", 1)
    assert _relative_key_and_value(10 * _DAY) == ("time.days_ago", 10)


@pytest.mark.level0
def test_format_time_context_just_now_cn(restore_language):
    """Sub-threshold deltas collapse to the localized 'just now' bucket."""
    set_language("cn")
    out = format_time_context(_NOW_MS - 3 * _SECOND, _NOW_MS)
    assert "刚刚" in out


@pytest.mark.level0
def test_format_time_context_minutes_ago_en(restore_language):
    """Minute-bucket diffs render the English relative wording."""
    set_language("en")
    out = format_time_context(_NOW_MS - 3 * _MINUTE, _NOW_MS)
    assert "3m ago" in out


@pytest.mark.level0
def test_format_time_context_hours_and_days(restore_language):
    """Hour and day buckets pick the right localized unit."""
    set_language("cn")
    assert "2 小时前" in format_time_context(_NOW_MS - 2 * _HOUR, _NOW_MS)
    assert "5 天前" in format_time_context(_NOW_MS - 5 * _DAY, _NOW_MS)


@pytest.mark.level0
def test_format_time_context_future_clock_skew(restore_language):
    """A future timestamp (clock skew) never renders a negative diff."""
    set_language("en")
    out = format_time_context(_NOW_MS + _MINUTE, _NOW_MS)
    assert "just now" in out


@pytest.mark.level0
def test_format_time_context_none_is_unknown(restore_language):
    """A missing timestamp renders the localized 'unknown' string."""
    set_language("en")
    assert format_time_context(None, _NOW_MS) == "unknown time"
    set_language("cn")
    assert format_time_context(None, _NOW_MS) == "时间未知"


@pytest.mark.level0
def test_format_time_context_absolute_has_offset(restore_language):
    """The absolute part carries a date and a numeric ``±HH:MM`` tz offset."""
    set_language("en")
    out = format_time_context(_NOW_MS, _NOW_MS)
    # Date prefix is stable regardless of local timezone (UTC is 2023-11-14,
    # local offsets keep it on 2023-11-14 or 2023-11-15).
    assert "2023-11-1" in out
    assert re.search(r"[+-]\d{2}:\d{2}", out) is not None
