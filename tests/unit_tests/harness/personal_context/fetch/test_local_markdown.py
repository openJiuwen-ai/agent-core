from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import io
import shutil
import subprocess
import threading
import time
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from openjiuwen.harness.personal_context.config import PersonalContextConfig, PersonalContextFetchServiceConfig
from openjiuwen.harness.personal_context.context_pipeline import ContextPipelineService
from openjiuwen.harness.personal_context.fetch import local_files, local_markdown
from openjiuwen.harness.personal_context.fetch.cursor_selection import record_completed_candidates


def _service(root: Path, home: Path) -> local_files.LocalFilesFetchService:
    return local_files.LocalFilesFetchService(PersonalContextFetchServiceConfig(
        service_id="local", provider="local_files", enabled=True, interval_seconds=60,
        time_range={"mode": "all"}, source={"root_dir": str(root)}, credentials={},
    ), home=home)


async def _fetch(service, cursor=None):
    candidates = await service.prepare_run(run_id="formats", run_started_at=datetime.now(UTC), cursor=cursor)
    batches = [batch async for batch in service.fetch(run_id="formats", cursor=cursor, candidates=candidates)]
    return candidates, batches


def _item(path: Path, tmp_path: Path):
    _, batches = asyncio.run(_fetch(_service(path.parent, tmp_path / "home")))
    assert not batches[0].failures
    return batches[0].items[0]


@pytest.mark.parametrize("extension,encoding", [
    (".csv", "utf-8-sig"), (".csv", "utf-16"), (".csv", "gb18030"), (".tsv", "utf-8"),
])
def test_local_tables_keep_records_multiline_cells_and_original_files(tmp_path, extension, encoding):
    root = tmp_path / "source"
    root.mkdir()
    separator = "\t" if extension == ".tsv" else ","
    raw = f'名称{separator}数量\n"服务器|甲\n附注\r尾注"{separator}2\n'.encode(encoding)
    path = root / ("采购" + extension)
    path.write_bytes(raw)

    item = _item(path, tmp_path)

    assert "| Row | A | B |" in item.content
    assert "| 1 | 名称 | 数量 |" in item.content
    assert "服务器\\|甲<br>附注<br>尾注" in item.content
    assert "| 2 |" in item.content
    assert item.revision_id == hashlib.sha256(raw).hexdigest()
    assert item.metadata["content_format"] == "markdown"
    assert path.read_bytes() == raw
    assert list(root.iterdir()) == [path]
    assert not (tmp_path / "home").exists()


def test_html_removes_presentation_but_keeps_code_table_links_and_title(tmp_path):
    path = tmp_path / "article.HTML"
    raw = ("<html><head><meta charset='utf-8'><style>CSS_NOISE" + ".x{color:red}" * 200 +
           "</style></head><body><nav>MENU_NOISE</nav><main><h1>知识标题</h1>"
           "<p>关键数字为12345。</p><pre><code>&lt;style&gt;.x{color:blue}&lt;/style&gt;</code></pre>"
           "<table><tr><th>名称</th><th>数量</th></tr><tr><td>设备</td><td>2</td></tr></table>"
           "<a href='source.pdf'>依据</a><script>SCRIPT_NOISE</script>"
           "<img alt='架构图' src='data:image/png;base64,AAAA'></main></body></html>").encode()
    path.write_bytes(raw)

    item = _item(path, tmp_path)

    assert "# 知识标题" in item.content
    assert "关键数字为12345" in item.content
    assert "<style>.x{color:blue}</style>" in item.content
    assert "```" in item.content
    assert "| 设备 | 2 |" in item.content
    assert (tmp_path / "source.pdf").as_uri() in item.content
    assert "架构图" in item.content
    assert all(value not in item.content for value in ("CSS_NOISE", "MENU_NOISE", "SCRIPT_NOISE", "base64"))
    assert len(item.content) < len(raw.decode()) / 3
    assert item.metadata["conversion_degraded"] is False


def test_html_optional_markdown_failure_uses_extracted_text(monkeypatch):
    import markdownify

    def broken(*_args, **_kwargs):
        raise ValueError("formatting failed")

    monkeypatch.setattr(markdownify, "markdownify", broken)
    rendered = local_markdown.render_document(b"<style>noise</style><main>usable body</main>", ".html", "", 1000)
    assert rendered["content"] == "usable body"
    assert rendered["conversion_metadata"]["conversion_degraded"] is True


