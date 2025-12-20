#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import json
import os
from typing import List

import requests
import aiohttp

from jiuwen.core.common.exception.exception import JiuWenBaseException
from jiuwen.core.common.exception.status_code import StatusCode
from jiuwen.core.common.logging import logger
from jiuwen.core.utils.common.ssl_utils import SslUtils
from jiuwen.core.utils.common.url_utils import UrlUtils
from jiuwen.core.utils.llm.messages import ToolInfo, Function
from jiuwen.core.utils.tool import constant
from jiuwen.core.utils.tool.base import Tool
from jiuwen.core.utils.tool.constant import Input, Output
from jiuwen.core.utils.tool.param import Param
from jiuwen.core.utils.tool.param_util import ParamUtil

RESTFUL_SSL_VERIFY = "RESTFUL_SSL_VERIFY"
RESTFUL_SSL_CERT = "RESTFUL_SSL_CERT"

timeout_aiohttp = aiohttp.ClientTimeout(total=constant.REQUEST_TIMEOUT)

class RestfulApi(Tool):
    def __init__(self, name: str, description: str, params: List[Param], path: str, headers: dict, method: str,
                 response: List[Param]):
        super().__init__()
        self.name = name
        self.description = description
        self.params: List[Param] = params
        self.path = path
        self.headers = headers
        self.method = method
        self.response: List[Param] = response

    def get_tool_info(self) -> ToolInfo:
        tool_info_dict = Param.format_functions(self)
        func = Function(**tool_info_dict)
        tool_info = ToolInfo(function=func)
        return tool_info

    def format_input_with_default_when_required(self, inputs: dict):
        """format input with default"""
        inputs = ParamUtil._assign_format_default_value(self.params, inputs)
        return inputs

    def get_header_params_from_input(self, inputs: dict):
        """get header params from input"""
        header_params = {}
        for param in self.params:
            if param.method == "Headers" and (inputs.get(param.name) or inputs.get(param.name) is False):
                header_params[param.name] = str(inputs.get(param.name))
                inputs.pop(param.name, None)
        return header_params

    def get_query_params_from_input(self, inputs: dict):
        """get query params from input"""
        query_params = {}
        for param in self.params:
            if inputs.get(param.name) or inputs.get(param.name) is False:
                query_params[param.name] = str(inputs.get(param.name))
                inputs.pop(param.name, None)
        return query_params

    def parse_retrieval_inputs(self, inputs: dict):
        """parse retrieval inputs"""
        if 'retrieval' in self.name:
            if 'multi_queries' not in inputs.keys():
                inputs['query'] = str(inputs.get('query'))
            else:
                for simple_input in inputs['multi_queries']:
                    simple_input['query'] = str(simple_input['query'])
        return inputs

    def invoke(self, inputs: Input, **kwargs) -> Output:
        """invoke api"""
        request_params = RequestParams(self, inputs, **kwargs)
        request_params.prepare_params()
        UrlUtils.check_url_is_valid(request_params.ip_address_url)
        ssl_verify, ssl_cert = SslUtils.get_ssl_config(RESTFUL_SSL_VERIFY, RESTFUL_SSL_CERT, ["false"])
        verify = ssl_cert if ssl_verify else False
        session = requests.Session()
        adapter = SslUtils.create_ssl_adapter(RESTFUL_SSL_VERIFY, RESTFUL_SSL_CERT, ["false"])
        if adapter is not None:
            session.mount("https://", adapter)
        try:
            response = session.request(
                self.method, request_params.ip_address_url, headers=request_params.headers,
                verify=verify, stream=False, allow_redirects=False, params=request_params.query_params_in_inputs,
                timeout=constant.REQUEST_TIMEOUT,
                **request_params.request_arg
            )
            response_data = _data_of(response)
            return response_data
        except (requests.exceptions.ReadTimeout, requests.exceptions.Timeout):
            return {
                constant.ERR_CODE: StatusCode.PLUGIN_REQUEST_TIMEOUT_ERROR.code,
                constant.ERR_MESSAGE: "plugin request time out",
                constant.RESTFUL_DATA: ""
            }
        except requests.exceptions.ProxyError:
            return {
                constant.ERR_CODE: StatusCode.PLUGIN_PROXY_CONNECT_ERROR.code,
                constant.ERR_MESSAGE: StatusCode.PLUGIN_PROXY_CONNECT_ERROR.errmsg,
                constant.RESTFUL_DATA: ""
            }
        except JiuWenBaseException as error:
            return {
                constant.ERR_CODE: error.error_code,
                constant.ERR_MESSAGE: error.message,
                constant.RESTFUL_DATA: ""
            }
        except Exception:
            return {
                constant.ERR_CODE: StatusCode.PLUGIN_UNEXPECTED_ERROR.code,
                constant.ERR_MESSAGE: "plugin request unknown error",
                constant.RESTFUL_DATA: ""
            }

    async def ainvoke(self, inputs: Input, **kwargs) -> Output:
        """async invoke api"""
        request_params = RequestParams(self, inputs, **kwargs)
        try:
            request_params.prepare_params()
            return await self._async_request(
                dict(ip_address_url=request_params.ip_address_url,
                     headers=request_params.headers,
                     request_arg=request_params.request_arg,
                     query_params_in_inputs=request_params.query_params_in_inputs)
            )
        except Exception:
            return {
                constant.ERR_CODE: StatusCode.PLUGIN_UNEXPECTED_ERROR.code,
                constant.ERR_MESSAGE: "plugin request unknown error",
                constant.RESTFUL_DATA: ""
            }

    async def _async_request(self, request_args: dict):
        ip_address_url = request_args.get('ip_address_url')
        UrlUtils.check_url_is_valid(ip_address_url)
        query_params_in_inputs = request_args.get('query_params_in_inputs')
        request_arg = request_args.get('request_arg')
        ssl_verify, ssl_cert = SslUtils.get_ssl_config(RESTFUL_SSL_VERIFY, RESTFUL_SSL_CERT, ["false"])
        if ssl_verify:
            ssl_context = SslUtils.create_strict_ssl_context(ssl_cert)
            async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=ssl_context)) as session:
                async with session.request(
                        self.method, ip_address_url, headers=request_args.get("headers"),
                        allow_redirects=False, timeout=timeout_aiohttp,
                        params=query_params_in_inputs, **request_arg
                ) as response:
                    response_data = await _data_of_async_request(response)
        else:
            async with aiohttp.ClientSession() as session:
                async with session.request(
                    self.method, ip_address_url, headers=request_args.get("headers"),
                    allow_redirects=False, timeout=timeout_aiohttp,
                    params=query_params_in_inputs, **request_arg
                ) as response:
                    response_data = await _data_of_async_request(response)
        return response_data


