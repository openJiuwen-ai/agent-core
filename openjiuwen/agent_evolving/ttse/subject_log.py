# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tag TTSE logs with the cluster subject that is inducing right now."""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Any

from openjiuwen.core.common.logging import logger as _logger

_induce_subject: ContextVar[str] = ContextVar("ttse_induce_subject", default="")


def bind_induce_subject(subject: str) -> Token[str]:
    """Bind ``subject`` for ``[TTSERail]`` logs on this task. Empty clears the tag."""

    return _induce_subject.set(subject or "")


def reset_induce_subject(token: Token[str]) -> None:
    _induce_subject.reset(token)


def current_induce_subject() -> str:
    return _induce_subject.get()


def tag_ttse_message(message: Any) -> Any:
    """Prefix a ``[TTSERail]`` message when a cluster subject is bound."""

    subject = _induce_subject.get()
    if not subject or not isinstance(message, str) or not message.startswith("[TTSERail]"):
        return message
    if message.startswith("[TTSERail subject="):
        return message
    return f"[TTSERail subject={subject}]{message[len('[TTSERail]'):]}"


class _TaggedLogger:
    """Forward to the process logger, tagging TTSE lines for the bound subject."""

    @staticmethod
    def __getattr__(name: str) -> Any:
        attr = getattr(_logger, name)
        if name not in {"debug", "info", "warning", "error", "exception", "critical"}:
            return attr

        def _emit(message: Any, *args: Any, **kwargs: Any) -> Any:
            return attr(tag_ttse_message(message), *args, **kwargs)

        return _emit


logger = _TaggedLogger()
