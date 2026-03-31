# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Test the summary flow.

Supports ACE, ReasoningBank, and ReMe algorithms based on .env configuration.
The summarize() method works the same way for all algorithms.
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


from openjiuwen.extensions.context_evolver import TaskMemoryService
from openjiuwen.extensions.context_evolver.core import config as app_config
from openjiuwen.extensions.context_evolver.core.op.sequential_op import SequentialOp
from openjiuwen.extensions.context_evolver.summary.task.ace.update import (
    PersistMemoryOp as ACEPersist,
)
from openjiuwen.extensions.context_evolver.summary.task.reasoning_bank.update import (
    PersistMemoryOp as RBPersist,
)
from openjiuwen.extensions.context_evolver.summary.task.reme.update import (
    PersistMemoryOp as ReMePersist,
)

# Set to True to force-skip tests that require API_KEY configured in .env file.
# Set to False to auto-detect: tests run only if a valid API_KEY is found in .env.
SKIP_API_TESTS = False


def _api_key_missing():
    """Check if API_KEY is missing or placeholder."""
    api_key = app_config.get("API_KEY")
    return not api_key or api_key == "your-api-key-here" or api_key.startswith("sk-proj-xxx")


requires_api_key = pytest.mark.skipif(
    SKIP_API_TESTS or _api_key_missing(),
    reason="API_KEY not configured - API_KEY is needed in .env file. "
           "Please create .env file by referring to .env.example",
)


@requires_api_key
@pytest.mark.asyncio
async def test_rb_summarize_and_retrieve():
    """Test full cycle: summarize then retrieve."""
    service = TaskMemoryService(
        llm_model="gpt-3.5-turbo",
        embedding_model="text-embedding-3-small",
        retrieval_algo="RB",
        summary_algo="RB",
    )

    algorithm = service.summary_algorithm
    logger.info("Using algorithm: %s", algorithm)

    user_id = "test_user_cycle"

    # Add trajectories
    trajectories = [
        {
            "query": "How do I implement caching in Python?",
            "response": "I used functools.lru_cache decorator. "
                       "Added @lru_cache(maxsize=128) and saw immediate speedup.",
            "feedback": "helpful",
        },
        {
            "query": "What's the best way to cache expensive computations?",
            "response": "I implemented memoization with lru_cache. "
                       "Works great for pure functions with hashable arguments.",
            "feedback": "helpful",
        },
    ]

    # Summarize - use new signature
    summary_result = await service.summarize(
        user_id=user_id,
        matts="none",
        query="How do I implement caching in Python?",
        trajectories=trajectories
    )
    logger.info("%s", summary_result)
    assert summary_result["status"] == "success"

    # Now retrieve using the learned memories
    retrieve_result = await service.retrieve(
        user_id=user_id,
        query="How should I implement caching?",
    )

    logger.info("%s retrieved result starts %s", '*' * 50, '*' * 50)
    # Assertions - use new response format
    logger.info("%s", retrieve_result)
    logger.info("%s retrieved result end %s", '*' * 50, '*' * 50)

    logger.info("%s Summarized with status starts %s", '*' * 50, '*' * 50)
    logger.info("%s", summary_result['status'])
    logger.info("%s Summarized with status ends %s", '*' * 50, '*' * 50)

    logger.info("%s memories size starts %s", '*' * 50, '*' * 50)
    logger.info("Created %d memories", len(summary_result['memory']))
    logger.info("%s memories size ends %s", '*' * 50, '*' * 50)

    logger.info("%s retrieved result starts %s", '*' * 50, '*' * 50)
    logger.info("Retrieved %d memories", len(retrieve_result['retrieved_memory']))
    if algorithm == "ReasoningBank":
        for index in range(len(retrieve_result['retrieved_memory'])):
            mem = retrieve_result['retrieved_memory'][index]
            logger.info("Memory %d Title: %s", index + 1, mem['title'])
            logger.info("Memory %d Desc: %s", index + 1, mem['description'])
            logger.info("Memory %d Content: %s", index + 1, mem['content'])
    elif algorithm == "ACE":
        for index in range(len(retrieve_result['retrieved_memory'])):
            mem = retrieve_result['retrieved_memory'][index]
            logger.info("Memory %d Content: %s", index + 1, mem['content'])
    logger.info("%s retrieved result end %s", '*' * 50, '*' * 50)

    # Assertions - new response format
    assert "status" in retrieve_result
    assert retrieve_result["status"] == "success"
    assert "memory_string" in retrieve_result
    assert "retrieved_memory" in retrieve_result