def _write_xlsx(path):
    from openpyxl import Workbook

    book = Workbook()
    sheet = book.active
    sheet.title = "预算"
    sheet.append(["项目", "金额"])
    sheet.append(["采购", 12345])
    sheet["B5"] = "=B2*2"
    book.create_sheet("日期").append([datetime(2026, 11, 9), True])
    book.save(path)
    book.close()


def test_xlsx_keeps_sheet_order_cell_positions_formulas_and_dates(tmp_path):
    path = tmp_path / "预算.xlsx"
    _write_xlsx(path)
    item = _item(path, tmp_path)
    assert item.content.index("## Sheet 预算") < item.content.index("## Sheet 日期")
    assert "| 2 | 采购 | 12345 |" in item.content
    assert "| 5 |  | =B2*2 |" in item.content
    assert "2026-11-09T00:00:00" in item.content
    assert not item.metadata["content_truncated"]


def test_legacy_xls_is_parsed_as_xls_and_preserves_sheet_and_values(tmp_path):
    # Generated with xlwt 1.3.0 from artificial procurement rows, no user data.
    path = tmp_path / "旧预算.xls"
    shutil.copyfile(Path(__file__).with_name("fixtures") / "local-procurement.xls", path)
    item = _item(path, tmp_path)
    assert "## Sheet 预算" in item.content
    assert "采购" in item.content
    assert "12345" in item.content
    assert not item.metadata["content_truncated"]


def _write_pptx(path):
    from pptx import Presentation
    from pptx.util import Inches

    presentation = Presentation()
    first = presentation.slides.add_slide(presentation.slide_layouts[1])
    first.shapes.title.text = "方案概览"
    first.placeholders[1].text = "保留关键结论12345"
    first.notes_slide.notes_text_frame.text = "备注依据"
    second = presentation.slides.add_slide(presentation.slide_layouts[6])
    table = second.shapes.add_table(2, 2, Inches(1), Inches(1), Inches(4), Inches(2)).table
    table.cell(0, 0).text = "项目"
    table.cell(0, 1).text = "金额"
    table.cell(1, 0).text = "采购"
    table.cell(1, 1).text = "12345"
    presentation.save(path)


def test_pptx_preserves_slide_order_tables_and_speaker_notes(tmp_path):
    path = tmp_path / "方案.pptx"
    _write_pptx(path)
    item = _item(path, tmp_path)
    assert item.content.index("## Slide 1") < item.content.index("## Slide 2")
    assert "方案概览" in item.content
    assert "保留关键结论12345" in item.content
    assert "备注依据" in item.content
    assert "| 2 | 采购 | 12345 |" in item.content


@pytest.mark.parametrize("extension", [".xlsx", ".xls", ".pptx", ".csv"])
def test_bad_document_does_not_block_later_text_file(tmp_path, extension):
    root = tmp_path / "source"
    root.mkdir()
    (root / ("bad" + extension)).write_bytes(b"\xff\xff\xff" if extension == ".csv" else b"broken")
    (root / "tail.txt").write_text("normal text", encoding="utf-8")
    _, batches = asyncio.run(_fetch(_service(root, tmp_path / "home")))
    assert [item.content for batch in batches for item in batch.items] == ["normal text"]
    assert len(batches[0].failures) == 1


def test_new_formats_have_separate_binary_limits_and_do_not_scan_office_lock_files(tmp_path):
    (tmp_path / "~$book.xlsx").write_bytes(b"broken")
    (tmp_path / "book.XLSX").write_bytes(b"broken")
    (tmp_path / "legacy.ppt").write_bytes(b"broken")
    (tmp_path / "~$macro.docm").write_bytes(b"broken")
    (tmp_path / "unsupported.zip").write_bytes(b"broken")
    assert [entry["relative_path"] for entry in local_files._scan_files(tmp_path)] == ["book.XLSX"]
    for extension in (".xlsx", ".xls", ".pptx"):
        assert local_files._max_size(extension) == 20 * 1024 * 1024
    assert local_files._max_size(".html") == 1024 * 1024


