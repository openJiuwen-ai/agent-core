# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Native UOF 1 XML / UOF 2 ZIP AIGC markers.

Structures follow Beihang's UOF/Open XML Translator and LibreOffice UOF
filters. The label is body content, sheet data or a referenced slide graphic.
"""

import copy
import io
import json
import os
import posixpath
import tempfile
import uuid
import zipfile
from pathlib import Path

from lxml import etree as ET

from openjiuwen.core.common.logging import agent_logger
from openjiuwen.harness.tools.aigc_marker._common import (
    canonical_signature,
    is_aigc_complete,
    parse_aigc_json,
)

VISIBLE_TEXT = "内容由AI生成"
UOF1 = {
    "uof": "http://schemas.uof.org/cn/2003/uof",
    "word": "http://schemas.uof.org/cn/2003/uof-wordproc",
    "sheet": "http://schemas.uof.org/cn/2003/uof-spreadsheet",
    "presentation": "http://schemas.uof.org/cn/2003/uof-slideshow",
    "graph": "http://schemas.uof.org/cn/2003/graph",
}
UOF_COMPONENTS = {
    "uof": "uof",
    "word": "wordproc",
    "sheet": "spreadsheet",
    "presentation": "presentation",
    "graph": "graph",
    "graphics": "graphics",
    "meta": "metadata",
    "rules": "rules",
}
UOF2 = {key: f"http://schemas.uof.org/cn/2009/{value}" for key, value in UOF_COMPONENTS.items()}
# GCMark writes this compatibility namespace alongside native UOF 2 metadata.
# Verified against its downloaded output on 2026-10-21; do not relabel the body.
UOF_GCMARK_META = "http://www.nits.org.cn/uof3/zh-cn/2021/metadata"
UOF3 = {key: f"http://www.nits.org.cn/uof3/zh-cn/2021/{value}" for key, value in UOF_COMPONENTS.items()}
KINDS = {
    ".uot": "word",
    ".uott": "word",
    ".uos": "sheet",
    ".uost": "sheet",
    ".uop": "presentation",
    ".uopt": "presentation",
}
CONTENT_ROOTS = {"word": "文字处理文档_4225", "sheet": "电子表格文档_E826", "presentation": "演示文稿文档_6C10"}


def _tag(ns, name):
    return "{" + ns + "}" + name


def _child(parent, ns, name, text=None, attributes=None):
    node = ET.SubElement(parent, _tag(ns, name), attrib=attributes or {})
    node.text = text
    return node


def _parse(data):
    parser = ET.XMLParser(resolve_entities=False, no_network=True, remove_blank_text=False)
    tree = ET.parse(io.BytesIO(data), parser)
    if tree.docinfo.doctype:
        raise ValueError("UOF documents with a DTD are not supported")
    return tree


def _xml(tree):
    return ET.tostring(tree, encoding="UTF-8", xml_declaration=True)


def _uid():
    return "aigc_" + uuid.uuid4().hex


def _has_text(node):
    return any(item.text == VISIBLE_TEXT for item in node.iter())


def _insert_after(parent, node, tag):
    positions = [i for i, item in enumerate(parent) if item.tag == tag]
    parent.insert(positions[-1] + 1 if positions else len(parent), node)


def _flat_signature(raw):
    """Keep provenance fields but unwrap legacy EXIF-style AIGC envelopes."""
    parsed = parse_aigc_json(raw)
    if not is_aigc_complete(parsed):
        return None
    return canonical_signature(parsed, "")


def _member_names(archive):
    names = archive.namelist()
    if len(set(names)) != len(names):
        raise ValueError("UOF package contains duplicate members")
    lower_names = {name.lower(): name for name in names}
    if len(lower_names) != len(names):
        raise ValueError("UOF package contains ambiguous member names")
    return lower_names


def _signature(meta, ns, version):
    names = ("用户自定义元数据_5210", "自定义元数据_5210") if version == 2 else ("用户自定义元数据",)
    name_attr = "名称_5211" if version == 2 else _tag(ns, "名称")
    for name in names:
        for prop in meta.iter(_tag(ns, name)):
            if prop.get(name_attr) == "AIGC":
                parsed = parse_aigc_json(prop.text)
                if is_aigc_complete(parsed):
                    return _flat_signature(prop.text)
    return None


def _metadata(meta, ns, version, signature):
    group_name = "用户自定义元数据集_520F" if version == 2 else "用户自定义元数据集"
    prop_name = "用户自定义元数据_5210" if version == 2 else "用户自定义元数据"
    name_attr = "名称_5211" if version == 2 else _tag(ns, "名称")
    type_attr = "类型_5212" if version == 2 else _tag(ns, "类型")
    # Accept shortened names used by the TC260 guide; create real schema names.
    groups = [n for n in meta if n.tag in {_tag(ns, group_name), _tag(ns, "自定义元数据集_520F")}]
    group = groups[0] if groups else _child(meta, ns, group_name)
    props = []
    for current_group in groups:
        props.extend(node for node in current_group if node.get(name_attr) == "AIGC")
    prop = props[0] if props else _child(group, ns, prop_name)
    changed = (
        prop.text != signature or prop.get(name_attr) != "AIGC" or prop.get(type_attr) != "string" or len(props) > 1
    )
    for duplicate in props[1:]:
        duplicate.getparent().remove(duplicate)
    prop.set(name_attr, "AIGC")
    prop.set(type_attr, "string")
    prop.text = signature
    return changed


def _package_signature(meta):
    """Preserve provenance; refuse ambiguous native/compatibility identities."""
    values = []
    for ns in (UOF2["meta"], UOF_GCMARK_META):
        for name in ("用户自定义元数据_5210", "自定义元数据_5210"):
            for prop in meta.iter(_tag(ns, name)):
                if prop.get("名称_5211") == "AIGC":
                    parsed = parse_aigc_json(prop.text)
                    if is_aigc_complete(parsed):
                        values.append((prop.text, parsed))
    if values and any(
        json.loads(canonical_signature(data, "")) != json.loads(canonical_signature(values[0][1], ""))
        for _, data in values[1:]
    ):
        raise ValueError("Conflicting UOF AIGC provenance; resolve metadata before marking")
    return _flat_signature(values[0][0]) if values else None


def _gcmark_metadata(meta, signature):
    """Mirror the native signature in GCMark's observed metadata structure."""
    ns = UOF_GCMARK_META
    groups = list(meta.findall(_tag(ns, "自定义元数据集_520F")))
    group = groups[0] if groups else _child(meta, ns, "自定义元数据集_520F")
    props = []
    property_tag = _tag(ns, "自定义元数据_5210")
    for current_group in groups:
        props.extend(node for node in current_group.findall(property_tag) if node.get("名称_5211") == "AIGC")
    prop = props[0] if props else _child(group, ns, "自定义元数据_5210")
    changed = (
        prop.text != signature or prop.get("名称_5211") != "AIGC" or prop.get("类型_5212") != "lpwstr" or len(props) > 1
    )
    for duplicate in props[1:]:
        duplicate.getparent().remove(duplicate)
    prop.set("名称_5211", "AIGC")
    prop.set("类型_5212", "lpwstr")
    prop.text = signature
    return changed


