# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""write_file / edit_file / read_file Office document support."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from docx import Document

from openjiuwen.harness.tools import filesystem as fs_mod
from openjiuwen.harness.tools.filesystem import EditFileTool, ReadFileTool, WriteFileTool


@pytest.fixture(autouse=True)
def _clear_read_registry() -> None:
    fs_mod._FILE_READ_REGISTRY.clear()
    yield
    fs_mod._FILE_READ_REGISTRY.clear()


def _make_docx(path: Path) -> Path:
    doc = Document()
    doc.add_paragraph("Write_file")
    doc.add_paragraph("Read_file")
    doc.save(str(path))
    return path


def _word_com_usable() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import pythoncom  # noqa: F401
        import winreg
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r"Word.Application\CLSID") as key:
            winreg.QueryValueEx(key, "")[0]
    except Exception:
        return False
    return True


def _is_ole_compound(path: Path) -> bool:
    return path.read_bytes()[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


@pytest.mark.asyncio
async def test_write_file_rewrites_docx_after_successful_read(tmp_path: Path) -> None:
    docx_path = _make_docx(tmp_path / "123.docx")
    op = MagicMock()

    read_result = await ReadFileTool(op, language="en").invoke({"file_path": str(docx_path)})
    assert read_result.success is True
    assert "Write_file" in (read_result.data or {}).get("content", "")

    write_result = await WriteFileTool(op, language="en").invoke(
        {"file_path": str(docx_path), "content": "Write_file\nRead_file\n你好"}
    )
    assert write_result.success is True, write_result.error
    assert docx_path.read_bytes()[:2] == b"PK"

    verify = await ReadFileTool(op, language="en").invoke({"file_path": str(docx_path)})
    assert verify.success is True
    content = (verify.data or {}).get("content", "")
    assert "你好" in content
    assert "Write_file" in content
    assert "Read_file" in content


@pytest.mark.asyncio
async def test_edit_file_updates_docx_via_text_replace(tmp_path: Path) -> None:
    docx_path = _make_docx(tmp_path / "note.docx")
    op = MagicMock()

    await ReadFileTool(op, language="en").invoke({"file_path": str(docx_path)})
    edit_result = await EditFileTool(op, language="en").invoke(
        {
            "file_path": str(docx_path),
            "old_string": "Read_file",
            "new_string": "Read_file\n你好",
        }
    )
    assert edit_result.success is True, edit_result.error
    assert docx_path.read_bytes()[:2] == b"PK"

    verify = await ReadFileTool(op, language="en").invoke({"file_path": str(docx_path)})
    assert "你好" in (verify.data or {}).get("content", "")


@pytest.mark.asyncio
async def test_write_file_still_requires_read_for_plain_text(tmp_path: Path) -> None:
    txt_path = tmp_path / "note.txt"
    txt_path.write_text("hello\n", encoding="utf-8")

    write_result = await WriteFileTool(MagicMock(), language="en").invoke(
        {"file_path": str(txt_path), "content": "hello\nworld\n"}
    )
    assert write_result.success is False
    assert "has not been read yet" in (write_result.error or "").lower()


@pytest.mark.asyncio
async def test_write_file_creates_new_docx_without_prior_read(tmp_path: Path) -> None:
    docx_path = tmp_path / "new.docx"
    write_result = await WriteFileTool(MagicMock(), language="en").invoke(
        {"file_path": str(docx_path), "content": "hello\nworld"}
    )
    assert write_result.success is True, write_result.error
    assert docx_path.exists()
    assert docx_path.read_bytes()[:2] == b"PK"

    verify = await ReadFileTool(MagicMock(), language="en").invoke({"file_path": str(docx_path)})
    assert "hello" in (verify.data or {}).get("content", "")
    assert "world" in (verify.data or {}).get("content", "")


@pytest.mark.asyncio
async def test_write_file_rewrites_empty_docx_after_read(tmp_path: Path) -> None:
    docx_path = tmp_path / "empty.docx"
    doc = Document()
    doc.save(str(docx_path))
    op = MagicMock()

    read_result = await ReadFileTool(op, language="en").invoke({"file_path": str(docx_path)})
    assert read_result.success is True

    write_result = await WriteFileTool(op, language="en").invoke(
        {"file_path": str(docx_path), "content": "hello\nworld"}
    )
    assert write_result.success is True, write_result.error
    assert docx_path.exists()

    verify = await ReadFileTool(op, language="en").invoke({"file_path": str(docx_path)})
    assert "hello" in (verify.data or {}).get("content", "")
    assert "world" in (verify.data or {}).get("content", "")


@pytest.mark.asyncio
@pytest.mark.skipif(not _word_com_usable(), reason="requires Windows + pywin32 + Microsoft Word")
async def test_write_file_creates_and_reads_doc(tmp_path: Path) -> None:
    doc_path = tmp_path / "new.doc"
    op = MagicMock()

    write_result = await WriteFileTool(op, language="en").invoke(
        {"file_path": str(doc_path), "content": "周致名Zzm\n你好"}
    )
    assert write_result.success is True, write_result.error
    assert doc_path.exists()
    assert _is_ole_compound(doc_path)

    read_result = await ReadFileTool(op, language="en").invoke({"file_path": str(doc_path)})
    assert read_result.success is True, read_result.error
    content = (read_result.data or {}).get("content", "")
    assert "周致名Zzm" in content
    assert "你好" in content


@pytest.mark.asyncio
@pytest.mark.skipif(not _word_com_usable(), reason="requires Windows + pywin32 + Microsoft Word")
async def test_write_file_rewrites_doc_after_successful_read(tmp_path: Path) -> None:
    doc_path = tmp_path / "zzm.doc"
    op = MagicMock()

    create = await WriteFileTool(op, language="en").invoke(
        {"file_path": str(doc_path), "content": "周致名Zzm"}
    )
    assert create.success is True, create.error
    fs_mod._FILE_READ_REGISTRY.clear()

    read_result = await ReadFileTool(op, language="en").invoke({"file_path": str(doc_path)})
    assert read_result.success is True, read_result.error

    write_result = await WriteFileTool(op, language="en").invoke(
        {"file_path": str(doc_path), "content": "周致名Zzm\n你好"}
    )
    assert write_result.success is True, write_result.error
    assert _is_ole_compound(doc_path)

    verify = await ReadFileTool(op, language="en").invoke({"file_path": str(doc_path)})
    assert verify.success is True, verify.error
    content = (verify.data or {}).get("content", "")
    assert "周致名Zzm" in content
    assert "你好" in content
