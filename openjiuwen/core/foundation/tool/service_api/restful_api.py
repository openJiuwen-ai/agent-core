# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import AsyncIterator, Dict, Any
from pydantic import Field
import aiohttp
import json
import asyncio

from openjiuwen.core.common.utils.schema_utils import SchemaUtils
from openjiuwen.core.foundation.tool import Tool
from openjiuwen.core.foundation.tool.base import Input, Output, ToolCard
from openjiuwen.core.common.exception.status_code import StatusCode
from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.common.security.ssl_utils import SslUtils
from openjiuwen.core.common.security.url_utils import UrlUtils


class RestfulApiCard(ToolCard):
    path: str = Field(..., description="restful api path, such as: /api/v1/users")
    method: str = Field(default="POST", description="http method")
    headers: Dict[str, Any] = Field(default_factory=dict, description="request headers")
    timeout: float = Field(default=60, ge=1, le=300, description="request timeout: second")
    response_batch_bytes_size: int = Field(default=10 * 1024 * 1024, ge=1024)


class RestfulApi(Tool):
    _RESTFUL_SSL_VERIFY = "RESTFUL_SSL_VERIFY"
    _RESTFUL_SSL_CERT = "RESTFUL_SSL_CERT"

    def __init__(self, card: RestfulApiCard):
        super().__init__(card)
        self._path = card.path
        self._method = card.method
        self._headers = card.headers
        self._timeout = card.timeout
        self._response_batch_size = card.response_batch_bytes_size

    async def _async_request(self, request_arg: dict):
        UrlUtils.check_url_is_valid(self._path)
        ssl_verify, ssl_cert = SslUtils.get_ssl_config(self._RESTFUL_SSL_VERIFY, self._RESTFUL_SSL_CERT, ["false"])
        if ssl_verify:
            ssl_context = SslUtils.create_strict_ssl_context(ssl_cert)
            connector = aiohttp.TCPConnector(ssl=ssl_context)
        else:
            connector = aiohttp.TCPConnector(ssl=False)
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.request(
                    self._method,
                    self._path,
                    headers=self._headers,
                    allow_redirects=False,
                    timeout=aiohttp.ClientTimeout(total=self._timeout),
                    **request_arg,
            ) as response:
                response_data = await self._data_of_async_request(response, self._response_batch_size)
        return response_data

    async def invoke(self, inputs: Input, **kwargs) -> Output:
        try:
            if not kwargs.get("skip_inputs_validate"):
                inputs = SchemaUtils.format_with_schema(inputs, self._card.input_params,
                                                        kwargs.get("skip_none_value", False))
            request_arg = {}
            if self._method in ["GET"]:
                request_arg["params"] = {k: str(v) for k, v in inputs.items()}
            elif self._method in ["POST"]:
                request_arg["json"] = {k: str(v) for k, v in inputs.items()}
            else:
                raise JiuWenBaseException(
                    error_code=StatusCode.PLUGIN_UNEXPECTED_ERROR.code, message="the http method is not supported"
                )
            return await self._async_request(request_arg)
        except (aiohttp.ClientTimeout, asyncio.TimeoutError):
            raise JiuWenBaseException(
                error_code=StatusCode.PLUGIN_REQUEST_TIMEOUT_ERROR.code,
                message=StatusCode.PLUGIN_REQUEST_TIMEOUT_ERROR.errmsg,
            )
        except aiohttp.ClientConnectorError:
            raise JiuWenBaseException(
                error_code=StatusCode.PLUGIN_PROXY_CONNECT_ERROR.code,
                message=StatusCode.PLUGIN_PROXY_CONNECT_ERROR.errmsg,
            )
        except aiohttp.ClientResponseError as e:
            raise JiuWenBaseException(
                error_code=StatusCode.PLUGIN_RESPONSE_HTTP_CODE_ERROR.code,
                message=f"Plugin response code: {e.status} error.",
            )
        except aiohttp.ClientError:
            raise JiuWenBaseException(
                error_code=StatusCode.PLUGIN_PROXY_CONNECT_ERROR.code,
                message=StatusCode.PLUGIN_PROXY_CONNECT_ERROR.errmsg,
            )
        except JiuWenBaseException as error:
            raise JiuWenBaseException(error_code=error.error_code, message=error.message)
        except Exception:
            raise JiuWenBaseException(
                error_code=StatusCode.PLUGIN_UNEXPECTED_ERROR.code, message="plugin request unknown error"
            )

    async def stream(self, inputs: Input, **kwargs) -> AsyncIterator[Output]:
        raise JiuWenBaseException(
            error_code=StatusCode.PLUGIN_UNEXPECTED_ERROR.code,
            message=f"Restful api not support stream mode",
        )

    @staticmethod
    async def _data_of_async_request(response: aiohttp.ClientResponse, response_batch_size):
        if response.status == 200:
            content = b""
            try:
                async for chunk in response.content.iter_chunked(1024):
                    content += chunk
                    if len(content) > response_batch_size:
                        raise JiuWenBaseException(
                            error_code=StatusCode.PLUGIN_RESPONSE_TOO_BIG_ERROR.code,
                            message=StatusCode.PLUGIN_RESPONSE_TOO_BIG_ERROR.errmsg,
                        )
                res = json.loads(content.decode("utf-8"))
                return res
            except json.JSONDecodeError:
                raise JiuWenBaseException(
                    error_code=StatusCode.PLUGIN_UNEXPECTED_ERROR.code,
                    message=f"Plugin response decode error",
                )
        raise JiuWenBaseException(
            error_code=StatusCode.PLUGIN_RESPONSE_HTTP_CODE_ERROR.code,
            message=f"Plugin response code: {response.status} error.",
        )
