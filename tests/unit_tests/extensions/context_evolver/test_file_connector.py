# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Test script for JSONFileConnector with realistic memory scenarios."""

from pathlib import Path
from datetime import datetime
import tempfile
import os
import asyncio
from collections import defaultdict
import pytest
from openjiuwen.core.common.logging import context_engine_logger as logger

from openjiuwen.extensions.context_evolver import (
    JSONFileConnector,
    MemoryVectorStore,
    ACEMemory,
    ReasoningBankMemory,
    ReasoningBankMemoryItem,
    ReMeMemory,
    ReMeMemoryMetadata,
)


@pytest.mark.asyncio
async def test_vector_store_with_realistic_memories():
    """Test saving and loading a vector store with ACE, ReasoningBank, and ReMe memories."""
    logger.info("=" * 70)
    logger.info("Testing Vector Store with Realistic Memories (ACE/ReasoningBank/ReMe)")
    logger.info("=" * 70)

    # Create a vector store
    vector_store = MemoryVectorStore()

    # Create realistic memories for each type
    logger.info("1. Creating memories...")

    # ACE Memory - Python caching
    ace_memory = ACEMemory(
        id="mem_ace_001",
        section="python_optimization",
        content="Use functools.lru_cache decorator for memoization to cache function results",
        helpful=5,
        harmful=0,
        neutral=1,
        created_at=datetime(2024, 1, 15, 10, 30, 0),
        updated_at=datetime(2024, 1, 20, 14, 15, 0),
        workspace_id="ace_workspace"  # ACE-specific workspace
    )
    logger.info("   [OK] Created ACE memory: %s", ace_memory.section)

    # ReasoningBank Memory - Error handling
    reasoning_bank_memory = ReasoningBankMemory(
        query="How to handle errors in Python?",
        memory=[
            ReasoningBankMemoryItem(
                title="Exception Handling Best Practices",
                description="Use specific exception types instead of catching all exceptions",
                content=(
                    "Always catch specific exceptions "
                    "(e.g., FileNotFoundError, ValueError) "
                    "rather than using bare except clauses"
                )
            ),
            ReasoningBankMemoryItem(
                title="Context Managers",
                description="Use context managers for resource cleanup",
                content="Use 'with' statement for file operations to ensure proper cleanup even if errors occur"
            )
        ],
        label=True,
        workspace_id="reasoningbank_workspace"  # ReasoningBank-specific workspace
    )
    logger.info("   [OK] Created ReasoningBank memory: %s", reasoning_bank_memory.query)

    # ReMe Memory - API design
    reme_memory = ReMeMemory(
        when_to_use="When designing RESTful APIs with proper error responses",
        content=(
            "Return appropriate HTTP status codes "
            "(200 for success, 404 for not found, "
            "500 for server errors) and include error "
            "details in JSON response body"
        ),
        score=0.92,
        created_at=datetime(2024, 1, 10, 9, 0, 0),
        updated_at=datetime(2024, 1, 25, 16, 45, 0),
        metadata=ReMeMemoryMetadata(
            tags=["api", "rest", "http", "error-handling"],
            step_type="design",
            tools_used=["flask", "fastapi"],
            confidence=0.95,
            freq=8,
            utility=0.9
        ),
        workspace_id="reme_workspace"  # ReMe-specific workspace
    )
    logger.info("   [OK] Created ReMe memory: %s...", reme_memory.when_to_use[:50])

    # Convert memories to VectorNodes and add to vector store
    logger.info("2. Adding memories to vector store...")

    # Create dummy embeddings (in real usage, these would come from an embedding service)
    dummy_embedding = [0.1] * 1536  # 1536 is typical for OpenAI embeddings

    # Add embeddings to nodes before inserting
    ace_node = ace_memory.to_vector_node()
    ace_node.embedding = dummy_embedding

    rb_node = reasoning_bank_memory.to_vector_node()
    rb_node.embedding = dummy_embedding

    reme_node = reme_memory.to_vector_node()
    reme_node.embedding = dummy_embedding

    await vector_store.async_upsert(ace_node)
    await vector_store.async_upsert(rb_node)
    await vector_store.async_upsert(reme_node)

    logger.info("   [OK] Added %d memories to vector store", vector_store.count())

    # Save to separate files using JSONFileConnector
    logger.info("3. Saving memories to separate JSON files...")
    connector = JSONFileConnector(indent=2)

    # Use repository-based path instead of temp directory
    test_output_dir = Path(__file__).parent / "memory_file"
    test_output_dir.mkdir(exist_ok=True)

    # Get all nodes and separate by workspace_id
    all_nodes = vector_store.get_all()

    # Group nodes by workspace_id and save separately
    workspaces = defaultdict(dict)
    for node in all_nodes:
        workspace_id = node.metadata.get("workspace_id", "default")
        workspaces[workspace_id][node.id] = node.to_dict()

    saved_files = {}
    for workspace_id, nodes_dict in workspaces.items():
        file_path = str(test_output_dir / f"{workspace_id}_memories.json")
        connector.save_to_file(file_path, nodes_dict)
        saved_files[workspace_id] = file_path
        logger.info("   [OK] Saved %d memories to: %s_memories.json", len(nodes_dict), workspace_id)
        logger.info("      File size: %d bytes", Path(file_path).stat().st_size)

    # Load from files and verify
    logger.info("4. Loading memories from JSON files...")
    new_vector_store = MemoryVectorStore()

    for workspace_id, file_path in saved_files.items():
        loaded_data = connector.load_from_file(file_path)
        logger.info("   [OK] Loaded %d nodes from %s_memories.json", len(loaded_data), workspace_id)

        # Use the helper method to load into vector store
        await new_vector_store.load_from_dict(loaded_data)

    logger.info("   [OK] Total restored: %d memories to new vector store", new_vector_store.count())

    # Verify the loaded memories
    logger.info("5. Verifying loaded memories...")
    loaded_nodes = new_vector_store.get_all()

    for loaded_node in loaded_nodes:
        memory_type = loaded_node.metadata.get("type")

        if memory_type == "ace_memory":
            # Reconstruct ACE memory
            ace = ACEMemory.from_vector_node(loaded_node)
            assert ace.section == ace_memory.section, "ACE memory section mismatch"
            assert ace.content == ace_memory.content, "ACE memory content mismatch"
            assert ace.workspace_id == "ace_workspace", "ACE workspace_id mismatch"
            logger.info("   [OK] ACE Memory verified: %s (workspace: %s)", ace.section, ace.workspace_id)

        elif memory_type == "reasoning_bank_memory":
            # Reconstruct ReasoningBank memory
            rb = ReasoningBankMemory.from_vector_node(loaded_node)
            assert rb.query == reasoning_bank_memory.query, "ReasoningBank query mismatch"
            assert len(rb.memory) == len(reasoning_bank_memory.memory), "ReasoningBank items count mismatch"
            assert rb.workspace_id == "reasoningbank_workspace", "ReasoningBank workspace_id mismatch"
            logger.info("   [OK] ReasoningBank Memory verified: %s (workspace: %s)", rb.query, rb.workspace_id)

        elif memory_type == "reme_memory":
            # Reconstruct ReMe memory
            reme = ReMeMemory.from_vector_node(loaded_node)
            assert reme.when_to_use == reme_memory.when_to_use, "ReMe when_to_use mismatch"
            assert reme.content == reme_memory.content, "ReMe content mismatch"
            assert reme.score == reme_memory.score, "ReMe score mismatch"
            assert reme.workspace_id == "reme_workspace", "ReMe workspace_id mismatch"
            logger.info(
                "   [OK] ReMe Memory verified: %s... (workspace: %s)",
                reme.when_to_use[:50], reme.workspace_id,
            )

    logger.info("=" * 70)
    logger.info("[PASS] All realistic memory tests passed!")
    logger.info("[DIR] Files saved in: %s", test_output_dir)
    logger.info("=" * 70)


