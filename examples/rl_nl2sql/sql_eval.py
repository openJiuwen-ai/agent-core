# -*- coding: UTF-8 -*-
"""
SQL execution-based evaluation for NL2SQL.

Compares a predicted SQL query against a gold SQL query by executing both
on the same SQLite database and checking whether the result sets are
denotationally equivalent (bag semantics with column permutation).

Adapted from https://github.com/taoyds/test-suite-sql-eval -- heavily
refactored: no async, no threading, no global state.
"""

import os
import random
import sqlite3
from collections import defaultdict
from itertools import product
from typing import List, Optional, Set, Tuple

import sqlparse

QUERY_TIMEOUT_SECONDS = 30


# ---------------------------------------------------------------------------
# SQL pre-processing helpers
# ---------------------------------------------------------------------------

def postprocess_sql(query: str) -> str:
    """Fix common formatting artefacts that cause execution errors."""
    query = query.strip().rstrip(";").strip()
    query = query.replace("> =", ">=").replace("< =", "<=").replace("! =", "!=")
    return query


def remove_distinct(sql: str) -> str:
    """Strip all DISTINCT keywords from *sql*."""
    tokens = list(sqlparse.parse(sql)[0].flatten())
    return "".join(t.value for t in tokens if t.value.lower() != "distinct")


# ---------------------------------------------------------------------------
# Query execution
# ---------------------------------------------------------------------------

def execute_query(
    db_path: str, sql: str, timeout: int = QUERY_TIMEOUT_SECONDS
) -> Tuple[bool, Optional[List[tuple]]]:
    """Run *sql* on the SQLite database at *db_path*.

    Returns ``(True, rows)`` on success or ``(False, None)`` on error/timeout.
    """
    try:
        conn = sqlite3.connect(db_path)

        def decode_bytes(b):
            """Decode byte data to string with error ignoring"""
            return b.decode(errors="ignore")

        conn.text_factory = decode_bytes
        conn.execute(f"PRAGMA busy_timeout = {timeout * 1000}")
        cursor = conn.cursor()
        cursor.execute(sql)
        rows = cursor.fetchall()
        conn.close()
        return True, rows
    except Exception:
        return False, None


# ---------------------------------------------------------------------------
# Result-set comparison
# ---------------------------------------------------------------------------

def _unorder_row(row: tuple) -> tuple:
    return tuple(sorted(row, key=lambda x: str(x) + str(type(x))))


def _quick_reject(
    result1: List[tuple], result2: List[tuple], order_matters: bool
) -> bool:
    """Fast pre-check: do the two result sets have the same bag of
    unordered rows?  Returns *True* if they pass (i.e. cannot be quickly
    rejected), *False* if they are definitely different."""
    s1 = [_unorder_row(r) for r in result1]
    s2 = [_unorder_row(r) for r in result2]
    if order_matters:
        return s1 == s2
    return set(s1) == set(s2)


def _multiset_eq(list1: List, list2: List) -> bool:
    if len(list1) != len(list2):
        return False
    counts: dict = defaultdict(int)
    for e in list1:
        counts[e] += 1
    for e in list2:
        counts[e] -= 1
        if counts[e] < 0:
            return False
    return True


def _constraint_permutations(
    tab1_col_sets: List[Set], result2: List[tuple]
) -> product:
    """Reduce the column-permutation search space by sampling rows."""
    num_cols = len(result2[0])
    constraints = [set(range(num_cols)) for _ in range(num_cols)]
    if num_cols <= 3:
        return product(*constraints)

    for _ in range(20):
        sample_row = random.choice(result2)
        for c1 in range(num_cols):
            for c2 in set(constraints[c1]):
                if sample_row[c2] not in tab1_col_sets[c1]:
                    constraints[c1].discard(c2)
    return product(*constraints)


def results_equivalent(
    result1: List[tuple], result2: List[tuple], order_matters: bool = False
) -> bool:
    """Check denotational equivalence of two query result sets.

    Supports column permutation and bag semantics.
    """
    if not result1 and not result2:
        return True
    if len(result1) != len(result2):
        return False
    if len(result1[0]) != len(result2[0]):
        return False
    if not _quick_reject(result1, result2, order_matters):
        return False

    num_cols = len(result1[0])
    col_sets = [{row[i] for row in result1} for i in range(num_cols)]

    for perm in _constraint_permutations(col_sets, result2):
        if len(perm) != len(set(perm)):
            continue
        if num_cols == 1:
            permuted = result2
        else:
            permuted = [tuple(row[i] for i in perm) for row in result2]

        if order_matters:
            if result1 == permuted:
                return True
        else:
            if set(result1) == set(permuted) and _multiset_eq(result1, permuted):
                return True
    return False


# ---------------------------------------------------------------------------
# Top-level evaluation entry point
# ---------------------------------------------------------------------------

def eval_exec_match(
    db_path: str,
    predicted_sql: str,
    gold_sql: str,
    keep_distinct: bool = False,
) -> bool:
    """Return *True* if *predicted_sql* is denotationally equivalent to
    *gold_sql* when executed on every ``.sqlite`` file in the same
    directory as *db_path*.

    This is the standard Spider execution-accuracy metric.
    """
    predicted_sql = postprocess_sql(predicted_sql)
    gold_sql = postprocess_sql(gold_sql)

    if not keep_distinct:
        predicted_sql = remove_distinct(predicted_sql)
        gold_sql = remove_distinct(gold_sql)

    order_matters = "order by" in gold_sql.lower()

    db_dir = os.path.dirname(db_path)
    db_files = [
        os.path.join(db_dir, f)
        for f in os.listdir(db_dir)
        if f.endswith(".sqlite")
    ]
    if not db_files:
        db_files = [db_path]

    for db_file in db_files:
        g_ok, g_rows = execute_query(db_file, gold_sql)
        if not g_ok or g_rows is None:
            return False

        p_ok, p_rows = execute_query(db_file, predicted_sql)
        if not p_ok or p_rows is None:
            return False

        if not results_equivalent(g_rows, p_rows, order_matters=order_matters):
            return False

    return True
