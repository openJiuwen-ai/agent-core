# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Process transports for external CLI agents."""

from openjiuwen.agent_teams.external.cli_agent.transport.base import (
    ProcessLike,
    ProcessTransport,
    StdinLike,
    StreamReaderLike,
)
from openjiuwen.agent_teams.external.cli_agent.transport.local import LocalTransport

__all__ = [
    "LocalTransport",
    "ProcessLike",
    "ProcessTransport",
    "StdinLike",
    "StreamReaderLike",
]
