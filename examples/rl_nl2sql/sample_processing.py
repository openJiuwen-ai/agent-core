# -*- coding: UTF-8 -*-
"""
Data extraction function for the NL2SQL scenario.
"""

import json
from typing import Any, Dict


def task_data_fn(task_sample: Dict[str, Any]) -> Dict[str, Any]:
    """Map one Parquet row to ``query`` and ``ground_truth``.

    Expected columns (see ``prepare_data.py``):
        ``question``, ``gold_sql``, ``db_id``, ``db_source``, ``schema_text``

    ``query`` concatenates database id, schema text, and the natural-language
    question. ``ground_truth`` is a JSON string with ``gold_sql`` and DB
    identifiers for the reward function.
    """
    db_source = task_sample.get("db_source", "database")
    db_id = task_sample.get("db_id", "")
    schema_text = task_sample.get("schema_text", "")
    question = task_sample.get("question", "")

    query = (
        f"Database: {db_source}/{db_id}\n\n"
        f"Schema:\n{schema_text}\n\n"
        f"Question: {question}"
    )

    ground_truth = json.dumps(
        {
            "gold_sql": task_sample.get("gold_sql", ""),
            "db_id": db_id,
            "db_source": db_source,
        },
        ensure_ascii=False,
    )

    return {"query": query, "ground_truth": ground_truth}