# ============================================================================
# ReMe Algorithm Tests
# ============================================================================


@requires_api_key
@pytest.mark.asyncio
async def test_reme_summarize_and_retrieve():
    """Test ReMe algorithm full cycle: summarize then retrieve."""
    service = TaskMemoryService(
        llm_model="gpt-3.5-turbo",
        embedding_model="text-embedding-3-small",
        retrieval_algo="REME",
        summary_algo="REME",
    )

    algorithm = service.summary_algorithm
    logger.info("Using algorithm: %s", algorithm)
    assert algorithm == "ReMe", f"Expected ReMe algorithm, got {algorithm}"

    user_id = "test_reme_cycle"

    # ReMe test trajectories
    trajectories = [
        (
            "USER: How do I get the most played songs "
            "from my Spotify library?\n"
            "ASSISTANT: I'll help you retrieve the most "
            "played songs. First, let me check the "
            "Spotify API.\n"
            "ACTION: spotify.search_songs("
            "genre='R&B', sort_by='-play_count', "
            "page_limit=10)\n"
            'OBSERVATION: [{"song_id": 88, "title": '
            '"Crimson Skies", "play_count": 995}, '
            '{"song_id": 185, "title": "Silent Sea", '
            '"play_count": 990}]\n'
            "ASSISTANT: Based on the API response, the "
            "most played R&B songs are "
            '"Crimson Skies" and "Silent Sea".'
        ),
        (
            "USER: What are the top songs in my "
            "Spotify playlists?\n"
            "ASSISTANT: Let me fetch your playlist "
            "library and find the top songs.\n"
            "ACTION: spotify.show_playlist_library()\n"
            'OBSERVATION: [{"playlist_id": 1, '
            '"name": "My Favorites", '
            '"song_count": 25}]\n'
            "ACTION: spotify.show_playlist("
            "playlist_id=1)\n"
            'OBSERVATION: [{"song_id": 12, '
            '"title": "Haunted Memories", '
            '"play_count": 965}]\n'
            "ASSISTANT: Your top song from playlists "
            'is "Haunted Memories" with 965 plays.'
        ),
        (
            "USER: Show me my most listened "
            "R&B tracks\n"
            "ASSISTANT: I'll search for your most "
            "played R&B tracks across your library.\n"
            "ACTION: spotify.show_song_library("
            "genre='R&B', sort_by='-play_count')\n"
            'OBSERVATION: [{"title": "Mysteries of '
            'the Silent Sea", "play_count": 990}, '
            '{"title": "Sorrow\'s Symphony", '
            '"play_count": 975}]\n'
            "ASSISTANT: Your most listened R&B tracks "
            'are "Mysteries of the Silent Sea" and '
            '"Sorrow\'s Symphony".'
        ),
    ]

    # First, summarize the trajectories
    summary_result = await service.summarize(
        user_id=user_id,
        matts="parallel",
        query="How to work with Spotify API for song data?",
        trajectories=trajectories,
        score=[0.5, 1.0, 1.0]
    )

    logger.info("%s Summary Result %s", '=' * 50, '=' * 50)
    logger.info("%s", summary_result)
    assert summary_result["status"] == "success"

    # Now retrieve using a similar query
    retrieve_result = await service.retrieve(
        user_id=user_id,
        query="How do I get the most played songs from my Spotify library?",
    )

    logger.info("%s Retrieve Result %s", '=' * 50, '=' * 50)
    logger.info("%s", retrieve_result)

    # Assertions for retrieve
    assert "status" in retrieve_result
    assert retrieve_result["status"] == "success"
    assert "memory_string" in retrieve_result
    assert "retrieved_memory" in retrieve_result

    # ReMe specific: check retrieved memory structure
    if retrieve_result["retrieved_memory"]:
        logger.info(
            "Retrieved %d memories",
            len(retrieve_result['retrieved_memory']),
        )
        for idx, memory in enumerate(retrieve_result["retrieved_memory"]):
            logger.info("Memory %d:", idx + 1)
            if "when_to_use" in memory:
                logger.info("  When to use: %s", memory['when_to_use'])
            if "content" in memory:
                content_preview = memory['content'][:100]
                logger.info("  Content: %s...", content_preview)

    logger.info("ReMe cycle test completed successfully")


