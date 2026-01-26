#!/usr/bin/env python
# coding: utf-8
"""
Phase 1 Refactoring Test
Tests the extracted ContextManager and integration with SuperReActAgent
"""

import os
import sys

# Add parent directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from examples.super_agent import ContextManager
from examples.super_agent import SuperReActAgent
from examples.super_agent import SuperAgentFactory
from openjiuwen.core.foundation.llm import ModelConfig
from openjiuwen.core.foundation.llm import BaseModelInfo


def test_context_manager_basic_operations():
    """Test 1: ContextManager basic operations (no LLM needed)"""
    print("\n" + "=" * 60)
    print("Test 1: ContextManager Basic Operations")
    print("=" * 60)

    # Create context manager without LLM (for basic operations)
    context = ContextManager(llm=None, max_history_length=10)

    # Test add_message
    context.add_message("user", "Hello")
    context.add_message("assistant", "Hi there!")
    context.add_message("user", "How are you?")

    # Test get_history
    history = context.get_history()
    assert len(history) == 3, f"Expected 3 messages, got {len(history)}"
    assert history[0]["role"] == "user", "First message should be user"
    assert history[0]["content"] == "Hello", "First message content mismatch"
    print("✅ add_message and get_history work correctly")

    # Test add_user_message helper
    context.add_user_message("Test user message")
    history = context.get_history()
    assert len(history) == 4, f"Expected 4 messages, got {len(history)}"
    print("✅ add_user_message helper works correctly")

    # Test add_assistant_message helper
    context.add_assistant_message("Test assistant message")
    history = context.get_history()
    assert len(history) == 5, f"Expected 5 messages, got {len(history)}"
    print("✅ add_assistant_message helper works correctly")

    # Test add_tool_message
    context.add_tool_message("tool_123", "Tool result")
    history = context.get_history()
    assert len(history) == 6, f"Expected 6 messages, got {len(history)}"
    assert history[-1]["role"] == "tool", "Last message should be tool"
    assert history[-1]["tool_call_id"] == "tool_123", "Tool call ID mismatch"
    print("✅ add_tool_message works correctly")

    # Test clear
    context.clear()
    history = context.get_history()
    assert len(history) == 0, f"Expected 0 messages after clear, got {len(history)}"
    print("✅ clear works correctly")

    print("\n✅ All basic operations passed!")
    return True


def test_context_manager_auto_trimming():
    """Test 2: ContextManager auto-trimming"""
    print("\n" + "=" * 60)
    print("Test 2: ContextManager Auto-Trimming")
    print("=" * 60)

    # Create context manager with small max length
    context = ContextManager(llm=None, max_history_length=5)

    # Add more messages than max
    for i in range(10):
        context.add_message("user", f"Message {i}")

    # Check that history is trimmed
    history = context.get_history()
    assert len(history) == 5, f"Expected 5 messages (auto-trimmed), got {len(history)}"

    # Check that recent messages are kept
    assert history[-1]["content"] == "Message 9", "Most recent message should be kept"
    print("✅ Auto-trimming keeps recent messages")

    # Test with system messages (should be preserved)
    context.clear()
    context.add_message("system", "System prompt")
    for i in range(10):
        context.add_message("user", f"Message {i}")

    history = context.get_history()
    assert history[0]["role"] == "system", "System message should be preserved"
    assert history[0]["content"] == "System prompt", "System message content should be preserved"
    print("✅ Auto-trimming preserves system messages")

    print("\n✅ Auto-trimming tests passed!")
    return True


def test_context_manager_remove_last_messages():
    """Test 3: ContextManager remove_last_messages (for retry logic)"""
    print("\n" + "=" * 60)
    print("Test 3: ContextManager Remove Last Messages")
    print("=" * 60)

    context = ContextManager(llm=None, max_history_length=100)

    # Add some messages
    context.add_message("user", "Message 1")
    context.add_message("assistant", "Response 1")
    context.add_message("tool", "Tool result", tool_call_id="123")
    context.add_message("user", "Message 2")
    context.add_message("assistant", "Response 2")

    # Remove last 2 messages
    context.remove_last_messages(2)
    history = context.get_history()

    assert len(history) == 3, f"Expected 3 messages after removing 2, got {len(history)}"
    assert history[-1]["content"] == "Tool result", "Last message should be tool result after removal"
    print("✅ remove_last_messages works correctly")

    print("\n✅ Remove messages test passed!")
    return True