def _paragraph(ns, version):
    attr = "标识符_4169" if version == 2 else _tag(ns, "标识符")
    paragraph = ET.Element(_tag(ns, "段落_416B" if version == 2 else "段落"), attrib={attr: _uid()})
    sentence = _child(paragraph, ns, "句_419D" if version == 2 else "句")
    if version == 2:
        props = _child(sentence, ns, "句属性_4158")
        _child(props, ns, "字体_4128", attributes={"字号_412D": "10.5", "颜色_412F": "#000000"})
    _child(sentence, ns, "文本串_415B" if version == 2 else "文本串", VISIBLE_TEXT)
    return paragraph


def _word_mark(root, version):
    ns = ET.QName(root).namespace if version == 2 else UOF1["word"]
    if version == 2:
        sections = root.findall(_tag(ns, "分节_416A"))
        if not sections:
            raise ValueError("UOF word-processing document has no body section")
        # A UOF 2 section is a properties marker, not a paragraph container.
        # Paragraphs and tables are siblings of section markers.
        body = root
    else:
        body = next(root.iterdescendants(_tag(ns, "主体")), None)
        if body is None:
            raise ValueError("UOF word-processing document has no body")
    if any(_has_text(p) for p in body if p.tag == _tag(ns, "段落_416B" if version == 2 else "段落")):
        return False
    paragraph = _paragraph(ns, version)
    if version == 2 and len(body) and body[-1].tag == _tag(ns, "分节_416A"):
        body.insert(len(body) - 1, paragraph)
    else:
        body.append(paragraph)
    return True


