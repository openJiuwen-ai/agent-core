#!/usr/bin/env python3
# -*- coding: UTF-8 -*-

"""
Test script for the scoped events functionality
"""

import pytest

from openjiuwen.core.runner.callback import AsyncCallbackFramework, events
from openjiuwen.core.runner.callback.events import (
    DEFAULT_SCOPE,
    EventBase,
    AgentEvents,
    ContextEvents,
    LLMCallEvents,
    MemoryEvents,
    TaskManagerEvents,
    ToolCallEvents,
    WorkflowEvents,
    build_event_name,
    parse_event_name,
)


def test_default_scope_events():
    """Test that system events use the default _framework scope"""
    print("=== Testing Default Scope Events ===")
    
    # Check that all system events have the correct scope
    assert events.AgentEvents.AGENT_STARTED == "_framework:agent_started"
    assert events.WorkflowEvents.WORKFLOW_STARTED == "_framework:workflow_started"
    assert events.LLMCallEvents.LLM_CALL_STARTED == "_framework:llm_call_started"
    assert events.ToolCallEvents.TOOL_CALL_STARTED == "_framework:tool_call_started"
    assert events.ContextEvents.CONTEXT_UPDATED == "_framework:context_updated"
    assert events.SessionEvents.SESSION_CREATED == "_framework:session_created"
    assert events.RetrievalEvents.RETRIEVAL_STARTED == "_framework:retrieval_started"
    
    print("✓ All system events use _framework scope")
    print(f"  AgentEvents.AGENT_STARTED: {events.AgentEvents.AGENT_STARTED}")
    print(f"  WorkflowEvents.WORKFLOW_STARTED: {events.WorkflowEvents.WORKFLOW_STARTED}")


def test_event_name_functions():
    """Test build_event_name and parse_event_name functions"""
    print("\n=== Testing Event Name Functions ===")
    
    # Test build_event_name
    scoped_event = build_event_name("my_scope", "my_event")
    assert scoped_event == "my_scope:my_event"
    print(f"✓ build_event_name works: {scoped_event}")
    
    # Test parse_event_name with scope
    scope, event_name = parse_event_name("my_scope:my_event")
    assert scope == "my_scope"
    assert event_name == "my_event"
    print(f"✓ parse_event_name with scope: scope={scope}, event_name={event_name}")
    
    # Test parse_event_name without scope (should use default)
    scope, event_name = parse_event_name("my_event")
    assert scope == DEFAULT_SCOPE
    assert event_name == "my_event"
    print(f"✓ parse_event_name without scope: scope={scope}, event_name={event_name}")


def test_custom_scope_events():
    """Test creating custom events with different scopes"""
    print("\n=== Testing Custom Scope Events ===")
    
    # Create a custom event class with a different scope
    class CustomEvents(EventBase):
        scope = "custom_scope"
        CUSTOM_EVENT_1 = EventBase.get_event("custom_event_1")
        CUSTOM_EVENT_2 = EventBase.get_event("custom_event_2")
    
    # Check that custom events use the specified scope
    assert CustomEvents.CUSTOM_EVENT_1 == "custom_scope:custom_event_1"
    assert CustomEvents.CUSTOM_EVENT_2 == "custom_scope:custom_event_2"
    
    print(f"✓ CustomEvents.CUSTOM_EVENT_1: {CustomEvents.CUSTOM_EVENT_1}")
    print(f"✓ CustomEvents.CUSTOM_EVENT_2: {CustomEvents.CUSTOM_EVENT_2}")


def test_scope_isolation():
    """Test that events with same name but different scopes are isolated"""
    print("\n=== Testing Scope Isolation ===")
    
    # Create two event classes with same event names but different scopes
    class Scope1Events(EventBase):
        scope = "scope1"
        SAME_EVENT = EventBase.get_event("same_event")
    
    class Scope2Events(EventBase):
        scope = "scope2"
        SAME_EVENT = EventBase.get_event("same_event")
    
    # Check that they are different events
    assert Scope1Events.SAME_EVENT != Scope2Events.SAME_EVENT
    assert Scope1Events.SAME_EVENT == "scope1:same_event"
    assert Scope2Events.SAME_EVENT == "scope2:same_event"
    
    print(f"✓ Scope1Events.SAME_EVENT: {Scope1Events.SAME_EVENT}")
    print(f"✓ Scope2Events.SAME_EVENT: {Scope2Events.SAME_EVENT}")
    print("✓ Events with same name but different scopes are isolated")


if __name__ == "__main__":
    test_default_scope_events()
    test_event_name_functions()
    test_custom_scope_events()
    test_scope_isolation()
    print("\n=== All Tests Passed! ===")


@pytest.fixture
def fw():
    return AsyncCallbackFramework(enable_metrics=False, enable_logging=False)


