# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import asyncio
import contextvars
import inspect
from typing import Any
import functools

from openjiuwen.core.session.checkpointer import Checkpointer
from openjiuwen.core.session.config.base import (
    Config,
    workflow_session_vars,
)
from openjiuwen.core.session.constants import (
    COMP_STREAM_CALL_TIMEOUT_KEY,
    END_COMP_TEMPLATE_BATCH_READER_TIMEOUT_KEY,
    END_COMP_TEMPLATE_RENDER_POSITION_TIMEOUT_KEY,
    FORCE_DEL_WORKFLOW_STATE_ENV_KEY,
    FORCE_DEL_WORKFLOW_STATE_KEY,
    LOOP_NUMBER_MAX_LIMIT_DEFAULT,
    LOOP_NUMBER_MAX_LIMIT_KEY,
    STREAM_INPUT_GEN_TIMEOUT_KEY,
    WORKFLOW_EXECUTE_TIMEOUT,
    WORKFLOW_STREAM_FIRST_FRAME_TIMEOUT,
    WORKFLOW_STREAM_FRAME_TIMEOUT,
)
from openjiuwen.core.session.interaction.base import AgentInterrupt
from openjiuwen.core.session.interaction.interaction import InteractionOutput
from openjiuwen.core.session.interaction.interactive_input import InteractiveInput
from openjiuwen.core.session.internal.workflow import (
    NodeSession,
    SubWorkflowSession,
    WorkflowSession,
)
from openjiuwen.core.session.internal.wrapper import (
    RouterSession,
    WrappedSession,
)
from openjiuwen.core.session.session import (
    BaseSession,
    ProxySession
)
from openjiuwen.core.session.state.base import Transformer
from openjiuwen.core.session.state.workflow_state import CommitState
from openjiuwen.core.session.utils import (
    EndFrame,
    extract_origin_key,
    get_by_schema,
    get_value_by_nested_path,
    is_ref_path,
    NESTED_PATH_SPLIT,
)



from openjiuwen.core.session.session import Session

deprecated = ["Session"]

_current_session = contextvars.ContextVar("current_session", default=None)


def get_current_session():
    return _current_session.get()


def with_session_for_class(cls):
    methods = ['invoke', 'stream', 'collect', 'transform']
    for method_name in methods:
        if hasattr(cls, method_name):
            method = getattr(cls, method_name)
            if asyncio.iscoroutinefunction(method):
                # Apply the session decorator
                decorated = with_session()(method)
                setattr(cls, method_name, decorated)

    return cls


def with_session(session: Any = None):
    def decorator(func):
        sig = inspect.signature(func)
        param_names = list(sig.parameters.keys())

        session_param_index = None
        for i, name in enumerate(param_names):
            if name == 'session':
                session_param_index = i
                break

        def get_target_session(args, kwargs):
            if session is not None:
                return session
            if 'session' in kwargs:
                return kwargs['session']
            if session_param_index is not None and len(args) > session_param_index:
                return args[session_param_index]
            return None

        if inspect.isasyncgenfunction(func):
            @functools.wraps(func)
            async def async_gen_wrapper(*args, **kwargs):
                target_session = get_target_session(args, kwargs)
                token = _current_session.set(target_session)
                try:
                    async for value in func(*args, **kwargs):
                        yield value
                finally:
                    _current_session.reset(token)

            return async_gen_wrapper

        elif inspect.isgeneratorfunction(func):
            @functools.wraps(func)
            def sync_gen_wrapper(*args, **kwargs):
                target_session = get_target_session(args, kwargs)
                token = _current_session.set(target_session)
                try:
                    for value in func(*args, **kwargs):
                        yield value
                finally:
                    _current_session.reset(token)

            return sync_gen_wrapper

        else:
            if asyncio.iscoroutinefunction(func):
                @functools.wraps(func)
                async def async_wrapper(*args, **kwargs):
                    target_session = get_target_session(args, kwargs)
                    token = _current_session.set(target_session)
                    try:
                        return await func(*args, **kwargs)
                    finally:
                        _current_session.reset(token)

                return async_wrapper
            else:
                @functools.wraps(func)
                def sync_wrapper(*args, **kwargs):
                    target_session = get_target_session(args, kwargs)
                    token = _current_session.set(target_session)
                    try:
                        return func(*args, **kwargs)
                    finally:
                        _current_session.reset(token)

                return sync_wrapper

    return decorator


__all__ = [
    # session
    "BaseSession",
    "WrappedSession",
    "ProxySession",

    # workflow session
    "WorkflowSession",
    "NodeSession",
    "SubWorkflowSession",
    "RouterSession",
    "workflow_session_vars",

    # agent session
    "CommitState",

    # interaction
    "InteractiveInput",
    "InteractionOutput",
    "Checkpointer",
    "AgentInterrupt",

    # config
    "Config",

    # constants
    "COMP_STREAM_CALL_TIMEOUT_KEY",
    "WORKFLOW_EXECUTE_TIMEOUT",
    "WORKFLOW_STREAM_FRAME_TIMEOUT",
    "WORKFLOW_STREAM_FIRST_FRAME_TIMEOUT",
    "END_COMP_TEMPLATE_RENDER_POSITION_TIMEOUT_KEY",
    "END_COMP_TEMPLATE_BATCH_READER_TIMEOUT_KEY",
    "LOOP_NUMBER_MAX_LIMIT_DEFAULT",
    "LOOP_NUMBER_MAX_LIMIT_KEY",
    "STREAM_INPUT_GEN_TIMEOUT_KEY",
    "FORCE_DEL_WORKFLOW_STATE_ENV_KEY",
    "FORCE_DEL_WORKFLOW_STATE_KEY",
    "NESTED_PATH_SPLIT",

    "EndFrame",
    "get_by_schema",
    "get_value_by_nested_path",
    "extract_origin_key",
    "is_ref_path",
    "Transformer",
] + deprecated
