# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""execution_id propagation across agent/workflow sessions (issue #2430)."""

import pytest

from openjiuwen.core.session.agent import Session, create_agent_session
from openjiuwen.core.session.config.base import Config
from openjiuwen.core.session.internal.agent import AgentSession
from openjiuwen.core.session.internal.workflow import NodeSession
from openjiuwen.core.session.workflow import Session as WorkflowSession

pytestmark = pytest.mark.asyncio


async def test_internal_agent_session_exposes_execution_id():
    session = AgentSession(session_id="s1", config=Config(), execution_id="exec-1")

    assert session.get_execution_id() == "exec-1"
    assert session.execution_id() == "exec-1"


async def test_internal_agent_session_workflow_inherits_execution_id():
    session = AgentSession(session_id="s1", config=Config(), execution_id="exec-1")

    workflow = session.create_workflow_session()
    node = NodeSession(workflow, "n1")

    assert workflow.get_execution_id() == "exec-1"
    assert node.get_execution_id() == "exec-1"


async def test_public_agent_session_forwards_execution_id():
    session = Session(session_id="s2", execution_id="exec-2")

    assert session.get_execution_id() == "exec-2"
    assert session.create_workflow_session().get_execution_id() == "exec-2"


async def test_create_agent_session_keyword_is_not_swallowed():
    session = create_agent_session(session_id="s3", execution_id="exec-3")

    assert session.get_execution_id() == "exec-3"


async def test_public_workflow_session_inherits_parent_execution_id():
    agent = AgentSession(session_id="s4", config=Config(), execution_id="exec-4")

    workflow = WorkflowSession(parent=agent, session_id="s4")

    assert workflow.get_execution_id() == "exec-4"


async def test_public_workflow_session_explicit_value_wins():
    agent = AgentSession(session_id="s5", config=Config(), execution_id="exec-parent")

    workflow = WorkflowSession(parent=agent, session_id="s5", execution_id="exec-explicit")

    assert workflow.get_execution_id() == "exec-explicit"


async def test_missing_execution_id_stays_none():
    session = AgentSession(session_id="s6", config=Config())

    assert session.get_execution_id() is None
    assert session.create_workflow_session().get_execution_id() is None
