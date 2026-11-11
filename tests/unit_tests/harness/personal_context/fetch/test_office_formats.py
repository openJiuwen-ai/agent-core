from __future__ import annotations

import asyncio
import hashlib
import io
import shutil
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from xml.etree import ElementTree

import pytest

from openjiuwen.harness.personal_context.config import PersonalContextFetchServiceConfig
from openjiuwen.harness.personal_context.fetch import local_files, local_markdown


FIXTURES = Path(__file__).with_name("fixtures")
WORD_TYPES = {
    ".docm": "application/vnd.ms-word.document.macroEnabled.main+xml",
    ".dotx": "application/vnd.openxmlformats-officedocument.wordprocessingml.template.main+xml",
    ".dotm": "application/vnd.ms-word.template.macroEnabledTemplate.main+xml",
}
EXCEL_TYPES = {
    ".xlsm": "application/vnd.ms-excel.sheet.macroEnabled.main+xml",
    ".xltx": "application/vnd.openxmlformats-officedocument.spreadsheetml.template.main+xml",
    ".xltm": "application/vnd.ms-excel.template.macroEnabled.main+xml",
    ".xlam": "application/vnd.ms-excel.addin.macroEnabled.main+xml",
}
SLIDE_TYPES = {
    ".pptm": "application/vnd.ms-powerpoint.presentation.macroEnabled.main+xml",
    ".potx": "application/vnd.openxmlformats-officedocument.presentationml.template.main+xml",
    ".potm": "application/vnd.ms-powerpoint.template.macroEnabled.main+xml",
    ".ppsx": "application/vnd.openxmlformats-officedocument.presentationml.slideshow.main+xml",
    ".ppsm": "application/vnd.ms-powerpoint.slideshow.macroEnabled.main+xml",
    ".ppam": "application/vnd.ms-powerpoint.addin.macroEnabled.main+xml",
    ".sldx": "application/vnd.openxmlformats-officedocument.presentationml.slide+xml",
    ".sldm": "application/vnd.ms-powerpoint.slide.macroEnabled.main+xml",
}


async def _fetch(root: Path):
    service = local_files.LocalFilesFetchService(PersonalContextFetchServiceConfig(
        service_id="office", provider="local_files", enabled=True, interval_seconds=60,
        time_range={"mode": "all"}, source={"root_dir": str(root)}, credentials={},
    ), home=root.parent / "home")
    candidates = await service.prepare_run(run_id="office", run_started_at=datetime.now(UTC), cursor=None)
    return [batch async for batch in service.fetch(run_id="office", cursor=None, candidates=candidates)]


def _item(path: Path):
    batches = asyncio.run(_fetch(path.parent))
    assert not [failure for batch in batches for failure in batch.failures]
    items = [item for batch in batches for item in batch.items]
    assert len(items) == 1
    assert items[0].revision_id == hashlib.sha256(path.read_bytes()).hexdigest()
    assert items[0].metadata["content_format"] == "markdown"
    return items[0]


def _variant(path: Path, data: bytes, part: str, content_type: str):
    """Create actual package variants by changing the declared main part type."""
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as target:
        for member in source.infolist():
            raw = source.read(member)
            if member.filename == "[Content_Types].xml":
                root = ElementTree.fromstring(raw)
                for node in root:
                    if node.get("PartName") == part:
                        node.set("ContentType", content_type)
                raw = ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)
            if path.suffix in {".sldx", ".sldm"} and member.filename == "_rels/.rels":
                root = ElementTree.fromstring(raw)
                for node in root:
                    if node.get("Type", "").endswith("/officeDocument"):
                        node.set("Target", "ppt/slides/slide1.xml")
                raw = ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)
            if path.suffix in {".sldx", ".sldm"} and member.filename in {"ppt/presentation.xml", "ppt/_rels/presentation.xml.rels"}:
                continue
            target.writestr(member.filename, raw)
    path.write_bytes(output.getvalue())


@pytest.mark.parametrize("extension", sorted(WORD_TYPES))
def test_word_macro_and_template_packages_are_collected(tmp_path, extension):
    from docx import Document

    document = Document()
    document.add_heading("中文研究计划", 1)
    document.add_paragraph("Evidence OFFICE_WORD_739251")
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "预算"
    table.cell(0, 1).text = "12345"
    data = io.BytesIO()
    document.save(data)
    path = tmp_path / ("研究" + extension)
    _variant(path, data.getvalue(), "/word/document.xml", WORD_TYPES[extension])

    content = _item(path).content

    assert all(text in content for text in ("中文研究计划", "OFFICE_WORD_739251", "预算", "12345"))


