#!/usr/bin/python3.11
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved
import os
from unittest import mock
from unittest.mock import MagicMock, patch

import pytest

from openjiuwen.core.utils.tool.schema import Parameters, ToolInfo
from openjiuwen.core.utils.tool.param import Param
from openjiuwen.core.utils.tool.service_api.restful_api import RestfulApi
from openjiuwen.core.common.exception.exception import JiuWenBaseException


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
    def test_ainvoke(self, mock_request):
        mock_data = RestfulApi(
            name="test",
            description="test",
            params=[],
            path="http://127.0.0.1:8000",
            headers={},
            method="GET",
            response=[],
        )
        mock_request.return_value = dict()
        try:
            os.environ["RESTFUL_SSL_CERT"] = "temp.crt"
            mock_data.ainvoke({})
            del os.environ["RESTFUL_SSL_CERT"]
        except Exception as e:
            pass
        self.assertEqual(mock_data.headers, {})

    @patch("requests.sessions.Session.request")
    def test_invoke(self, mock_request):
        mock_data = RestfulApi(
            name="test",
            description="test",
            params=[],
            path="http://127.0.0.1:8000",
            headers={},
            method="GET",
            response=[],
        )
        mock_request.return_value = dict()
        os.environ["RESTFUL_SSL_CERT"] = "temp.crt"
        with pytest.raises(JiuWenBaseException) as e:
            mock_data.invoke({})
        assert "[182000] restful api only support ainvoke" == str(e.value)
        del os.environ["RESTFUL_SSL_CERT"]

    def test_get_tool_info(self):
        mock_data = RestfulApi(
            name="test",
            description="test",
            params=[Param("test", "test", param_type="string", default_value="123")],
            path="http://127.0.0.1:8000",
            headers={},
            method="GET",
            response=[Param("results", "test", param_type="string", default_value="456")],
        )
        res = mock_data.get_tool_info()
        too_info = ToolInfo(
            name='test',
            description='test',
            parameters=Parameters(
                type='object',
                properties={'test': {'description': 'test', 'type': 'string'}},
                required=['test']
            )
        )
        self.assertEqual(res, too_info)
