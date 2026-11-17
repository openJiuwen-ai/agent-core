# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Capacity behavior for the shared TTSE experience bank."""

import json

import pytest

from openjiuwen.agent_evolving.ttse.config import TTSEConfig
from openjiuwen.agent_evolving.ttse.stores import TTSERecordStore, _new_record


@pytest.mark.asyncio
@pytest.mark.parametrize("rtype", ["fact", "tip"])
@pytest.mark.parametrize("direct", [False, True])
async def test_default_bank_retains_more_than_400_records(tmp_path, rtype, direct):
    path = tmp_path / "bank.json"
    records = [_new_record(f"existing rule {i:04d}", count=2) for i in range(400)]
    path.write_text(json.dumps({f"{rtype}s": records}), encoding="utf-8")
    config = TTSEConfig(store_path=str(path))
    store = TTSERecordStore(config)

    if direct:
        await store.add_record_direct(rtype, "new independent experience")
    else:
        assert await getattr(store, f"add_{rtype}")("new independent experience")

    reloaded = TTSERecordStore(config)
    saved = getattr(reloaded, f"{rtype}s")
    assert len(saved) == 401
    assert {record["text"] for record in saved} == {
        *(record["text"] for record in records),
        "new independent experience",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("rtype", ["fact", "tip"])
@pytest.mark.parametrize("direct", [False, True])
@pytest.mark.parametrize("cap", [0, 2])
async def test_explicit_capacity_still_applies(tmp_path, rtype, direct, cap):
    config = TTSEConfig(store_path=str(tmp_path / "bank.json"), **{f"max_{rtype}s": cap})
    store = TTSERecordStore(config)
    for text in ("alpha experience", "beta experience", "gamma experience"):
        if direct:
            await store.add_record_direct(rtype, text)
        else:
            await getattr(store, f"add_{rtype}")(text)

    saved = getattr(TTSERecordStore(config), f"{rtype}s")
    assert len(saved) == cap
    assert [record["text"] for record in saved] == ["alpha experience", "beta experience"][:cap]


def test_unlimited_bank_embedding_cache_scales_with_records():
    store = TTSERecordStore(TTSEConfig(store_path=""))
    store.facts = [_new_record(f"fact {i}") for i in range(401)]
    store.tips = [_new_record(f"tip {i}") for i in range(401)]
    for record in (*store.facts, *store.tips):
        store._store_embedding(record["text"], [1.0])
    for i in range(100):
        store._store_embedding(f"query {i}", [1.0])

    assert store._embedding_cache_limit() == 902
    assert len(store._emb_cache) == 902
    assert all(record["text"] in store._emb_cache for record in (*store.facts, *store.tips))