def _sheet_mark(root, version):
    ns = ET.QName(root).namespace if version == 2 else UOF1["sheet"]
    word_ns = posixpath.join(ns.rsplit("/", 1)[0], "wordproc") if version == 2 else UOF1["word"]
    names = (
        ("工作表_E825", "工作表内容_E80E", "行_E7F1", "单元格_E7F2", "数据_E7B3")
        if version == 2
        else ("工作表", "工作表内容", "行", "单元格", "数据")
    )
    sheets = list(root.iter(_tag(ns, names[0])))
    if not sheets:
        raise ValueError("UOF spreadsheet has no worksheets")
    changed = False
    for sheet in sheets:
        body = sheet.find(_tag(ns, names[1]))
        if body is None:
            raise ValueError("UOF chart-only worksheet cannot receive a cell watermark")
        if any(_has_text(r) for r in body if r.tag == _tag(ns, names[2])):
            continue
        row_attr = "行号_E7F3" if version == 2 else _tag(ns, "行号")
        rows = [int(r.get(row_attr, "0")) for r in body if r.tag == _tag(ns, names[2])]
        row_number = max(rows, default=0) + 1
        if row_number > 1048576:
            raise ValueError("UOF worksheet has no free row for the watermark")
        row_attrs = {
            row_attr: str(row_number),
            "是否隐藏_E73C" if version == 2 else _tag(ns, "隐藏"): "false",
            "行高_E7F4" if version == 2 else _tag(ns, "行高"): "24",
        }
        row = ET.Element(_tag(ns, names[2]), attrib=row_attrs)
        cell = _child(row, ns, names[3], attributes={"列号_E7BC" if version == 2 else _tag(ns, "列号"): "1"})
        data = _child(cell, ns, names[4], attributes={"类型_E7B6" if version == 2 else _tag(ns, "数据类型"): "text"})
        sentence = _child(data, word_ns, "句_419D" if version == 2 else "句")
        _child(sentence, word_ns, "文本串_415B" if version == 2 else "文本串", VISIBLE_TEXT)
        _insert_after(body, row, _tag(ns, names[2]))
        bounds = body.find(_tag(ns, "最大行列_E7E6" if version == 2 else "最大行列"))
        if version == 1:
            body.set(_tag(ns, "最大行"), str(row_number))
        if bounds is not None:
            bounds.set("最大行_E7E7" if version == 2 else _tag(ns, "最大行"), str(row_number))
        changed = True
    return changed


