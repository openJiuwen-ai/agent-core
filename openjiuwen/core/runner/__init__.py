# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

def __getattr__(name):
    if name == "Runner":
        from openjiuwen.core.runner.runner import Runner
        return Runner
    if name in ("get_request_id", "set_request_id", "reset_request_id"):
        from openjiuwen.core.runner.context_vars import (
            get_request_id,
            set_request_id,
            reset_request_id,
        )
        return locals()[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