# ============================================================================
# ACE Algorithm Tests
# ============================================================================


@requires_api_key
@pytest.mark.asyncio
async def test_ace_summarize_and_retrieve():
    """Test ACE algorithm full cycle: summarize then retrieve."""
    service = TaskMemoryService(
        llm_model="gpt-3.5-turbo",
        embedding_model="text-embedding-3-small",
        retrieval_algo="ACE",
        summary_algo="ACE",
    )

    algorithm = service.summary_algorithm
    logger.info("Using algorithm: %s", algorithm)
    assert algorithm == "ACE", f"Expected ACE algorithm, got {algorithm}"

    user_id = "test_ace_cycle"

    # ACE test trajectory
    trajectory = (
        "USER: Give me a comma-separated list of top 4 "
        "most played r&b song titles from across my "
        "Spotify song, album and playlist libraries.\n"
        "ASSISTANT: I'll help you find the top 4 most "
        "played R&B songs. Let me search across your "
        "libraries.\n"
        "ACTION: spotify.search_songs("
        "genre='R&B', sort_by='-play_count', "
        "page_limit=4)\n"
        'OBSERVATION: [{"title": "Crimson Skies of '
        'Longing", "play_count": 995}, {"title": '
        '"Mysteries of the Silent Sea", "play_count": '
        '990}, {"title": "Sorrow\'s Silent Symphony", '
        '"play_count": 975}, {"title": "Crimson Veil", '
        '"play_count": 972}]\n'
        "ASSISTANT: Based on my search, the top 4 most "
        "played R&B songs are:\n"
        "Crimson Skies of Longing, Mysteries of the "
        "Silent Sea, Sorrow's Silent Symphony, "
        "Crimson Veil"
    )

    ground_truth = "Crimson Skies of Longing, Mysteries of the Silent Sea, Sorrow's Silent Symphony, Crimson Veil"

    feedback = (
        "The agent correctly identified the top 4 R&B songs "
        "by using the search_songs API with the correct "
        "genre filter and sort parameter."
    )

    # First, summarize the trajectory with ground truth and feedback
    summary_result = await service.summarize(
        user_id=user_id,
        matts="none",
        query=(
            "Give me a comma-separated list of top 4 most played "
            "r&b song titles from across my Spotify song, "
            "album and playlist libraries."
        ),
        trajectories=[trajectory],
        ground_truth=ground_truth,
        feedback=[feedback]
    )

    logger.info("%s ACE Summary Result %s", '=' * 50, '=' * 50)
    logger.info("%s", summary_result)
    assert summary_result["status"] == "success"

    # Now retrieve using a similar query
    retrieve_result = await service.retrieve(
        user_id=user_id,
        query="How do I get songs from my Spotify library?",
    )

    logger.info("%s ACE Retrieve Result %s", '=' * 50, '=' * 50)
    logger.info("%s", retrieve_result)

    # Assertions for retrieve
    assert "status" in retrieve_result
    assert retrieve_result["status"] == "success"
    assert "memory_string" in retrieve_result
    assert "retrieved_memory" in retrieve_result

    # ACE specific: check retrieved memory structure
    if retrieve_result["retrieved_memory"]:
        logger.info(
            "Retrieved %d memories",
            len(retrieve_result['retrieved_memory']),
        )
        for idx, memory in enumerate(retrieve_result["retrieved_memory"]):
            logger.info("Memory %d:", idx + 1)
            if "section" in memory:
                logger.info("  Section: %s", memory['section'])
            if "content" in memory:
                content_preview = memory['content'][:100]
                logger.info("  Content: %s...", content_preview)

    logger.info("ACE cycle test completed successfully")


# ============================================================================
# RefCon Algorithm Tests
# ============================================================================


