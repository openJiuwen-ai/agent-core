# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for graph_memory validate_input"""

import pytest

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.memory.config.graph import EpisodeType
from openjiuwen.core.memory.graph.graph_memory.validate_input import (
    validate_add_memory_input,
    validate_search_input,
)


class TestValidateAddMemoryInput:
    """Tests for validate_add_memory_input"""

    @staticmethod
    def test_valid_input_passes():
        """Valid src_type, user_id and optional content_fmt_kwargs pass"""
        validate_add_memory_input(
            user_id_max_length=32,
            src_type=EpisodeType.CONVERSATION,
            user_id="user-1",
            content_fmt_kwargs=None,
        )
        validate_add_memory_input(
            user_id_max_length=32,
            src_type=EpisodeType.DOCUMENT,
            user_id="u",
            content_fmt_kwargs={"user": "User", "assistant": "Assistant"},
        )

    @staticmethod
    def test_content_fmt_kwargs_empty_dict_raises():
        """Empty content_fmt_kwargs dict raises"""
        with pytest.raises(BaseError, match="content_fmt_kwargs"):
            validate_add_memory_input(
                user_id_max_length=32,
                src_type=EpisodeType.CONVERSATION,
                user_id="user-1",
                content_fmt_kwargs={},
            )

    @staticmethod
    def test_content_fmt_kwargs_not_dict_raises():
        """Non-dict content_fmt_kwargs raises"""
        with pytest.raises(BaseError, match="content_fmt_kwargs"):
            validate_add_memory_input(
                user_id_max_length=32,
                src_type=EpisodeType.CONVERSATION,
                user_id="user-1",
                content_fmt_kwargs="not a dict",
            )

    @staticmethod
    def test_content_fmt_kwargs_non_string_values_raise():
        """content_fmt_kwargs with non-string key or value raises"""
        with pytest.raises(BaseError, match="non-empty keys and values"):
            validate_add_memory_input(
                user_id_max_length=32,
                src_type=EpisodeType.CONVERSATION,
                user_id="user-1",
                content_fmt_kwargs={"user": 123},
            )
        with pytest.raises(BaseError, match="non-empty keys and values"):
            validate_add_memory_input(
                user_id_max_length=32,
                src_type=EpisodeType.CONVERSATION,
                user_id="user-1",
                content_fmt_kwargs={"": "Assistant"},
            )

    @staticmethod
    def test_src_type_not_episode_type_raises():
        """Invalid src_type raises"""
        with pytest.raises(BaseError, match="src_type"):
            validate_add_memory_input(
                user_id_max_length=32,
                src_type="conversation",
                user_id="user-1",
            )

    @staticmethod
    def test_user_id_empty_raises():
        """Empty or too long user_id raises"""
        with pytest.raises(BaseError, match="user_id"):
            validate_add_memory_input(
                user_id_max_length=32,
                src_type=EpisodeType.CONVERSATION,
                user_id="",
            )
        with pytest.raises(BaseError, match="user_id"):
            validate_add_memory_input(
                user_id_max_length=32,
                src_type=EpisodeType.CONVERSATION,
                user_id="   ",
            )
        with pytest.raises(BaseError, match="user_id"):
            validate_add_memory_input(
                user_id_max_length=5,
                src_type=EpisodeType.CONVERSATION,
                user_id="long-user-id",
            )

    @staticmethod
    def test_user_id_not_string_raises():
        """user_id not a string raises"""
        with pytest.raises(BaseError, match="user_id"):
            validate_add_memory_input(
                user_id_max_length=32,
                src_type=EpisodeType.CONVERSATION,
                user_id=123,
            )


class TestValidateSearchInput:
    """Tests for validate_search_input"""

    @staticmethod
    def test_valid_query_and_user_id_returns_list():
        """Valid query and single user_id returns list of one"""
        result = validate_search_input("hello", "user-1", [True, True, True])
        assert result == ["user-1"]

    @staticmethod
    def test_user_id_list_returned_as_is():
        """List of user_ids is returned as-is (validated)"""
        result = validate_search_input("q", ["u1", "u2"], [True, False, True])
        assert result == ["u1", "u2"]

    @staticmethod
    def test_empty_query_raises():
        """Empty or whitespace query raises"""
        with pytest.raises(BaseError, match="query"):
            validate_search_input("", "user-1", [True, True, True])
        with pytest.raises(BaseError, match="query"):
            validate_search_input("   ", "user-1", [True, True, True])

    @staticmethod
    def test_query_not_string_raises():
        """Query not a string raises"""
        with pytest.raises(BaseError, match="query"):
            validate_search_input(123, "user-1", [True, True, True])

    @staticmethod
    def test_user_id_invalid_raises():
        """Invalid user_id (empty, too long, not string) raises"""
        with pytest.raises(BaseError, match="user_id"):
            validate_search_input("q", "", [True, True, True])
        with pytest.raises(BaseError, match="user_id"):
            validate_search_input("q", "x" * 33, [True, True, True])
        with pytest.raises(BaseError, match="user_id"):
            validate_search_input("q", ["valid", ""], [True, True, True])

    @staticmethod
    def test_settings_not_all_bool_raises():
        """settings (entity, relation, episode) must be booleans"""
        with pytest.raises(BaseError, match="boolean"):
            validate_search_input("q", "user-1", [True, 1, True])