def test_basic_save_load():
    """Test basic save and load functionality."""
    logger.info("Testing basic save/load...")

    connector = JSONFileConnector()
    test_data = {
        "key1": "value1",
        "key2": [1, 2, 3],
        "key3": {"nested": "data"}
    }

    # Use temp directory for testing (auto-cleaned on exit)
    with tempfile.TemporaryDirectory() as temp_dir:
        test_file = os.path.join(temp_dir, "test_output.json")

        # Test save
        connector.save_to_file(test_file, test_data)
        assert Path(test_file).exists(), "File should exist after save"

        # Test load
        loaded_data = connector.load_from_file(test_file)
        assert loaded_data == test_data, "Loaded data should match original"

        logger.info("[OK] Basic save/load works!")

        # Test exists
        assert connector.exists(test_file), "exists() should return True"

        # Test delete
        assert connector.delete(test_file), "delete() should return True"
        assert not connector.exists(test_file), "File should not exist after delete"

        logger.info("[OK] Exists/delete works!")


def test_unicode_support():
    """Test UTF-8 encoding."""
    logger.info("Testing Unicode support...")

    connector = JSONFileConnector()

    # Use temp directory for testing (auto-cleaned on exit)
    with tempfile.TemporaryDirectory() as temp_dir:
        test_file = os.path.join(temp_dir, "unicode_test.json")

        # Test with Unicode data
        test_data = {
            "chinese": "测试",
            "emoji": "🎉",
            "japanese": "テスト"
        }

        connector.save_to_file(test_file, test_data)
        loaded_data = connector.load_from_file(test_file)

        assert loaded_data == test_data, "Unicode should be preserved"

        logger.info("[OK] Unicode support works!")


if __name__ == "__main__":
    # Run tests using pytest
    pytest.main([__file__, "-v"])
