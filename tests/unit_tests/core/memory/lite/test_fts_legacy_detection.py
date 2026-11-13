# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Regression tests for _fts_table_is_legacy trigram detection.

_ensure_schema creates chunks_fts with tokenize='trigram' (quoted). The legacy
check used a bare substring test for "tokenize=trigram", which never matches
the quoted form — every process start re-detected its own table as legacy,
dropped it, and once meta ftsTrigram=true suppressed the migration reindex the
FTS index stayed permanently empty (keyword search returned 0 hits).
"""

import sqlite3
import unittest
from unittest.mock import MagicMock

from openjiuwen.core.memory.lite.manager import FTS_TABLE, MemoryIndexManager

# The schemas under test use contentless_delete=1 (SQLite >= 3.43) and the
# trigram tokenizer (SQLite >= 3.34). On older runtimes the CREATE itself
# fails, so skip the suite instead of erroring the CI.
MIN_SQLITE_FOR_CONTENTLESS_DELETE = (3, 43, 0)
REQUIRES_FTS5_TRIGRAM = unittest.skipUnless(
    sqlite3.sqlite_version_info >= MIN_SQLITE_FOR_CONTENTLESS_DELETE,
    "contentless_delete fts5 tables need SQLite >= 3.43, this runtime has "
    + sqlite3.sqlite_version,
)


def _manager_with_table(sql: str) -> MemoryIndexManager:
    """Build a minimal manager whose sqlite_master holds the given schema."""
    mgr = MagicMock(spec=MemoryIndexManager)
    # Bind the real method under test onto the mock.
    mgr._fts_table_is_legacy = MemoryIndexManager._fts_table_is_legacy.__get__(mgr)
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE other (x TEXT)")
    db.execute(sql)
    mgr.db = db
    return mgr


@REQUIRES_FTS5_TRIGRAM
class TestFtsTableIsLegacy(unittest.TestCase):
    def test_quoted_trigram_is_not_legacy(self):
        """The schema _ensure_schema itself writes must not be flagged legacy."""
        mgr = _manager_with_table(
            f"CREATE VIRTUAL TABLE {FTS_TABLE} USING fts5("
            "id UNINDEXED, path UNINDEXED, source UNINDEXED, text, "
            "content='', contentless_delete=1, tokenize='trigram')"
        )
        self.assertFalse(mgr._fts_table_is_legacy())

    def test_unquoted_trigram_is_not_legacy(self):
        """Equivalent unquoted form (hand-written or migrated) is not legacy."""
        mgr = _manager_with_table(
            f"CREATE VIRTUAL TABLE {FTS_TABLE} USING fts5("
            "id UNINDEXED, path UNINDEXED, source UNINDEXED, text, "
            "content='', contentless_delete=1, tokenize=trigram)"
        )
        self.assertFalse(mgr._fts_table_is_legacy())

    def test_legacy_unicode61_is_legacy(self):
        """Old unicode61 default (no tokenize clause) must be flagged legacy."""
        mgr = _manager_with_table(
            f"CREATE VIRTUAL TABLE {FTS_TABLE} USING fts5("
            "id UNINDEXED, path UNINDEXED, source UNINDEXED, text, "
            "content='', contentless_delete=1, tokenize='unicode61')"
        )
        self.assertTrue(mgr._fts_table_is_legacy())

    def test_no_tokenize_clause_is_legacy(self):
        """Table without any tokenize clause (implicit unicode61) is legacy."""
        mgr = _manager_with_table(
            f"CREATE VIRTUAL TABLE {FTS_TABLE} USING fts5("
            "id UNINDEXED, path UNINDEXED, source UNINDEXED, text)"
        )
        self.assertTrue(mgr._fts_table_is_legacy())

    def test_missing_table_is_not_legacy(self):
        """No table at all is not legacy — CREATE IF NOT EXISTS handles it."""
        mgr = _manager_with_table("CREATE TABLE filler (x TEXT)")
        self.assertFalse(mgr._fts_table_is_legacy())

    def test_no_db_is_not_legacy(self):
        """Uninitialized connection is not legacy."""
        mgr = MagicMock(spec=MemoryIndexManager)
        mgr._fts_table_is_legacy = MemoryIndexManager._fts_table_is_legacy.__get__(mgr)
        mgr.db = None
        self.assertFalse(mgr._fts_table_is_legacy())

    def test_survives_process_restarts(self):
        """The shipped scenario: schema written by _ensure_schema, then the
        legacy check runs again on later process starts — must stay False so the
        FTS table (and its postings) is never dropped behind the schema's back."""
        mgr = _manager_with_table(
            f"CREATE VIRTUAL TABLE {FTS_TABLE} USING fts5("
            "id UNINDEXED, path UNINDEXED, source UNINDEXED, text, "
            "content='', contentless_delete=1, tokenize='trigram')"
        )
        for _ in range(3):
            self.assertFalse(mgr._fts_table_is_legacy())


if __name__ == "__main__":
    unittest.main()
