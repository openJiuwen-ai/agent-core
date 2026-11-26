# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""OFD marks: DocInfo CustomData metadata and referenced page image objects."""

import io
import math
import os
import posixpath
import tempfile
import xml.etree.ElementTree as ET
import zipfile

from openjiuwen.core.common.logging import agent_logger
from openjiuwen.harness.tools.aigc_marker._common import parse_aigc_json, select_signature

OFD_NS = "http://www.ofdspec.org/2016"
VISIBLE_TEXT = "内容由AI生成"
PNG_MARK_KEY = "AIGCVisibleMark"
ET.register_namespace("ofd", OFD_NS)


def _tag(name):
    return f"{{{OFD_NS}}}{name}"


def _xml(data):
    if b"<!DOCTYPE" in data.upper():
        raise ValueError("OFD XML with a DOCTYPE is not supported")
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True, insert_pis=True))
    return ET.fromstring(data, parser=parser)


def _serialize(root):
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _resolve(base, location):
    """Resolve an ST_Loc inside the ZIP without extracting any files."""
    location = (location or "").strip().replace("\\", "/")
    if not location:
        raise ValueError("OFD contains an empty resource location")
    result = posixpath.normpath(location.lstrip("/") if location.startswith("/") else posixpath.join(base, location))
    if result == ".." or result.startswith("../") or ":" in result:
        raise ValueError(f"Invalid OFD package location: {location}")
    return result


def _number(value):
    return format(value, ".6g")


