# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""ProcessOn POSM/POSF native visible labels and section 6.7 metadata."""

import json
import zipfile
from pathlib import Path

from openjiuwen.harness.tools.aigc_marker._common import canonical_signature, parse_aigc_json, read_pos_json
from openjiuwen.harness.tools.aigc_marker._package_utils import replace_zip_entries


def package_names(archive):
    names = archive.namelist()
    if len(set(name.casefold() for name in names)) != len(names):
        raise ValueError("ProcessOn package contains duplicate or ambiguous members")
    if any(info.flag_bits & 1 for info in archive.infolist()):
        raise ValueError("Encrypted ProcessOn packages are not supported")
    return set(names)


def _metadata(archive):
    names = package_names(archive)
    # The guide specifies root-level meta.json, not XMind metadata.json.
    if any(name.casefold() == "meta.json" and name != "meta.json" for name in names):
        raise ValueError("ProcessOn metadata must be named meta.json")
    data = read_pos_json(archive.read("meta.json")) if "meta.json" in names else {}
    if not isinstance(data, dict):
        raise ValueError("ProcessOn meta.json must contain an object")
    return data


def _signature(raw):
    # Existing exports may use a JSON object; standardize to the guide's string.
    return raw if isinstance(raw, dict) else parse_aigc_json(raw)


def metadata_update(archive, fallback_signature):
    """Build a metadata update, retaining lawful provenance and other fields."""
    data = _metadata(archive)
    existing = _signature(data.get("AIGC"))
    data["AIGC"] = canonical_signature(existing, fallback_signature)
    return json.dumps(data, ensure_ascii=False).encode("utf-8")


def read_pos_metadata(file_path):
    with zipfile.ZipFile(file_path) as archive:
        return _signature(_metadata(archive).get("AIGC"))


class PosAigcDecorator:
    """Mark recognized native JSON canvases and preserve metadata provenance."""

    @staticmethod
    def _get_aigc_data(file_path):
        return read_pos_metadata(file_path)

    @staticmethod
    def decorate(file_path, fallback_signature, add_visible_mark=True):
        if Path(file_path).suffix.lower() not in (".posm", ".posf"):
            raise ValueError("Unsupported ProcessOn extension")
        with zipfile.ZipFile(file_path) as archive:
            names = package_names(archive)
            if not any(n != "meta.json" and not n.endswith("/") for n in names):
                raise ValueError("ProcessOn package has no content parts")
            value = metadata_update(archive, fallback_signature)
            updates = {}
            if "meta.json" not in names or archive.read("meta.json") != value:
                updates["meta.json"] = value
            if add_visible_mark:
                from openjiuwen.harness.tools.aigc_marker._pos_visible import canvas_updates

                updates.update(canvas_updates(archive, Path(file_path).suffix.lower())[0])
        if updates:
            replace_zip_entries(file_path, updates)
        if add_visible_mark:
            inspect_pos_visible(file_path)


def inspect_pos_visible(file_path):
    """Read-only structural check of every recognized canvas's visible marks."""
    from openjiuwen.harness.tools.aigc_marker._pos_visible import canvas_updates

    with zipfile.ZipFile(file_path) as archive:
        package_names(archive)
        _, count = canvas_updates(archive, Path(file_path).suffix.lower(), mark=False)
    return {
        "visible_storage_verified": True,
        "canvas_count": count,
        "text": "内容由AI生成",
        "scope": "native canvas properties and text; no editor rendering or website claim",
    }
