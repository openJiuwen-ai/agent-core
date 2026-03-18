# -*- coding: UTF-8 -*-
"""
SQL execution tool for the NL2SQL RL training scenario.

Provides rich error feedback so the agent can iteratively correct its SQL.
"""

import os
import re
import shutil
import sqlite3
import tempfile

from openjiuwen.core.common.logging import tool_logger
from openjiuwen.core.foundation.tool import tool

SPIDER_DATA_DIR = os.environ.get("SPIDER_DATA_DIR", "/home/data/spider_data")

_MAX_DISPLAY_ROWS = 50
_MAX_CELL_WIDTH = 80


def _resolve_db_path(database: str) -> str:
    """Resolve a ``db_source/db_id`` identifier to an absolute ``.sqlite`` path."""
    parts = database.strip().strip("/").split("/")
    if len(parts) == 2:
        db_source, db_id = parts
    elif len(parts) == 1:
        db_source, db_id = "database", parts[0]
    else:
        db_source = parts[0]
        db_id = parts[-1]
    return os.path.join(SPIDER_DATA_DIR, db_source, db_id, f"{db_id}.sqlite")


def _get_table_info(db_path: str) -> str:
    """Return a summary of all tables and their columns in the database."""
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        tables = [row[0] for row in cursor.fetchall()]
        if not tables:
            conn.close()
            return "No tables found in database."

        lines = []
        for table in tables:
            cursor.execute(f"PRAGMA table_info(\"{table}\")")
            cols = cursor.fetchall()
            col_strs = [f"{c[1]} ({c[2]})" for c in cols]
            lines.append(f"  {table}: {', '.join(col_strs)}")
        conn.close()
        return "Available tables and columns:\n" + "\n".join(lines)
    except Exception:
        return ""


def _format_results(columns: list, rows: list) -> str:
    """Format query results into a readable table string."""
    total = len(rows)
    display_rows = rows[:_MAX_DISPLAY_ROWS]

    def _truncate(val: str) -> str:
        s = str(val)
        return s[:_MAX_CELL_WIDTH] + "..." if len(s) > _MAX_CELL_WIDTH else s

    header = " | ".join(columns)
    separator = "-" * min(len(header), 120)
    result_lines = [f"Columns: {header}", separator]

    for i, row in enumerate(display_rows, 1):
        formatted = " | ".join(_truncate(v) for v in row)
        result_lines.append(f"Row {i}: {formatted}")

    if total > _MAX_DISPLAY_ROWS:
        result_lines.append(f"... ({total} rows total, showing first {_MAX_DISPLAY_ROWS})")
    else:
        result_lines.append(f"({total} row{'s' if total != 1 else ''} returned)")

    return "\n".join(result_lines)


def _enrich_error(error_msg: str, db_path: str) -> str:
    """Append contextual hints to an error message when possible."""
    lower = error_msg.lower()

    if "no such table" in lower:
        match = re.search(r"no such table:\s*(\S+)", error_msg, re.IGNORECASE)
        bad_table = match.group(1) if match else "unknown"
        hint = _get_table_info(db_path)
        return (
            f"SQL Error: no such table: {bad_table}\n\n"
            f"Hint: The table \"{bad_table}\" does not exist.\n{hint}"
        )

    if "no such column" in lower:
        match = re.search(r"no such column:\s*(\S+)", error_msg, re.IGNORECASE)
        bad_col = match.group(1) if match else "unknown"
        table_match = re.search(r"(\w+)\.(\w+)", bad_col)
        hint = _get_table_info(db_path)
        if table_match:
            return (
                f"SQL Error: no such column: {bad_col}\n\n"
                f"Hint: Column \"{table_match.group(2)}\" not found in table "
                f"\"{table_match.group(1)}\".\n{hint}"
            )
        return f"SQL Error: no such column: {bad_col}\n\nHint: Check column names.\n{hint}"

    if "ambiguous column name" in lower:
        hint = _get_table_info(db_path)
        return (
            f"SQL Error: {error_msg}\n\n"
            f"Hint: Use table_name.column_name to disambiguate.\n{hint}"
        )

    if "near" in lower and "syntax error" in lower:
        return f"SQL Syntax Error: {error_msg}\n\nHint: Check SQL syntax near the reported token."

    return f"SQL Error: {error_msg}"


@tool(
    name="execute_sql",
    description=(
        "Execute a SQL query on a SQLite database and return the results. "
        "The 'database' parameter should be in 'db_source/db_id' format "
        "(e.g. 'database/concert_singer'). Returns query results on success "
        "or a detailed error message with hints on failure."
    ),
)
def execute_sql(database: str, sql: str) -> str:
    """
    Execute *sql* on the SQLite database return a formatted result table or a detailed error with hints.
    """
    db_path = _resolve_db_path(database)

    if not os.path.exists(db_path):
        hint = (
            f"Database file not found: {db_path}\n"
            f"Make sure the database identifier is correct (e.g. 'database/concert_singer').\n"
            f"SPIDER_DATA_DIR is set to: {SPIDER_DATA_DIR}"
        )
        return f"Error: {hint}"

    with tempfile.TemporaryDirectory() as tmp_dir:
        db_dir = os.path.dirname(db_path)
        tmp_db = os.path.join(tmp_dir, os.path.basename(db_path))
        shutil.copyfile(db_path, tmp_db)

        conn = None
        try:
            conn = sqlite3.connect(tmp_db)

            def decode_bytes(b):
                """Decode byte data to string with error ignoring"""
                return b.decode(errors="ignore")

            conn.text_factory = decode_bytes
            cursor = conn.cursor()
            cursor.execute(sql.strip())
            rows = cursor.fetchall()
            columns = (
                [desc[0] for desc in cursor.description] if cursor.description else []
            )
            conn.close()

            if not rows:
                return "Query executed successfully. 0 rows returned."
            return _format_results(columns, rows)

        except Exception as e:
            if conn:
                try:
                    conn.close()
                except Exception as close_err:
                    tool_logger.exception("Failed to close database connection: %s", close_err)
            return _enrich_error(str(e), db_path)
