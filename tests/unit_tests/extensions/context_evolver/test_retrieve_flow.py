# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Test the minimal retrieve flow.

Runs all tests against each algorithm: ACE, ReasoningBank, and ReMe.
"""

import sys
import os
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Path setup (required for direct execution via `uv run python`)
# ---------------------------------------------------------------------------
_here = os.path.dirname(os.path.abspath(__file__))
_agent_core_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(_here))))
if _agent_core_root not in sys.path:
    sys.path.append(_agent_core_root)

import pytest
from openjiuwen.core.common.logging import context_engine_logger as logger

from openjiuwen.extensions.context_evolver import (
    TaskMemoryService,
    AddMemoryRequest,
)
from openjiuwen.extensions.context_evolver.core import config as app_config

# ---------------------------------------------------------------------------
# Skip marker – tests are skipped when API_KEY is absent or a placeholder.
# Set SKIP_API_TESTS = True to force-skip regardless of .env contents.
# ---------------------------------------------------------------------------
SKIP_API_TESTS = False


def _api_key_missing():
    """Check if API_KEY is missing or a placeholder value."""
    api_key = app_config.get("API_KEY")
    return not api_key or api_key == "your-api-key-here" or api_key.startswith("sk-proj-xxx")


requires_api_key = pytest.mark.skipif(
    SKIP_API_TESTS or _api_key_missing(),
    reason="API_KEY not configured - API_KEY is needed in .env file. "
           "Please create .env file by referring to .env.example",
)


ALGORITHMS = ["ACE", "ReasoningBank", "ReMe", "RefCon", "DivCon"]


def _create_service(algorithm):
    """Create a TaskMemoryService configured for the given algorithm."""
    return TaskMemoryService(
        llm_model="gpt-3.5-turbo",
        embedding_model="text-embedding-3-small",
        retrieval_algo=algorithm,
        summary_algo=algorithm,
    )


def _make_add_request(algorithm, content, identifier):
    """Create an AddMemoryRequest appropriate for the given algorithm."""
    if algorithm == "ReasoningBank":
        return AddMemoryRequest(
            content=content,
            title=f"Memory {identifier}",
            description=f"Description for memory {identifier}",
        )
    elif algorithm in ("ReMe", "RefCon", "DivCon"):
        return AddMemoryRequest(
            content=content,
            when_to_use=f"When to use memory {identifier}",
        )
    else:  # ACE
        return AddMemoryRequest(
            content=content,
            section="test",
        )


@requires_api_key
@pytest.mark.parametrize("algorithm", ALGORITHMS)
@pytest.mark.asyncio
async def test_add_and_retrieve_memory(algorithm):
    """Test adding a memory and retrieving it for each algorithm."""
    service = _create_service(algorithm)
    assert service.summary_algorithm == algorithm

    user_id = f"test_user_{algorithm}"
    logger.info("Testing add_and_retrieve with algorithm: %s", algorithm)

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
    elif algorithm in ("ReMe", "RefCon", "DivCon"):
        await service.add_memory(
            user_id=user_id,
            request=AddMemoryRequest(
                content=content,
                when_to_use="When implementing caching in Python",
            ),
        )
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

    logger.info("[%s] retrieved result: %s", algorithm, result)

    assert result["status"] == "success"
    assert len(result["retrieved_memory"]) > 0
    assert "memory_string" in result

    logger.info("[%s] memory_string: %s...", algorithm,
                result['memory_string'][:200] if result['memory_string'] else 'No memories')

    if algorithm == "ReasoningBank":
        assert result["retrieved_memory"][0]["title"] == "Python Caching Strategy"
        assert result["retrieved_memory"][0]["description"] == "How to implement caching in Python applications"
        assert result["retrieved_memory"][0]["content"] == content
    elif algorithm in ("ReMe", "RefCon", "DivCon"):
        assert result["retrieved_memory"][0]["when_to_use"] == "When implementing caching in Python"
        assert result["retrieved_memory"][0]["content"] == content
    else:  # ACE
        assert content in result["retrieved_memory"][0]["content"]
        assert result["retrieved_memory"][0]["section"] == "python_best_practices"

    logger.info("[%s] test_add_and_retrieve_memory PASSED", algorithm)


@requires_api_key
@pytest.mark.parametrize("algorithm", ALGORITHMS)
@pytest.mark.asyncio
async def test_retrieve_without_memories(algorithm):
    """Test retrieving when no memories exist for each algorithm."""
    service = _create_service(algorithm)

    user_id = f"empty_user_{algorithm}"
    logger.info("Testing retrieve_without_memories with algorithm: %s", algorithm)

    result = await service.retrieve(
        user_id=user_id,
        query="What is Python?",
    )

    assert "status" in result
    assert result["status"] == "success"
    assert "memory_string" in result
    assert "retrieved_memory" in result
    assert len(result["retrieved_memory"]) == 0

    logger.info("[%s] Retrieve without memories: status=%s, memories=%d",
                algorithm, result['status'], len(result['retrieved_memory']))
    logger.info("[%s] test_retrieve_without_memories PASSED", algorithm)


@requires_api_key
@pytest.mark.parametrize("algorithm", ALGORITHMS)
@pytest.mark.asyncio
async def test_playbook_operations(algorithm):
    """Test playbook get and clear operations for each algorithm."""
    service = _create_service(algorithm)

    user_id = f"test_user_playbook_{algorithm}"
    logger.info("Testing playbook_operations with algorithm: %s", algorithm)

    # Add two memories
    await service.add_memory(
        user_id=user_id,
        request=_make_add_request(algorithm, "Content 1", "1"),
    )
    await service.add_memory(
        user_id=user_id,
        request=_make_add_request(algorithm, "Content 2", "2"),
    )

    # Get playbook
    playbook = await service.get_playbook(user_id)
    logger.info("[%s] playbook: %s", algorithm, playbook)
    assert playbook["user_id"] == user_id
    assert playbook["memory_count"] == 2
    assert "memories" in playbook
    assert len(playbook["memories"]) == 2

    # Verify algorithm-specific fields in memories
    if algorithm == "ReasoningBank":
        for mem in playbook["memories"]:
            assert "memory" in mem
            assert len(mem["memory"]) > 0
        memory_contents = [mem["memory"][0].content for mem in playbook["memories"]]
        assert "Content 1" in memory_contents
        assert "Content 2" in memory_contents
        memory_titles = [mem["memory"][0].title for mem in playbook["memories"]]
        assert "Memory 1" in memory_titles
        assert "Memory 2" in memory_titles
    elif algorithm in ("ReMe", "RefCon", "DivCon"):
        memory_contents = [m["content"] for m in playbook["memories"]]
        assert "Content 1" in memory_contents or any("Content 1" in str(m) for m in playbook["memories"])
        assert "Content 2" in memory_contents or any("Content 2" in str(m) for m in playbook["memories"])
    else:  # ACE
        memory_contents = [m["content"] for m in playbook["memories"]]
        assert any("Content 1" in c for c in memory_contents), f"Content 1 not found in {memory_contents}"
        assert any("Content 2" in c for c in memory_contents), f"Content 2 not found in {memory_contents}"

    # Clear playbook
    result = await service.clear_playbook(user_id)
    assert result["status"] == "success"

    # Verify cleared
    playbook = await service.get_playbook(user_id)
    assert playbook["memory_count"] == 0
    assert playbook["memories"] == []

    logger.info("[%s] test_playbook_operations PASSED", algorithm)


# ===========================================================================
# Mock-based tests — no API key required
# ===========================================================================

class TestAlgorithmNormalization:
    """Tests for normalize_algo_name static method (no API key needed)."""

    @staticmethod
    def test_ace_normalized():
        assert TaskMemoryService.normalize_algo_name("ACE") == "ACE"

    @staticmethod
    def test_rb_normalized_to_reasoning_bank():
        assert TaskMemoryService.normalize_algo_name("RB") == "ReasoningBank"

    @staticmethod
    def test_reasoningbank_normalized():
        assert TaskMemoryService.normalize_algo_name("REASONINGBANK") == "ReasoningBank"

    @staticmethod
    def test_reme_normalized():
        assert TaskMemoryService.normalize_algo_name("REME") == "ReMe"

    @staticmethod
    def test_refcon_normalized():
        assert TaskMemoryService.normalize_algo_name("REFCON") == "RefCon"

    @staticmethod
    def test_divcon_normalized():
        assert TaskMemoryService.normalize_algo_name("DIVCON") == "DivCon"

    @staticmethod
    def test_invalid_algorithm_raises():
        with pytest.raises(Exception):
            TaskMemoryService.normalize_algo_name("INVALID_ALGO")


class TestServiceConstruction:
    """Tests for TaskMemoryService construction (mocked LLM/embedding)."""

    @staticmethod
    def _make_service(**kwargs):
        from openjiuwen.extensions.context_evolver.core import config as _cfg
        snap = _cfg.snapshot()
        try:
            _cfg.set_value("API_KEY", "test-key")
            _cfg.delete("PERSIST_TYPE")
            with patch(
                "openjiuwen.extensions.context_evolver.service.task_memory_service.OpenAILLMWrapper"
            ) as mock_llm_cls, patch(
                "openjiuwen.extensions.context_evolver.service.task_memory_service.OpenAIEmbeddingWrapper"
            ) as mock_emb_cls:
                mock_llm_cls.return_value = MagicMock()
                mock_emb_cls.return_value = MagicMock()
                svc = TaskMemoryService(
                    llm_model="gpt-test",
                    embedding_model="emb-test",
                    api_key="test-key",
                    **kwargs,
                )
        finally:
            _cfg.restore(snap)
        return svc

    @staticmethod
    def test_explicit_ace_algorithm():
        svc = TestServiceConstruction._make_service(retrieval_algo="ACE", summary_algo="ACE")
        assert svc.summary_algorithm == "ACE"
        assert svc.retrieval_algorithm == "ACE"

    @staticmethod
    def test_retrieval_algo_stored_correctly():
        for key, expected in [("ACE", "ACE"), ("RB", "ReasoningBank"), ("REME", "ReMe"),
                               ("REFCON", "RefCon"), ("DIVCON", "DivCon")]:
            svc = TestServiceConstruction._make_service(retrieval_algo=key, summary_algo=key)
            assert svc.retrieval_algorithm == expected, f"Failed for {key}"
            assert svc.summary_algorithm == expected, f"Failed for {key}"

    @staticmethod
    def test_persist_type_json_by_default():
        svc = TestServiceConstruction._make_service()
        assert svc.persist_type == "json"

    @staticmethod
    def test_persist_type_json_stored():
        svc = TestServiceConstruction._make_service(
            persist_type="json",
            persist_path="./tmp/{algo_name}/{user_id}.json",
        )
        assert svc.persist_type == "json"
        assert svc.persist_path == "./tmp/{algo_name}/{user_id}.json"

    @staticmethod
    def test_persist_type_auto_stored():
        svc = TestServiceConstruction._make_service(persist_type="auto")
        assert svc.persist_type == "auto"

    @staticmethod
    def test_milvus_params_stored():
        svc = TestServiceConstruction._make_service(
            persist_type="milvus",
            milvus_host="milvus-host",
            milvus_port=9999,
            milvus_collection="my_coll",
        )
        assert svc.milvus_host == "milvus-host"
        assert svc.milvus_port == 9999
        assert svc.milvus_collection == "my_coll"

    @staticmethod
    def test_retrieve_flow_created_for_each_algorithm():
        for algo in ["ACE", "RB", "REME", "REFCON", "DIVCON"]:
            svc = TestServiceConstruction._make_service(retrieval_algo=algo, summary_algo="ACE")
            assert svc.retrieve_flow is not None, f"retrieve_flow is None for {algo}"

    @staticmethod
    def test_summary_flow_created_for_each_algorithm():
        for algo in ["ACE", "RB", "REME", "REFCON", "DIVCON"]:
            svc = TestServiceConstruction._make_service(retrieval_algo="ACE", summary_algo=algo)
            assert svc.summary_flow is not None, f"summary_flow is None for {algo}"


if __name__ == "__main__":
    # Run tests using pytest
    pytest.main([__file__, "-v"])