@pytest.mark.parametrize("member", ["xl/worksheets/one.xml", "xl/_rels/workbook.xml.rels", "ppt/media/image.bin"])
def test_conversion_limit_and_archive_limit_are_explicit(monkeypatch, member):
    monkeypatch.setattr(local_markdown, "MAX_ROWS", 2)
    rendered = local_markdown.render_document(b"a,b\n1,2\n3,4\n", ".csv", "", 1000)
    assert rendered["content_truncated"] is True
    assert "| 2 | 1 | 2 |" in rendered["content"]
    assert "| 3 |" not in rendered["content"]
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr(member, b"x" * 101)
    monkeypatch.setattr(local_markdown, "MAX_ARCHIVE_BYTES", 100)
    with pytest.raises(ValueError, match="archive"):
        local_markdown.render_document(archive.getvalue(), ".xlsx", "", 1000)


def test_csv_long_field_is_kept_and_output_truncation_is_reported(tmp_path):
    value = "large-field-" + "x" * 150_000
    raw = (value + ",12345\n").encode()
    path = tmp_path / "large.csv"
    path.write_bytes(raw)

    item = _item(path, tmp_path)

    assert value in item.content
    assert "12345" in item.content
    assert item.metadata["content_truncated"] is False
    rendered = local_markdown.render_document(raw, ".csv", "", 100)
    assert "large-field-" in rendered["content"]
    assert len(rendered["content"]) <= 100
    assert rendered["content_truncated"] is True


def test_markdown_cleanup_preserves_blank_lines_and_whitespace_inside_code():
    source = "# Title\r\n\r\n\r\n```css\n.a {  \n\n\n  color: red;\n}\n```\n\n\ntext\x00"
    cleaned = local_markdown._clean_markdown(source)
    assert "# Title\n\n```" in cleaned
    assert ".a {  \n\n\n  color: red;" in cleaned
    assert cleaned.endswith("```\n\ntext")


def _slow_worker(tmp_path, monkeypatch):
    marker = tmp_path / "worker-ready"
    script = tmp_path / "slow.py"
    script.write_text("import pathlib, time\npathlib.Path(" + repr(str(marker)) + ").touch()\ntime.sleep(30)\n", encoding="utf-8")
    original = subprocess.Popen
    processes = []

    def spawn(args, **kwargs):
        process = original([args[0], str(script)], **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(local_markdown.subprocess, "Popen", spawn)
    return marker, processes


def test_real_parser_process_is_terminated_on_timeout(tmp_path, monkeypatch):
    _, processes = _slow_worker(tmp_path, monkeypatch)
    monkeypatch.setattr(local_markdown, "CONVERSION_SECONDS", 0.2)
    started = time.monotonic()
    with pytest.raises(ValueError, match="timed out"):
        local_markdown.convert_document(tmp_path / "unused", ".csv", max_chars=100, cancelled=lambda: False)
    assert time.monotonic() - started < 1.5
    assert processes[0].poll() is not None


@pytest.mark.parametrize("stop_kind", ["timeout", "cancel"])
def test_parser_owns_the_actual_interpreter_and_terminates_it(tmp_path, monkeypatch, stop_kind):
    marker = tmp_path / "actual-parser-pid"
    script = tmp_path / "owned_parser.py"
    script.write_text(
        f"import os, pathlib, time\npathlib.Path({str(marker)!r}).write_text(str(os.getpid()))\n"
        "time.sleep(2)\n", encoding="utf-8",
    )
    actual = subprocess.Popen
    processes = []

    def spawn(args, **kwargs):
        process = actual([args[0], str(script)], **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(local_markdown.subprocess, "Popen", spawn)
    monkeypatch.setattr(local_markdown, "CONVERSION_SECONDS", 0.4)
    began = time.monotonic()
    with pytest.raises(ValueError, match="cancelled or timed out"):
        local_markdown.convert_document(
            tmp_path / "unused", ".csv", max_chars=100,
            cancelled=lambda: stop_kind == "cancel" and marker.exists(),
        )
    assert marker.exists()
    assert processes[0].pid == int(marker.read_text()), "the owned process must be the parser, not a redirector"
    assert processes[0].returncode is not None
    assert processes[0].stdout.closed
    assert time.monotonic() - began < 1


@pytest.mark.asyncio
async def test_cancellation_terminates_parser_without_blocking_event_loop(tmp_path, monkeypatch):
    marker, processes = _slow_worker(tmp_path, monkeypatch)
    monkeypatch.setattr(local_markdown, "CONVERSION_SECONDS", 10)
    path = tmp_path / "one.csv"
    path.write_text("name,value\na,1\n")
    candidate = local_files._scan_files(tmp_path)[0]
    task = asyncio.create_task(local_files._materialize_async(candidate))
    try:
        async with asyncio.timeout(5):
            while not marker.exists():
                await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=0.2)
        async with asyncio.timeout(1):
            while processes[0].poll() is None:
                await asyncio.sleep(0.01)
    finally:
        task.cancel()
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=0.5)


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_slow_parser_creation_releases_normal_executor_and_closes_late_process(tmp_path, monkeypatch, cancel):
    entered, release = threading.Event(), threading.Event()
    original = subprocess.Popen
    processes = []
    script = tmp_path / "slow-parser.py"
    script.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")

    def spawn(args, **kwargs):
        entered.set()
        if not release.wait(5):
            raise RuntimeError("test did not release parser creation")
        process = original([args[0], str(script)], **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(local_markdown.subprocess, "Popen", spawn)
    monkeypatch.setattr(local_markdown, "CONVERSION_SECONDS", 0.2)
    loop = asyncio.get_running_loop()
    loop.set_default_executor(concurrent.futures.ThreadPoolExecutor(max_workers=1))
    path = tmp_path / "one.csv"
    path.write_text("name,value\na,1\n", encoding="utf-8")
    candidate = local_files._scan_files(tmp_path)[0]
    task = asyncio.create_task(local_files._materialize_async(candidate))
    try:
        async with asyncio.timeout(1):
            while not entered.is_set():
                await asyncio.sleep(0.01)
        if cancel:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 0.2)
        else:
            with pytest.raises(local_files.BaseError, match="materialization failed"):
                await asyncio.wait_for(task, 0.7)
        assert await asyncio.wait_for(asyncio.to_thread(lambda: "normal tool result"), 0.4) == "normal tool result"
        release.set()
        async with asyncio.timeout(2):
            while not processes or processes[0].poll() is None or not processes[0].stdout.closed:
                await asyncio.sleep(0.01)
        assert processes[0].stdout.closed
    finally:
        release.set()
        task.cancel()
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=0.5)


