#!/usr/bin/python3.11
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved
import os
from unittest import mock
from unittest.mock import MagicMock, patch

import pytest

from openjiuwen.core.foundation.tool import ToolInfo, RestfulApiCard
from openjiuwen.core.foundation.tool.service_api.restful_api import RestfulApi
from openjiuwen.core.common.exception.exception import JiuWenBaseException


@pytest.mark.asyncio
class TestRestFulApi:
    def assertEqual(self, left, right):
        assert left == right

    @pytest.fixture(autouse=True)
    def setUp(self):
        response_mock = MagicMock()
        response_mock.status_code = 200
        response_mock.text = "{}"
        response_mock.content = b"{}"
        self.mocked_functions = mock.patch.multiple(
            "requests",
            request=mock.MagicMock(return_value=response_mock)
        )
        self.mocked_functions.start()

    def tearDown(self):
        self.mocked_functions.stop()

    @patch('requests.sessions.Session.request')
    async def test_invoke(self, mock_request):
        mock_data = RestfulApi(
            card=RestfulApiCard(
                name="test",
                description="test",
                path="http://127.0.0.1:8000",
                headers={},
                method="GET",
            ),
        )
        mock_request.return_value = dict()
        try:
            os.environ["RESTFUL_SSL_CERT"] = "temp.crt"
            await mock_data.invoke({})
            del os.environ["RESTFUL_SSL_CERT"]
        except Exception as e:
            pass
        self.assertEqual(mock_data._headers, {})

    @patch("requests.sessions.Session.request")
    async def test_stream(self, mock_request):
        mock_data = RestfulApi(
            card=RestfulApiCard(
                name="test",
                description="test",
                path="http://127.0.0.1:8000",
                headers={},
                method="GET",
            ),
        )
        mock_request.return_value = dict()
        os.environ["RESTFUL_SSL_CERT"] = "temp.crt"
        with pytest.raises(JiuWenBaseException) as e:
            await mock_data.stream({})
        assert "[182000] Restful api not support stream mode" == str(e.value)
        del os.environ["RESTFUL_SSL_CERT"]

    def test_get_tool_info(self):
        mock_data = RestfulApi(
            card=RestfulApiCard(
                name="test",
                description="test",
                input_params={
                    "type": "object",
                    "properties": {
                        "test": {"description": "test", "type": "string", "default": "123"},
                    },
                    "required": ["test"],
                },
                path="http://127.0.0.1:8000",
                headers={},
                method="GET",
            ),
        )
        res = mock_data.card.tool_info()
        too_info = ToolInfo(
            name="test",
            description="test",
            parameters={
                "type": "object",
                "properties": {"test": {"description": "test", "type": "string", "default": "123"}},
                "required": ["test"],
            },
        )
        self.assertEqual(res, too_info)
