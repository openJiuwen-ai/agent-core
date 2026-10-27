# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""StatusCode helpers for subagent runtime failures."""

from __future__ import annotations

from typing import NoReturn

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError, build_error, raise_error


def raise_subagent_not_found(subagent_id: str) -> NoReturn:
    raise_error(
        StatusCode.DEEPAGENT_SUBAGENT_NOT_FOUND,
        error_msg=f"subagent_id={subagent_id}",
    )


def raise_subagent_capacity_invalid(*, used: int, limit: int) -> NoReturn:
    raise_error(
        StatusCode.DEEPAGENT_SUBAGENT_CAPACITY_INVALID,
        error_msg=(
            f"used={used}, limit={limit}; no free subagent slot, call subagent_wait for a running "
            "subagent or subagent_close an idle one before spawning more"
        ),
    )


def build_subagent_runtime_error(
    reason: str,
    *,
    cause: BaseException | None = None,
) -> BaseError:
    return build_error(
        StatusCode.DEEPAGENT_SUBAGENT_RUNTIME_ERROR,
        error_msg=reason,
        cause=cause,
    )