@requires_api_key
@pytest.mark.asyncio
async def test_refcon_summarize_and_retrieve():
    """Test RefCon algorithm full cycle: summarize then retrieve.

    RefCon uses comparative extraction across all trajectories (ReMeComparativeAllExtractionOp)
    with deduplication enabled and validation disabled.
    """
    service = TaskMemoryService(
        llm_model="gpt-3.5-turbo",
        embedding_model="text-embedding-3-small",
        retrieval_algo="REFCON",
        summary_algo="REFCON",
    )

    algorithm = service.summary_algorithm
    logger.info("Using algorithm: %s", algorithm)
    assert algorithm == "RefCon", f"Expected RefCon algorithm, got {algorithm}"

    user_id = "test_refcon_cycle"

    # RefCon test trajectories — multiple trajectories for comparative extraction
    trajectories = [
        (
            "USER: How do I search for files by extension in Linux?\n"
            "ASSISTANT: I'll help you find files by extension.\n"
            "ACTION: shell.run(command='find /home -name \"*.py\" -type f')\n"
            "OBSERVATION: /home/user/scripts/process.py\n/home/user/projects/app.py\n"
            "ASSISTANT: Found 2 Python files: process.py and app.py."
        ),
        (
            "USER: How can I locate all Python files on the system?\n"
            "ASSISTANT: Let me search for Python files using find.\n"
            "ACTION: shell.run(command='find / -name \"*.py\" -type f 2>/dev/null')\n"
            "OBSERVATION: /usr/lib/python3/dist-packages/apt/__init__.py\n"
            "/home/user/scripts/process.py\n"
            "ASSISTANT: Found multiple Python files across the system."
        ),
        (
            "USER: Find Python scripts but only in the current user's home directory.\n"
            "ASSISTANT: I'll narrow the search to the home directory.\n"
            "ACTION: shell.run(command='find ~ -name \"*.py\" -type f')\n"
            "OBSERVATION: /home/user/scripts/process.py\n/home/user/projects/app.py\n"
            "ASSISTANT: Found 2 Python scripts in your home directory."
        ),
    ]

    # Summarize trajectories with RefCon (comparative across all)
    summary_result = await service.summarize(
        user_id=user_id,
        matts="parallel",
        query="How do I find Python files in Linux?",
        trajectories=trajectories,
        score=[1.0, 0.8, 1.0],
    )

    logger.info("%s RefCon Summary Result %s", "=" * 50, "=" * 50)
    logger.info("%s", summary_result)
    assert summary_result["status"] == "success"

    # Retrieve using a similar query
    retrieve_result = await service.retrieve(
        user_id=user_id,
        query="How do I search for Python files on Linux?",
    )

    logger.info("%s RefCon Retrieve Result %s", "=" * 50, "=" * 50)
    logger.info("%s", retrieve_result)

    assert "status" in retrieve_result
    assert retrieve_result["status"] == "success"
    assert "memory_string" in retrieve_result
    assert "retrieved_memory" in retrieve_result

    if retrieve_result["retrieved_memory"]:
        logger.info("Retrieved %d memories", len(retrieve_result["retrieved_memory"]))
        for idx, memory in enumerate(retrieve_result["retrieved_memory"]):
            logger.info("Memory %d:", idx + 1)
            if "when_to_use" in memory:
                logger.info("  When to use: %s", memory["when_to_use"])
            if "content" in memory:
                logger.info("  Content: %s...", memory["content"][:100])

    logger.info("RefCon cycle test completed successfully")


# ============================================================================
# DivCon Algorithm Tests
# ============================================================================