class RequestParams:
    """Restful API request parameters"""

    def __init__(self, restful_api: RestfulApi, inputs: Input, **kwargs):
        self.restful_api = restful_api
        self.inputs = inputs
        self.kwargs = kwargs

        inputs = restful_api.format_input_with_default_when_required(inputs)
        self.header_params_in_inputs = restful_api.get_header_params_from_input(inputs)
        self.query_params_in_inputs = restful_api.get_query_params_from_input(inputs)
        self.inputs = restful_api.parse_retrieval_inputs(inputs)

        self.method = restful_api.method.upper()
        restful_api.method = self.method

        self.ip_address_url = None
        self.headers = None
        self.request_arg = None

    def prepare_params(self):
        """prepare params"""
        restful_api = self.restful_api
        if restful_api.method not in constant.HTTP_METHOD:
            raise JiuWenBaseException(
                error_code=StatusCode.PLUGIN_UNEXPECTED_ERROR.code,
                message="the http method is not supported"
            )
        url = restful_api.path
        headers = restful_api.headers if isinstance(restful_api.headers, dict) else {}
        headers.update(self.header_params_in_inputs)
        request_arg = dict(json=self.inputs)
        self.ip_address_url = url
        self.headers = headers
        self.request_arg = request_arg


def _data_of(response):
    if response.status_code == 200:
        content = b""
        try:
            for chunk in response.iter_content(chunk_size=1024):
                content += chunk
                if len(content) > constant.MAX_RESULT_SIZE:
                    raise JiuWenBaseException(
                        error_code=StatusCode.PLUGIN_UNEXPECTED_ERROR.code,
                        message=StatusCode.PLUGIN_UNEXPECTED_ERROR.errmsg
                    )
            res = json.loads(content.decode('utf-8'))
        except json.JSONDecodeError:
            logger.error("JSON deocde failed. Raw response suppressed for security.")
            return {constant.ERR_CODE: 0, constant.ERR_MESSAGE: 'success', constant.RESTFUL_DATA: ""}
        if constant.ERR_CODE not in res or constant.ERR_MESSAGE not in res or constant.RESTFUL_DATA not in res:
            return {constant.ERR_CODE: 0, constant.ERR_MESSAGE: 'success', constant.RESTFUL_DATA: res}
        return res
    raise JiuWenBaseException(
        error_code=StatusCode.PLUGIN_RESPONSE_HTTP_CODE_ERROR.code,
        message=f"Plugin response code: {response.status_code} error."
    )


async def _data_of_async_request(response: aiohttp.ClientResponse):
    if response.status == 200:
        content = b""
        try:
            async for chunk in response.content.iter_chunked(1024):
                content += chunk
                if len(content) > constant.MAX_RESULT_SIZE:
                    raise JiuWenBaseException(
                        error_code=StatusCode.PLUGIN_RESPONSE_TOO_BIG_ERROR.code,
                        message=StatusCode.PLUGIN_RESPONSE_TOO_BIG_ERROR.errmsg
                    )
            res = json.loads(content.decode('utf-8'))
        except json.JSONDecodeError:
            return {
                constant.ERR_CODE: 0,
                constant.ERR_MESSAGE: 'success',
                constant.RESTFUL_DATA: ""
            }
        if constant.ERR_CODE not in res or constant.ERR_MESSAGE not in res or constant.RESTFUL_DATA not in res:
            return {constant.ERR_CODE: 0, constant.ERR_MESSAGE: 'success', constant.RESTFUL_DATA: res}
        return res
    raise JiuWenBaseException(
        error_code=StatusCode.PLUGIN_RESPONSE_HTTP_CODE_ERROR.code,
        message=f"Plugin response code: {response.status} error."
    )
