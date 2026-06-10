# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import contextvars

_REQUEST_ID_CTX_VAR: contextvars.ContextVar[str] = contextvars.ContextVar(
    "_REQUEST_ID_CTX_VAR", default=""
)


def get_request_id() -> str:
    """获取当前请求的 request_id（从 context variable）。"""
    return _REQUEST_ID_CTX_VAR.get()


def set_request_id(rid: str) -> contextvars.Token:
    """设置当前请求的 request_id（返回 token 用于 reset）。"""
    return _REQUEST_ID_CTX_VAR.set(rid or "")


def reset_request_id(token: contextvars.Token) -> None:
    """重置 request_id context variable。"""
    _REQUEST_ID_CTX_VAR.reset(token)