# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Where a tool call is executed.

Like :class:`ToolExposure`, this is runtime policy metadata and never enters
the model-facing :class:`ToolInfo` schema.
"""

from enum import Enum


class ToolExecution(str, Enum):
    """Who runs a tool call."""

    # The agent runtime calls ``Tool.invoke``.
    SERVER = "server"
    # The connected client runs the call; the runtime pauses the call with a
    # ``client_tool`` interrupt and turns the client's answer into the result.
    CLIENT = "client"


__all__ = ["ToolExecution"]
