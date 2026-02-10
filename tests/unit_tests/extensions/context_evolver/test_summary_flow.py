# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Test the summary flow.

Supports ACE, ReasoningBank, and ReMe algorithms based on .env configuration.
The summarize() method works the same way for all algorithms.
"""

import asyncio
import pytest
from openjiuwen.core.common.logging import context_engine_logger as logger


from openjiuwen.extensions.context_evolver import TaskMemoryService


@pytest.mark.skip(reason="Temporarily disabled: API_KEY is needed in .env file")
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


@pytest.mark.skip(reason="Temporarily disabled: API_KEY is needed in .env file")
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


@pytest.mark.skip(reason="Temporarily disabled: API_KEY is needed in .env file")
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


if __name__ == "__main__":
    logger.info("Running summary flow tests...")

    logger.info("1. Testing reasoning bank summarize and retrieve cycle...")
    asyncio.run(test_rb_summarize_and_retrieve())

    logger.info("2. Testing ReMe summarize and retrieve cycle...")
    asyncio.run(test_reme_summarize_and_retrieve())

    logger.info("3. Testing ACE summarize and retrieve cycle...")
    asyncio.run(test_ace_summarize_and_retrieve())

    logger.info("All tests passed!")
