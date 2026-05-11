# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Chained session management system.

Provides scope-isolation-based chained session data storage, supporting session
management, data isolation, and unidirectional visibility control in multi-Agent
systems.
"""

from openjiuwen.core.session.session_controller.chain_session import ChainSession
from openjiuwen.core.session.session_controller.data_container import (
    DataContainer,
    Permission,
    SharingPolicy,
    DataContainerFactory,
    AgentSessionContainer,
)
from openjiuwen.core.session.session_controller.global_controller import GlobalSessionController
from openjiuwen.core.session.session_controller.schema import SessionMeta, ScopeSessionsMeta
from openjiuwen.core.session.session_controller.scope_factory import SessionScopeFactory
from openjiuwen.core.session.session_controller.session_controller import SessionController
from .scope import (
    Scope,
    MainScope,
    Subject,
    DirectSubject,
    GroupSubject,
    GroupUserSubject,
    SessionScope,
    SessionScopeKey,
)

__all__ = [
    # Scope and Subject
    'Scope',
    'Subject',
    'SessionScope',
    'SessionScopeKey',

    # Factory class
    'SessionScopeFactory',

    # Data container and permissions
    'DataContainer',
    'Permission',
    'SharingPolicy',
    'DataContainerFactory',

    # Core session class
    'ChainSession',

    # Controllers
    'SessionController',
    'GlobalSessionController',
]
