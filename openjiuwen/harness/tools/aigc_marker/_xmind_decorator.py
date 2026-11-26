# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""AIGC labels for modern JSON and legacy XML XMind workbooks."""

import json
import uuid
import zipfile

from lxml import etree as ET

from openjiuwen.harness.tools.aigc_marker._common import canonical_signature, parse_aigc_json
from openjiuwen.harness.tools.aigc_marker._package_utils import replace_zip_entries

TEXT = "内容由AI生成"
CONTENT_NS = "urn:xmind:xmap:xmlns:content:2.0"
MANIFEST_NS = "urn:xmind:xmap:xmlns:manifest:1.0"


class XmindAigcDecorator:
    """Append an attached topic per sheet; preserve existing topics and resources."""

    @staticmethod
    def _get_aigc_data(file_path):
        with zipfile.ZipFile(file_path) as archive:
            if "metadata.json" not in archive.namelist():
                return None
            metadata = json.loads(archive.read("metadata.json"))
            raw = metadata.get("AIGC")
            return raw if isinstance(raw, dict) else parse_aigc_json(raw)

    @staticmethod
    def decorate(file_path, fallback_signature, add_visible_mark=True):
        updates = {}
        with zipfile.ZipFile(file_path) as archive:
            names = set(archive.namelist())
            if "content.json" not in names and "content.xml" not in names:
                raise ValueError("XMind content.json/content.xml is missing")
            # Some workbooks contain both representations. Keep them in sync.
            if add_visible_mark and "content.json" in names:
                sheets = json.loads(archive.read("content.json"))
                if not isinstance(sheets, list) or not sheets:
                    raise ValueError("XMind workbook has no sheets")
                for sheet in sheets:
                    topic = sheet.get("rootTopic")
                    if not isinstance(topic, dict):
                        raise ValueError("XMind sheet rootTopic is missing")
                    # A collapsed root would hide the label in ordinary viewing.
                    if topic.get("branch") == "folded":
                        topic.pop("branch")
                    children = topic.setdefault("children", {}).setdefault("attached", [])
                    if not any(child.get("title") == TEXT for child in children):
                        children.append({"id": uuid.uuid4().hex, "class": "topic", "title": TEXT})
                updates["content.json"] = json.dumps(sheets, ensure_ascii=False).encode("utf-8")
            if add_visible_mark and "content.xml" in names:
                root = ET.fromstring(archive.read("content.xml"), ET.XMLParser(resolve_entities=False, no_network=True))
                namespace = root.tag.split("}")[0][1:] if root.tag.startswith("{") else CONTENT_NS

                def q(name):
                    return "{" + namespace + "}" + name

                sheets = root.findall(q("sheet"))
                if not sheets:
                    raise ValueError("XMind XML workbook has no sheets")
                for sheet in sheets:
                    topic = sheet.find(q("topic"))
                    if topic is None:
                        raise ValueError("XMind XML sheet topic is missing")
                    if topic.get("branch") == "folded":
                        topic.attrib.pop("branch")
                    children = topic.find(q("children"))
                    if children is None:
                        children = ET.SubElement(topic, q("children"))
                    attached = next(
                        (node for node in children if node.tag == q("topics") and node.get("type") == "attached"), None
                    )
                    if attached is None:
                        attached = ET.SubElement(children, q("topics"), type="attached")
                    if not any(node.findtext(q("title")) == TEXT for node in attached):
                        mark = ET.SubElement(attached, q("topic"), id=uuid.uuid4().hex)
                        ET.SubElement(mark, q("title")).text = TEXT
                updates["content.xml"] = ET.tostring(root, encoding="utf-8", xml_declaration=True)
            metadata = json.loads(archive.read("metadata.json")) if "metadata.json" in names else {}
            if not isinstance(metadata, dict):
                raise ValueError("XMind metadata.json must contain an object")
            # Main text 6.6 specifies a string; appendix B.7 also shows an object.
            metadata["AIGC"] = canonical_signature(metadata.get("AIGC"), fallback_signature)
            updates["metadata.json"] = json.dumps(metadata, ensure_ascii=False).encode("utf-8")
            if "content.json" in names or "manifest.json" in names:
                manifest = (
                    json.loads(archive.read("manifest.json")) if "manifest.json" in names else {"file-entries": {}}
                )
                if not isinstance(manifest, dict) or not isinstance(manifest.setdefault("file-entries", {}), dict):
                    raise ValueError("invalid XMind JSON manifest")
                manifest["file-entries"].setdefault("metadata.json", {})
                updates["manifest.json"] = json.dumps(manifest, ensure_ascii=False).encode("utf-8")
            if "content.xml" in names:
                part = "META-INF/manifest.xml"
                manifest = (
                    ET.fromstring(archive.read(part), ET.XMLParser(resolve_entities=False, no_network=True))
                    if part in names
                    else ET.Element("{" + MANIFEST_NS + "}manifest")
                )
                namespace = manifest.tag.split("}")[0][1:] if manifest.tag.startswith("{") else MANIFEST_NS
                tag = "{" + namespace + "}file-entry"
                if not any(entry.get("full-path") == "metadata.json" for entry in manifest):
                    ET.SubElement(manifest, tag, {"full-path": "metadata.json", "media-type": "application/json"})
                updates[part] = ET.tostring(manifest, encoding="utf-8", xml_declaration=True)
        replace_zip_entries(file_path, updates)
