# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from openjiuwen.core.context_engine.context.context_utils import (
    MODEL_DEFAULT_CONTEXT_WINDOW_TOKENS,
    ContextUtils,
)


def _lookup(model_name: str) -> int | None:
    return ContextUtils._get_positive_int_by_model_name(
        MODEL_DEFAULT_CONTEXT_WINDOW_TOKENS, model_name
    )


def test_pangu_entries_resolve_to_512k():
    for name in (
        "openpangu-2.0",
        "openpangu-2.0-pro",
        "openpangu-2.0-flash",
        "openpangu-2.1-pro",
    ):
        assert _lookup(name) == 512000


def test_pangu_lookup_is_case_insensitive():
    assert _lookup("OpenPangu-2.0") == 512000


def test_suffix_sku_lookup_matches_encoded_cap():
    assert _lookup("qwen2.5-72b-32k") == 32000
    assert _lookup("qwen2.5-72b-8k") == 8000
    assert _lookup("qwen2.5-72b-4k") == 4000
    assert _lookup("qwen2.5-72b-1k") == 1000
    assert _lookup("qwen2.5-vl-72b-48k") == 48000


def test_bare_names_without_native_window_are_not_listed():
    # The catalog "4K" label is the SKU serving cap, not the model window; a
    # bare entry would shadow OpenRouter metadata with a too-small budget.
    for name in (
        "glm-4-9b",
        "qwen2.5-72b",
        "qwen2.5-72b-awq",
        "qwen2.5-32b",
        "qwen2.5-32b-awq",
        "qwen2.5-14b",
        "qwen2.5-7b",
        "qwen2.5-1.5b",
        "qwen2.5-0.5b",
        "qwen2.5-vl-7b",
    ):
        assert name not in MODEL_DEFAULT_CONTEXT_WINDOW_TOKENS
        assert _lookup(name) is None


def test_unknown_name_misses_lookup():
    assert _lookup("no-such-model-name") is None


def test_every_entry_is_str_key_positive_int_value():
    for key, value in MODEL_DEFAULT_CONTEXT_WINDOW_TOKENS.items():
        assert isinstance(key, str)
        assert isinstance(value, int)
        assert value > 0


def test_qwen3_bare_names_resolve_to_yarn_extended_window():
    # 官方原生窗口 32768，YaRN 扩展服务窗口 131072。表取扩展值：
    # 生产默认 enable_openrouter_model_context_window_tokens=false 时
    # 表是唯一防线，删裸键会落 200K 兜底造成供应商真截断。
    for name in ("qwen3-32b", "qwen3-30b-a3b", "qwen3-235b-a22b"):
        assert _lookup(name) == 131072


def test_builtin_table_shadows_openrouter_metadata():
    # 内置表(优先级3) 遮蔽 OpenRouter 元数据(优先级4)，无运行时纠正：
    # 元数据报原生 32768 时，裸键 131072 仍生效。
    from openjiuwen.core.context_engine.context import context_utils as cu

    cu._OPENROUTER_MODEL_CONTEXT_WINDOW_TOKENS["qwen3-32b"] = 32768
    try:
        assert ContextUtils.resolve_context_max("qwen3-32b") == 131072
    finally:
        cu._OPENROUTER_MODEL_CONTEXT_WINDOW_TOKENS.pop("qwen3-32b", None)


def test_explicit_config_overrides_builtin_table():
    # 显式配置(优先级1) 与部署 dict(优先级2) 均高于内置表。
    assert ContextUtils.resolve_context_max("qwen3-32b", fallback_context_window_tokens=64000) == 64000
    assert (
        ContextUtils.resolve_context_max(
            "qwen3-32b", model_context_window_tokens={"qwen3-32b": 96000}
        )
        == 96000
    )
