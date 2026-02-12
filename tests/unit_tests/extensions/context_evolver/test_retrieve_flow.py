# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Test the minimal retrieve flow.

Supports ACE, ReasoningBank, and ReMe algorithms based on .env configuration.
"""

import asyncio
import pytest
from openjiuwen.core.common.logging import context_engine_logger as logger

from openjiuwen.extensions.context_evolver import (
    TaskMemoryService,
    AddMemoryRequest,
)


def get_algorithm_specific_memory_params(service: TaskMemoryService, content: str, identifier: str):
    """Get memory parameters based on configured algorithm.

    Args:
        service: TaskMemoryService instance
        content: Memory content
        identifier: Unique identifier for the memory

    Returns:
        AddMemoryRequest object for add_memory()
    """
    if service.summary_algorithm == "ReasoningBank":
        return AddMemoryRequest(
            content=content,
            title=f"Memory {identifier}",
            description=f"Description for memory {identifier}",
        )
    elif service.summary_algorithm == "ReMe":
        # ReMe uses when_to_use and content (similar to ACE but without section)
        return AddMemoryRequest(
            content=content,
            when_to_use=f"When to use memory {identifier}",
        )
    else:  # ACE
        return AddMemoryRequest(
            content=content,
            section="test",
        )


@pytest.mark.skip(reason="Temporarily disabled: API_KEY is needed in .env file")
@pytest.mark.asyncio
async def test_add_and_retrieve_memory():
    """Test adding a memory and retrieving it."""
    # Initialize service
    service = TaskMemoryService(
        llm_model="gpt-3.5-turbo",  # Using cheaper model for testing
        embedding_model="text-embedding-3-small",
    )

    user_id = "test_user"
    algorithm = service.summary_algorithm
    logger.info("Using algorithm: %s", algorithm)

    # Add a test memory with algorithm-specific parameters
    content = ("Use functools.lru_cache decorator for simple memoization. "
               "For more complex cases, consider using Redis or memcached.")

    if algorithm == "ReasoningBank":
        await service.add_memory(
            user_id=user_id,
            request=AddMemoryRequest(
                content=content,
                title="Python Caching Strategy",
                description="How to implement caching in Python applications",
            ),
        )
    elif algorithm == "ReMe":
        # ReMe uses when_to_use and content (similar to ACE but without section)
        add_status = await service.add_memory(
            user_id=user_id,
            request=AddMemoryRequest(
                content=content,
                when_to_use="When implementing caching in Python",
            ),
        )
        logger.info("Added status: %s", add_status)

    else:  # ACE
        await service.add_memory(
            user_id=user_id,
            request=AddMemoryRequest(
                content=content,
                section="python_best_practices",
            ),
        )

    # Retrieve using the memory
    result = await service.retrieve(
        user_id=user_id,
        query="How do I implement caching in Python?",
    )

    logger.info("%s retrieved result starts %s", '*' * 50, '*' * 50)
    # Assertions - use new response format
    logger.info("%s", result)
    logger.info("%s retrieved result end %s", '*' * 50, '*' * 50)

    logger.info("%s Memory string starts %s", '*' * 50, '*' * 50)
    logger.info("%s...", result['memory_string'][:200] if result['memory_string'] else 'No memories')
    logger.info("%s Memory string ends %s", '*' * 50, '*' * 50)

    logger.info("%s Retrieved memories starts %s", '*' * 50, '*' * 50)
    logger.info("%s", result['retrieved_memory'][0]["content"])
    logger.info("%s Retrieved memories ends %s", '*' * 50, '*' * 50)
    

    assert result["status"] == "success"

    if algorithm == "ReasoningBank":
        assert result["retrieved_memory"][0]["title"] == "Python Caching Strategy"
        assert result["retrieved_memory"][0]["description"] == "How to implement caching in Python applications"
        assert result["retrieved_memory"][0]["content"] == content
    elif algorithm == "ReMe":
        # ReMe returns when_to_use and content
        assert result["retrieved_memory"][0]["when_to_use"] == "When implementing caching in Python"
        assert result["retrieved_memory"][0]["content"] == content
    else:  # ACE
        assert content in result["retrieved_memory"][0]["content"] 
        assert result["retrieved_memory"][0]["section"] == "python_best_practices"


@pytest.mark.skip(reason="Temporarily disabled: API_KEY is needed in .env file")    
@pytest.mark.asyncio
async def test_retrieve_without_memories():
    """Test retrieving when no memories exist."""
    service = TaskMemoryService(
        llm_model="gpt-3.5-turbo",
        embedding_model="text-embedding-3-small",
    )

    user_id = "empty_user"

    result = await service.retrieve(
        user_id=user_id,
        query="What is Python?",
    )

    # New response format
    assert "status" in result
    assert result["status"] == "success"
    assert "memory_string" in result
    assert "retrieved_memory" in result
    assert len(result["retrieved_memory"]) == 0

    logger.info("Retrieve without memories: status=%s, memories=%d", result['status'], len(result['retrieved_memory']))


@pytest.mark.skip(reason="Temporarily disabled: API_KEY is needed in .env file")
@pytest.mark.asyncio
async def test_playbook_operations():
    """Test playbook get and clear operations."""
    service = TaskMemoryService(
        llm_model="gpt-3.5-turbo",
        embedding_model="text-embedding-3-small",
    )

    user_id = "test_user_2"
    algorithm = service.summary_algorithm
    logger.info("Using algorithm: %s", algorithm)

    # Add some memories with algorithm-specific parameters
    if algorithm == "ReasoningBank":
        await service.add_memory(
            user_id=user_id,
            request=AddMemoryRequest(
                content="Content 1",
                title="Test Memory 1",
                description="Description for test memory 1",
            ),
        )
        await service.add_memory(
            user_id=user_id,
            request=AddMemoryRequest(
                content="Content 2",
                title="Test Memory 2",
                description="Description for test memory 2",
            ),
        )
    elif algorithm == "ReMe":
        # ReMe uses when_to_use and content (similar to ACE but without section)
        await service.add_memory(
            user_id=user_id,
            request=AddMemoryRequest(
                content="Content 1",
                when_to_use="Test memory 1",
            ),
        )
        await service.add_memory(
            user_id=user_id,
            request=AddMemoryRequest(
                content="Content 2",
                when_to_use="Test memory 2",
            ),
        )
    else:  # ACE
        await service.add_memory(
            user_id=user_id,
            request=AddMemoryRequest(
                content="Content 1",
                section="test",
            ),
        )
        await service.add_memory(
            user_id=user_id,
            request=AddMemoryRequest(
                content="Content 2",
                section="test",
            ),
        )

    # Get playbook
    playbook = await service.get_playbook(user_id)
    logger.info("getting playbook...")
    logger.info("%s", playbook)
    assert playbook["user_id"] == user_id
    assert playbook["memory_count"] == 2
    assert "memories" in playbook
    assert len(playbook["memories"]) == 2

    # Verify algorithm-specific fields in memories
    if algorithm == "ReasoningBank":
        # For ReasoningBank, content is inside the memory items
        for mem in playbook["memories"]:
            assert "memory" in mem
            assert len(mem["memory"]) > 0
        # Extract content from memory items
        memory_contents = [mem["memory"][0].content for mem in playbook["memories"]]
        assert "Content 1" in memory_contents
        assert "Content 2" in memory_contents
        # Verify titles
        memory_titles = [mem["memory"][0].title for mem in playbook["memories"]]
        assert "Test Memory 1" in memory_titles
        assert "Test Memory 2" in memory_titles
    elif algorithm == "ReMe":
        # For ReMe, content is at the top level with when_to_use
        memory_contents = [m["content"] for m in playbook["memories"]]
        assert "Content 1" in memory_contents or any("Content 1" in str(m) for m in playbook["memories"])
        assert "Content 2" in memory_contents or any("Content 2" in str(m) for m in playbook["memories"])
    else:  # ACE
        # For ACE, content is stored directly without when_to_use prefix
        memory_contents = [m["content"] for m in playbook["memories"]]
        # Check that content strings contain the expected values
        assert any("Content 1" in c for c in memory_contents), f"Content 1 not found in {memory_contents}"
        assert any("Content 2" in c for c in memory_contents), f"Content 2 not found in {memory_contents}"

    # Clear playbook
    result = await service.clear_playbook(user_id)
    assert result["status"] == "success"

    # Verify cleared
    playbook = await service.get_playbook(user_id)
    assert playbook["memory_count"] == 0
    assert playbook["memories"] == []


if __name__ == "__main__":
    # Run tests manually
    asyncio.run(test_add_and_retrieve_memory())

    asyncio.run(test_retrieve_without_memories())

    logger.info("Running test_playbook_operations...")
    asyncio.run(test_playbook_operations())

    logger.info("All tests passed!")
