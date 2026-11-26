# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Mark OOXML documents and templates without converting their package types."""

import posixpath
import re
import zipfile
from pathlib import Path

from lxml import etree as ET

from openjiuwen.harness.tools.aigc_marker._common import (
    DEFAULT_CUSTOM_PROPERTY_FMTID,
    parse_aigc_json,
    select_signature,
)
from openjiuwen.harness.tools.aigc_marker._package_utils import replace_zip_entries

NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
    "ct": "http://schemas.openxmlformats.org/package/2006/content-types",
    "cp": "http://schemas.openxmlformats.org/officeDocument/2006/custom-properties",
    "vt": "http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes",
}
STRICT_NS = {
    **NS,
    "w": "http://purl.oclc.org/ooxml/wordprocessingml/main",
    "s": "http://purl.oclc.org/ooxml/spreadsheetml/main",
    "p": "http://purl.oclc.org/ooxml/presentationml/main",
    "a": "http://purl.oclc.org/ooxml/drawingml/main",
    "r": "http://purl.oclc.org/ooxml/officeDocument/relationships",
    "cp": "http://purl.oclc.org/ooxml/officeDocument/customProperties",
    "vt": "http://purl.oclc.org/ooxml/officeDocument/docPropsVTypes",
}
TEXT = "内容由AI生成"
MARK_NAME = "AIGC Marker"
CUSTOM_TYPE = "application/vnd.openxmlformats-officedocument.custom-properties+xml"


def q(prefix, name):
    return f"{{{NS[prefix]}}}{name}"


def xml(data):
    return ET.fromstring(data, ET.XMLParser(resolve_entities=False, no_network=True))


def dump(root):
    return ET.tostring(root, encoding="UTF-8", xml_declaration=True, standalone=True)


def relationships_name(part):
    return posixpath.join(posixpath.dirname(part), "_rels", posixpath.basename(part) + ".rels")


def resolve_part(part, target):
    result = posixpath.normpath(posixpath.join(posixpath.dirname(part), target.replace("\\", "/")))
    if target.startswith("/"):
        result = target.lstrip("/")
    if result == ".." or result.startswith("../") or ":" in result:
        raise ValueError("OOXML relationship escapes the package")
    return result


