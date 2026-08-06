# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import errno
import os
import tempfile

import pytest

from openjiuwen.symphony.shared.fingerprint import _io


def test_atomic_write_json_replace_failure_keeps_target_and_removes_temporary_file(tmp_path, monkeypatch) -> None:
    target = tmp_path / "fingerprint.json"
    target.write_text('{"snapshot": "last-success"}\n', encoding="utf-8")

    def fail_replace(_source, _target) -> None:
        raise OSError("injected replace failure")

    monkeypatch.setattr(_io.os, "replace", fail_replace)

    with pytest.raises(OSError, match="injected replace failure"):
        _io.atomic_write_json(target, {"snapshot": "next"})

    assert target.read_text(encoding="utf-8") == '{"snapshot": "last-success"}\n'
    assert list(tmp_path.glob(".fingerprint.json.*.tmp")) == []


def test_atomic_write_json_closes_descriptor_when_text_stream_creation_fails(tmp_path, monkeypatch) -> None:
    real_mkstemp = tempfile.mkstemp
    opened_descriptor: int | None = None

    def record_mkstemp(*args, **kwargs):
        nonlocal opened_descriptor
        opened_descriptor, temporary_name = real_mkstemp(*args, **kwargs)
        return opened_descriptor, temporary_name

    def fail_fdopen(*_args, **_kwargs):
        raise OSError("injected stream creation failure")

    monkeypatch.setattr(_io.tempfile, "mkstemp", record_mkstemp)
    monkeypatch.setattr(_io.os, "fdopen", fail_fdopen)

    with pytest.raises(OSError, match="injected stream creation failure"):
        _io.atomic_write_json(tmp_path / "fingerprint.json", {"snapshot": "next"})

    assert opened_descriptor is not None
    with pytest.raises(OSError) as exc_info:
        os.fstat(opened_descriptor)
    assert exc_info.value.errno == errno.EBADF
    assert list(tmp_path.glob(".fingerprint.json.*.tmp")) == []
