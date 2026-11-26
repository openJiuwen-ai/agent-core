# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import asyncio
import json
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest
import pytest_asyncio

from openjiuwen.core.runner import Runner  # noqa: F401 - bootstrap the harness, as for other native tool tests
from openjiuwen.core.sys_operation.local._rw_lock_manager import ReadWriteLockManager
from openjiuwen.harness.security.permission_engine.fileguard.file_tool_specs import lookup_file_tool_specs
from openjiuwen.harness.tools import MarkerProvenance, create_aigc_marker_tools


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    path = tmp_path / "work"
    path.mkdir()
    return path


def _make_tools(workspace: Path, producer: str = "test-producer"):
    return create_aigc_marker_tools(
        workspace, provenance=MarkerProvenance(producer, "test-propagator", "test"), agent_id="test-agent"
    )


@pytest_asyncio.fixture(autouse=True)
async def _stop_file_locks():
    yield
    await ReadWriteLockManager.stop()


@pytest.mark.level0
def test_aigc_tool_cards_and_file_permissions(workspace: Path):
    mark, verify = _make_tools(workspace)
    assert mark.card.name == "aigc_mark_file"
    assert mark.card.id == "aigc_mark_file_test-agent"
    assert mark.card.parallel_safe is False
    assert verify.card.name == "aigc_verify_file"
    assert verify.card.idempotent is True
    assert lookup_file_tool_specs(mark.card.name)[0].action == "write"
    assert lookup_file_tool_specs(verify.card.name)[0].action == "read"


@pytest.mark.level0
@pytest.mark.asyncio
async def test_mark_and_verify_markdown_preserves_existing_provenance(workspace: Path):
    pytest.importorskip("lxml")
    path = workspace / "基准 样例.md"
    path.write_text("# Example\n", encoding="utf-8")
    arguments = {"file_path": str(path)}
    mark, verify = _make_tools(workspace)
    assert not (await verify.invoke(arguments)).success

    result = await mark.invoke({**arguments, "skip_visible": True})
    assert result.success, result.error
    assert "内容由AI生成" not in path.read_text(encoding="utf-8")
    before = path.read_bytes()
    checked = await verify.invoke(arguments)
    assert checked.success, checked.error
    assert checked.data["metadata"]["ContentProducer"] == "test-producer"
    assert checked.data["metadata"]["ContentPropagator"] == "test-propagator"
    assert path.read_bytes() == before

    second_mark, _ = _make_tools(workspace, "other-producer")
    result = await second_mark.invoke(arguments)
    assert result.success, result.error
    assert path.read_text(encoding="utf-8").count("内容由AI生成") == 1
    assert (await verify.invoke(arguments)).data["metadata"] == checked.data["metadata"]
    result = await mark.invoke(arguments)
    assert result.success, result.error
    assert result.data["changed"] is False


@pytest.mark.level1
@pytest.mark.asyncio
async def test_concurrent_marks_keep_the_visible_mark(workspace: Path):
    pytest.importorskip("lxml")
    path = workspace / "parallel.md"
    path.write_text("Body", encoding="utf-8")
    mark, _ = _make_tools(workspace)
    second_mark, _ = _make_tools(workspace)
    results = await asyncio.gather(
        mark.invoke({"file_path": str(path)}),
        second_mark.invoke({"file_path": str(path), "skip_visible": True}),
    )
    assert all(result.success for result in results), results
    assert path.read_text(encoding="utf-8").count("内容由AI生成") == 1


@pytest.mark.level1
@pytest.mark.asyncio
async def test_rejected_paths_and_failed_conversion_leave_files_intact(workspace: Path):
    outside = workspace.parent / "outside.md"
    outside.write_text("Outside", encoding="utf-8")
    relative = workspace / "relative.md"
    relative.write_text("Relative", encoding="utf-8")
    bad = workspace / "bad.posm"
    bad.write_bytes(b"not a ProcessOn file")
    mark, verify = _make_tools(workspace)
    for path in (outside, Path(relative.name), bad):
        assert not (await mark.invoke({"file_path": str(path)})).success
        assert not (await verify.invoke({"file_path": str(path)})).success
    assert outside.read_text(encoding="utf-8") == "Outside"
    assert relative.read_text(encoding="utf-8") == "Relative"
    assert bad.read_bytes() == b"not a ProcessOn file"
    assert not list(workspace.glob(".aigc-marker-*"))


