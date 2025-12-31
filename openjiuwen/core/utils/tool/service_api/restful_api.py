# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import asyncio
import json
from typing import List

import aiohttp

from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.common.exception.status_code import StatusCode
from openjiuwen.core.common.logging import logger
from openjiuwen.core.common.security.ssl_utils import SslUtils
from openjiuwen.core.common.security.url_utils import UrlUtils
from openjiuwen.core.utils.tool.schema import ToolInfo
from openjiuwen.core.utils.tool import constant
from openjiuwen.core.utils.tool.base import Tool
from openjiuwen.core.utils.tool.constant import Input, Output
from openjiuwen.core.utils.tool.param import Param
from openjiuwen.core.utils.tool.param_util import ParamUtil

RESTFUL_SSL_VERIFY = "RESTFUL_SSL_VERIFY"
RESTFUL_SSL_CERT = "RESTFUL_SSL_CERT"

timeout_aiohttp = aiohttp.ClientTimeout(total=constant.REQUEST_TIMEOUT)


class RestfulApi(Tool):

    def __init__(
        self,
        name: str,
        description: str,
        params: List[Param],
        path: str,
        headers: dict,
        method: str,
        response: List[Param],
        queries: dict = None,
        builtin_params: List[Param] = None,
    ):
        super().__init__()
        self.name = name
        self.description = description
        self.params: List[Param] = params
        self.path = path
        self.headers = headers
        self.method = method
        self.response: List[Param] = response
        self.builtin_params = builtin_params
        self.queries = {} if queries is None else queries

    def get_tool_info(self) -> ToolInfo:
        tool_info_dict = Param.format_functions(self)
        tool_info = ToolInfo(**tool_info_dict)
        return tool_info

    def get_header_params_from_input(self, inputs: dict):
        """get header params from input"""
        header_params = {}
        all_params = self.params + (self.builtin_params or [])
        for param in all_params:
            if param.method == "Headers" and (inputs.get(param.name) or inputs.get(param.name) is False):
                header_params[param.name] = str(inputs.get(param.name))
                inputs.pop(param.name, None)
        return header_params

    def get_query_params_from_input(self, inputs: dict):
        """get query params from input"""
        query_params = {}
        all_params = self.params + (self.builtin_params or [])
        for param in all_params:
            if param.method == "Query" and inputs.get(param.name) or inputs.get(param.name) is False:
                query_params[param.name] = str(inputs.get(param.name))
                inputs.pop(param.name, None)
        return query_params

    def get_body_params_from_input(self, inputs: dict):
        """get query params from input"""
        body_params = {}
        all_params = self.params + (self.builtin_params or [])
        for param in all_params:
            if param.method == "Body" and inputs.get(param.name) or inputs.get(param.name) is False:
                body_params[param.name] = str(inputs.get(param.name))
                inputs.pop(param.name, None)
        return body_params

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
        raise JiuWenBaseException(
            error_code=StatusCode.PLUGIN_UNEXPECTED_ERROR.code, message="restful api only support ainvoke"
        )

    async def ainvoke(self, inputs: Input, **kwargs) -> Output:
        """async invoke api"""
        request_params = RequestParams(self, inputs, **kwargs)
        try:
            request_params.prepare_params()
            return await self._async_request(
                dict(
                    ip_address_url=request_params.ip_address_url,
                    headers=request_params.headers,
                    request_arg=request_params.request_arg,
                )
            )
        except asyncio.TimeoutError:
            return {
                constant.ERR_CODE: StatusCode.PLUGIN_REQUEST_TIMEOUT_ERROR.code,
                constant.ERR_MESSAGE: StatusCode.PLUGIN_REQUEST_TIMEOUT_ERROR.errmsg,
                constant.RESTFUL_DATA: ""
            }
        except aiohttp.ClientConnectorError:
            return {
                constant.ERR_CODE: StatusCode.PLUGIN_PROXY_CONNECT_ERROR.code,
                constant.ERR_MESSAGE: StatusCode.PLUGIN_PROXY_CONNECT_ERROR.errmsg,
                constant.RESTFUL_DATA: ""
            }
        except aiohttp.ClientResponseError as e:
            return {
                constant.ERR_CODE: StatusCode.PLUGIN_RESPONSE_HTTP_CODE_ERROR.code,
                constant.ERR_MESSAGE: f"Plugin response code: {e.status} error.",
                constant.RESTFUL_DATA: ""
            }
        except aiohttp.ClientError:
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

    async def _async_request(self, request_args: dict):
        ip_address_url = request_args.get('ip_address_url')
        UrlUtils.check_url_is_valid(ip_address_url)
        url_is_https = ip_address_url.startswith("https://")
        request_arg = request_args.get('request_arg')
        ssl_verify, ssl_cert = SslUtils.get_ssl_config(RESTFUL_SSL_VERIFY, RESTFUL_SSL_CERT, ["false"],
                                                       url_is_https)
        if ssl_verify:
            ssl_context = SslUtils.create_strict_ssl_context(ssl_cert)
            connector = aiohttp.TCPConnector(ssl=ssl_context)
        else:
            connector = aiohttp.TCPConnector(ssl=False)
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.request(
                self.method,
                ip_address_url,
                headers=request_args.get("headers"),
                allow_redirects=False,
                timeout=timeout_aiohttp,
                **request_arg,
            ) as response:
                response_data = await _data_of_async_request(response)
        return response_data


class RequestParams:
    """Restful API request parameters"""

    def __init__(self, restful_api: RestfulApi, inputs: Input, **kwargs):
        self.restful_api = restful_api
        self.inputs = inputs
        self.kwargs = kwargs

        self.method = restful_api.method.upper()
        restful_api.method = self.method
        for param in self.restful_api.params:
            if self.method == "GET":
                param.method = "Query" if param.method == "" else param.method
            if self.method == "POST":
                param.method = "Body" if param.method == "" else param.method

        inputs = ParamUtil.format_input_with_default_when_required(self.restful_api.params, inputs)
        if self.restful_api.builtin_params:
            inputs = ParamUtil.format_input_with_default_when_required(self.restful_api.builtin_params, inputs)
        self.header_params_in_inputs = restful_api.get_header_params_from_input(inputs)
        self.query_params_in_inputs = restful_api.get_query_params_from_input(inputs)
        self.body_params_in_inputs = restful_api.get_body_params_from_input(inputs)
        self.inputs = restful_api.parse_retrieval_inputs(inputs)

        self.ip_address_url = None
        self.headers = None
        self.request_arg = None

    def prepare_params(self):
        """prepare params"""
        restful_api = self.restful_api
        if restful_api.method not in ["GET", "POST"]:
            raise JiuWenBaseException(
                error_code=StatusCode.PLUGIN_UNEXPECTED_ERROR.code, message="the http method is not supported"
            )
        url = restful_api.path
        headers = restful_api.headers if isinstance(restful_api.headers, dict) else {}
        headers.update(self.header_params_in_inputs)
        request_arg = dict(json=self.inputs)
        self.ip_address_url = url
        self.headers = headers
        queries = restful_api.queries if isinstance(restful_api.queries, dict) else {}
        queries.update(self.query_params_in_inputs)
        self.request_arg = request_arg
        self.request_arg["params"] = queries
        self.request_arg["json"] = self.body_params_in_inputs


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