def test_super_react_agent_initialization():
    """Test 4: SuperReActAgent initialization with new ContextManager"""
    print("\n" + "=" * 60)
    print("Test 4: SuperReActAgent Initialization")
    print("=" * 60)

    # Create model config (won't actually call API)
    model_info = BaseModelInfo(
        api_key="test_key",
        api_base="https://test.api",
        model="test/model",
        timeout=60
    )

    model_config = ModelConfig(
        model_provider="test",
        model_info=model_info
    )

    # Create single_agent config
    agent_config = SuperAgentFactory.create_main_agent_config(
        agent_id="test_agent",
        agent_version="1.0",
        description="Test single_agent",
        model=model_config,
        prompt_template=[
            {"role": "system", "content": "You are a test assistant"}
        ],
        max_iteration=10,
        max_tool_calls_per_turn=3,
        enable_o3_hints=False,
        enable_o3_final_answer=False
    )

    # Create single_agent (should not fail)
    try:
        agent = SuperReActAgent(
            agent_config=agent_config,
            workflows=None,
            tools=[],
            task_log=None
        )
        print("✅ SuperReActAgent instantiated successfully")
    except Exception as e:
        print(f"❌ Failed to instantiate SuperReActAgent: {e}")
        return False

    # Check that context manager is properly initialized
    assert agent._context_manager is not None, "Context manager should be initialized"
    assert isinstance(agent._context_manager, ContextManager), "Should be ContextManager instance"
    print("✅ ContextManager is properly initialized in single_agent")

    # Check that LLM is created eagerly
    assert agent._llm is not None, "LLM should be created eagerly"
    print("✅ LLM is created eagerly")

    # Check that context manager has LLM reference
    assert agent._context_manager._llm is not None, "ContextManager should have LLM reference"
    assert agent._context_manager._llm is agent._llm, "ContextManager should share same LLM instance"
    print("✅ ContextManager has correct LLM reference")

    # Test that we can add messages through single_agent's context manager
    agent._context_manager.add_user_message("Test message")
    history = agent._context_manager.get_history()
    assert len(history) == 1, "Should have 1 message in history"
    print("✅ Can add messages through single_agent's context manager")

    print("\n✅ SuperReActAgent initialization test passed!")
    return True


def test_backward_compatibility():
    """Test 5: Backward compatibility check"""
    print("\n" + "=" * 60)
    print("Test 5: Backward Compatibility")
    print("=" * 60)

    # Check that old public API still works
    model_info = BaseModelInfo(
        api_key="test_key",
        api_base="https://test.api",
        model="test/model",
        timeout=60
    )

    model_config = ModelConfig(
        model_provider="test",
        model_info=model_info
    )

    agent_config = SuperAgentFactory.create_main_agent_config(
        agent_id="test_agent",
        agent_version="1.0",
        description="Test single_agent",
        model=model_config,
        prompt_template=[
            {"role": "system", "content": "You are a test assistant"}
        ],
        max_iteration=10,
        max_tool_calls_per_turn=3,
        enable_o3_hints=False,
        enable_o3_final_answer=False
    )

    agent = SuperReActAgent(
        agent_config=agent_config,
        workflows=None,
        tools=[],
        task_log=None
    )

    # Check that old methods still exist and work
    assert hasattr(agent, '_context_manager'), "Agent should have _context_manager attribute"
    assert hasattr(agent, '_get_llm'), "Agent should have _get_llm method"
    assert hasattr(agent, 'invoke'), "Agent should have invoke method"
    assert hasattr(agent, 'stream'), "Agent should have stream method"
    assert hasattr(agent, 'register_sub_agent'), "Agent should have register_sub_agent method"
    print("✅ All public API methods exist")

    # Check that context manager has expected methods
    assert hasattr(agent._context_manager, 'add_message'), "ContextManager should have add_message"
    assert hasattr(agent._context_manager, 'get_history'), "ContextManager should have get_history"
    assert hasattr(agent._context_manager, 'clear'), "ContextManager should have clear"
    assert hasattr(agent._context_manager, 'remove_last_messages'), "ContextManager should have remove_last_messages"
    assert hasattr(agent._context_manager, 'generate_summary'), "ContextManager should have generate_summary"
    print("✅ ContextManager has all expected methods")

    print("\n✅ Backward compatibility test passed!")
    return True


def run_all_tests():
    """Run all tests"""
    print("\n" + "=" * 70)
    print("PHASE 1 REFACTORING TEST SUITE")
    print("=" * 70)

    results = []

    try:
        results.append(("ContextManager Basic Operations", test_context_manager_basic_operations()))
    except Exception as e:
        print(f"❌ Test 1 failed with exception: {e}")
        import traceback
        traceback.print_exc()
        results.append(("ContextManager Basic Operations", False))

    try:
        results.append(("ContextManager Auto-Trimming", test_context_manager_auto_trimming()))
    except Exception as e:
        print(f"❌ Test 2 failed with exception: {e}")
        import traceback
        traceback.print_exc()
        results.append(("ContextManager Auto-Trimming", False))

    try:
        results.append(("ContextManager Remove Messages", test_context_manager_remove_last_messages()))
    except Exception as e:
        print(f"❌ Test 3 failed with exception: {e}")
        import traceback
        traceback.print_exc()
        results.append(("ContextManager Remove Messages", False))

    try:
        results.append(("SuperReActAgent Initialization", test_super_react_agent_initialization()))
    except Exception as e:
        print(f"❌ Test 4 failed with exception: {e}")
        import traceback
        traceback.print_exc()
        results.append(("SuperReActAgent Initialization", False))

    try:
        results.append(("Backward Compatibility", test_backward_compatibility()))
    except Exception as e:
        print(f"❌ Test 5 failed with exception: {e}")
        import traceback
        traceback.print_exc()
        results.append(("Backward Compatibility", False))

    # Print summary
    print("\n" + "=" * 70)
    print("TEST SUMMARY")
    print("=" * 70)

    passed = sum(1 for _, result in results if result)
    total = len(results)

    for test_name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{status}: {test_name}")

    print("\n" + "=" * 70)
    print(f"TOTAL: {passed}/{total} tests passed")
    print("=" * 70)

    if passed == total:
        print("\n🎉 All tests passed! Phase 1 refactoring is successful!")
        return True
    else:
        print(f"\n⚠️  {total - passed} test(s) failed. Please review the errors above.")
        return False


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
