# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from openjiuwen.core.sys_operation.local.output_encoding import (
    IncrementalCommandDecoder,
    choose_output_encoding,
    decode_command_output,
)


def test_utf8_nanjing_stays_nanjing():
    raw = "南京".encode("utf-8")
    assert raw.decode("gbk") == "鍗椾含"
    assert decode_command_output(raw) == "南京"
    assert choose_output_encoding(raw) == "utf-8"


def test_gbk_nanjing_roundtrips():
    raw = "南京".encode("gbk")
    assert decode_command_output(raw) == "南京"
    assert choose_output_encoding(raw) != "utf-8"


def test_truncated_utf8_does_not_fallback_whole_buffer_to_gbk():
    payload = "南京今天多云，东风转东北风，3-4级。".encode("utf-8")
    truncated = payload[:7]  # 「南京」6 bytes + 1 byte of 「今」
    text = decode_command_output(truncated)
    assert text.startswith("南京")
    assert "鍗椾含" not in text


def test_long_truncated_utf8_page_keeps_nanjing():
    payload = ("南京今天多云。" * 200).encode("utf-8")
    truncated = payload[:3000]
    text = decode_command_output(truncated)
    assert "南京" in text
    assert "鍗椾含" not in text


def test_empty_bytes():
    assert decode_command_output(b"") == ""
    assert choose_output_encoding(b"") == "utf-8"


def test_explicit_encoding_locks_gbk_misread():
    raw = "南京".encode("utf-8")
    assert decode_command_output(raw, encoding="gbk") == "鍗椾含"


def test_incremental_utf8_matches_oneshot():
    raw = "南京天气".encode("utf-8")
    dec = IncrementalCommandDecoder()
    assert dec.feed(raw, final=True) == "南京天气"


def test_incremental_gbk_after_prefix():
    raw = ("南京" * 80).encode("gbk")
    dec = IncrementalCommandDecoder()
    first = dec.feed(raw[:300], final=False)
    rest = dec.feed(raw[300:], final=True)
    assert (first + rest) == "南京" * 80