@requires_api_key
@pytest.mark.asyncio
async def test_divcon_summarize_and_retrieve():
    """Test DivCon algorithm full cycle: summarize then retrieve.

    DivCon uses the same pipeline as RefCon (ReMeComparativeAllExtractionOp)
    but is intended for diverse/contrastive trajectory sets.
    """
    service = TaskMemoryService(
        llm_model="gpt-3.5-turbo",
        embedding_model="text-embedding-3-small",
        retrieval_algo="DIVCON",
        summary_algo="DIVCON",
    )

    algorithm = service.summary_algorithm
    logger.info("Using algorithm: %s", algorithm)
    assert algorithm == "DivCon", f"Expected DivCon algorithm, got {algorithm}"

    user_id = "test_divcon_cycle"

    # DivCon test trajectories — diverse approaches to the same task
    trajectories = [
        (
            "USER: List all running Docker containers.\n"
            "ASSISTANT: I'll list all running containers.\n"
            "ACTION: shell.run(command='docker ps')\n"
            "OBSERVATION: CONTAINER ID   IMAGE     COMMAND   STATUS\n"
            "abc123         nginx     ...       Up 2 hours\n"
            "ASSISTANT: Found 1 running container: nginx (abc123)."
        ),
        (
            "USER: Show me Docker containers that are currently active.\n"
            "ASSISTANT: Let me check for active containers.\n"
            "ACTION: shell.run(command='docker container ls')\n"
            "OBSERVATION: CONTAINER ID   IMAGE     STATUS\n"
            "abc123         nginx     Up 2 hours\n"
            "def456         redis     Up 30 minutes\n"
            "ASSISTANT: There are 2 active containers: nginx and redis."
        ),
        (
            "USER: List Docker containers including stopped ones.\n"
            "ASSISTANT: I'll include stopped containers in the listing.\n"
            "ACTION: shell.run(command='docker ps -a')\n"
            "OBSERVATION: CONTAINER ID   IMAGE     STATUS\n"
            "abc123         nginx     Up 2 hours\n"
            "def456         redis     Exited (0) 1 day ago\n"
            "ASSISTANT: Found 2 containers: nginx (running), redis (stopped)."
        ),
    ]

    # Summarize trajectories with DivCon
    summary_result = await service.summarize(
        user_id=user_id,
        matts="none",
        query="How do I list Docker containers?",
        trajectories=trajectories,
        score=[1.0, 1.0, 0.9],
    )

    logger.info("%s DivCon Summary Result %s", "=" * 50, "=" * 50)
    logger.info("%s", summary_result)
    assert summary_result["status"] == "success"

    # Retrieve using a related query
    retrieve_result = await service.retrieve(
        user_id=user_id,
        query="How can I view active Docker containers?",
    )

    logger.info("%s DivCon Retrieve Result %s", "=" * 50, "=" * 50)
    logger.info("%s", retrieve_result)

    assert "status" in retrieve_result
    assert retrieve_result["status"] == "success"
    assert "memory_string" in retrieve_result
    assert "retrieved_memory" in retrieve_result

    if retrieve_result["retrieved_memory"]:
        logger.info("Retrieved %d memories", len(retrieve_result["retrieved_memory"]))
        for idx, memory in enumerate(retrieve_result["retrieved_memory"]):
            logger.info("Memory %d:", idx + 1)
            if "when_to_use" in memory:
                logger.info("  When to use: %s", memory["when_to_use"])
            if "content" in memory:
                logger.info("  Content: %s...", memory["content"][:100])

    logger.info("DivCon cycle test completed successfully")


# ============================================================================
# Parametrized Tests for All Five Algorithms
# ============================================================================

ALL_ALGORITHMS = [
    ("ACE", "ACE"),
    ("RB", "ReasoningBank"),
    ("REME", "ReMe"),
    ("REFCON", "RefCon"),
    ("DIVCON", "DivCon"),
]


@requires_api_key
@pytest.mark.parametrize("algo_key,algo_name", ALL_ALGORITHMS)
@pytest.mark.asyncio
async def test_algorithm_normalize(algo_key, algo_name):
    """Test that each algorithm key normalizes to the expected name."""
    service = TaskMemoryService(
        llm_model="gpt-3.5-turbo",
        embedding_model="text-embedding-3-small",
        retrieval_algo=algo_key,
        summary_algo=algo_key,
    )
    assert service.summary_algorithm == algo_name, (
        f"Expected '{algo_name}', got '{service.summary_algorithm}'"
    )
    assert service.retrieval_algorithm == algo_name, (
        f"Expected retrieval '{algo_name}', got '{service.retrieval_algorithm}'"
    )
    logger.info("Algorithm normalization OK: %s -> %s", algo_key, algo_name)


# ===========================================================================
# Mock-based tests — no API key required
# ===========================================================================

