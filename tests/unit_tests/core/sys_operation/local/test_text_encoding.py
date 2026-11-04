# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import codecs

import pytest

from openjiuwen.core.sys_operation.local._text_encoding import detect_text_encoding


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (b"", "utf-8"),
        (b"plain ascii\n", "utf-8"),
        ("中文内容\n".encode("utf-8"), "utf-8"),
        ("中文内容\n".encode("gbk"), "gb18030"),
        (codecs.BOM_UTF8 + "中文内容\n".encode("utf-8"), "utf-8-sig"),
        (codecs.BOM_UTF16_LE + "中文内容\n".encode("utf-16-le"), "utf-16"),
        (codecs.BOM_UTF32_LE + "中文内容\n".encode("utf-32-le"), "utf-32"),
    ],
)
def test_detect_text_encoding(tmp_path, payload, expected):
    file_path = tmp_path / "sample.txt"
    file_path.write_bytes(payload)

    assert detect_text_encoding(file_path) == expected


def test_detect_text_encoding_missing_file(tmp_path):
    assert detect_text_encoding(tmp_path / "missing.txt") == "utf-8"
