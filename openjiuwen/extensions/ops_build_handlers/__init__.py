# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Platform Ops build handlers contributed by openjiuwen subsystems.

Each module here registers one ``ops.build`` BUILTIN handler through its
``register(bus, ...)`` entry, extending the shared
``EventHandlerOpsBuilder`` with its namespace of read-only query methods.
Hosts call these during initialization (``Registry.init(host_handlers=...)``);
the bus freezes ``ops.build`` handlers once the build has run.
"""

from openjiuwen.extensions.ops_build_handlers import team

__all__ = [
    "team",
]