def _graphic(version, graphic_id, ns=None):
    ns = ns or (UOF2 if version == 2 else UOF1)
    graph = ET.Element(
        _tag(ns["graph"], "图形_8062" if version == 2 else "图形"),
        attrib={"标识符_804B" if version == 2 else _tag(ns["graph"], "标识符"): graphic_id},
    )
    if version == 1:
        graph.set(_tag(ns["graph"], "层次"), "32767")
    shape = _child(graph, ns["graph"], "预定义图形_8018" if version == 2 else "预定义图形")
    _child(shape, ns["graph"], "类别_8019" if version == 2 else "类别", "11")
    _child(shape, ns["graph"], "名称_801A" if version == 2 else "名称", "Rectangle")
    _child(shape, ns["graph"], "生成软件_801B" if version == 2 else "生成软件", "AIGC Marker")
    props = _child(shape, ns["graph"], "属性_801D" if version == 2 else "属性")
    _child(props, ns["graph"], "旋转角度_804D" if version == 2 else "旋转角度", "0.0")
    _child(props, ns["graph"], "是否打印对象_804E" if version == 2 else "打印对象", "true")
    if version == 2:
        _child(props, ns["graph"], "大小_8060", attributes={"长_C604": "24", "宽_C605": "180"})
        text = _child(
            graph, ns["graph"], "文本_803C", attributes={"是否为文本框_8046": "true", "是否自动换行_8047": "false"}
        )
        _child(
            text, ns["graph"], "边距_803D", attributes={"左_C608": "0", "上_C609": "0", "右_C60A": "0", "下_C60B": "0"}
        )
        _child(text, ns["graph"], "对齐_803E", attributes={"水平对齐_421D": "left", "文字对齐_421E": "top"})
        _child(text, ns["graph"], "文字排列方向_8042", "t2b-l2r-0e-0w")
        content = _child(text, ns["graph"], "内容_8043")
    else:
        _child(props, ns["graph"], "宽度", "6")
        _child(props, ns["graph"], "高度", "0.8")
        content = _child(graph, ns["graph"], "文本内容", attributes={_tag(ns["graph"], "文本框"): "true"})
    content.append(_paragraph(ns["word"], version))
    return graph


def _presentation_mark(root, graphic_root, version):
    ns = (UOF3 if ET.QName(root).namespace == UOF3["presentation"] else UOF2) if version == 2 else UOF1
    slides = list(root.iter(_tag(ns["presentation"], "幻灯片_6C0F" if version == 2 else "幻灯片")))
    show_attr = "是否显示_6B28" if version == 2 else _tag(ns["presentation"], "显示")
    slides = [slide for slide in slides if slide.get(show_attr, "true").lower() not in ("false", "0", "否")]
    if not slides:
        raise ValueError("UOF presentation has no visible slides")
    slide = slides[0]
    anchor_tag = _tag(ns["uof"], "锚点_C644" if version == 2 else "锚点")
    ref_attr = "图形引用_C62E" if version == 2 else _tag(ns["uof"], "图形引用")
    id_attr = "标识符_804B" if version == 2 else _tag(ns["graph"], "标识符")
    refs = {anchor.get(ref_attr) for anchor in slide if anchor.tag == anchor_tag}
    if any(g.get(id_attr) in refs and _has_text(g) for g in graphic_root):
        return False
    graphic_id = _uid()
    graphic_root.append(_graphic(version, graphic_id, ns))
    anchor = ET.Element(anchor_tag, attrib={ref_attr: graphic_id})
    if version == 2:
        position = _child(anchor, ns["uof"], "位置_C620")
        horizontal = _child(position, ns["uof"], "水平_4106")
        _child(horizontal, ns["uof"], "绝对_4107", attributes={"值_4108": "12"})
        vertical = _child(position, ns["uof"], "垂直_410D")
        _child(vertical, ns["uof"], "绝对_4107", attributes={"值_4108": "12"})
        _child(anchor, ns["uof"], "大小_C621", attributes={"长_C604": "24", "宽_C605": "180"})
    else:
        for key, value in {"x坐标": "0.5", "y坐标": "0.5", "宽度": "6", "高度": "0.8"}.items():
            anchor.set(_tag(ns["uof"], key), value)
    _insert_after(slide, anchor, anchor_tag)
    return True


