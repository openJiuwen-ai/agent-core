"""
Data extraction function for the calculator scenario.
"""

from typing import Any, Dict


def task_data_fn(task_sample: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a dataset row to agent inputs.

    Dataset columns: ``question``, ``result``, ``chain``, ...
    Returns ``query`` (for agent) and ``ground_truth`` (for reward).
    """
    return {
        "query": task_sample.get("question", ""),
        "ground_truth": task_sample.get("result", ""),
    }
