# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for the evolvable-workspace frontmatter primitives (design-v5 block A).

Covers ``body_sha256`` (the evolution baseline comparison value),
``read_frontmatter`` (hand-written bodies, malformed frontmatter raising
``ValueError``), ``write_frontmatter`` round-trip and ``atomic_write``.
"""

import pytest

from openjiuwen.agent_teams.team_workspace.frontmatter import (
    atomic_write,
    body_sha256,
    read_frontmatter,
    write_frontmatter,
)


class TestBodySha256:
    """The body hash is the single evolution comparison value."""

    def test_deterministic(self):
        assert body_sha256("hello") == body_sha256("hello")

    def test_differs_by_content(self):
        assert body_sha256("hello") != body_sha256("hello world")


class TestReadFrontmatter:
    """Frontmatter splitting and degradation rules."""

    def test_parses_meta_and_body(self):
        text = "---\nkind: prompt\nname: x\n---\nbody line"
        meta, body = read_frontmatter(text)
        assert meta == {"kind": "prompt", "name": "x"}
        assert body == "body line"

    def test_no_frontmatter_is_handwritten_body(self):
        text = "plain body"
        meta, body = read_frontmatter(text)
        assert meta == {}
        assert body == "plain body"

    def test_unterminated_delimiter_yields_full_text(self):
        text = "---\nkind: prompt\nbody line"
        meta, body = read_frontmatter(text)
        assert meta == {}
        assert body == text

    def test_malformed_yaml_raises(self):
        text = "---\nkind: [unclosed\n---\nbody"
        with pytest.raises(ValueError):
            read_frontmatter(text)

    def test_non_dict_meta_raises(self):
        text = "---\n- list\n- item\n---\nbody"
        with pytest.raises(ValueError):
            read_frontmatter(text)


class TestWriteFrontmatter:
    """Write → read round-trip keeps meta and body byte-faithfully."""

    def test_round_trip(self):
        meta = {"kind": "prompt", "name": "x", "baseline_sha256": "abc", "evolved": False}
        text = write_frontmatter(meta, "the body")
        parsed_meta, body = read_frontmatter(text)
        assert parsed_meta == meta
        assert body == "the body"

    def test_round_trip_preserves_trailing_newline(self):
        """A body-final ``\\n`` must survive the round-trip.

        ``body_sha256`` feeds the evolution judgement: if the write side hashed
        a body with a trailing newline and the read side returned it without,
        the hashes would never match and an untouched file would read as
        evolved. Byte-faithful body extraction keeps write → read hash-identical.
        """
        meta = {"kind": "prompt", "name": "x", "baseline_sha256": "abc", "evolved": False}
        body = "line1\nline2\n"
        text = write_frontmatter(meta, body)
        parsed_meta, round_tripped = read_frontmatter(text)
        assert parsed_meta == meta
        assert round_tripped == body
        assert body_sha256(round_tripped) == body_sha256(body)

    def test_round_trip_preserves_crlf(self):
        """Line endings inside the body are preserved byte-faithfully."""
        meta = {"kind": "prompt", "name": "x", "baseline_sha256": "abc", "evolved": False}
        body = "line1\r\nline2\r\n"
        text = write_frontmatter(meta, body)
        parsed_meta, round_tripped = read_frontmatter(text)
        assert parsed_meta == meta
        assert round_tripped == body


class TestAtomicWrite:
    """Atomic write lands the file at the target path."""

    def test_writes_file_and_creates_parents(self, tmp_path):
        target = tmp_path / "a" / "b" / "f.md"
        atomic_write(target, "content")
        assert target.read_text(encoding="utf-8") == "content"

    def test_replaces_existing(self, tmp_path):
        target = tmp_path / "f.md"
        atomic_write(target, "first")
        atomic_write(target, "second")
        assert target.read_text(encoding="utf-8") == "second"
