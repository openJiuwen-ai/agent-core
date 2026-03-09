#!/usr/bin/env python3
# -*- coding: UTF-8 -*-

"""
Test script for the scoped events functionality
"""

from openjiuwen.core.runner.callback import events
from openjiuwen.core.runner.callback.events import EventBase, build_event_name, parse_event_name, DEFAULT_SCOPE


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