@pytest.mark.parametrize("failure,exception", [("process", OSError), ("process", TimeoutError), ("thread", OSError)])
def test_failed_parser_creation_does_not_prevent_later_conversion(tmp_path, monkeypatch, failure, exception):
    path = tmp_path / "one.csv"
    path.write_text("name,value\na,739251\n", encoding="utf-8")

    def failed(*_args, **_kwargs):
        raise exception("creation failed")

    with monkeypatch.context() as patch:
        if failure == "process":
            patch.setattr(local_markdown.subprocess, "Popen", failed)
        else:
            patch.setattr(local_markdown.threading.Thread, "start", failed)
        for _ in range(33):
            with pytest.raises(exception, match="creation failed"):
                local_markdown.convert_document(path, ".csv", max_chars=1000, cancelled=lambda: False)
    result = local_markdown.convert_document(path, ".csv", max_chars=1000, cancelled=lambda: False)
    assert "739251" in result["content"]


@pytest.mark.asyncio
async def test_converted_markdown_survives_publication_and_temporary_inputs_are_removed(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    source = root / "采购.csv"
    raw = "采购项目,金额\n服务器,12345\n".encode()
    source.write_bytes(raw)
    home = tmp_path / "home"
    service = _service(root, home)
    candidates, batches = await _fetch(service)
    queue = asyncio.Queue(maxsize=8)
    pipeline = ContextPipelineService(home=home, config=PersonalContextConfig.from_dict({
        "collection_enabled": True, "agent_use_enabled": True, "strategy_profile": "rules",
        "fetch_services": [],
    }), input_queue=queue)
    await pipeline.start()
    try:
        for tag, payload in [("batch", batches[0]), ("finish", None)]:
            completion = asyncio.get_running_loop().create_future()
            await queue.put((tag, "local", "formats", payload, completion))
            await asyncio.wait_for(asyncio.shield(completion), timeout=10)
        published = [path for path in (home / "workspace" / "context").rglob("*.md") if path.name != "description.md"]
        assert published
        assert "12345" in "\n".join(path.read_text(encoding="utf-8") for path in published)
        assert len(list((home / "workspace" / "source-meta").glob("src_*.md"))) == 1
        assert not list((home / "workspace" / "sandboxes").rglob("*"))
        assert list(root.iterdir()) == [source]
        assert source.read_bytes() == raw
        cursor = record_completed_candidates(batches[-1].next_cursor, candidates)
        next_candidates, next_batches = await _fetch(service, cursor)
        assert next_candidates == ()
        assert next_batches[0].items == ()
    finally:
        await pipeline.stop(timeout_seconds=1)
