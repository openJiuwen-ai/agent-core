"""
Data loading utilities for graph memory test data.

Loads conversation JSON files and converts messages into a uniform format suitable for graph memory pipelines.
Supports chunking conversations with optional overlap for sliding-window processing.
"""

import glob
import json
from pathlib import Path
from typing import Dict, List

# Mapping from short agent names to full descriptive names
MAP_AGENT_TO_FULLNAME: Dict[str, str] = {
    "小智": "技术向导型AI助手",
    "小赢": "营销推荐型AI助手",
    "小优": "贴心关怀型AI客服",
    "小鑫": "规则严谨型AI客服",
    "小艺": "手机内置个人生活助手",
}


def chunk_conv(messages: List[Dict], chunk: int, overlap_last: int = 0):
    """Yield consecutive chunks of messages with optional overlap.

    Args:
        messages: List of message dicts to chunk.
        chunk: Number of messages per chunk.
        overlap_last: Number of messages to overlap with the previous chunk
            (start index is shifted back by this amount, bounded by 0).

    Yields:
        Slices of `messages`, each of length at most `chunk`.
    """
    for start_idx in range(0, len(messages), chunk):
        start_idx = max(start_idx - overlap_last, 0)
        next_idx = start_idx + chunk
        yield messages[start_idx:next_idx]


def convert_test_data(msg: dict) -> Dict[str, str]:
    """Convert a single raw message dict into a normalized message for profiling.

    User messages get a role like "name（用户）"; agent messages get a role
    like "short_name（full_name）" using MAP_AGENT_TO_FULLNAME.

    Args:
        msg: Raw message with keys "role", "content", "iso_time", and either
            "name" (user) or "agent_id"/"agent_name" (agent).

    Returns:
        Dict with keys "role", "content", "iso_time".

    Raises:
        ValueError: If the message is from an agent not in MAP_AGENT_TO_FULLNAME.
    """
    if msg["role"] == "user":
        return dict(role=msg["name"] + "（用户）", content=msg["content"], iso_time=msg["iso_time"])
    agent = None
    if msg["agent_id"] in MAP_AGENT_TO_FULLNAME:
        agent = msg["agent_id"]
    elif msg["agent_name"] in MAP_AGENT_TO_FULLNAME:
        agent = msg["agent_name"]
    else:
        raise ValueError(str(msg))
    content = msg["content"].removeprefix(MAP_AGENT_TO_FULLNAME[agent]).lstrip()
    return dict(role=f"{agent}（{MAP_AGENT_TO_FULLNAME[agent]}）", content=content, iso_time=msg["iso_time"])


def load_test_data(file: str) -> List[Dict[str, str]]:
    """Load and normalize a conversation from a JSON test data file.

    Expects the file to be JSON with a "conversation" key containing a list
    of message dicts. Each message is converted via convert_test_data.

    Args:
        file: Path to a UTF-8 JSON file.

    Returns:
        List of normalized message dicts with keys "role", "content", "iso_time".
    """
    with open(file, encoding="utf-8") as f:
        conv = json.load(f)["conversation"]
    return [convert_test_data(msg) for msg in conv]


def list_data_files() -> List[str]:
    """List paths to all conversation JSON files in this package's mock_data directory.

    Looks for files matching "conversation_*.json" under the mock_data folder
    next to this module. Paths are returned in sorted order.

    Returns:
        Sorted list of absolute paths to matching JSON files.
    """
    glob_pattern = str(Path(__file__).parent.absolute() / "mock_data" / "conversation_*.json")
    return sorted(glob.glob(glob_pattern))