@pytest.mark.parametrize("extension", sorted(EXCEL_TYPES))
def test_excel_macro_and_template_packages_keep_all_sheet_content(tmp_path, extension):
    from openpyxl import Workbook

    book = Workbook()
    book.active.title = "预算"
    book.active.append(["OFFICE_EXCEL_739251", 12345])
    book.active["B4"] = "=B1*2"
    book.create_sheet("进度").append(["第二工作表", "完成"])
    data = io.BytesIO()
    book.save(data)
    book.close()
    path = tmp_path / ("预算" + extension)
    _variant(path, data.getvalue(), "/xl/workbook.xml", EXCEL_TYPES[extension])

    content = _item(path).content

    assert all(text in content for text in ("预算", "OFFICE_EXCEL_739251", "12345", "进度", "第二工作表", "完成"))
    assert "| 4 |  | =B1*2 |" in content


@pytest.mark.parametrize("extension", sorted(SLIDE_TYPES))
def test_presentation_macro_templates_shows_addins_and_single_slides(tmp_path, extension):
    from pptx import Presentation

    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[1])
    slide.shapes.title.text = "中文演示 OFFICE_SLIDE_739251"
    slide.placeholders[1].text = "第一张幻灯片的正文"
    slide.notes_slide.notes_text_frame.text = "演讲备注"
    data = io.BytesIO()
    presentation.save(data)
    path = tmp_path / ("演示" + extension)
    part = "/ppt/slides/slide1.xml" if extension in {".sldx", ".sldm"} else "/ppt/presentation.xml"
    _variant(path, data.getvalue(), part, SLIDE_TYPES[extension])

    content = _item(path).content

    assert "OFFICE_SLIDE_739251" in content
    assert "第一张幻灯片的正文" in content
    assert "演讲备注" in content


