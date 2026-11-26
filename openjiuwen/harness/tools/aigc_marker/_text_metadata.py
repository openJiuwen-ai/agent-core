# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Read-only verification against TC260-PG-20258A sections 6.1-6.7."""

import json
import posixpath
import zipfile
from pathlib import Path

from lxml import etree as ET

from openjiuwen.harness.tools.aigc_marker._common import AIGC_FIELDS, is_aigc_complete, unique_json_object

GUIDE_EXTENSIONS = frozenset(
    {
        ".docx",
        ".dotx",
        ".xlsx",
        ".xltx",
        ".pptx",
        ".potx",
        ".uot",
        ".uott",
        ".uos",
        ".uost",
        ".uop",
        ".uopt",
        ".pdf",
        ".ofd",
        ".md",
        ".xmind",
        ".posm",
        ".posf",
    }
)


def _record(raw, allow_object=False):
    if allow_object and isinstance(raw, dict):
        data = raw
    elif isinstance(raw, str):
        data = json.loads(raw, object_pairs_hook=unique_json_object)
    else:
        raise ValueError("AIGC value must be a JSON string")
    if not is_aigc_complete(data) or any(k not in data or not isinstance(data[k], str) for k in AIGC_FIELDS):
        raise ValueError("AIGC must contain seven string fields and a valid Label")
    return data


def _archive(path):
    archive = zipfile.ZipFile(path)
    names = archive.namelist()
    if len(set(n.casefold() for n in names)) != len(names):
        archive.close()
        raise ValueError("Ambiguous or duplicate ZIP members")
    return archive


def _xml(raw):
    tree = ET.fromstring(raw, ET.XMLParser(resolve_entities=False, no_network=True))
    if tree.getroottree().docinfo.doctype:
        raise ValueError("Metadata with a DTD is not supported")
    return tree


def inspect_text_metadata(file_path):
    path = Path(file_path)
    ext = path.suffix.lower()
    profile = "TC260-PG-20258A"
    values = []
    if ext in (".docx", ".dotx", ".xlsx", ".xltx", ".pptx", ".potx"):
        from openjiuwen.harness.tools.aigc_marker._ooxml_decorator import NS, STRICT_NS

        with _archive(path) as z:
            root = _xml(z.read("docProps/custom.xml"))
            rels = _xml(z.read("_rels/.rels"))
            links = [
                r for r in rels if r.get("Type", "").rsplit("/", 1)[-1] in ("custom-properties", "customProperties")
            ]
            if (
                len(links) != 1
                or links[0].get("TargetMode") == "External"
                or links[0].get("Target", "").lstrip("/") != "docProps/custom.xml"
            ):
                raise ValueError("OOXML custom property relationship does not reference docProps/custom.xml")
        if ET.QName(root).localname != "Properties" or ET.QName(root).namespace not in (NS["cp"], STRICT_NS["cp"]):
            raise ValueError("Invalid OOXML custom property namespace")
        props = [p for p in root if p.get("name") == "AIGC"]
        invalid_property = "Expected one AIGC property with one lpwstr value"
        if len(props) != 1 or len(props[0]) != 1:
            raise ValueError(invalid_property)
        value = props[0][0]
        tag = ET.QName(value)
        if tag.localname != "lpwstr" or tag.namespace not in (NS["vt"], STRICT_NS["vt"]):
            raise ValueError(invalid_property)
        values = [_record(value.text)]
        storage = "docProps/custom.xml/Properties/property[@name=AIGC]/lpwstr"
    elif ext in (".uot", ".uott", ".uos", ".uost", ".uop", ".uopt"):
        from openjiuwen.harness.tools.aigc_marker._uof_decorator import UOF_GCMARK_META, UofAigcDecorator

        if not zipfile.is_zipfile(path):
            data = UofAigcDecorator.get_aigc_data(path)
            values = [_record(data, True)]
            profile = "legacy-UOF1-extension"
            storage = "UOF1 XML native metadata (not section 6.2 ZIP storage)"
        else:
            with _archive(path) as z:
                meta = _xml(z.read("_meta/meta.xml"))

            def q(name):
                return "{" + UOF_GCMARK_META + "}" + name

            props = meta.findall(q("自定义元数据集_520F") + "/" + q("自定义元数据_5210"))
            props = [p for p in props if p.get("名称_5211") == "AIGC"]
            if len(props) != 1:
                raise ValueError("Expected one guide-format UOF AIGC property")
            values = [_record(props[0].text)]
            storage = "_meta/meta.xml/自定义元数据集_520F/自定义元数据_5210[@名称_5211=AIGC]"
    elif ext == ".pdf":
        from pypdf import PdfReader

        values = [_record((PdfReader(path).metadata or {}).get("/AIGC"))]
        storage = "Document Information Dictionary /AIGC"
    elif ext == ".ofd":
        ns = {"o": "http://www.ofdspec.org/2016"}
        with _archive(path) as z:
            root = _xml(z.read("OFD.xml"))
        bodies = root.findall("o:DocBody", ns)
        if not bodies:
            raise ValueError("OFD has no DocBody")
        for body in bodies:
            props = body.findall('o:DocInfo/o:CustomDatas/o:CustomData[@name="AIGC"]', ns)
            if len(props) != 1:
                raise ValueError("Expected one lowercase-name AIGC property per OFD DocBody")
            values.append(_record(props[0].text))
        storage = "OFD.xml/DocBody/DocInfo/CustomDatas/CustomData[@name=AIGC]"
    elif ext == ".md":
        from openjiuwen.harness.tools.aigc_marker._md_decorator import MdAigcDecorator

        values = [_record(MdAigcDecorator.get_aigc_data(path), True)]
        storage = "YAML frontmatter AIGC mapping"
    elif ext in (".xmind", ".posm", ".posf"):
        part = "metadata.json" if ext == ".xmind" else "meta.json"
        with _archive(path) as z:
            meta = json.loads(z.read(part).decode("utf-8-sig"), object_pairs_hook=unique_json_object)
        if not isinstance(meta, dict):
            raise ValueError("Metadata root must be an object")
        values = [_record(meta.get("AIGC"))]
        metadata_path = posixpath.join(part, "AIGC")
        storage = f"{metadata_path} (JSON string)"
    else:
        raise ValueError("Format is outside the text metadata guide")
    if not values:
        raise ValueError("No AIGC record found")
    return {
        "profile": profile,
        "storage": storage,
        "metadata": values[0],
        "record_count": len(values),
        "guide_storage_verified": profile == "TC260-PG-20258A",
    }
