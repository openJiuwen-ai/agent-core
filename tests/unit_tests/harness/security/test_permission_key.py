# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""make_key: how permission config keys are spelled."""

from __future__ import annotations

import pytest

from openjiuwen.harness.security.permission_key import DEFAULT_KEY, make_key


def test_no_dimension_is_default() -> None:
    assert make_key() == "default"
    assert DEFAULT_KEY == "default"


def test_enterprise_identity() -> None:
    assert make_key(group_id="g", bot_id="b", user_id="alice") == "group:g/bot:b/user:alice"


def test_order_is_fixed_regardless_of_argument_order() -> None:
    assert make_key(user_id="u", bot_id="b", group_id="g") == "group:g/bot:b/user:u"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"group_id": "g"},
        {"bot_id": "b"},
        {"user_id": "u"},
        {"group_id": "g", "bot_id": "b"},
        {"group_id": "g", "user_id": "u"},
        {"bot_id": "b", "user_id": "u"},
    ],
)
def test_partial_identity_is_rejected(kwargs: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        make_key(**kwargs)


@pytest.mark.parametrize("value", ["", "   ", "	"])
def test_blank_value_is_rejected(value: str) -> None:
    with pytest.raises(ValueError):
        make_key(group_id=value, bot_id="b", user_id="u")


@pytest.mark.parametrize("value", [1, b"s", ("s",)])
def test_non_string_value_is_rejected(value: object) -> None:
    with pytest.raises(TypeError):
        make_key(group_id=value, bot_id="b", user_id="u")  # type: ignore[arg-type]


def test_values_are_used_as_given() -> None:
    assert make_key(user_id="a/b", group_id="g", bot_id="b") == "group:g/bot:b/user:a/b"


def test_arguments_must_be_named() -> None:
    with pytest.raises(TypeError):
        make_key("g", "b", "u")  # type: ignore[misc]