class UofAigcDecorator:
    """Preserve UOF containers while adding native content and metadata."""

    def __init__(self):
        self.name = "uof_aigc_decorator"

    def decorate(self, file_path, fallback_signature, add_visible_mark=True):
        path = Path(file_path)
        kind = KINDS.get(path.suffix.lower())
        if kind is None:
            raise ValueError("Unsupported UOF extension")
        if zipfile.is_zipfile(path):
            self._package(path, kind, fallback_signature, add_visible_mark)
        else:
            self._flat(path, kind, fallback_signature, add_visible_mark)
        agent_logger.debug("  [UOF] AIGC mark added successfully")

    @staticmethod
    def get_aigc_data(file_path):
        path = Path(file_path)
        if zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as archive:
                names = _member_names(archive)
                if "_meta/meta.xml" not in names:
                    return None
                meta = _parse(archive.read(names["_meta/meta.xml"])).getroot()
            raw = _package_signature(meta)
        else:
            root = _parse(path.read_bytes()).getroot()
            meta = root.find(_tag(UOF1["uof"], "元数据"))
            raw = _signature(meta, UOF1["uof"], 1) if meta is not None else None
        return parse_aigc_json(raw)

    def _replace(self, path, write):
        fd, temp = tempfile.mkstemp(prefix=".aigc-", suffix=path.suffix, dir=path.parent)
        os.close(fd)
        try:
            write(temp)
            if not is_aigc_complete(self.get_aigc_data(temp)):
                raise ValueError("UOF metadata readback failed before replacement")
            os.replace(temp, path)
        finally:
            if os.path.exists(temp):
                os.remove(temp)

    def _flat(self, path, kind, fallback_signature, visible):
        tree = _parse(path.read_bytes())
        root = tree.getroot()
        if root.tag != _tag(UOF1["uof"], "UOF"):
            raise ValueError("Expected a UOF 1 XML document or a UOF 2 ZIP package")
        sections = {"word": "文字处理", "sheet": "电子表格", "presentation": "演示文稿"}
        section = sections.get(kind)
        if section is None or root.find(_tag(UOF1["uof"], section)) is None:
            raise ValueError("UOF document type does not match the file extension")
        meta = root.find(_tag(UOF1["uof"], "元数据"))
        if meta is None:
            meta = ET.Element(_tag(UOF1["uof"], "元数据"))
            root.insert(0, meta)
        signature = _signature(meta, UOF1["uof"], 1) or fallback_signature
        _metadata(meta, UOF1["uof"], 1, signature)
        if visible:
            if kind == "word":
                _word_mark(root, 1)
            elif kind == "sheet":
                _sheet_mark(root, 1)
            else:
                graphics = root.find(_tag(UOF1["uof"], "对象集"))
                if graphics is None:
                    graphics = ET.Element(_tag(UOF1["uof"], "对象集"))
                    root.insert(1, graphics)
                _presentation_mark(root, graphics, 1)
        # A UOF 1 document is a single XML that cannot carry the guide 6.2 ZIP
        # storage.  Package the marked document into a UOF 2021 ZIP container so
        # the implicit identifier lives in _meta/meta.xml and is detectable by
        # guide-compliant readers.  The original body is preserved byte for byte
        # inside content.xml, with its native metadata retained for UOF 1 apps.
        self._replace(path, lambda temp: self._wrap_uof1_package(temp, kind, _xml(tree), signature))

    @staticmethod
    def _wrap_uof1_package(temp, kind, body_xml, signature):
        """Write a UOF 2021 ZIP package exposing the guide AIGC record.

        The marked UOF 1 body is preserved byte for byte as content.xml.  The
        new _meta/meta.xml mirrors the guide 6.2 structure used by GCMark.
        Companion parts use UOF 2021 namespaces for a well-formed package.
        """
        ns = UOF3
        meta_root = ET.Element(_tag(ns["meta"], "元数据_5200"), nsmap={"meta": ns["meta"]})
        _gcmark_metadata(meta_root, signature)
        mime = {"word": "text", "sheet": "spreadsheet", "presentation": "presentation"}[kind]
        manifest = ET.Element(
            _tag(ns["uof"], "UOF_0000"),
            nsmap={"uof": ns["uof"]},
            attrib={"mimetype_0001": "vnd.uof." + mime, "language_0002": "cn", "version_0003": "3.0"},
        )
        entries = {
            "uof.xml": _xml(ET.ElementTree(manifest)),
            "content.xml": body_xml,
            "_meta/meta.xml": _xml(ET.ElementTree(meta_root)),
            "styles.xml": _xml(
                ET.ElementTree(
                    ET.Element(
                        _tag("http://schemas.uof.org/cn/2009/styles", "式样集_990B"),
                        nsmap={"style": "http://schemas.uof.org/cn/2009/styles"},
                    )
                )
            ),
            "graphics.xml": _xml(
                ET.ElementTree(ET.Element(_tag(ns["graphics"], "图形集_7C00"), nsmap={"graphics": ns["graphics"]}))
            ),
            "rules.xml": _xml(
                ET.ElementTree(ET.Element(_tag(ns["rules"], "公用处理规则_B665"), nsmap={"rules": ns["rules"]}))
            ),
            "hyperlinks.xml": _xml(
                ET.ElementTree(ET.Element(_tag("http://schemas.uof.org/cn/2009/hyperlinks", "超级链接集_AA0B")))
            ),
            "objectdata.xml": _xml(
                ET.ElementTree(ET.Element(_tag("http://schemas.uof.org/cn/2009/objects", "对象数据集_D700")))
            ),
            "extend.xml": _xml(
                ET.ElementTree(ET.Element(_tag("http://schemas.uof.org/cn/2009/extend", "扩展区_B200")))
            ),
            "theme.xml": _xml(ET.ElementTree(ET.Element(_tag(ns["rules"], "配色方案集_6C11")))),
        }
        with zipfile.ZipFile(temp, "w", zipfile.ZIP_DEFLATED) as package:
            for name, value in entries.items():
                package.writestr(name, value)

    @staticmethod
    def _is_wrapped(path):
        """True when content.xml holds a preserved UOF 1 ``UOF`` document."""
        try:
            with zipfile.ZipFile(path) as source:
                lower = {name.lower(): name for name in source.namelist()}
                if "content.xml" not in lower:
                    return False
                root = _parse(source.read(lower["content.xml"])).getroot()
            return root.tag == _tag(UOF1["uof"], "UOF")
        except Exception:
            return False

    def _wrapped_package(self, path, kind, fallback_signature, visible):
        """Refresh a package created from a UOF 1 body (_flat conversion).

        The preserved UOF 1 body keeps its native metadata and visible mark.
        This pass only re-syncs the guide record in _meta/meta.xml and, for an
        upgrade from implicit-only, adds the visible mark to the preserved body.
        """
        with zipfile.ZipFile(path) as source:
            names = source.namelist()
            lower = {name.lower(): name for name in names}
            meta_name = lower.get("_meta/meta.xml")
            if meta_name is None:
                raise ValueError("Legacy-wrapped UOF package is missing _meta/meta.xml")
            meta_tree = _parse(source.read(meta_name))
            meta = meta_tree.getroot()
            signature = _package_signature(meta) or fallback_signature
            updates = {}
            if _gcmark_metadata(meta, signature):
                updates["_meta/meta.xml"] = _xml(meta_tree)
            if visible:
                body_name = lower["content.xml"]
                body_tree = _parse(source.read(body_name))
                root = body_tree.getroot()
                if kind == "word":
                    body_marked = _word_mark(root, 1)
                elif kind == "sheet":
                    body_marked = _sheet_mark(root, 1)
                else:
                    graphics = root.find(_tag(UOF1["uof"], "对象集"))
                    if graphics is None:
                        graphics = ET.Element(_tag(UOF1["uof"], "对象集"))
                        root.insert(1, graphics)
                    body_marked = _presentation_mark(root, graphics, 1)
                if body_marked:
                    updates[body_name] = _xml(body_tree)
            if not updates:
                return
            records = []
            for original_info in source.infolist():
                info = copy.copy(original_info)
                target = info.filename
                if info.filename == meta_name:
                    info.filename = "_meta/meta.xml"
                    target = "_meta/meta.xml"
                data = updates.pop(target, None)
                records.append((info, data if data is not None else source.read(original_info)))
            archive_comment = source.comment

        # Close the source before replacement (required on Windows).
        def write(temp):
            with zipfile.ZipFile(temp, "w", compression=zipfile.ZIP_DEFLATED) as target:
                target.comment = archive_comment
                for info, data in records:
                    target.writestr(info, data)
                for name, data in updates.items():
                    target.writestr(name, data)

        self._replace(path, write)

    def _package(self, path, kind, fallback_signature, visible):
        if self._is_wrapped(path):
            self._wrapped_package(path, kind, fallback_signature, visible)
            return
        with zipfile.ZipFile(path) as source:
            names = source.namelist()
            lower_names = _member_names(source)
            if any("encrypt" in name.lower() or "signature" in name.lower() for name in names):
                raise ValueError("Encrypted or signed UOF packages are not supported")
            if "content.xml" not in lower_names or "uof.xml" not in lower_names:
                raise ValueError("UOF 2 package must contain uof.xml and content.xml")
            manifest = _parse(source.read(lower_names["uof.xml"])).getroot()
            namespaces = next(
                (family for family in (UOF2, UOF3) if manifest.tag == _tag(family["uof"], "UOF_0000")), None
            )
            if namespaces is None:
                raise ValueError("Expected a UOF 2009 or 2021 ZIP package")
            body_name = lower_names["content.xml"]
            body_tree = _parse(source.read(body_name))
            body = body_tree.getroot()
            if body.tag != _tag(namespaces[kind], CONTENT_ROOTS[kind]):
                raise ValueError("UOF document type does not match the file extension")
            meta_name = lower_names.get("_meta/meta.xml", "_meta/meta.xml")
            meta_tree = (
                _parse(source.read(meta_name))
                if meta_name in names
                else ET.ElementTree(
                    ET.Element(_tag(namespaces["meta"], "元数据_5200"), nsmap={"meta": namespaces["meta"]})
                )
            )
            meta = meta_tree.getroot()
            if meta.tag != _tag(namespaces["meta"], "元数据_5200"):
                raise ValueError("Unexpected UOF metadata root")
            signature = _package_signature(meta) or fallback_signature
            updates = {}
            metadata_changed = _metadata(meta, UOF2["meta"], 2, signature) if namespaces is UOF2 else False
            metadata_changed |= _gcmark_metadata(meta, signature)
            if metadata_changed or meta_name != "_meta/meta.xml":
                updates["_meta/meta.xml"] = _xml(meta_tree)
            if visible:
                if kind == "word":
                    changed = _word_mark(body, 2)
                elif kind == "sheet":
                    changed = _sheet_mark(body, 2)
                else:
                    graph_name = lower_names.get("graphics.xml", "graphics.xml")
                    graph_tree = (
                        _parse(source.read(graph_name))
                        if graph_name in names
                        else ET.ElementTree(
                            ET.Element(
                                _tag(namespaces["graphics"], "图形集_7C00"),
                                nsmap={"graphics": namespaces["graphics"], "graph": namespaces["graph"]},
                            )
                        )
                    )
                    if graph_tree.getroot().tag != _tag(namespaces["graphics"], "图形集_7C00"):
                        raise ValueError("Unexpected UOF graphics root")
                    changed = _presentation_mark(body, graph_tree.getroot(), 2)
                    if changed:
                        updates[graph_name] = _xml(graph_tree)
                if changed:
                    updates[body_name] = _xml(body_tree)
            if not updates:
                return
            records = []
            for original_info in source.infolist():
                info = copy.copy(original_info)
                if info.filename == meta_name:
                    info.filename = "_meta/meta.xml"
                data = updates.pop(info.filename, None)
                records.append((info, data if data is not None else source.read(original_info)))
            archive_comment = source.comment

        # Close the source before replacement (required on Windows).
        def write(temp):
            with zipfile.ZipFile(temp, "w", compression=zipfile.ZIP_DEFLATED) as target:
                target.comment = archive_comment
                for info, data in records:
                    target.writestr(info, data)
                for name, data in updates.items():
                    target.writestr(name, data)

        self._replace(path, write)
