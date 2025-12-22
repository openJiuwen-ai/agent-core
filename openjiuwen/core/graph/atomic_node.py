# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from abc import ABC, abstractmethod
from typing import Any, Optional

from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.common.exception.status_code import StatusCode
from openjiuwen.core.session import BaseSession
from openjiuwen.core.session import CommitState


def _validate_session_and_state(session: Optional[BaseSession]) -> None:
    if session is None:
        raise JiuWenBaseException(StatusCode.SESSION_STATE_SESSION_NONE.code, 
                                 StatusCode.SESSION_STATE_SESSION_NONE.errmsg)
    
    if not isinstance(session, BaseSession):
        raise JiuWenBaseException(StatusCode.SESSION_STATE_INVALID_SESSION_TYPE.code, 
                                 StatusCode.SESSION_STATE_INVALID_SESSION_TYPE.errmsg.format(
                                     session_type=type(session).__name__))
    
    state = session.state()
    if not isinstance(state, CommitState):
        raise JiuWenBaseException(StatusCode.SESSION_STATE_INVALID_STATE_TYPE.code, 
                                 StatusCode.SESSION_STATE_INVALID_STATE_TYPE.errmsg.format(
                                     state_type=type(state).__name__))


class AtomicNode(ABC):
    def atomic_invoke(self, **kwargs) -> Any:
        session = kwargs.get("session", None)
        _validate_session_and_state(session)
        result = self._atomic_invoke(**kwargs)
        session.state().commit_cmp()
        return result

    @abstractmethod
    def _atomic_invoke(self, **kwargs) -> Any:
        pass


class AsyncAtomicNode(ABC):
    async def atomic_invoke(self, **kwargs) -> Any:
        session = kwargs.get("session", None)
        _validate_session_and_state(session)
        result = await self._atomic_invoke(**kwargs)
        session.state().commit_cmp()
        return result

    @abstractmethod
    async def _atomic_invoke(self, **kwargs) -> Any:
        pass