class TestSummaryFlowMock:
    """Mock-based tests for summary flow construction (no API key needed)."""

    @staticmethod
    def _make_service(**kwargs):
        snap = app_config.snapshot()
        try:
            app_config.set_value("API_KEY", "test-key")
            app_config.delete("PERSIST_TYPE")
            with patch(
                "openjiuwen.extensions.context_evolver.service.task_memory_service.OpenAILLMWrapper"
            ) as mock_llm, patch(
                "openjiuwen.extensions.context_evolver.service.task_memory_service.OpenAIEmbeddingWrapper"
            ) as mock_emb:
                mock_llm.return_value = MagicMock()
                mock_emb.return_value = MagicMock()
                svc = TaskMemoryService(
                    llm_model="gpt-test",
                    embedding_model="emb-test",
                    api_key="test-key",
                    **kwargs,
                )
        finally:
            app_config.restore(snap)
        return svc

    @staticmethod
    def _last_op(flow):
        """Return the last op in a SequentialOp chain, or the flow itself."""
        if isinstance(flow, SequentialOp) and flow.ops:
            return flow.ops[-1]
        return flow

    @staticmethod
    def test_no_persist_op_when_persist_type_none():
        """When persist_type is None the last op must NOT be a PersistMemoryOp."""
        persist_ops = (ACEPersist, RBPersist, ReMePersist)

        for algo in ["ACE", "RB", "REME", "REFCON", "DIVCON"]:
            svc = TestSummaryFlowMock._make_service(summary_algo=algo, persist_type=None)
            last = TestSummaryFlowMock._last_op(svc.summary_flow)
            assert not isinstance(last, persist_ops), \
                f"Unexpected PersistMemoryOp at end of {algo} flow when persist_type=None"

    @staticmethod
    def test_persist_op_appended_when_persist_type_json_ace():
        svc = TestSummaryFlowMock._make_service(summary_algo="ACE", persist_type="json")
        last = TestSummaryFlowMock._last_op(svc.summary_flow)
        assert isinstance(last, ACEPersist)

    @staticmethod
    def test_persist_op_appended_when_persist_type_json_rb():
        svc = TestSummaryFlowMock._make_service(summary_algo="RB", persist_type="json")
        last = TestSummaryFlowMock._last_op(svc.summary_flow)
        assert isinstance(last, RBPersist)

    @staticmethod
    def test_persist_op_appended_when_persist_type_json_reme():
        svc = TestSummaryFlowMock._make_service(summary_algo="REME", persist_type="json")
        last = TestSummaryFlowMock._last_op(svc.summary_flow)
        assert isinstance(last, ReMePersist)

    @staticmethod
    def test_persist_op_appended_refcon():
        svc = TestSummaryFlowMock._make_service(summary_algo="REFCON", persist_type="json")
        last = TestSummaryFlowMock._last_op(svc.summary_flow)
        assert isinstance(last, ReMePersist)

    @staticmethod
    def test_persist_op_appended_divcon():
        svc = TestSummaryFlowMock._make_service(summary_algo="DIVCON", persist_type="json")
        last = TestSummaryFlowMock._last_op(svc.summary_flow)
        assert isinstance(last, ReMePersist)

    @staticmethod
    def test_persist_path_forwarded_to_op():
        svc = TestSummaryFlowMock._make_service(
            summary_algo="ACE",
            persist_type="json",
            persist_path="./data/{algo_name}/{user_id}.json",
        )
        last = TestSummaryFlowMock._last_op(svc.summary_flow)
        assert last.helper.persist_path == "./data/{algo_name}/{user_id}.json"

    @staticmethod
    def test_reconfigure_preserves_persist_type():
        """After reconfigure(), the new summary flow should still have PersistMemoryOp."""
        svc = TestSummaryFlowMock._make_service(summary_algo="ACE", persist_type="json")
        svc.reconfigure("RB")
        last = TestSummaryFlowMock._last_op(svc.summary_flow)
        assert isinstance(last, RBPersist)
        assert svc.summary_algorithm == "ReasoningBank"

    @staticmethod
    def test_reconfigure_without_persist_no_persist_op():
        """After reconfigure() when persist_type=None, still no PersistMemoryOp."""
        persist_ops = (ACEPersist, RBPersist, ReMePersist)

        svc = TestSummaryFlowMock._make_service(summary_algo="ACE", persist_type=None)
        svc.reconfigure("REME")
        last = TestSummaryFlowMock._last_op(svc.summary_flow)
        assert not isinstance(last, persist_ops)

    @staticmethod
    def test_all_algorithm_normalizations():
        """Verify all five algorithm keys normalize to the expected display names."""
        expected = {
            "ACE": "ACE",
            "RB": "ReasoningBank",
            "REASONINGBANK": "ReasoningBank",
            "REME": "ReMe",
            "REFCON": "RefCon",
            "DIVCON": "DivCon",
        }
        for key, name in expected.items():
            result = TaskMemoryService.normalize_algo_name(key)
            assert result == name, f"{key} -> expected {name}, got {result}"


if __name__ == "__main__":
    # Run tests using pytest
    pytest.main([__file__, "-v"])
