# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""generate_id: prefixed base36 task-id generation."""
from __future__ import annotations

from openjiuwen.agent_teams.id_generator import generate_id


def test_generate_id_known_kinds_use_mapped_prefix():
    """Known kinds map to their one-character prefix."""
    assert generate_id("swarmflow").startswith("w")
    assert generate_id("async_tool").startswith("x")
    assert generate_id("session_spawn").startswith("s")


def test_generate_id_unknown_kind_uses_default_prefix():
    """An unregistered kind falls back to the default 't' prefix."""
    assert generate_id("totally_unknown").startswith("t")
    assert generate_id("async_tasks_list").startswith("t")


def test_generate_id_length():
    """Default body is 8 chars (id length 9 with prefix); length is honored."""
    assert len(generate_id("swarmflow")) == 9
    assert len(generate_id("swarmflow", length=12)) == 13


def test_generate_id_charset_is_base36_body():
    """The body uses only base36 chars (digits + lowercase ASCII)."""
    allowed = set("0123456789abcdefghijklmnopqrstuvwxyz")
    body = generate_id("swarmflow")[1:]
    assert set(body) <= allowed


def test_generate_id_is_unique_in_bulk():
    """A large batch of ids has no collisions (random body)."""
    ids = {generate_id("swarmflow") for _ in range(5000)}
    assert len(ids) == 5000