@pytest.mark.parametrize("extension,fixture,expected", [
    *((ext, "local-procurement.xls", "采购") for ext in (".xlt", ".xla", ".xlw")),
    (".xlsb", "local-binary-workbook.xlsb", "This workbook contains 4 sheets"),
])
def test_real_binary_office_serializations_are_parsed(tmp_path, extension, fixture, expected):
    # XLS/XLT/XLA share their binary serialization.
    path = tmp_path / ("binary" + extension)
    shutil.copyfile(FIXTURES / fixture, path)
    before = path.read_bytes()

    item = _item(path)

    assert expected in item.content
    assert path.read_bytes() == before
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("extension,kind", [
    (".odt", "text"), (".ott", "text"), (".ods", "spreadsheet"), (".ots", "spreadsheet"),
    (".odp", "presentation"), (".otp", "presentation"),
])
def test_opendocument_and_templates_extract_readable_content(tmp_path, extension, kind):
    path = tmp_path / ("开放文档" + extension)
    inner = {
        "text": '<text:p>中文文档 OFFICE_ODF_739251</text:p>',
        "spreadsheet": '<table:table table:name="预算"><table:table-row><table:table-cell office:value-type="string"><text:p>中文文档 OFFICE_ODF_739251</text:p></table:table-cell></table:table-row></table:table>',
        "presentation": '<draw:page draw:name="第一页"><draw:frame><draw:text-box><text:p>中文文档 OFFICE_ODF_739251</text:p></draw:text-box></draw:frame></draw:page>',
    }[kind]
    xml = ('<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
           'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
           'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
           'xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0">'
           f'<office:body><office:{kind}>{inner}</office:{kind}></office:body></office:document-content>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("mimetype", f"application/vnd.oasis.opendocument.{kind}")
        archive.writestr("META-INF/manifest.xml", '<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0"><manifest:file-entry manifest:full-path="/" manifest:media-type="application/vnd.oasis.opendocument.' + kind + '"/><manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/></manifest:manifest>')
        archive.writestr("content.xml", xml)

    assert "OFFICE_ODF_739251" in _item(path).content


@pytest.mark.parametrize("extension", [".doc", ".dot", ".ppt", ".pot", ".pps", ".ppa", ".rtf"])
def test_deferred_formats_are_skipped_without_loading_a_parser(tmp_path, extension):
    (tmp_path / ("deferred" + extension)).write_bytes(b"not supported")
    (tmp_path / "kept.csv").write_text("OFFICE_GOOD_739251\n", encoding="utf-8")

    batches = asyncio.run(_fetch(tmp_path))

    assert not [failure for batch in batches for failure in batch.failures]
    assert [item.content for batch in batches for item in batch.items] == [
        local_markdown.render_document(b"OFFICE_GOOD_739251\n", ".csv", "", 2000000)["content"],
    ]


def test_bad_office_document_does_not_prevent_later_document(tmp_path):
    (tmp_path / "a.docm").write_bytes(b"not a Word document")
    (tmp_path / "b.csv").write_bytes(b"OFFICE_GOOD_739251\n")

    batches = asyncio.run(_fetch(tmp_path))

    assert len([failure for batch in batches for failure in batch.failures]) == 1
    assert any("OFFICE_GOOD_739251" in item.content for batch in batches for item in batch.items)


def test_archive_limit_and_output_truncation(tmp_path):
    path = tmp_path / "body.csv"
    path.write_bytes(b"OFFICE_GOOD_739251 long content\n")
    result = local_markdown.convert_document(path, ".csv", max_chars=10, cancelled=lambda: False)
    assert len(result["content"]) <= 10
    assert result["content_truncated"] is True

    data = io.BytesIO()
    with zipfile.ZipFile(data, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("padding.bin", b"x" * (local_markdown.MAX_ARCHIVE_BYTES + 1))
    with pytest.raises(ValueError, match="archive exceeds"):
        local_markdown.render_document(data.getvalue(), ".docm", "", 1000)


def test_office_formats_have_document_size_limits():
    assert len(local_files._SUPPORTED_EXTENSIONS) == 37
    for extension in local_markdown.OFFICE_EXTENSIONS:
        assert local_files._max_size(extension) == 20 * 1024 * 1024


def test_repeated_opendocument_rows_and_columns_are_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(local_markdown, "MAX_ROWS", 2)
    monkeypatch.setattr(local_markdown, "MAX_COLUMNS", 2)
    xml = ('<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
           'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0">'
           '<office:body><office:spreadsheet><table:table table:name="repeated">'
           '<table:table-row table:number-rows-repeated="1000000000">'
           '<table:table-cell table:number-columns-repeated="1000000000" '
           'office:value-type="float" office:value="739251"/>'
           '</table:table-row></table:table></office:spreadsheet></office:body></office:document-content>')
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("content.xml", xml)

    result = local_markdown.render_document(data.getvalue(), ".ods", "", 2000)

    assert "| 1 | 739251 | 739251 |" in result["content"]
    assert "| 2 | 739251 | 739251 |" in result["content"]
    assert "| 3 |" not in result["content"]
    assert result["content_truncated"] is True


@pytest.mark.parametrize("extension,kind", [(".ods", "spreadsheet"), (".odp", "presentation")])
def test_opendocument_inline_spaces_line_breaks_and_tabs_are_preserved(tmp_path, extension, kind):
    body = '<text:p>SKU<text:s text:c="2"/>123<text:line-break/>next<text:tab/>column</text:p>'
    if kind == "spreadsheet":
        body = ('<table:table table:name="SKU"><table:table-row><table:table-cell office:value-type="string">'
                + body + '</table:table-cell></table:table-row></table:table>')
    else:
        body = '<draw:page><draw:frame><draw:text-box>' + body + '</draw:text-box></draw:frame></draw:page>'
    content = ('<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
               'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
               'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
               'xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0">'
               f'<office:body><office:{kind}>{body}</office:{kind}></office:body></office:document-content>')
    path = tmp_path / ("inline" + extension)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("content.xml", content)

    text = _item(path).content

    assert "SKU  123" in text
    assert "next\tcolumn" in text
    assert ("123<br>next" if kind == "spreadsheet" else "123\nnext") in text


@pytest.mark.parametrize("kind", ["paragraphs", "cells", "slide"])
def test_opendocument_expansion_stops_after_shared_character_budget(monkeypatch, kind):
    paragraph = '<text:p>x<text:s text:c="1000000000"/></text:p>'
    if kind == "slide":
        extension = ".odp"
        body = '<draw:page>' + paragraph * 100 + '</draw:page>'
    else:
        extension = ".ods"
        cells = ('<table:table-cell>' + paragraph * 100 + '</table:table-cell>' if kind == "paragraphs" else
                 ('<table:table-cell>' + paragraph + '</table:table-cell>') * 100)
        body = '<table:table><table:table-row>' + cells + '</table:table-row></table:table>'
    xml = ('<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
           'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
           'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
           'xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0">'
           '<office:body>' + body + '</office:body></office:document-content>')
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("content.xml", xml)
    original = local_markdown._odf_text
    parsed = []

    def record(node, budget):
        parsed.append(budget)
        return original(node, budget)

    monkeypatch.setattr(local_markdown, "_odf_text", record)

    result = local_markdown.render_document(data.getvalue(), extension, "", 1000)

    assert len(parsed) <= 2
    assert len(result["content"]) <= 1000
    assert result["content_truncated"] is True
