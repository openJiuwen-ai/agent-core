# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Spell the key under which a permission config is registered.

The engine treats a key as an opaque string.  Everything that publishes or
looks up a config builds its key here, so the same config always gets the
same key and a key in a log line shows whose config was used.
"""

from __future__ import annotations

DEFAULT_KEY = "default"


def _clean(name: str, value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if not value.strip():
        raise ValueError(f"{name} must not be blank")
    return value


def make_key(
    *,
    group_id: str | None = None,
    bot_id: str | None = None,
    user_id: str | None = None,
) -> str:
    """Return the key for the given dimensions.

    ``group_id``, ``bot_id`` and ``user_id`` identify one user and must be given
    all together or not at all.  They are joined in that order as ``name:value``
    separated by ``/``; with none given the result is ``default``.  Values are
    used as given.
    """

    group = _clean("group_id", group_id)
    bot = _clean("bot_id", bot_id)
    user = _clean("user_id", user_id)

    if len({group is None, bot is None, user is None}) != 1:
        raise ValueError("group_id, bot_id and user_id must be given together")

    parts = (("group", group), ("bot", bot), ("user", user))
    segments = [f"{name}:{value}" for name, value in parts if value is not None]
    return "/".join(segments) if segments else DEFAULT_KEY
