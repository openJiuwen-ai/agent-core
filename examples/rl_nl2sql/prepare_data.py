# -*- coding: UTF-8 -*-
"""
Convert Spider JSON dataset to Parquet format for RL training.

Usage:
    python prepare_data.py --spider_dir /path/to/spider_data --output_dir /path/to/output

Produces ``train.parquet`` and ``dev.parquet`` with columns:
    question, gold_sql, db_id, db_source, schema_text
"""

import argparse
import json
import os
from typing import Any, Dict, List

import pandas as pd

COLUMN_TYPE_MAP = {
    "text": "TEXT",
    "number": "INTEGER",
    "real": "REAL",
    "time": "TEXT",
    "boolean": "INTEGER",
    "others": "TEXT",
}


def _build_schema_text(db_meta: Dict[str, Any]) -> str:
    """Build a DDL-style schema string from a Spider ``tables.json`` entry."""
    table_names = db_meta["table_names_original"]
    col_names = db_meta["column_names_original"]
    col_types = db_meta["column_types"]
    primary_keys = set(db_meta.get("primary_keys", []))
    foreign_keys = db_meta.get("foreign_keys", [])

    columns_by_table: Dict[int, List[str]] = {i: [] for i in range(len(table_names))}
    for col_idx, (table_idx, col_name) in enumerate(col_names):
        if table_idx < 0:
            continue
        sql_type = COLUMN_TYPE_MAP.get(col_types[col_idx], "TEXT")
        pk_tag = " PRIMARY KEY" if col_idx in primary_keys else ""
        columns_by_table[table_idx].append(f"  {col_name} {sql_type}{pk_tag}")

    fk_by_table: Dict[int, List[str]] = {i: [] for i in range(len(table_names))}
    for child_col_idx, parent_col_idx in foreign_keys:
        child_table_idx, child_col = col_names[child_col_idx]
        parent_table_idx, parent_col = col_names[parent_col_idx]
        parent_table = table_names[parent_table_idx]
        fk_by_table[child_table_idx].append(
            f"  FOREIGN KEY ({child_col}) REFERENCES {parent_table}({parent_col})"
        )

    parts: List[str] = []
    for table_idx, table_name in enumerate(table_names):
        lines = columns_by_table[table_idx] + fk_by_table[table_idx]
        ddl = f"CREATE TABLE {table_name} (\n" + ",\n".join(lines) + "\n);"
        parts.append(ddl)

    return "\n\n".join(parts)


def _load_tables(tables_json_path: str) -> Dict[str, Dict[str, Any]]:
    """Load tables.json and return a dict keyed by db_id."""
    with open(tables_json_path, "r", encoding="utf-8") as f:
        tables_list = json.load(f)
    return {entry["db_id"]: entry for entry in tables_list}


def _load_examples(json_path: str) -> List[Dict[str, Any]]:
    with open(json_path, "r", encoding="utf-8") as f:
        return json.load(f)


def build_records(
    examples: List[Dict[str, Any]],
    tables_map: Dict[str, Dict[str, Any]],
    db_source: str,
) -> List[Dict[str, str]]:
    """Convert Spider examples to flat records for Parquet."""
    records = []
    schema_cache: Dict[str, str] = {}

    for ex in examples:
        db_id = ex["db_id"]
        if db_id not in schema_cache:
            if db_id in tables_map:
                schema_cache[db_id] = _build_schema_text(tables_map[db_id])
            else:
                schema_cache[db_id] = ""

        records.append(
            {
                "question": ex["question"],
                "gold_sql": ex["query"],
                "db_id": db_id,
                "db_source": db_source,
                "schema_text": schema_cache[db_id],
            }
        )
    return records


def main():
    parser = argparse.ArgumentParser(description="Convert Spider JSON to Parquet")
    parser.add_argument(
        "--spider_dir",
        type=str,
        required=True,
        help="Path to spider_data directory",
    )
    parser.add_argument(
        "--output_dir",
        type=str,
        required=True,
        help="Directory for output parquet files",
    )
    args = parser.parse_args()

    spider_dir = args.spider_dir
    output_dir = args.output_dir
    os.makedirs(output_dir, exist_ok=True)

    tables_map = _load_tables(os.path.join(spider_dir, "tables.json"))

    train_examples = _load_examples(os.path.join(spider_dir, "train_spider.json"))
    others_path = os.path.join(spider_dir, "train_others.json")
    if os.path.exists(others_path):
        train_examples += _load_examples(others_path)

    train_records = build_records(train_examples, tables_map, db_source="database")
    train_df = pd.DataFrame(train_records)
    train_path = os.path.join(output_dir, "train.parquet")
    train_df.to_parquet(train_path, index=False)

    dev_examples = _load_examples(os.path.join(spider_dir, "dev.json"))
    dev_records = build_records(dev_examples, tables_map, db_source="database")
    dev_df = pd.DataFrame(dev_records)
    dev_path = os.path.join(output_dir, "dev.parquet")
    dev_df.to_parquet(dev_path, index=False)

    test_json = os.path.join(spider_dir, "test.json")
    if os.path.exists(test_json):
        test_tables_path = os.path.join(spider_dir, "test_tables.json")
        test_tables_map = (
            _load_tables(test_tables_path) if os.path.exists(test_tables_path) else tables_map
        )
        test_examples = _load_examples(test_json)
        test_records = build_records(test_examples, test_tables_map, db_source="test_database")
        test_df = pd.DataFrame(test_records)
        test_path = os.path.join(output_dir, "test.parquet")
        test_df.to_parquet(test_path, index=False)


if __name__ == "__main__":
    main()