class OfdAigcDecorator:
    def __init__(self):
        self.name = "ofd_aigc_decorator"

    def decorate(self, file_path: str, fallback_signature: str, add_visible_mark: bool = True):
        try:
            with zipfile.ZipFile(file_path) as source:
                infos = source.infolist()
                if len({info.filename for info in infos}) != len(infos):
                    raise ValueError("OFD package contains duplicate ZIP entries")
                members = {info.filename: source.read(info) for info in infos}
                archive_comment = source.comment
            root = _xml(members["OFD.xml"])
            if root.tag != _tag("OFD"):
                raise ValueError("Not a supported OFD 2016 document")
            bodies = root.findall(_tag("DocBody"))
            if not bodies:
                raise ValueError("OFD has no DocBody")
            updates = {}
            max_id = self._max_id(members)
            for body in bodies:
                doc_root = body.find(_tag("DocRoot"))
                document_path = _resolve("", doc_root.text if doc_root is not None else None)
                document = _xml(members[document_path])
                common = document.find(_tag("CommonData"))
                if common is None:
                    raise ValueError("OFD document has no CommonData")
                doc_info = body.find(_tag("DocInfo"))
                if doc_info is None:
                    raise ValueError("OFD document has no required DocInfo")
                custom = doc_info.find(_tag("CustomDatas"))
                if custom is None:
                    custom = ET.SubElement(doc_info, _tag("CustomDatas"))
                marks = [
                    e for e in custom.findall(_tag("CustomData")) if e.get("Name") == "AIGC" or e.get("name") == "AIGC"
                ]
                mark = marks[0] if marks else ET.SubElement(custom, _tag("CustomData"), Name="AIGC")
                if any(mark.get(key) not in (None, "AIGC") for key in ("Name", "name")):
                    raise ValueError("Conflicting OFD AIGC property names")
                # OFD uses Name; TC260-PG-20258A and GCMark read lowercase name.
                # Keep both on the same property, with the same provenance value.
                if mark.get("Name") != "AIGC" or mark.get("name") != "AIGC":
                    mark.set("Name", "AIGC")
                    mark.set("name", "AIGC")
                    updates["OFD.xml"] = b""
                signature = select_signature([p.text for p in marks], fallback_signature)
                if mark.text != signature:
                    mark.text = signature
                    updates["OFD.xml"] = b""
                for duplicate in marks[1:]:
                    custom.remove(duplicate)
                    updates["OFD.xml"] = b""
                if add_visible_mark:
                    max_id = self._add_visible(document_path, document, common, members, updates, max_id)
            if "OFD.xml" in updates:
                updates["OFD.xml"] = _serialize(root)
            if updates:
                self._atomic_write(file_path, members, infos, archive_comment, updates)
            agent_logger.debug(
                "  [OFD] AIGC mark added successfully" if updates else "  [OFD] AIGC mark complete, skipping"
            )
        except Exception as exc:
            raise RuntimeError(f"OFD AIGC marking failed: {exc}") from exc

    @staticmethod
    def _get_aigc_data(file_path: str) -> dict | None:
        try:
            with zipfile.ZipFile(file_path) as source:
                root = _xml(source.read("OFD.xml"))
                for data in root.findall(
                    f"{_tag('DocBody')}/{_tag('DocInfo')}/{_tag('CustomDatas')}/{_tag('CustomData')}"
                ):
                    if data.get("Name") == "AIGC" or data.get("name") == "AIGC":
                        return parse_aigc_json(data.text)
        except (OSError, KeyError, zipfile.BadZipFile, ET.ParseError, ValueError):
            return None
        return None

    @staticmethod
    def _max_id(members):
        maximum = 0
        for name, data in members.items():
            if not name.lower().endswith(".xml"):
                continue
            try:
                root = _xml(data)
            except (ET.ParseError, ValueError):
                continue
            for element in root.iter():
                for key, value in element.attrib.items():
                    if key == "ID" and value.isdecimal():
                        maximum = max(maximum, int(value))
                if element.tag == _tag("MaxUnitID") and (element.text or "").strip().isdecimal():
                    maximum = max(maximum, int(element.text.strip()))
        return maximum

    @staticmethod
    def _marked_media(common, document_dir, members):
        """Find our PNG resources through actual OFD resource references."""
        from PIL import Image

        marked = set()
        for ref in list(common):
            if ref.tag not in (_tag("PublicRes"), _tag("DocumentRes")):
                continue
            path = _resolve(document_dir, ref.text)
            res = _xml(members[path])
            base = _resolve(posixpath.dirname(path), res.get("BaseLoc", "."))
            for media in res.findall(f"{_tag('MultiMedias')}/{_tag('MultiMedia')}"):
                loc = media.find(_tag("MediaFile"))
                if loc is None or media.get("Type") != "Image":
                    continue
                media_path = _resolve(base, loc.text)
                if not members.get(media_path, b"").startswith(b"\x89PNG\r\n\x1a\n"):
                    continue
                with Image.open(io.BytesIO(members[media_path])) as image:
                    if image.info.get(PNG_MARK_KEY) == VISIBLE_TEXT:
                        marked.add(media.get("ID"))
        return marked

    @staticmethod
    def _watermark_png():
        from PIL import Image, ImageDraw, ImageFont
        from PIL.PngImagePlugin import PngInfo

        from openjiuwen.harness.tools.aigc_marker._fonts import get_font_path

        font_path = get_font_path()
        if not font_path:
            raise RuntimeError("HarmonyOS Sans SC font installed by 小艺 Work was not found")
        font = ImageFont.truetype(font_path, 48)
        bounds = font.getbbox(VISIBLE_TEXT)
        image = Image.new("RGBA", (bounds[2] - bounds[0] + 24, bounds[3] - bounds[1] + 20), (255, 255, 255, 230))
        ImageDraw.Draw(image).text((12 - bounds[0], 10 - bounds[1]), VISIBLE_TEXT, font=font, fill=(0, 0, 0, 255))
        info = PngInfo()
        info.add_text(PNG_MARK_KEY, VISIBLE_TEXT)
        output = io.BytesIO()
        image.save(output, format="PNG", pnginfo=info)
        return output.getvalue(), image.size

    @staticmethod
    def _page_box(page, common):
        box = page.find(f"{_tag('Area')}/{_tag('PhysicalBox')}")
        if box is None:
            box = common.find(f"{_tag('PageArea')}/{_tag('PhysicalBox')}")
        try:
            values = [float(value) for value in box.text.split()]
        except (AttributeError, TypeError, ValueError) as exc:
            raise ValueError("OFD page has no valid PhysicalBox") from exc
        if len(values) != 4 or not all(math.isfinite(value) for value in values) or min(values[2:]) <= 0:
            raise ValueError("OFD page has an invalid PhysicalBox")
        return values

    def _add_visible(self, document_path, document, common, members, updates, max_id):
        document_dir = posixpath.dirname(document_path)
        marked_ids = self._marked_media(common, document_dir, members)
        pages = document.findall(f"{_tag('Pages')}/{_tag('Page')}")
        if not pages:
            raise ValueError("OFD document has no pages")
        missing = []
        for page_ref in pages:
            path = _resolve(document_dir, page_ref.get("BaseLoc"))
            page = _xml(updates.get(path, members[path]))
            if not any(obj.get("ResourceID") in marked_ids for obj in page.iter(_tag("ImageObject"))):
                missing.append((path, page, self._page_box(page, common)))
        if not missing:
            return max_id
        image_bytes, (pixel_width, pixel_height) = self._watermark_png()
        suffix = 0
        while True:
            stem = "AIGCWatermark" + (f"{suffix}" if suffix else "")
            resource_path = posixpath.join(document_dir, stem + "Res.xml")
            image_path = posixpath.join(document_dir, "Res", stem + ".png")
            if all(path not in members and path not in updates for path in (resource_path, image_path)):
                break
            suffix += 1
        max_id += 1
        resource_id = str(max_id)
        res = ET.Element(_tag("Res"), BaseLoc="Res")
        media_resources = ET.SubElement(res, _tag("MultiMedias"))
        media = ET.SubElement(media_resources, _tag("MultiMedia"), ID=resource_id, Type="Image", Format="PNG")
        ET.SubElement(media, _tag("MediaFile")).text = posixpath.basename(image_path)
        updates[resource_path] = _serialize(res)
        updates[image_path] = image_bytes
        # CommonData is an XSD sequence: DocumentRes precedes TemplatePage/DefaultCS.
        ref = ET.Element(_tag("DocumentRes"))
        ref.text = posixpath.basename(resource_path)
        insert_at = next(
            (i for i, child in enumerate(common) if child.tag in (_tag("TemplatePage"), _tag("DefaultCS"))), len(common)
        )
        common.insert(insert_at, ref)
        for path, page, (origin_x, origin_y, width, height) in missing:
            wm_height = min(5.0, height * 0.12, width * 0.9 * pixel_height / pixel_width)
            wm_width = wm_height * pixel_width / pixel_height
            margin = min(5.0, height * 0.05)
            x = origin_x + (width - wm_width) / 2
            y = origin_y + height - wm_height - margin
            page_content = page.find(_tag("Content"))
            if page_content is None:
                page_content = ET.Element(_tag("Content"))
                insert_at = next((i for i, child in enumerate(page) if child.tag == _tag("Actions")), len(page))
                page.insert(insert_at, page_content)
            max_id += 1
            layer = ET.SubElement(page_content, _tag("Layer"), ID=str(max_id), Type="Foreground")
            max_id += 1
            ET.SubElement(
                layer,
                _tag("ImageObject"),
                ID=str(max_id),
                ResourceID=resource_id,
                Boundary=" ".join(_number(value) for value in (x, y, wm_width, wm_height)),
                CTM=f"{_number(wm_width)} 0 0 {_number(wm_height)} 0 0",
            )
            updates[path] = _serialize(page)
        max_unit = common.find(_tag("MaxUnitID"))
        if max_unit is None:
            max_unit = ET.Element(_tag("MaxUnitID"))
            common.insert(0, max_unit)
        max_unit.text = str(max_id)
        updates[document_path] = _serialize(document)
        return max_id

    @staticmethod
    def _atomic_write(file_path, members, infos, archive_comment, updates):
        fd, temporary = tempfile.mkstemp(
            prefix=".aigc-", suffix=".ofd", dir=os.path.dirname(os.path.abspath(file_path))
        )
        os.close(fd)
        try:
            with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as target:
                target.comment = archive_comment
                for info in infos:
                    target.writestr(info, updates.get(info.filename, members[info.filename]))
                for name, data in updates.items():
                    if name not in members:
                        target.writestr(name, data)
            os.replace(temporary, file_path)
        finally:
            if os.path.exists(temporary):
                os.remove(temporary)
