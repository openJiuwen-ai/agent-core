# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for deterministic skill-experience de-duplication."""

from __future__ import annotations

from openjiuwen.agent_evolving.checkpointing.types import EvolutionPatch, EvolutionRecord
from openjiuwen.agent_evolving.optimizer.skill_call.experience_dedup import (
    filter_duplicate_candidates,
    filter_duplicate_records,
    filter_uncovered_signals,
)
from openjiuwen.agent_evolving.signal.base import EvolutionTarget, make_evolution_signal, make_signal_fingerprint


def _record(
    *,
    summary: str,
    content: str,
    root_cause: str = "",
    context: str = "",
    record_id: str = "",
    merge_target: str | None = None,
) -> EvolutionRecord:
    record = EvolutionRecord.make(
        source="user_intent",
        context=context,
        change=EvolutionPatch(
            section="Examples",
            action="append",
            content=content,
            target=EvolutionTarget.BODY,
            merge_target=merge_target,
        ),
        summary=summary,
        root_cause=root_cause,
    )
    if record_id:
        record.id = record_id
    return record


def test_drops_paraphrased_lang_zh_experience():
    existing = [
        _record(
            summary="查中文天气时在 URL 加 &lang=zh 参数",
            content="curl wttr.in/Nanjing?lang=zh",
            root_cause="Skill 未说明如何获取中文输出",
        )
    ]
    generated = [
        _record(
            summary="查询中文天气时，在 wttr.in 的 URL 中加上 ?lang=zh 参数即可返回中文的天气描述。",
            content="## 中文输出\ncurl -s \"wttr.in/Nanjing?lang=zh&format=%l\"",
            root_cause="Skill 名为 weather-zh 但未说明如何获取中文输出的具体参数",
        )
    ]
    assert filter_duplicate_records(generated, existing) == []


def test_drops_same_uv_feedback_candidate():
    existing = [
        _record(
            summary="当用户需要紫外线指数时，wttr.in 不支持该字段，改用 Open-Meteo 加 daily=uv_index_max 参数获取。",
            content="## 紫外线指数\ncurl open-meteo daily=uv_index_max",
            root_cause="Skill 未说明如何获取紫外线指数",
            context="[user_intent] 这个不对，缺少紫外线指数",
        )
    ]
    candidates = [
        {
            "action": "append",
            "summary": "需要紫外线指数时改用 Open-Meteo 的 uv_index_max",
            "root_cause": "wttr.in compact format 不支持 UV 字段",
            "content": "使用 Open-Meteo daily=uv_index_max 获取紫外线指数",
        }
    ]
    assert filter_duplicate_candidates(candidates, existing) == []


def test_keeps_unrelated_experience():
    existing = [
        _record(
            summary="当 wttr.in 命令返回 Exit code 52 时稍等重试",
            content="## Troubleshooting\nExit code 52 empty reply",
            root_cause="wttr.in 偶发空响应",
        )
    ]
    generated = [
        _record(
            summary="当用户需要紫外线指数时改用 Open-Meteo",
            content="## UV\nopen-meteo uv_index_max",
            root_cause="Skill 未覆盖紫外线指数字段",
        )
    ]
    kept = filter_duplicate_records(generated, existing)
    assert len(kept) == 1


def test_fake_merge_target_still_dedups():
    existing = [
        _record(
            summary="查询中文天气时加上 lang=zh",
            content="wttr.in?lang=zh",
            record_id="ev_existing",
        )
    ]
    generated = [
        _record(
            summary="查询中文天气时加上 lang=zh",
            content="wttr.in?lang=zh",
            merge_target="ev_missing",
        )
    ]
    assert filter_duplicate_records(generated, existing) == []


def test_real_merge_target_is_kept():
    existing = [
        _record(
            summary="查询中文天气时加上 lang=zh",
            content="wttr.in?lang=zh",
            record_id="ev_existing",
        )
    ]
    generated = [
        _record(
            summary="查询中文天气时加上 lang=zh",
            content="wttr.in?lang=zh 并补充示例",
            merge_target="ev_existing",
        )
    ]
    kept = filter_duplicate_records(generated, existing)
    assert len(kept) == 1


def test_skips_signal_already_saved_from_same_user_message():
    existing = [
        _record(
            summary="当用户需要紫外线指数时改用 Open-Meteo",
            content="open-meteo uv_index_max",
            context="[user_intent] 这个不对，缺少紫外线指数",
        )
    ]
    signals = [
        make_evolution_signal(
            signal_type="user_intent",
            section="Instructions",
            excerpt="用户指出天气结果缺少紫外线指数",
            skill_name="weather-zh",
            context={"user_message": "这个不对，缺少紫外线指数"},
        )
    ]
    assert filter_uncovered_signals(signals, existing) == []


def test_user_intent_fingerprint_ignores_llm_excerpt_paraphrase():
    first = make_evolution_signal(
        signal_type="user_intent",
        section="Instructions",
        excerpt="用户指出天气结果缺少紫外线指数",
        skill_name="weather-zh",
        context={"user_message": "这个不对，缺少紫外线指数"},
    )
    second = make_evolution_signal(
        signal_type="user_intent",
        section="Instructions",
        excerpt="输出缺少 UV 指数，应补充说明",
        skill_name="weather-zh",
        context={"user_message": "这个不对，缺少紫外线指数"},
    )
    assert make_signal_fingerprint(first) == make_signal_fingerprint(second)