class OoxmlAigcDecorator:
    """Preserve all original parts, including template content types and unknown extensions."""

    def __init__(self):
        self.ns = NS.copy()

    def q(self, prefix, name):
        return f"{{{self.ns[prefix]}}}{name}"

    @staticmethod
    def _get_aigc_data(file_path):
        with zipfile.ZipFile(file_path) as archive:
            rels = xml(archive.read("_rels/.rels"))
            relation = next(
                (r for r in rels if r.get("Type", "").rsplit("/", 1)[-1] in ("custom-properties", "customProperties")),
                None,
            )
            if relation is None:
                return None
            part = resolve_part("", relation.get("Target", ""))
            for prop in xml(archive.read(part)):
                if prop.get("name") == "AIGC" and len(prop):
                    return parse_aigc_json(prop[0].text)
        return None

    def decorate(self, file_path, fallback_signature, add_visible_mark=True):
        updates = {}
        with zipfile.ZipFile(file_path) as archive:
            root_rels = xml(archive.read("_rels/.rels"))
            office = next((r for r in root_rels if r.get("Type", "").endswith("/officeDocument")), None)
            if office is None or office.get("TargetMode") == "External":
                raise ValueError("OOXML office document relationship is missing")
            main = resolve_part("", office.get("Target", ""))
            document = xml(archive.read(main))
            strict = ET.QName(document).namespace in (STRICT_NS["w"], STRICT_NS["s"], STRICT_NS["p"])
            self.ns = (STRICT_NS if strict else NS).copy()
            extension = Path(file_path).suffix.lower()
            if add_visible_mark:
                if extension in (".docx", ".dotx"):
                    self._word(document)
                    updates[main] = dump(document)
                elif extension in (".xlsx", ".xltx"):
                    self._excel(archive, main, document, updates)
                elif extension in (".pptx", ".potx"):
                    self._powerpoint(archive, main, document, updates)
                else:
                    raise ValueError(f"unsupported OOXML extension: {extension}")
            self._metadata(archive, root_rels, fallback_signature, updates)
        replace_zip_entries(file_path, updates)

    def _metadata(self, archive, root_rels, fallback_signature, updates):
        relation = next(
            (r for r in root_rels if r.get("Type", "").rsplit("/", 1)[-1] in ("custom-properties", "customProperties")),
            None,
        )
        if relation is not None and relation.get("TargetMode") == "External":
            raise ValueError("external custom properties are not supported")
        part = resolve_part("", relation.get("Target", "")) if relation is not None else "docProps/custom.xml"
        if part in archive.namelist():
            properties = xml(archive.read(part))
        else:
            properties = ET.Element(self.q("cp", "Properties"), nsmap={None: self.ns["cp"], "vt": self.ns["vt"]})
        # An existing properties part retains its own namespace family.
        property_ns = ET.QName(properties).namespace
        value_ns = STRICT_NS["vt"] if property_ns == STRICT_NS["cp"] else NS["vt"]

        def metadata_q(name):
            return f"{{{property_ns}}}{name}"

        if property_ns not in (NS["cp"], STRICT_NS["cp"]):
            raise ValueError("Unexpected OOXML custom property namespace")
        props = [p for p in properties if p.get("name") == "AIGC"]
        signature = select_signature([p[0].text for p in props if len(p)], fallback_signature)
        prop = props[0] if props else None
        if prop is None:
            ids = [int(p.get("pid", "1")) for p in properties]
            prop = ET.SubElement(
                properties,
                metadata_q("property"),
                fmtid="{" + DEFAULT_CUSTOM_PROPERTY_FMTID + "}",
                pid=str(max(ids, default=1) + 1),
                name="AIGC",
            )
        for duplicate in props[1:]:
            properties.remove(duplicate)
        for child in list(prop):
            prop.remove(child)
        ET.SubElement(prop, f"{{{value_ns}}}lpwstr").text = signature
        updates[part] = dump(properties)
        if relation is None:
            used = {r.get("Id") for r in root_rels}
            index = 1
            while f"rIdAigc{index}" in used:
                index += 1
            ET.SubElement(
                root_rels,
                self.q("rel", "Relationship"),
                Id=f"rIdAigc{index}",
                Type=self.ns["r"] + ("/customProperties" if self.ns["r"] == STRICT_NS["r"] else "/custom-properties"),
                Target=part,
            )
            updates["_rels/.rels"] = dump(root_rels)
        types = xml(archive.read("[Content_Types].xml"))
        if not any(t.get("PartName") == "/" + part for t in types):
            ET.SubElement(types, self.q("ct", "Override"), PartName="/" + part, ContentType=CUSTOM_TYPE)
            updates["[Content_Types].xml"] = dump(types)

    def _word(self, document):
        body = document.find(self.q("w", "body"))
        if body is None:
            raise ValueError("Word body is missing")
        if any("".join(p.itertext()).strip() == TEXT for p in body.findall(self.q("w", "p"))):
            return
        paragraph = ET.Element(self.q("w", "p"))
        run = ET.SubElement(paragraph, self.q("w", "r"))
        properties = ET.SubElement(run, self.q("w", "rPr"))
        ET.SubElement(
            properties, self.q("w", "rFonts"), {self.q("w", "ascii"): "宋体", self.q("w", "eastAsia"): "宋体"}
        )
        ET.SubElement(properties, self.q("w", "sz"), {self.q("w", "val"): "21"})
        ET.SubElement(run, self.q("w", "t")).text = TEXT
        section = body.find(self.q("w", "sectPr"))
        body.insert(body.index(section) if section is not None else len(body), paragraph)

    def _excel(self, archive, main, workbook, updates):
        rels = xml(archive.read(relationships_name(main)))
        worksheets = {r.get("Id"): r for r in rels if r.get("Type", "").endswith("/worksheet")}
        count = 0
        for sheet in workbook.findall("s:sheets/s:sheet", self.ns):
            rel = worksheets.get(sheet.get(self.q("r", "id")))
            if rel is None:
                continue  # chartsheets have no cell grid
            part = resolve_part(main, rel.get("Target", ""))
            root = xml(archive.read(part))
            data = root.find(self.q("s", "sheetData"))
            if data is None:
                raise ValueError("worksheet sheetData is missing")
            if not any(t.text == TEXT for t in data.iter(self.q("s", "t"))):
                row_num = max((int(row.get("r", "0")) for row in data), default=0) + 1
                if row_num > 1048576:
                    raise ValueError("worksheet has no free row for AIGC label")
                row = ET.SubElement(data, self.q("s", "row"), r=str(row_num), hidden="0", ht="15", customHeight="1")
                cell = ET.SubElement(row, self.q("s", "c"), r=f"A{row_num}", t="inlineStr")
                ET.SubElement(ET.SubElement(cell, self.q("s", "is")), self.q("s", "t")).text = TEXT
                dimension = root.find(self.q("s", "dimension"))
                if dimension is not None:
                    old = dimension.get("ref", "A1").split(":")
                    end_col = re.sub(r"\d", "", old[-1]) or "A"
                    start_row = re.sub(r"\D", "", old[0]) or "1"
                    dimension.set("ref", f"A{start_row}:{end_col}{row_num}")
            # Keep a label on printed/exported pages even when Print_Area is fixed.
            footer = root.find(self.q("s", "headerFooter"))
            if footer is None:
                footer = ET.Element(self.q("s", "headerFooter"))
                following = {
                    "rowBreaks",
                    "colBreaks",
                    "customProperties",
                    "cellWatches",
                    "ignoredErrors",
                    "smartTags",
                    "drawing",
                    "legacyDrawing",
                    "legacyDrawingHF",
                    "picture",
                    "oleObjects",
                    "controls",
                    "webPublishItems",
                    "tableParts",
                    "extLst",
                }
                index = next((i for i, child in enumerate(root) if ET.QName(child).localname in following), len(root))
                root.insert(index, footer)
            for kind in ("oddFooter", "evenFooter", "firstFooter"):
                node = footer.find(self.q("s", kind))
                if node is None:
                    node = ET.SubElement(footer, self.q("s", kind))
                if TEXT not in (node.text or ""):
                    node.text = (node.text or "") + "&R" + TEXT
            footer_parts = ("oddHeader", "oddFooter", "evenHeader", "evenFooter", "firstHeader", "firstFooter")
            order = {name: index for index, name in enumerate(footer_parts)}
            footer[:] = sorted(footer, key=lambda node: order.get(ET.QName(node).localname, 99))
            updates[part] = dump(root)
            count += 1
        if count == 0:
            raise ValueError("workbook has no worksheets for a visible label")

    def _powerpoint(self, archive, main, presentation, updates):
        slides = presentation.find(self.q("p", "sldIdLst"))
        if slides is None or len(slides) == 0:
            raise ValueError("presentation has no slides for a visible label")
        rels = xml(archive.read(relationships_name(main)))
        by_id = {r.get("Id"): r for r in rels}
        rel = None
        for slide_id in slides:
            candidate = by_id.get(slide_id.get(self.q("r", "id")))
            if candidate is not None:
                candidate_root = xml(archive.read(resolve_part(main, candidate.get("Target", ""))))
                if candidate_root.get("show", "1") not in ("0", "false"):
                    rel = candidate
                    break
        if rel is None:
            raise ValueError("presentation has no visible slides for a label")
        part = resolve_part(main, rel.get("Target", ""))
        slide = xml(archive.read(part))
        tree = slide.find("p:cSld/p:spTree", self.ns)
        if tree is None:
            raise ValueError("slide shape tree is missing")
        if any(t.text == TEXT for t in tree.iter(self.q("a", "t"))):
            return
        size = presentation.find(self.q("p", "sldSz"))
        if size is None:
            raise ValueError("presentation slide size is missing")
        width, height = int(size.get("cx")), int(size.get("cy"))
        box_w, box_h = min(2743200, width - 2 * 91440), min(365760, height - 2 * 91440)
        if box_w <= 0 or box_h <= 0:
            raise ValueError("slide is too small for a visible label")
        ids = [int(e.get("id", "0")) for e in tree.iter(self.q("p", "cNvPr"))]
        shape = ET.Element(self.q("p", "sp"))
        extension = tree.find(self.q("p", "extLst"))
        tree.insert(tree.index(extension) if extension is not None else len(tree), shape)
        nv = ET.SubElement(shape, self.q("p", "nvSpPr"))
        ET.SubElement(nv, self.q("p", "cNvPr"), id=str(max(ids, default=0) + 1), name=MARK_NAME)
        ET.SubElement(nv, self.q("p", "cNvSpPr"), txBox="1")
        ET.SubElement(nv, self.q("p", "nvPr"))
        sp = ET.SubElement(shape, self.q("p", "spPr"))
        transform = ET.SubElement(sp, self.q("a", "xfrm"))
        ET.SubElement(transform, self.q("a", "off"), x=str(width - box_w - 91440), y=str(height - box_h - 91440))
        ET.SubElement(transform, self.q("a", "ext"), cx=str(box_w), cy=str(box_h))
        geometry = ET.SubElement(sp, self.q("a", "prstGeom"), prst="rect")
        ET.SubElement(geometry, self.q("a", "avLst"))
        ET.SubElement(sp, self.q("a", "noFill"))
        ET.SubElement(ET.SubElement(sp, self.q("a", "ln")), self.q("a", "noFill"))
        tx = ET.SubElement(shape, self.q("p", "txBody"))
        ET.SubElement(tx, self.q("a", "bodyPr"), wrap="none", anchor="ctr")
        ET.SubElement(tx, self.q("a", "lstStyle"))
        paragraph = ET.SubElement(tx, self.q("a", "p"))
        ET.SubElement(paragraph, self.q("a", "pPr"), algn="r")
        run = ET.SubElement(paragraph, self.q("a", "r"))
        rpr = ET.SubElement(run, self.q("a", "rPr"), lang="zh-CN", sz="1600")
        ET.SubElement(ET.SubElement(rpr, self.q("a", "solidFill")), self.q("a", "srgbClr"), val="808080")
        ET.SubElement(rpr, self.q("a", "latin"), typeface="微软雅黑")
        ET.SubElement(rpr, self.q("a", "ea"), typeface="微软雅黑")
        ET.SubElement(run, self.q("a", "t")).text = TEXT
        updates[part] = dump(slide)
