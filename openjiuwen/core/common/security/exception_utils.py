#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from pydantic import ValidationError

from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.common.exception.status_code import StatusCode


class ExceptionUtils:
    @staticmethod
    def raise_exception(error_code: StatusCode, error_msg: str = "", exception: Exception = None):
        if exception is not None:
            raise JiuWenBaseException(error_code=error_code.code,
                                      message=error_code.errmsg.format(error_msg=error_msg)) from exception
        else:
            raise JiuWenBaseException(error_code=error_code.code, message=error_code.errmsg.format(error_msg=error_msg))

    @staticmethod
    def format_validation_error(e: ValidationError) -> str:
        return "\n".join([f"{'.'.join(map(str, err.get('loc', [])))}: {err.get('msg', 'Unknown error')}"
                          for err in e.errors()
                          ])
