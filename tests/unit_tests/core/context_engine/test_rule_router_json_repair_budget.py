# coding: utf-8
"""Guard: oversized non-JSON tool blobs must not enter json_repair (event-loop stall)."""

from __future__ import annotations

import time
from unittest.mock import patch

from openjiuwen.core.context_engine.processor.forked.offloader.rule_compression.compressors.json_array_compressor import (
    JsonArrayCompressor,
    _load_json_array,
)
from openjiuwen.core.context_engine.processor.forked.offloader.rule_compression.common import (
    JSON_REPAIR_MAX_CHARS,
)
from openjiuwen.core.context_engine.processor.forked.offloader.rule_compression.router import (
    RuleContentRouter,
)
from openjiuwen.core.context_engine.processor.forked.offloader.rule_compression.types import (
    ContentType,
    RuleContext,
)


def _huge_tool_blob(payload_chars: int = 300_000) -> str:
    # Mirrors fetch_webpage tool results that wrap CDN/API bodies in a Python-ish list.
    inner = "function(){" + ("x" * payload_chars) + "}"
    return "[{'url': 'https://unpkg.com/vue@3/dist/vue.global.js', 'content': '" + inner + "'}]"


def test_rule_router_skips_json_repair_on_huge_non_json_bracket_blob():
    blob = _huge_tool_blob()
    router = RuleContentRouter()

    with patch(
        "openjiuwen.core.context_engine.processor.forked.offloader.rule_compression.common.repair_json_loads"
    ) as repair:
        started = time.perf_counter()
        detected = router.detect(blob)
        elapsed = time.perf_counter() - started

    repair.assert_not_called()
    assert detected == ContentType.PLAIN_TEXT
    assert elapsed < 2.0, f"detect took {elapsed:.3f}s; expected cheap skip without json_repair"


def test_rule_router_still_repairs_small_invalid_json_array():
    # Trailing comma — invalid JSON, small enough to repair.
    content = '[{"id": 1},]'
    router = RuleContentRouter()

    with patch(
        "openjiuwen.core.context_engine.processor.forked.offloader.rule_compression.common.repair_json_loads",
        return_value=[{"id": 1}],
    ) as repair:
        detected = router.detect(content)

    repair.assert_called_once()
    assert detected == ContentType.JSON_ARRAY


def test_load_json_array_skips_json_repair_on_huge_blob():
    blob = _huge_tool_blob()
    assert len(blob) > JSON_REPAIR_MAX_CHARS

    with patch(
        "openjiuwen.core.context_engine.processor.forked.offloader.rule_compression.common.repair_json_loads"
    ) as repair:
        rows = _load_json_array(blob)

    repair.assert_not_called()
    assert rows is None


def test_json_array_compressor_does_not_repair_huge_blob():
    blob = _huge_tool_blob()
    ctx = RuleContext(
        max_tokens=100,
        head_tokens=50,
        tail_tokens=50,
        count_tokens=lambda text: max(len(text) // 3, 1),
    )
    with patch(
        "openjiuwen.core.context_engine.processor.forked.offloader.rule_compression.common.repair_json_loads"
    ) as repair:
        result = JsonArrayCompressor().compress(blob, ctx)

    repair.assert_not_called()
    assert result.modified is False
    assert result.content == blob
