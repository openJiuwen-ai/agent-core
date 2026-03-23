# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import math

import pytest

from openjiuwen.dev_tools.agent_builder.utils.constants import (
    API_BASE_PATH,
    API_VERSION,
    DEFAULT_MAX_HISTORY_SIZE,
    DEFAULT_MAX_RETRIES,
    DEFAULT_TIMEOUT,
    GENERATE_DL_FROM_DESIGN_CONTENT,
    JSON_EXTRACT_PATTERN,
    MAX_HISTORY_SIZE,
    MAX_QUERY_LENGTH,
    MAX_SESSION_ID_LENGTH,
    MIN_HISTORY_SIZE,
    MIN_QUERY_LENGTH,
    MODIFY_DL_CONTENT,
    PROGRESS_HEARTBEAT_INTERVAL,
    PROGRESS_UPDATE_INTERVAL,
    RESOURCE_TYPE_KNOWLEDGE,
    RESOURCE_TYPE_PLUGIN,
    RESOURCE_TYPE_WORKFLOW,
    WORKFLOW_DESIGN_RESPONSE_CONTENT,
    WORKFLOW_REQUEST_CONTENT,
)


class TestWorkflowConstants:
    @staticmethod
    def test_workflow_request_content():
        assert isinstance(WORKFLOW_REQUEST_CONTENT, str)
        assert len(WORKFLOW_REQUEST_CONTENT) > 0
        assert "workflow" in WORKFLOW_REQUEST_CONTENT.lower()

    @staticmethod
    def test_workflow_design_response_content():
        assert isinstance(WORKFLOW_DESIGN_RESPONSE_CONTENT, str)
        assert "Workflow design content" in WORKFLOW_DESIGN_RESPONSE_CONTENT

    @staticmethod
    def test_generate_dl_from_design_content():
        assert isinstance(GENERATE_DL_FROM_DESIGN_CONTENT, str)
        assert "Process Definition Language" in GENERATE_DL_FROM_DESIGN_CONTENT

    @staticmethod
    def test_modify_dl_content():
        assert isinstance(MODIFY_DL_CONTENT, str)
        assert "correct" in MODIFY_DL_CONTENT.lower()


class TestDefaultConfiguration:
    @staticmethod
    def test_default_max_history_size():
        assert isinstance(DEFAULT_MAX_HISTORY_SIZE, int)
        assert DEFAULT_MAX_HISTORY_SIZE == 50

    @staticmethod
    def test_default_max_retries():
        assert isinstance(DEFAULT_MAX_RETRIES, int)
        assert DEFAULT_MAX_RETRIES == 3

    @staticmethod
    def test_default_timeout():
        assert isinstance(DEFAULT_TIMEOUT, int)
        assert DEFAULT_TIMEOUT == 30


class TestResourceTypes:
    @staticmethod
    def test_resource_type_plugin():
        assert RESOURCE_TYPE_PLUGIN == "plugin"

    @staticmethod
    def test_resource_type_knowledge():
        assert RESOURCE_TYPE_KNOWLEDGE == "knowledge"

    @staticmethod
    def test_resource_type_workflow():
        assert RESOURCE_TYPE_WORKFLOW == "workflow"


class TestRegexPatterns:
    @staticmethod
    def test_json_extract_pattern():
        import re
        assert isinstance(JSON_EXTRACT_PATTERN, str)
        pattern = re.compile(JSON_EXTRACT_PATTERN)
        
        test_cases = [
            ("```json\n{\"key\": \"value\"}\n```", "{\"key\": \"value\"}"),
            ("```\n{\"key\": \"value\"}\n```", "{\"key\": \"value\"}"),
            ("```json\n[1, 2, 3]\n```", "[1, 2, 3]"),
        ]
        for text, expected in test_cases:
            matches = pattern.findall(text)
            assert len(matches) > 0
            assert matches[0].strip() == expected.strip()


class TestApiConstants:
    @staticmethod
    def test_api_version():
        assert API_VERSION == "v1"

    @staticmethod
    def test_api_base_path():
        assert API_BASE_PATH == "/api/v1"


class TestProgressConstants:
    @staticmethod
    def test_progress_update_interval():
        assert isinstance(PROGRESS_UPDATE_INTERVAL, float)
        assert math.isclose(PROGRESS_UPDATE_INTERVAL, 0.1)

    @staticmethod
    def test_progress_heartbeat_interval():
        assert isinstance(PROGRESS_HEARTBEAT_INTERVAL, float)
        assert math.isclose(PROGRESS_HEARTBEAT_INTERVAL, 30.0)


class TestLimitConstants:
    @staticmethod
    def test_max_query_length():
        assert isinstance(MAX_QUERY_LENGTH, int)
        assert MAX_QUERY_LENGTH == 5000

    @staticmethod
    def test_min_query_length():
        assert isinstance(MIN_QUERY_LENGTH, int)
        assert MIN_QUERY_LENGTH == 1

    @staticmethod
    def test_max_session_id_length():
        assert isinstance(MAX_SESSION_ID_LENGTH, int)
        assert MAX_SESSION_ID_LENGTH == 255

    @staticmethod
    def test_max_history_size():
        assert isinstance(MAX_HISTORY_SIZE, int)
        assert MAX_HISTORY_SIZE == 1000

    @staticmethod
    def test_min_history_size():
        assert isinstance(MIN_HISTORY_SIZE, int)
        assert MIN_HISTORY_SIZE == 1

    @staticmethod
    def test_length_constraints_valid():
        assert MIN_QUERY_LENGTH <= MAX_QUERY_LENGTH
        assert MIN_HISTORY_SIZE <= MAX_HISTORY_SIZE