@pytest.mark.asyncio
async def test_agent_events_callback(fw):
    """Test that AgentEvents can trigger callbacks"""
    triggered = []

    @fw.on(AgentEvents.AGENT_STARTED)
    async def handler(**kwargs):
        triggered.append("agent_started")

    @fw.emit_before(AgentEvents.AGENT_STARTED)
    async def run_agent():
        return "agent_result"

    result = await run_agent()

    assert result == "agent_result"
    assert triggered == ["agent_started"]


@pytest.mark.asyncio
async def test_workflow_events_callback(fw):
    """Test that WorkflowEvents can trigger callbacks"""
    triggered = []

    @fw.on(WorkflowEvents.WORKFLOW_STARTED)
    async def on_start(**kwargs):
        triggered.append("start")

    @fw.on(WorkflowEvents.WORKFLOW_FINISHED)
    async def on_finish(result, **kwargs):
        triggered.append(f"finish:{result}")

    @fw.emit_before(WorkflowEvents.WORKFLOW_STARTED)
    @fw.emit_after(WorkflowEvents.WORKFLOW_FINISHED)
    async def run_workflow():
        return "workflow_done"

    result = await run_workflow()

    assert result == "workflow_done"
    assert "start" in triggered
    assert "finish:workflow_done" in triggered


@pytest.mark.asyncio
async def test_llm_call_events_callback(fw):
    """Test that LLMCallEvents can trigger callbacks"""
    started = []
    output = []

    @fw.on(LLMCallEvents.LLM_CALL_STARTED)
    async def on_start(**kwargs):
        started.append("llm_called")

    @fw.on(LLMCallEvents.LLM_OUTPUT)
    async def on_output(result, **kwargs):
        output.append(result)

    @fw.emit_before(LLMCallEvents.LLM_CALL_STARTED)
    @fw.emit_after(LLMCallEvents.LLM_OUTPUT, result_key="result")
    async def call_llm():
        return "llm_response"

    result = await call_llm()

    assert result == "llm_response"
    assert started == ["llm_called"]
    assert output == ["llm_response"]


@pytest.mark.asyncio
async def test_tool_call_events_callback(fw):
    """Test that ToolCallEvents can trigger callbacks"""
    started = []
    finished = []

    @fw.on(ToolCallEvents.TOOL_CALL_STARTED)
    async def on_start(tool_name, **kwargs):
        started.append(tool_name)

    @fw.on(ToolCallEvents.TOOL_CALL_FINISHED)
    async def on_finish(result, **kwargs):
        finished.append(result)

    @fw.emit_before(ToolCallEvents.TOOL_CALL_STARTED)
    @fw.emit_after(ToolCallEvents.TOOL_CALL_FINISHED, result_key="result")
    async def call_tool(tool_name):
        return f"{tool_name}_result"

    result = await call_tool("calculator")

    assert started == ["calculator"]
    assert finished == ["calculator_result"]


@pytest.mark.asyncio
async def test_context_events_callback(fw):
    """Test that ContextEvents can trigger callbacks"""
    updated = []

    @fw.on(ContextEvents.CONTEXT_UPDATED)
    async def on_update(messages, **kwargs):
        updated.append(messages)

    @fw.emit_after(ContextEvents.CONTEXT_UPDATED, result_key="messages")
    async def update_context():
        return ["msg1", "msg2"]

    result = await update_context()

    assert result == ["msg1", "msg2"]
    assert updated == [["msg1", "msg2"]]


@pytest.mark.asyncio
async def test_memory_events_callback(fw):
    """Test that MemoryEvents can trigger callbacks"""
    search_started = []
    search_finished = []

    @fw.on(MemoryEvents.MEMORY_SEARCH_STARTED)
    async def on_search_start(query, **kwargs):
        search_started.append(query)

    @fw.on(MemoryEvents.MEMORY_SEARCH_FINISHED)
    async def on_search_finish(result, **kwargs):
        search_finished.append(result)

    @fw.emit_before(MemoryEvents.MEMORY_SEARCH_STARTED)
    @fw.emit_after(MemoryEvents.MEMORY_SEARCH_FINISHED, result_key="result")
    async def search_memory(query):
        return [f"result_for_{query}"]

    result = await search_memory("test query")

    assert result == ["result_for_test query"]
    assert search_started == ["test query"]
    assert search_finished == [["result_for_test query"]]


@pytest.mark.asyncio
async def test_task_manager_events_callback(fw):
    """Test that TaskManagerEvents can trigger callbacks"""
    created = []
    completed = []

    @fw.on(TaskManagerEvents.TASK_CREATED)
    async def on_created(task_id, **kwargs):
        created.append(task_id)

    @fw.on(TaskManagerEvents.TASK_COMPLETED)
    async def on_completed(result, **kwargs):
        completed.append(result)

    @fw.emit_before(TaskManagerEvents.TASK_CREATED)
    @fw.emit_after(TaskManagerEvents.TASK_COMPLETED, result_key="result")
    async def run_task(task_id):
        return f"{task_id}_done"

    result = await run_task("task-001")

    assert result == "task-001_done"
    assert created == ["task-001"]
    assert completed == ["task-001_done"]