@pytest.mark.level1
@pytest.mark.asyncio
async def test_links_outside_workspace_are_rejected(workspace: Path):
    outside = workspace.parent / "outside.md"
    outside.write_text("Outside", encoding="utf-8")
    link = workspace / "link.md"
    try:
        link.symlink_to(outside)
    except (NotImplementedError, OSError):
        pytest.skip("File symlinks are unavailable")
    mark, verify = _make_tools(workspace)
    assert not (await mark.invoke({"file_path": str(link)})).success
    assert not (await verify.invoke({"file_path": str(link)})).success
    assert outside.read_text(encoding="utf-8") == "Outside"


@pytest.mark.level1
@pytest.mark.asyncio
async def test_mark_pdf_and_png(workspace: Path):
    pytest.importorskip("lxml")
    pypdf = pytest.importorskip("pypdf")
    image = pytest.importorskip("PIL.Image")
    pdf_path = workspace / "sample.pdf"
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.add_metadata({"/Title": "原始标题", "/Author": "Sample author", "/Custom": "Original value"})
    writer.write(pdf_path)
    png_path = workspace / "sample.png"
    image.new("RGB", (100, 100)).save(png_path)
    mark, verify = _make_tools(workspace)
    for path in (pdf_path, png_path):
        result = await mark.invoke({"file_path": str(path), "skip_visible": True})
        assert result.success, result.error
    assert (await verify.invoke({"file_path": str(pdf_path)})).success
    pdf_metadata = pypdf.PdfReader(pdf_path).metadata
    assert pdf_metadata.title == "原始标题"
    assert pdf_metadata.author == "Sample author"
    assert pdf_metadata["/Custom"] == "Original value"
    with image.open(png_path) as marked:
        assert json.loads(marked.info["AIGC"])["ContentProducer"] == "test-producer"


@pytest.mark.level1
@pytest.mark.asyncio
async def test_mark_docx_and_processon_mind_map(workspace: Path):
    pytest.importorskip("lxml")
    docx = pytest.importorskip("docx")
    word_path = workspace / "sample.docx"
    document = docx.Document()
    document.add_paragraph("Original paragraph")
    document.save(word_path)
    diagram_path = workspace / "sample.posm"
    with zipfile.ZipFile(diagram_path, "w") as archive:
        archive.writestr("content.json", json.dumps({"root": True, "title": "Mind map", "children": []}))
    mark, verify = _make_tools(workspace)
    for path in (word_path, diagram_path):
        result = await mark.invoke({"file_path": str(path)})
        assert result.success, result.error
        result = await verify.invoke({"file_path": str(path)})
        assert result.success, result.error
    assert [part.text for part in docx.Document(word_path).paragraphs] == ["Original paragraph", "内容由AI生成"]
    with zipfile.ZipFile(diagram_path) as archive:
        canvas = json.loads(archive.read("content.json"))
        assert canvas["showWatermark"] is True
        assert any(child["title"] == "内容由AI生成" for child in canvas["children"])


@pytest.mark.level1
@pytest.mark.asyncio
async def test_mark_mp3_and_reject_disguised_playlist(workspace: Path):
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if ffmpeg is None or ffprobe is None:
        pytest.skip("FFmpeg executables are unavailable")
    path = workspace / "sample.mp3"
    subprocess.run(
        [ffmpeg, "-v", "error", "-f", "lavfi", "-i", "sine=f=440:r=44100", "-t", "0.2", str(path)],
        check=True,
    )
    mark, _ = _make_tools(workspace)
    result = await mark.invoke({"file_path": str(path), "skip_visible": True})
    assert result.success, result.error
    probe = subprocess.run(
        [ffprobe, "-v", "error", "-show_format", "-of", "json", str(path)], capture_output=True, check=True
    )
    tags = {key.casefold(): value for key, value in json.loads(probe.stdout)["format"]["tags"].items()}
    assert json.loads(tags["aigc"])["ContentProducer"] == "test-producer"
    repeated = await mark.invoke({"file_path": str(path), "skip_visible": True})
    assert repeated.success and repeated.data["changed"] is False
    playlist = workspace / "playlist.mp3"
    original = b"#EXTM3U\n#EXTINF:1,\nhttps://example.invalid/sample.mp3\n"
    playlist.write_bytes(original)
    assert not (await mark.invoke({"file_path": str(playlist), "skip_visible": True})).success
    assert playlist.read_bytes() == original
