#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.


class JiuWenBaseException(Exception):
    def __init__(self, error_code: int, message: str) -> None:
        super().__init__(error_code, message)
        self._error_code = error_code
        self._message = message

    def __str__(self):
        return f"[{self._error_code}] {self._message}"

    @property
    def error_code(self) -> int:
        return self._error_code

    @property
    def message(self) -> str:
        return self._message


class InterruptException(JiuWenBaseException):
    def __init__(self, error_code: int, message: str):
        super().__init__(error_code, message)
